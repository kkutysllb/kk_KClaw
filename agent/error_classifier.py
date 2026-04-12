"""API 错误分类,用于智能故障转移和恢复。

提供结构化的 API 错误分类和优先级排序的
分类管道,决定正确的恢复动作
(重试、轮换凭据、回退到其他提供者、压缩
上下文,或中止)。

用集中的分类器替换分散的内联字符串匹配,
run_agent.py 中的主重试循环对每次 API 失败都会咨询该分类器。
"""

from __future__ import annotations

import enum
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# ── 错误分类 ──────────────────────────────────────────────────────

class FailoverReason(enum.Enum):
    """API 调用为何失败 — 决定恢复策略。"""

    # 认证 / 授权
    auth = "auth"                        # 瞬时认证错误(401/403) — 刷新/轮换
    auth_permanent = "auth_permanent"    # 刷新后认证仍失败 — 中止

    # 计费 / 配额
    billing = "billing"                  # 402 或确认积分耗尽 — 立即轮换
    rate_limit = "rate_limit"            # 429 或基于配额的限流 — 退避然后轮换

    # 服务器端
    overloaded = "overloaded"            # 503/529 — 提供者过载,退避
    server_error = "server_error"        # 500/502 — 内部服务器错误,重试

    # 传输
    timeout = "timeout"                  # 连接/读取超时 — 重建客户端 + 重试

    # 上下文 / 载荷
    context_overflow = "context_overflow"  # 上下文过大 — 压缩,不故障转移
    payload_too_large = "payload_too_large"  # 413 — 压缩载荷

    # 模型
    model_not_found = "model_not_found"  # 404 或无效模型 — 回退到不同模型

    # 请求格式
    format_error = "format_error"        # 400 错误请求 — 中止或剥离 + 重试

    # 提供者特定
    thinking_signature = "thinking_signature"  # Anthropic 思考块签名无效
    long_context_tier = "long_context_tier"    # Anthropic “额外用量”层级门控

    # 兜底
    unknown = "unknown"                  # 无法分类 — 带退避重试


# ── 分类结果 ───────────────────────────────────────────────────────

@dataclass
class ClassifiedError:
    """API 错误的结构化分类,带恢复提示。"""

    reason: FailoverReason
    status_code: Optional[int] = None
    provider: Optional[str] = None
    model: Optional[str] = None
    message: str = ""
    error_context: Dict[str, Any] = field(default_factory=dict)

    # 恢复动作提示 — 重试循环检查这些而不是
    # 重新分类错误本身。
    retryable: bool = True
    should_compress: bool = False
    should_rotate_credential: bool = False
    should_fallback: bool = False

    @property
    def is_auth(self) -> bool:
        return self.reason in (FailoverReason.auth, FailoverReason.auth_permanent)

    @property
    def is_transient(self) -> bool:
        """错误预计会在重试时解决(带或不带退避)。"""
        return self.reason in (
            FailoverReason.rate_limit,
            FailoverReason.overloaded,
            FailoverReason.server_error,
            FailoverReason.timeout,
            FailoverReason.unknown,
        )


# ── 提供者特定模式 ──────────────────────────────────────────────────

# 指示计费耗尽的模式(非瞬时限流)
_BILLING_PATTERNS = [
    "insufficient credits",
    "insufficient_quota",
    "credit balance",
    "credits have been exhausted",
    "top up your credits",
    "payment required",
    "billing hard limit",
    "exceeded your current quota",
    "account is deactivated",
    "plan does not include",
]

# 指示限流的模式(瞬时的,将解决)
_RATE_LIMIT_PATTERNS = [
    "rate limit",
    "rate_limit",
    "too many requests",
    "throttled",
    "requests per minute",
    "tokens per minute",
    "requests per day",
    "try again in",
    "please retry after",
    "resource_exhausted",
]

# 需要消除歧义的使用限制模式(可能是计费或限流)
_USAGE_LIMIT_PATTERNS = [
    "usage limit",
    "quota",
    "limit exceeded",
    "key limit exceeded",
]

# 确认使用限制是瞬时的(非计费)的模式
_USAGE_LIMIT_TRANSIENT_SIGNALS = [
    "try again",
    "retry",
    "resets at",
    "reset in",
    "wait",
    "requests remaining",
    "periodic",
    "window",
]

# 从消息文本检测的载荷过大模式(无 status_code 属性)。
# 代理和一些后端在错误消息中嵌入 HTTP 状态。
_PAYLOAD_TOO_LARGE_PATTERNS = [
    "request entity too large",
    "payload too large",
    "error code: 413",
]

# 上下文溢出模式
_CONTEXT_OVERFLOW_PATTERNS = [
    "context length",
    "context size",
    "maximum context",
    "token limit",
    "too many tokens",
    "reduce the length",
    "exceeds the limit",
    "context window",
    "prompt is too long",
    "prompt exceeds max length",
    "max_tokens",
    "maximum number of tokens",
    # 中文错误消息(某些提供者返回这些)
    "超过最大长度",
    "上下文长度",
]

# 模型未找到模式
_MODEL_NOT_FOUND_PATTERNS = [
    "is not a valid model",
    "invalid model",
    "model not found",
    "model_not_found",
    "does not exist",
    "no such model",
    "unknown model",
    "unsupported model",
]

# 认证模式(非状态码信号)
_AUTH_PATTERNS = [
    "invalid api key",
    "invalid_api_key",
    "authentication",
    "unauthorized",
    "forbidden",
    "invalid token",
    "token expired",
    "token revoked",
    "access denied",
]

# Anthropic 思考块签名模式
_THINKING_SIG_PATTERNS = [
    "signature",  # Combined with "thinking" check
]

# 传输错误类型名称
_TRANSPORT_ERROR_TYPES = frozenset({
    "ReadTimeout", "ConnectTimeout", "PoolTimeout",
    "ConnectError", "RemoteProtocolError",
    "ConnectionError", "ConnectionResetError",
    "ConnectionAbortedError", "BrokenPipeError",
    "TimeoutError", "ReadError",
    "ServerDisconnectedError",
    # OpenAI SDK 错误(不是 Python 内置类的子类)
    "APIConnectionError",
    "APITimeoutError",
})

# 服务器断开模式(无状态码,但传输级别)
_SERVER_DISCONNECT_PATTERNS = [
    "server disconnected",
    "peer closed connection",
    "connection reset by peer",
    "connection was closed",
    "network connection lost",
    "unexpected eof",
    "incomplete chunked read",
]


# ── 分类管道 ─────────────────────────────────────────────────────

def classify_api_error(
    error: Exception,
    *,
    provider: str = "",
    model: str = "",
    approx_tokens: int = 0,
    context_length: int = 200000,
    num_messages: int = 0,
) -> ClassifiedError:
    """将 API 错误分类为结构化的恢复建议。

    优先级排序的管道:
      1. 特殊情况的提供者特定模式(思考签名、层级门控)
      2. HTTP 状态码 + 消息感知细化
      3. 错误代码分类(来自响应体)
      4. 消息模式匹配(计费 vs 限流 vs 上下文 vs 认证)
      5. 传输错误启发式
      6. 服务器断开 + 大会话 → 上下文溢出
      7. 兜底: unknown(带退避重试)

    Args:
        error: API 调用的异常。
        provider: 当前提供者名称(例如 "openrouter"、"anthropic")。
        model: 当前模型 slug。
        approx_tokens: 当前上下文的近似 token 数。
        context_length: 当前模型的最大上下文长度。

    Returns:
        ClassifiedError,带原因和恢复动作提示。
    """
    status_code = _extract_status_code(error)
    error_type = type(error).__name__
    body = _extract_error_body(error)
    error_code = _extract_error_code(body)

    # 构建全面的错误消息字符串用于模式匹配。
    # 仅 str(error) 可能不包含体消息(例如 OpenAI SDK 的
    # APIStatusError.__str__ 返回第一个参数,而不是体)。
    # 附加体消息,以便 402 消除歧义中的 "try again" 等模式
    # 即使在结构化体中也能检测到。
    #
    # 还提取 metadata.raw — OpenRouter 将上游提供者错误包装在
    # {"error": {"message": "Provider returned error", "metadata":
    # {"raw": "<actual error JSON>"}}} 中,真正的错误消息(例如
    # "context length exceeded")仅在内层 JSON 中。
    _raw_msg = str(error).lower()
    _body_msg = ""
    _metadata_msg = ""
    if isinstance(body, dict):
        _err_obj = body.get("error", {})
        if isinstance(_err_obj, dict):
            _body_msg = (_err_obj.get("message") or "").lower()
            # 解析 metadata.raw 用于包装的提供者错误
            _metadata = _err_obj.get("metadata", {})
            if isinstance(_metadata, dict):
                _raw_json = _metadata.get("raw") or ""
                if isinstance(_raw_json, str) and _raw_json.strip():
                    try:
                        import json
                        _inner = json.loads(_raw_json)
                        if isinstance(_inner, dict):
                            _inner_err = _inner.get("error", {})
                            if isinstance(_inner_err, dict):
                                _metadata_msg = (_inner_err.get("message") or "").lower()
                    except (json.JSONDecodeError, TypeError):
                        pass
        if not _body_msg:
            _body_msg = (body.get("message") or "").lower()
    # 合并所有消息源用于模式匹配
    parts = [_raw_msg]
    if _body_msg and _body_msg not in _raw_msg:
        parts.append(_body_msg)
    if _metadata_msg and _metadata_msg not in _raw_msg and _metadata_msg not in _body_msg:
        parts.append(_metadata_msg)
    error_msg = " ".join(parts)
    provider_lower = (provider or "").strip().lower()
    model_lower = (model or "").strip().lower()

    def _result(reason: FailoverReason, **overrides) -> ClassifiedError:
        defaults = {
            "reason": reason,
            "status_code": status_code,
            "provider": provider,
            "model": model,
            "message": _extract_message(error, body),
        }
        defaults.update(overrides)
        return ClassifiedError(**defaults)

    # ── 1. 提供者特定模式(最高优先级) ────────────

    # Anthropic 思考块签名无效(400)。
    # 不限制提供者 — OpenRouter 代理 Anthropic 错误,因此
    # 提供者可能是 "openrouter",即使错误是 Anthropic 特定的。
    # 消息模式("signature" + "thinking")足够独特。
    if (
        status_code == 400
        and "signature" in error_msg
        and "thinking" in error_msg
    ):
        return _result(
            FailoverReason.thinking_signature,
            retryable=True,
            should_compress=False,
        )

    # Anthropic 长上下文层级门控(429 "extra usage" + "long context")
    if (
        status_code == 429
        and "extra usage" in error_msg
        and "long context" in error_msg
    ):
        return _result(
            FailoverReason.long_context_tier,
            retryable=True,
            should_compress=True,
        )

    # ── 2. HTTP 状态码分类 ──────────────────────────

    if status_code is not None:
        classified = _classify_by_status(
            status_code, error_msg, error_code, body,
            provider=provider_lower, model=model_lower,
            approx_tokens=approx_tokens, context_length=context_length,
            num_messages=num_messages,
            result_fn=_result,
        )
        if classified is not None:
            return classified

    # ── 3. 错误代码分类 ────────────────────────────────

    if error_code:
        classified = _classify_by_error_code(error_code, error_msg, _result)
        if classified is not None:
            return classified

    # ── 4. 消息模式匹配(无状态码) ────────────────

    classified = _classify_by_message(
        error_msg, error_type,
        approx_tokens=approx_tokens,
        context_length=context_length,
        result_fn=_result,
    )
    if classified is not None:
        return classified

    # ── 5. 服务器断开 + 大会话 → 上下文溢出 ─────
    # 必须在通用传输错误捕获之前 — 大会话的断开
    # 更可能是上下文溢出而非瞬时的传输小问题。
    # 如果没有此排序,RemoteProtocolError 总是映射到 timeout,
    # 而不考虑会话大小。

    is_disconnect = any(p in error_msg for p in _SERVER_DISCONNECT_PATTERNS)
    if is_disconnect and not status_code:
        is_large = approx_tokens > context_length * 0.6 or approx_tokens > 120000 or num_messages > 200
        if is_large:
            return _result(
                FailoverReason.context_overflow,
                retryable=True,
                should_compress=True,
            )
        return _result(FailoverReason.timeout, retryable=True)

    # ── 6. 传输 / 超时启发式 ───────────────────────────

    if error_type in _TRANSPORT_ERROR_TYPES or isinstance(error, (TimeoutError, ConnectionError, OSError)):
        return _result(FailoverReason.timeout, retryable=True)

    # ── 7. 兜底: unknown ────────────────────────────────────────

    return _result(FailoverReason.unknown, retryable=True)


# ── 状态码分类 ──────────────────────────────────────────────────────────

def _classify_by_status(
    status_code: int,
    error_msg: str,
    error_code: str,
    body: dict,
    *,
    provider: str,
    model: str,
    approx_tokens: int,
    context_length: int,
    num_messages: int = 0,
    result_fn,
) -> Optional[ClassifiedError]:
    """基于 HTTP 状态码进行分类,带消息感知细化。"""

    if status_code == 401:
        # 本身不可重试 — 凭据池轮换和
        # 提供者特定的刷新(Codex、Anthropic、Nous)在
        # run_agent.py 的可重试性检查之前运行。如果成功,
        # 循环 `continue`。如果失败,retryable=False 确保我们
        # 命中客户端错误中止路径(首先尝试回退)。
        return result_fn(
            FailoverReason.auth,
            retryable=False,
            should_rotate_credential=True,
            should_fallback=True,
        )

    if status_code == 403:
        # OpenRouter 403 "key limit exceeded" 实际上是计费问题
        if "key limit exceeded" in error_msg or "spending limit" in error_msg:
            return result_fn(
                FailoverReason.billing,
                retryable=False,
                should_rotate_credential=True,
                should_fallback=True,
            )
        return result_fn(
            FailoverReason.auth,
            retryable=False,
            should_fallback=True,
        )

    if status_code == 402:
        return _classify_402(error_msg, result_fn)

    if status_code == 404:
        if any(p in error_msg for p in _MODEL_NOT_FOUND_PATTERNS):
            return result_fn(
                FailoverReason.model_not_found,
                retryable=False,
                should_fallback=True,
            )
        # 通用 404 — 可能是模型或端点
        return result_fn(
            FailoverReason.model_not_found,
            retryable=False,
            should_fallback=True,
        )

    if status_code == 413:
        return result_fn(
            FailoverReason.payload_too_large,
            retryable=True,
            should_compress=True,
        )

    if status_code == 429:
        # 已经在上方的 long_context_tier 中检查过;这是正常的限流
        return result_fn(
            FailoverReason.rate_limit,
            retryable=True,
            should_rotate_credential=True,
            should_fallback=True,
        )

    if status_code == 400:
        return _classify_400(
            error_msg, error_code, body,
            provider=provider, model=model,
            approx_tokens=approx_tokens,
            context_length=context_length,
            num_messages=num_messages,
            result_fn=result_fn,
        )

    if status_code in (500, 502):
        return result_fn(FailoverReason.server_error, retryable=True)

    if status_code in (503, 529):
        return result_fn(FailoverReason.overloaded, retryable=True)

    # 其他 4xx — 不可重试
    if 400 <= status_code < 500:
        return result_fn(
            FailoverReason.format_error,
            retryable=False,
            should_fallback=True,
        )

    # 其他 5xx — 可重试
    if 500 <= status_code < 600:
        return result_fn(FailoverReason.server_error, retryable=True)

    return None


def _classify_402(error_msg: str, result_fn) -> ClassifiedError:
    """消除 402 歧义:计费耗尽 vs 瞬时使用限制。

    OpenClaw 的关键洞察:某些 402 是伪装成支付错误的瞬时限流。
    "Usage limit, try again in 5 minutes" 不是计费问题 —
    它是会重置的周期性配额。
    """
    # 首先检查瞬时使用限制信号
    has_usage_limit = any(p in error_msg for p in _USAGE_LIMIT_PATTERNS)
    has_transient_signal = any(p in error_msg for p in _USAGE_LIMIT_TRANSIENT_SIGNALS)

    if has_usage_limit and has_transient_signal:
        # 瞬态配额 — 视为限流,不是计费
        return result_fn(
            FailoverReason.rate_limit,
            retryable=True,
            should_rotate_credential=True,
            should_fallback=True,
        )

    # 确认计费耗尽
    return result_fn(
        FailoverReason.billing,
        retryable=False,
        should_rotate_credential=True,
        should_fallback=True,
    )


def _classify_400(
    error_msg: str,
    error_code: str,
    body: dict,
    *,
    provider: str,
    model: str,
    approx_tokens: int,
    context_length: int,
    num_messages: int = 0,
    result_fn,
) -> ClassifiedError:
    """分类 400 错误请求 — 上下文溢出、格式错误或通用。"""

    # 400 的上下文溢出
    if any(p in error_msg for p in _CONTEXT_OVERFLOW_PATTERNS):
        return result_fn(
            FailoverReason.context_overflow,
            retryable=True,
            should_compress=True,
        )

    # 某些提供者返回模型未找到为 400 而不是 404(例如 OpenRouter)。
    if any(p in error_msg for p in _MODEL_NOT_FOUND_PATTERNS):
        return result_fn(
            FailoverReason.model_not_found,
            retryable=False,
            should_fallback=True,
        )

    # 某些提供者返回限流 / 计费错误为 400 而不是 429/402。
    # 在回退到 format_error 之前检查这些模式。
    if any(p in error_msg for p in _RATE_LIMIT_PATTERNS):
        return result_fn(
            FailoverReason.rate_limit,
            retryable=True,
            should_rotate_credential=True,
            should_fallback=True,
        )
    if any(p in error_msg for p in _BILLING_PATTERNS):
        return result_fn(
            FailoverReason.billing,
            retryable=False,
            should_rotate_credential=True,
            should_fallback=True,
        )

    # 通用 400 + 大会话 → 可能的上下文溢出
    # 当上下文过大时,Anthropic 有时返回裸的 "Error" 消息
    err_body_msg = ""
    if isinstance(body, dict):
        err_obj = body.get("error", {})
        if isinstance(err_obj, dict):
            err_body_msg = (err_obj.get("message") or "").strip().lower()
    is_generic = len(err_body_msg) < 30 or err_body_msg in ("error", "")
    is_large = approx_tokens > context_length * 0.4 or approx_tokens > 80000 or num_messages > 80

    if is_generic and is_large:
        return result_fn(
            FailoverReason.context_overflow,
            retryable=True,
            should_compress=True,
        )

    # 不可重试的格式错误
    return result_fn(
        FailoverReason.format_error,
        retryable=False,
        should_fallback=True,
    )


# ── 错误代码分类 ────────────────────────────────────────────────────

def _classify_by_error_code(
    error_code: str, error_msg: str, result_fn,
) -> Optional[ClassifiedError]:
    """通过响应体中的结构化错误代码进行分类。"""
    code_lower = error_code.lower()

    if code_lower in ("resource_exhausted", "throttled", "rate_limit_exceeded"):
        return result_fn(
            FailoverReason.rate_limit,
            retryable=True,
            should_rotate_credential=True,
        )

    if code_lower in ("insufficient_quota", "billing_not_active", "payment_required"):
        return result_fn(
            FailoverReason.billing,
            retryable=False,
            should_rotate_credential=True,
            should_fallback=True,
        )

    if code_lower in ("model_not_found", "model_not_available", "invalid_model"):
        return result_fn(
            FailoverReason.model_not_found,
            retryable=False,
            should_fallback=True,
        )

    if code_lower in ("context_length_exceeded", "max_tokens_exceeded"):
        return result_fn(
            FailoverReason.context_overflow,
            retryable=True,
            should_compress=True,
        )

    return None


# ── 消息模式分类 ──────────────────────────────────────────────────────

def _classify_by_message(
    error_msg: str,
    error_type: str,
    *,
    approx_tokens: int,
    context_length: int,
    result_fn,
) -> Optional[ClassifiedError]:
    """当无状态码时,基于错误消息模式进行分类。"""

    # 载荷过大模式(从无 status_code 时的消息文本)
    if any(p in error_msg for p in _PAYLOAD_TOO_LARGE_PATTERNS):
        return result_fn(
            FailoverReason.payload_too_large,
            retryable=True,
            should_compress=True,
        )

    # 计费模式
    if any(p in error_msg for p in _BILLING_PATTERNS):
        return result_fn(
            FailoverReason.billing,
            retryable=False,
            should_rotate_credential=True,
            should_fallback=True,
        )

    # 限流模式
    if any(p in error_msg for p in _RATE_LIMIT_PATTERNS):
        return result_fn(
            FailoverReason.rate_limit,
            retryable=True,
            should_rotate_credential=True,
            should_fallback=True,
        )

    # 上下文溢出模式
    if any(p in error_msg for p in _CONTEXT_OVERFLOW_PATTERNS):
        return result_fn(
            FailoverReason.context_overflow,
            retryable=True,
            should_compress=True,
        )

    # 认证模式
    if any(p in error_msg for p in _AUTH_PATTERNS):
        return result_fn(
            FailoverReason.auth,
            retryable=True,
            should_rotate_credential=True,
        )

    # 模型未找到模式
    if any(p in error_msg for p in _MODEL_NOT_FOUND_PATTERNS):
        return result_fn(
            FailoverReason.model_not_found,
            retryable=False,
            should_fallback=True,
        )

    return None


# ── 助手 ──────────────────────────────────────────────────────────────

def _extract_status_code(error: Exception) -> Optional[int]:
    """遍历错误及其原因链以查找 HTTP 状态码。"""
    current = error
    for _ in range(5):  # Max depth to prevent infinite loops
        code = getattr(current, "status_code", None)
        if isinstance(code, int):
            return code
        # 某些 SDK 使用 .status 而不是 .status_code
        code = getattr(current, "status", None)
        if isinstance(code, int) and 100 <= code < 600:
            return code
        # 遍历原因链
        cause = getattr(current, "__cause__", None) or getattr(current, "__context__", None)
        if cause is None or cause is current:
            break
        current = cause
    return None


def _extract_error_body(error: Exception) -> dict:
    """从 SDK 异常中提取结构化错误体。"""
    body = getattr(error, "body", None)
    if isinstance(body, dict):
        return body
    # 某些错误有 .response.json()
    response = getattr(error, "response", None)
    if response is not None:
        try:
            json_body = response.json()
            if isinstance(json_body, dict):
                return json_body
        except Exception:
            pass
    return {}


def _extract_error_code(body: dict) -> str:
    """从响应体中提取错误代码字符串。"""
    if not body:
        return ""
    error_obj = body.get("error", {})
    if isinstance(error_obj, dict):
        code = error_obj.get("code") or error_obj.get("type") or ""
        if isinstance(code, str) and code.strip():
            return code.strip()
    # 顶级 code
    code = body.get("code") or body.get("error_code") or ""
    if isinstance(code, (str, int)):
        return str(code).strip()
    return ""


def _extract_message(error: Exception, body: dict) -> str:
    """提取最具信息量的错误消息。"""
    # 首先尝试结构化体
    if body:
        error_obj = body.get("error", {})
        if isinstance(error_obj, dict):
            msg = error_obj.get("message", "")
            if isinstance(msg, str) and msg.strip():
                return msg.strip()[:500]
        msg = body.get("message", "")
        if isinstance(msg, str) and msg.strip():
            return msg.strip()[:500]
    # 回退到 str(error)
    return str(error)[:500]
