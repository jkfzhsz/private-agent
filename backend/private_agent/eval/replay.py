"""M4 §8.10 ReplayExecutor - 交互式回放(蓝图 §8.10,AC-8, AC-9)。

Source: spec/m4-eval-runner-replay AC-8, AC-9 + plan step 6
- 创建临时评估会话(title="eval-" 前缀),执行 ReAct 循环,清理会话
- mock_enabled=True: 用 MockToolRegistry 替换 handler
- mock_enabled=False: 真实执行 tool_def.handler(args)
- 复用 ReactLoop,通过 event_sink 回调同步收集 actual_events(避免 event_queue 轮询死锁)
- try/finally 确保会话清理(异常时也清理)

会话管理用私有 helper 方法封装 SQL(spec Assumptions: MVP 不抽取 SessionRepo)。
"""
from __future__ import annotations

import asyncpg

from private_agent.core.context_manager import ContextManager
from private_agent.core.react_loop import ReactLoop
from private_agent.eval.mock_tool_registry import MockToolRegistry
from private_agent.eval.models import EvalSample
from private_agent.models.base import ModelAdapter
from private_agent.skills.models import Skill
from private_agent.tools.registry import ToolRegistry

__all__ = ["ReplayExecutor"]


class ReplayExecutor:
    """交互式回放执行器(蓝图 §8.10)。

    复用 ReactLoop,通过 event_sink 回调同步收集 actual_events(避免 event_queue
    轮询导致的死锁),创建临时评估会话执行 ReAct 循环,执行完毕后清理会话。
    """

    def __init__(
        self,
        *,
        context_manager_cls: type,
        model_adapter: ModelAdapter,
        tool_registry: ToolRegistry,
        mock_data_dir: str | None = None,
    ) -> None:
        self._context_manager_cls = context_manager_cls
        self._model_adapter = model_adapter
        self._tool_registry = tool_registry
        self._mock_data_dir = mock_data_dir

    async def run_replay(
        self,
        *,
        sample: EvalSample,
        skill: Skill,
        model_id: str,
        mock_enabled: bool,
        conn: asyncpg.Connection,
    ) -> tuple[str, list[dict]]:
        """交互式回放(蓝图 §8.10,AC-8, AC-9)。

        流程:
        1. 创建临时评估会话(title="eval-{sample.sample_id}")
        2. 构建 ContextManager + Frozen Zone(skill.system_prompt + tools)
        3. mock_enabled 时 MockToolRegistry.set_sample_id(sample.sample_id)
        4. ReactLoop(event_sink=同步收集回调) + run_turn(sample.input)
        5. event_sink 在 _emit_event 内同步 append,保证事件顺序与产出一致
        6. try/finally 清理临时会话

        Args:
            sample: 评估样本(含 input + sample_id)。
            skill: Skill 实例(含 system_prompt + manifest.dependencies.tools 白名单)。
            model_id: 模型 ID(用于 sessions.model_id)。
            mock_enabled: True 时用 MockToolRegistry 替换 handler。
            conn: asyncpg.Connection。

        Returns:
            (final_output, actual_events) 二元组。
            final_output 从 final 事件 payload.content 提取;error 时为空串。
        """
        session_id = await self._create_eval_session(
            conn, title=f"eval-{sample.sample_id}", model_id=model_id
        )
        try:
            # 构建 ContextManager + Frozen Zone
            ctx: ContextManager = self._context_manager_cls(
                session_id=session_id,
                system_prompt=skill.system_prompt,
                tools=[],
            )
            await ctx.build_initial(conn)

            # 准备工具列表(mock 或真实)
            whitelist = self._extract_tool_whitelist(skill)
            if mock_enabled:
                if self._mock_data_dir is None:
                    raise ValueError(
                        "mock_enabled=True 但 mock_data_dir 未配置(ReplayExecutor 构造函数)"
                    )
                mock_registry = MockToolRegistry(
                    real_registry=self._tool_registry,
                    mock_data_dir=self._mock_data_dir,
                )
                mock_registry.set_sample_id(sample.sample_id)
                tools = mock_registry.list_tools_for_session(whitelist)
            else:
                tools = self._tool_registry.list_tools_for_session(whitelist)

            # event_sink 同步收集 actual_events(在 _emit_event 内调用,顺序有保证)
            actual_events: list[dict] = []

            async def _event_sink(event: dict) -> None:
                actual_events.append(event)

            loop = ReactLoop(
                session_id=session_id,
                context_manager=ctx,
                adapter=self._model_adapter,
                tools=tools,
                conn=conn,
                event_sink=_event_sink,
            )

            # 直接 await run_turn,event_sink 同步收集事件(无死锁风险)
            await loop.run_turn(sample.input)

            # 提取 final_output
            final_output = ""
            for evt in actual_events:
                if evt["event_type"] == "final":
                    final_output = evt["payload"].get("content", "")
                    break

            return final_output, actual_events
        finally:
            # 清理临时会话(异常时也清理)
            await self._delete_session(conn, session_id)

    @staticmethod
    def _extract_tool_whitelist(skill: Skill) -> list[str]:
        """从 skill.manifest.dependencies.tools 提取 enabled 工具名白名单。"""
        return [
            t.name
            for t in skill.manifest.dependencies.tools
            if t.enabled
        ]

    @staticmethod
    async def _create_eval_session(
        conn: asyncpg.Connection,
        *,
        title: str,
        model_id: str = "mock-glm",
    ) -> int:
        """创建临时评估会话(title="eval-" 前缀),返回 session_id。

        封装 SQL(spec Assumptions: MVP 不抽取 SessionRepo,用 helper 方法隔离)。
        """
        return await conn.fetchval(
            "INSERT INTO sessions (title, model_id) VALUES ($1, $2) RETURNING id",
            title,
            model_id,
        )

    @staticmethod
    async def _delete_session(conn: asyncpg.Connection, session_id: int) -> None:
        """删除临时评估会话(含级联 messages)。

        封装 SQL(spec Assumptions: MVP 不抽取 SessionRepo,用 helper 方法隔离)。
        """
        await conn.execute("DELETE FROM sessions WHERE id=$1", session_id)
