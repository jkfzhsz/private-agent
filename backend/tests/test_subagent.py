"""V1.5 项-1(ADR-012) M2 并行执行 + 监听/心跳闭环测试。

覆盖 M2 验收清单:
- 原验收: 心跳停(置过期) → stale → grace → kill → DB failed(heartbeat_timeout)
  + 主对话不阻塞(handler 返回失败结果)
- AC-1 心跳协程故障可观测: kill 心跳 task → heartbeat_task_failure 日志,
  业务 task 不被误杀(正常完成)
- AC-2 硬总时长兜底: 死循环 + 心跳正常 → max_lifetime 强制 failed
- AC-3 原子幂等: 并发条件更新只成功一次
- AC-4 cancel 拒绝终止: zombie_task_detected 日志 + DB 仍置 failed
- 附加: 成功路径(独立子 session + 结果落库)、delegate 校验(1~3)、
  并行聚合、list_sessions 过滤 sub、启动崩溃兜底
"""
import asyncio
import os

import asyncpg
import pytest
from fastapi.testclient import TestClient

from private_agent.config import loader
from private_agent.core import subagent as subagent_mod
from private_agent.core.subagent import (
    SubagentRunner,
    cleanup_zombies_on_startup,
    grace_expired_ids,
    kill_tasks,
    lifetime_exceeded_ids,
    scan_and_mark_stalled,
)
from private_agent.models.base import ChatResult, ModelCapability
from private_agent.storage import db, migrations
from private_agent.tools.builtins.delegate_subtask import (
    build_delegate_subtask_tool,
)

TEST_DSN = os.environ.get(
    "PA_TEST_DSN",
    "postgresql://postgres:123123@localhost:5432/private_agent_test",
)

# 测试用 subagent 配置(小值加速, 时间线见各测试注释)
_FAST_CFG = {
    "heartbeat_interval_sec": 0.2,   # 心跳周期
    "heartbeat_timeout_sec": 1.0,    # stale 阈值
    "heartbeat_poll_sec": 0.2,       # watchdog 轮询
    "grace_sec": 1.0,                # stale 后宽限
    "max_total_lifetime_sec": 999.0,  # 硬总时长(默认极大, 按测试覆盖)
    "max_parallel": 3,
    "max_nesting_depth": 2,
    "cancel_wait_sec": 0.5,
    "max_restarts": 0,
}


def _setup_schema() -> None:
    async def _run() -> None:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            await conn.execute("DROP SCHEMA public CASCADE")
            await conn.execute("CREATE SCHEMA public")
            await conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
            await migrations.migrate_all(conn)
        finally:
            await conn.close()

    asyncio.run(_run())


@pytest.fixture(scope="module", autouse=True)
def _schema_fixture():
    """模块级: 重建测试库 schema 一次(含 subagents 表 + sessions.kind 迁移)。"""
    _setup_schema()


@pytest.fixture(autouse=True)
def _patch_db_connect(monkeypatch):
    """SubagentRunner 内部 db.connect → 测试库(所有测试统一)。"""

    async def _fake_connect(cfg=None):
        return await asyncpg.connect(TEST_DSN)

    monkeypatch.setattr(db, "connect", _fake_connect)


@pytest.fixture(autouse=True)
def _clean_subagents():
    """每个测试前清空 subagents 表(防 parent_task 等标识跨测试污染)。"""
    async def _run() -> None:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            await conn.execute("TRUNCATE subagents RESTART IDENTITY CASCADE")
        finally:
            await conn.close()

    asyncio.run(_run())


def _test_cfg() -> dict:
    """真实 config.yaml + 测试用 subagent 配置覆盖。"""
    cfg = loader.load_config()
    cfg["tools"]["subagent"] = dict(_FAST_CFG)
    return cfg


class _MockAdapter:
    """预设响应 mock 适配器(与既有测试同构)。"""

    provider_name = "mock"
    capability = ModelCapability(
        streaming=False, function_calling=True, vision=False, json_mode=False
    )

    def __init__(self, responses: list[ChatResult]) -> None:
        self._responses = list(responses)
        self._idx = 0

    async def chat(self, messages, tools=None, max_tokens=None, **kwargs) -> ChatResult:
        if self._idx >= len(self._responses):
            raise RuntimeError(f"mock adapter exhausted: idx={self._idx}")
        r = self._responses[self._idx]
        self._idx += 1
        return r


class _HungAdapter:
    """chat 永不返回(模拟模型调用无限挂起/死循环)。"""

    provider_name = "mock"
    capability = ModelCapability(
        streaming=False, function_calling=True, vision=False, json_mode=False
    )

    async def chat(self, messages, tools=None, max_tokens=None, **kwargs) -> ChatResult:
        await asyncio.sleep(3600)
        return ChatResult(content="unreachable")


def _make_runner(
    *,
    conn,
    cfg,
    subagent_id: int,
    prompt: str,
    parent_session_id: int,
    parent_turn: int,
    adapter,
    events: list | None = None,
):
    """构造 SubagentRunner(注入 mock 依赖)。"""
    async def _sys(conn, sid):
        return "sub system prompt"

    def _adapter_factory(model_id):
        return adapter

    async def _sink(ev: dict) -> None:
        if events is not None:
            events.append(ev)

    return SubagentRunner(
        cfg=cfg,
        subagent_id=subagent_id,
        task_id="t1",
        prompt=prompt,
        parent_session_id=parent_session_id,
        parent_turn=parent_turn,
        tools=[],
        event_sink=_sink,
        system_prompt_factory=_sys,
        adapter_factory=_adapter_factory,
    )


async def _new_parent_session(conn, model_id: str | None = "mock") -> int:
    return await conn.fetchval(
        "INSERT INTO sessions (title, model_id, workspace) "
        "VALUES ('parent', $1, 'C:/ws') RETURNING id",
        model_id,
    )


async def _insert_pending_subagent(
    conn, parent_session_id: int, prompt: str = "do something"
) -> int:
    return await conn.fetchval(
        "INSERT INTO subagents (session_id, parent_turn, parent_task, prompt, "
        "model_id, status) VALUES ($1, 1, 't1', $2, 'mock', 'pending') RETURNING id",
        parent_session_id, prompt,
    )


# ──────────────────────────────────────────────────────────────────────────────
# M1 存储: 迁移幂等
# ──────────────────────────────────────────────────────────────────────────────


def test_migration_subagents_and_kind_idempotent():
    """migrate_all 幂等: subagents 表 + sessions.kind 列存在, 重复迁移无异常。"""

    async def _run() -> None:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            # 已由模块 fixture 建好, 再跑一次验证幂等
            await migrations.migrate_all(conn)
            has_table = await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM pg_tables "
                "WHERE schemaname='public' AND tablename='subagents')"
            )
            assert has_table is True
            row = await conn.fetchrow(
                "SELECT column_name, column_default FROM information_schema.columns "
                "WHERE table_name='sessions' AND column_name='kind'"
            )
            assert row is not None
            # 主会话默认 kind='main'
            sid = await _new_parent_session(conn)
            kind = await conn.fetchval(
                "SELECT kind FROM sessions WHERE id=$1", sid
            )
            assert kind == "main"
        finally:
            await conn.close()

    asyncio.run(_run())


# ──────────────────────────────────────────────────────────────────────────────
# SubagentRunner 成功路径
# ──────────────────────────────────────────────────────────────────────────────


def test_runner_success_creates_sub_session_and_result():
    """成功路径: 子 session(kind='sub', 继承 model/workspace) + 结果落库。"""

    async def _run() -> None:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            parent = await _new_parent_session(conn)
            sid = await _insert_pending_subagent(conn, parent)
            events: list[dict] = []
            adapter = _MockAdapter(
                responses=[ChatResult(content="research done", used_provider="mock")]
            )
            runner = _make_runner(
                conn=conn, cfg=_test_cfg(), subagent_id=sid, prompt="调研 A",
                parent_session_id=parent, parent_turn=1, adapter=adapter,
                events=events,
            )
            await asyncio.wait_for(runner.run(), timeout=30)

            row = await conn.fetchrow(
                "SELECT status, result, tool_calls, session_id FROM subagents WHERE id=$1",
                sid,
            )
            assert row["status"] == "succeeded"
            assert "research done" in row["result"]
            assert row["tool_calls"] == 0
            sub_sess = await conn.fetchrow(
                "SELECT kind, model_id, workspace, locked_skill_name "
                "FROM sessions WHERE id=$1",
                row["session_id"],
            )
            assert sub_sess["kind"] == "sub"
            assert sub_sess["model_id"] == "mock"
            assert sub_sess["workspace"] == "C:/ws"
            # 子代理消息落在子 session(独立 ctx)
            msgs = await conn.fetchval(
                "SELECT COUNT(*) FROM messages WHERE session_id=$1",
                row["session_id"],
            )
            assert msgs >= 2  # user + assistant
            # final 事件已包装推送
            finals = [e for e in events if e.get("event_type") == "final"]
            assert finals and finals[0]["payload"]["content"] == "research done"
        finally:
            await conn.close()

    asyncio.run(_run())


def test_runner_model_error_marks_failed():
    """模型异常路径: failed + error 摘要落库。"""

    async def _run() -> None:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            parent = await _new_parent_session(conn)
            sid = await _insert_pending_subagent(conn, parent)

            class _FailAdapter:
                provider_name = "mock"
                capability = _MockAdapter.capability

                async def chat(self, messages, tools=None, max_tokens=None, **kwargs):
                    raise RuntimeError("upstream exploded")

            runner = _make_runner(
                conn=conn, cfg=_test_cfg(), subagent_id=sid, prompt="p",
                parent_session_id=parent, parent_turn=1, adapter=_FailAdapter(),
            )
            await asyncio.wait_for(runner.run(), timeout=30)
            row = await conn.fetchrow(
                "SELECT status, error FROM subagents WHERE id=$1", sid
            )
            assert row["status"] == "failed"
            assert "upstream exploded" in row["error"]
        finally:
            await conn.close()

    asyncio.run(_run())


# ──────────────────────────────────────────────────────────────────────────────
# delegate_subtask 工具: 校验 + 并行聚合
# ──────────────────────────────────────────────────────────────────────────────


def _build_tool(conn, cfg, session_id, events):
    """构建 delegate 工具(测试用 adapter_factory 由各测试按需覆盖)。"""
    async def _sink(ev: dict) -> None:
        events.append(ev)

    async def _sys(c, sid):
        return "sub system prompt"

    def _adapter_factory(model_id):
        raise AssertionError("unexpected adapter_factory call (未配置)")

    return build_delegate_subtask_tool(
        conn=conn,
        cfg=cfg,
        session_id=session_id,
        event_sink=_sink,
        tools=[],
        system_prompt_factory=_sys,
        adapter_factory=_adapter_factory,
    )


def test_delegate_validation_rules():
    """校验: 空/超 3 个/缺字段/嵌套(子会话)拒绝。"""

    async def _run() -> None:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            parent = await _new_parent_session(conn)
            events: list[dict] = []
            tool = _build_tool(conn, _test_cfg(), parent, events)
            # 空列表
            r = await tool.handler({"subtasks": []})
            assert r.error and "不能为空" in r.error
            # 超 3 个
            r = await tool.handler({
                "subtasks": [
                    {"id": f"t{i}", "prompt": "p"} for i in range(4)
                ]
            })
            assert r.error and "最多 3" in r.error
            # 缺 prompt
            r = await tool.handler({"subtasks": [{"id": "t1"}]})
            assert r.error and "非空 id 与 prompt" in r.error
            # 子会话(深度 1)再委派 → 拒绝
            sub_sess = await conn.fetchval(
                "INSERT INTO sessions (kind, title) VALUES ('sub', 'x') RETURNING id"
            )
            tool2 = _build_tool(conn, _test_cfg(), sub_sess, events)
            r = await tool2.handler({
                "subtasks": [{"id": "t1", "prompt": "p"}]
            })
            assert r.error and "嵌套" in r.error
        finally:
            await conn.close()

    asyncio.run(_run())


def test_delegate_parallel_two_subtasks():
    """两个子任务并行执行 + 聚合结果(各含 task id 与独立结果)。"""

    async def _run() -> None:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            parent = await _new_parent_session(conn)
            events: list[dict] = []
            adapters = [
                _MockAdapter(
                    responses=[ChatResult(content="answer for A", used_provider="m")]
                ),
                _MockAdapter(
                    responses=[ChatResult(content="answer for B", used_provider="m")]
                ),
            ]

            async def _sink(ev):
                events.append(ev)

            async def _sys(c, sid):
                return "sub system prompt"

            # 每子代理一个独立 mock(按 runner 创建顺序分发, 与 subtasks 顺序一致)
            seen = {"n": 0}

            def _af(model_id):
                i = seen["n"]
                seen["n"] += 1
                return adapters[i]

            tool = build_delegate_subtask_tool(
                conn=conn, cfg=_test_cfg(), session_id=parent, event_sink=_sink,
                tools=[], system_prompt_factory=_sys, adapter_factory=_af,
            )
            result = await asyncio.wait_for(
                tool.handler({
                    "subtasks": [
                        # 2026-08-13 类型感知限流: 同类型只开 1 个 → 用不同
                        # 类型(search + analysis)验证并行, 避免触发同轮去重
                        {"id": "t1", "prompt": "调研 A"},
                        {"id": "t2", "prompt": "分析 B 的数据特征", "type": "analysis"},
                    ]
                }),
                timeout=45,
            )
            assert result.error is None
            assert "answer for A" in result.output
            assert "answer for B" in result.output
            # 两个 subagents 行均 succeeded(按 parent_task 过滤 —— session_id
            # 已被 runner 回填为子 session id)
            rows = await conn.fetch(
                "SELECT parent_task, status FROM subagents "
                "WHERE parent_task IN ('t1', 't2') ORDER BY id",
            )
            assert len(rows) == 2
            assert {r["status"] for r in rows} == {"succeeded"}
            # WS 事件序列: 2 × subagent_start, 2 × subagent_result
            starts = [e for e in events if e.get("type") == "subagent_start"]
            results = [e for e in events if e.get("type") == "subagent_result"]
            assert len(starts) == 2 and len(results) == 2
        finally:
            await conn.close()

    asyncio.run(_run())


# ──────────────────────────────────────────────────────────────────────────────
# watchdog 闭环: stale → grace → kill(原验收)
# ──────────────────────────────────────────────────────────────────────────────


def test_watchdog_stale_grace_kill():
    """心跳停(interval=60s 模拟不刷新 + 置过期) → stale(stalled_at) →
    grace 耗尽 → failed(heartbeat_timeout) + runner 终止 + 主对话不阻塞。"""

    async def _run() -> None:
        conn = await asyncpg.connect(TEST_DSN)
        # handler 专用连接(与测试干预连接分离 —— 测试在 handler 轮询期间
        # 并发 UPDATE, 共用同一 asyncpg 连接会 InterfaceError)
        handler_conn = await asyncpg.connect(TEST_DSN)
        try:
            parent = await _new_parent_session(conn)
            events: list[dict] = []

            async def _sink(ev):
                events.append(ev)

            async def _sys(c, sid):
                return "sub system prompt"

            cfg = _test_cfg()
            cfg["tools"]["subagent"] = {
                **_FAST_CFG,
                "heartbeat_interval_sec": 60.0,   # 心跳几乎不刷 → 模拟心跳停
                "heartbeat_timeout_sec": 1.0,
                "heartbeat_poll_sec": 0.2,
                "grace_sec": 1.0,
                "cancel_wait_sec": 0.5,
                "max_total_lifetime_sec": 999.0,
            }

            def _af(model_id):
                return _HungAdapter()

            tool = build_delegate_subtask_tool(
                conn=handler_conn, cfg=cfg, session_id=parent, event_sink=_sink,
                tools=[], system_prompt_factory=_sys, adapter_factory=_af,
            )
            handler_task = asyncio.create_task(
                tool.handler({
                    "subtasks": [{"id": "t1", "prompt": "挂起的任务"}]
                })
            )
            # 等 runner 进入挂起(chat 永不返回; Windows 冷连接 ~0.7s/个)
            await asyncio.sleep(1.5)
            # 模拟心跳停止: 把最后心跳置为过期(心跳 interval=60s, 不会覆盖;
            # 该行 session_id 已被 runner 回填为子 session, 按 parent_task 过滤)
            await conn.execute(
                "UPDATE subagents SET last_heartbeat_at=now()-interval '10 seconds' "
                "WHERE parent_task='t1'",
            )
            # watchdog: 1.0s 后检到 stale → stalled_at 置位; +1.0s grace → kill
            result = await asyncio.wait_for(handler_task, timeout=30)
            assert result.error is None  # 主对话不阻塞, 失败结果回主模型
            assert "heartbeat_timeout" in result.output
            # DB 终态
            row = await conn.fetchrow(
                "SELECT status, error, stalled_at, finished_at FROM subagents "
                "WHERE parent_task='t1'",
            )
            assert row["status"] == "failed"
            assert row["error"] == "heartbeat_timeout"
            assert row["stalled_at"] is not None
            assert row["finished_at"] is not None
            # WS 事件: start → stalled → error(heartbeat_timeout)
            types = [e.get("type") for e in events]
            assert "subagent_start" in types
            assert "subagent_stalled" in types
            assert "subagent_error" in types
        finally:
            await handler_conn.close()
            await conn.close()

    asyncio.run(_run())


# ──────────────────────────────────────────────────────────────────────────────
# AC-2: 硬总时长兜底(死循环 + 心跳正常)
# ──────────────────────────────────────────────────────────────────────────────


def test_max_lifetime_enforced():
    """子代理死循环但心跳正常 → max_total_lifetime_sec 到达后强制
    failed(max_lifetime_exceeded)。"""

    async def _run() -> None:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            parent = await _new_parent_session(conn)
            events: list[dict] = []

            async def _sink(ev):
                events.append(ev)

            async def _sys(c, sid):
                return "sub system prompt"

            cfg = _test_cfg()
            cfg["tools"]["subagent"] = {
                **_FAST_CFG,
                "heartbeat_interval_sec": 0.2,   # 心跳正常刷新
                "heartbeat_timeout_sec": 999.0,  # 心跳永不 stale
                "heartbeat_poll_sec": 0.2,
                "grace_sec": 999.0,
                "max_total_lifetime_sec": 2.0,   # 硬总时长 2s
                "cancel_wait_sec": 0.5,
            }

            def _af(model_id):
                return _HungAdapter()

            tool = build_delegate_subtask_tool(
                conn=conn, cfg=cfg, session_id=parent, event_sink=_sink,
                tools=[], system_prompt_factory=_sys, adapter_factory=_af,
            )
            result = await asyncio.wait_for(
                tool.handler({
                    "subtasks": [{"id": "t1", "prompt": "死循环任务"}]
                }),
                timeout=30,
            )
            assert result.error is None
            assert "max_lifetime_exceeded" in result.output
            row = await conn.fetchrow(
                "SELECT status, error FROM subagents WHERE parent_task='t1'"
            )
            assert row["status"] == "failed"
            assert row["error"] == "max_lifetime_exceeded"
        finally:
            await conn.close()

    asyncio.run(_run())


# ──────────────────────────────────────────────────────────────────────────────
# AC-1: 心跳协程故障可观测(业务不误杀)
# ──────────────────────────────────────────────────────────────────────────────


def test_heartbeat_failure_observable(monkeypatch):
    """kill 心跳 task → heartbeat_task_failure ERROR 日志; 业务 task 照常完成。"""

    async def _run() -> None:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            parent = await _new_parent_session(conn)
            sid = await _insert_pending_subagent(conn, parent)
            errors: list[tuple] = []
            monkeypatch.setattr(
                subagent_mod.logger, "error",
                lambda *a, **k: errors.append(a),
            )

            class _SlowAdapter:
                provider_name = "mock"
                capability = _MockAdapter.capability

                async def chat(self, messages, tools=None, max_tokens=None, **kwargs):
                    await asyncio.sleep(3.0)  # 业务慢但正常
                    return ChatResult(content="slow done", used_provider="m")

            cfg = _test_cfg()
            runner = _make_runner(
                conn=conn, cfg=cfg, subagent_id=sid, prompt="p",
                parent_session_id=parent, parent_turn=1, adapter=_SlowAdapter(),
            )
            run_task = asyncio.create_task(runner.run())
            # 等待心跳 task 启动(Windows asyncpg 冷连接 ~700ms, 轮询等待非固定 sleep)
            for _ in range(50):
                if runner._hb_task is not None:
                    break
                await asyncio.sleep(0.1)
            assert runner._hb_task is not None, "heartbeat task should have started"
            # 仅 kill 心跳 task(业务 task 不受影响)
            runner._hb_task.cancel()
            await asyncio.wait_for(run_task, timeout=30)
            # 业务不误杀: 正常 succeeded
            row = await conn.fetchrow(
                "SELECT status, result FROM subagents WHERE id=$1", sid
            )
            assert row["status"] == "succeeded"
            assert "slow done" in row["result"]
            # 心跳故障可观测: ERROR 日志含 heartbeat_task_failure
            assert any(
                "heartbeat_task_failure" in str(a)
                for e in errors for a in e
            ), f"expect heartbeat_task_failure log, got {errors}"
        finally:
            await conn.close()

    asyncio.run(_run())


# ──────────────────────────────────────────────────────────────────────────────
# AC-3: 原子条件更新幂等(并发只成功一次)
# ──────────────────────────────────────────────────────────────────────────────


def test_atomic_condition_updates_idempotent():
    """并发 scan_and_mark_stalled / grace_expired 同一 running 记录 → 只一次命中。"""

    async def _run() -> None:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            parent = await _new_parent_session(conn)
            sid = await conn.fetchval(
                """
                INSERT INTO subagents (session_id, parent_turn, prompt, status,
                                       last_heartbeat_at, started_at)
                VALUES ($1, 1, 'p', 'running',
                        now()-interval '2 minutes', now()-interval '2 minutes')
                RETURNING id
                """,
                parent,
            )
            # 并发 stale 扫描(两个独立连接)
            conn2 = await asyncpg.connect(TEST_DSN)
            try:
                r1, r2 = await asyncio.gather(
                    scan_and_mark_stalled(conn, [sid], 1.0),
                    scan_and_mark_stalled(conn2, [sid], 1.0),
                )
            finally:
                await conn2.close()
            assert sorted(r1 + r2) == [sid]  # 只成功一次
            # 已 stalled 的再次扫描不命中(不再重复)
            again = await scan_and_mark_stalled(conn, [sid], 1.0)
            assert again == []
            # grace 并发过期 → 只一次置 failed(先等 stalled_at 超过 grace_sec,
            # 消除"连接建立快慢"导致的时序竞态)
            await asyncio.sleep(0.7)
            conn3 = await asyncpg.connect(TEST_DSN)
            try:
                g1, g2 = await asyncio.gather(
                    grace_expired_ids(conn, [sid], 0.5),
                    grace_expired_ids(conn3, [sid], 0.5),
                )
            finally:
                await conn3.close()
            assert sorted(g1 + g2) == [sid]
            row = await conn.fetchrow(
                "SELECT status, error FROM subagents WHERE id=$1", sid
            )
            assert row["status"] == "failed"
            assert row["error"] == "heartbeat_timeout"
        finally:
            await conn.close()

    asyncio.run(_run())


# ──────────────────────────────────────────────────────────────────────────────
# AC-4: cancel 拒绝终止 → zombie_task_detected(DB 仍置 failed)
# ──────────────────────────────────────────────────────────────────────────────


def test_zombie_task_detected(monkeypatch):
    """cancel 无法终止的 task → zombie_task_detected ERROR 日志;
    DB 状态已由调用方先行置 failed(业务按失败处理)。"""

    async def _run() -> None:
        errors: list[tuple] = []
        monkeypatch.setattr(
            subagent_mod.logger, "error",
            lambda *a, **k: errors.append(a),
        )

        async def _stubborn():
            """拒绝终止的协程(模拟同步阻塞): cancel 后继续挂起。"""
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                try:
                    await asyncio.sleep(3600)
                except asyncio.CancelledError:
                    pass
                raise

        task = asyncio.create_task(_stubborn())
        await asyncio.sleep(0.1)
        # 调用方语义: DB 已先行置 failed(heartbeat_timeout) —— 见 handler 流程;
        # M4: kill_tasks 增加 conn 参数(zombie 时埋点入 react_events)
        await kill_tasks(
            None, {1: task}, [1], cancel_wait_sec=0.3,
            parent_session_id=None,
        )
        # zombie 日志
        assert any(
            "zombie_task_detected" in str(a)
            for e in errors for a in e
        ), f"expect zombie_task_detected log, got {errors}"
        # 清理: 再 cancel 让 task 真正退出
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(_run())


# ──────────────────────────────────────────────────────────────────────────────
# §3.3e: 启动崩溃兜底
# ──────────────────────────────────────────────────────────────────────────────


def test_cleanup_zombies_on_startup():
    """重启兜底: running 心跳过期 → failed(heartbeat_timeout_after_restart);
    心跳新鲜的 running 保留。"""

    async def _run() -> None:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            parent = await _new_parent_session(conn)
            stale_id = await conn.fetchval(
                "INSERT INTO subagents (session_id, parent_turn, prompt, status, "
                "last_heartbeat_at) VALUES ($1, 1, 'stale', 'running', "
                "now()-interval '30 minutes') RETURNING id",
                parent,
            )
            fresh_id = await conn.fetchval(
                "INSERT INTO subagents (session_id, parent_turn, prompt, status, "
                "last_heartbeat_at) VALUES ($1, 2, 'fresh', 'running', now()) "
                "RETURNING id",
                parent,
            )
            cfg = _test_cfg()  # heartbeat_timeout_sec=1.0
            n = await cleanup_zombies_on_startup(conn, cfg)
            assert n == 1
            s_row = await conn.fetchrow(
                "SELECT status, error FROM subagents WHERE id=$1", stale_id
            )
            assert s_row["status"] == "failed"
            assert s_row["error"] == "heartbeat_timeout_after_restart"
            f_row = await conn.fetchrow(
                "SELECT status FROM subagents WHERE id=$1", fresh_id
            )
            assert f_row["status"] == "running"
            # 幂等: 再跑一次不再命中
            n2 = await cleanup_zombies_on_startup(conn, cfg)
            assert n2 == 0
        finally:
            await conn.close()

    asyncio.run(_run())


# ──────────────────────────────────────────────────────────────────────────────
# R9: list_sessions 过滤子会话
# ──────────────────────────────────────────────────────────────────────────────


def test_list_sessions_filters_sub():
    """历史会话列表不显示 kind='sub' 的子代理会话。"""

    async def _seed() -> None:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            # main 会话(有 assistant 回复)
            main_id = await conn.fetchval(
                "INSERT INTO sessions (title, kind) VALUES ('main-sess', 'main') "
                "RETURNING id"
            )
            await conn.execute(
                "INSERT INTO messages (session_id, turn, role, content) "
                "VALUES ($1, 1, 'user', 'hi'), ($1, 1, 'assistant', 'hello')",
                main_id,
            )
            # sub 会话(有 assistant 回复, 但应被过滤)
            sub_id = await conn.fetchval(
                "INSERT INTO sessions (title, kind) VALUES ('sub-sess', 'sub') "
                "RETURNING id"
            )
            await conn.execute(
                "INSERT INTO messages (session_id, turn, role, content) "
                "VALUES ($1, 1, 'user', 'sub-task'), ($1, 1, 'assistant', 'done')",
                sub_id,
            )
        finally:
            await conn.close()

    asyncio.run(_seed())
    from private_agent.main import app

    client = TestClient(app)
    resp = client.get("/admin/sessions", headers={"X-Admin-Token": "test-admin-token"})
    assert resp.status_code == 200
    sessions = resp.json()
    assert any(s.get("title") == "main-sess" for s in sessions)
    assert not any(s.get("title") == "sub-sess" for s in sessions)
