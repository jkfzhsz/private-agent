"""阶段二批次 2: SSRF 防护(审查 A.2.3/B.1.3)。

风险面: `http_request` 工具允许 LLM 请求任意 URL —— 未校验时可通过
127.0.0.1/私网/云元数据(169.254.169.254) 探测内网服务、窃取云实例凭证。

防护策略(纵深):
1. scheme 白名单: 仅 http/https(file/gopher/ftp 等一律拒绝)
2. 主机解析后全量校验: getaddrinfo 解析所有 A/AAAA 记录, 任一命中
   私网/回环/链路本地/保留 CIDR 即拒绝(防"多 A 记录逃逸")
3. 重定向每跳校验: 手动跟随, 每跳先 validate 再请求(防 302 → 127.0.0.1)
4. 响应体大小上限 + 超时(防拖垮 sidecar)

已知边界(DNS rebinding): 校验与连接是两次 DNS 解析, 攻击者可在间隙
切换解析结果。第一版采用"校验+日志告警"(记录解析 IP 集合), 严格
绑定连接 IP 的自定义 transport 列为后续增强(见 docs/security-model.md)。
"""
from __future__ import annotations

import ipaddress
import logging
import socket
import urllib.parse
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)

# 默认黑名单 CIDR(含 IPv6 与 v4-mapped; ipaddress 自动处理 ::ffff:x.x.x.x)
_DEFAULT_BLOCKED_NETWORKS: list[ipaddress._BaseNetwork] = [
    ipaddress.ip_network(n)
    for n in (
        "0.0.0.0/8",       # 本网络
        "10.0.0.0/8",      # RFC1918 私网
        "100.64.0.0/10",   # CGNAT
        "127.0.0.0/8",     # 回环
        "169.254.0.0/16",  # 链路本地(含云元数据 169.254.169.254)
        "172.16.0.0/12",   # RFC1918 私网
        "192.0.0.0/24",    # IETF 协议分配
        "192.0.2.0/24",    # TEST-NET-1
        "192.168.0.0/16",  # RFC1918 私网
        "198.18.0.0/15",   # 基准测试
        "198.51.100.0/24", # TEST-NET-2
        "203.0.113.0/24",  # TEST-NET-3
        "224.0.0.0/4",     # 组播
        "240.0.0.0/4",     # 保留
        "255.255.255.255/32",
        "::/128",
        "::1/128",         # IPv6 回环
        "fc00::/7",        # IPv6 唯一本地
        "fe80::/10",       # IPv6 链路本地
        "ff00::/8",        # IPv6 组播
    )
]


class SSRFBlockedError(ValueError):
    """目标 URL 命中 SSRF 黑名单(内网/回环/保留地址/scheme 非法)。"""


@dataclass(frozen=True)
class UrlCheckResult:
    """URL 校验结果(含解析到的 IP 集合, 供日志/审计)。"""
    url: str
    hostname: str
    resolved_ips: tuple[str, ...]
    scheme: str


def _in_blocked_networks(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return any(ip in net for net in _DEFAULT_BLOCKED_NETWORKS)


def _resolve_hostname(hostname: str) -> list[str]:
    """解析主机名的全部 IP(v4+v6), 失败返回空列表(由上层决定拒绝)。"""
    try:
        infos = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return []
    ips: list[str] = []
    for info in infos:
        ip_str = info[4][0]
        if ip_str not in ips:
            ips.append(ip_str)
    return ips


def validate_outbound_url(url: str, allow_private: bool = False) -> UrlCheckResult:
    """校验出网 URL, 通过则返回校验结果; 命中黑名单抛 SSRFBlockedError。

    Args:
        url: 目标 URL(http/https)。
        allow_private: True 时放行私网/回环/链路本地(局域网场景, 如内网 NAS)。

    Raises:
        SSRFBlockedError: scheme 非法 / 主机解析失败 / 任一解析 IP 命中黑名单。
    """
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise SSRFBlockedError(
            f"scheme '{parsed.scheme}' not allowed (http/https only)"
        )
    hostname = parsed.hostname
    if not hostname:
        raise SSRFBlockedError(f"invalid url: no hostname in '{url}'")
    if not allow_private and _is_private_hostname(hostname):
        raise SSRFBlockedError(
            f"hostname '{hostname}' is a private/literal address"
        )
    ips = _resolve_hostname(hostname)
    if not ips:
        raise SSRFBlockedError(f"cannot resolve hostname '{hostname}'")
    if not allow_private:
        for ip_str in ips:
            try:
                ip = ipaddress.ip_address(ip_str)
            except ValueError:
                continue
            if _in_blocked_networks(ip):
                raise SSRFBlockedError(
                    f"resolved IP {ip_str} of '{hostname}' is in blocked range"
                )
    logger.debug("ssrf ok: %s -> %s", url, ",".join(ips))
    return UrlCheckResult(
        url=url, hostname=hostname, resolved_ips=tuple(ips), scheme=parsed.scheme
    )


def _is_private_hostname(hostname: str) -> bool:
    """主机名本身是字面 IP 或 localhost 时直接拒绝(免解析)。"""
    lowered = hostname.lower().rstrip(".")
    if lowered in ("localhost",):
        return True
    try:
        ip = ipaddress.ip_address(lowered)
    except ValueError:
        return False
    # 字面 IP 直接按地址类型判断(回环/私网/链路本地/保留/组播)
    return not ip.is_global


def safe_httpx_client(
    max_response_bytes: int = 2 * 1024 * 1024,
    timeout: float = 30.0,
    allow_private: bool = False,
    max_redirects: int = 5,
    transport: httpx.AsyncBaseTransport | None = None,
) -> "SafeHttpxClient":
    """构造 SSRF 防护的 httpx 客户端(重定向每跳校验 + 响应体大小限制)。

    transport: 测试可注入 MockTransport(不注入时走真实网络)。
    """
    return SafeHttpxClient(
        max_response_bytes=max_response_bytes,
        timeout=timeout,
        allow_private=allow_private,
        max_redirects=max_redirects,
        transport=transport,
    )


class SafeHttpxClient:
    """SSRF 防护 HTTP 客户端。

    - follow_redirects=False, 手动跟随并在每跳前 validate(防重定向逃逸)
    - 响应体累计读取, 超过 max_response_bytes 即终止(防拖垮)
    - 超时: connect/read/write/pool 统一 timeout
    """

    def __init__(
        self,
        max_response_bytes: int = 2 * 1024 * 1024,
        timeout: float = 30.0,
        allow_private: bool = False,
        max_redirects: int = 5,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._max_bytes = max_response_bytes
        self._allow_private = allow_private
        self._max_redirects = max_redirects
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            follow_redirects=False,
            trust_env=False,  # 不读系统代理(阶段一教训: Windows 系统代理会劫持)
            transport=transport,  # 测试可注入 MockTransport
        )

    async def __aenter__(self) -> "SafeHttpxClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self._client.aclose()

    async def request(
        self, method: str, url: str, **kwargs
    ) -> httpx.Response:
        """带 SSRF 校验的请求(每跳校验 + 响应体大小限制)。"""
        current = url
        for _ in range(self._max_redirects + 1):
            validate_outbound_url(current, allow_private=self._allow_private)
            resp = await self._client.request(method, current, **kwargs)
            if resp.is_redirect and resp.headers.get("location"):
                location = resp.headers["location"]
                next_url = urllib.parse.urljoin(str(resp.url), location)
                # 关闭已读 body, 释放连接
                await resp.aclose()
                if next_url == current:
                    return resp
                current = next_url
                continue
            return resp
        raise SSRFBlockedError(f"too many redirects (> {self._max_redirects})")

    async def limited_content(self, resp: httpx.Response) -> bytes:
        """流式读取响应体, 超过 max_response_bytes 截断并抛错。"""
        chunks: list[bytes] = []
        total = 0
        async for chunk in resp.aiter_bytes():
            total += len(chunk)
            if total > self._max_bytes:
                await resp.aclose()
                raise SSRFBlockedError(
                    f"response exceeds {self._max_bytes} bytes limit"
                )
            chunks.append(chunk)
        return b"".join(chunks)
