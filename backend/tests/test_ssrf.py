"""阶段二批次 2 - SSRF 防护测试(审查 A.2.3/B.1.3)。

覆盖:
- 字面 IP 黑名单表驱动: 回环/私网/CGNAT/链路本地/云元数据/IPv6
- scheme 白名单: file/gopher/ftp/javascript 拒绝
- 多 A 记录逃逸: 任一解析 IP 命中黑名单即拒
- allow_private 显式放行
- SafeHttpxClient(MockTransport): 重定向每跳校验(302→内网拒)、响应体大小限制
- http_request 工具集成: 内网 URL 第一跳拒绝、enabled=false 绕过
"""
import socket

import httpx
import pytest

from private_agent.security.ssrf import (
    SSRFBlockedError,
    SafeHttpxClient,
    safe_httpx_client,
    validate_outbound_url,
)
from private_agent.tools.builtins.http_request import http_request_handler


# ── 单元: 字面 IP / localhost 黑名单(免 DNS) ─────────────────────────────────


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8765/admin/settings",
        "http://10.0.0.1/",
        "http://172.16.0.1/",
        "http://172.31.255.254/",
        "http://192.168.1.1/",
        "http://100.64.0.1/",                       # CGNAT
        "http://169.254.169.254/latest/meta-data/",  # 云元数据
        "http://0.0.0.0/",
        "http://[::1]:8080/",
        "http://[fc00::1]/",
        "http://[fe80::1]/",
    ],
)
def test_blocked_literal_ips(url):
    with pytest.raises(SSRFBlockedError):
        validate_outbound_url(url)


def test_blocked_localhost_hostname():
    with pytest.raises(SSRFBlockedError):
        validate_outbound_url("http://localhost:8765/")


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "gopher://127.0.0.1:70/",
        "ftp://example.com/",
        "javascript:alert(1)",
        "http://",  # 无 host
    ],
)
def test_blocked_schemes(url):
    with pytest.raises(SSRFBlockedError):
        validate_outbound_url(url)


# ── 单元: DNS 解析相关(monkeypatch 防真实网络) ────────────────────────────────

_PUBLIC_DNS = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 0))]
_MIXED_DNS = [
    (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
    (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0)),  # 任一内网 → 拒
]


def test_public_url_allowed(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _PUBLIC_DNS)
    result = validate_outbound_url("http://example.com/path?q=1")
    assert result.hostname == "example.com"
    assert "8.8.8.8" in result.resolved_ips


def test_multi_a_record_any_blocked(monkeypatch):
    """多 A 记录逃逸: 解析结果任一命中黑名单即拒绝。"""
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _MIXED_DNS)
    with pytest.raises(SSRFBlockedError):
        validate_outbound_url("http://mixed-record.test/")


def test_unresolvable_host_rejected(monkeypatch):
    def fake(*a, **k):
        raise socket.gaierror(-2, "Name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    with pytest.raises(SSRFBlockedError):
        validate_outbound_url("http://nonexistent-host.invalid/")


def test_allow_private_explicit():
    """allow_private=True 显式放行局域网场景(内网 NAS 等)。"""
    result = validate_outbound_url("http://192.168.1.10:5000/", allow_private=True)
    assert result.hostname == "192.168.1.10"
    result2 = validate_outbound_url("http://127.0.0.1:8765/", allow_private=True)
    assert result2.hostname == "127.0.0.1"


# ── SafeHttpxClient: 重定向每跳校验 / 大小限制(MockTransport 全本地) ─────────


def _mock_client(handler) -> SafeHttpxClient:
    return SafeHttpxClient(transport=httpx.MockTransport(handler))


async def test_redirect_to_internal_blocked():
    """第一跳公网 URL 302 重定向到内网 → 第二跳 validate 拒绝。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"Location": "http://127.0.0.1:9999/"})

    async with safe_httpx_client(
        transport=httpx.MockTransport(handler),
        allow_private=False,
    ) as client:
        with pytest.raises(SSRFBlockedError):
            await client.request("GET", "http://public.example/start")


async def test_redirect_chain_followed_when_public(monkeypatch):
    """重定向到公网目标时正常跟随(每跳校验通过)。"""
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _PUBLIC_DNS)
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if request.url.path == "/start":
            return httpx.Response(302, headers={"Location": "http://public.example/final"})
        return httpx.Response(200, text="ok")

    async with safe_httpx_client(transport=httpx.MockTransport(handler)) as client:
        resp = await client.request("GET", "http://public.example/start")
    assert resp.status_code == 200
    assert seen == ["http://public.example/start", "http://public.example/final"]


async def test_response_size_limit(monkeypatch):
    """响应体超过限制 → SSRFBlockedError(防拖垮 sidecar)。"""
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _PUBLIC_DNS)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="x" * 100)

    async with safe_httpx_client(
        transport=httpx.MockTransport(handler), max_response_bytes=50
    ) as client:
        resp = await client.request("GET", "http://public.example/")
        with pytest.raises(SSRFBlockedError):
            await client.limited_content(resp)


# ── http_request 工具集成 ─────────────────────────────────────────────────────


async def test_handler_blocks_internal_url():
    """工具层: 内网 URL 第一跳即拒绝, 返回 SSRF blocked 错误。"""
    result = await http_request_handler({"url": "http://127.0.0.1:8765/admin/settings"})
    assert result.error is not None
    assert "SSRF blocked" in result.error
    assert result.metadata.get("blocked") is True


async def test_handler_blocks_metadata_url():
    result = await http_request_handler(
        {"url": "http://169.254.169.254/latest/meta-data/"}
    )
    assert result.error is not None and "SSRF blocked" in result.error


async def test_handler_scheme_rejected():
    result = await http_request_handler({"url": "file:///etc/passwd"})
    assert result.error is not None and "SSRF blocked" in result.error


async def test_handler_disabled_ssrf_bypasses_validation():
    """enabled=false 显式绕过(不推荐, 仅兼容旧行为): 走原始请求, 不再报 SSRF。"""
    result = await http_request_handler(
        {"url": "http://127.0.0.1:1/", "_ssrf_config": {"enabled": False}}
    )
    # 127.0.0.1:1 无服务 → 连接类错误, 而非 SSRF blocked
    assert result.error is not None
    assert "SSRF blocked" not in (result.error or "")
