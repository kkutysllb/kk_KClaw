"""models.dev 注册表集成 — 提供者和模型的主要数据库。

从 https://models.dev/api.json 获取 — 一个社区维护的数据库,
包含 4000+ 模型和 109+ 提供者。提供:

- **提供者元数据**: 名称、base URL、环境变量、文档链接
- **模型元数据**: 上下文窗口、最大输出、每百万 token 成本、能力
  (推理、工具、视觉、PDF、音频)、模态、知识截止日期、
  开放权重标志、家族分组、弃用状态

数据解析顺序(类似 TypeScript OpenCode):
  1. 捆绑快照(随包发布 — 离线优先)
  2. 磁盘缓存(~/.kclaw/models_dev_cache.json)
  3. 网络获取(https://models.dev/api.json)
  4. 后台每 60 分钟刷新

其他模块应从此处导入 dataclass 和查询函数,
而不是自己解析原始 JSON。
"""

import difflib
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from utils import atomic_json_write

import requests

logger = logging.getLogger(__name__)

MODELS_DEV_URL = "https://models.dev/api.json"
_MODELS_DEV_CACHE_TTL = 3600  # 1 hour in-memory

# 内存缓存
_models_dev_cache: Dict[str, Any] = {}
_models_dev_cache_time: float = 0


# ---------------------------------------------------------------------------
# Dataclass — 提供者和模型的丰富元数据
# ---------------------------------------------------------------------------

@dataclass
class ModelInfo:
    """来自 models.dev 的单个模型的完整元数据。"""

    id: str
    name: str
    family: str
    provider_id: str        # models.dev 提供者 ID(例如 "anthropic")

    # 能力
    reasoning: bool = False
    tool_call: bool = False
    attachment: bool = False       # 支持图片/文件附件(视觉)
    temperature: bool = False
    structured_output: bool = False
    open_weights: bool = False

    # 模态
    input_modalities: Tuple[str, ...] = ()    # ("text", "image", "pdf", ...)
    output_modalities: Tuple[str, ...] = ()

    # 限制
    context_window: int = 0
    max_output: int = 0
    max_input: Optional[int] = None

    # 成本(每百万 token, USD)
    cost_input: float = 0.0
    cost_output: float = 0.0
    cost_cache_read: Optional[float] = None
    cost_cache_write: Optional[float] = None

    # 元数据
    knowledge_cutoff: str = ""
    release_date: str = ""
    status: str = ""          # "alpha"、"beta"、"deprecated" 或 ""
    interleaved: Any = False  # True or {"field": "reasoning_content"}

    def has_cost_data(self) -> bool:
        return self.cost_input > 0 or self.cost_output > 0

    def supports_vision(self) -> bool:
        return self.attachment or "image" in self.input_modalities

    def supports_pdf(self) -> bool:
        return "pdf" in self.input_modalities

    def supports_audio_input(self) -> bool:
        return "audio" in self.input_modalities

    def format_cost(self) -> str:
        """人类可读的成本字符串,例如 '$3.00/M 输入, $15.00/M 输出'。"""
        if not self.has_cost_data():
            return "unknown"
        parts = [f"${self.cost_input:.2f}/M in", f"${self.cost_output:.2f}/M out"]
        if self.cost_cache_read is not None:
            parts.append(f"cache read ${self.cost_cache_read:.2f}/M")
        return ", ".join(parts)

    def format_capabilities(self) -> str:
        """人类可读的能力描述,例如 '推理, 工具, 视觉, PDF'。"""
        caps = []
        if self.reasoning:
            caps.append("reasoning")
        if self.tool_call:
            caps.append("tools")
        if self.supports_vision():
            caps.append("vision")
        if self.supports_pdf():
            caps.append("PDF")
        if self.supports_audio_input():
            caps.append("audio")
        if self.structured_output:
            caps.append("structured output")
        if self.open_weights:
            caps.append("open weights")
        return ", ".join(caps) if caps else "basic"


@dataclass
class ProviderInfo:
    """来自 models.dev 的提供者完整元数据。"""

    id: str                         # models.dev 提供者 ID
    name: str                       # 显示名称
    env: Tuple[str, ...]            # API 密钥的环境变量名
    api: str                        # base URL
    doc: str = ""                   # 文档 URL
    model_count: int = 0

    def has_api_url(self) -> bool:
        return bool(self.api)


# ---------------------------------------------------------------------------
# 提供者 ID 映射: KClaw ↔ models.dev
# ---------------------------------------------------------------------------

# KClaw 提供者名称 → models.dev 提供者 ID
PROVIDER_TO_MODELS_DEV: Dict[str, str] = {
    "openrouter": "openrouter",
    "anthropic": "anthropic",
    "zai": "zai",
    "kimi-coding": "kimi-for-coding",
    "minimax": "minimax",
    "minimax-cn": "minimax-cn",
    "deepseek": "deepseek",
    "alibaba": "alibaba",
    "qwen-oauth": "alibaba",
    "copilot": "github-copilot",
    "ai-gateway": "vercel",
    "opencode-zen": "opencode",
    "opencode-go": "opencode-go",
    "kilocode": "kilo",
    "fireworks": "fireworks-ai",
    "huggingface": "huggingface",
    "gemini": "google",
    "google": "google",
    "xai": "xai",
    "nvidia": "nvidia",
    "groq": "groq",
    "mistral": "mistral",
    "togetherai": "togetherai",
    "perplexity": "perplexity",
    "cohere": "cohere",
}

# 反向映射: models.dev → KClaw(惰性构建)
_MODELS_DEV_TO_PROVIDER: Optional[Dict[str, str]] = None


def _get_reverse_mapping() -> Dict[str, str]:
    """返回 models.dev ID → KClaw 提供者 ID 的映射。"""
    global _MODELS_DEV_TO_PROVIDER
    if _MODELS_DEV_TO_PROVIDER is None:
        _MODELS_DEV_TO_PROVIDER = {v: k for k, v in PROVIDER_TO_MODELS_DEV.items()}
    return _MODELS_DEV_TO_PROVIDER


def _get_cache_path() -> Path:
    """返回磁盘缓存文件路径。"""
    from kclaw_constants import get_kclaw_home
    return get_kclaw_home() / "models_dev_cache.json"


def _load_disk_cache() -> Dict[str, Any]:
    """从磁盘缓存加载 models.dev 数据。"""
    try:
        cache_path = _get_cache_path()
        if cache_path.exists():
            with open(cache_path, encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.debug("加载 models.dev 磁盘缓存失败: %s", e)
    return {}


def _save_disk_cache(data: Dict[str, Any]) -> None:
    """原子性地将 models.dev 数据保存到磁盘缓存。"""
    try:
        cache_path = _get_cache_path()
        atomic_json_write(cache_path, data, indent=None, separators=(",", ":"))
    except Exception as e:
        logger.debug("保存 models.dev 磁盘缓存失败: %s", e)


def fetch_models_dev(force_refresh: bool = False) -> Dict[str, Any]:
    """获取 models.dev 注册表。内存缓存(1小时) + 磁盘回退。

    返回以提供者 ID 为键的完整注册表字典,失败则返回空字典。
    """
    global _models_dev_cache, _models_dev_cache_time

    # 检查内存缓存
    if (
        not force_refresh
        and _models_dev_cache
        and (time.time() - _models_dev_cache_time) < _MODELS_DEV_CACHE_TTL
    ):
        return _models_dev_cache

    # 尝试网络获取
    try:
        response = requests.get(MODELS_DEV_URL, timeout=15)
        response.raise_for_status()
        data = response.json()
        if isinstance(data, dict) and data:
            _models_dev_cache = data
            _models_dev_cache_time = time.time()
            _save_disk_cache(data)
            logger.debug(
                "Fetched models.dev registry: %d providers, %d total models",
                len(data),
                sum(len(p.get("models", {})) for p in data.values() if isinstance(p, dict)),
            )
            return data
    except Exception as e:
        logger.debug("Failed to fetch models.dev: %s", e)

    # 回退到磁盘缓存 — 使用较短的 TTL(5分钟)以便我们很快
    # 重试网络获取,而不是提供一整小时的过期数据。
    if not _models_dev_cache:
        _models_dev_cache = _load_disk_cache()
        if _models_dev_cache:
            _models_dev_cache_time = time.time() - _MODELS_DEV_CACHE_TTL + 300
            logger.debug("从磁盘缓存加载 models.dev(%d 个提供者)", len(_models_dev_cache))

    return _models_dev_cache


def lookup_models_dev_context(provider: str, model: str) -> Optional[int]:
    """在 models.dev 中查找提供者+模型的 context_length。

    返回以 token 为单位的上下文窗口,如果未找到则返回 None。
    处理不区分大小写的匹配,并过滤掉 context=0 的条目。
    """
    mdev_provider_id = PROVIDER_TO_MODELS_DEV.get(provider)
    if not mdev_provider_id:
        return None

    data = fetch_models_dev()
    provider_data = data.get(mdev_provider_id)
    if not isinstance(provider_data, dict):
        return None

    models = provider_data.get("models", {})
    if not isinstance(models, dict):
        return None

    # 精确匹配
    entry = models.get(model)
    if entry:
        ctx = _extract_context(entry)
        if ctx:
            return ctx

    # 不区分大小写匹配
    model_lower = model.lower()
    for mid, mdata in models.items():
        if mid.lower() == model_lower:
            ctx = _extract_context(mdata)
            if ctx:
                return ctx

    return None


def _extract_context(entry: Dict[str, Any]) -> Optional[int]:
    """从 models.dev 模型条目中提取 context_length。

    对无效/零值返回 None(某些音频/图像模型的 context=0)。
    """
    if not isinstance(entry, dict):
        return None
    limit = entry.get("limit")
    if not isinstance(limit, dict):
        return None
    ctx = limit.get("context")
    if isinstance(ctx, (int, float)) and ctx > 0:
        return int(ctx)
    return None


# ---------------------------------------------------------------------------
# 模型能力元数据
# ---------------------------------------------------------------------------


@dataclass
class ModelCapabilities:
    """来自 models.dev 的模型结构化能力元数据。"""

    supports_tools: bool = True
    supports_vision: bool = False
    supports_reasoning: bool = False
    context_window: int = 200000
    max_output_tokens: int = 8192
    model_family: str = ""


def _get_provider_models(provider: str) -> Optional[Dict[str, Any]]:
    """将 KClaw 提供者 ID 解析为其 models.dev 模型字典。

    返回模型字典,如果提供者未知或无数据则返回 None。
    """
    mdev_provider_id = PROVIDER_TO_MODELS_DEV.get(provider)
    if not mdev_provider_id:
        return None

    data = fetch_models_dev()
    provider_data = data.get(mdev_provider_id)
    if not isinstance(provider_data, dict):
        return None

    models = provider_data.get("models", {})
    if not isinstance(models, dict):
        return None

    return models


def _find_model_entry(models: Dict[str, Any], model: str) -> Optional[Dict[str, Any]]:
    """通过精确匹配查找模型条目,然后是不区分大小写的回退。"""
    # 精确匹配
    entry = models.get(model)
    if isinstance(entry, dict):
        return entry

    # 不区分大小写匹配
    model_lower = model.lower()
    for mid, mdata in models.items():
        if mid.lower() == model_lower and isinstance(mdata, dict):
            return mdata

    return None


def get_model_capabilities(provider: str, model: str) -> Optional[ModelCapabilities]:
    """从 models.dev 缓存查找完整的能力元数据。

    使用现有的 fetch_models_dev() 和 PROVIDER_TO_MODELS_DEV 映射。
    如果模型未找到则返回 None。

    从模型条目字段中提取:
      - reasoning  (bool)  → supports_reasoning
      - tool_call  (bool)  → supports_tools
      - attachment (bool)  → supports_vision
      - limit.context (int) → context_window
      - limit.output  (int) → max_output_tokens
      - family     (str)   → model_family
    """
    models = _get_provider_models(provider)
    if models is None:
        return None

    entry = _find_model_entry(models, model)
    if entry is None:
        return None

    # 提取能力标志(缺失时默认为 False)
    supports_tools = bool(entry.get("tool_call", False))
    supports_vision = bool(entry.get("attachment", False))
    supports_reasoning = bool(entry.get("reasoning", False))

    # 提取限制
    limit = entry.get("limit", {})
    if not isinstance(limit, dict):
        limit = {}

    ctx = limit.get("context")
    context_window = int(ctx) if isinstance(ctx, (int, float)) and ctx > 0 else 200000

    out = limit.get("output")
    max_output_tokens = int(out) if isinstance(out, (int, float)) and out > 0 else 8192

    model_family = entry.get("family", "") or ""

    return ModelCapabilities(
        supports_tools=supports_tools,
        supports_vision=supports_vision,
        supports_reasoning=supports_reasoning,
        context_window=context_window,
        max_output_tokens=max_output_tokens,
        model_family=model_family,
    )


def list_provider_models(provider: str) -> List[str]:
    """返回 models.dev 中某提供者的所有模型 ID。

    如果提供者未知或无数据则返回空列表。
    """
    models = _get_provider_models(provider)
    if models is None:
        return []
    return list(models.keys())


# 指示非代理或噪声模型(TTS、嵌入、过期预览快照、
# 仅直播/流式、仅图像)的模式。
import re
_NOISE_PATTERNS: re.Pattern = re.compile(
    r"-tts\b|embedding|live-|-(preview|exp)-\d{2,4}[-_]|"
    r"-image\b|-image-preview\b|-customtools\b",
    re.IGNORECASE,
)


def list_agentic_models(provider: str) -> List[str]:
    """返回 models.dev 中适合代理使用的模型 ID。

    过滤 tool_call=True 并排除噪声(TTS、嵌入、过期预览快照、
    仅直播/流式、仅图像模型)。任何失败时返回空列表。
    """
    models = _get_provider_models(provider)
    if models is None:
        return []

    result = []
    for mid, entry in models.items():
        if not isinstance(entry, dict):
            continue
        if not entry.get("tool_call", False):
            continue
        if _NOISE_PATTERNS.search(mid):
            continue
        result.append(mid)
    return result


def search_models_dev(
    query: str, provider: str = None, limit: int = 5
) -> List[Dict[str, Any]]:
    """在 models.dev 目录中进行模糊搜索。返回匹配的模型条目。

    Args:
        query: 用于匹配模型 ID 的搜索字符串。
        provider: 可选的 KClaw 提供者 ID 以限制搜索范围。
                  如果为 None,在 PROVIDER_TO_MODELS_DEV 中的所有提供者中搜索。
        limit: 要返回的最大结果数。

    Returns:
        dict 列表,每个包含 'provider'、'model_id' 和来自 models.dev 的
        完整模型 'entry'。
    """
    data = fetch_models_dev()
    if not data:
        return []

    # 构建 (provider_id, model_id, entry) 候选列表
    candidates: List[tuple] = []

    if provider is not None:
        # 仅搜索指定提供者
        mdev_provider_id = PROVIDER_TO_MODELS_DEV.get(provider)
        if not mdev_provider_id:
            return []
        provider_data = data.get(mdev_provider_id, {})
        if isinstance(provider_data, dict):
            models = provider_data.get("models", {})
            if isinstance(models, dict):
                for mid, mdata in models.items():
                    candidates.append((provider, mid, mdata))
    else:
        # 搜索所有映射的提供者
        for kclaw_prov, mdev_prov in PROVIDER_TO_MODELS_DEV.items():
            provider_data = data.get(mdev_prov, {})
            if isinstance(provider_data, dict):
                models = provider_data.get("models", {})
                if isinstance(models, dict):
                    for mid, mdata in models.items():
                        candidates.append((kclaw_prov, mid, mdata))

    if not candidates:
        return []

    # 使用 difflib 进行模糊匹配 — 不区分大小写比较
    model_ids_lower = [c[1].lower() for c in candidates]
    query_lower = query.lower()

    # 首先尝试精确子串匹配(比纯编辑距离更直观)
    substring_matches = []
    for prov, mid, mdata in candidates:
        if query_lower in mid.lower():
            substring_matches.append({"provider": prov, "model_id": mid, "entry": mdata})

    # 然后为剩余槽位添加 difflib 模糊匹配
    fuzzy_ids = difflib.get_close_matches(
        query_lower, model_ids_lower, n=limit * 2, cutoff=0.4
    )

    seen_ids: set = set()
    results: List[Dict[str, Any]] = []

    # 优先处理子串匹配
    for match in substring_matches:
        key = (match["provider"], match["model_id"])
        if key not in seen_ids:
            seen_ids.add(key)
            results.append(match)
            if len(results) >= limit:
                return results

    # 添加模糊匹配
    for fid in fuzzy_ids:
        # 查找与此小写 ID 匹配的原始大小写候选
        for prov, mid, mdata in candidates:
            if mid.lower() == fid:
                key = (prov, mid)
                if key not in seen_ids:
                    seen_ids.add(key)
                    results.append({"provider": prov, "model_id": mid, "entry": mdata})
                    if len(results) >= limit:
                        return results

    return results


# ---------------------------------------------------------------------------
# 丰富 dataclass 构造器 — 将原始 models.dev JSON 解析为 dataclass
# ---------------------------------------------------------------------------

def _parse_model_info(model_id: str, raw: Dict[str, Any], provider_id: str) -> ModelInfo:
    """将原始 models.dev 模型条目字典转换为 ModelInfo dataclass。"""
    limit = raw.get("limit") or {}
    if not isinstance(limit, dict):
        limit = {}

    cost = raw.get("cost") or {}
    if not isinstance(cost, dict):
        cost = {}

    modalities = raw.get("modalities") or {}
    if not isinstance(modalities, dict):
        modalities = {}

    input_mods = modalities.get("input") or []
    output_mods = modalities.get("output") or []

    ctx = limit.get("context")
    ctx_int = int(ctx) if isinstance(ctx, (int, float)) and ctx > 0 else 0
    out = limit.get("output")
    out_int = int(out) if isinstance(out, (int, float)) and out > 0 else 0
    inp = limit.get("input")
    inp_int = int(inp) if isinstance(inp, (int, float)) and inp > 0 else None

    return ModelInfo(
        id=model_id,
        name=raw.get("name", "") or model_id,
        family=raw.get("family", "") or "",
        provider_id=provider_id,
        reasoning=bool(raw.get("reasoning", False)),
        tool_call=bool(raw.get("tool_call", False)),
        attachment=bool(raw.get("attachment", False)),
        temperature=bool(raw.get("temperature", False)),
        structured_output=bool(raw.get("structured_output", False)),
        open_weights=bool(raw.get("open_weights", False)),
        input_modalities=tuple(input_mods) if isinstance(input_mods, list) else (),
        output_modalities=tuple(output_mods) if isinstance(output_mods, list) else (),
        context_window=ctx_int,
        max_output=out_int,
        max_input=inp_int,
        cost_input=float(cost.get("input", 0) or 0),
        cost_output=float(cost.get("output", 0) or 0),
        cost_cache_read=float(cost["cache_read"]) if "cache_read" in cost and cost["cache_read"] is not None else None,
        cost_cache_write=float(cost["cache_write"]) if "cache_write" in cost and cost["cache_write"] is not None else None,
        knowledge_cutoff=raw.get("knowledge", "") or "",
        release_date=raw.get("release_date", "") or "",
        status=raw.get("status", "") or "",
        interleaved=raw.get("interleaved", False),
    )


def _parse_provider_info(provider_id: str, raw: Dict[str, Any]) -> ProviderInfo:
    """将原始 models.dev 提供者条目字典转换为 ProviderInfo。"""
    env = raw.get("env") or []
    models = raw.get("models") or {}
    return ProviderInfo(
        id=provider_id,
        name=raw.get("name", "") or provider_id,
        env=tuple(env) if isinstance(env, list) else (),
        api=raw.get("api", "") or "",
        doc=raw.get("doc", "") or "",
        model_count=len(models) if isinstance(models, dict) else 0,
    )


# ---------------------------------------------------------------------------
# 提供者级查询
# ---------------------------------------------------------------------------

def get_provider_info(provider_id: str) -> Optional[ProviderInfo]:
    """从 models.dev 获取提供者完整元数据。

    接受 KClaw 提供者 ID(例如 "kilocode")或 models.dev ID(例如 "kilo")。
    如果提供者不在目录中则返回 None。
    """
    # 解析 KClaw ID → models.dev ID
    mdev_id = PROVIDER_TO_MODELS_DEV.get(provider_id, provider_id)

    data = fetch_models_dev()
    raw = data.get(mdev_id)
    if not isinstance(raw, dict):
        return None

    return _parse_provider_info(mdev_id, raw)


def list_all_providers() -> Dict[str, ProviderInfo]:
    """返回 models.dev 中的所有提供者,格式为 {provider_id: ProviderInfo}。

    返回完整目录 — 109+ 提供者。对于有 KClaw 别名的提供者,
    models.dev ID 和 KClaw ID 都会包含。
    """
    data = fetch_models_dev()
    result: Dict[str, ProviderInfo] = {}

    for pid, pdata in data.items():
        if isinstance(pdata, dict):
            info = _parse_provider_info(pid, pdata)
            result[pid] = info

    return result


def get_providers_for_env_var(env_var: str) -> List[str]:
    """反向查找:查找使用给定环境变量的所有提供者。

    用于自动检测: "用户设置了 ANTHROPIC_API_KEY,这启用了哪些提供者?"

    返回 models.dev 提供者 ID 列表。
    """
    data = fetch_models_dev()
    matches: List[str] = []

    for pid, pdata in data.items():
        if isinstance(pdata, dict):
            env = pdata.get("env", [])
            if isinstance(env, list) and env_var in env:
                matches.append(pid)

    return matches


# ---------------------------------------------------------------------------
# 模型级查询(丰富 ModelInfo)
# ---------------------------------------------------------------------------

def get_model_info(
    provider_id: str, model_id: str
) -> Optional[ModelInfo]:
    """从 models.dev 获取模型完整元数据。

    接受 KClaw 或 models.dev 提供者 ID。先尝试精确匹配,然后
    不区分大小写的回退。如果未找到则返回 None。
    """
    mdev_id = PROVIDER_TO_MODELS_DEV.get(provider_id, provider_id)

    data = fetch_models_dev()
    pdata = data.get(mdev_id)
    if not isinstance(pdata, dict):
        return None

    models = pdata.get("models", {})
    if not isinstance(models, dict):
        return None

    # 精确匹配
    raw = models.get(model_id)
    if isinstance(raw, dict):
        return _parse_model_info(model_id, raw, mdev_id)

    # 不区分大小写回退
    model_lower = model_id.lower()
    for mid, mdata in models.items():
        if mid.lower() == model_lower and isinstance(mdata, dict):
            return _parse_model_info(mid, mdata, mdev_id)

    return None


def get_model_info_any_provider(model_id: str) -> Optional[ModelInfo]:
    """在所有提供者中按 ID 搜索模型。

    当你有完整的 slug 如 "anthropic/claude-sonnet-4.6" 或裸名称并
    想在任何地方找到它时很有用。先检查 KClaw 映射的提供者,
    然后回退到所有 models.dev 提供者。
    """
    data = fetch_models_dev()

    # 先尝试 KClaw 映射的提供者(更可能是用户想要的)
    for kclaw_id, mdev_id in PROVIDER_TO_MODELS_DEV.items():
        pdata = data.get(mdev_id)
        if not isinstance(pdata, dict):
            continue
        models = pdata.get("models", {})
        if not isinstance(models, dict):
            continue

        raw = models.get(model_id)
        if isinstance(raw, dict):
            return _parse_model_info(model_id, raw, mdev_id)

        # 不区分大小写
        model_lower = model_id.lower()
        for mid, mdata in models.items():
            if mid.lower() == model_lower and isinstance(mdata, dict):
                return _parse_model_info(mid, mdata, mdev_id)

    # 回退到所有提供者
    for pid, pdata in data.items:
        if pid in _get_reverse_mapping():
            continue  # 已检查
        if not isinstance(pdata, dict):
            continue
        models = pdata.get("models", {})
        if not isinstance(models, dict):
            continue

        raw = models.get(model_id)
        if isinstance(raw, dict):
            return _parse_model_info(model_id, raw, pid)

    return None


def list_provider_model_infos(provider_id: str) -> List[ModelInfo]:
    """返回某提供者的所有模型,格式为 ModelInfo 对象。

    默认过滤掉已弃用的模型。
    """
    mdev_id = PROVIDER_TO_MODELS_DEV.get(provider_id, provider_id)

    data = fetch_models_dev()
    pdata = data.get(mdev_id)
    if not isinstance(pdata, dict):
        return []

    models = pdata.get("models", {})
    if not isinstance(models, dict):
        return []

    result: List[ModelInfo] = []
    for mid, mdata in models.items():
        if not isinstance(mdata, dict):
            continue
        status = mdata.get("status", "")
        if status == "deprecated":
            continue
        result.append(_parse_model_info(mid, mdata, mdev_id))

    return result
