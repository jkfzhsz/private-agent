"""B5 P0-7 AC-1..7 - ResourceLimiter + disable_network 测试。

Source: plan/b5-sandbox-security step 4
"""
import sys
from unittest.mock import MagicMock, patch

from private_agent.sandbox.resource_limiter import ResourceLimiter, disable_network


def test_get_preexec_fn_returns_callable_on_posix():
    """AC-1: Linux 返回 callable。"""
    with patch("os.name", "posix"), patch.dict(sys.modules, {"resource": MagicMock()}):
        limiter = ResourceLimiter(memory_limit_mb=512, cpu_timeout_sec=300)
        fn = limiter.get_preexec_fn()
        assert callable(fn)


def test_get_preexec_fn_returns_none_on_windows():
    """AC-2: Windows 返回 None。"""
    with patch("os.name", "nt"):
        limiter = ResourceLimiter(memory_limit_mb=512, cpu_timeout_sec=300)
        assert limiter.get_preexec_fn() is None


def test_preexec_fn_sets_rlimit():
    """AC-3: preexec_fn 设置 RLIMIT_AS 和 RLIMIT_CPU。"""
    mock_resource = MagicMock()
    with patch("os.name", "posix"), patch.dict(sys.modules, {"resource": mock_resource}):
        limiter = ResourceLimiter(memory_limit_mb=256, cpu_timeout_sec=120)
        fn = limiter.get_preexec_fn()
        fn()
        assert mock_resource.setrlimit.call_count == 2
        mock_resource.setrlimit.assert_any_call(
            mock_resource.RLIMIT_AS, (256 * 1024 * 1024, 256 * 1024 * 1024)
        )


def test_sandbox_service_reads_memory_limit_mb():
    """AC-4: SandboxService 构造 ResourceLimiter。"""
    from private_agent.sandbox.service import SandboxService

    config = {
        "sandbox": {
            "workspace_root": ".sandbox",
            "limits": {
                "cpu_timeout_sec": 300,
                "memory_limit_mb": 512,
                "disk_limit_mb": 100,
            },
            "languages": {"python": {"command": "python"}},
            "security": {},
            "output": {},
        }
    }
    svc = SandboxService(config)
    assert hasattr(svc, "_resource_limiter")
    assert svc._resource_limiter.memory_limit == 512 * 1024 * 1024


def test_disable_network_sets_invalid_proxy():
    """AC-6: 设置无效代理。"""
    env = {"PATH": "/usr/bin", "HOME": "/home/user"}
    result = disable_network(env)
    assert result["HTTP_PROXY"] == "invalid"
    assert result["HTTPS_PROXY"] == "invalid"
    assert result["http_proxy"] == "invalid"
    assert result["https_proxy"] == "invalid"
    assert result["NO_PROXY"] == "*"


def test_disable_network_overwrites_existing_proxy():
    """AC-7: 覆盖已有代理。"""
    env = {"HTTP_PROXY": "http://real:8080"}
    result = disable_network(env)
    assert result["HTTP_PROXY"] == "invalid"