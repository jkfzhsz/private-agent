"""项目优化(opencode 借鉴) - Doom Loop 死循环检测测试。

覆盖:
- _detect_tool_loop: same_args(同参数重复) / same_tool(同工具高频)
- 循环检测 → 注入提示消息(仅内存) + tool_loop_detected 事件
- 提示超限仍循环 → 强制终止本轮
- 正常工具调用不误报
- status_bar 环境注入(workspace/platform)
"""
import asyncio
import os

import asyncpg

from private_agent.core.context_manager import ContextManager
from private_agent.core.react_loop import ReactLoop
from private_agent.core.status_bar import AgentStatusBar
from private_agent.models.base import ChatResult
from private_agent.storage import migrations

TEST_DSN = os.environ.get(
    "PA_TEST_DSN",
    "postgresql://postgres:123123@localhost:5432/private_agent_test",
)


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


async def _create_session(conn):
    return await conn.fetchval(
        "INSERT INTO sessions (title, model_id) VALUES ($1, $2) RETURNING id",
        "test-loop", "mock",
    )


class _MockAdapter:
    provider_name = "mock"

    def __init__(self, responses):
        self._responses = list(responses)
        self._idx = 0

    async def chat(self, messages, tools=None, max_tokens=None):
        r = self._responses[self._idx]
        self._idx += 1
        return r




def _make_sink(events):
    """构造 async event_sink(ReactLoop._emit_event 需要 awaitable)。"""
    async def _sink(ev):
        events.append(ev)
    return _sink


def _echo_tool(name="echo"):
    from private_agent.tools.defs import ToolDef, ToolResult

    async def _h(args):
        return ToolResult(output="ok")

    return ToolDef(
        name=name, description="echo", parameters_schema={"type": "object"},
        handler=_h,
    )


def _tool_call(name, args_str='{"text": "x"}'):
    return ChatResult(
        content="", used_provider="mock",
        tool_calls=[{
            "id": "c1", "type": "function",
            "function": {"name": name, "arguments": args_str},
        }],
    )


# ── _detect_tool_loop 单元 ──

def test_detect_same_args_loop():
    """同参数重复 ≥3 次 → same_args。"""
    _setup_schema()

    async def _run():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            cm = ContextManager(session_id=session_id, system_prompt="sys", tools=[])
            await cm.build_initial(conn)
            loop = ReactLoop(session_id=session_id, context_manager=cm,
                             adapter=_MockAdapter([]), tools=[], conn=conn,
                             cfg={"context": {"status_bar": {"enabled": False}}})
            assert loop._detect_tool_loop("web_search", {"q": "weather"}) is None
            assert loop._detect_tool_loop("web_search", {"q": "weather"}) is None
            # 第三次同参数 → same_args
            assert loop._detect_tool_loop("web_search", {"q": "weather"}) == "same_args"
        finally:
            await conn.close()

    asyncio.run(_run())


def test_detect_same_tool_loop():
    """同工具高频(不同参数) → same_tool。"""
    _setup_schema()

    async def _run():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            cm = ContextManager(session_id=session_id, system_prompt="sys", tools=[])
            await cm.build_initial(conn)
            loop = ReactLoop(session_id=session_id, context_manager=cm,
                             adapter=_MockAdapter([]), tools=[], conn=conn,
                             cfg={"context": {"status_bar": {"enabled": False}}})
            for i in range(4):
                assert loop._detect_tool_loop("web_search", {"q": f"query{i}"}) is None
            # 第 5 次同工具 → same_tool
            assert loop._detect_tool_loop("web_search", {"q": "query4"}) == "same_tool"
        finally:
            await conn.close()

    asyncio.run(_run())


def test_no_false_positive_on_alternating():
    """交替工具调用不误报。"""
    _setup_schema()

    async def _run():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            cm = ContextManager(session_id=session_id, system_prompt="sys", tools=[])
            await cm.build_initial(conn)
            loop = ReactLoop(session_id=session_id, context_manager=cm,
                             adapter=_MockAdapter([]), tools=[], conn=conn,
                             cfg={"context": {"status_bar": {"enabled": False}}})
            for i in range(10):
                name = "tool_a" if i % 2 == 0 else "tool_b"
                assert loop._detect_tool_loop(name, {"n": i}) is None
        finally:
            await conn.close()

    asyncio.run(_run())


# ── ReactLoop 集成: 循环提示注入 + 强制终止 ──

def test_loop_detected_injects_note_and_emits_event():
    """循环检测 → 注入提示消息 + tool_loop_detected 事件(不入库误伤)。"""
    _setup_schema()

    async def _run():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            cm = ContextManager(session_id=session_id, system_prompt="sys", tools=[])
            await cm.build_initial(conn)
            echo = _echo_tool()
            # 三轮都调用相同参数的 echo → 第三轮触发循环提示
            adapter = _MockAdapter(responses=[
                _tool_call("echo", '{"text": "x"}'),
                _tool_call("echo", '{"text": "x"}'),
                _tool_call("echo", '{"text": "x"}'),  # 第3次: 触发 same_args 检测
                ChatResult(content="final answer", used_provider="mock"),
            ])
            events = []
            loop = ReactLoop(
                session_id=session_id, context_manager=cm,
                adapter=adapter, tools=[echo], conn=conn,
                cfg={"context": {"status_bar": {"enabled": False},
                                 "loop": {"max_warnings": 1}}},
                event_sink=_make_sink(events),
            )
            await loop.run_turn("loop test")
            # 触发了循环检测(事件)
            loop_evts = [e for e in events if e.get("event_type") == "tool_loop_detected"]
            assert len(loop_evts) == 1
            assert loop_evts[0]["payload"]["loop_type"] == "same_args"
            assert loop_evts[0]["payload"]["tool_name"] == "echo"
            # 模型第三轮收到注入的提示消息(仅内存, 不持久化)
            assert cm.active_zone.messages  # 提示消息存在
            # DB 中无提示消息(仅内存注入)
            n_db = await conn.fetchval(
                "SELECT COUNT(*) FROM messages WHERE session_id=$1 AND content LIKE '[System Note]%'",
                session_id,
            )
            assert n_db == 0
        finally:
            await conn.close()

    asyncio.run(_run())


def test_loop_warning_exceeds_forces_stop():
    """循环提示超限仍循环 → 强制终止本轮(final + 不执行更多工具)。"""
    _setup_schema()

    async def _run():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await _create_session(conn)
            cm = ContextManager(session_id=session_id, system_prompt="sys", tools=[])
            await cm.build_initial(conn)
            echo = _echo_tool()
            # max_warnings=1: 第一次检测循环注入提示(第3次调用), 第4次仍循环 → 终止
            adapter = _MockAdapter(responses=[
                _tool_call("echo", '{"text": "x"}'),
                _tool_call("echo", '{"text": "x"}'),
                _tool_call("echo", '{"text": "x"}'),  # 第3次: 触发提示
                _tool_call("echo", '{"text": "x"}'),  # 第4次: 仍循环 → 强制终止
            ])
            events = []
            loop = ReactLoop(
                session_id=session_id, context_manager=cm,
                adapter=adapter, tools=[echo], conn=conn,
                cfg={"context": {"status_bar": {"enabled": False},
                                 "loop": {"max_warnings": 1}}},
                event_sink=_make_sink(events),
            )
            await loop.run_turn("loop test")
            finals = [e for e in events if e.get("event_type") == "final"]
            assert finals, "应产生 final(强制终止消息)"
            assert "死循环" in finals[-1]["payload"]["content"]
            # 只执行了 3 次 echo(第4次被终止, 未执行)
            n_tool_result = len([e for e in events if e.get("event_type") == "tool_result"])
            assert n_tool_result == 3
        finally:
            await conn.close()

    asyncio.run(_run())


# ── status_bar 环境注入 ──

def test_status_bar_injects_environment():
    """状态栏注入工作目录/平台行(运行时环境信息)。"""
    bar = AgentStatusBar()
    text = bar.render(state="acting", turn=1, iteration=0, max_iterations=10,
                      workspace="D:\\Private agent", platform="Windows")
    assert "工作目录: D:\\Private agent" in text
    assert "运行平台: Windows" in text
    # 不传时无环境行(兼容旧调用)
    text2 = bar.render(state="idle")
    assert "工作目录:" not in text2
