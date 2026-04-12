"""推理 API 响应的速率限制跟踪。

从提供者响应中捕获 x-ratelimit-* 头信息,并为 /usage 斜杠命令
提供格式化显示。目前支持 Nous Portal 头格式(也被 OpenRouter 和
遵循相同约定的 OpenAI 兼容 API 使用)。

头信息模式(共 12 个头):
    x-ratelimit-limit-requests          RPM 上限
    x-ratelimit-limit-requests-1h       RPH 上限
    x-ratelimit-limit-tokens            TPM 上限
    x-ratelimit-limit-tokens-1h         TPH 上限
    x-ratelimit-remaining-requests      分钟窗口剩余请求数
    x-ratelimit-remaining-requests-1h   小时窗口剩余请求数
    x-ratelimit-remaining-tokens        分钟窗口剩余 token 数
    x-ratelimit-remaining-tokens-1h     小时窗口剩余 token 数
    x-ratelimit-reset-requests          分钟请求窗口重置秒数
    x-ratelimit-reset-requests-1h       小时请求窗口重置秒数
    x-ratelimit-reset-tokens            分钟 token 窗口重置秒数
    x-ratelimit-reset-tokens-1h         小时 token 窗口重置秒数
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Optional


@dataclass
class RateLimitBucket:
    """一个速率限制窗口(例如每分钟请求数)。"""

    limit: int = 0
    remaining: int = 0
    reset_seconds: float = 0.0
    captured_at: float = 0.0  # 捕获时的 time.time()

    @property
    def used(self) -> int:
        return max(0, self.limit - self.remaining)

    @property
    def usage_pct(self) -> float:
        if self.limit <= 0:
            return 0.0
        return (self.used / self.limit) * 100.0

    @property
    def remaining_seconds_now(self) -> float:
        """预估的剩余重置秒数,已根据已过时间调整。"""
        elapsed = time.time() - self.captured_at
        return max(0.0, self.reset_seconds - elapsed)


@dataclass
class RateLimitState:
    """从响应头解析的完整速率限制状态。"""

    requests_min: RateLimitBucket = field(default_factory=RateLimitBucket)
    requests_hour: RateLimitBucket = field(default_factory=RateLimitBucket)
    tokens_min: RateLimitBucket = field(default_factory=RateLimitBucket)
    tokens_hour: RateLimitBucket = field(default_factory=RateLimitBucket)
    captured_at: float = 0.0  # 头信息被捕获的时间
    provider: str = ""

    @property
    def has_data(self) -> bool:
        return self.captured_at > 0

    @property
    def age_seconds(self) -> float:
        if not self.has_data:
            return float("inf")
        return time.time() - self.captured_at


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_rate_limit_headers(
    headers: Mapping[str, str],
    provider: str = "",
) -> Optional[RateLimitState]:
    """将 x-ratelimit-* 头信息解析为 RateLimitState。

    如果不存在速率限制头信息则返回 None。
    """
    # 快速检查:必须至少存在一个速率限制头信息
    has_any = any(k.lower().startswith("x-ratelimit-") for k in headers)
    if not has_any:
        return None

    now = time.time()

    def _bucket(resource: str, suffix: str = "") -> RateLimitBucket:
        # 例如 resource="requests", suffix="" -> 每分钟
        #      resource="tokens", suffix="-1h" -> 每小时
        tag = f"{resource}{suffix}"
        return RateLimitBucket(
            limit=_safe_int(headers.get(f"x-ratelimit-limit-{tag}")),
            remaining=_safe_int(headers.get(f"x-ratelimit-remaining-{tag}")),
            reset_seconds=_safe_float(headers.get(f"x-ratelimit-reset-{tag}")),
            captured_at=now,
        )

    return RateLimitState(
        requests_min=_bucket("requests"),
        requests_hour=_bucket("requests", "-1h"),
        tokens_min=_bucket("tokens"),
        tokens_hour=_bucket("tokens", "-1h"),
        captured_at=now,
        provider=provider,
    )


# ── 格式化 ──────────────────────────────────────────────────────────


def _fmt_count(n: int) -> str:
    """人类友好的数字: 7999856 -> '8.0M', 33599 -> '33.6K', 799 -> '799'。"""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 10_000:
        return f"{n / 1_000:.1f}K"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def _fmt_seconds(seconds: float) -> str:
    """秒数 -> 人类友好的时长: '58s'、'2m 14s'、'58m 57s'、'1h 2m'。"""
    s = max(0, int(seconds))
    if s < 60:
        return f"{s}s"
    if s < 3600:
        m, sec = divmod(s, 60)
        return f"{m}m {sec}s" if sec else f"{m}m"
    h, remainder = divmod(s, 3600)
    m = remainder // 60
    return f"{h}h {m}m" if m else f"{h}h"


def _bar(pct: float, width: int = 20) -> str:
    """ASCII 进度条: [████████░░░░░░░░░░░░] 40%。"""
    filled = int(pct / 100.0 * width)
    filled = max(0, min(width, filled))
    empty = width - filled
    return f"[{'█' * filled}{'░' * empty}]"


def _bucket_line(label: str, bucket: RateLimitBucket, label_width: int = 14) -> str:
    """将一个桶格式化为单行。"""
    if bucket.limit <= 0:
        return f"  {label:<{label_width}}  (无数据)"

    pct = bucket.usage_pct
    used = _fmt_count(bucket.used)
    limit = _fmt_count(bucket.limit)
    remaining = _fmt_count(bucket.remaining)
    reset = _fmt_seconds(bucket.remaining_seconds_now)

    bar = _bar(pct)
    return f"  {label:<{label_width}} {bar} {pct:5.1f}%  {used}/{limit} 已用  ({remaining} 剩余, {reset} 后重置)"


def format_rate_limit_display(state: RateLimitState) -> str:
    """格式化速率限制状态用于终端/聊天显示。"""
    if not state.has_data:
        return "暂无速率限制数据 — 请先发起一次 API 请求。"

    age = state.age_seconds
    if age < 5:
        freshness = "刚刚"
    elif age < 60:
        freshness = f"{int(age)}秒前"
    else:
        freshness = f"{_fmt_seconds(age)}前"

    provider_label = state.provider.title() if state.provider else "提供者"

    lines = [
        f"{provider_label} 速率限制 (捕获于 {freshness}):",
        "",
        _bucket_line("请求/分钟", state.requests_min),
        _bucket_line("请求/小时", state.requests_hour),
        "",
        _bucket_line("Token/分钟", state.tokens_min),
        _bucket_line("Token/小时", state.tokens_hour),
    ]

    # 如果任何桶变得紧张则添加警告
    warnings = []
    for label, bucket in [
        ("requests/min", state.requests_min),
        ("requests/hr", state.requests_hour),
        ("tokens/min", state.tokens_min),
        ("tokens/hr", state.tokens_hour),
    ]:
        if bucket.limit > 0 and bucket.usage_pct >= 80:
            reset = _fmt_seconds(bucket.remaining_seconds_now)
            warnings.append(f"  ⚠ {label} 已达 {bucket.usage_pct:.0f}% — {reset} 后重置")

    if warnings:
        lines.append("")
        lines.extend(warnings)

    return "\n".join(lines)


def format_rate_limit_compact(state: RateLimitState) -> str:
    """单行紧凑摘要,用于状态栏 / gateway 消息。"""
    if not state.has_data:
        return "暂无速率限制数据。"

    rm = state.requests_min
    tm = state.tokens_min
    rh = state.requests_hour
    th = state.tokens_hour

    parts = []
    if rm.limit > 0:
        parts.append(f"RPM: {rm.remaining}/{rm.limit}")
    if rh.limit > 0:
        parts.append(f"RPH: {_fmt_count(rh.remaining)}/{_fmt_count(rh.limit)} (resets {_fmt_seconds(rh.remaining_seconds_now)})")
    if tm.limit > 0:
        parts.append(f"TPM: {_fmt_count(tm.remaining)}/{_fmt_count(tm.limit)}")
    if th.limit > 0:
        parts.append(f"TPH: {_fmt_count(th.remaining)}/{_fmt_count(th.limit)} (resets {_fmt_seconds(th.remaining_seconds_now)})")

    return " | ".join(parts)
