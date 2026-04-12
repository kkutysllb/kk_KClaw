"""同一提供者的凭据故障转移持久化多凭据池。"""

from __future__ import annotations

import logging
import random
import threading
import time
import uuid
import os
import re
from dataclasses import dataclass, fields, replace
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from kclaw_constants import OPENROUTER_BASE_URL
import kclaw_cli.auth as auth_mod
from kclaw_cli.auth import (
    CODEX_ACCESS_TOKEN_REFRESH_SKEW_SECONDS,
    DEFAULT_AGENT_KEY_MIN_TTL_SECONDS,
    PROVIDER_REGISTRY,
    _codex_access_token_is_expiring,
    _decode_jwt_claims,
    _import_codex_cli_tokens,
    _load_auth_store,
    _load_provider_state,
    _resolve_zai_base_url,
    read_credential_pool,
    write_credential_pool,
)

logger = logging.getLogger(__name__)


def _load_config_safe() -> Optional[dict]:
    """安全加载 config.yaml，出错时返回 None。"""
    try:
        from kclaw_cli.config import load_config

        return load_config()
    except Exception:
        return None


# --- 状态和类型常量 ---

STATUS_OK = "ok"
STATUS_EXHAUSTED = "exhausted"

AUTH_TYPE_OAUTH = "oauth"
AUTH_TYPE_API_KEY = "api_key"

SOURCE_MANUAL = "manual"

STRATEGY_FILL_FIRST = "fill_first"
STRATEGY_ROUND_ROBIN = "round_robin"
STRATEGY_RANDOM = "random"
STRATEGY_LEAST_USED = "least_used"
SUPPORTED_POOL_STRATEGIES = {
    STRATEGY_FILL_FIRST,
    STRATEGY_ROUND_ROBIN,
    STRATEGY_RANDOM,
    STRATEGY_LEAST_USED,
}

# 耗尽凭据的重试冷却时间。
# 429（速率限制）和 402（计费/配额）都在 1 小时后冷却。
# 提供者提供的 reset_at 时间戳会覆盖这些默认值。
EXHAUSTED_TTL_429_SECONDS = 60 * 60          # 1 hour
EXHAUSTED_TTL_DEFAULT_SECONDS = 60 * 60      # 1 hour

# 自定义 OpenAI 兼容端点的池键前缀。
# 自定义端点都共享 provider='custom'，但按键区分：
# 'custom:<normalized_name>'。
CUSTOM_POOL_PREFIX = "custom:"


# 仅通过 JSON 往返的字段 — 从不用于逻辑属性。
_EXTRA_KEYS = frozenset({
    "token_type", "scope", "client_id", "portal_base_url", "obtained_at",
    "expires_in", "agent_key_id", "agent_key_expires_in", "agent_key_reused",
    "agent_key_obtained_at", "tls",
})


@dataclass
class PooledCredential:
    provider: str
    id: str
    label: str
    auth_type: str
    priority: int
    source: str
    access_token: str
    refresh_token: Optional[str] = None
    last_status: Optional[str] = None
    last_status_at: Optional[float] = None
    last_error_code: Optional[int] = None
    last_error_reason: Optional[str] = None
    last_error_message: Optional[str] = None
    last_error_reset_at: Optional[float] = None
    base_url: Optional[str] = None
    expires_at: Optional[str] = None
    expires_at_ms: Optional[int] = None
    last_refresh: Optional[str] = None
    inference_base_url: Optional[str] = None
    agent_key: Optional[str] = None
    agent_key_expires_at: Optional[str] = None
    request_count: int = 0
    extra: Dict[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.extra is None:
            self.extra = {}

    def __getattr__(self, name: str):
        if name in _EXTRA_KEYS:
            return self.extra.get(name)
        raise AttributeError(f"'{type(self).__name__}' object has no attribute {name!r}")

    @classmethod
    def from_dict(cls, provider: str, payload: Dict[str, Any]) -> "PooledCredential":
        field_names = {f.name for f in fields(cls) if f.name != "provider"}
        data = {k: payload.get(k) for k in field_names if k in payload}
        extra = {k: payload[k] for k in _EXTRA_KEYS if k in payload and payload[k] is not None}
        data["extra"] = extra
        data.setdefault("id", uuid.uuid4().hex[:6])
        data.setdefault("label", payload.get("source", provider))
        data.setdefault("auth_type", AUTH_TYPE_API_KEY)
        data.setdefault("priority", 0)
        data.setdefault("source", SOURCE_MANUAL)
        data.setdefault("access_token", "")
        return cls(provider=provider, **data)

    def to_dict(self) -> Dict[str, Any]:
        _ALWAYS_EMIT = {
            "last_status",
            "last_status_at",
            "last_error_code",
            "last_error_reason",
            "last_error_message",
            "last_error_reset_at",
        }
        result: Dict[str, Any] = {}
        for field_def in fields(self):
            if field_def.name in ("provider", "extra"):
                continue
            value = getattr(self, field_def.name)
            if value is not None or field_def.name in _ALWAYS_EMIT:
                result[field_def.name] = value
        for k, v in self.extra.items():
            if v is not None:
                result[k] = v
        return result

    @property
    def runtime_api_key(self) -> str:
        if self.provider == "nous":
            return str(self.agent_key or self.access_token or "")
        return str(self.access_token or "")

    @property
    def runtime_base_url(self) -> Optional[str]:
        if self.provider == "nous":
            return self.inference_base_url or self.base_url
        return self.base_url


def label_from_token(token: str, fallback: str) -> str:
    claims = _decode_jwt_claims(token)
    for key in ("email", "preferred_username", "upn"):
        value = claims.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return fallback


def _next_priority(entries: List[PooledCredential]) -> int:
    return max((entry.priority for entry in entries), default=-1) + 1


def _is_manual_source(source: str) -> bool:
    normalized = (source or "").strip().lower()
    return normalized == SOURCE_MANUAL or normalized.startswith(f"{SOURCE_MANUAL}:")


def _exhausted_ttl(error_code: Optional[int]) -> int:
    """根据导致耗尽的 HTTP 状态码返回冷却秒数。"""
    if error_code == 429:
        return EXHAUSTED_TTL_429_SECONDS
    return EXHAUSTED_TTL_DEFAULT_SECONDS


def _parse_absolute_timestamp(value: Any) -> Optional[float]:
    """尽力解析提供者重置时间戳。

    接受纪元秒、纪元毫秒和 ISO-8601 字符串。
    返回自纪元以来的秒数。
    """
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        numeric = float(value)
        if numeric <= 0:
            return None
        return numeric / 1000.0 if numeric > 1_000_000_000_000 else numeric
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            numeric = float(raw)
        except ValueError:
            numeric = None
        if numeric is not None:
            return numeric / 1000.0 if numeric > 1_000_000_000_000 else numeric
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def _extract_retry_delay_seconds(message: str) -> Optional[float]:
    if not message:
        return None
    delay_match = re.search(r"quotaResetDelay[:\s\"]+(\d+(?:\.\d+)?)(ms|s)", message, re.IGNORECASE)
    if delay_match:
        value = float(delay_match.group(1))
        return value / 1000.0 if delay_match.group(2).lower() == "ms" else value
    sec_match = re.search(r"retry\s+(?:after\s+)?(\d+(?:\.\d+)?)\s*(?:sec|secs|seconds|s\b)", message, re.IGNORECASE)
    if sec_match:
        return float(sec_match.group(1))
    return None


def _normalize_error_context(error_context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(error_context, dict):
        return {}
    normalized: Dict[str, Any] = {}
    reason = error_context.get("reason")
    if isinstance(reason, str) and reason.strip():
        normalized["reason"] = reason.strip()
    message = error_context.get("message")
    if isinstance(message, str) and message.strip():
        normalized["message"] = message.strip()
    reset_at = (
        error_context.get("reset_at")
        or error_context.get("resets_at")
        or error_context.get("retry_until")
    )
    parsed_reset_at = _parse_absolute_timestamp(reset_at)
    if parsed_reset_at is None and isinstance(message, str):
        retry_delay_seconds = _extract_retry_delay_seconds(message)
        if retry_delay_seconds is not None:
            parsed_reset_at = time.time() + retry_delay_seconds
    if parsed_reset_at is not None:
        normalized["reset_at"] = parsed_reset_at
    return normalized


def _exhausted_until(entry: PooledCredential) -> Optional[float]:
    if entry.last_status != STATUS_EXHAUSTED:
        return None
    reset_at = _parse_absolute_timestamp(getattr(entry, "last_error_reset_at", None))
    if reset_at is not None:
        return reset_at
    if entry.last_status_at:
        return entry.last_status_at + _exhausted_ttl(entry.last_error_code)
    return None


def _normalize_custom_pool_name(name: str) -> str:
    """规范化自定义提供者名称以用作池键后缀。"""
    return name.strip().lower().replace(" ", "-")


def _iter_custom_providers(config: Optional[dict] = None):
    """为每个有效的 custom_providers 条目生成 (normalized_name, entry_dict)。"""
    if config is None:
        config = _load_config_safe()
    if config is None:
        return
    custom_providers = config.get("custom_providers")
    if not isinstance(custom_providers, list):
        return
    for entry in custom_providers:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not isinstance(name, str):
            continue
        yield _normalize_custom_pool_name(name), entry


def get_custom_provider_pool_key(base_url: str) -> Optional[str]:
    """在 config.yaml 的 custom_providers 列表中查找匹配 base_url 的条目，返回 'custom:<name>'。

    如果未找到匹配则返回 None。
    """
    if not base_url:
        return None
    normalized_url = base_url.strip().rstrip("/")
    for norm_name, entry in _iter_custom_providers():
        entry_url = str(entry.get("base_url") or "").strip().rstrip("/")
        if entry_url and entry_url == normalized_url:
            return f"{CUSTOM_POOL_PREFIX}{norm_name}"
    return None


def list_custom_pool_providers() -> List[str]:
    """返回 auth.json 中有条目的所有 'custom:*' 池键。"""
    pool_data = read_credential_pool(None)
    return sorted(
        key for key in pool_data
        if key.startswith(CUSTOM_POOL_PREFIX)
        and isinstance(pool_data.get(key), list)
        and pool_data[key]
    )


def _get_custom_provider_config(pool_key: str) -> Optional[Dict[str, Any]]:
    """返回与池键（如 'custom:together.ai'）匹配的 custom_providers 配置条目。"""
    if not pool_key.startswith(CUSTOM_POOL_PREFIX):
        return None
    suffix = pool_key[len(CUSTOM_POOL_PREFIX):]
    for norm_name, entry in _iter_custom_providers():
        if norm_name == suffix:
            return entry
    return None


def get_pool_strategy(provider: str) -> str:
    """返回提供者的已配置选择策略。"""
    config = _load_config_safe()
    if config is None:
        return STRATEGY_FILL_FIRST

    strategies = config.get("credential_pool_strategies")
    if not isinstance(strategies, dict):
        return STRATEGY_FILL_FIRST

    strategy = str(strategies.get(provider, "") or "").strip().lower()
    if strategy in SUPPORTED_POOL_STRATEGIES:
        return strategy
    return STRATEGY_FILL_FIRST


DEFAULT_MAX_CONCURRENT_PER_CREDENTIAL = 1


class CredentialPool:
    def __init__(self, provider: str, entries: List[PooledCredential]):
        self.provider = provider
        self._entries = sorted(entries, key=lambda entry: entry.priority)
        self._current_id: Optional[str] = None
        self._strategy = get_pool_strategy(provider)
        self._lock = threading.Lock()
        self._active_leases: Dict[str, int] = {}
        self._max_concurrent = DEFAULT_MAX_CONCURRENT_PER_CREDENTIAL

    def has_credentials(self) -> bool:
        return bool(self._entries)

    def has_available(self) -> bool:
        """如果至少有一个条目不在耗尽冷却中则为 True。"""
        return bool(self._available_entries())

    def entries(self) -> List[PooledCredential]:
        return list(self._entries)

    def current(self) -> Optional[PooledCredential]:
        if not self._current_id:
            return None
        return next((entry for entry in self._entries if entry.id == self._current_id), None)

    def _replace_entry(self, old: PooledCredential, new: PooledCredential) -> None:
        """按 id 原地交换条目，保持排序顺序。"""
        for idx, entry in enumerate(self._entries):
            if entry.id == old.id:
                self._entries[idx] = new
                return

    def _persist(self) -> None:
        write_credential_pool(
            self.provider,
            [entry.to_dict() for entry in self._entries],
        )

    def _mark_exhausted(
        self,
        entry: PooledCredential,
        status_code: Optional[int],
        error_context: Optional[Dict[str, Any]] = None,
    ) -> PooledCredential:
        normalized_error = _normalize_error_context(error_context)
        updated = replace(
            entry,
            last_status=STATUS_EXHAUSTED,
            last_status_at=time.time(),
            last_error_code=status_code,
            last_error_reason=normalized_error.get("reason"),
            last_error_message=normalized_error.get("message"),
            last_error_reset_at=normalized_error.get("reset_at"),
        )
        self._replace_entry(entry, updated)
        self._persist()
        return updated

    def _sync_anthropic_entry_from_credentials_file(self, entry: PooledCredential) -> PooledCredential:
        """从 ~/.claude/.credentials.json 同步 claude_code 池条目（如果 token 不同）。

        OAuth 刷新 token 是一次性的。当外部（如 Claude Code CLI
        或另一个 profile 的池）刷新 token 时，它会将新的令牌对
        写入 ~/.claude/.credentials.json。池条目的刷新 token
        就会变得过时。此方法检测并同步。
        """
        if self.provider != "anthropic" or entry.source != "claude_code":
            return entry
        try:
            from agent.anthropic_adapter import read_claude_code_credentials
            creds = read_claude_code_credentials()
            if not creds:
                return entry
            file_refresh = creds.get("refreshToken", "")
            file_access = creds.get("accessToken", "")
            file_expires = creds.get("expiresAt", 0)
            # 如果凭据文件有不同的令牌对，同步它
            if file_refresh and file_refresh != entry.refresh_token:
                logger.debug("池条目 %s：从凭据文件同步 token（刷新 token 已更改）", entry.id)
                updated = replace(
                    entry,
                    access_token=file_access,
                    refresh_token=file_refresh,
                    expires_at_ms=file_expires,
                    last_status=None,
                    last_status_at=None,
                    last_error_code=None,
                )
                self._replace_entry(entry, updated)
                self._persist()
                return updated
        except Exception as exc:
            logger.debug("从凭据文件同步失败：%s", exc)
        return entry

    def _sync_codex_entry_from_cli(self, entry: PooledCredential) -> PooledCredential:
        """从 ~/.codex/auth.json 同步 openai-codex 池条目（如果 token 不同）。

        OpenAI OAuth 刷新 token 是一次性的，每次刷新都会轮换。
        当 Codex CLI（或另一个 KClaw profile）刷新其 token 时，
        池条目的 refresh_token 就会变得过时。此方法通过与
        ~/.codex/auth.json 比较来检测并同步新的令牌对。
        """
        if self.provider != "openai-codex":
            return entry
        try:
            cli_tokens = _import_codex_cli_tokens()
            if not cli_tokens:
                return entry
            cli_refresh = cli_tokens.get("refresh_token", "")
            cli_access = cli_tokens.get("access_token", "")
            if cli_refresh and cli_refresh != entry.refresh_token:
                logger.debug("池条目 %s：从 ~/.codex/auth.json 同步 token（刷新 token 已更改）", entry.id)
                updated = replace(
                    entry,
                    access_token=cli_access,
                    refresh_token=cli_refresh,
                    last_status=None,
                    last_status_at=None,
                    last_error_code=None,
                )
                self._replace_entry(entry, updated)
                self._persist()
                return updated
        except Exception as exc:
            logger.debug("从 ~/.codex/auth.json 同步失败：%s", exc)
        return entry

    def _refresh_entry(self, entry: PooledCredential, *, force: bool) -> Optional[PooledCredential]:
        if entry.auth_type != AUTH_TYPE_OAUTH or not entry.refresh_token:
            if force:
                self._mark_exhausted(entry, None)
            return None

        try:
            if self.provider == "anthropic":
                from agent.anthropic_adapter import refresh_anthropic_oauth_pure

                refreshed = refresh_anthropic_oauth_pure(
                    entry.refresh_token,
                    use_json=entry.source.endswith("kclaw_pkce"),
                )
                updated = replace(
                    entry,
                    access_token=refreshed["access_token"],
                    refresh_token=refreshed["refresh_token"],
                    expires_at_ms=refreshed["expires_at_ms"],
                )
                # 保持 ~/.claude/.credentials.json 同步，以便回退路径
                # (resolve_anthropic_token) 和其他 profile 能看到
                # 最新的 token。
                if entry.source == "claude_code":
                    try:
                        from agent.anthropic_adapter import _write_claude_code_credentials
                        _write_claude_code_credentials(
                            refreshed["access_token"],
                            refreshed["refresh_token"],
                            refreshed["expires_at_ms"],
                        )
                    except Exception as wexc:
                        logger.debug("将刷新的 token 写入凭据文件失败：%s", wexc)
            elif self.provider == "openai-codex":
                refreshed = auth_mod.refresh_codex_oauth_pure(
                    entry.access_token,
                    entry.refresh_token,
                )
                updated = replace(
                    entry,
                    access_token=refreshed["access_token"],
                    refresh_token=refreshed["refresh_token"],
                    last_refresh=refreshed.get("last_refresh"),
                )
            elif self.provider == "nous":
                nous_state = {
                    "access_token": entry.access_token,
                    "refresh_token": entry.refresh_token,
                    "client_id": entry.client_id,
                    "portal_base_url": entry.portal_base_url,
                    "inference_base_url": entry.inference_base_url,
                    "token_type": entry.token_type,
                    "scope": entry.scope,
                    "obtained_at": entry.obtained_at,
                    "expires_at": entry.expires_at,
                    "agent_key": entry.agent_key,
                    "agent_key_expires_at": entry.agent_key_expires_at,
                    "tls": entry.tls,
                }
                refreshed = auth_mod.refresh_nous_oauth_from_state(
                    nous_state,
                    min_key_ttl_seconds=DEFAULT_AGENT_KEY_MIN_TTL_SECONDS,
                    force_refresh=force,
                    force_mint=force,
                )
                # 应用返回的字段：dataclass 字段通过 replace，额外字段通过 dict 更新
                field_updates = {}
                extra_updates = dict(entry.extra)
                _field_names = {f.name for f in fields(entry)}
                for k, v in refreshed.items():
                    if k in _field_names:
                        field_updates[k] = v
                    elif k in _EXTRA_KEYS:
                        extra_updates[k] = v
                updated = replace(entry, extra=extra_updates, **field_updates)
            else:
                return entry
        except Exception as exc:
            logger.debug("凭据刷新失败 %s/%s：%s", self.provider, entry.id, exc)
            # 对于 anthropic claude_code 条目：刷新 token 可能已被
            # 另一个进程消费。检查 ~/.claude/.credentials.json
            # 是否有更新的令牌对并重试一次。
            if self.provider == "anthropic" and entry.source == "claude_code":
                synced = self._sync_anthropic_entry_from_credentials_file(entry)
                if synced.refresh_token != entry.refresh_token:
                    logger.debug("使用凭据文件同步的 token 重试刷新")
                    try:
                        from agent.anthropic_adapter import refresh_anthropic_oauth_pure
                        refreshed = refresh_anthropic_oauth_pure(
                            synced.refresh_token,
                            use_json=synced.source.endswith("kclaw_pkce"),
                        )
                        updated = replace(
                            synced,
                            access_token=refreshed["access_token"],
                            refresh_token=refreshed["refresh_token"],
                            expires_at_ms=refreshed["expires_at_ms"],
                            last_status=STATUS_OK,
                            last_status_at=None,
                            last_error_code=None,
                        )
                        self._replace_entry(synced, updated)
                        self._persist()
                        try:
                            from agent.anthropic_adapter import _write_claude_code_credentials
                            _write_claude_code_credentials(
                                refreshed["access_token"],
                                refreshed["refresh_token"],
                                refreshed["expires_at_ms"],
                            )
                        except Exception as wexc:
                            logger.debug("将刷新的 token 写入凭据文件失败（重试路径）：%s", wexc)
                        return updated
                    except Exception as retry_exc:
                        logger.debug("重试刷新也失败：%s", retry_exc)
                elif not self._entry_needs_refresh(synced):
                    # 凭据文件有有效（未过期）的 token — 直接使用
                    logger.debug("凭据文件有有效 token，无需刷新直接使用")
                    return synced
            self._mark_exhausted(entry, None)
            return None

        updated = replace(
            updated,
            last_status=STATUS_OK,
            last_status_at=None,
            last_error_code=None,
            last_error_reason=None,
            last_error_message=None,
            last_error_reset_at=None,
        )
        self._replace_entry(entry, updated)
        self._persist()
        return updated

    def _entry_needs_refresh(self, entry: PooledCredential) -> bool:
        if entry.auth_type != AUTH_TYPE_OAUTH:
            return False
        if self.provider == "anthropic":
            if entry.expires_at_ms is None:
                return False
            return int(entry.expires_at_ms) <= int(time.time() * 1000) + 120_000
        if self.provider == "openai-codex":
            return _codex_access_token_is_expiring(
                entry.access_token,
                CODEX_ACCESS_TOKEN_REFRESH_SKEW_SECONDS,
            )
        if self.provider == "nous":
            # Nous 刷新/mint 可能需要网络访问，应在运行时凭据
            # 实际解析时才执行，而非仅枚举池用于列出、迁移或选择时。
            return False
        return False

    def mark_used(self, entry_id: Optional[str] = None) -> None:
        """递增 request_count 用于跟踪。由 least_used 策略使用。"""
        target_id = entry_id or self._current_id
        if not target_id:
            return
        with self._lock:
            for idx, entry in enumerate(self._entries):
                if entry.id == target_id:
                    self._entries[idx] = replace(entry, request_count=entry.request_count + 1)
                    return

    def select(self) -> Optional[PooledCredential]:
        with self._lock:
            return self._select_unlocked()

    def _available_entries(self, *, clear_expired: bool = False, refresh: bool = False) -> List[PooledCredential]:
        """返回不在耗尽冷却中的条目。

        当 *clear_expired* 为 True 时，冷却时间已过的条目
        被重置为 STATUS_OK 并持久化。当 *refresh* 为 True 时，
        需要 token 刷新的条目会被刷新（失败则跳过）。
        """
        now = time.time()
        cleared_any = False
        available: List[PooledCredential] = []
        for entry in self._entries:
            # 对于 anthropic claude_code 条目，在任何状态/刷新
            # 检查之前从凭据文件同步。这可以获取由其他进程
            # （Claude Code CLI、其他 KClaw profile）刷新的 token。
            if (self.provider == "anthropic" and entry.source == "claude_code"
                    and entry.last_status == STATUS_EXHAUSTED):
                synced = self._sync_anthropic_entry_from_credentials_file(entry)
                if synced is not entry:
                    entry = synced
                    cleared_any = True
            # 对于 openai-codex 条目，在任何状态/刷新检查之前
            # 从 ~/.codex/auth.json 同步。这可以获取由 Codex CLI
            # 或另一个 KClaw profile 刷新的 token。
            if (self.provider == "openai-codex"
                    and entry.last_status == STATUS_EXHAUSTED
                    and entry.refresh_token):
                synced = self._sync_codex_entry_from_cli(entry)
                if synced is not entry:
                    entry = synced
                    cleared_any = True
            if entry.last_status == STATUS_EXHAUSTED:
                exhausted_until = _exhausted_until(entry)
                if exhausted_until is not None and now < exhausted_until:
                    continue
                if clear_expired:
                    cleared = replace(
                        entry,
                        last_status=STATUS_OK,
                        last_status_at=None,
                        last_error_code=None,
                        last_error_reason=None,
                        last_error_message=None,
                        last_error_reset_at=None,
                    )
                    self._replace_entry(entry, cleared)
                    entry = cleared
                    cleared_any = True
            if refresh and self._entry_needs_refresh(entry):
                refreshed = self._refresh_entry(entry, force=False)
                if refreshed is None:
                    continue
                entry = refreshed
            available.append(entry)
        if cleared_any:
            self._persist()
        return available

    def _select_unlocked(self) -> Optional[PooledCredential]:
        available = self._available_entries(clear_expired=True, refresh=True)
        if not available:
            self._current_id = None
            logger.info("凭据池：没有可用条目（全部耗尽或为空）")
            return None

        if self._strategy == STRATEGY_RANDOM:
            entry = random.choice(available)
            self._current_id = entry.id
            return entry

        if self._strategy == STRATEGY_LEAST_USED and len(available) > 1:
            entry = min(available, key=lambda e: e.request_count)
            self._current_id = entry.id
            return entry

        if self._strategy == STRATEGY_ROUND_ROBIN and len(available) > 1:
            entry = available[0]
            rotated = [candidate for candidate in self._entries if candidate.id != entry.id]
            rotated.append(replace(entry, priority=len(self._entries) - 1))
            self._entries = [replace(candidate, priority=idx) for idx, candidate in enumerate(rotated)]
            self._persist()
            self._current_id = entry.id
            return self.current() or entry

        entry = available[0]
        self._current_id = entry.id
        return entry

    def peek(self) -> Optional[PooledCredential]:
        current = self.current()
        if current is not None:
            return current
        available = self._available_entries()
        return available[0] if available else None

    def mark_exhausted_and_rotate(
        self,
        *,
        status_code: Optional[int],
        error_context: Optional[Dict[str, Any]] = None,
    ) -> Optional[PooledCredential]:
        with self._lock:
            entry = self.current() or self._select_unlocked()
            if entry is None:
                return None
            _label = entry.label or entry.id[:8]
            logger.info(
                "凭据池：标记 %s 已耗尽（状态=%s），轮换",
                _label, status_code,
            )
            self._mark_exhausted(entry, status_code, error_context)
            self._current_id = None
            next_entry = self._select_unlocked()
            if next_entry:
                _next_label = next_entry.label or next_entry.id[:8]
                logger.info("凭据池：轮换到 %s", _next_label)
            return next_entry

    def acquire_lease(self, credential_id: Optional[str] = None) -> Optional[str]:
        """获取凭据的软租约。

        如果提供了特定的 credential_id，直接租用该条目。
        否则优先租用最少租用的可用凭据，使用优先级作为
        稳定的决胜因素。当每个凭据都已达到软上限时，
        仍返回最少租用的那个而非阻塞。
        """
        with self._lock:
            if credential_id:
                self._active_leases[credential_id] = self._active_leases.get(credential_id, 0) + 1
                self._current_id = credential_id
                return credential_id

            available = self._available_entries(clear_expired=True, refresh=True)
            if not available:
                return None

            below_cap = [
                entry for entry in available
                if self._active_leases.get(entry.id, 0) < self._max_concurrent
            ]
            candidates = below_cap if below_cap else available
            chosen = min(
                candidates,
                key=lambda entry: (self._active_leases.get(entry.id, 0), entry.priority),
            )
            self._active_leases[chosen.id] = self._active_leases.get(chosen.id, 0) + 1
            self._current_id = chosen.id
            return chosen.id

    def release_lease(self, credential_id: str) -> None:
        """释放先前获取的凭据租约。"""
        with self._lock:
            count = self._active_leases.get(credential_id, 0)
            if count <= 1:
                self._active_leases.pop(credential_id, None)
            else:
                self._active_leases[credential_id] = count - 1

    def active_lease_count(self, credential_id: str) -> int:
        """返回凭据的活动租约数。"""
        with self._lock:
            return self._active_leases.get(credential_id, 0)

    def try_refresh_current(self) -> Optional[PooledCredential]:
        with self._lock:
            return self._try_refresh_current_unlocked()

    def _try_refresh_current_unlocked(self) -> Optional[PooledCredential]:
        entry = self.current()
        if entry is None:
            return None
        refreshed = self._refresh_entry(entry, force=True)
        if refreshed is not None:
            self._current_id = refreshed.id
        return refreshed

    def reset_statuses(self) -> int:
        count = 0
        new_entries = []
        for entry in self._entries:
            if entry.last_status or entry.last_status_at or entry.last_error_code:
                new_entries.append(
                    replace(
                        entry,
                        last_status=None,
                        last_status_at=None,
                        last_error_code=None,
                        last_error_reason=None,
                        last_error_message=None,
                        last_error_reset_at=None,
                    )
                )
                count += 1
            else:
                new_entries.append(entry)
        if count:
            self._entries = new_entries
            self._persist()
        return count

    def remove_index(self, index: int) -> Optional[PooledCredential]:
        if index < 1 or index > len(self._entries):
            return None
        removed = self._entries.pop(index - 1)
        self._entries = [
            replace(entry, priority=new_priority)
            for new_priority, entry in enumerate(self._entries)
        ]
        self._persist()
        if self._current_id == removed.id:
            self._current_id = None
        return removed

    def resolve_target(self, target: Any) -> Tuple[Optional[int], Optional[PooledCredential], Optional[str]]:
        raw = str(target or "").strip()
        if not raw:
            return None, None, "未提供凭据目标。"

        for idx, entry in enumerate(self._entries, start=1):
            if entry.id == raw:
                return idx, entry, None

        label_matches = [
            (idx, entry)
            for idx, entry in enumerate(self._entries, start=1)
            if entry.label.strip().lower() == raw.lower()
        ]
        if len(label_matches) == 1:
            return label_matches[0][0], label_matches[0][1], None
        if len(label_matches) > 1:
            return None, None, f'凭据标签 "{raw}" 不明确。请使用数字索引或条目 id。'
        if raw.isdigit():
            index = int(raw)
            if 1 <= index <= len(self._entries):
                return index, self._entries[index - 1], None
            return None, None, f"没有凭据 #{index}。"
        return None, None, f'没有匹配 "{raw}" 的凭据。'

    def add_entry(self, entry: PooledCredential) -> PooledCredential:
        entry = replace(entry, priority=_next_priority(self._entries))
        self._entries.append(entry)
        self._persist()
        return entry


def _upsert_entry(entries: List[PooledCredential], provider: str, source: str, payload: Dict[str, Any]) -> bool:
    existing_idx = None
    for idx, entry in enumerate(entries):
        if entry.source == source:
            existing_idx = idx
            break

    if existing_idx is None:
        payload.setdefault("id", uuid.uuid4().hex[:6])
        payload.setdefault("priority", _next_priority(entries))
        payload.setdefault("label", payload.get("label") or source)
        entries.append(PooledCredential.from_dict(provider, payload))
        return True

    existing = entries[existing_idx]
    field_updates = {}
    extra_updates = {}
    _field_names = {f.name for f in fields(existing)}
    for key, value in payload.items():
        if key in {"id", "priority"} or value is None:
            continue
        if key == "label" and existing.label:
            continue
        if key in _field_names:
            if getattr(existing, key) != value:
                field_updates[key] = value
        elif key in _EXTRA_KEYS:
            if existing.extra.get(key) != value:
                extra_updates[key] = value
    if field_updates or extra_updates:
        if extra_updates:
            field_updates["extra"] = {**existing.extra, **extra_updates}
        entries[existing_idx] = replace(existing, **field_updates)
        return True
    return False


def _normalize_pool_priorities(provider: str, entries: List[PooledCredential]) -> bool:
    if provider != "anthropic":
        return False

    source_rank = {
        "env:ANTHROPIC_TOKEN": 0,
        "env:CLAUDE_CODE_OAUTH_TOKEN": 1,
        "kclaw_pkce": 2,
        "claude_code": 3,
        "env:ANTHROPIC_API_KEY": 4,
    }
    manual_entries = sorted(
        (entry for entry in entries if _is_manual_source(entry.source)),
        key=lambda entry: entry.priority,
    )
    seeded_entries = sorted(
        (entry for entry in entries if not _is_manual_source(entry.source)),
        key=lambda entry: (
            source_rank.get(entry.source, len(source_rank)),
            entry.priority,
            entry.label,
        ),
    )

    ordered = [*manual_entries, *seeded_entries]
    id_to_idx = {entry.id: idx for idx, entry in enumerate(entries)}
    changed = False
    for new_priority, entry in enumerate(ordered):
        if entry.priority != new_priority:
            entries[id_to_idx[entry.id]] = replace(entry, priority=new_priority)
            changed = True
    return changed


def _seed_from_singletons(provider: str, entries: List[PooledCredential]) -> Tuple[bool, Set[str]]:
    changed = False
    active_sources: Set[str] = set()
    auth_store = _load_auth_store()

    if provider == "anthropic":
        from agent.anthropic_adapter import read_claude_code_credentials, read_kclaw_oauth_credentials

        for source_name, creds in (
            ("kclaw_pkce", read_kclaw_oauth_credentials()),
            ("claude_code", read_claude_code_credentials()),
        ):
            if creds and creds.get("accessToken"):
                active_sources.add(source_name)
                changed |= _upsert_entry(
                    entries,
                    provider,
                    source_name,
                    {
                        "source": source_name,
                        "auth_type": AUTH_TYPE_OAUTH,
                        "access_token": creds.get("accessToken", ""),
                        "refresh_token": creds.get("refreshToken"),
                        "expires_at_ms": creds.get("expiresAt"),
                        "label": label_from_token(creds.get("accessToken", ""), source_name),
                    },
                )

    elif provider == "nous":
        state = _load_provider_state(auth_store, "nous")
        if state:
            active_sources.add("device_code")
            changed |= _upsert_entry(
                entries,
                provider,
                "device_code",
                {
                    "source": "device_code",
                    "auth_type": AUTH_TYPE_OAUTH,
                    "access_token": state.get("access_token", ""),
                    "refresh_token": state.get("refresh_token"),
                    "expires_at": state.get("expires_at"),
                    "token_type": state.get("token_type"),
                    "scope": state.get("scope"),
                    "client_id": state.get("client_id"),
                    "portal_base_url": state.get("portal_base_url"),
                    "inference_base_url": state.get("inference_base_url"),
                    "agent_key": state.get("agent_key"),
                    "agent_key_expires_at": state.get("agent_key_expires_at"),
                    "tls": state.get("tls") if isinstance(state.get("tls"), dict) else None,
                    "label": label_from_token(state.get("access_token", ""), "device_code"),
                },
            )

    elif provider == "openai-codex":
        state = _load_provider_state(auth_store, "openai-codex")
        tokens = state.get("tokens") if isinstance(state, dict) else None
        if isinstance(tokens, dict) and tokens.get("access_token"):
            active_sources.add("device_code")
            changed |= _upsert_entry(
                entries,
                provider,
                "device_code",
                {
                    "source": "device_code",
                    "auth_type": AUTH_TYPE_OAUTH,
                    "access_token": tokens.get("access_token", ""),
                    "refresh_token": tokens.get("refresh_token"),
                    "base_url": "https://chatgpt.com/backend-api/codex",
                    "last_refresh": state.get("last_refresh"),
                    "label": label_from_token(tokens.get("access_token", ""), "device_code"),
                },
            )

    return changed, active_sources


def _seed_from_env(provider: str, entries: List[PooledCredential]) -> Tuple[bool, Set[str]]:
    changed = False
    active_sources: Set[str] = set()
    if provider == "openrouter":
        token = os.getenv("OPENROUTER_API_KEY", "").strip()
        if token:
            source = "env:OPENROUTER_API_KEY"
            active_sources.add(source)
            changed |= _upsert_entry(
                entries,
                provider,
                source,
                {
                    "source": source,
                    "auth_type": AUTH_TYPE_API_KEY,
                    "access_token": token,
                    "base_url": OPENROUTER_BASE_URL,
                    "label": "OPENROUTER_API_KEY",
                },
            )
        return changed, active_sources

    pconfig = PROVIDER_REGISTRY.get(provider)
    if not pconfig or pconfig.auth_type != AUTH_TYPE_API_KEY:
        return changed, active_sources

    env_url = ""
    if pconfig.base_url_env_var:
        env_url = os.getenv(pconfig.base_url_env_var, "").strip().rstrip("/")

    env_vars = list(pconfig.api_key_env_vars)
    if provider == "anthropic":
        env_vars = [
            "ANTHROPIC_TOKEN",
            "CLAUDE_CODE_OAUTH_TOKEN",
            "ANTHROPIC_API_KEY",
        ]

    for env_var in env_vars:
        token = os.getenv(env_var, "").strip()
        if not token:
            continue
        source = f"env:{env_var}"
        active_sources.add(source)
        auth_type = AUTH_TYPE_OAUTH if provider == "anthropic" and not token.startswith("sk-ant-api") else AUTH_TYPE_API_KEY
        base_url = env_url or pconfig.inference_base_url
        if provider == "zai":
            base_url = _resolve_zai_base_url(token, pconfig.inference_base_url, env_url)
        changed |= _upsert_entry(
            entries,
            provider,
            source,
            {
                "source": source,
                "auth_type": auth_type,
                "access_token": token,
                "base_url": base_url,
                "label": env_var,
            },
        )
    return changed, active_sources


def _prune_stale_seeded_entries(entries: List[PooledCredential], active_sources: Set[str]) -> bool:
    retained = [
        entry
        for entry in entries
        if _is_manual_source(entry.source)
        or entry.source in active_sources
        or not (
            entry.source.startswith("env:")
            or entry.source in {"claude_code", "kclaw_pkce"}
        )
    ]
    if len(retained) == len(entries):
        return False
    entries[:] = retained
    return True


def _seed_custom_pool(pool_key: str, entries: List[PooledCredential]) -> Tuple[bool, Set[str]]:
    """从 custom_providers 配置和模型配置中种入自定义端点池。"""
    changed = False
    active_sources: Set[str] = set()

    # 从 custom_providers 配置条目的 api_key 字段种入
    cp_config = _get_custom_provider_config(pool_key)
    if cp_config:
        api_key = str(cp_config.get("api_key") or "").strip()
        base_url = str(cp_config.get("base_url") or "").strip().rstrip("/")
        name = str(cp_config.get("name") or "").strip()
        if api_key:
            source = f"config:{name}"
            active_sources.add(source)
            changed |= _upsert_entry(
                entries,
                pool_key,
                source,
                {
                    "source": source,
                    "auth_type": AUTH_TYPE_API_KEY,
                    "access_token": api_key,
                    "base_url": base_url,
                    "label": name or source,
                },
            )

    # 从 model.api_key 种入（当 model.provider=='custom' 且 model.base_url 匹配时）
    try:
        config = _load_config_safe()
        model_cfg = config.get("model") if config else None
        if isinstance(model_cfg, dict):
            model_provider = str(model_cfg.get("provider") or "").strip().lower()
            model_base_url = str(model_cfg.get("base_url") or "").strip().rstrip("/")
            model_api_key = ""
            for k in ("api_key", "api"):
                v = model_cfg.get(k)
                if isinstance(v, str) and v.strip():
                    model_api_key = v.strip()
                    break
            if model_provider == "custom" and model_base_url and model_api_key:
                # 检查此模型的 base_url 是否匹配我们的自定义提供者
                matched_key = get_custom_provider_pool_key(model_base_url)
                if matched_key == pool_key:
                    source = "model_config"
                    active_sources.add(source)
                    changed |= _upsert_entry(
                        entries,
                        pool_key,
                        source,
                        {
                            "source": source,
                            "auth_type": AUTH_TYPE_API_KEY,
                            "access_token": model_api_key,
                            "base_url": model_base_url,
                            "label": "model_config",
                        },
                    )
    except Exception:
        pass

    return changed, active_sources


def load_pool(provider: str) -> CredentialPool:
    provider = (provider or "").strip().lower()
    raw_entries = read_credential_pool(provider)
    entries = [PooledCredential.from_dict(provider, payload) for payload in raw_entries]

    if provider.startswith(CUSTOM_POOL_PREFIX):
        # 自定义端点池 — 从 custom_providers 配置和模型配置种入
        custom_changed, custom_sources = _seed_custom_pool(provider, entries)
        changed = custom_changed
        changed |= _prune_stale_seeded_entries(entries, custom_sources)
    else:
        singleton_changed, singleton_sources = _seed_from_singletons(provider, entries)
        env_changed, env_sources = _seed_from_env(provider, entries)
        changed = singleton_changed or env_changed
        changed |= _prune_stale_seeded_entries(entries, singleton_sources | env_sources)
        changed |= _normalize_pool_priorities(provider, entries)

    if changed:
        write_credential_pool(
            provider,
            [entry.to_dict() for entry in sorted(entries, key=lambda item: item.priority)],
        )
    return CredentialPool(provider, entries)
