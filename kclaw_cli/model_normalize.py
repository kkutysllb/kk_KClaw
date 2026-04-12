"""各提供商的模型名称规范化。

不同的 LLM 提供商期望不同格式的模型标识符：

- **聚合器**（OpenRouter、Nous、AI Gateway、Kilo Code）需要
  ``vendor/model`` slug，如 ``anthropic/claude-sonnet-4.6``。
- **Anthropic** 原生 API 期望裸名称，点替换为
  连字符：``claude-sonnet-4-6``。
- **Copilot** 期望裸名称 **保留点**：
  ``claude-sonnet-4.6``。
- **OpenCode Zen** 遵循与 Anthropic 相同的点转连字符约定：
  ``claude-sonnet-4-6``。
- **OpenCode Go** 在模型名称中保留点：``minimax-m2.7``。
- **DeepSeek** 只接受两个模型标识符：
  ``deepseek-chat`` 和 ``deepseek-reasoner``。
- **Custom** 和其他提供者直接透传名称。

此模块集中处理这些转换，让调用者只需写：

    api_model = normalize_model_for_provider(user_input, provider)

灵感来自 Clawdbot 的 ``normalizeAnthropicModelId`` 模式。
"""

from __future__ import annotations

from typing import Optional

# ---------------------------------------------------------------------------
# 供应商前缀映射
# ---------------------------------------------------------------------------
# 将裸模型名称的第一个连字符分隔标记映射到聚合器 API（OpenRouter、Nous 等）
# 使用的供应商 slug。
#
# 示例: "claude-sonnet-4.6" -> 第一个标记 "claude" -> 供应商 "anthropic"
#          -> 聚合器 slug: "anthropic/claude-sonnet-4.6"

_VENDOR_PREFIXES: dict[str, str] = {
    "claude": "anthropic",
    "gpt": "openai",
    "o1": "openai",
    "o3": "openai",
    "o4": "openai",
    "gemini": "google",
    "gemma": "google",
    "deepseek": "deepseek",
    "glm": "z-ai",
    "kimi": "moonshotai",
    "minimax": "minimax",
    "grok": "x-ai",
    "qwen": "qwen",
    "mimo": "xiaomi",
    "nemotron": "nvidia",
    "llama": "meta-llama",
    "step": "stepfun",
    "trinity": "arcee-ai",
}

# API 接受 vendor/model slug 的提供者。
_AGGREGATOR_PROVIDERS: frozenset[str] = frozenset({
    "openrouter",
    "nous",
    "ai-gateway",
    "kilocode",
})

# 需要裸名称且将点替换为连字符的提供者。
_DOT_TO_HYPHEN_PROVIDERS: frozenset[str] = frozenset({
    "anthropic",
    "opencode-zen",
})

# 需要裸名称且保留点的提供者。
_STRIP_VENDOR_ONLY_PROVIDERS: frozenset[str] = frozenset({
    "copilot",
    "copilot-acp",
})

# 自有命名权威的提供者 — 原样透传。
_PASSTHROUGH_PROVIDERS: frozenset[str] = frozenset({
    "gemini",
    "zai",
    "kimi-coding",
    "minimax",
    "minimax-cn",
    "alibaba",
    "qwen-oauth",
    "huggingface",
    "openai-codex",
    "custom",
})

# ---------------------------------------------------------------------------
# DeepSeek 特殊处理
# ---------------------------------------------------------------------------
# DeepSeek 的 API 只识别两个确切的模型标识符。我们映射
# 常见别名和模式到规范名称。

_DEEPSEEK_REASONER_KEYWORDS: frozenset[str] = frozenset({
    "reasoner",
    "r1",
    "think",
    "reasoning",
    "cot",
})

_DEEPSEEK_CANONICAL_MODELS: frozenset[str] = frozenset({
    "deepseek-chat",
    "deepseek-reasoner",
})


def _normalize_for_deepseek(model_name: str) -> str:
    """将任何模型输入映射到 DeepSeek 接受的两个标识符之一。

    规则：
    - 已经是 ``deepseek-chat`` 或 ``deepseek-reasoner`` -> 透传。
    - 包含任何推理关键词（r1、think、reasoning、cot、reasoner）
      -> ``deepseek-reasoner``。
    - 其他一切 -> ``deepseek-chat``。

    参数:
        model_name: 裸模型名称（供应商前缀已剥离）。

    返回:
        ``"deepseek-chat"`` 或 ``"deepseek-reasoner"`` 之一。
    """
    bare = _strip_vendor_prefix(model_name).lower()

    if bare in _DEEPSEEK_CANONICAL_MODELS:
        return bare

    # Check for reasoner-like keywords anywhere in the name
    for keyword in _DEEPSEEK_REASONER_KEYWORDS:
        if keyword in bare:
            return "deepseek-reasoner"

    return "deepseek-chat"


# ---------------------------------------------------------------------------
# 辅助工具函数
# ---------------------------------------------------------------------------

def _strip_vendor_prefix(model_name: str) -> str:
    """移除 ``vendor/`` 前缀（如果存在）。

    示例::

        >>> _strip_vendor_prefix("anthropic/claude-sonnet-4.6")
        'claude-sonnet-4.6'
        >>> _strip_vendor_prefix("claude-sonnet-4.6")
        'claude-sonnet-4.6'
        >>> _strip_vendor_prefix("meta-llama/llama-4-scout")
        'llama-4-scout'
    """
    if "/" in model_name:
        return model_name.split("/", 1)[1]
    return model_name


def _dots_to_hyphens(model_name: str) -> str:
    """将模型名称中的点替换为连字符。

    Anthropic 的原生 API 在营销名称使用点的地方使用连字符：
    ``claude-sonnet-4.6`` -> ``claude-sonnet-4-6``。
    """
    return model_name.replace(".", "-")


def detect_vendor(model_name: str) -> Optional[str]:
    """从裸模型名称检测供应商 slug。

    使用模型名称的第一个连字符分隔标记在 ``_VENDOR_PREFIXES`` 中
    查找对应的供应商。也处理不区分大小写的匹配和特殊模式。

    参数:
        model_name: 模型名称，可选已包含 ``vendor/`` 前缀。
            如果存在前缀则直接使用。

    返回:
        供应商 slug（例如 ``"anthropic"``、``"openai"``），
        如果无法自信检测则返回 ``None``。

    示例::

        >>> detect_vendor("claude-sonnet-4.6")
        'anthropic'
        >>> detect_vendor("gpt-5.4-mini")
        'openai'
        >>> detect_vendor("anthropic/claude-sonnet-4.6")
        'anthropic'
        >>> detect_vendor("my-custom-model")
    """
    name = model_name.strip()
    if not name:
        return None

    # If there's already a vendor/ prefix, extract it
    if "/" in name:
        return name.split("/", 1)[0].lower() or None

    name_lower = name.lower()

    # Try first hyphen-delimited token (exact match)
    first_token = name_lower.split("-")[0]
    if first_token in _VENDOR_PREFIXES:
        return _VENDOR_PREFIXES[first_token]

    # Handle patterns where the first token includes version digits,
    # e.g. "qwen3.5-plus" -> first token "qwen3.5", but prefix is "qwen"
    for prefix, vendor in _VENDOR_PREFIXES.items():
        if name_lower.startswith(prefix):
            return vendor

    return None


def _prepend_vendor(model_name: str) -> str:
    """Prepend the detected ``vendor/`` prefix if missing.

    Used for aggregator providers that require ``vendor/model`` format.
    If the name already contains a ``/``, it is returned as-is.
    If no vendor can be detected, the name is returned unchanged
    (aggregators may still accept it or return an error).

    Examples::

        >>> _prepend_vendor("claude-sonnet-4.6")
        'anthropic/claude-sonnet-4.6'
        >>> _prepend_vendor("anthropic/claude-sonnet-4.6")
        'anthropic/claude-sonnet-4.6'
        >>> _prepend_vendor("my-custom-thing")
        'my-custom-thing'
    """
    if "/" in model_name:
        return model_name

    vendor = detect_vendor(model_name)
    if vendor:
        return f"{vendor}/{model_name}"
    return model_name


# ---------------------------------------------------------------------------
# Main normalisation entry point
# ---------------------------------------------------------------------------

def normalize_model_for_provider(model_input: str, target_provider: str) -> str:
    """Translate a model name into the format the target provider's API expects.

    This is the primary entry point for model name normalisation.  It
    accepts any user-facing model identifier and transforms it for the
    specific provider that will receive the API call.

    Args:
        model_input: The model name as provided by the user or config.
            Can be bare (``"claude-sonnet-4.6"``), vendor-prefixed
            (``"anthropic/claude-sonnet-4.6"``), or already in native
            format (``"claude-sonnet-4-6"``).
        target_provider: The canonical KClaw provider id, e.g.
            ``"openrouter"``, ``"anthropic"``, ``"copilot"``,
            ``"deepseek"``, ``"custom"``.  Should already be normalised
            via ``kclaw_cli.models.normalize_provider()``.

    Returns:
        The model identifier string that the target provider's API
        expects.

    Raises:
        No exceptions -- always returns a best-effort string.

    Examples::

        >>> normalize_model_for_provider("claude-sonnet-4.6", "openrouter")
        'anthropic/claude-sonnet-4.6'

        >>> normalize_model_for_provider("anthropic/claude-sonnet-4.6", "anthropic")
        'claude-sonnet-4-6'

        >>> normalize_model_for_provider("anthropic/claude-sonnet-4.6", "copilot")
        'claude-sonnet-4.6'

        >>> normalize_model_for_provider("openai/gpt-5.4", "copilot")
        'gpt-5.4'

        >>> normalize_model_for_provider("claude-sonnet-4.6", "opencode-zen")
        'claude-sonnet-4-6'

        >>> normalize_model_for_provider("deepseek-v3", "deepseek")
        'deepseek-chat'

        >>> normalize_model_for_provider("deepseek-r1", "deepseek")
        'deepseek-reasoner'

        >>> normalize_model_for_provider("my-model", "custom")
        'my-model'

        >>> normalize_model_for_provider("claude-sonnet-4.6", "zai")
        'claude-sonnet-4.6'
    """
    name = (model_input or "").strip()
    if not name:
        return name

    provider = (target_provider or "").strip().lower()

    # --- Aggregators: need vendor/model format ---
    if provider in _AGGREGATOR_PROVIDERS:
        return _prepend_vendor(name)

    # --- Anthropic / OpenCode: strip vendor, dots -> hyphens ---
    if provider in _DOT_TO_HYPHEN_PROVIDERS:
        bare = _strip_vendor_prefix(name)
        return _dots_to_hyphens(bare)

    # --- Copilot: strip vendor, keep dots ---
    if provider in _STRIP_VENDOR_ONLY_PROVIDERS:
        return _strip_vendor_prefix(name)

    # --- DeepSeek: map to one of two canonical names ---
    if provider == "deepseek":
        return _normalize_for_deepseek(name)

    # --- Custom & all others: pass through as-is ---
    return name


# ---------------------------------------------------------------------------
# Batch / convenience helpers
# ---------------------------------------------------------------------------

def model_display_name(model_id: str) -> str:
    """Return a short, human-readable display name for a model id.

    Strips the vendor prefix (if any) for a cleaner display in menus
    and status bars, while preserving dots for readability.

    Examples::

        >>> model_display_name("anthropic/claude-sonnet-4.6")
        'claude-sonnet-4.6'
        >>> model_display_name("claude-sonnet-4-6")
        'claude-sonnet-4-6'
    """
    return _strip_vendor_prefix((model_id or "").strip())


def is_aggregator_provider(provider: str) -> bool:
    """Check if a provider is an aggregator that needs vendor/model format."""
    return (provider or "").strip().lower() in _AGGREGATOR_PROVIDERS


def vendor_for_model(model_name: str) -> str:
    """Return the vendor slug for a model, or ``""`` if unknown.

    Convenience wrapper around :func:`detect_vendor` that never returns
    ``None``.
    """
    return detect_vendor(model_name) or ""
