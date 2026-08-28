"""0.6.0 F1-5: install_preflight 预检单测(monkeypatch 网络/psutil, 不出网)。

验收(设计文档 F1-5/C 阶段①): 不可达域名 → 结构化拒绝 + 镜像建议;
内存不足 → 拒绝; probe=False 只做清单判定。
"""
import asyncio

import pytest

from private_agent.tools.builtins import install_preflight as ipf
from private_agent.tools.builtins.install_preflight import (
    build_install_preflight_tool,
    mirror_suggestion,
    preflight_check,
)


CFG = {
    "environment": {
        "network": {
            "blocked_domains": ["google.com", "huggingface.co", "raw.githubusercontent.com"],
            "reachable_domains": ["mirrors.aliyun.com", "registry.npmmirror.com"],
        },
        "hardware": {"mem_budget_mb": 500},
        "guard": True,
    }
}


def _run(coro):
    return asyncio.run(coro)


# ── mirror_suggestion ───────────────────────────────────────────────────

def test_mirror_suggestion_kinds():
    assert "mirrors.aliyun.com" in mirror_suggestion("pip")
    assert "npmmirror.com" in mirror_suggestion("npm")
    assert "hf-mirror.com" in mirror_suggestion("hf")
    assert "镜像" in mirror_suggestion("generic")


# ── preflight_check: 域名清单判定 ────────────────────────────────────────

def test_blocked_domain_rejected_without_probe():
    """清单内不可达域名 → 拒绝 + 镜像建议(零网络探测, probe=False)。"""
    r = _run(preflight_check(CFG, domains=["huggingface.co"], probe=False))
    assert r["ok"] is False
    assert r["domain_results"][0]["blocked"] is True
    assert r["domain_results"][0]["reachable"] is False
    assert any("hf-mirror.com" in s for s in r["suggestions"])


def test_subdomain_blocked_matched():
    """子域名命中: raw.githubusercontent.com 在 blocked 清单含 github.com 时……
    注意清单语义: 仅精确与 *.blocked 匹配 —— github.com 不拦 api.github.com 之外的
    pypi.org; raw.githubusercontent.com 显式在清单内。"""
    r = _run(preflight_check(CFG, domains=["raw.githubusercontent.com"], probe=False))
    assert r["ok"] is False


def test_reachable_known_domain_passes():
    """已验证可达清单内域名 → 免测直接 PASS。"""
    r = _run(preflight_check(CFG, domains=["mirrors.aliyun.com"], probe=False))
    assert r["ok"] is True
    assert r["domain_results"][0]["reachable"] is True


def test_unknown_domain_probe_success(monkeypatch):
    """清单外域名 → 动态探测(monkeypatch 探测函数, 不出网)。"""

    async def fake_probe(host):
        return True, None

    monkeypatch.setattr(ipf, "_probe_domain", fake_probe)
    r = _run(preflight_check(CFG, domains=["pypi.org"], probe=True))
    assert r["ok"] is True
    assert r["domain_results"][0]["reachable"] is True


def test_unknown_domain_probe_failure(monkeypatch):
    """探测失败(模拟境内不可达) → 拒绝 + 通用镜像建议。"""

    async def fake_probe(host):
        return False, "ConnectTimeout"

    monkeypatch.setattr(ipf, "_probe_domain", fake_probe)
    r = _run(preflight_check(CFG, domains=["files.pythonhosted.org"], probe=True))
    assert r["ok"] is False
    assert any("镜像" in s for s in r["suggestions"])


def test_mixed_domains_all_must_pass(monkeypatch):
    """混合域名: 任一 FAIL → 整体 REJECTED。"""

    async def fake_probe(host):
        return (host != "googleapis.com", None if host != "googleapis.com" else "ConnectTimeout")

    monkeypatch.setattr(ipf, "_probe_domain", fake_probe)
    r = _run(
        preflight_check(
            CFG, domains=["mirrors.aliyun.com", "googleapis.com"], probe=True
        )
    )
    assert r["ok"] is False


# ── preflight_check: 内存水位 ────────────────────────────────────────────

def test_mem_insufficient_rejected(monkeypatch):
    """可用内存 < 需求 → 拒绝 + 提请用户(0.5.1 裁决线语义)。"""

    class FakeVM:
        available = 100 * 1024 * 1024  # 100MB

    class FakePsutil:
        @staticmethod
        def virtual_memory():
            return FakeVM()

    import sys
    monkeypatch.setitem(sys.modules, "psutil", FakePsutil)
    r = _run(preflight_check(CFG, domains=["mirrors.aliyun.com"], mem_required_mb=500, probe=False))
    assert r["ok"] is False
    assert r["mem"]["budget_ok"] is False
    assert any("内存不足" in s for s in r["suggestions"])


def test_mem_sufficient_passes(monkeypatch):
    class FakeVM:
        available = 1900 * 1024 * 1024  # 1900MB

    class FakePsutil:
        @staticmethod
        def virtual_memory():
            return FakeVM()

    import sys
    monkeypatch.setitem(sys.modules, "psutil", FakePsutil)
    r = _run(preflight_check(CFG, domains=["mirrors.aliyun.com"], mem_required_mb=500, probe=False))
    assert r["ok"] is True
    assert r["mem"]["budget_ok"] is True


def test_mem_psutil_failure_does_not_block(monkeypatch):
    """psutil 不可用 → 放行不假拒(观测系统兜底, 不因观测故障阻塞业务)。"""
    import sys

    monkeypatch.setitem(sys.modules, "psutil", None)  # import psutil → ImportError
    r = _run(preflight_check(CFG, domains=["mirrors.aliyun.com"], mem_required_mb=500, probe=False))
    assert r["ok"] is True
    assert r["mem"]["avail"] is None


# ── 工具封装 ─────────────────────────────────────────────────────────────

def test_tool_handler_rejects_empty_domains():
    tool = build_install_preflight_tool(CFG)
    res = _run(tool.handler({}))
    assert res.error and "domains 必填" in res.error


def test_tool_handler_pass_metadata():
    """PASS 结果带 metadata.preflight(D 批 code_execution 守卫复核契约)。"""
    tool = build_install_preflight_tool(CFG)
    res = _run(tool.handler({"domains": ["mirrors.aliyun.com"]}))
    assert not res.error
    assert res.metadata["preflight"]["ok"] is True
    assert "PASS" in res.output


def test_tool_handler_rejected_output_has_suggestion(monkeypatch):
    async def fake_probe(host):
        return False, "ConnectTimeout"

    monkeypatch.setattr(ipf, "_probe_domain", fake_probe)
    tool = build_install_preflight_tool(CFG)
    res = _run(tool.handler({"domains": ["some-unreachable.example"]}))
    assert not res.error  # 预检工具本身执行成功, 结果是 REJECTED
    assert "REJECTED" in res.output
    assert "建议:" in res.output
