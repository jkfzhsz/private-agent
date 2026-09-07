"""2026-09-07 S4: _session_end_memory_extract 触发守卫测试。

背景: on_session_end 此前全仓零调用(WS 断连路径未接线), 真实会话
平均 1.6~3.7 轮 vs 间隔提取每 8 轮 → 自动提取管线全场景空转
(生产全库仅触发 3 次、office 106 会话 0 记忆)。
接线后守卫: 距上次间隔提取的尾部轮次 ≥ 2 才提取。
"""
from __future__ import annotations

import asyncio

import pytest

import private_agent.main as main_mod


class _FakeConn:
    """模拟 asyncpg conn(fetchrow/fetchval 两个查询点)。"""

    def __init__(self, session_row: dict | None, max_turn: int) -> None:
        self._session_row = session_row
        self._max_turn = max_turn

    async def fetchrow(self, query: str, *args):
        return self._session_row

    async def fetchval(self, query: str, *args):
        return self._max_turn


class _SpyManager:
    """记录 on_session_end 调用的假 MemoryManager。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def on_session_end(self, **kwargs) -> list:
        self.calls.append(kwargs)
        return []


@pytest.fixture
def spy_mgr(monkeypatch: pytest.MonkeyPatch) -> _SpyManager:
    spy = _SpyManager()

    async def _fake_cfg():
        return {"memory": {"extract_interval_turns": 8}}

    monkeypatch.setattr(main_mod, "_load_cfg_with_runtime", _fake_cfg)
    monkeypatch.setattr(main_mod, "_build_memory_manager", lambda c, cfg: spy)
    return spy


def test_extracts_when_tail_ge2(spy_mgr: _SpyManager):
    """max_turn=10, interval=8 → 尾部 2 轮未提取 → 触发, scope 正确传递。"""
    conn = _FakeConn(
        {"memory_enabled": True, "locked_skill_name": "office"}, max_turn=10
    )
    asyncio.run(main_mod._session_end_memory_extract(conn, session_id=1))
    assert len(spy_mgr.calls) == 1
    call = spy_mgr.calls[0]
    assert call["session_id"] == 1
    assert call["current_turn"] == 10
    assert call["scope"] == "office"


def test_skips_when_tail_lt2(spy_mgr: _SpyManager):
    """max_turn=9 → 尾部 1 轮 → 跳过(几乎无新内容)。"""
    conn = _FakeConn(
        {"memory_enabled": True, "locked_skill_name": "office"}, max_turn=9
    )
    asyncio.run(main_mod._session_end_memory_extract(conn, session_id=1))
    assert spy_mgr.calls == []


def test_skips_when_just_extracted(spy_mgr: _SpyManager):
    """max_turn=16 → 刚在 16 轮间隔提取过(尾部 0) → 跳过。"""
    conn = _FakeConn(
        {"memory_enabled": True, "locked_skill_name": "office"}, max_turn=16
    )
    asyncio.run(main_mod._session_end_memory_extract(conn, session_id=1))
    assert spy_mgr.calls == []


def test_skips_one_turn_session(spy_mgr: _SpyManager):
    """max_turn=1(office 主体形态的 1 轮短会话) → 跳过, 控制 LLM 成本。"""
    conn = _FakeConn(
        {"memory_enabled": True, "locked_skill_name": "office"}, max_turn=1
    )
    asyncio.run(main_mod._session_end_memory_extract(conn, session_id=1))
    assert spy_mgr.calls == []


def test_skips_short_session_with_content(spy_mgr: _SpyManager):
    """max_turn=3(< interval, 尾部 3) → 触发(短会话的核心路径)。"""
    conn = _FakeConn(
        {"memory_enabled": True, "locked_skill_name": "frontend_design"},
        max_turn=3,
    )
    asyncio.run(main_mod._session_end_memory_extract(conn, session_id=2))
    assert len(spy_mgr.calls) == 1
    assert spy_mgr.calls[0]["scope"] == "frontend_design"


def test_skips_when_memory_disabled(spy_mgr: _SpyManager):
    """会话级记忆开关关闭 → 跳过(V1.1-3.5 语义保持)。"""
    conn = _FakeConn(
        {"memory_enabled": False, "locked_skill_name": "office"}, max_turn=10
    )
    asyncio.run(main_mod._session_end_memory_extract(conn, session_id=1))
    assert spy_mgr.calls == []


def test_skips_when_session_missing(spy_mgr: _SpyManager):
    """会话行不存在 → 静默跳过(防御)。"""
    conn = _FakeConn(None, max_turn=10)
    asyncio.run(main_mod._session_end_memory_extract(conn, session_id=999))
    assert spy_mgr.calls == []


def test_failure_is_silent(spy_mgr: _SpyManager, monkeypatch: pytest.MonkeyPatch):
    """提取内部异常 → 静默(记日志), 不冒泡打断连主流程。"""
    async def _boom():
        raise RuntimeError("cfg load failed")

    monkeypatch.setattr(main_mod, "_load_cfg_with_runtime", _boom)
    conn = _FakeConn(
        {"memory_enabled": True, "locked_skill_name": "office"}, max_turn=10
    )
    # 不抛异常即通过
    asyncio.run(main_mod._session_end_memory_extract(conn, session_id=1))
