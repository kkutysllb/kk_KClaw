"""MCP 扩展包的 OSV 恶意软件检查。

在通过 npx/uvx 启动 MCP 服务器之前，查询 OSV（开源漏洞）
API 以检查包是否有已知的恶意软件 advisories（MAL-* ID）。
常规 CVE 被忽略 — 仅阻止已确认的恶意软件。

该 API 是免费的、公开的，由 Google 维护。典型延迟约为 300ms。
故障开放：网络错误允许包继续。

灵感来自 Block/goose 的扩展恶意软件检查。
"""

import json
import logging
import os
import re
import urllib.request
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

_OSV_ENDPOINT = os.getenv("OSV_ENDPOINT", "https://api.osv.dev/v1/query")
_TIMEOUT = 10  # seconds


def check_package_for_malware(
    command: str, args: list
) -> Optional[str]:
    """检查 MCP 服务器包是否有已知的恶意软件 advisories。

    检查 *command*（例如 ``npx``、``uvx``）和 *args* 以推断
    包名称和生态系统。查询 OSV API 以获取 MAL-* advisories。

    返回:
        如果发现恶意软件则返回错误消息字符串，如果干净/未知则返回 None。
        在网络错误或无法识别的命令时返回 None（允许）。
    """
    ecosystem = _infer_ecosystem(command)
    if not ecosystem:
        return None  # 不是 npx/uvx — 跳过

    package, version = _parse_package_from_args(args, ecosystem)
    if not package:
        return None

    try:
        malware = _query_osv(package, ecosystem, version)
    except Exception as exc:
        # 故障开放：网络错误、超时、解析失败 → 允许
        logger.debug("OSV 检查失败（允许）%s/%s: %s", ecosystem, package, exc)
        return None

    if malware:
        ids = ", ".join(m["id"] for m in malware[:3])
        summaries = "; ".join(
            m.get("summary", m["id"])[:100] for m in malware[:3]
        )
        return (
            f"BLOCKED: Package '{package}' ({ecosystem}) has known malware "
            f"advisories: {ids}. Details: {summaries}"
        )
    return None


def _infer_ecosystem(command: str) -> Optional[str]:
    """从命令名称推断包生态系统。"""
    base = os.path.basename(command).lower()
    if base in ("npx", "npx.cmd"):
        return "npm"
    if base in ("uvx", "uvx.cmd", "pipx"):
        return "PyPI"
    return None


def _parse_package_from_args(
    args: list, ecosystem: str
) -> Tuple[Optional[str], Optional[str]]:
    """从命令参数中提取包名称和可选版本。

    返回 (package_name, version) 或如果无法解析则返回 (None, None)。
    """
    if not args:
        return None, None

    # 跳过标志以找到包标记
    package_token = None
    for arg in args:
        if not isinstance(arg, str):
            continue
        if arg.startswith("-"):
            continue
        package_token = arg
        break

    if not package_token:
        return None, None

    if ecosystem == "npm":
        return _parse_npm_package(package_token)
    elif ecosystem == "PyPI":
        return _parse_pypi_package(package_token)
    return package_token, None


def _parse_npm_package(token: str) -> Tuple[Optional[str], Optional[str]]:
    """解析 npm 包：@scope/name@version 或 name@version。"""
    if token.startswith("@"):
        # 作用域包：@scope/name@version
        match = re.match(r"^(@[^/]+/[^@]+)(?:@(.+))?$", token)
        if match:
            return match.group(1), match.group(2)
        return token, None
    # 无作用域包：name@version
    if "@" in token:
        parts = token.rsplit("@", 1)
        name = parts[0]
        version = parts[1] if len(parts) > 1 and parts[1] != "latest" else None
        return name, version
    return token, None


def _parse_pypi_package(token: str) -> Tuple[Optional[str], Optional[str]]:
    """解析 PyPI 包：name==version 或 name[extras]==version。"""
    # 剥离额外内容：name[extra1,extra2]==version
    match = re.match(r"^([a-zA-Z0-9._-]+)(?:\[[^\]]*\])?(?:==(.+))?$", token)
    if match:
        return match.group(1), match.group(2)
    return token, None


def _query_osv(
    package: str, ecosystem: str, version: Optional[str] = None
) -> list:
    """查询 OSV API 以获取 MAL-* advisories。返回恶意软件漏洞列表。"""
    payload = {"package": {"name": package, "ecosystem": ecosystem}}
    if version:
        payload["version"] = version

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        _OSV_ENDPOINT,
        data=data,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "kclaw-osv-check/1.0",
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        result = json.loads(resp.read())

    vulns = result.get("vulns", [])
    # 仅恶意软件 advisories — 忽略常规 CVE
    return [v for v in vulns if v.get("id", "").startswith("MAL-")]
