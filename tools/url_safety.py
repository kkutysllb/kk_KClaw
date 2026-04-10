"""URL 安全检查 — 阻止对私有/内部网络地址的请求。

防止 SSRF（服务器端请求伪造），即恶意提示或技能
可能诱骗代理获取内部资源，如云
元数据端点（169.254.169.254）、本地主机服务或私有
网络主机。

限制（已记录，在预检级别无法修复）：
  - DNS 重新绑定（TOCTOU）：攻击者控制的 DNS 服务器，TTL=0
    可以在检查时返回公共 IP，然后在实际
    连接时返回私有 IP。修复此问题需要连接级验证（例如
    Python 的 Champion 库或类似 Stripe's Smokescreen 的出口代理）。
  - vision_tools 中的基于重定向的绕过通过 httpx 事件
    钩子缓解，该钩子重新验证每个重定向目标。Web 工具使用第三方
    SDK（Firecrawl/Tavily），其中重定向处理在其服务器上。
"""

import ipaddress
import logging
import socket
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# 无论 IP 解析如何都应始终阻止的主机名
_BLOCKED_HOSTNAMES = frozenset({
    "metadata.google.internal",
    "metadata.goog",
})

# 100.64.0.0/10（CGNAT / 共享地址空间，RFC 6598）不被
# ipaddress.is_private 覆盖 — 它对 is_private 和 is_global 都返回 False。
# 必须明确阻止。由运营商级 NAT、Tailscale/WireGuard
# VPN 和某些云内部网络使用。
_CGNAT_NETWORK = ipaddress.ip_network("100.64.0.0/10")


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """如果 IP 应被阻止以进行 SSRF 保护则返回 True。"""
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
        return True
    if ip.is_multicast or ip.is_unspecified:
        return True
    # is_private 未覆盖的 CGNAT 范围
    if ip in _CGNAT_NETWORK:
        return True
    return False


def is_safe_url(url: str) -> bool:
    """如果 URL 目标不是私有/内部地址则返回 True。

    将主机名解析为 IP 并根据私有范围进行检查。
    失败关闭：DNS 错误和意外异常阻止请求。
    """
    try:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").strip().lower()
        if not hostname:
            return False

        # 阻止已知的内部主机名
        if hostname in _BLOCKED_HOSTNAMES:
            logger.warning("Blocked request to internal hostname: %s", hostname)
            return False

        # Try to resolve and check IP
        try:
            addr_info = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        except socket.gaierror:
            # DNS 解析失败 — 失败关闭。如果 DNS 无法解析它，
            # HTTP 客户端也将失败，因此阻止不会丢失任何东西。
            logger.warning("Blocked request — DNS resolution failed for: %s", hostname)
            return False

        for family, _, _, _, sockaddr in addr_info:
            ip_str = sockaddr[0]
            try:
                ip = ipaddress.ip_address(ip_str)
            except ValueError:
                continue

            if _is_blocked_ip(ip):
                logger.warning(
                    "Blocked request to private/internal address: %s -> %s",
                    hostname, ip_str,
                )
                return False

        return True

    except Exception as exc:
        # 对意外错误失败关闭 — 不要让解析边缘情况
        # 成为 SSRF 绕过向量
        logger.warning("Blocked request — URL safety check error for %s: %s", url, exc)
        return False
