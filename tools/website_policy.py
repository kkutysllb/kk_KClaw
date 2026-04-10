"""网站访问策略辅助函数,用于 URL 功能的工具。

本模块从 ~/.kclaw/config.yaml 和可选的共享列表文件加载用户管理的网站黑名单。
它故意设计得很轻量,以便 web/浏览器工具可以强制执行 URL 策略,
而无需引入更重的 CLI 配置堆栈。

策略在内存中以较短的 TTL 缓存,以便配置更改能够快速生效,
而无需在每次 URL 检查时重新读取文件。
"""

from __future__ import annotations

import fnmatch
import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from kclaw_constants import get_kclaw_home

logger = logging.getLogger(__name__)

_DEFAULT_WEBSITE_BLOCKLIST = {
    "enabled": False,
    "domains": [],
    "shared_files": [],
}

# 缓存: 解析后的策略 + 时间戳。避免在每次 URL 检查时重新读取 config.yaml
# (一个 50 页的 web_crawl 否则意味着 51 次 YAML 解析)。
_CACHE_TTL_SECONDS = 30.0
_cache_lock = threading.Lock()
_cached_policy: Optional[Dict[str, Any]] = None
_cached_policy_path: Optional[str] = None
_cached_policy_time: float = 0.0


def _get_default_config_path() -> Path:
    return get_kclaw_home() / "config.yaml"


class WebsitePolicyError(Exception):
    """当网站策略文件格式错误时引发。"""


def _normalize_host(host: str) -> str:
    return (host or "").strip().lower().rstrip(".")


def _normalize_rule(rule: Any) -> Optional[str]:
    if not isinstance(rule, str):
        return None
    value = rule.strip().lower()
    if not value or value.startswith("#"):
        return None
    if "://" in value:
        parsed = urlparse(value)
        value = parsed.netloc or parsed.path
    value = value.split("/", 1)[0].strip().rstrip(".")
    if value.startswith("www."):
        value = value[4:]
    return value or None


def _iter_blocklist_file_rules(path: Path) -> List[str]:
    """从共享黑名单文件加载规则。

    缺失或不可读取的文件会记录警告并返回空列表,
    而不是抛出异常 — 错误的文件路径不应该禁用所有 web 工具。
    """
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning("Shared blocklist file not found (skipping): %s", path)
        return []
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("Failed to read shared blocklist file %s (skipping): %s", path, exc)
        return []

    rules: List[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        normalized = _normalize_rule(stripped)
        if normalized:
            rules.append(normalized)
    return rules


def _load_policy_config(config_path: Optional[Path] = None) -> Dict[str, Any]:
    config_path = config_path or _get_default_config_path()
    if not config_path.exists():
        return dict(_DEFAULT_WEBSITE_BLOCKLIST)

    try:
        import yaml
    except ImportError:
        logger.debug("PyYAML not installed — website blocklist disabled")
        return dict(_DEFAULT_WEBSITE_BLOCKLIST)

    try:
        with open(config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f) or {}
    except yaml.YAMLError as exc:
        raise WebsitePolicyError(f"Invalid config YAML at {config_path}: {exc}") from exc
    except OSError as exc:
        raise WebsitePolicyError(f"Failed to read config file {config_path}: {exc}") from exc
    if not isinstance(config, dict):
        raise WebsitePolicyError("config root must be a mapping")

    security = config.get("security", {})
    if security is None:
        security = {}
    if not isinstance(security, dict):
        raise WebsitePolicyError("security must be a mapping")

    website_blocklist = security.get("website_blocklist", {})
    if website_blocklist is None:
        website_blocklist = {}
    if not isinstance(website_blocklist, dict):
        raise WebsitePolicyError("security.website_blocklist must be a mapping")

    policy = dict(_DEFAULT_WEBSITE_BLOCKLIST)
    policy.update(website_blocklist)
    return policy


def load_website_blocklist(config_path: Optional[Path] = None) -> Dict[str, Any]:
    """加载并返回解析后的网站黑名单策略。

    结果会缓存 ``_CACHE_TTL_SECONDS`` 秒,以避免在每次 URL 检查时重新读取
    config.yaml。传递显式的 ``config_path`` 以绕过缓存 (由测试使用)。
    """
    global _cached_policy, _cached_policy_path, _cached_policy_time

    resolved_path = str(config_path) if config_path else "__default__"
    now = time.monotonic()

    # 如果缓存仍然有效且路径相同,则返回缓存的策略
    if config_path is None:
        with _cache_lock:
            if (
                _cached_policy is not None
                and _cached_policy_path == resolved_path
                and (now - _cached_policy_time) < _CACHE_TTL_SECONDS
            ):
                return _cached_policy

    config_path = config_path or _get_default_config_path()
    policy = _load_policy_config(config_path)

    raw_domains = policy.get("domains", []) or []
    if not isinstance(raw_domains, list):
        raise WebsitePolicyError("security.website_blocklist.domains must be a list")

    raw_shared_files = policy.get("shared_files", []) or []
    if not isinstance(raw_shared_files, list):
        raise WebsitePolicyError("security.website_blocklist.shared_files must be a list")

    enabled = policy.get("enabled", True)
    if not isinstance(enabled, bool):
        raise WebsitePolicyError("security.website_blocklist.enabled must be a boolean")

    rules: List[Dict[str, str]] = []
    seen: set[Tuple[str, str]] = set()

    for raw_rule in raw_domains:
        normalized = _normalize_rule(raw_rule)
        if normalized and ("config", normalized) not in seen:
            rules.append({"pattern": normalized, "source": "config"})
            seen.add(("config", normalized))

    for shared_file in raw_shared_files:
        if not isinstance(shared_file, str) or not shared_file.strip():
            continue
        path = Path(shared_file).expanduser()
        if not path.is_absolute():
            path = (get_kclaw_home() / path).resolve()
        for normalized in _iter_blocklist_file_rules(path):
            key = (str(path), normalized)
            if key in seen:
                continue
            rules.append({"pattern": normalized, "source": str(path)})
            seen.add(key)

    result = {"enabled": enabled, "rules": rules}

    # 缓存结果 (仅针对默认路径 — 显式路径是测试)
    if config_path == _get_default_config_path():
        with _cache_lock:
            _cached_policy = result
            _cached_policy_path = "__default__"
            _cached_policy_time = now

    return result


def invalidate_cache() -> None:
    """强制下一次 ``check_website_access`` 调用重新读取配置。"""
    global _cached_policy
    with _cache_lock:
        _cached_policy = None


def _match_host_against_rule(host: str, pattern: str) -> bool:
    if not host or not pattern:
        return False
    if pattern.startswith("*."):
        return fnmatch.fnmatch(host, pattern)
    return host == pattern or host.endswith(f".{pattern}")


def _extract_host_from_urlish(url: str) -> str:
    parsed = urlparse(url)
    host = _normalize_host(parsed.hostname or parsed.netloc)
    if host:
        return host

    if "://" not in url:
        schemeless = urlparse(f"//{url}")
        host = _normalize_host(schemeless.hostname or schemeless.netloc)
        if host:
            return host

    return ""


def check_website_access(url: str, config_path: Optional[Path] = None) -> Optional[Dict[str, str]]:
    """检查 URL 是否被网站黑名单策略允许。

    如果允许访问则返回 ``None``,如果被阻止则返回包含阻止元数据的字典
    (``host``, ``rule``, ``source``, ``message``)。

    策略错误时从不抛出异常 — 记录警告并返回 ``None``
    (fail-open),以便配置拼写错误不会破坏所有 web 工具。
    传递显式的 ``config_path`` (测试) 以获得严格的错误传播。
    """
    # 快速路径: 如果没有显式 config_path 且缓存的策略被禁用
    # 或为空,则跳过所有工作 (无需 YAML 读取,无需主机提取)。
    if config_path is None:
        with _cache_lock:
            if _cached_policy is not None and not _cached_policy.get("enabled"):
                return None

    host = _extract_host_from_urlish(url)
    if not host:
        return None

    try:
        policy = load_website_blocklist(config_path)
    except WebsitePolicyError as exc:
        if config_path is not None:
            raise  # Tests pass explicit paths — let errors propagate
        logger.warning("Website policy config error (failing open): %s", exc)
        return None
    except Exception as exc:
        logger.warning("Unexpected error loading website policy (failing open): %s", exc)
        return None

    if not policy.get("enabled"):
        return None

    for rule in policy.get("rules", []):
        pattern = rule.get("pattern", "")
        if _match_host_against_rule(host, pattern):
            logger.info("Blocked URL %s — matched rule '%s' from %s",
                        url, pattern, rule.get("source", "config"))
            return {
                "url": url,
                "host": host,
                "rule": pattern,
                "source": rule.get("source", "config"),
                "message": (
                    f"Blocked by website policy: '{host}' matched rule '{pattern}'"
                    f" from {rule.get('source', 'config')}"
                ),
            }
    return None
