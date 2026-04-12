"""模型元数据、上下文长度和 token 估算工具。

纯工具函数,无 AIAgent 依赖。由 ContextCompressor 和 run_agent.py
用于预检上下文检查。
"""

import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import requests
import yaml

from kclaw_constants import OPENROUTER_MODELS_URL

logger = logging.getLogger(__name__)

# 可作为 "provider:" 前缀出现在模型 ID 之前的提供者名称。
# 只剥离这些 — Ollama 风格的 "model:tag" 冒号(例如 "qwen3.5:27b")
# 被保留,以便完整模型名到达缓存查找和服务器查询。
_PROVIDER_PREFIXES: frozenset[str] = frozenset({
    "openrouter", "nous", "openai-codex", "copilot", "copilot-acp",
    "gemini", "zai", "kimi-coding", "minimax", "minimax-cn", "anthropic", "deepseek",
    "opencode-zen", "opencode-go", "ai-gateway", "kilocode", "alibaba",
    "qwen-oauth",
    "custom", "local",
    # 常见别名
    "google", "google-gemini", "google-ai-studio",
    "glm", "z-ai", "z.ai", "zhipu", "github", "github-copilot",
    "github-models", "kimi", "moonshot", "claude", "deep-seek",
    "opencode", "zen", "go", "vercel", "kilo", "dashscope", "aliyun", "qwen",
    "qwen-portal",
})


_OLLAMA_TAG_PATTERN = re.compile(
    r"^(\d+\.?\d*b|latest|stable|q\d|fp?\d|instruct|chat|coder|vision|text)",
    re.IGNORECASE,
)


def _strip_provider_prefix(model: str) -> str:
    """从模型字符串中剥离已识别的提供者前缀。

    ``"local:my-model"`` → ``"my-model"``
    ``"qwen3.5:27b"``   → ``"qwen3.5:27b"``  (不变 — 不是提供者前缀)
    ``"qwen:0.5b"``     → ``"qwen:0.5b"``    (不变 — Ollama model:tag)
    ``"deepseek:latest"``→ ``"deepseek:latest"``(不变 — Ollama model:tag)
    """
    if ":" not in model or model.startswith("http"):
        return model
    prefix, suffix = model.split(":", 1)
    prefix_lower = prefix.strip().lower()
    if prefix_lower in _PROVIDER_PREFIXES:
        # 如果后缀看起来像 Ollama tag(例如 "7b"、"latest"、"q4_0")则不剥离
        if _OLLAMA_TAG_PATTERN.match(suffix.strip()):
            return model
        return suffix
    return model

_model_metadata_cache: Dict[str, Dict[str, Any]] = {}
_model_metadata_cache_time: float = 0
_MODEL_CACHE_TTL = 3600
_endpoint_model_metadata_cache: Dict[str, Dict[str, Dict[str, Any]]] = {}
_endpoint_model_metadata_cache_time: Dict[str, float] = {}
_ENDPOINT_MODEL_CACHE_TTL = 300

# 当模型未知时,上下文长度探测的递减层级。
# 从 128K 开始(大多数现代模型的安全默认值),在遇到
# 上下文长度错误时逐步降低,直到一个生效。
CONTEXT_PROBE_TIERS = [
    128_000,
    64_000,
    32_000,
    16_000,
    8_000,
]

# 当所有检测方法都失败时的默认上下文长度。
DEFAULT_FALLBACK_CONTEXT = CONTEXT_PROBE_TIERS[0]

# 精简的回退默认值 — 仅宽泛的模型家族模式。
# 仅当提供者未知且 models.dev/OpenRouter/Anthropic 都未命中时触发。
# 替换了之前的 80+ 条目字典。
# 对于提供者特定的上下文长度,models.dev 是主要来源。
DEFAULT_CONTEXT_LENGTHS = {
    # Anthropic Claude 4.6 (1M 上下文) — 仅裸 ID 以避免
    # 模糊匹配冲突(例如 "anthropic/claude-sonnet-4" 是
    # "anthropic/claude-sonnet-4.6" 的子串)。
    # OpenRouter 前缀的模型通过 OpenRouter 实时 API 或 models.dev 解析。
    "claude-opus-4-6": 1000000,
    "claude-sonnet-4-6": 1000000,
    "claude-opus-4.6": 1000000,
    "claude-sonnet-4.6": 1000000,
    # 旧版 Claude 模型的通用匹配(必须排在具体条目之后)
    "claude": 200000,
    # OpenAI
    "gpt-4.1": 1047576,
    "gpt-5": 128000,
    "gpt-4": 128000,
    # Google
    "gemini": 1048576,
    # Gemma(通过 AI Studio 服务的开放模型)
    "gemma-4-31b": 256000,
    "gemma-4-26b": 256000,
    "gemma-3": 131072,
    "gemma": 8192,  # 旧版 gemma 模型的回退
    # DeepSeek
    "deepseek": 128000,
    # Meta
    "llama": 131072,
    # Qwen
    "qwen": 131072,
    # MiniMax (小写 — 查找时在第 973 行将模型名转为小写)
    "minimax-m1-256k": 1000000,
    "minimax-m1-128k": 1000000,
    "minimax-m1-80k": 1000000,
    "minimax-m1-40k": 1000000,
    "minimax-m1": 1000000,
    "minimax-m2.5": 1048576,
    "minimax-m2.7": 1048576,
    "minimax": 1048576,
    # GLM
    "glm": 202752,
    # Kimi
    "kimi": 262144,
    # Arcee
    "trinity": 262144,
    # Hugging Face 推理提供者 — 模型 ID 使用 org/name 格式
    "Qwen/Qwen3.5-397B-A17B": 131072,
    "Qwen/Qwen3.5-35B-A3B": 131072,
    "deepseek-ai/DeepSeek-V3.2": 65536,
    "moonshotai/Kimi-K2.5": 262144,
    "moonshotai/Kimi-K2-Thinking": 262144,
    "MiniMaxAI/MiniMax-M2.5": 1048576,
    "XiaomiMiMo/MiMo-V2-Flash": 32768,
    "mimo-v2-pro": 1048576,
    "mimo-v2-omni": 1048576,
    "zai-org/GLM-5": 202752,
}

_CONTEXT_LENGTH_KEYS = (
    "context_length",
    "context_window",
    "max_context_length",
    "max_position_embeddings",
    "max_model_len",
    "max_input_tokens",
    "max_sequence_length",
    "max_seq_len",
    "n_ctx_train",
    "n_ctx",
)

_MAX_COMPLETION_KEYS = (
    "max_completion_tokens",
    "max_output_tokens",
    "max_tokens",
)

# 本地服务器主机名 / 地址模式
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1", "0.0.0.0")


def _normalize_base_url(base_url: str) -> str:
    return (base_url or "").strip().rstrip("/")


def _is_openrouter_base_url(base_url: str) -> bool:
    return "openrouter.ai" in _normalize_base_url(base_url).lower()


def _is_custom_endpoint(base_url: str) -> bool:
    normalized = _normalize_base_url(base_url)
    return bool(normalized) and not _is_openrouter_base_url(normalized)


_URL_TO_PROVIDER: Dict[str, str] = {
    "api.openai.com": "openai",
    "chatgpt.com": "openai",
    "api.anthropic.com": "anthropic",
    "api.z.ai": "zai",
    "api.moonshot.ai": "kimi-coding",
    "api.kimi.com": "kimi-coding",
    "api.minimax": "minimax",
    "dashscope.aliyuncs.com": "alibaba",
    "dashscope-intl.aliyuncs.com": "alibaba",
    "portal.qwen.ai": "qwen-oauth",
    "openrouter.ai": "openrouter",
    "generativelanguage.googleapis.com": "gemini",
    "inference-api.nousresearch.com": "nous",
    "api.deepseek.com": "deepseek",
    "api.githubcopilot.com": "copilot",
    "models.github.ai": "copilot",
    "api.fireworks.ai": "fireworks",
    "opencode.ai": "opencode-go",
}


def _infer_provider_from_url(base_url: str) -> Optional[str]:
    """从 base URL 推断 models.dev 的提供者名称。

    这允许通过 models.dev 为自定义端点(如 DashScope(阿里巴巴)、
    Z.AI、Kimi 等)解析上下文长度,而无需用户在配置中显式设置
    提供者名称。
    """
    normalized = _normalize_base_url(base_url)
    if not normalized:
        return None
    parsed = urlparse(normalized if "://" in normalized else f"https://{normalized}")
    host = parsed.netloc.lower() or parsed.path.lower()
    for url_part, provider in _URL_TO_PROVIDER.items():
        if url_part in host:
            return provider
    return None


def _is_known_provider_base_url(base_url: str) -> bool:
    return _infer_provider_from_url(base_url) is not None


def is_local_endpoint(base_url: str) -> bool:
    """如果 base_url 指向本机(localhost / RFC-1918 / WSL)则返回 True。"""
    normalized = _normalize_base_url(base_url)
    if not normalized:
        return False
    url = normalized if "://" in normalized else f"http://{normalized}"
    try:
        parsed = urlparse(url)
        host = parsed.hostname or ""
    except Exception:
        return False
    if host in _LOCAL_HOSTS:
        return True
    # RFC-1918 私有范围和链路本地地址
    import ipaddress
    try:
        addr = ipaddress.ip_address(host)
        return addr.is_private or addr.is_loopback or addr.is_link_local
    except ValueError:
        pass
    # 看起来像私有范围的裸 IP(例如 172.26.x.x 用于 WSL)
    parts = host.split(".")
    if len(parts) == 4:
        try:
            first, second = int(parts[0]), int(parts[1])
            if first == 10:
                return True
            if first == 172 and 16 <= second <= 31:
                return True
            if first == 192 and second == 168:
                return True
        except ValueError:
            pass
    return False


def detect_local_server_type(base_url: str) -> Optional[str]:
    """通过探测已知端点检测 base_url 上运行的是哪个本地服务器。

    返回以下之一: "ollama"、"lm-studio"、"vllm"、"llamacpp" 或 None。
    """
    import httpx

    normalized = _normalize_base_url(base_url)
    server_url = normalized
    if server_url.endswith("/v1"):
        server_url = server_url[:-3]

    try:
        with httpx.Client(timeout=2.0) as client:
            # LM Studio 暴露 /api/v1/models — 首先检查(最具体)
            try:
                r = client.get(f"{server_url}/api/v1/models")
                if r.status_code == 200:
                    return "lm-studio"
            except Exception:
                pass
            # Ollama 暴露 /api/tags 并响应 {"models": [...]}
            # LM Studio 在此路径返回 {"error": "Unexpected endpoint"} 并带有 status 200,
            # 所以我们必须验证响应是否包含 "models"。
            try:
                r = client.get(f"{server_url}/api/tags")
                if r.status_code == 200:
                    try:
                        data = r.json()
                        if "models" in data:
                            return "ollama"
                    except Exception:
                        pass
            except Exception:
                pass
            # llama.cpp 暴露 /v1/props(旧版本使用 /props 而无 /v1 前缀)
            try:
                r = client.get(f"{server_url}/v1/props")
                if r.status_code != 200:
                    r = client.get(f"{server_url}/props")  # 旧版本的回退
                if r.status_code == 200 and "default_generation_settings" in r.text:
                    return "llamacpp"
            except Exception:
                pass
            # vLLM: /version
            try:
                r = client.get(f"{server_url}/version")
                if r.status_code == 200:
                    data = r.json()
                    if "version" in data:
                        return "vllm"
            except Exception:
                pass
    except Exception:
        pass

    return None


def _iter_nested_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from _iter_nested_dicts(nested)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_nested_dicts(item)


def _coerce_reasonable_int(value: Any, minimum: int = 1024, maximum: int = 10_000_000) -> Optional[int]:
    try:
        if isinstance(value, bool):
            return None
        if isinstance(value, str):
            value = value.strip().replace(",", "")
        result = int(value)
    except (TypeError, ValueError):
        return None
    if minimum <= result <= maximum:
        return result
    return None


def _extract_first_int(payload: Dict[str, Any], keys: tuple[str, ...]) -> Optional[int]:
    keyset = {key.lower() for key in keys}
    for mapping in _iter_nested_dicts(payload):
        for key, value in mapping.items():
            if str(key).lower() not in keyset:
                continue
            coerced = _coerce_reasonable_int(value)
            if coerced is not None:
                return coerced
    return None


def _extract_context_length(payload: Dict[str, Any]) -> Optional[int]:
    return _extract_first_int(payload, _CONTEXT_LENGTH_KEYS)


def _extract_max_completion_tokens(payload: Dict[str, Any]) -> Optional[int]:
    return _extract_first_int(payload, _MAX_COMPLETION_KEYS)


def _extract_pricing(payload: Dict[str, Any]) -> Dict[str, Any]:
    alias_map = {
        "prompt": ("prompt", "input", "input_cost_per_token", "prompt_token_cost"),
        "completion": ("completion", "output", "output_cost_per_token", "completion_token_cost"),
        "request": ("request", "request_cost"),
        "cache_read": ("cache_read", "cached_prompt", "input_cache_read", "cache_read_cost_per_token"),
        "cache_write": ("cache_write", "cache_creation", "input_cache_write", "cache_write_cost_per_token"),
    }
    for mapping in _iter_nested_dicts(payload):
        normalized = {str(key).lower(): value for key, value in mapping.items()}
        if not any(any(alias in normalized for alias in aliases) for aliases in alias_map.values()):
            continue
        pricing: Dict[str, Any] = {}
        for target, aliases in alias_map.items():
            for alias in aliases:
                if alias in normalized and normalized[alias] not in (None, ""):
                    pricing[target] = normalized[alias]
                    break
        if pricing:
            return pricing
    return {}


def _add_model_aliases(cache: Dict[str, Dict[str, Any]], model_id: str, entry: Dict[str, Any]) -> None:
    cache[model_id] = entry
    if "/" in model_id:
        bare_model = model_id.split("/", 1)[1]
        cache.setdefault(bare_model, entry)


def fetch_model_metadata(force_refresh: bool = False) -> Dict[str, Dict[str, Any]]:
    """从 OpenRouter 获取模型元数据(缓存 1 小时)。"""
    global _model_metadata_cache, _model_metadata_cache_time

    if not force_refresh and _model_metadata_cache and (time.time() - _model_metadata_cache_time) < _MODEL_CACHE_TTL:
        return _model_metadata_cache

    try:
        response = requests.get(OPENROUTER_MODELS_URL, timeout=10)
        response.raise_for_status()
        data = response.json()

        cache = {}
        for model in data.get("data", []):
            model_id = model.get("id", "")
            entry = {
                "context_length": model.get("context_length", 128000),
                "max_completion_tokens": model.get("top_provider", {}).get("max_completion_tokens", 4096),
                "name": model.get("name", model_id),
                "pricing": model.get("pricing", {}),
            }
            _add_model_aliases(cache, model_id, entry)
            canonical = model.get("canonical_slug", "")
            if canonical and canonical != model_id:
                _add_model_aliases(cache, canonical, entry)

        _model_metadata_cache = cache
        _model_metadata_cache_time = time.time()
        logger.debug("Fetched metadata for %s models from OpenRouter", len(cache))
        return cache

    except Exception as e:
        logging.warning(f"从 OpenRouter 获取模型元数据失败: {e}")
        return _model_metadata_cache or {}


def fetch_endpoint_model_metadata(
    base_url: str,
    api_key: str = "",
    force_refresh: bool = False,
) -> Dict[str, Dict[str, Any]]:
    """从 OpenAI 兼容的 ``/models`` 端点获取模型元数据。

    用于硬编码的全局模型名默认值不可靠的显式自定义端点。
    结果按 base URL 在内存中缓存。
    """
    normalized = _normalize_base_url(base_url)
    if not normalized or _is_openrouter_base_url(normalized):
        return {}

    if not force_refresh:
        cached = _endpoint_model_metadata_cache.get(normalized)
        cached_at = _endpoint_model_metadata_cache_time.get(normalized, 0)
        if cached is not None and (time.time() - cached_at) < _ENDPOINT_MODEL_CACHE_TTL:
            return cached

    candidates = [normalized]
    if normalized.endswith("/v1"):
        alternate = normalized[:-3].rstrip("/")
    else:
        alternate = normalized + "/v1"
    if alternate and alternate not in candidates:
        candidates.append(alternate)

    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    last_error: Optional[Exception] = None

    for candidate in candidates:
        url = candidate.rstrip("/") + "/models"
        try:
            response = requests.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            payload = response.json()
            cache: Dict[str, Dict[str, Any]] = {}
            for model in payload.get("data", []):
                if not isinstance(model, dict):
                    continue
                model_id = model.get("id")
                if not model_id:
                    continue
                entry: Dict[str, Any] = {"name": model.get("name", model_id)}
                context_length = _extract_context_length(model)
                if context_length is not None:
                    entry["context_length"] = context_length
                max_completion_tokens = _extract_max_completion_tokens(model)
                if max_completion_tokens is not None:
                    entry["max_completion_tokens"] = max_completion_tokens
                pricing = _extract_pricing(model)
                if pricing:
                    entry["pricing"] = pricing
                _add_model_aliases(cache, model_id, entry)

            # 如果这是 llama.cpp 服务器,查询 /props 获取实际分配的上下文
            is_llamacpp = any(
                m.get("owned_by") == "llamacpp"
                for m in payload.get("data", []) if isinstance(m, dict)
            )
            if is_llamacpp:
                try:
                    # 先尝试 /v1/props(当前 llama.cpp);回退到 /props 用于旧版本
                    base = candidate.rstrip("/").replace("/v1", "")
                    props_resp = requests.get(base + "/v1/props", headers=headers, timeout=5)
                    if not props_resp.ok:
                        props_resp = requests.get(base + "/props", headers=headers, timeout=5)
                    if props_resp.ok:
                        props = props_resp.json()
                        gen_settings = props.get("default_generation_settings", {})
                        n_ctx = gen_settings.get("n_ctx")
                        model_alias = props.get("model_alias", "")
                        if n_ctx and model_alias and model_alias in cache:
                            cache[model_alias]["context_length"] = n_ctx
                except Exception:
                    pass

            _endpoint_model_metadata_cache[normalized] = cache
            _endpoint_model_metadata_cache_time[normalized] = time.time()
            return cache
        except Exception as exc:
            last_error = exc

    if last_error:
        logger.debug("Failed to fetch model metadata from %s/models: %s", normalized, last_error)
    _endpoint_model_metadata_cache[normalized] = {}
    _endpoint_model_metadata_cache_time[normalized] = time.time()
    return {}


def _get_context_cache_path() -> Path:
    """返回持久化上下文长度缓存文件的路径。"""
    from kclaw_constants import get_kclaw_home
    return get_kclaw_home() / "context_length_cache.yaml"


def _load_context_cache() -> Dict[str, int]:
    """从磁盘加载 model+provider → context_length 缓存。"""
    path = _get_context_cache_path()
    if not path.exists():
        return {}
    try:
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        return data.get("context_lengths", {})
    except Exception as e:
        logger.debug("加载上下文长度缓存失败: %s", e)
        return {}


def save_context_length(model: str, base_url: str, length: int) -> None:
    """持久化发现的模型+提供者组合的上下文长度。

    缓存键为 ``model@base_url``,以便从不同提供者服务的同一模型名
    可以有不同的限制。
    """
    key = f"{model}@{base_url}"
    cache = _load_context_cache()
    if cache.get(key) == length:
        return  # 已存储
    cache[key] = length
    path = _get_context_cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            yaml.dump({"context_lengths": cache}, f, default_flow_style=False)
        logger.info("已缓存上下文长度 %s -> %s token", key, f"{length:,}")
    except Exception as e:
        logger.debug("保存上下文长度缓存失败: %s", e)


def get_cached_context_length(model: str, base_url: str) -> Optional[int]:
    """查找之前发现的模型+提供者上下文长度。"""
    key = f"{model}@{base_url}"
    cache = _load_context_cache()
    return cache.get(key)


def get_next_probe_tier(current_length: int) -> Optional[int]:
    """返回下一个较低的探测层级,如果已在最低则返回 None。"""
    for tier in CONTEXT_PROBE_TIERS:
        if tier < current_length:
            return tier
    return None


def parse_context_limit_from_error(error_msg: str) -> Optional[int]:
    """尝试从 API 错误消息中提取实际的上下文限制。

    许多提供者在其错误文本中包含限制,例如:
      - "maximum context length is 32768 tokens"
      - "context_length_exceeded: 131072"
      - "Maximum context size 32768 exceeded"
      - "model's max context length is 65536"
    """
    error_lower = error_msg.lower()
    # 模式:查找上下文相关关键词附近的数字
    patterns = [
        r'(?:max(?:imum)?|limit)\s*(?:context\s*)?(?:length|size|window)?\s*(?:is|of|:)?\s*(\d{4,})',
        r'context\s*(?:length|size|window)\s*(?:is|of|:)?\s*(\d{4,})',
        r'(\d{4,})\s*(?:token)?\s*(?:context|limit)',
        r'>\s*(\d{4,})\s*(?:max|limit|token)',  # "250000 tokens > 200000 maximum"
        r'(\d{4,})\s*(?:max(?:imum)?)\b',  # "200000 maximum"
    ]
    for pattern in patterns:
        match = re.search(pattern, error_lower)
        if match:
            limit = int(match.group(1))
            # 健全性检查:必须是合理的上下文长度
            if 1024 <= limit <= 10_000_000:
                return limit
    return None


def _model_id_matches(candidate_id: str, lookup_model: str) -> bool:
    """如果 *candidate_id*(来自服务器)匹配 *lookup_model*(配置的)则返回 True。

    支持两种形式:
    - 精确匹配: "nvidia-nemotron-super-49b-v1" == "nvidia-nemotron-super-49b-v1"
    - Slug 匹配: "nvidia/nvidia-nemotron-super-49b-v1" 匹配 "nvidia-nemotron-super-49b-v1"
                    (最后一个 "/" 之后的部分等于 lookup_model)

    这覆盖了 LM Studio 的原生 API,它将模型存储为 "publisher/slug",
    而用户通常在 "local:" 前缀后只配置 slug。
    """
    if candidate_id == lookup_model:
        return True
    # Slug 匹配: candidate 的基本名等于查找名
    if "/" in candidate_id and candidate_id.rsplit("/", 1)[1] == lookup_model:
        return True
    return False


def query_ollama_num_ctx(model: str, base_url: str) -> Optional[int]:
    """查询 Ollama 服务器获取模型的上下文长度。

    通过 ``/api/show`` 从 GGUF 元数据返回模型的最大上下文,
    或如果设置了 Modelfile 中的显式 ``num_ctx`` 则返回它。
    如果服务器不可达或不是 Ollama 则返回 None。

    这是应该作为 Ollama 聊天请求中 ``num_ctx`` 传递的值,
    以覆盖默认的 2048。
    """
    import httpx

    bare_model = _strip_provider_prefix(model)
    server_url = base_url.rstrip("/")
    if server_url.endswith("/v1"):
        server_url = server_url[:-3]

    try:
        server_type = detect_local_server_type(base_url)
    except Exception:
        return None
    if server_type != "ollama":
        return None

    try:
        with httpx.Client(timeout=3.0) as client:
            resp = client.post(f"{server_url}/api/show", json={"name": bare_model})
            if resp.status_code != 200:
                return None
            data = resp.json()

            # 优先使用 Modelfile 参数中的显式 num_ctx(用户覆盖)
            params = data.get("parameters", "")
            if "num_ctx" in params:
                for line in params.split("\n"):
                    if "num_ctx" in line:
                        parts = line.strip().split()
                        if len(parts) >= 2:
                            try:
                                return int(parts[-1])
                            except ValueError:
                                pass

            # 回退到 GGUF model_info context_length(训练最大值)
            model_info = data.get("model_info", {})
            for key, value in model_info.items():
                if "context_length" in key and isinstance(value, (int, float)):
                    return int(value)
    except Exception:
        pass
    return None


def _query_local_context_length(model: str, base_url: str) -> Optional[int]:
    """查询本地服务器获取模型的上下文长度。"""
    import httpx

    # 剥离已识别的提供者前缀(例如 "local:model-name" → "model-name")。
    # Ollama "model:tag" 冒号(例如 "qwen3.5:27b")被有意保留。
    model = _strip_provider_prefix(model)

    # 剥离 /v1 后缀以获取服务器根目录
    server_url = base_url.rstrip("/")
    if server_url.endswith("/v1"):
        server_url = server_url[:-3]

    try:
        server_type = detect_local_server_type(base_url)
    except Exception:
        server_type = None

    try:
        with httpx.Client(timeout=3.0) as client:
            # Ollama: /api/show 返回带有上下文信息的模型详情
            if server_type == "ollama":
                resp = client.post(f"{server_url}/api/show", json={"name": model})
                if resp.status_code == 200:
                    data = resp.json()
                    # 检查 model_info 获取上下文长度
                    model_info = data.get("model_info", {})
                    for key, value in model_info.items():
                        if "context_length" in key and isinstance(value, (int, float)):
                            return int(value)
                    # 检查参数字符串中的 num_ctx
                    params = data.get("parameters", "")
                    if "num_ctx" in params:
                        for line in params.split("\n"):
                            if "num_ctx" in line:
                                parts = line.strip().split()
                                if len(parts) >= 2:
                                    try:
                                        return int(parts[-1])
                                    except ValueError:
                                        pass

            # LM Studio 原生 API: /api/v1/models 返回 max_context_length。
            # 这比 OpenAI 兼容的 /v1/models 更可靠,后者不包含
            # LM Studio 服务器的上下文窗口信息。
            # 使用 _model_id_matches 进行模糊匹配: LM Studio 将模型存储为
            # "publisher/slug",但用户在 "local:" 前缀后只配置 "slug"。
            if server_type == "lm-studio":
                resp = client.get(f"{server_url}/api/v1/models")
                if resp.status_code == 200:
                    data = resp.json()
                    for m in data.get("models", []):
                        if _model_id_matches(m.get("key", ""), model) or _model_id_matches(m.get("id", ""), model):
                            # 优先使用已加载实例的上下文(实际运行时值)
                            for inst in m.get("loaded_instances", []):
                                cfg = inst.get("config", {})
                                ctx = cfg.get("context_length")
                                if ctx and isinstance(ctx, (int, float)):
                                    return int(ctx)
                            # 回退到 max_context_length(理论模型最大值)
                            ctx = m.get("max_context_length") or m.get("context_length")
                            if ctx and isinstance(ctx, (int, float)):
                                return int(ctx)

            # LM Studio / vLLM / llama.cpp: 尝试 /v1/models/{model}
            resp = client.get(f"{server_url}/v1/models/{model}")
            if resp.status_code == 200:
                data = resp.json()
                # vLLM 返回 max_model_len
                ctx = data.get("max_model_len") or data.get("context_length") or data.get("max_tokens")
                if ctx and isinstance(ctx, (int, float)):
                    return int(ctx)

            # 尝试 /v1/models 并在列表中查找模型。
            # 使用 _model_id_matches 处理 "publisher/slug" vs 裸 "slug"。
            resp = client.get(f"{server_url}/v1/models")
            if resp.status_code == 200:
                data = resp.json()
                models_list = data.get("data", [])
                for m in models_list:
                    if _model_id_matches(m.get("id", ""), model):
                        ctx = m.get("max_model_len") or m.get("context_length") or m.get("max_tokens")
                        if ctx and isinstance(ctx, (int, float)):
                            return int(ctx)
    except Exception:
        pass

    return None


def _normalize_model_version(model: str) -> str:
    """规范化版本分隔符用于匹配。

    Nous 使用破折号: claude-opus-4-6, claude-sonnet-4-5
    OpenRouter 使用点: claude-opus-4.6, claude-sonnet-4.5
    将两者规范化为破折号进行比较。
    """
    return model.replace(".", "-")


def _query_anthropic_context_length(model: str, base_url: str, api_key: str) -> Optional[int]:
    """查询 Anthropic 的 /v1/models 端点获取上下文长度。

    仅适用于常规 ANTHROPIC_API_KEY (sk-ant-api*)。
    OAuth 令牌 (sk-ant-oat*) 来自 Claude Code 会返回 401。
    """
    if not api_key or api_key.startswith("sk-ant-oat"):
        return None  # OAuth 令牌无法访问 /v1/models
    try:
        base = base_url.rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3]
        url = f"{base}/v1/models?limit=1000"
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        }
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code != 200:
            return None
        data = resp.json()
        for m in data.get("data", []):
            if m.get("id") == model:
                ctx = m.get("max_input_tokens")
                if isinstance(ctx, int) and ctx > 0:
                    return ctx
    except Exception as e:
        logger.debug("Anthropic /v1/models 查询失败: %s", e)
    return None


def _resolve_nous_context_length(model: str) -> Optional[int]:
    """通过 OpenRouter 元数据解析 Nous Portal 模型上下文长度。

    Nous 模型 ID 是裸的(例如 'claude-opus-4-6'),而 OpenRouter 使用
    带前缀的 ID(例如 'anthropic/claude-opus-4.6')。尝试后缀匹配
    并进行版本规范化(点↔破折号)。
    """
    metadata = fetch_model_metadata()  # OpenRouter cache
    # 精确匹配优先
    if model in metadata:
        return metadata[model].get("context_length")

    normalized = _normalize_model_version(model).lower()

    for or_id, entry in metadata.items():
        bare = or_id.split("/", 1)[1] if "/" in or_id else or_id
        if bare.lower() == model.lower() or _normalize_model_version(bare).lower() == normalized:
            return entry.get("context_length")

    # 部分前缀匹配,例如 gemini-3-flash → gemini-3-flash-preview
    # 要求匹配在词边界处(后面跟着 -、: 或字符串结尾)
    model_lower = model.lower()
    for or_id, entry in metadata.items():
        bare = or_id.split("/", 1)[1] if "/" in or_id else or_id
        for candidate, query in [(bare.lower(), model_lower), (_normalize_model_version(bare).lower(), normalized)]:
            if candidate.startswith(query) and (
                len(candidate) == len(query) or candidate[len(query)] in "-:."
            ):
                return entry.get("context_length")

    return None


def get_model_context_length(
    model: str,
    base_url: str = "",
    api_key: str = "",
    config_context_length: int | None = None,
    provider: str = "",
) -> int:
    """获取模型的上下文长度。

    解析顺序:
    0. 显式配置覆盖(model.context_length 或 custom_providers 每模型)
    1. 持久化缓存(之前通过探测发现的)
    2. 活动端点元数据(显式自定义端点的 /models)
    3. 本地服务器查询(用于本地端点)
    4. Anthropic /v1/models API(仅 API-key 用户,非 OAuth)
    5. OpenRouter 实时 API 元数据
    6. Nous 后缀匹配(通过 OpenRouter 缓存)
    7. models.dev 注册表查找(感知提供者)
    8. 精简的硬编码默认值(宽泛家族模式)
    9. 默认回退(128K)
    """
    # 0. 显式配置覆盖 — 用户最清楚
    if config_context_length is not None and isinstance(config_context_length, int) and config_context_length > 0:
        return config_context_length

    # 规范化带提供者前缀的模型名(例如 "local:model-name" →
    # "model-name"),以便缓存查找和服务器查询使用本地服务器
    # 实际知道的裸 ID。Ollama "model:tag" 冒号被保留。
    model = _strip_provider_prefix(model)

    # 1. 检查持久化缓存(model+provider)
    if base_url:
        cached = get_cached_context_length(model, base_url)
        if cached is not None:
            return cached

    # 2. 真正自定义/未知端点的活动端点元数据。
    # 已知提供者(Copilot、OpenAI、Anthropic 等)跳过此步 — 它们的
    # /models 端点可能报告提供者施加的限制(例如 Copilot 返回 128k)
    # 而非模型的完整上下文(400k)。models.dev 有正确的每提供者值,
    # 在步骤 5+ 中检查。
    if _is_custom_endpoint(base_url) and not _is_known_provider_base_url(base_url):
        endpoint_metadata = fetch_endpoint_model_metadata(base_url, api_key=api_key)
        matched = endpoint_metadata.get(model)
        if not matched:
            # 单模型服务器:如果只加载了一个模型,使用它
            if len(endpoint_metadata) == 1:
                matched = next(iter(endpoint_metadata.values()))
            else:
                    # 模糊匹配:子串双向匹配
                for key, entry in endpoint_metadata.items():
                    if model in key or key in model:
                        matched = entry
                        break
        if matched:
            context_length = matched.get("context_length")
            if isinstance(context_length, int):
                return context_length
        if not _is_known_provider_base_url(base_url):
            # 3. 尝试直接查询本地服务器
            if is_local_endpoint(base_url):
                local_ctx = _query_local_context_length(model, base_url)
                if local_ctx and local_ctx > 0:
                    save_context_length(model, base_url, local_ctx)
                    return local_ctx
            logger.info(
                "无法检测模型 %r 在 %s 的上下文长度 — "
                "默认为 %s token(向下探测)。在 config.yaml 中设置 model.context_length "
                "以覆盖。",
                model, base_url, f"{DEFAULT_FALLBACK_CONTEXT:,}",
            )
            return DEFAULT_FALLBACK_CONTEXT

    # 4. Anthropic /v1/models API(仅限常规 API 密钥,非 OAuth)
    if provider == "anthropic" or (
        base_url and "api.anthropic.com" in base_url
    ):
        ctx = _query_anthropic_context_length(model, base_url or "https://api.anthropic.com", api_key)
        if ctx:
            return ctx

    # 5. 感知提供者的查找(在通用 OpenRouter 缓存之前)
    # 这些是提供者特定的,优先于通用 OR 缓存,
    # 因为同一模型在不同提供者可能有不同的上下文限制
    # (例如 claude-opus-4.6 在 Anthropic 为 1M,但在 GitHub Copilot 为 128K)。
    # 如果提供者是通用的(openrouter/custom/空),尝试从 URL 推断。
    effective_provider = provider
    if not effective_provider or effective_provider in ("openrouter", "custom"):
        if base_url:
            inferred = _infer_provider_from_url(base_url)
            if inferred:
                effective_provider = inferred

    if effective_provider == "nous":
        ctx = _resolve_nous_context_length(model)
        if ctx:
            return ctx
    if effective_provider:
        from agent.models_dev import lookup_models_dev_context
        ctx = lookup_models_dev_context(effective_provider, model)
        if ctx:
            return ctx

    # 6. OpenRouter 实时 API 元数据(不感知提供者的回退)
    metadata = fetch_model_metadata()
    if model in metadata:
        return metadata[model].get("context_length", 128000)

    # 8. 硬编码默认值(模糊匹配 — 最长键优先以获得特异性)
    # 仅检查 `default_model in model`(键是否是输入的子串)。
    # 反向(`model in default_model`)会导致较短名称如
    # "claude-sonnet-4" 错误匹配 "claude-sonnet-4-6" 并返回 1M。
    model_lower = model.lower()
    for default_model, length in sorted(
        DEFAULT_CONTEXT_LENGTHS.items(), key=lambda x: len(x[0]), reverse=True
    ):
        if default_model in model_lower:
            return length

    # 9. 最后手段:查询本地服务器
    if base_url and is_local_endpoint(base_url):
        local_ctx = _query_local_context_length(model, base_url)
        if local_ctx and local_ctx > 0:
            save_context_length(model, base_url, local_ctx)
            return local_ctx

    # 10. 默认回退 — 128K
    return DEFAULT_FALLBACK_CONTEXT


def estimate_tokens_rough(text: str) -> int:
    """粗略 token 估算(~4 字符/token),用于预检。"""
    if not text:
        return 0
    return len(text) // 4


def estimate_messages_tokens_rough(messages: List[Dict[str, Any]]) -> int:
    """消息列表的粗略 token 估算(仅用于预检)。"""
    total_chars = sum(len(str(msg)) for msg in messages)
    return total_chars // 4


def estimate_request_tokens_rough(
    messages: List[Dict[str, Any]],
    *,
    system_prompt: str = "",
    tools: Optional[List[Dict[str, Any]]] = None,
) -> int:
    """完整 chat-completions 请求的粗略 token 估算。

    包含 KClaw 发送给提供者的主要载荷桶:
    系统提示词、对话消息和工具 schema。启用 50+
    工具时,仅 schema 就可增加 20-30K token — 仅计算
    消息时的一个显著盲点。
    """
    total_chars = 0
    if system_prompt:
        total_chars += len(system_prompt)
    if messages:
        total_chars += sum(len(str(msg)) for msg in messages)
    if tools:
        total_chars += len(str(tools))
    return total_chars // 4
