from __future__ import annotations

import ipaddress
import logging
import socket
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

_BLOCKED_HOSTNAMES = frozenset({"metadata.google.internal", "metadata.goog"})
_ALWAYS_BLOCKED_IPS = frozenset(
    {
        ipaddress.ip_address("169.254.169.254"),
        ipaddress.ip_address("169.254.170.2"),
        ipaddress.ip_address("169.254.169.253"),
        ipaddress.ip_address("fd00:ec2::254"),
        ipaddress.ip_address("100.100.100.200"),
    }
)
_ALWAYS_BLOCKED_NETWORKS = (ipaddress.ip_network("169.254.0.0/16"),)
_CGNAT_NETWORK = ipaddress.ip_network("100.64.0.0/10")


def safe_url_for_log(url: str, max_len: int = 80) -> str:
    text = str(url or "").replace("\n", "").replace("\r", "")
    return text[:max_len] + ("..." if len(text) > max_len else "")


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
        return True
    if ip.is_multicast or ip.is_unspecified:
        return True
    if ip in _CGNAT_NETWORK:
        return True
    return False


def is_safe_url(url: str) -> bool:
    """Fail-closed SSRF guard for remote media downloads."""

    try:
        parsed = urlparse(url)
        hostname = (parsed.hostname or "").strip().lower().rstrip(".")
        scheme = (parsed.scheme or "").strip().lower()
        if scheme not in {"http", "https"} or not hostname:
            return False
        if hostname in _BLOCKED_HOSTNAMES:
            logger.warning("Blocked request to internal hostname: %s", hostname)
            return False

        try:
            addr_info = socket.getaddrinfo(
                hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM
            )
        except socket.gaierror:
            logger.warning("Blocked request because DNS failed for: %s", hostname)
            return False

        for _family, _type, _proto, _canonname, sockaddr in addr_info:
            try:
                ip = ipaddress.ip_address(sockaddr[0])
            except ValueError:
                continue
            if ip in _ALWAYS_BLOCKED_IPS or any(
                ip in net for net in _ALWAYS_BLOCKED_NETWORKS
            ):
                logger.warning("Blocked request to metadata address: %s -> %s", hostname, ip)
                return False
            if _is_blocked_ip(ip):
                logger.warning(
                    "Blocked request to private/internal address: %s -> %s",
                    hostname,
                    ip,
                )
                return False
        return True
    except Exception as exc:
        logger.warning("Blocked request due to URL safety error for %s: %s", url, exc)
        return False

