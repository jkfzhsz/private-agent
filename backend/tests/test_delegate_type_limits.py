"""2026-08-13 类型感知限流: delegate_subtask 同轮去重 + 进程级并发测试。

覆盖(方案 §6): 同轮同类型去重(拒绝不建行) / 混合类型通过 / 显式 type 覆盖 /
进程级并发等待超时拒绝。
"""
import asyncio

import pytest

from private_agent.core.subagent import SubagentTypeRegistry
from private_agent.tools.builtins import delegate_subtask as ds


class _Conn:
    """mock conn: kind=main, 建行返回递增 id, 聚合返回空。"""

    def __init__(self):
        self._next = 100
        self.inserts = 0

    async def fetchval(self, q, *a):
        # 注意: 建行 SQL 含 "model_id" 列名, 必须先判断 INSERT(否则误返模型名)
        if "INSERT INTO subagents" in q:
            self.inserts += 1
            self._next += 1
            return self._next
        if "kind" in q:
            return "main"
        if "MAX(turn)" in q:
            return 1
        if "model_id" in q:
            return "deepseek-flash"
        return None

    async def fetch(self, q, *a):
        return []

    async def execute(self, q, *a):
        return "UPDATE 0"


def _mk_subtasks(*items):
    """items: (prompt, type_or_None) 或纯 prompt 字符串。"""
    out = []
    for i, item in enumerate(items):
        if isinstance(item, tuple):
            prompt, typ = item
            entry = {"id": f"t{i}", "prompt": prompt}
            if typ:
                entry["type"] = typ
            out.append(entry)
        else:
            out.append({"id": f"t{i}", "prompt": item})
    return out


@pytest.fixture
def reg(monkeypatch):
    """每个测试独立 registry, 避免进程级单例跨测试污染。"""
    r = SubagentTypeRegistry()
    monkeypatch.setattr(ds, "subagent_type_registry", r)
    return r


def _run(monkeypatch, conn, subtasks, sc=None):
    """调用 _delegate_handler(mock watchdog/runner, 聚焦类型逻辑)。"""
    async def _fake_watchdog(**kw):
        return None

    class _FakeRunner:
        def __init__(self, **kw):
            pass

        async def run(self):
            return 0

    monkeypatch.setattr(ds, "_watchdog_wait", _fake_watchdog)
    monkeypatch.setattr(ds, "SubagentRunner", _FakeRunner)
    sc = sc or {
        "max_parallel": 3,
        "cancel_wait_sec": 5,
        "same_type_max": 1,
        "type_wait_timeout_sec": 0.2,
    }

    async def _run():
        return await ds._delegate_handler(
            conn=conn, cfg={}, session_id=1,
            event_sink=lambda ev: asyncio.sleep(0),
            tools=[], args={"subtasks": subtasks}, sc=sc,
            system_prompt_factory=lambda c, sid: "sp",
            adapter_factory=lambda m: None,
            compress_adapter=None,
        )

    return asyncio.run(_run())


def test_same_type_dedup_rejects(monkeypatch, reg):
    """同轮 3 个 search 子任务 → 拒绝(不建行)。"""
    conn = _Conn()
    subtasks = _mk_subtasks(
        ("搜索苏州市水网项目", None),
        ("搜索苏州市电网项目", None),
        ("搜索苏州市管网项目", None),
    )
    res = _run(monkeypatch, conn, subtasks)
    assert res.error and "同类子任务已含 search" in res.error
    assert conn.inserts == 0, "去重拒绝不应建行"


def test_mixed_types_pass(monkeypatch, reg):
    """search + analysis + code 混合 → 通过(互不冲突)。"""
    conn = _Conn()
    subtasks = _mk_subtasks(
        ("搜索苏州项目清单", None),
        ("分析销售数据统计特征", None),
        ("编写脚本处理文件", None),
    )
    res = _run(monkeypatch, conn, subtasks)
    assert res.error in (None, ""), f"混合类型不应被拒: {res.error}"
    assert conn.inserts == 3


def test_explicit_type_overrides_inference(monkeypatch, reg):
    """显式 type 覆盖推断: 3 个显式 code → 按 code 去重拒绝。"""
    conn = _Conn()
    subtasks = _mk_subtasks(
        ("搜索苏州项目", "code"),
        ("搜索苏州政策", "code"),
        ("搜索苏州企业", "code"),
    )
    res = _run(monkeypatch, conn, subtasks)
    assert res.error and "同类子任务已含 code" in res.error
    assert conn.inserts == 0


def test_process_level_concurrent_wait_timeout(monkeypatch, reg):
    """进程级并发: 已有 search running → 新 search 委派等待超时拒绝。"""
    conn = _Conn()
    # 先占满 search 配额
    async def _pre():
        assert await reg.acquire("search", max_conc=1, timeout_sec=0.2) is True

    asyncio.run(_pre())
    subtasks = _mk_subtasks(("搜索苏州项目清单", None))
    res = _run(monkeypatch, conn, subtasks)
    assert res.error and "并发已达上限" in res.error
    assert conn.inserts == 0


def test_release_after_normal_completion(monkeypatch, reg):
    """正常完成路径释放配额: 委派后 registry 计数归零。"""
    conn = _Conn()
    subtasks = _mk_subtasks(("搜索苏州项目清单", None), ("分析销售数据", None))
    _run(monkeypatch, conn, subtasks)
    assert reg.current("search") == 0
    assert reg.current("analysis") == 0
