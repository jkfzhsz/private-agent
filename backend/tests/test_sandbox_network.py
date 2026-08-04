"""阶段二批次 3 - 沙箱网络隔离测试(审查 A.1.1/B.1.1 网络拦截部分)。

背景: disable_network 此前定义但从未接线(service.py 只做 sanitize),
且原实现设 NO_PROXY=* 与代理拦截自相矛盾(拦截完全失效)。本批次:
1) 修正 disable_network(移除 NO_PROXY, 代理指向本机无服务端口)
2) service.py 按 limits.network_enabled 接线(默认 false → 默认禁网)

覆盖:
- disable_network 纯函数: 无效代理注入 + NO_PROXY 移除(稳定, 无网络依赖)
- 集成禁网(默认): 沙箱内 requests 请求公网 → ProxyError(经无效代理失败)
- 集成放行(network_enabled=true): 无 ProxyError + 本地 server 可达
- EnvSanitizer 边界: .env 敏感键不可见(既有 security 测试扩展)
"""
from __future__ import annotations

import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from private_agent.sandbox.resource_limiter import disable_network
from private_agent.sandbox.service import SandboxService

_VENV_PY = Path(__file__).resolve().parents[1] / ".venv" / "Scripts" / "python.exe"


def _make_config(tmp_path: Path, network_enabled: bool | None = None) -> dict:
    limits = {"cpu_timeout_sec": 10, "memory_limit_mb": 512, "disk_limit_mb": 100}
    if network_enabled is not None:
        limits["network_enabled"] = network_enabled
    return {
        "sandbox": {
            "workspace_root": str(tmp_path),
            "languages": {
                "python": {"command": str(_VENV_PY), "script_extension": ".py"},
            },
            "limits": limits,
            "security": {
                "code_scan_enabled": True,
                "env_sanitization_enabled": True,
            },
            "output": {"stdout_artifact_threshold": 2000, "code_artifact_threshold": 4000},
        }
    }


# ── disable_network 纯函数(稳定) ──────────────────────────────────────────────


def test_disable_network_injects_bad_proxy():
    env = {"PATH": "/usr/bin", "HOME": "/home/u", "HTTP_PROXY": "http://real:8080"}
    out = disable_network(env)
    assert out["HTTP_PROXY"] == "http://127.0.0.1:9"
    assert out["HTTPS_PROXY"] == "http://127.0.0.1:9"
    assert out["http_proxy"] == "http://127.0.0.1:9"
    assert out["ALL_PROXY"] == "http://127.0.0.1:9"


def test_disable_network_removes_no_proxy():
    """NO_PROXY 必须移除——否则所有主机绕过代理, 拦截失效(修正点)。"""
    env = {"NO_PROXY": "*", "no_proxy": "example.com"}
    out = disable_network(env)
    assert "NO_PROXY" not in out
    assert "no_proxy" not in out


def test_disable_network_keeps_basic_vars():
    env = {"PATH": "/usr/bin", "LANG": "en_US"}
    out = disable_network(env)
    assert out["PATH"] == "/usr/bin"


# ── 集成: 沙箱内网络行为(真实子进程) ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_network_disabled_by_default(tmp_path: Path) -> None:
    """默认(config 无 network_enabled → false): requests 走无效代理 → ProxyError。"""
    config = _make_config(tmp_path)  # 未显式开启 network_enabled
    svc = SandboxService(config)
    result = await svc.execute(
        code=(
            "import httpx\n"
            "try:\n"
            "    httpx.get('http://example.com', timeout=5)\n"
            "    print('NET_OK')\n"
            "except Exception as e:\n"
            "    print('NET_BLOCKED:', type(e).__name__)\n"
        ),
        language="python",
        timeout=15,
        session_id="",
    )
    assert result.exit_code == 0, result.stderr
    # 走无效代理(http://127.0.0.1:9) → ProxyError / ConnectionError
    assert "NET_BLOCKED" in result.stdout
    assert "NET_OK" not in result.stdout


@pytest.mark.asyncio
async def test_network_enabled_no_proxy_block(tmp_path: Path) -> None:
    """network_enabled=true: 不再注入无效代理 → 无 ProxyError。"""
    config = _make_config(tmp_path, network_enabled=True)
    svc = SandboxService(config)
    result = await svc.execute(
        code=(
            "import httpx\n"
            "try:\n"
            "    r = httpx.get('http://example.com', timeout=8)\n"
            "    print('REACHED', r.status_code)\n"
            "except Exception as e:\n"
            "    print('NET_ERR:', type(e).__name__)\n"
        ),
        language="python",
        timeout=20,
        session_id="",
    )
    assert result.exit_code == 0, result.stderr
    assert "ProxyError" not in result.stdout


@pytest.mark.asyncio
async def test_network_enabled_local_server_reachable(tmp_path: Path) -> None:
    """network_enabled=true 且本地服务可达(不依赖外网)。"""

    class _H(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"local-ok")

        def log_message(self, *a):  # noqa: A003
            pass

    server = HTTPServer(("127.0.0.1", 0), _H)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        config = _make_config(tmp_path, network_enabled=True)
        svc = SandboxService(config)
        result = await svc.execute(
            code=(
                "import httpx\n"
                f"r = httpx.get('http://127.0.0.1:{port}/', timeout=5)\n"
                "print(r.text)\n"
            ),
            language="python",
            timeout=15,
            session_id="",
        )
        assert result.exit_code == 0, result.stderr
        assert "local-ok" in result.stdout
    finally:
        server.shutdown()


# ── EnvSanitizer 边界(既有 security 测试的端到端补充) ─────────────────────────


@pytest.mark.asyncio
async def test_sandbox_cannot_read_dotenv_keys(tmp_path: Path) -> None:
    """沙箱内环境变量不含敏感键(PA_DB_PASSWORD/PA_MASTER_KEY 等被脱敏)。"""
    config = _make_config(tmp_path)
    svc = SandboxService(config)
    result = await svc.execute(
        code=(
            "import os\n"
            "keys = [k for k in os.environ if 'PASSWORD' in k.upper() or 'MASTER_KEY' in k.upper()]\n"
            "print('SENSITIVE_KEYS:', keys)\n"
        ),
        language="python",
        timeout=15,
        session_id="",
    )
    assert result.exit_code == 0, result.stderr
    assert "SENSITIVE_KEYS: []" in result.stdout
