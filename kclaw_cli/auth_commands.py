"""凭据池认证子命令。"""

from __future__ import annotations

from getpass import getpass
import math
import time
from types import SimpleNamespace
import uuid

from agent.credential_pool import (
    AUTH_TYPE_API_KEY,
    AUTH_TYPE_OAUTH,
    CUSTOM_POOL_PREFIX,
    SOURCE_MANUAL,
    STATUS_EXHAUSTED,
    STRATEGY_FILL_FIRST,
    STRATEGY_ROUND_ROBIN,
    STRATEGY_RANDOM,
    STRATEGY_LEAST_USED,
    PooledCredential,
    _exhausted_until,
    _normalize_custom_pool_name,
    get_pool_strategy,
    label_from_token,
    list_custom_pool_providers,
    load_pool,
)
import kclaw_cli.auth as auth_mod
from kclaw_cli.auth import PROVIDER_REGISTRY
from kclaw_constants import OPENROUTER_BASE_URL


# 支持 OAuth 登录的提供者（除 API 密钥外）。
_OAUTH_CAPABLE_PROVIDERS = {"anthropic", "nous", "openai-codex", "qwen-oauth"}


def _get_custom_provider_names() -> list:
    """返回 config 中 custom_providers 的 (显示名称, 池键) 元组列表。"""
    try:
        from kclaw_cli.config import load_config

        config = load_config()
    except Exception:
        return []
    custom_providers = config.get("custom_providers")
    if not isinstance(custom_providers, list):
        return []
    result = []
    for entry in custom_providers:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        pool_key = f"{CUSTOM_POOL_PREFIX}{_normalize_custom_pool_name(name)}"
        result.append((name.strip(), pool_key))
    return result


def _resolve_custom_provider_input(raw: str) -> str | None:
    """如果原始输入匹配 custom_providers 条目名称（不区分大小写），返回其池键。"""
    normalized = (raw or "").strip().lower().replace(" ", "-")
    if not normalized:
        return None
    # Direct match on 'custom:name' format
    if normalized.startswith(CUSTOM_POOL_PREFIX):
        return normalized
    for display_name, pool_key in _get_custom_provider_names():
        if _normalize_custom_pool_name(display_name) == normalized:
            return pool_key
    return None


def _normalize_provider(provider: str) -> str:
    normalized = (provider or "").strip().lower()
    if normalized in {"or", "open-router"}:
        return "openrouter"
    # Check if it matches a custom provider name
    custom_key = _resolve_custom_provider_input(normalized)
    if custom_key:
        return custom_key
    return normalized


def _provider_base_url(provider: str) -> str:
    if provider == "openrouter":
        return OPENROUTER_BASE_URL
    if provider.startswith(CUSTOM_POOL_PREFIX):
        from agent.credential_pool import _get_custom_provider_config

        cp_config = _get_custom_provider_config(provider)
        if cp_config:
            return str(cp_config.get("base_url") or "").strip()
        return ""
    pconfig = PROVIDER_REGISTRY.get(provider)
    return pconfig.inference_base_url if pconfig else ""


def _oauth_default_label(provider: str, count: int) -> str:
    return f"{provider}-oauth-{count}"


def _api_key_default_label(count: int) -> str:
    return f"api-key-{count}"


def _display_source(source: str) -> str:
    return source.split(":", 1)[1] if source.startswith("manual:") else source


def _format_exhausted_status(entry) -> str:
    if entry.last_status != STATUS_EXHAUSTED:
        return ""
    reason = getattr(entry, "last_error_reason", None)
    reason_text = f" {reason}" if isinstance(reason, str) and reason.strip() else ""
    code = f" ({entry.last_error_code})" if entry.last_error_code else ""
    exhausted_until = _exhausted_until(entry)
    if exhausted_until is None:
        return f" 已耗尽{reason_text}{code}"
    remaining = max(0, int(math.ceil(exhausted_until - time.time())))
    if remaining <= 0:
        return f" 已耗尽{reason_text}{code}（准备重试）"
    minutes, seconds = divmod(remaining, 60)
    hours, minutes = divmod(minutes, 60)
    days, hours = divmod(hours, 24)
    if days:
        wait = f"{days}天{hours}时"
    elif hours:
        wait = f"{hours}时{minutes}分"
    elif minutes:
        wait = f"{minutes}分{seconds}秒"
    else:
        wait = f"{seconds}秒"
    return f" 已耗尽{reason_text}{code}（还剩 {wait}）"


def auth_add_command(args) -> None:
    provider = _normalize_provider(getattr(args, "provider", ""))
    if provider not in PROVIDER_REGISTRY and provider != "openrouter" and not provider.startswith(CUSTOM_POOL_PREFIX):
        raise SystemExit(f"未知提供者: {provider}")

    requested_type = str(getattr(args, "auth_type", "") or "").strip().lower()
    if requested_type in {AUTH_TYPE_API_KEY, "api-key"}:
        requested_type = AUTH_TYPE_API_KEY
    if not requested_type:
        if provider.startswith(CUSTOM_POOL_PREFIX):
            requested_type = AUTH_TYPE_API_KEY
        else:
            requested_type = AUTH_TYPE_OAUTH if provider in {"anthropic", "nous", "openai-codex", "qwen-oauth"} else AUTH_TYPE_API_KEY

    pool = load_pool(provider)

    if requested_type == AUTH_TYPE_API_KEY:
        token = (getattr(args, "api_key", None) or "").strip()
        if not token:
            token = getpass("粘贴您的 API 密钥: ").strip()
        if not token:
            raise SystemExit("未提供 API 密钥。")
        default_label = _api_key_default_label(len(pool.entries()) + 1)
        label = (getattr(args, "label", None) or "").strip()
        if not label:
            label = input(f"标签（可选，默认: {default_label}）: ").strip() or default_label
        entry = PooledCredential(
            provider=provider,
            id=uuid.uuid4().hex[:6],
            label=label,
            auth_type=AUTH_TYPE_API_KEY,
            priority=0,
            source=SOURCE_MANUAL,
            access_token=token,
            base_url=_provider_base_url(provider),
        )
        pool.add_entry(entry)
        print(f'已添加 {provider} 凭据 #{len(pool.entries())}: "{label}"')
        return

    if provider == "anthropic":
        from agent import anthropic_adapter as anthropic_mod

        creds = anthropic_mod.run_kclaw_oauth_login_pure()
        if not creds:
            raise SystemExit("Anthropic OAuth 登录未返回凭据。")
        label = (getattr(args, "label", None) or "").strip() or label_from_token(
            creds["access_token"],
            _oauth_default_label(provider, len(pool.entries()) + 1),
        )
        entry = PooledCredential(
            provider=provider,
            id=uuid.uuid4().hex[:6],
            label=label,
            auth_type=AUTH_TYPE_OAUTH,
            priority=0,
            source=f"{SOURCE_MANUAL}:kclaw_pkce",
            access_token=creds["access_token"],
            refresh_token=creds.get("refresh_token"),
            expires_at_ms=creds.get("expires_at_ms"),
            base_url=_provider_base_url(provider),
        )
        pool.add_entry(entry)
        print(f'已添加 {provider} OAuth 凭据 #{len(pool.entries())}: "{entry.label}"')
        return

    if provider == "nous":
        creds = auth_mod._nous_device_code_login(
            portal_base_url=getattr(args, "portal_url", None),
            inference_base_url=getattr(args, "inference_url", None),
            client_id=getattr(args, "client_id", None),
            scope=getattr(args, "scope", None),
            open_browser=not getattr(args, "no_browser", False),
            timeout_seconds=getattr(args, "timeout", None) or 15.0,
            insecure=bool(getattr(args, "insecure", False)),
            ca_bundle=getattr(args, "ca_bundle", None),
            min_key_ttl_seconds=max(60, int(getattr(args, "min_key_ttl_seconds", 5 * 60))),
        )
        label = (getattr(args, "label", None) or "").strip() or label_from_token(
            creds.get("access_token", ""),
            _oauth_default_label(provider, len(pool.entries()) + 1),
        )
        entry = PooledCredential.from_dict(provider, {
            **creds,
            "label": label,
            "auth_type": AUTH_TYPE_OAUTH,
            "source": f"{SOURCE_MANUAL}:device_code",
            "base_url": creds.get("inference_base_url"),
        })
        pool.add_entry(entry)
        print(f'已添加 {provider} OAuth 凭据 #{len(pool.entries())}: "{entry.label}"')
        return

    if provider == "openai-codex":
        creds = auth_mod._codex_device_code_login()
        label = (getattr(args, "label", None) or "").strip() or label_from_token(
            creds["tokens"]["access_token"],
            _oauth_default_label(provider, len(pool.entries()) + 1),
        )
        entry = PooledCredential(
            provider=provider,
            id=uuid.uuid4().hex[:6],
            label=label,
            auth_type=AUTH_TYPE_OAUTH,
            priority=0,
            source=f"{SOURCE_MANUAL}:device_code",
            access_token=creds["tokens"]["access_token"],
            refresh_token=creds["tokens"].get("refresh_token"),
            base_url=creds.get("base_url"),
            last_refresh=creds.get("last_refresh"),
        )
        pool.add_entry(entry)
        print(f'已添加 {provider} OAuth 凭据 #{len(pool.entries())}: "{entry.label}"')
        return

    if provider == "qwen-oauth":
        creds = auth_mod.resolve_qwen_runtime_credentials(refresh_if_expiring=False)
        label = (getattr(args, "label", None) or "").strip() or label_from_token(
            creds["api_key"],
            _oauth_default_label(provider, len(pool.entries()) + 1),
        )
        entry = PooledCredential(
            provider=provider,
            id=uuid.uuid4().hex[:6],
            label=label,
            auth_type=AUTH_TYPE_OAUTH,
            priority=0,
            source=f"{SOURCE_MANUAL}:qwen_cli",
            access_token=creds["api_key"],
            base_url=creds.get("base_url"),
        )
        pool.add_entry(entry)
        print(f'已添加 {provider} OAuth 凭据 #{len(pool.entries())}: "{entry.label}"')
        return

    raise SystemExit(f"`kclaw auth add {provider}` 尚未实现 auth type {requested_type}。")


def auth_list_command(args) -> None:
    provider_filter = _normalize_provider(getattr(args, "provider", "") or "")
    if provider_filter:
        providers = [provider_filter]
    else:
        providers = sorted({
            *PROVIDER_REGISTRY.keys(),
            "openrouter",
            *list_custom_pool_providers(),
        })
    for provider in providers:
        pool = load_pool(provider)
        entries = pool.entries()
        if not entries:
            continue
        current = pool.peek()
        print(f"{provider} ({len(entries)} 个凭据):")
        for idx, entry in enumerate(entries, start=1):
            marker = "  "
            if current is not None and entry.id == current.id:
                marker = "← "
            status = _format_exhausted_status(entry)
            source = _display_source(entry.source)
            print(f"  #{idx}  {entry.label:<20} {entry.auth_type:<7} {source}{status} {marker}".rstrip())
        print()


def auth_remove_command(args) -> None:
    provider = _normalize_provider(getattr(args, "provider", ""))
    target = getattr(args, "target", None)
    if target is None:
        target = getattr(args, "index", None)
    pool = load_pool(provider)
    index, matched, error = pool.resolve_target(target)
    if matched is None or index is None:
        raise SystemExit(f"{error} 提供者: {provider}.")
    removed = pool.remove_index(index)
    if removed is None:
        raise SystemExit(f'没有找到与 "{target}" 匹配的 {provider} 凭据。')
    print(f"已移除 {provider} 凭据 #{index} ({removed.label})")

    # If this was an env-seeded credential, also clear the env var from .env
    # so it doesn't get re-seeded on the next load_pool() call.
    if removed.source.startswith("env:"):
        env_var = removed.source[len("env:"):]
        if env_var:
            from kclaw_cli.config import remove_env_value
            cleared = remove_env_value(env_var)
            if cleared:
                print(f"已从 .env 中清除 {env_var}")

    # If this was a singleton-seeded credential (OAuth device_code, kclaw_pkce),
    # clear the underlying auth store / credential file so it doesn't get
    # re-seeded on the next load_pool() call.
    elif removed.source == "device_code" and provider in ("openai-codex", "nous"):
        from kclaw_cli.auth import (
            _load_auth_store, _save_auth_store, _auth_store_lock,
        )
        with _auth_store_lock():
            auth_store = _load_auth_store()
            providers_dict = auth_store.get("providers")
            if isinstance(providers_dict, dict) and provider in providers_dict:
                del providers_dict[provider]
                _save_auth_store(auth_store)
                print(f"已从认证存储中清除 {provider} OAuth 令牌")

    elif removed.source == "kclaw_pkce" and provider == "anthropic":
        from kclaw_constants import get_kclaw_home
        oauth_file = get_kclaw_home() / ".anthropic_oauth.json"
        if oauth_file.exists():
            oauth_file.unlink()
            print("已清除 KClaw Anthropic OAuth 凭据")

    elif removed.source == "claude_code" and provider == "anthropic":
        print("注意: Claude Code 凭据位于 ~/.claude/.credentials.json")
        print("      如果要取消 Claude Code 的授权，请手动删除。")


def auth_reset_command(args) -> None:
    provider = _normalize_provider(getattr(args, "provider", ""))
    pool = load_pool(provider)
    count = pool.reset_statuses()
    print(f"已重置 {count} 个 {provider} 凭据的状态")


def _interactive_auth() -> None:
    """当 `kclaw auth` 无参数调用时的交互式凭据池管理。"""
    # Show current pool status first
    print("凭据池状态")
    print("=" * 50)

    auth_list_command(SimpleNamespace(provider=None))
    print()

    # Main menu
    choices = [
        "添加凭据",
        "移除凭据",
        "重置提供者的冷却时间",
        "设置提供者的轮换策略",
        "退出",
    ]
    print("您想做什么?")
    for i, choice in enumerate(choices, 1):
        print(f"  {i}. {choice}")

    try:
        raw = input("\n选择: ").strip()
    except (EOFError, KeyboardInterrupt):
        return

    if not raw or raw == str(len(choices)):
        return

    if raw == "1":
        _interactive_add()
    elif raw == "2":
        _interactive_remove()
    elif raw == "3":
        _interactive_reset()
    elif raw == "4":
        _interactive_strategy()


def _pick_provider(prompt: str = "提供者") -> str:
    """提示输入提供者名称并带有自动补全提示。"""
    known = sorted(set(list(PROVIDER_REGISTRY.keys()) + ["openrouter"]))
    custom_names = _get_custom_provider_names()
    if custom_names:
        custom_display = [name for name, _key in custom_names]
        print(f"\n已知提供者: {', '.join(known)}")
        print(f"自定义端点: {', '.join(custom_display)}")
    else:
        print(f"\n已知提供者: {', '.join(known)}")
    try:
        raw = input(f"{prompt}: ").strip()
    except (EOFError, KeyboardInterrupt):
        raise SystemExit()
    return _normalize_provider(raw)


def _interactive_add() -> None:
    provider = _pick_provider("添加凭据的提供者")
    if provider not in PROVIDER_REGISTRY and provider != "openrouter" and not provider.startswith(CUSTOM_POOL_PREFIX):
        raise SystemExit(f"未知提供者: {provider}")

    # For OAuth-capable providers, ask which type
    if provider in _OAUTH_CAPABLE_PROVIDERS:
        print(f"\n{provider} 支持 API 密钥和 OAuth 登录。")
        print("  1. API 密钥（从提供商仪表板粘贴密钥）")
        print("  2. OAuth 登录（通过浏览器认证）")
        try:
            type_choice = input("类型 [1/2]: ").strip()
        except (EOFError, KeyboardInterrupt):
            return
        if type_choice == "2":
            auth_type = "oauth"
        else:
            auth_type = "api_key"
    else:
        auth_type = "api_key"

    label = None
    try:
        typed_label = input("标签 / 账户名称（可选）: ").strip()
    except (EOFError, KeyboardInterrupt):
        return
    if typed_label:
        label = typed_label

    auth_add_command(SimpleNamespace(
        provider=provider, auth_type=auth_type, label=label, api_key=None,
        portal_url=None, inference_url=None, client_id=None, scope=None,
        no_browser=False, timeout=None, insecure=False, ca_bundle=None,
    ))


def _interactive_remove() -> None:
    provider = _pick_provider("移除凭据的提供者")
    pool = load_pool(provider)
    if not pool.has_credentials():
        print(f"没有 {provider} 的凭据。")
        return

    # Show entries with indices
    for i, e in enumerate(pool.entries(), 1):
        exhausted = _format_exhausted_status(e)
        print(f"  #{i}  {e.label:25s} {e.auth_type:10s} {e.source}{exhausted} [id:{e.id}]")

    try:
        raw = input("输入编号、id 或标签（留空取消）: ").strip()
    except (EOFError, KeyboardInterrupt):
        return
    if not raw:
        return

    auth_remove_command(SimpleNamespace(provider=provider, target=raw))


def _interactive_reset() -> None:
    provider = _pick_provider("重置冷却时间的提供者")

    auth_reset_command(SimpleNamespace(provider=provider))


def _interactive_strategy() -> None:
    provider = _pick_provider("设置策略的提供者")
    current = get_pool_strategy(provider)
    strategies = [STRATEGY_FILL_FIRST, STRATEGY_ROUND_ROBIN, STRATEGY_LEAST_USED, STRATEGY_RANDOM]

    print(f"\n{provider} 当前策略: {current}")
    print()
    descriptions = {
        STRATEGY_FILL_FIRST: "使用第一个密钥直到耗尽，然后切换下一个",
        STRATEGY_ROUND_ROBIN: "均匀轮换所有密钥",
        STRATEGY_LEAST_USED: "始终选择使用次数最少的密钥",
        STRATEGY_RANDOM: "随机选择",
    }
    for i, s in enumerate(strategies, 1):
        marker = " ←" if s == current else ""
        print(f"  {i}. {s:15s} — {descriptions.get(s, '')}{marker}")

    try:
        raw = input("\n策略 [1-4]: ").strip()
    except (EOFError, KeyboardInterrupt):
        return
    if not raw:
        return

    try:
        idx = int(raw) - 1
        strategy = strategies[idx]
    except (ValueError, IndexError):
        print("无效的选择。")
        return

    from kclaw_cli.config import load_config, save_config
    cfg = load_config()
    pool_strategies = cfg.get("credential_pool_strategies") or {}
    if not isinstance(pool_strategies, dict):
        pool_strategies = {}
    pool_strategies[provider] = strategy
    cfg["credential_pool_strategies"] = pool_strategies
    save_config(cfg)
    print(f"已将 {provider} 策略设置为: {strategy}")


def auth_command(args) -> None:
    action = getattr(args, "auth_action", "")
    if action == "add":
        auth_add_command(args)
        return
    if action == "list":
        auth_list_command(args)
        return
    if action == "remove":
        auth_remove_command(args)
        return
    if action == "reset":
        auth_reset_command(args)
        return
    # No subcommand — launch interactive mode
    _interactive_auth()
