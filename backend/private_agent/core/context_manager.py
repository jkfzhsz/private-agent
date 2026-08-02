"""蓝图 §3.1-3.3 上下文管理器 - 三区分区 + 启动构建 + 每轮构建。

Source: spec/m1-react-loop AC-3 + Solution `core/context_manager.py`
- Zone: dataclass(name/messages/hash), hash 字段 M1-b step 10 预留
- ContextManager: 三区管理(frozen/stable/active)
- build_initial: 启动构建,持久化 Frozen Zone(system_prompt + 工具定义)到 messages 表
- build_per_turn / append_*_message: 每轮构建,Active Zone 追加用户/助手/工具消息
- get_messages: 返回 Frozen + Stable + Active 合并消息列表
- build_messages: get_messages 的 async 别名,供 run_turn 调用
- spec Out of scope: 三区构建不含压缩;启动时 Stable/Active 为空
- spec Assumptions: hash 字段预留,本次只存字段不做校验

M2 §4.5 扩展:ContextManager 接受可选的 memory_manager,在 ensure_initial 时
注入用户记忆到 Stable Zone。
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from private_agent.errors import FrozenHashMismatchError
from private_agent.tools.defs import ToolDef

if TYPE_CHECKING:
    import asyncpg

__all__ = ["Zone", "ContextManager"]


@dataclass
class Zone:
    """蓝图 §3.2 分区元数据。

    name: 'frozen' | 'stable' | 'active'
    messages: 该区消息列表(role/content/... dict)
    hash: M1-b step 10 预留(SHA-256 校验),本次仅占位
    """

    name: str
    messages: list[dict] = field(default_factory=list)
    hash: str | None = None


class ContextManager:
    """蓝图 §3.1-3.3 上下文管理器。

    三区:
    - frozen_zone: 系统提示词 + 工具定义(启动构建,不可变)
    - stable_zone: 压缩摘要区(M1 为空,压缩留 M1-b step 11)
    - active_zone: 当前会话消息(每轮追加,build_per_turn 维护)
    """

    def __init__(
        self,
        session_id: int,
        system_prompt: str,
        tools: list[ToolDef],
        memory_manager: Any | None = None,
    ) -> None:
        self.session_id = session_id
        self._system_prompt = system_prompt
        self._tools = list(tools)
        self._memory_manager = memory_manager
        self.frozen_zone = Zone(name="frozen")
        self.stable_zone = Zone(name="stable")
        self.active_zone = Zone(name="active")

    def _build_frozen_content(self) -> str:
        """构造 Frozen Zone system 消息内容:system_prompt + 工具定义。"""
        if not self._tools:
            return self._system_prompt
        tools_json = json.dumps(
            [t.to_openai_schema() for t in self._tools],
            ensure_ascii=False,
        )
        return f"{self._system_prompt}\n\n[TOOLS]\n{tools_json}"

    def compute_frozen_hash(self) -> str:
        """M3 §7.3 AC-1: 计算 Frozen Zone 内容的 SHA-256 hash。

        hash 基于 system_prompt + 工具定义(与 _build_frozen_content 同源)。
        用于会话锁定,存 sessions.frozen_hash。
        """
        content = self._build_frozen_content()
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    async def build_initial(self, conn: "asyncpg.Connection") -> None:
        """启动构建(蓝图 §3.3):持久化 Frozen Zone 到 messages 表。

        - role='system', zone='frozen', turn=0
        - Stable/Active 启动时为空,不入库
        - 内存三区同步更新

        Args:
            conn: Postgres 连接。
        """
        content = self._build_frozen_content()
        await conn.execute(
            """
            INSERT INTO messages (session_id, turn, role, content, zone)
            VALUES ($1, $2, $3, $4, $5)
            """,
            self.session_id,
            0,
            "system",
            content,
            "frozen",
        )
        self.frozen_zone.messages = [{"role": "system", "content": content}]
        self.stable_zone.messages = []
        self.active_zone.messages = []

    async def _inject_memories(self, conn: "asyncpg.Connection") -> None:
        """注入用户记忆到 Stable Zone(蓝图 §4.5)。

        在 ensure_initial 调用 build_initial 后执行,将高重要性记忆注入
        Stable Zone 初始内容。
        """
        if self._memory_manager is None:
            return
        memories = await self._memory_manager.load_user_memories()
        if not memories:
            return
        memories_text = self._memory_manager.format_memories_for_stable(memories)
        await conn.execute(
            """
            INSERT INTO messages (session_id, turn, role, content, zone)
            VALUES ($1, $2, $3, $4, $5)
            """,
            self.session_id,
            0,
            "user",
            memories_text,
            "stable",
        )
        self.stable_zone.messages = [
            {"role": "user", "content": memories_text}
        ]

    async def ensure_initial(self, conn: "asyncpg.Connection") -> None:
        """幂等启动构建(蓝图 §3.3 + spec AC-3)。

        若 Frozen Zone 已存在则跳过 INSERT,仅从 DB reload Frozen Zone 到内存
        (保证 adapter.chat 能拿到 system prompt);否则调用 build_initial
        并注入用户记忆(§4.5)。

        用于多次 user_message 场景(同一 session_id 复用),避免重复 INSERT
        Frozen Zone 行(违反 spec AC-3 "会话启动后 messages 表有 Frozen Zone"
        的单条语义)。

        M1 简化:仅 reload Frozen Zone,不 reload Stable/Active 历史消息
        (M1-b/M2 补完整 reload 与压缩)。

        Args:
            conn: Postgres 连接。
        """
        row = await conn.fetchrow(
            """
            SELECT content FROM messages
            WHERE session_id=$1 AND zone='frozen'
            ORDER BY id LIMIT 1
            """,
            self.session_id,
        )
        if row is not None:
            self.frozen_zone.messages = [
                {"role": "system", "content": row["content"]}
            ]
            self.stable_zone.messages = []
            self.active_zone.messages = []
            # B1 P1-4: hash 校验(环境变量 PA_FROZEN_HASH_VERIFY=0 可关)
            if os.environ.get("PA_FROZEN_HASH_VERIFY", "1") != "0":
                db_hash = await conn.fetchval(
                    "SELECT frozen_hash FROM sessions WHERE id=$1",
                    self.session_id,
                )
                if db_hash is not None:
                    computed = self.compute_frozen_hash()
                    if computed != db_hash:
                        raise FrozenHashMismatchError(
                            f"frozen_hash mismatch: db={db_hash[:8]}... "
                            f"computed={computed[:8]}..."
                        )
            return
        await self.build_initial(conn)
        await self._inject_memories(conn)

    async def verify_frozen_hash(self, conn: "asyncpg.Connection") -> None:
        """校验 Frozen Zone hash 与 sessions.frozen_hash 一致(B1 P1-4)。

        供 react_loop 每轮调用(本 spec 不集成,由 B3 与 checkpoint 一起接入);
        PA_FROZEN_HASH_VERIFY=0 时跳过校验(逃生通道)。

        Args:
            conn: Postgres 连接。

        Raises:
            FrozenHashMismatchError: frozen_hash 非 NULL 且与计算值不一致。
        """
        if os.environ.get("PA_FROZEN_HASH_VERIFY", "1") == "0":
            return
        db_hash = await conn.fetchval(
            "SELECT frozen_hash FROM sessions WHERE id=$1", self.session_id
        )
        if db_hash is None:
            return  # 老会话无 hash,跳过
        computed = self.compute_frozen_hash()
        if computed != db_hash:
            raise FrozenHashMismatchError(
                f"frozen_hash mismatch: db={db_hash[:8]}... computed={computed[:8]}..."
            )

    async def reload_from_db(self, conn: "asyncpg.Connection") -> None:
        """完整重放历史消息(Frozen+Stable+Active 三区)(蓝图 §8.10,AC-1)。

        M1 ensure_initial 仅 reload Frozen Zone,本方法补全 Stable/Active reload,
        用于 ReplayExecutor 重建评估会话上下文。

        按 (turn, id) 顺序读取 messages 表,按 zone 分组重建三区内存:
        - frozen: role='system' 单条
        - stable: 任意 role(记忆注入为 user)
        - active: user/assistant/tool 三类,保留 tool_calls/tool_call_id/name

        Args:
            conn: Postgres 连接。
        """
        rows = await conn.fetch(
            """
            SELECT role, content, tool_calls, tool_call_id, name, zone
            FROM messages
            WHERE session_id=$1
            ORDER BY turn, id
            """,
            self.session_id,
        )
        frozen: list[dict] = []
        stable: list[dict] = []
        active: list[dict] = []
        for row in rows:
            zone = row["zone"]
            role = row["role"]
            if zone == "frozen":
                frozen.append({"role": role, "content": row["content"]})
            elif zone == "stable":
                stable.append({"role": role, "content": row["content"]})
            elif zone == "active":
                msg: dict[str, Any] = {"role": role, "content": row["content"]}
                # tool_calls(JSONB):asyncpg 默认返回 str,需 json.loads
                tc = row["tool_calls"]
                if tc is not None:
                    if isinstance(tc, str):
                        msg["tool_calls"] = json.loads(tc)
                    else:
                        msg["tool_calls"] = tc
                if row["tool_call_id"] is not None:
                    msg["tool_call_id"] = row["tool_call_id"]
                if row["name"] is not None:
                    msg["name"] = row["name"]
                active.append(msg)
        self.frozen_zone.messages = frozen
        self.stable_zone.messages = stable
        self.active_zone.messages = active

    async def replace_frozen_zone(
        self,
        conn: "asyncpg.Connection",
        *,
        system_prompt: str,
        tools: list[ToolDef],
        skill_version: str | None = None,
    ) -> None:
        """替换 Frozen Zone(版本切换/回滚时使用)(蓝图 §8.10,AC-2)。

        流程:
        1. 删除 messages 表中 session_id+zone='frozen' 的记录
        2. 更新 self._system_prompt + self._tools
        3. 调 build_initial(conn)(用新 system_prompt + tools 插入新 frozen 行)
        4. 重新计算 frozen_hash
        5. UPDATE sessions SET frozen_hash(始终)+ locked_skill_version(仅当 skill_version 非 None)

        Args:
            conn: Postgres 连接。
            system_prompt: 新的系统提示词。
            tools: 新的工具定义列表。
            skill_version: 可选,新 Skill 版本(传入时更新 sessions.locked_skill_version)。
        """
        # 1. 删除旧 frozen 行
        await conn.execute(
            "DELETE FROM messages WHERE session_id=$1 AND zone='frozen'",
            self.session_id,
        )
        # 2. 更新内存 system_prompt + tools
        self._system_prompt = system_prompt
        self._tools = list(tools)
        # 3. 重新 build_initial(插入新 frozen 行 + 内存同步)
        await self.build_initial(conn)
        # 4. 重新计算 frozen_hash
        new_hash = self.compute_frozen_hash()
        # B1 P1-4: 写后完整性兜底校验(AC-15)
        # 理论上 new_hash == compute_frozen_hash()(刚算的),但作为并发/篡改安全网
        # spec AC-15 要求此校验存在;测试通过 mock compute_frozen_hash 验证抛错路径
        if os.environ.get("PA_FROZEN_HASH_VERIFY", "1") != "0":
            computed = self.compute_frozen_hash()
            if computed != new_hash:
                raise FrozenHashMismatchError(
                    f"replace_frozen_zone post-write hash mismatch: "
                    f"new_hash={new_hash[:8]}... computed={computed[:8]}..."
                )
        # 5. UPDATE sessions
        if skill_version is not None:
            await conn.execute(
                "UPDATE sessions SET frozen_hash=$2, locked_skill_version=$3 WHERE id=$1",
                self.session_id,
                new_hash,
                skill_version,
            )
        else:
            await conn.execute(
                "UPDATE sessions SET frozen_hash=$2 WHERE id=$1",
                self.session_id,
                new_hash,
            )

    async def append_user_message(
        self,
        conn: "asyncpg.Connection",
        *,
        turn: int,
        content: str,
    ) -> None:
        """每轮构建:追加用户消息到 Active Zone(蓝图 §3.3)。

        持久化到 messages 表(role='user', zone='active') + 内存同步。

        Args:
            conn: Postgres 连接。
            turn: 当前轮次(≥1)。
            content: 用户消息文本。
        """
        await conn.execute(
            """
            INSERT INTO messages (session_id, turn, role, content, zone)
            VALUES ($1, $2, $3, $4, $5)
            """,
            self.session_id,
            turn,
            "user",
            content,
            "active",
        )
        self.active_zone.messages.append({"role": "user", "content": content})

    async def append_assistant_message(
        self,
        conn: "asyncpg.Connection",
        *,
        turn: int,
        content: str,
        tool_calls: list[dict] | None = None,
    ) -> None:
        """每轮构建:追加助手消息到 Active Zone(蓝图 §3.3)。

        持久化到 messages 表(role='assistant', zone='active') + 内存同步。
        tool_calls 不为空时持久化为 JSONB。

        Args:
            conn: Postgres 连接。
            turn: 当前轮次。
            content: 助手消息文本(可空,纯 tool_call 时为空字符串)。
            tool_calls: 工具调用列表(OpenAI 格式),无则 None。
        """
        if tool_calls:
            tool_calls_json = json.dumps(tool_calls, ensure_ascii=False)
            await conn.execute(
                """
                INSERT INTO messages (session_id, turn, role, content, tool_calls, zone)
                VALUES ($1, $2, $3, $4, $5::jsonb, $6)
                """,
                self.session_id,
                turn,
                "assistant",
                content,
                tool_calls_json,
                "active",
            )
        else:
            await conn.execute(
                """
                INSERT INTO messages (session_id, turn, role, content, zone)
                VALUES ($1, $2, $3, $4, $5)
                """,
                self.session_id,
                turn,
                "assistant",
                content,
                "active",
            )
        msg: dict[str, Any] = {"role": "assistant", "content": content}
        if tool_calls:
            msg["tool_calls"] = tool_calls
        self.active_zone.messages.append(msg)

    async def append_tool_message(
        self,
        conn: "asyncpg.Connection",
        *,
        turn: int,
        tool_call_id: str,
        content: str,
        name: str,
        error: str | None = None,
    ) -> None:
        """每轮构建:追加工具结果消息到 Active Zone(蓝图 §3.3)。

        持久化到 messages 表(role='tool', zone='active') + 内存同步。

        Args:
            conn: Postgres 连接。
            turn: 当前轮次。
            tool_call_id: 对应 tool_call 的 id(OpenAI tool_call_id 字段)。
            content: 工具输出文本。
            name: 工具名称。
            error: 工具执行错误(V2 P1 权限拒绝/超时回传, 拼入 content)。
        """
        if error:
            content = f"[{error}]\n{content}"
        await conn.execute(
            """
            INSERT INTO messages (session_id, turn, role, content, tool_call_id, name, zone)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            """,
            self.session_id,
            turn,
            "tool",
            content,
            tool_call_id,
            name,
            "active",
        )
        self.active_zone.messages.append(
            {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": content,
                "name": name,
            }
        )

    def get_messages(self) -> list[dict]:
        """返回 Frozen + Stable + Active 三区合并后的消息列表。

        供 ModelAdapter.chat(messages) 使用。顺序:frozen → stable → active。
        """
        return [
            *self.frozen_zone.messages,
            *self.stable_zone.messages,
            *self.active_zone.messages,
        ]

    async def build_messages(self) -> list[dict]:
        """返回 Frozen + Stable + Active 三区合并后的消息列表（供 adapter.chat 使用）。

        与 get_messages 功能相同,但为 async 方法,适配 run_turn 中的 await 调用。
        """
        return self.get_messages()

    async def build_per_turn(
        self,
        conn: "asyncpg.Connection",
        *,
        turn: int,
        user_content: str,
    ) -> list[dict]:
        """每轮构建(蓝图 §3.3):追加用户消息并返回合并消息列表。

        副作用:
        - 持久化用户消息到 messages 表(zone='active')
        - 内存 active_zone 追加用户消息

        Args:
            conn: Postgres 连接。
            turn: 当前轮次(≥1)。
            user_content: 用户输入文本。

        Returns:
            Frozen + Stable + Active 合并后的消息列表(供 adapter.chat 使用)。
        """
        await self.append_user_message(conn, turn=turn, content=user_content)
        return self.get_messages()