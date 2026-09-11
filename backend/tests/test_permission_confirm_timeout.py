"""main._permission_confirm_timeout 三优先级测试(session-78067 反馈#3)。

2026-09-11: 确认弹窗超时原硬编码 60s, 用户漏看/离开时 fail-closed 拒绝
导致任务失败。改为: PA_CONFIRM_TIMEOUT env > config permission.
confirm_timeout_sec > 默认 300s。

本测试验证:
- 默认值 300(无 env、config 无该项)
- env 覆盖(PA_CONFIRM_TIMEOUT)
- config 覆盖(monkeypatch load_config)
- 非法 env 忽略 → 回退 config/默认
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """确保测试不受外部 PA_CONFIRM_TIMEOUT 污染。"""
    monkeypatch.delenv("PA_CONFIRM_TIMEOUT", raising=False)


def _import_fn():
    from private_agent.main import _permission_confirm_timeout
    return _permission_confirm_timeout


def test_default_300_when_no_env_no_config(monkeypatch):
    """无 env、config 无 confirm_timeout_sec → 默认 300。"""
    import private_agent.config.loader as loader

    monkeypatch.setattr(loader, "load_config", lambda *a, **k: {"permission": {}})
    fn = _import_fn()
    assert fn() == 300.0


def test_env_overrides(monkeypatch):
    """PA_CONFIRM_TIMEOUT=120 → 120(优先于 config)。"""
    import private_agent.config.loader as loader

    monkeypatch.setenv("PA_CONFIRM_TIMEOUT", "120")
    monkeypatch.setattr(
        loader, "load_config",
        lambda *a, **k: {"permission": {"confirm_timeout_sec": 999}},
    )
    fn = _import_fn()
    assert fn() == 120.0


def test_config_used_when_no_env(monkeypatch):
    """无 env, config 有 confirm_timeout_sec=180 → 180。"""
    import private_agent.config.loader as loader

    monkeypatch.setattr(
        loader, "load_config",
        lambda *a, **k: {"permission": {"confirm_timeout_sec": 180}},
    )
    fn = _import_fn()
    assert fn() == 180.0


def test_invalid_env_falls_back_to_config(monkeypatch):
    """PA_CONFIRM_TIMEOUT 非数字 → 忽略, 回退 config。"""
    import private_agent.config.loader as loader

    monkeypatch.setenv("PA_CONFIRM_TIMEOUT", "not-a-number")
    monkeypatch.setattr(
        loader, "load_config",
        lambda *a, **k: {"permission": {"confirm_timeout_sec": 240}},
    )
    fn = _import_fn()
    assert fn() == 240.0


def test_load_config_error_falls_back_to_default(monkeypatch):
    """load_config 抛异常 → 默认 300。"""
    import private_agent.config.loader as loader

    def _boom(*a, **k):
        raise RuntimeError("config broken")

    monkeypatch.setattr(loader, "load_config", _boom)
    fn = _import_fn()
    assert fn() == 300.0
