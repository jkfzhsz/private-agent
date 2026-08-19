"""V1.5 项-4 断点恢复 —— ReactLoop resume 模式测试。

场景语义(与生产路径一致):
- checkpoint.turn = 已完整完成的轮次(每轮 final 后写入)
- 中断轮 = checkpoint.turn + 1, 该轮 user 消息已持久化, 可能有残留
  assistant/tool 消息(main.py resume 前置逻辑已清理, 保留 user)
- ReactLoop(resume_from_turn=中断轮).run_turn(resume=True):
  * turn 不递增(沿用中断轮号, 前端按 turn 分组不错乱)
  * 不重复 append user 消息(避免同轮两条 user)
  * 从现有上下文直接进入 ReAct 循环
"""
import asyncio
import os

import asyncpg

from private_agent.core.checkpoint import CheckpointManager
from private_agent.core.context_manager import ContextManager
from private_agent.core.react_loop import ReactLoop
from private_agent.models.base import ChatResult, ModelCapability
from private_agent.storage import migrations
from private_agent.tools.defs import ECHO_TOOL

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


class _MockAdapter:
    """测试用 mock 适配器, 返回预设 ChatResult。"""

    provider_name = "mock"
    capability = ModelCapability(
        streaming=False, function_calling=True, vision=False, json_mode=False
    )

    def __init__(self, responses: list[ChatResult]) -> None:
        self._responses = list(responses)
        self._idx = 0
        self.chat_calls: list[list[dict]] = []

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        max_tokens: int | None = None,
        **kwargs,  # 多模态(require_vision)等扩展参数, mock 忽略
    ) -> ChatResult:
        self.chat_calls.append(list(messages))
        if self._idx >= len(self._responses):
            raise RuntimeError(f"mock adapter exhausted: idx={self._idx}")
        result = self._responses[self._idx]
        self._idx += 1
        return result


def test_resume_turn_keeps_turn_and_skips_duplicate_user():
    """resume 模式: turn 不递增、不重复 append user 消息、从现有上下文续跑。"""
    _setup_schema()

    async def _run() -> None:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await conn.fetchval(
                "INSERT INTO sessions (title, model_id) VALUES ('resume', 'mock') "
                "RETURNING id"
            )
            cm = ContextManager(
                session_id=session_id, system_prompt="sys", tools=[ECHO_TOOL]
            )
            await cm.build_initial(conn)

            # ── 造中断场景 ──
            # turn 1 正常完成(有 checkpoint, turn=1)
            adapter1 = _MockAdapter(
                responses=[ChatResult(content="round1 done", used_provider="mock")]
            )
            loop1 = ReactLoop(
                session_id=session_id,
                context_manager=cm,
                adapter=adapter1,
                tools=[ECHO_TOOL],
                conn=conn,
            )
            await loop1.run_turn("first question")
            # turn 2 中断: 只有 user 消息 + 一条残留 tool 消息(Phase A 异常路径),
            # 无 assistant 配对 —— 恢复前必须清理, 否则下次模型调用 400
            await cm.append_user_message(
                conn, turn=2, content="second question (interrupted)"
            )
            await cm.append_tool_message(
                conn, turn=2, tool_call_id="call_x", content="",
                name="echo", error="interrupted mid-tool",
            )

            # 校验 checkpoint 在 turn 1
            ckpt = await CheckpointManager.load_latest_checkpoint(conn, session_id)
            assert ckpt is not None and ckpt["turn"] == 1

            # ── 调用方 resume 前置逻辑(与 main.py _handle_user_message 一致):
            # 清 turn>1 的 assistant/tool 残留, 保留 user ──
            await conn.execute(
                """DELETE FROM messages
                   WHERE session_id=$1 AND turn>$2
                     AND role IN ('assistant', 'tool')""",
                session_id, 1,
            )
            await conn.execute(
                "DELETE FROM react_events WHERE session_id=$1 AND turn>$2",
                session_id, 1,
            )
            await conn.execute(
                "UPDATE sessions SET status='active' WHERE id=$1", session_id
            )

            # ── resume 续跑 ──
            cm2 = ContextManager(
                session_id=session_id, system_prompt="sys", tools=[ECHO_TOOL]
            )
            await cm2.build_initial(conn)
            await cm2.reload_from_db(conn)  # 生产路径: 重建内存上下文
            adapter2 = _MockAdapter(
                responses=[ChatResult(content="resumed answer", used_provider="mock")]
            )
            loop2 = ReactLoop(
                session_id=session_id,
                context_manager=cm2,
                adapter=adapter2,
                tools=[ECHO_TOOL],
                conn=conn,
                resume_from_turn=2,
            )
            await loop2.run_turn(resume=True)

            # turn 保持 2(不递增)
            assert loop2._turn == 2

            # 内存上下文: turn 2 的 user 消息只有一条(没有重复追加)
            user_msgs_turn2 = [
                m for m in cm2.get_messages_with_meta()
                if m.get("turn") == 2 and m.get("role") == "user"
            ]
            assert len(user_msgs_turn2) == 1
            assert user_msgs_turn2[0]["content"] == "second question (interrupted)"

            # DB: turn 2 最终一条 user + 一条 assistant(final), 无残留 tool
            rows = await conn.fetch(
                "SELECT role, content FROM messages "
                "WHERE session_id=$1 AND turn=2 ORDER BY id",
                session_id,
            )
            roles = [r["role"] for r in rows]
            assert roles == ["user", "assistant"]
            assert rows[1]["content"] == "resumed answer"

            # resume 轮也有 checkpoint(续跑轮完成后写)
            ckpt2 = await CheckpointManager.load_latest_checkpoint(conn, session_id)
            assert ckpt2 is not None and ckpt2["turn"] == 2

            # 模型看到的上下文包含中断轮 user 消息(续跑不丢用户问题)
            assert any(
                "second question" in str(m)
                for m in adapter2.chat_calls[0]
            )
        finally:
            await conn.close()

    asyncio.run(_run())


def test_resume_turn_requires_resume_from_turn():
    """resume=True 但未提供 resume_from_turn → ValueError。"""
    _setup_schema()

    async def _run() -> None:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await conn.fetchval(
                "INSERT INTO sessions (title, model_id) VALUES ('resume2', 'mock') "
                "RETURNING id"
            )
            cm = ContextManager(
                session_id=session_id, system_prompt="sys", tools=[]
            )
            await cm.build_initial(conn)
            adapter = _MockAdapter(
                responses=[ChatResult(content="x", used_provider="mock")]
            )
            loop = ReactLoop(
                session_id=session_id,
                context_manager=cm,
                adapter=adapter,
                tools=[],
                conn=conn,
                # resume_from_turn 缺省 None
            )
            try:
                await loop.run_turn(resume=True)
                raise AssertionError("应抛出 ValueError")
            except ValueError as e:
                assert "resume_from_turn" in str(e)
        finally:
            await conn.close()

    asyncio.run(_run())


def test_resume_missing_user_message_recovery_note():
    """中断轮 user 消息缺失(任务创建后立即取消)时, 恢复提示占位不重复。"""
    _setup_schema()

    async def _run() -> None:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            session_id = await conn.fetchval(
                "INSERT INTO sessions (title, model_id) VALUES ('resume3', 'mock') "
                "RETURNING id"
            )
            cm = ContextManager(
                session_id=session_id, system_prompt="sys", tools=[ECHO_TOOL]
            )
            await cm.build_initial(conn)
            # turn 1 正常完成(checkpoint turn=1)
            adapter1 = _MockAdapter(
                responses=[ChatResult(content="done", used_provider="mock")]
            )
            loop1 = ReactLoop(
                session_id=session_id, context_manager=cm, adapter=adapter1,
                tools=[ECHO_TOOL], conn=conn,
            )
            await loop1.run_turn("q1")
            # 中断轮 turn 2 无任何消息(模拟 append 前被 cancel)
            ckpt = await CheckpointManager.load_latest_checkpoint(conn, session_id)
            assert ckpt["turn"] == 1
            # 恢复提示占位(main.py resume 前置逻辑)
            await conn.execute(
                """INSERT INTO messages (session_id, turn, role, content)
                   VALUES ($1, $2, 'user', $3)""",
                session_id, 2,
                "[resume] 请继续完成之前中断的任务(若已完成请简要说明)。",
            )
            await conn.execute(
                "UPDATE sessions SET status='active' WHERE id=$1", session_id
            )
            cm2 = ContextManager(
                session_id=session_id, system_prompt="sys", tools=[ECHO_TOOL]
            )
            await cm2.build_initial(conn)
            await cm2.reload_from_db(conn)
            adapter2 = _MockAdapter(
                responses=[ChatResult(content="continued", used_provider="mock")]
            )
            loop2 = ReactLoop(
                session_id=session_id, context_manager=cm2, adapter=adapter2,
                tools=[ECHO_TOOL], conn=conn, resume_from_turn=2,
            )
            await loop2.run_turn(resume=True)
            assert loop2._turn == 2
            rows = await conn.fetch(
                "SELECT role, content FROM messages "
                "WHERE session_id=$1 AND turn=2 ORDER BY id",
                session_id,
            )
            assert [r["role"] for r in rows] == ["user", "assistant"]
            assert "[resume]" in rows[0]["content"]
        finally:
            await conn.close()

    asyncio.run(_run())
