"""共享辅助客户端路由器，用于侧任务。

提供单一解析链，使每个消费者（上下文压缩、
会话搜索、网页提取、视觉分析、浏览器视觉）都能
获取最佳可用后端，无需重复回退逻辑。

文本任务的解析顺序（自动模式）：
  1. OpenRouter  (OPENROUTER_API_KEY)
  2. Nous Portal (~/.kclaw/auth.json 活动提供者)
  3. 自定义端点 (config.yaml model.base_url + OPENAI_API_KEY)
  4. Codex OAuth（通过 chatgpt.com 使用 gpt-5.3-codex 的 Responses API，
     封装为 chat.completions 客户端）
  5. 原生 Anthropic
  6. 直接 API 密钥提供者 (z.ai/GLM, Kimi/Moonshot, MiniMax, MiniMax-CN)
  7. None

视觉/多模态任务的解析顺序（自动模式）：
  1. 选定的主提供者，如果它是以下支持的视觉后端之一
  2. OpenRouter
  3. Nous Portal
  4. Codex OAuth（gpt-5.3-codex 通过 Responses API 支持视觉）
  5. 原生 Anthropic
  6. 自定义端点（用于本地视觉模型：Qwen-VL、LLaVA、Pixtral 等）
  7. None

按任务提供者覆盖（如 AUXILIARY_VISION_PROVIDER、
CONTEXT_COMPRESSION_PROVIDER）可以为每个任务强制指定特定提供者。
默认 "auto" 遵循上述链。

按任务模型覆盖（如 AUXILIARY_VISION_MODEL、
AUXILIARY_WEB_EXTRACT_MODEL）允许调用者使用不同于
提供者默认值的模型 slug。

按任务直接端点覆盖（如 AUXILIARY_VISION_BASE_URL、
AUXILIARY_VISION_API_KEY）允许调用者将特定辅助任务
路由到自定义 OpenAI 兼容端点，而无需修改主模型设置。

付费/额度耗尽回退：
  当解析的提供者返回 HTTP 402 或与额度相关的错误时，
  call_llm() 自动使用自动检测链中的下一个可用提供者重试。
  这处理了用户耗尽 OpenRouter 余额但有 Codex OAuth
  或其他提供者可用的常见情况。
"""

import json
import logging
import os
import threading
import time
from pathlib import Path  # noqa: F401 — used by test mocks
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

from openai import OpenAI

from agent.credential_pool import load_pool
from kclaw_cli.config import get_kclaw_home
from kclaw_constants import OPENROUTER_BASE_URL

logger = logging.getLogger(__name__)

_PROVIDER_ALIASES = {
    "google": "gemini",
    "google-gemini": "gemini",
    "google-ai-studio": "gemini",
    "glm": "zai",
    "z-ai": "zai",
    "z.ai": "zai",
    "zhipu": "zai",
    "kimi": "kimi-coding",
    "moonshot": "kimi-coding",
    "minimax-china": "minimax-cn",
    "minimax_cn": "minimax-cn",
    "claude": "anthropic",
    "claude-code": "anthropic",
}


def _normalize_aux_provider(provider: Optional[str], *, for_vision: bool = False) -> str:
    normalized = (provider or "auto").strip().lower()
    if normalized.startswith("custom:"):
        suffix = normalized.split(":", 1)[1].strip()
        if not suffix:
            return "custom"
        normalized = suffix if not for_vision else "custom"
    if normalized == "codex":
        return "openai-codex"
    if normalized == "main":
        # Resolve to the user's actual main provider so named custom providers
        # and non-aggregator providers (DeepSeek, Alibaba, etc.) work correctly.
        main_prov = _read_main_provider()
        if main_prov and main_prov not in ("auto", "main", ""):
            return main_prov
        return "custom"
    return _PROVIDER_ALIASES.get(normalized, normalized)

# Default auxiliary models for direct API-key providers (cheap/fast for side tasks)
_API_KEY_PROVIDER_AUX_MODELS: Dict[str, str] = {
    "gemini": "gemini-3-flash-preview",
    "zai": "glm-4.5-flash",
    "kimi-coding": "kimi-k2-turbo-preview",
    "minimax": "MiniMax-M2.7",
    "minimax-cn": "MiniMax-M2.7",
    "anthropic": "claude-haiku-4-5-20251001",
    "ai-gateway": "google/gemini-3-flash",
    "opencode-zen": "gemini-3-flash",
    "opencode-go": "glm-5",
    "kilocode": "google/gemini-3-flash-preview",
}

# OpenRouter app attribution headers
_OR_HEADERS = {
    "HTTP-Referer": "https://kclaw.nousresearch.com",
    "X-OpenRouter-Title": "KClaw Agent",
    "X-OpenRouter-Categories": "productivity,cli-agent",
}

# Nous Portal extra_body for product attribution.
# Callers should pass this as extra_body in chat.completions.create()
# when the auxiliary client is backed by Nous Portal.
NOUS_EXTRA_BODY = {"tags": ["product=kclaw"]}

# Set at resolve time — True if the auxiliary client points to Nous Portal
auxiliary_is_nous: bool = False

# Default auxiliary models per provider
_OPENROUTER_MODEL = "google/gemini-3-flash-preview"
_NOUS_MODEL = "google/gemini-3-flash-preview"
_NOUS_FREE_TIER_VISION_MODEL = "xiaomi/mimo-v2-omni"
_NOUS_FREE_TIER_AUX_MODEL = "xiaomi/mimo-v2-pro"
_NOUS_DEFAULT_BASE_URL = "https://inference-api.nousresearch.com/v1"
_ANTHROPIC_DEFAULT_BASE_URL = "https://api.anthropic.com"
_AUTH_JSON_PATH = get_kclaw_home() / "auth.json"

# Codex fallback: uses the Responses API (the only endpoint the Codex
# OAuth token can access) with a fast model for auxiliary tasks.
# ChatGPT-backed Codex accounts currently reject gpt-5.3-codex for these
# auxiliary flows, while gpt-5.2-codex remains broadly available and supports
# vision via Responses.
_CODEX_AUX_MODEL = "gpt-5.2-codex"
_CODEX_AUX_BASE_URL = "https://chatgpt.com/backend-api/codex"


def _to_openai_base_url(base_url: str) -> str:
    """将 Anthropic 风格的 base URL 规范化为 OpenAI 兼容格式。

    某些提供者（MiniMax、MiniMax-CN）为 Anthropic Messages API 暴露
    ``/anthropic`` 端点，并为 OpenAI chat completions 暴露单独的 ``/v1``
    端点。辅助客户端使用 OpenAI SDK，因此必须命中 ``/v1`` 接口。
    传入原始 ``inference_base_url`` 会导致请求发送到
    ``/anthropic/chat/completions`` — 返回 404。
    """
    url = str(base_url or "").strip().rstrip("/")
    if url.endswith("/anthropic"):
        rewritten = url[: -len("/anthropic")] + "/v1"
        logger.debug("辅助客户端：重写 base URL %s → %s", url, rewritten)
        return rewritten
    return url


def _select_pool_entry(provider: str) -> Tuple[bool, Optional[Any]]:
    """返回 (pool_exists_for_provider, selected_entry)。"""
    try:
        pool = load_pool(provider)
    except Exception as exc:
        logger.debug("辅助客户端：无法加载 %s 的凭据池：%s", provider, exc)
        return False, None
    if not pool or not pool.has_credentials():
        return False, None
    try:
        return True, pool.select()
    except Exception as exc:
        logger.debug("辅助客户端：无法选择 %s 的池条目：%s", provider, exc)
        return True, None


def _pool_runtime_api_key(entry: Any) -> str:
    if entry is None:
        return ""
    # 使用 PooledCredential.runtime_api_key 属性，它处理
    # 提供者特定的回退（如 nous 的 agent_key）。
    key = getattr(entry, "runtime_api_key", None) or getattr(entry, "access_token", "")
    return str(key or "").strip()


def _pool_runtime_base_url(entry: Any, fallback: str = "") -> str:
    if entry is None:
        return str(fallback or "").strip().rstrip("/")
    # runtime_base_url 处理提供者特定逻辑（如 nous 优先使用 inference_base_url）。
    # 对于非 PooledCredential 条目，通过 inference_base_url 和 base_url 回退。
    url = (
        getattr(entry, "runtime_base_url", None)
        or getattr(entry, "inference_base_url", None)
        or getattr(entry, "base_url", None)
        or fallback
    )
    return str(url or "").strip().rstrip("/")


# ── Codex Responses → chat.completions 适配器 ─────────────────────────────
# 所有辅助消费者调用 client.chat.completions.create(**kwargs) 并
# 读取 response.choices[0].message.content。此适配器将这些调用
# 转换为 Codex Responses API，使调用者无需任何更改。


def _convert_content_for_responses(content: Any) -> Any:
    """将 chat.completions 内容转换为 Responses API 格式。

    chat.completions 使用:
      {"type": "text", "text": "..."}
      {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}

    Responses API 使用:
      {"type": "input_text", "text": "..."}
      {"type": "input_image", "image_url": "data:image/png;base64,..."}

    如果内容是纯字符串，则原样返回（Responses API
    直接接受纯文本消息的字符串）。
    """
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content) if content else ""

    converted: List[Dict[str, Any]] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        ptype = part.get("type", "")
        if ptype == "text":
            converted.append({"type": "input_text", "text": part.get("text", "")})
        elif ptype == "image_url":
            # chat.completions 嵌套 URL: {"image_url": {"url": "..."}}
            image_data = part.get("image_url", {})
            url = image_data.get("url", "") if isinstance(image_data, dict) else str(image_data)
            entry: Dict[str, Any] = {"type": "input_image", "image_url": url}
            # 保留 detail（如果指定）
            detail = image_data.get("detail") if isinstance(image_data, dict) else None
            if detail:
                entry["detail"] = detail
            converted.append(entry)
        elif ptype in ("input_text", "input_image"):
            # 已是 Responses 格式 — 直接传递
            converted.append(part)
        else:
            # 未知内容类型 — 尝试保留为文本
            text = part.get("text", "")
            if text:
                converted.append({"type": "input_text", "text": text})

    return converted or ""


class _CodexCompletionsAdapter:
    """即插即用的适配层，接受 chat.completions.create() kwargs
    并通过 Codex Responses 流式 API 路由它们。"""

    def __init__(self, real_client: OpenAI, model: str):
        self._client = real_client
        self._model = model

    def create(self, **kwargs) -> Any:
        messages = kwargs.get("messages", [])
        model = kwargs.get("model", self._model)

        # 从对话消息中分离 system/instructions。
        # 将 chat.completions 多模态内容块转换为 Responses
        # API 格式（input_text / input_image 替代 text / image_url）。
        instructions = "You are a helpful assistant."
        input_msgs: List[Dict[str, Any]] = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content") or ""
            if role == "system":
                instructions = content if isinstance(content, str) else str(content)
            else:
                input_msgs.append({
                    "role": role,
                    "content": _convert_content_for_responses(content),
                })

        resp_kwargs: Dict[str, Any] = {
            "model": model,
            "instructions": instructions,
            "input": input_msgs or [{"role": "user", "content": ""}],
            "store": False,
        }

        # 注意：Codex 端点 (chatgpt.com/backend-api/codex) 不支持
        # max_output_tokens 或 temperature — 省略以避免 400 错误。

        # 工具支持（flush_memories 和类似调用者）
        tools = kwargs.get("tools")
        if tools:
            converted = []
            for t in tools:
                fn = t.get("function", {}) if isinstance(t, dict) else {}
                name = fn.get("name")
                if not name:
                    continue
                converted.append({
                    "type": "function",
                    "name": name,
                    "description": fn.get("description", ""),
                    "parameters": fn.get("parameters", {}),
                })
            if converted:
                resp_kwargs["tools"] = converted

        # 流式收集响应
        text_parts: List[str] = []
        tool_calls_raw: List[Any] = []
        usage = None

        try:
            # 在流式传输期间收集输出项和文本增量 —
            # Codex 后端可能从 get_final_response() 返回空的 response.output，
            # 即使项目已被流式传输。
            collected_output_items: List[Any] = []
            collected_text_deltas: List[str] = []
            has_function_calls = False
            with self._client.responses.stream(**resp_kwargs) as stream:
                for _event in stream:
                    _etype = getattr(_event, "type", "")
                    if _etype == "response.output_item.done":
                        _done = getattr(_event, "item", None)
                        if _done is not None:
                            collected_output_items.append(_done)
                    elif "output_text.delta" in _etype:
                        _delta = getattr(_event, "delta", "")
                        if _delta:
                            collected_text_deltas.append(_delta)
                    elif "function_call" in _etype:
                        has_function_calls = True
                final = stream.get_final_response()

            # 从收集的流事件中回填空输出
            _output = getattr(final, "output", None)
            if isinstance(_output, list) and not _output:
                if collected_output_items:
                    final.output = list(collected_output_items)
                    logger.debug(
                        "Codex auxiliary: backfilled %d output items from stream events",
                        len(collected_output_items),
                    )
                elif collected_text_deltas and not has_function_calls:
                    # 仅在未流式传输工具调用时合成文本 —
                    # 带有附带文本的 function_call 响应不应
                    # 被折叠为纯文本消息。
                    assembled = "".join(collected_text_deltas)
                    final.output = [SimpleNamespace(
                        type="message", role="assistant", status="completed",
                        content=[SimpleNamespace(type="output_text", text=assembled)],
                    )]
                    logger.debug(
                        "Codex auxiliary: synthesized from %d deltas (%d chars)",
                        len(collected_text_deltas), len(assembled),
                    )

            # 从 Responses 输出中提取文本和工具调用。
            # 项目可能是 SDK 对象（属性）或字典（原始/回退路径），
            # 因此使用处理两种形状的助手函数。
            def _item_get(obj: Any, key: str, default: Any = None) -> Any:
                val = getattr(obj, key, None)
                if val is None and isinstance(obj, dict):
                    val = obj.get(key, default)
                return val if val is not None else default

            for item in getattr(final, "output", []):
                item_type = _item_get(item, "type")
                if item_type == "message":
                    for part in (_item_get(item, "content") or []):
                        ptype = _item_get(part, "type")
                        if ptype in ("output_text", "text"):
                            text_parts.append(_item_get(part, "text", ""))
                elif item_type == "function_call":
                    tool_calls_raw.append(SimpleNamespace(
                        id=_item_get(item, "call_id", ""),
                        type="function",
                        function=SimpleNamespace(
                            name=_item_get(item, "name", ""),
                            arguments=_item_get(item, "arguments", "{}"),
                        ),
                    ))

            resp_usage = getattr(final, "usage", None)
            if resp_usage:
                usage = SimpleNamespace(
                    prompt_tokens=getattr(resp_usage, "input_tokens", 0),
                    completion_tokens=getattr(resp_usage, "output_tokens", 0),
                    total_tokens=getattr(resp_usage, "total_tokens", 0),
                )
        except Exception as exc:
            logger.debug("Codex 辅助 Responses API 调用失败：%s", exc)
            raise

        content = "".join(text_parts).strip() or None

        # 构建看起来像 chat.completions 的响应
        message = SimpleNamespace(
            role="assistant",
            content=content,
            tool_calls=tool_calls_raw or None,
        )
        choice = SimpleNamespace(
            index=0,
            message=message,
            finish_reason="stop" if not tool_calls_raw else "tool_calls",
        )
        return SimpleNamespace(
            choices=[choice],
            model=model,
            usage=usage,
        )


class _CodexChatShim:
    """封装适配器以提供 client.chat.completions.create()。"""

    def __init__(self, adapter: _CodexCompletionsAdapter):
        self.completions = adapter


class CodexAuxiliaryClient:
    """通过 Codex Responses API 路由的 OpenAI 客户端兼容封装。

    消费者可以正常调用 client.chat.completions.create(**kwargs)。
    还暴露 .api_key 和 .base_url 供异步封装器内省。
    """

    def __init__(self, real_client: OpenAI, model: str):
        self._real_client = real_client
        adapter = _CodexCompletionsAdapter(real_client, model)
        self.chat = _CodexChatShim(adapter)
        self.api_key = real_client.api_key
        self.base_url = real_client.base_url

    def close(self):
        self._real_client.close()


class _AsyncCodexCompletionsAdapter:
    """Codex Responses 适配器的异步版本。

    通过 asyncio.to_thread() 封装同步适配器，使异步消费者
    （web_tools、session_search）可以正常 await 它。
    """

    def __init__(self, sync_adapter: _CodexCompletionsAdapter):
        self._sync = sync_adapter

    async def create(self, **kwargs) -> Any:
        import asyncio
        return await asyncio.to_thread(self._sync.create, **kwargs)


class _AsyncCodexChatShim:
    def __init__(self, adapter: _AsyncCodexCompletionsAdapter):
        self.completions = adapter


class AsyncCodexAuxiliaryClient:
    """匹配 AsyncOpenAI.chat.completions.create() 的异步兼容封装。"""

    def __init__(self, sync_wrapper: "CodexAuxiliaryClient"):
        sync_adapter = sync_wrapper.chat.completions
        async_adapter = _AsyncCodexCompletionsAdapter(sync_adapter)
        self.chat = _AsyncCodexChatShim(async_adapter)
        self.api_key = sync_wrapper.api_key
        self.base_url = sync_wrapper.base_url


class _AnthropicCompletionsAdapter:
    """Anthropic Messages API 的 OpenAI 客户端兼容适配器。"""

    def __init__(self, real_client: Any, model: str, is_oauth: bool = False):
        self._client = real_client
        self._model = model
        self._is_oauth = is_oauth

    def create(self, **kwargs) -> Any:
        from agent.anthropic_adapter import build_anthropic_kwargs, normalize_anthropic_response

        messages = kwargs.get("messages", [])
        model = kwargs.get("model", self._model)
        tools = kwargs.get("tools")
        tool_choice = kwargs.get("tool_choice")
        max_tokens = kwargs.get("max_tokens") or kwargs.get("max_completion_tokens") or 2000
        temperature = kwargs.get("temperature")

        normalized_tool_choice = None
        if isinstance(tool_choice, str):
            normalized_tool_choice = tool_choice
        elif isinstance(tool_choice, dict):
            choice_type = str(tool_choice.get("type", "")).lower()
            if choice_type == "function":
                normalized_tool_choice = tool_choice.get("function", {}).get("name")
            elif choice_type in {"auto", "required", "none"}:
                normalized_tool_choice = choice_type

        anthropic_kwargs = build_anthropic_kwargs(
            model=model,
            messages=messages,
            tools=tools,
            max_tokens=max_tokens,
            reasoning_config=None,
            tool_choice=normalized_tool_choice,
            is_oauth=self._is_oauth,
        )
        if temperature is not None:
            anthropic_kwargs["temperature"] = temperature

        response = self._client.messages.create(**anthropic_kwargs)
        assistant_message, finish_reason = normalize_anthropic_response(response)

        usage = None
        if hasattr(response, "usage") and response.usage:
            prompt_tokens = getattr(response.usage, "input_tokens", 0) or 0
            completion_tokens = getattr(response.usage, "output_tokens", 0) or 0
            total_tokens = getattr(response.usage, "total_tokens", 0) or (prompt_tokens + completion_tokens)
            usage = SimpleNamespace(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
            )

        choice = SimpleNamespace(
            index=0,
            message=assistant_message,
            finish_reason=finish_reason,
        )
        return SimpleNamespace(
            choices=[choice],
            model=model,
            usage=usage,
        )


class _AnthropicChatShim:
    def __init__(self, adapter: _AnthropicCompletionsAdapter):
        self.completions = adapter


class AnthropicAuxiliaryClient:
    """原生 Anthropic 客户端上的 OpenAI 客户端兼容封装。"""

    def __init__(self, real_client: Any, model: str, api_key: str, base_url: str, is_oauth: bool = False):
        self._real_client = real_client
        adapter = _AnthropicCompletionsAdapter(real_client, model, is_oauth=is_oauth)
        self.chat = _AnthropicChatShim(adapter)
        self.api_key = api_key
        self.base_url = base_url

    def close(self):
        close_fn = getattr(self._real_client, "close", None)
        if callable(close_fn):
            close_fn()


class _AsyncAnthropicCompletionsAdapter:
    def __init__(self, sync_adapter: _AnthropicCompletionsAdapter):
        self._sync = sync_adapter

    async def create(self, **kwargs) -> Any:
        import asyncio
        return await asyncio.to_thread(self._sync.create, **kwargs)


class _AsyncAnthropicChatShim:
    def __init__(self, adapter: _AsyncAnthropicCompletionsAdapter):
        self.completions = adapter


class AsyncAnthropicAuxiliaryClient:
    def __init__(self, sync_wrapper: "AnthropicAuxiliaryClient"):
        sync_adapter = sync_wrapper.chat.completions
        async_adapter = _AsyncAnthropicCompletionsAdapter(sync_adapter)
        self.chat = _AsyncAnthropicChatShim(async_adapter)
        self.api_key = sync_wrapper.api_key
        self.base_url = sync_wrapper.base_url


def _read_nous_auth() -> Optional[dict]:
    """Read and validate ~/.kclaw/auth.json for an active Nous provider.

    Returns the provider state dict if Nous is active with tokens,
    otherwise None.
    """
    pool_present, entry = _select_pool_entry("nous")
    if pool_present:
        if entry is None:
            return None
        return {
            "access_token": getattr(entry, "access_token", ""),
            "refresh_token": getattr(entry, "refresh_token", None),
            "agent_key": getattr(entry, "agent_key", None),
            "inference_base_url": _pool_runtime_base_url(entry, _NOUS_DEFAULT_BASE_URL),
            "portal_base_url": getattr(entry, "portal_base_url", None),
            "client_id": getattr(entry, "client_id", None),
            "scope": getattr(entry, "scope", None),
            "token_type": getattr(entry, "token_type", "Bearer"),
            "source": "pool",
        }

    try:
        if not _AUTH_JSON_PATH.is_file():
            return None
        data = json.loads(_AUTH_JSON_PATH.read_text())
        if data.get("active_provider") != "nous":
            return None
        provider = data.get("providers", {}).get("nous", {})
        # Must have at least an access_token or agent_key
        if not provider.get("agent_key") and not provider.get("access_token"):
            return None
        return provider
    except Exception as exc:
        logger.debug("Could not read Nous auth: %s", exc)
        return None


def _nous_api_key(provider: dict) -> str:
    """Extract the best API key from a Nous provider state dict."""
    return provider.get("agent_key") or provider.get("access_token", "")


def _nous_base_url() -> str:
    """Resolve the Nous inference base URL from env or default."""
    return os.getenv("NOUS_INFERENCE_BASE_URL", _NOUS_DEFAULT_BASE_URL)


def _read_codex_access_token() -> Optional[str]:
    """Read a valid, non-expired Codex OAuth access token from KClaw auth store.

    If a credential pool exists but currently has no selectable runtime entry
    (for example all pool slots are marked exhausted), fall back to the
    profile's auth.json token instead of hard-failing. This keeps explicit
    fallback-to-Codex working when the pool state is stale but the stored OAuth
    token is still valid.
    """
    pool_present, entry = _select_pool_entry("openai-codex")
    if pool_present:
        token = _pool_runtime_api_key(entry)
        if token:
            return token

    try:
        from kclaw_cli.auth import _read_codex_tokens
        data = _read_codex_tokens()
        tokens = data.get("tokens", {})
        access_token = tokens.get("access_token")
        if not isinstance(access_token, str) or not access_token.strip():
            return None

        # Check JWT expiry — expired tokens block the auto chain and
        # prevent fallback to working providers (e.g. Anthropic).
        try:
            import base64
            payload = access_token.split(".")[1]
            payload += "=" * (-len(payload) % 4)
            claims = json.loads(base64.urlsafe_b64decode(payload))
            exp = claims.get("exp", 0)
            if exp and time.time() > exp:
                logger.debug("Codex access token expired (exp=%s), skipping", exp)
                return None
        except Exception:
            pass  # Non-JWT token or decode error — use as-is

        return access_token.strip()
    except Exception as exc:
        logger.debug("Could not read Codex auth for auxiliary client: %s", exc)
        return None


def _resolve_api_key_provider() -> Tuple[Optional[OpenAI], Optional[str]]:
    """按 PROVIDER_REGISTRY 顺序尝试每个 API 密钥提供者。

    返回第一个具有可用运行时凭据的提供者的 (client, model)，
    如果均未配置则返回 (None, None)。
    """
    try:
        from kclaw_cli.auth import PROVIDER_REGISTRY, resolve_api_key_provider_credentials
    except ImportError:
        logger.debug("无法导入 PROVIDER_REGISTRY 用于 API 密钥回退")
        return None, None

    for provider_id, pconfig in PROVIDER_REGISTRY.items():
        if pconfig.auth_type != "api_key":
            continue
        if provider_id == "anthropic":
            return _try_anthropic()

        pool_present, entry = _select_pool_entry(provider_id)
        if pool_present:
            api_key = _pool_runtime_api_key(entry)
            if not api_key:
                continue

            base_url = _to_openai_base_url(
                _pool_runtime_base_url(entry, pconfig.inference_base_url) or pconfig.inference_base_url
            )
            model = _API_KEY_PROVIDER_AUX_MODELS.get(provider_id, "default")
            logger.debug("辅助文本客户端: %s (%s) 通过凭据池", pconfig.name, model)
            extra = {}
            if "api.kimi.com" in base_url.lower():
                extra["default_headers"] = {"User-Agent": "KimiCLI/1.0"}
            elif "api.githubcopilot.com" in base_url.lower():
                from kclaw_cli.models import copilot_default_headers

                extra["default_headers"] = copilot_default_headers()
            return OpenAI(api_key=api_key, base_url=base_url, **extra), model

        creds = resolve_api_key_provider_credentials(provider_id)
        api_key = str(creds.get("api_key", "")).strip()
        if not api_key:
            continue

        base_url = _to_openai_base_url(
            str(creds.get("base_url", "")).strip().rstrip("/") or pconfig.inference_base_url
        )
        model = _API_KEY_PROVIDER_AUX_MODELS.get(provider_id, "default")
        logger.debug("辅助文本客户端: %s (%s)", pconfig.name, model)
        extra = {}
        if "api.kimi.com" in base_url.lower():
            extra["default_headers"] = {"User-Agent": "KimiCLI/1.0"}
        elif "api.githubcopilot.com" in base_url.lower():
            from kclaw_cli.models import copilot_default_headers

            extra["default_headers"] = copilot_default_headers()
        return OpenAI(api_key=api_key, base_url=base_url, **extra), model

    return None, None


# ── 提供者解析辅助函数 ─────────────────────────────────────────────

def _get_auxiliary_provider(task: str = "") -> str:
    """读取特定辅助任务的提供者覆盖设置。

    先检查 AUXILIARY_{TASK}_PROVIDER（如 AUXILIARY_VISION_PROVIDER），
    然后检查 CONTEXT_{TASK}_PROVIDER（用于压缩部分的 summary_provider），
    最后回退到 "auto"。返回值之一: "auto"、"openrouter"、"nous"、"main"。
    """
    if task:
        for prefix in ("AUXILIARY_", "CONTEXT_"):
            val = os.getenv(f"{prefix}{task.upper()}_PROVIDER", "").strip().lower()
            if val and val != "auto":
                return val
    return "auto"


def _get_auxiliary_env_override(task: str, suffix: str) -> Optional[str]:
    """从 AUXILIARY_* 或 CONTEXT_* 前缀读取辅助环境变量覆盖。"""
    if not task:
        return None
    for prefix in ("AUXILIARY_", "CONTEXT_"):
        val = os.getenv(f"{prefix}{task.upper()}_{suffix}", "").strip()
        if val:
            return val
    return None


def _try_openrouter() -> Tuple[Optional[OpenAI], Optional[str]]:
    pool_present, entry = _select_pool_entry("openrouter")
    if pool_present:
        or_key = _pool_runtime_api_key(entry)
        if not or_key:
            return None, None
        base_url = _pool_runtime_base_url(entry, OPENROUTER_BASE_URL) or OPENROUTER_BASE_URL
        logger.debug("辅助客户端: OpenRouter 通过凭据池")
        return OpenAI(api_key=or_key, base_url=base_url,
                       default_headers=_OR_HEADERS), _OPENROUTER_MODEL

    or_key = os.getenv("OPENROUTER_API_KEY")
    if not or_key:
        return None, None
    logger.debug("辅助客户端: OpenRouter")
    return OpenAI(api_key=or_key, base_url=OPENROUTER_BASE_URL,
                   default_headers=_OR_HEADERS), _OPENROUTER_MODEL


def _try_nous(vision: bool = False) -> Tuple[Optional[OpenAI], Optional[str]]:
    nous = _read_nous_auth()
    if not nous:
        return None, None
    global auxiliary_is_nous
    auxiliary_is_nous = True
    logger.debug("辅助客户端: Nous Portal")
    if nous.get("source") == "pool":
        model = "gemini-3-flash"
    else:
        model = _NOUS_MODEL
    # 免费用户无法使用付费辅助模型 — 使用免费模型替代：
    # mimo-v2-omni 用于视觉任务，mimo-v2-pro 用于文本任务。
    try:
        from kclaw_cli.models import check_nous_free_tier
        if check_nous_free_tier():
            model = _NOUS_FREE_TIER_VISION_MODEL if vision else _NOUS_FREE_TIER_AUX_MODEL
            logger.debug("免费 Nous 账户 — 使用 %s 作为辅助/%s",
                         model, "视觉" if vision else "文本")
    except Exception:
        pass
    return (
        OpenAI(
            api_key=_nous_api_key(nous),
            base_url=str(nous.get("inference_base_url") or _nous_base_url()).rstrip("/"),
        ),
        model,
    )


def _read_main_model() -> str:
    """从 config.yaml 读取用户配置的主模型。

    config.yaml 的 model.default 是活动模型的唯一真实来源。
    不再查询环境变量。
    """
    try:
        from kclaw_cli.config import load_config
        cfg = load_config()
        model_cfg = cfg.get("model", {})
        if isinstance(model_cfg, str) and model_cfg.strip():
            return model_cfg.strip()
        if isinstance(model_cfg, dict):
            default = model_cfg.get("default", "")
            if isinstance(default, str) and default.strip():
                return default.strip()
    except Exception:
        pass
    return ""


def _read_main_provider() -> str:
    """从 config.yaml 读取用户配置的主提供者。

    返回小写的提供者标识（如 "alibaba"、"openrouter"），
    未配置时返回 ""。
    """
    try:
        from kclaw_cli.config import load_config
        cfg = load_config()
        model_cfg = cfg.get("model", {})
        if isinstance(model_cfg, dict):
            provider = model_cfg.get("provider", "")
            if isinstance(provider, str) and provider.strip():
                return provider.strip().lower()
    except Exception:
        pass
    return ""


def _resolve_custom_runtime() -> Tuple[Optional[str], Optional[str]]:
    """以与主 CLI 相同的方式解析活动的自定义/主端点。

    同时覆盖环境变量驱动的 OPENAI_BASE_URL 设置和配置文件保存的
    自定义端点（其中 base URL 存在于 config.yaml 而非实时环境中）。
    """
    try:
        from kclaw_cli.runtime_provider import resolve_runtime_provider

        runtime = resolve_runtime_provider(requested="custom")
    except Exception as exc:
        logger.debug("辅助客户端: 自定义运行时解析失败: %s", exc)
        return None, None

    custom_base = runtime.get("base_url")
    custom_key = runtime.get("api_key")
    if not isinstance(custom_base, str) or not custom_base.strip():
        return None, None

    custom_base = custom_base.strip().rstrip("/")
    if "openrouter.ai" in custom_base.lower():
        # requested='custom' 在未配置自定义端点时会回退到 OpenRouter。
        # 对于辅助路由，将其视为"无自定义端点"。
        return None, None

    # 本地服务器（Ollama、llama.cpp、vLLM、LM Studio）不需要认证。
    # 使用占位密钥 — OpenAI SDK 要求非空字符串，但本地服务器会忽略
    # Authorization 头。与 cli.py _ensure_runtime_credentials() 相同的修复
    # （PR #2556）。
    if not isinstance(custom_key, str) or not custom_key.strip():
        custom_key = "no-key-required"

    return custom_base, custom_key.strip()


def _current_custom_base_url() -> str:
    custom_base, _ = _resolve_custom_runtime()
    return custom_base or ""


def _try_custom_endpoint() -> Tuple[Optional[OpenAI], Optional[str]]:
    custom_base, custom_key = _resolve_custom_runtime()
    if not custom_base or not custom_key:
        return None, None
    model = _read_main_model() or "gpt-4o-mini"
    logger.debug("辅助客户端: 自定义端点 (%s)", model)
    return OpenAI(api_key=custom_key, base_url=custom_base), model


def _try_codex() -> Tuple[Optional[Any], Optional[str]]:
    pool_present, entry = _select_pool_entry("openai-codex")
    if pool_present:
        codex_token = _pool_runtime_api_key(entry)
        if codex_token:
            base_url = _pool_runtime_base_url(entry, _CODEX_AUX_BASE_URL) or _CODEX_AUX_BASE_URL
        else:
            codex_token = _read_codex_access_token()
            if not codex_token:
                return None, None
            base_url = _CODEX_AUX_BASE_URL
    else:
        codex_token = _read_codex_access_token()
        if not codex_token:
            return None, None
        base_url = _CODEX_AUX_BASE_URL
    logger.debug("辅助客户端: Codex OAuth (%s 通过 Responses API)", _CODEX_AUX_MODEL)
    real_client = OpenAI(api_key=codex_token, base_url=base_url)
    return CodexAuxiliaryClient(real_client, _CODEX_AUX_MODEL), _CODEX_AUX_MODEL


def _try_anthropic() -> Tuple[Optional[Any], Optional[str]]:
    try:
        from agent.anthropic_adapter import build_anthropic_client, resolve_anthropic_token
    except ImportError:
        return None, None

    pool_present, entry = _select_pool_entry("anthropic")
    if pool_present:
        if entry is None:
            return None, None
        token = _pool_runtime_api_key(entry)
    else:
        entry = None
        token = resolve_anthropic_token()
    if not token:
        return None, None

    # 允许从 config.yaml model.base_url 覆盖 base URL，但仅当配置的
    # 提供者是 anthropic 时 — 否则非 Anthropic 的 base_url（如 Codex 端点）
    # 会泄露到 Anthropic 请求中。
    base_url = _pool_runtime_base_url(entry, _ANTHROPIC_DEFAULT_BASE_URL) if pool_present else _ANTHROPIC_DEFAULT_BASE_URL
    try:
        from kclaw_cli.config import load_config
        cfg = load_config()
        model_cfg = cfg.get("model")
        if isinstance(model_cfg, dict):
            cfg_provider = str(model_cfg.get("provider") or "").strip().lower()
            if cfg_provider == "anthropic":
                cfg_base_url = (model_cfg.get("base_url") or "").strip().rstrip("/")
                if cfg_base_url:
                    base_url = cfg_base_url
    except Exception:
        pass

    from agent.anthropic_adapter import _is_oauth_token
    is_oauth = _is_oauth_token(token)
    model = _API_KEY_PROVIDER_AUX_MODELS.get("anthropic", "claude-haiku-4-5-20251001")
    logger.debug("辅助客户端: Anthropic 原生 (%s) 于 %s (oauth=%s)", model, base_url, is_oauth)
    try:
        real_client = build_anthropic_client(token, base_url)
    except ImportError:
        # anthropic_adapter 模块可以导入，但 SDK 本身缺失 —
        # build_anthropic_client 在 _anthropic_sdk 为 None 时抛出 ImportError。
        # 视为不可用。
        return None, None
    return AnthropicAuxiliaryClient(real_client, model, token, base_url, is_oauth=is_oauth), model


def _resolve_forced_provider(forced: str) -> Tuple[Optional[OpenAI], Optional[str]]:
    """解析指定的强制提供者。凭据缺失时返回 (None, None)。"""
    if forced == "openrouter":
        client, model = _try_openrouter()
        if client is None:
            logger.warning("auxiliary.provider=openrouter 但 OPENROUTER_API_KEY 未设置")
        return client, model

    if forced == "nous":
        client, model = _try_nous()
        if client is None:
            logger.warning("auxiliary.provider=nous 但 Nous Portal 未配置（运行: kclaw auth）")
        return client, model

    if forced == "codex":
        client, model = _try_codex()
        if client is None:
            logger.warning("auxiliary.provider=codex 但未找到 Codex OAuth token（运行: kclaw model）")
        return client, model

    if forced == "main":
        # "main" = 跳过 OpenRouter/Nous，使用主聊天模型的凭据。
        for try_fn in (_try_custom_endpoint, _try_codex, _resolve_api_key_provider):
            client, model = try_fn()
            if client is not None:
                return client, model
        logger.warning("auxiliary.provider=main 但未找到主端点凭据")
        return None, None

    # 未知提供者名称 — 回退到自动检测
    logger.warning("未知 auxiliary.provider=%r，回退到自动检测", forced)
    return None, None


_AUTO_PROVIDER_LABELS = {
    "_try_openrouter": "openrouter",
    "_try_nous": "nous",
    "_try_custom_endpoint": "local/custom",
    "_try_codex": "openai-codex",
    "_resolve_api_key_provider": "api-key",
}

_AGGREGATOR_PROVIDERS = frozenset({"openrouter", "nous"})


def _get_provider_chain() -> List[tuple]:
    """返回有序的提供者检测链。

    在调用时构建（非模块级别），以便测试中对 ``_try_*`` 函数的
    补丁能被正确捕获。
    """
    return [
        ("openrouter", _try_openrouter),
        ("nous", _try_nous),
        ("local/custom", _try_custom_endpoint),
        ("openai-codex", _try_codex),
        ("api-key", _resolve_api_key_provider),
    ]


def _is_payment_error(exc: Exception) -> bool:
    """检测支付/额度/配额耗尽错误。

    对于 HTTP 402（Payment Required）以及消息表明计费耗尽（而非速率限制）
    的 429/其他错误，返回 True。
    """
    status = getattr(exc, "status_code", None)
    if status == 402:
        return True
    err_lower = str(exc).lower()
    # OpenRouter 和其他提供者在 402 响应体中包含 "credits" 或 "afford"，
    # 但有时会将它们包装在 429 或其他状态码中。
    if status in (402, 429, None):
        if any(kw in err_lower for kw in ("credits", "insufficient funds",
                                           "can only afford", "billing",
                                           "payment required")):
            return True
    return False


def _try_payment_fallback(
    failed_provider: str,
    task: str = None,
) -> Tuple[Optional[Any], Optional[str], str]:
    """在支付/额度错误后尝试替代提供者。

    遍历标准自动检测链，跳过返回支付错误的提供者。

    返回:
        (client, model, provider_label) 或 (None, None, "") 表示无回退可用。
    """
    # 规范化失败的提供者标签用于匹配。
    skip = failed_provider.lower().strip()
    # 如果 Step-1 主提供者路径映射到同一后端，也跳过。
    # （例如 main_provider="openrouter" → 跳过链中的 "openrouter"）
    main_provider = _read_main_provider()
    skip_labels = {skip}
    if main_provider and main_provider.lower() in skip:
        skip_labels.add(main_provider.lower())
    # 将常见的 resolved_provider 值映射回链标签。
    _alias_to_label = {"openrouter": "openrouter", "nous": "nous",
                       "openai-codex": "openai-codex", "codex": "openai-codex",
                       "custom": "local/custom", "local/custom": "local/custom"}
    skip_chain_labels = {_alias_to_label.get(s, s) for s in skip_labels}

    tried = []
    for label, try_fn in _get_provider_chain():
        if label in skip_chain_labels:
            continue
        client, model = try_fn()
        if client is not None:
            logger.info(
                "辅助 %s: %s 发生支付错误 — 回退到 %s (%s)",
                task or "调用", failed_provider, label, model or "默认",
            )
            return client, model, label
        tried.append(label)

    logger.warning(
        "辅助 %s: %s 发生支付错误且无可用回退（已尝试: %s）",
        task or "调用", failed_provider, ", ".join(tried),
    )
    return None, None, ""


def _resolve_auto() -> Tuple[Optional[OpenAI], Optional[str]]:
    """完整自动检测链。

    优先级:
      1. 如果用户的主提供者不是聚合器（OpenRouter / Nous），
         直接使用主提供者 + 主模型。这确保使用 Alibaba、DeepSeek、
         ZAI 等的用户能使用同一提供者处理辅助任务 — 无需 OpenRouter 密钥。
      2. OpenRouter → Nous → 自定义 → Codex → API 密钥提供者（原始链）。
    """
    global auxiliary_is_nous
    auxiliary_is_nous = False  # 重置 — _try_nous() 会在成功时设为 True

    # ── 步骤 1: 非聚合器主提供者 → 直接使用主模型 ──
    main_provider = _read_main_provider()
    main_model = _read_main_model()
    if (main_provider and main_model
            and main_provider not in _AGGREGATOR_PROVIDERS
            and main_provider not in ("auto", "custom", "")):
        client, resolved = resolve_provider_client(main_provider, main_model)
        if client is not None:
            logger.info("辅助自动检测: 使用主提供者 %s (%s)",
                        main_provider, resolved or main_model)
            return client, resolved or main_model

    # ── 步骤 2: 聚合器 / 回退链 ──────────────────────────────
    tried = []
    for label, try_fn in _get_provider_chain():
        client, model = try_fn()
        if client is not None:
            if tried:
                logger.info("辅助自动检测: 使用 %s (%s) — 已跳过: %s",
                            label, model or "默认", ", ".join(tried))
            else:
                logger.info("辅助自动检测: 使用 %s (%s)", label, model or "默认")
            return client, model
        tried.append(label)
    logger.warning("辅助自动检测: 无可用提供者（已尝试: %s）。"
                   "压缩、摘要和记忆刷入将无法工作。"
                   "请设置 OPENROUTER_API_KEY 或在 config.yaml 中配置本地模型。",
                   ", ".join(tried))
    return None, None


# ── 集中提供者路由器 ─────────────────────────────────────────────
#
# resolve_provider_client() 是创建正确配置客户端的单一入口，
# 接受 (provider, model) 对。它处理认证查找、base URL 解析、
# 提供者特定请求头和 API 格式差异（Chat Completions vs Codex Responses API）。
#
# 所有辅助消费者代码应通过此函数或下面的公共辅助函数访问 —
# 绝不要临时查找认证环境变量。


def _to_async_client(sync_client, model: str):
    """将同步客户端转换为其异步对应版本，保留 Codex 路由。"""
    from openai import AsyncOpenAI

    if isinstance(sync_client, CodexAuxiliaryClient):
        return AsyncCodexAuxiliaryClient(sync_client), model
    if isinstance(sync_client, AnthropicAuxiliaryClient):
        return AsyncAnthropicAuxiliaryClient(sync_client), model

    async_kwargs = {
        "api_key": sync_client.api_key,
        "base_url": str(sync_client.base_url),
    }
    base_lower = str(sync_client.base_url).lower()
    if "openrouter" in base_lower:
        async_kwargs["default_headers"] = dict(_OR_HEADERS)
    elif "api.githubcopilot.com" in base_lower:
        from kclaw_cli.models import copilot_default_headers

        async_kwargs["default_headers"] = copilot_default_headers()
    elif "api.kimi.com" in base_lower:
        async_kwargs["default_headers"] = {"User-Agent": "KimiCLI/1.0"}
    return AsyncOpenAI(**async_kwargs), model


def resolve_provider_client(
    provider: str,
    model: str = None,
    async_mode: bool = False,
    raw_codex: bool = False,
    explicit_base_url: str = None,
    explicit_api_key: str = None,
) -> Tuple[Optional[Any], Optional[str]]:
    """集中路由器：给定提供者名称和可选模型，返回正确配置的客户端，
    包含正确的认证、base URL 和 API 格式。

    返回的客户端始终暴露 ``.chat.completions.create()`` — 对于
    Codex/Responses API 提供者，适配器透明处理转换。

    参数:
        provider: 提供者标识。取值之一:
            "openrouter"、"nous"、"openai-codex"（或 "codex"）、
            "zai"、"kimi-coding"、"minimax"、"minimax-cn"、
            "custom"（OPENAI_BASE_URL + OPENAI_API_KEY）、
            "auto"（完整自动检测链）。
        model: 模型标识覆盖。若为 None，使用提供者的默认辅助模型。
        async_mode: 若为 True，返回异步兼容的客户端。
        raw_codex: 若为 True，对 Codex 提供者返回原始 OpenAI 客户端，
            而非包装在 CodexAuxiliaryClient 中。当调用者需要直接访问
            responses.stream() 时使用（例如主 agent 循环）。
        explicit_base_url: 可选的直接 OpenAI 兼容端点。
        explicit_api_key: 与 explicit_base_url 配对的可选 API 密钥。

    返回:
        (client, resolved_model) 或 (None, None) 表示认证不可用。
    """
    # 规范化别名
    provider = _normalize_aux_provider(provider)

    # ── Auto: 按优先级尝试所有提供者 ────────────────────
    if provider == "auto":
        client, resolved = _resolve_auto()
        if client is None:
            return None, None
        # 当自动检测落在非 OpenRouter 提供者（如本地服务器）上时，
        # OpenRouter 格式的模型覆盖（如 "google/gemini-3-flash-preview"）
        # 将不起作用。丢弃它并使用提供者自己的默认模型。
        if model and "/" in model and resolved and "/" not in resolved:
            logger.debug(
                "为非 OpenRouter 辅助提供者丢弃 OpenRouter 格式模型 %r"
                "（改用 %r）", model, resolved)
            model = None
        final_model = model or resolved
        return (_to_async_client(client, final_model) if async_mode
                else (client, final_model))

    # ── OpenRouter ───────────────────────────────────────────────────
    if provider == "openrouter":
        client, default = _try_openrouter()
        if client is None:
            logger.warning("resolve_provider_client: 请求 openrouter "
                           "但 OPENROUTER_API_KEY 未设置")
            return None, None
        final_model = model or default
        return (_to_async_client(client, final_model) if async_mode
                else (client, final_model))

    # ── Nous Portal (OAuth) ──────────────────────────────────────────
    if provider == "nous":
        client, default = _try_nous()
        if client is None:
            logger.warning("resolve_provider_client: 请求 nous "
                           "但 Nous Portal 未配置（运行: kclaw auth）")
            return None, None
        final_model = model or default
        return (_to_async_client(client, final_model) if async_mode
                else (client, final_model))

    # ── OpenAI Codex (OAuth → Responses API) ─────────────────────────
    if provider == "openai-codex":
        if raw_codex:
            # 返回原始 OpenAI 客户端，供需要直接访问
            # responses.stream() 的调用者使用（例如主 agent 循环）。
            codex_token = _read_codex_access_token()
            if not codex_token:
                logger.warning("resolve_provider_client: 请求 openai-codex "
                               "但未找到 Codex OAuth token（运行: kclaw model）")
                return None, None
            final_model = model or _CODEX_AUX_MODEL
            raw_client = OpenAI(api_key=codex_token, base_url=_CODEX_AUX_BASE_URL)
            return (raw_client, final_model)
        # 标准路径: 包装在 CodexAuxiliaryClient 适配器中
        client, default = _try_codex()
        if client is None:
            logger.warning("resolve_provider_client: 请求 openai-codex "
                           "但未找到 Codex OAuth token（运行: kclaw model）")
            return None, None
        final_model = model or default
        return (_to_async_client(client, final_model) if async_mode
                else (client, final_model))

    # ── 自定义端点 (OPENAI_BASE_URL + OPENAI_API_KEY) ───────────
    if provider == "custom":
        if explicit_base_url:
            custom_base = explicit_base_url.strip()
            custom_key = (
                (explicit_api_key or "").strip()
                or os.getenv("OPENAI_API_KEY", "").strip()
                or "no-key-required"  # 本地服务器不需要认证
            )
            if not custom_base:
                logger.warning(
                    "resolve_provider_client: 请求显式自定义端点 "
                    "但 base_url 为空"
                )
                return None, None
            final_model = model or _read_main_model() or "gpt-4o-mini"
            client = OpenAI(api_key=custom_key, base_url=custom_base)
            return (_to_async_client(client, final_model) if async_mode
                    else (client, final_model))
        # 先尝试自定义，然后 Codex，再 API 密钥提供者
        for try_fn in (_try_custom_endpoint, _try_codex,
                       _resolve_api_key_provider):
            client, default = try_fn()
            if client is not None:
                final_model = model or default
                return (_to_async_client(client, final_model) if async_mode
                        else (client, final_model))
        logger.warning("resolve_provider_client: 请求 custom/main "
                       "但未找到端点凭据")
        return None, None

    # ── 命名自定义提供者 (config.yaml custom_providers 列表) ───
    try:
        from kclaw_cli.runtime_provider import _get_named_custom_provider
        custom_entry = _get_named_custom_provider(provider)
        if custom_entry:
            custom_base = custom_entry.get("base_url", "").strip()
            custom_key = custom_entry.get("api_key", "").strip() or "no-key-required"
            if custom_base:
                final_model = model or _read_main_model() or "gpt-4o-mini"
                client = OpenAI(api_key=custom_key, base_url=custom_base)
                logger.debug(
                    "resolve_provider_client: 命名自定义提供者 %r (%s)",
                    provider, final_model)
                return (_to_async_client(client, final_model) if async_mode
                        else (client, final_model))
            logger.warning(
                "resolve_provider_client: 命名自定义提供者 %r 没有 base_url",
                provider)
            return None, None
    except ImportError:
        pass

    # ── API-key providers from PROVIDER_REGISTRY ─────────────────────
    try:
        from kclaw_cli.auth import PROVIDER_REGISTRY, resolve_api_key_provider_credentials
    except ImportError:
        logger.debug("kclaw_cli.auth 不可用于提供者 %s", provider)
        return None, None

    pconfig = PROVIDER_REGISTRY.get(provider)
    if pconfig is None:
        logger.warning("resolve_provider_client: 未知提供者 %r", provider)
        return None, None

    if pconfig.auth_type == "api_key":
        if provider == "anthropic":
            client, default_model = _try_anthropic()
            if client is None:
                logger.warning("resolve_provider_client: 请求 anthropic 但未找到 Anthropic 凭据")
                return None, None
            final_model = model or default_model
            return (_to_async_client(client, final_model) if async_mode else (client, final_model))

        creds = resolve_api_key_provider_credentials(provider)
        api_key = str(creds.get("api_key", "")).strip()
        if not api_key:
            tried_sources = list(pconfig.api_key_env_vars)
            if provider == "copilot":
                tried_sources.append("gh auth token")
            logger.debug("resolve_provider_client: 提供者 %s 未配置 API 密钥"
                         "（已尝试: %s）",
                         provider, ", ".join(tried_sources))
            return None, None

        base_url = _to_openai_base_url(
            str(creds.get("base_url", "")).strip().rstrip("/") or pconfig.inference_base_url
        )

        default_model = _API_KEY_PROVIDER_AUX_MODELS.get(provider, "")
        final_model = model or default_model

        # 提供者特定请求头
        headers = {}
        if "api.kimi.com" in base_url.lower():
            headers["User-Agent"] = "KimiCLI/1.0"
        elif "api.githubcopilot.com" in base_url.lower():
            from kclaw_cli.models import copilot_default_headers

            headers.update(copilot_default_headers())

        client = OpenAI(api_key=api_key, base_url=base_url,
                        **({"default_headers": headers} if headers else {}))
        logger.debug("resolve_provider_client: %s (%s)", provider, final_model)
        return (_to_async_client(client, final_model) if async_mode
                else (client, final_model))

    elif pconfig.auth_type in ("oauth_device_code", "oauth_external"):
        # OAuth 提供者 — 通过其特定的尝试函数路由
        if provider == "nous":
            return resolve_provider_client("nous", model, async_mode)
        if provider == "openai-codex":
            return resolve_provider_client("openai-codex", model, async_mode)
        # 其他 OAuth 提供者不直接支持
        logger.warning("resolve_provider_client: OAuth 提供者 %s 不"
                       "直接支持，请尝试 'auto'", provider)
        return None, None

    logger.warning("resolve_provider_client: 未处理的 auth_type %s 用于 %s",
                   pconfig.auth_type, provider)
    return None, None


# ── 公共 API ──────────────────────────────────────────────────────────────

def get_text_auxiliary_client(task: str = "") -> Tuple[Optional[OpenAI], Optional[str]]:
    """返回用于纯文本辅助任务的 (client, default_model_slug)。

    参数:
        task: 可选任务名称（"compression"、"web_extract"），用于检查
              任务特定的提供者覆盖。

    调用者可以通过每个任务的环境变量覆盖返回的模型
    （如 CONTEXT_COMPRESSION_MODEL、AUXILIARY_WEB_EXTRACT_MODEL）。
    """
    provider, model, base_url, api_key = _resolve_task_provider_model(task or None)
    return resolve_provider_client(
        provider,
        model=model,
        explicit_base_url=base_url,
        explicit_api_key=api_key,
    )


def get_async_text_auxiliary_client(task: str = ""):
    """返回用于异步消费者的 (async_client, model_slug)。

    对于标准提供者返回 (AsyncOpenAI, model)。对于 Codex 返回
    (AsyncCodexAuxiliaryClient, model) 包装 Responses API。
    无可用提供者时返回 (None, None)。
    """
    provider, model, base_url, api_key = _resolve_task_provider_model(task or None)
    return resolve_provider_client(
        provider,
        model=model,
        async_mode=True,
        explicit_base_url=base_url,
        explicit_api_key=api_key,
    )


_VISION_AUTO_PROVIDER_ORDER = (
    "openrouter",
    "nous",
)


def _normalize_vision_provider(provider: Optional[str]) -> str:
    return _normalize_aux_provider(provider, for_vision=True)


def _resolve_strict_vision_backend(provider: str) -> Tuple[Optional[Any], Optional[str]]:
    provider = _normalize_vision_provider(provider)
    if provider == "openrouter":
        return _try_openrouter()
    if provider == "nous":
        return _try_nous(vision=True)
    if provider == "openai-codex":
        return _try_codex()
    if provider == "anthropic":
        return _try_anthropic()
    if provider == "custom":
        return _try_custom_endpoint()
    return None, None


def _strict_vision_backend_available(provider: str) -> bool:
    return _resolve_strict_vision_backend(provider)[0] is not None


def _preferred_main_vision_provider() -> Optional[str]:
    """返回所选主提供者（当其同时也是受支持的视觉后端时）。"""
    try:
        from kclaw_cli.config import load_config

        config = load_config()
        model_cfg = config.get("model", {})
        if isinstance(model_cfg, dict):
            provider = _normalize_vision_provider(model_cfg.get("provider", ""))
            if provider in _VISION_AUTO_PROVIDER_ORDER:
                return provider
    except Exception:
        pass
    return None


def get_available_vision_backends() -> List[str]:
    """返回当前可用的视觉后端，按自动选择顺序排列。

    顺序: 活动提供者 → OpenRouter → Nous → 停止。这是设置、
    工具门控和视觉任务运行时自动路由的唯一真实来源。
    """
    available: List[str] = []
    # 1. 活动提供者 — 如果用户配置了提供者，优先尝试。
    main_provider = _read_main_provider()
    if main_provider and main_provider not in ("auto", ""):
        if main_provider in _VISION_AUTO_PROVIDER_ORDER:
            if _strict_vision_backend_available(main_provider):
                available.append(main_provider)
        else:
            client, _ = resolve_provider_client(main_provider, _read_main_model())
            if client is not None:
                available.append(main_provider)
    # 2. OpenRouter, 3. Nous — 如果已被主提供者覆盖则跳过。
    for p in _VISION_AUTO_PROVIDER_ORDER:
        if p not in available and _strict_vision_backend_available(p):
            available.append(p)
    return available


def resolve_vision_provider_client(
    provider: Optional[str] = None,
    model: Optional[str] = None,
    *,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    async_mode: bool = False,
) -> Tuple[Optional[str], Optional[Any], Optional[str]]:
    """解析视觉任务实际使用的客户端。

    直接端点覆盖优先于提供者选择。显式提供者覆盖仍然使用
    通用提供者路由器处理非标准后端，因此用户可以有意强制使用实验性提供者。
    自动模式保持保守，仅尝试目前已知可用的视觉后端。
    """
    requested, resolved_model, resolved_base_url, resolved_api_key = _resolve_task_provider_model(
        "vision", provider, model, base_url, api_key
    )
    requested = _normalize_vision_provider(requested)

    def _finalize(resolved_provider: str, sync_client: Any, default_model: Optional[str]):
        if sync_client is None:
            return resolved_provider, None, None
        final_model = resolved_model or default_model
        if async_mode:
            async_client, async_model = _to_async_client(sync_client, final_model)
            return resolved_provider, async_client, async_model
        return resolved_provider, sync_client, final_model

    if resolved_base_url:
        client, final_model = resolve_provider_client(
            "custom",
            model=resolved_model,
            async_mode=async_mode,
            explicit_base_url=resolved_base_url,
            explicit_api_key=resolved_api_key,
        )
        if client is None:
            return "custom", None, None
        return "custom", client, final_model

    if requested == "auto":
        # 视觉自动检测顺序:
        #   1. 活动提供者 + 模型（用户的主聊天配置）
        #   2. OpenRouter（已知视觉能力的默认模型）
        #   3. Nous Portal（已知视觉能力的默认模型）
        #   4. 停止
        main_provider = _read_main_provider()
        main_model = _read_main_model()
        if main_provider and main_provider not in ("auto", ""):
            if main_provider in _VISION_AUTO_PROVIDER_ORDER:
                # 已知的严格后端 — 使用其默认值。
                sync_client, default_model = _resolve_strict_vision_backend(main_provider)
                if sync_client is not None:
                    return _finalize(main_provider, sync_client, default_model)
            else:
                # 外来提供者（DeepSeek、Alibaba、命名自定义等）
                rpc_client, rpc_model = resolve_provider_client(
                    main_provider, main_model)
                if rpc_client is not None:
                    logger.info(
                        "视觉自动检测: 使用活动提供者 %s (%s)",
                        main_provider, rpc_model or main_model,
                    )
                    return _finalize(
                        main_provider, rpc_client, rpc_model or main_model)

        # 回退到聚合器。
        for candidate in _VISION_AUTO_PROVIDER_ORDER:
            if candidate == main_provider:
                continue  # 已在上面尝试过
            sync_client, default_model = _resolve_strict_vision_backend(candidate)
            if sync_client is not None:
                return _finalize(candidate, sync_client, default_model)

        logger.debug("辅助视觉客户端: 无可用后端")
        return None, None, None

    if requested in _VISION_AUTO_PROVIDER_ORDER:
        sync_client, default_model = _resolve_strict_vision_backend(requested)
        return _finalize(requested, sync_client, default_model)

    client, final_model = _get_cached_client(requested, resolved_model, async_mode)
    if client is None:
        return requested, None, None
    return requested, client, final_model


def get_vision_auxiliary_client() -> Tuple[Optional[OpenAI], Optional[str]]:
    """返回用于视觉/多模态辅助任务的 (client, default_model_slug)。"""
    _, client, final_model = resolve_vision_provider_client(async_mode=False)
    return client, final_model


def get_async_vision_auxiliary_client():
    """返回用于异步视觉消费者的 (async_client, model_slug)。"""
    _, client, final_model = resolve_vision_provider_client(async_mode=True)
    return client, final_model


def get_auxiliary_extra_body() -> dict:
    """返回辅助 API 调用的 extra_body 关键字参数。

    当辅助客户端由 Nous Portal 支持时包含产品标签。
    否则返回空字典。
    """
    return dict(NOUS_EXTRA_BODY) if auxiliary_is_nous else {}


def auxiliary_max_tokens_param(value: int) -> dict:
    """返回辅助客户端提供者的正确 max tokens 关键字参数。

    OpenRouter 和本地模型使用 'max_tokens'。直接 OpenAI 较新模型
    （gpt-4o、o 系列、gpt-5+）需要 'max_completion_tokens'。
    Codex 适配器内部转换 max_tokens，因此我们也对它使用 max_tokens。
    """
    custom_base = _current_custom_base_url()
    or_key = os.getenv("OPENROUTER_API_KEY")
    # 仅对直接 OpenAI 自定义端点使用 max_completion_tokens
    if (not or_key
            and _read_nous_auth() is None
            and "api.openai.com" in custom_base.lower()):
        return {"max_completion_tokens": value}
    return {"max_tokens": value}


# ── 集中 LLM 调用 API ────────────────────────────────────────────────
#
# call_llm() 和 async_call_llm() 拥有完整的请求生命周期:
#   1. 从任务配置（或显式参数）解析提供者 + 模型
#   2. 获取或创建该提供者的缓存客户端
#   3. 为提供者 + 模型格式化请求参数（max_tokens 处理等）
#   4. 执行 API 调用
#   5. 返回响应
#
# 每个辅助 LLM 消费者都应使用这些函数，而非手动构建客户端
# 并调用 .chat.completions.create()。

# 客户端缓存: (provider, async_mode, base_url, api_key) -> (client, default_model)
_client_cache: Dict[tuple, tuple] = {}
_client_cache_lock = threading.Lock()


def neuter_async_httpx_del() -> None:
    """猴子补丁 ``AsyncHttpxClientWrapper.__del__`` 使其成为空操作。

    OpenAI SDK 的 ``AsyncHttpxClientWrapper.__del__`` 通过
    ``asyncio.get_running_loop().create_task()`` 调度 ``self.aclose()``。
    当 ``AsyncOpenAI`` 客户端在 prompt_toolkit 的事件循环运行时被
    垃圾回收（常见的 CLI 空闲状态），``aclose()`` 任务在
    prompt_toolkit 的循环上运行，但底层 TCP 传输绑定到*不同的*循环
    （客户端最初创建的工作线程循环）。如果该循环已关闭或其线程
    已终止，传输的 ``self._loop.call_soon()`` 会抛出
    ``RuntimeError("Event loop is closed")``，prompt_toolkit 将其
    显示为 "Unhandled exception in event loop ... Press ENTER to continue..."。

    将 ``__del__`` 置空是安全的，因为:
    - 缓存客户端通过 ``_force_close_async_httpx`` 在陈旧循环检测时
      显式清理，通过 ``shutdown_cached_clients`` 在退出时清理。
    - 未缓存客户端的 TCP 连接在进程退出时由操作系统清理。
    - OpenAI SDK 本身将此标记为 TODO（``# TODO(someday):
      support non asyncio runtimes here``）。

    在 CLI 启动时调用一次，在任何 ``AsyncOpenAI`` 客户端创建之前。
    """
    try:
        from openai._base_client import AsyncHttpxClientWrapper
        AsyncHttpxClientWrapper.__del__ = lambda self: None  # type: ignore[assignment]
    except (ImportError, AttributeError):
        pass  # SDK 内部变更时优雅降级


def _force_close_async_httpx(client: Any) -> None:
    """将 AsyncOpenAI 客户端内的 httpx AsyncClient 标记为已关闭。

    这防止 ``AsyncHttpxClientWrapper.__del__`` 在（可能已关闭的）
    事件循环上调度 ``aclose()``，该操作会导致
    ``RuntimeError: Event loop is closed`` → prompt_toolkit 的
    "Press ENTER to continue..." 处理器。

    我们有意不运行完整的异步关闭路径 — 连接将在进程退出时
    由操作系统释放。
    """
    try:
        from httpx._client import ClientState
        inner = getattr(client, "_client", None)
        if inner is not None and not getattr(inner, "is_closed", True):
            inner._state = ClientState.CLOSED
    except Exception:
        pass


def shutdown_cached_clients() -> None:
    """关闭所有缓存客户端（同步和异步）以防止事件循环错误。

    在 CLI 关闭期间调用，*在*事件循环关闭之前，以避免
    ``AsyncHttpxClientWrapper.__del__`` 在已死亡循环上抛出异常。
    """
    import inspect

    with _client_cache_lock:
        for key, entry in list(_client_cache.items()):
            client = entry[0]
            if client is None:
                continue
            # 先将任何异步 httpx 传输标记为已关闭（防止 __del__
            # 在已死亡的事件循环上调度 aclose()）。
            _force_close_async_httpx(client)
            # 同步客户端: 干净地关闭 httpx 连接池。
            # 异步客户端: 跳过 — 上面已经置空了 __del__。
            try:
                close_fn = getattr(client, "close", None)
                if close_fn and not inspect.iscoroutinefunction(close_fn):
                    close_fn()
            except Exception:
                pass
        _client_cache.clear()


def cleanup_stale_async_clients() -> None:
    """强制关闭事件循环已关闭的缓存异步客户端。

    在每次 agent 轮次后调用，主动清理陈旧客户端，防止 GC 在它们
    上触发 ``AsyncHttpxClientWrapper.__del__``。这是纵深防御 —
    主要修复是 ``neuter_async_httpx_del``，它完全禁用 ``__del__``。
    """
    with _client_cache_lock:
        stale_keys = []
        for key, entry in _client_cache.items():
            client, _default, cached_loop = entry
            if cached_loop is not None and cached_loop.is_closed():
                _force_close_async_httpx(client)
                stale_keys.append(key)
        for key in stale_keys:
            del _client_cache[key]


def _get_cached_client(
    provider: str,
    model: str = None,
    async_mode: bool = False,
    base_url: str = None,
    api_key: str = None,
) -> Tuple[Optional[Any], Optional[str]]:
    """获取或创建给定提供者的缓存客户端。

    异步客户端 (AsyncOpenAI) 内部使用 httpx.AsyncClient，它绑定到
    客户端创建时的事件循环。在*不同*循环上使用此客户端会导致
    死锁或 RuntimeError。为防止跨循环问题（特别是在 gateway 模式下，
    _run_async() 可能在工作线程中生成新循环），异步客户端的缓存键
    包含当前事件循环的标识，以便每个循环获得自己的客户端实例。
    """
    # 为异步客户端包含循环标识以防止跨循环复用。
    # httpx.AsyncClient（在 AsyncOpenAI 内部）绑定到创建时的循环 —
    # 在不同循环上复用会导致死锁 (#2681)。
    loop_id = 0
    current_loop = None
    if async_mode:
        try:
            import asyncio as _aio
            current_loop = _aio.get_event_loop()
            loop_id = id(current_loop)
        except RuntimeError:
            pass
    cache_key = (provider, async_mode, base_url or "", api_key or "", loop_id)
    with _client_cache_lock:
        if cache_key in _client_cache:
            cached_client, cached_default, cached_loop = _client_cache[cache_key]
            if async_mode:
                # 缓存的异步客户端如果其循环已关闭，在 httpx 尝试
                # 清理传输时会抛出 "Event loop is closed"。
                # 丢弃陈旧客户端并创建新的。
                if cached_loop is not None and cached_loop.is_closed():
                    _force_close_async_httpx(cached_client)
                    del _client_cache[cache_key]
                else:
                    return cached_client, model or cached_default
            else:
                return cached_client, model or cached_default
    # 在锁外构建
    client, default_model = resolve_provider_client(
        provider,
        model,
        async_mode,
        explicit_base_url=base_url,
        explicit_api_key=api_key,
    )
    if client is not None:
        # 对于异步客户端，记住它们创建时的循环，以便稍后检测陈旧条目。
        bound_loop = current_loop
        with _client_cache_lock:
            if cache_key not in _client_cache:
                _client_cache[cache_key] = (client, default_model, bound_loop)
            else:
                client, default_model, _ = _client_cache[cache_key]
    return client, model or default_model


def _resolve_task_provider_model(
    task: str = None,
    provider: str = None,
    model: str = None,
    base_url: str = None,
    api_key: str = None,
) -> Tuple[str, Optional[str], Optional[str], Optional[str]]:
    """确定调用的提供者 + 模型。

    优先级:
      1. 显式 provider/model/base_url/api_key 参数（始终优先）
      2. 环境变量覆盖（AUXILIARY_{TASK}_*、CONTEXT_{TASK}_*）
      3. 配置文件（auxiliary.{task}.* 或 compression.*）
      4. "auto"（完整自动检测链）

    返回 (provider, model, base_url, api_key)，其中 model 可能为 None
    （使用提供者默认值）。当 base_url 设置时，provider 被强制为
    "custom"，任务使用该直接端点。
    """
    config = {}
    cfg_provider = None
    cfg_model = None
    cfg_base_url = None
    cfg_api_key = None

    if task:
        try:
            from kclaw_cli.config import load_config
            config = load_config()
        except ImportError:
            config = {}

        aux = config.get("auxiliary", {}) if isinstance(config, dict) else {}
        task_config = aux.get(task, {}) if isinstance(aux, dict) else {}
        if not isinstance(task_config, dict):
            task_config = {}
        cfg_provider = str(task_config.get("provider", "")).strip() or None
        cfg_model = str(task_config.get("model", "")).strip() or None
        cfg_base_url = str(task_config.get("base_url", "")).strip() or None
        cfg_api_key = str(task_config.get("api_key", "")).strip() or None

        # 向后兼容: compression 部分有自己的键。
        # auxiliary.compression 默认为 provider="auto"，因此将 None
        # 和 "auto" 都视为"未显式配置"。
        if task == "compression" and (not cfg_provider or cfg_provider == "auto"):
            comp = config.get("compression", {}) if isinstance(config, dict) else {}
            if isinstance(comp, dict):
                cfg_provider = comp.get("summary_provider", "").strip() or None
                cfg_model = cfg_model or comp.get("summary_model", "").strip() or None
                _sbu = comp.get("summary_base_url") or ""
                cfg_base_url = cfg_base_url or _sbu.strip() or None

    env_model = _get_auxiliary_env_override(task, "MODEL") if task else None
    resolved_model = model or env_model or cfg_model

    if base_url:
        return "custom", resolved_model, base_url, api_key
    if provider:
        return provider, resolved_model, base_url, api_key

    if task:
        env_base_url = _get_auxiliary_env_override(task, "BASE_URL")
        env_api_key = _get_auxiliary_env_override(task, "API_KEY")
        if env_base_url:
            return "custom", resolved_model, env_base_url, env_api_key or cfg_api_key

        env_provider = _get_auxiliary_provider(task)
        if env_provider != "auto":
            return env_provider, resolved_model, None, None

        if cfg_base_url:
            return "custom", resolved_model, cfg_base_url, cfg_api_key
        if cfg_provider and cfg_provider != "auto":
            return cfg_provider, resolved_model, None, None
        return "auto", resolved_model, None, None

    return "auto", resolved_model, None, None


_DEFAULT_AUX_TIMEOUT = 30.0


def _get_task_timeout(task: str, default: float = _DEFAULT_AUX_TIMEOUT) -> float:
    """从 auxiliary.{task}.timeout 读取超时，回退到 *default*。"""
    if not task:
        return default
    try:
        from kclaw_cli.config import load_config
        config = load_config()
    except ImportError:
        return default
    aux = config.get("auxiliary", {}) if isinstance(config, dict) else {}
    task_config = aux.get(task, {}) if isinstance(aux, dict) else {}
    raw = task_config.get("timeout")
    if raw is not None:
        try:
            return float(raw)
        except (ValueError, TypeError):
            pass
    return default


def _build_call_kwargs(
    provider: str,
    model: str,
    messages: list,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    tools: Optional[list] = None,
    timeout: float = 30.0,
    extra_body: Optional[dict] = None,
    base_url: Optional[str] = None,
) -> dict:
    """构建 .chat.completions.create() 的关键字参数，包含模型/提供者调整。"""
    kwargs: Dict[str, Any] = {
        "model": model,
        "messages": messages,
        "timeout": timeout,
    }

    if temperature is not None:
        kwargs["temperature"] = temperature

    if max_tokens is not None:
        # Codex 适配器内部处理 max_tokens；OpenRouter/Nous 使用 max_tokens。
        # 直接 OpenAI api.openai.com 较新模型需要 max_completion_tokens。
        if provider == "custom":
            custom_base = base_url or _current_custom_base_url()
            if "api.openai.com" in custom_base.lower():
                kwargs["max_completion_tokens"] = max_tokens
            else:
                kwargs["max_tokens"] = max_tokens
        else:
            kwargs["max_tokens"] = max_tokens

    if tools:
        kwargs["tools"] = tools

    # 提供者特定的 extra_body
    merged_extra = dict(extra_body or {})
    if provider == "nous" or auxiliary_is_nous:
        merged_extra.setdefault("tags", []).extend(["product=kclaw"])
    if merged_extra:
        kwargs["extra_body"] = merged_extra

    return kwargs


def call_llm(
    task: str = None,
    *,
    provider: str = None,
    model: str = None,
    base_url: str = None,
    api_key: str = None,
    messages: list,
    temperature: float = None,
    max_tokens: int = None,
    tools: list = None,
    timeout: float = None,
    extra_body: dict = None,
) -> Any:
    """集中同步 LLM 调用。

    解析提供者 + 模型（从任务配置、显式参数或自动检测），
    处理认证、请求格式化和模型特定参数调整。

    参数:
        task: 辅助任务名称（"compression"、"vision"、"web_extract"、
              "session_search"、"skills_hub"、"mcp"、"flush_memories"）。
              从配置/环境变量读取 provider:model。设置 provider 时忽略。
        provider: 显式提供者覆盖。
        model: 显式模型覆盖。
        messages: 聊天消息列表。
        temperature: 采样温度（None = 提供者默认值）。
        max_tokens: 最大输出 token 数（处理 max_tokens vs max_completion_tokens）。
        tools: 工具定义（用于函数调用）。
        timeout: 请求超时秒数（None = 从 auxiliary.{task}.timeout 配置读取）。
        extra_body: 额外请求体字段。

    返回:
        带有 .choices[0].message.content 的响应对象

    抛出:
        RuntimeError: 如果未配置提供者。
    """
    resolved_provider, resolved_model, resolved_base_url, resolved_api_key = _resolve_task_provider_model(
        task, provider, model, base_url, api_key)

    if task == "vision":
        effective_provider, client, final_model = resolve_vision_provider_client(
            provider=provider,
            model=model,
            base_url=base_url,
            api_key=api_key,
            async_mode=False,
        )
        if client is None and resolved_provider != "auto" and not resolved_base_url:
            logger.warning(
                "视觉提供者 %s 不可用，回退到自动视觉后端",
                resolved_provider,
            )
            effective_provider, client, final_model = resolve_vision_provider_client(
                provider="auto",
                model=resolved_model,
                async_mode=False,
            )
        if client is None:
            raise RuntimeError(
                f"未配置 task={task} provider={resolved_provider} 的 LLM 提供者。"
                f"运行: kclaw setup"
            )
        resolved_provider = effective_provider or resolved_provider
    else:
        client, final_model = _get_cached_client(
            resolved_provider,
            resolved_model,
            base_url=resolved_base_url,
            api_key=resolved_api_key,
        )
        if client is None:
            # 当用户显式选择了非 OpenRouter 提供者但未找到凭据时，
            # 快速失败而非静默路由到 OpenRouter（会导致令人困惑的 404 错误）。
            _explicit = (resolved_provider or "").strip().lower()
            if _explicit and _explicit not in ("auto", "openrouter", "custom"):
                raise RuntimeError(
                    f"提供者 '{_explicit}' 在 config.yaml 中已设置但未找到 API 密钥。"
                    f"请设置 {_explicit.upper()}_API_KEY 环境变量，"
                    f"或使用 `kclaw model` 切换到其他提供者。"
                )
            # 对于无凭据的 auto/custom，尝试完整自动链
            # 而非硬编码 OpenRouter（可能已耗尽）。
            # 传递 model=None 以便每个提供者使用自己的默认值 —
            # resolved_model 可能是 OpenRouter 格式的 slug，
            # 在其他提供者上不适用。
            if not resolved_base_url:
                logger.info("辅助 %s: 提供者 %s 不可用，尝试自动检测链",
                            task or "调用", resolved_provider)
                client, final_model = _get_cached_client("auto")
        if client is None:
            raise RuntimeError(
                f"No LLM provider configured for task={task} provider={resolved_provider}. "
                f"Run: kclaw setup")

    effective_timeout = timeout if timeout is not None else _get_task_timeout(task)

    # 记录即将执行的操作 — 使辅助操作可见
    _base_info = str(getattr(client, "base_url", resolved_base_url) or "")
    if task:
        logger.info("辅助 %s: 使用 %s (%s)%s",
                     task, resolved_provider or "auto", final_model or "默认",
                     f" 于 {_base_info}" if _base_info and "openrouter" not in _base_info else "")

    kwargs = _build_call_kwargs(
        resolved_provider, final_model, messages,
        temperature=temperature, max_tokens=max_tokens,
        tools=tools, timeout=effective_timeout, extra_body=extra_body,
        base_url=resolved_base_url)

    # 处理 max_tokens vs max_completion_tokens 重试，然后支付回退。
    try:
        return client.chat.completions.create(**kwargs)
    except Exception as first_err:
        err_str = str(first_err)
        if "max_tokens" in err_str or "unsupported_parameter" in err_str:
            kwargs.pop("max_tokens", None)
            kwargs["max_completion_tokens"] = max_tokens
            try:
                return client.chat.completions.create(**kwargs)
            except Exception as retry_err:
                # 如果 max_tokens 重试也遇到支付错误，
                # 继续进入下面的支付回退逻辑。
                if not _is_payment_error(retry_err):
                    raise
                first_err = retry_err

        # ── 支付 / 额度耗尽回退 ──────────────────────
        # 当解析的提供者返回 402 或额度相关错误时，
        # 尝试替代提供者而非放弃。这处理了用户耗尽 OpenRouter 额度
        # 但有 Codex OAuth 或其他提供者可用的常见情况。
        if _is_payment_error(first_err):
            fb_client, fb_model, fb_label = _try_payment_fallback(
                resolved_provider, task)
            if fb_client is not None:
                fb_kwargs = _build_call_kwargs(
                    fb_label, fb_model, messages,
                    temperature=temperature, max_tokens=max_tokens,
                    tools=tools, timeout=effective_timeout,
                    extra_body=extra_body)
                return fb_client.chat.completions.create(**fb_kwargs)
        raise


def extract_content_or_reasoning(response) -> str:
    """从 LLM 响应中提取内容，回退到推理字段。

    当推理模型（DeepSeek-R1、Qwen-QwQ 等）返回 ``content=None``
    且推理内容在结构化字段中时，模拟主 agent 循环的行为。

    解析顺序:
      1. ``message.content`` — 去除内嵌思考/推理块，检查剩余非空白文本。
      2. ``message.reasoning`` / ``message.reasoning_content`` — 直接
         结构化推理字段（DeepSeek、Moonshot、Novita 等）。
      3. ``message.reasoning_details`` — OpenRouter 统一数组格式。

    返回最佳可用文本，或 ``""`` 如果未找到任何内容。
    """
    import re

    msg = response.choices[0].message
    content = (msg.content or "").strip()

    if content:
        # 去除内嵌思考/推理块（与 _strip_think_blocks 一致）
        cleaned = re.sub(
            r"<(?:think|thinking|reasoning|REASONING_SCRATCHPAD)>"
            r".*?"
            r"</(?:think|thinking|reasoning|REASONING_SCRATCHPAD)>",
            "", content, flags=re.DOTALL | re.IGNORECASE,
        ).strip()
        if cleaned:
            return cleaned

    # 内容为空或仅有推理 — 尝试结构化推理字段
    reasoning_parts: list[str] = []
    for field in ("reasoning", "reasoning_content"):
        val = getattr(msg, field, None)
        if val and isinstance(val, str) and val.strip() and val not in reasoning_parts:
            reasoning_parts.append(val.strip())

    details = getattr(msg, "reasoning_details", None)
    if details and isinstance(details, list):
        for detail in details:
            if isinstance(detail, dict):
                summary = (
                    detail.get("summary")
                    or detail.get("content")
                    or detail.get("text")
                )
                if summary and summary not in reasoning_parts:
                    reasoning_parts.append(summary.strip() if isinstance(summary, str) else str(summary))

    if reasoning_parts:
        return "\n\n".join(reasoning_parts)

    return ""


async def async_call_llm(
    task: str = None,
    *,
    provider: str = None,
    model: str = None,
    base_url: str = None,
    api_key: str = None,
    messages: list,
    temperature: float = None,
    max_tokens: int = None,
    tools: list = None,
    timeout: float = None,
    extra_body: dict = None,
) -> Any:
    """集中异步 LLM 调用。

    与 call_llm() 相同但是异步。详见 call_llm() 的完整文档。
    """
    resolved_provider, resolved_model, resolved_base_url, resolved_api_key = _resolve_task_provider_model(
        task, provider, model, base_url, api_key)

    if task == "vision":
        effective_provider, client, final_model = resolve_vision_provider_client(
            provider=provider,
            model=model,
            base_url=base_url,
            api_key=api_key,
            async_mode=True,
        )
        if client is None and resolved_provider != "auto" and not resolved_base_url:
            logger.warning(
                "视觉提供者 %s 不可用，回退到自动视觉后端",
                resolved_provider,
            )
            effective_provider, client, final_model = resolve_vision_provider_client(
                provider="auto",
                model=resolved_model,
                async_mode=True,
            )
        if client is None:
            raise RuntimeError(
                f"未配置 task={task} provider={resolved_provider} 的 LLM 提供者。"
                f"运行: kclaw setup"
            )
        resolved_provider = effective_provider or resolved_provider
    else:
        client, final_model = _get_cached_client(
            resolved_provider,
            resolved_model,
            async_mode=True,
            base_url=resolved_base_url,
            api_key=resolved_api_key,
        )
        if client is None:
            _explicit = (resolved_provider or "").strip().lower()
            if _explicit and _explicit not in ("auto", "openrouter", "custom"):
                raise RuntimeError(
                    f"提供者 '{_explicit}' 在 config.yaml 中已设置但未找到 API 密钥。"
                    f"请设置 {_explicit.upper()}_API_KEY 环境变量，"
                    f"或使用 `kclaw model` 切换到其他提供者。"
                )
            if not resolved_base_url:
                logger.warning("提供者 %s 不可用，回退到 OpenRouter",
                               resolved_provider)
                client, final_model = _get_cached_client(
                    "openrouter", resolved_model or _OPENROUTER_MODEL,
                    async_mode=True)
        if client is None:
            raise RuntimeError(
                f"No LLM provider configured for task={task} provider={resolved_provider}. "
                f"Run: kclaw setup")

    effective_timeout = timeout if timeout is not None else _get_task_timeout(task)

    kwargs = _build_call_kwargs(
        resolved_provider, final_model, messages,
        temperature=temperature, max_tokens=max_tokens,
        tools=tools, timeout=effective_timeout, extra_body=extra_body,
        base_url=resolved_base_url)

    try:
        return await client.chat.completions.create(**kwargs)
    except Exception as first_err:
        err_str = str(first_err)
        if "max_tokens" in err_str or "unsupported_parameter" in err_str:
            kwargs.pop("max_tokens", None)
            kwargs["max_completion_tokens"] = max_tokens
            return await client.chat.completions.create(**kwargs)
        raise
