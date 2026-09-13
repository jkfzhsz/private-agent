"""会议室约定注入 system prompt —— W3 集成测试(设计文档 §4.3/§4.4)。

覆盖:
1. 房间会话(kind='room') → 提示词含主持人视角的「会议室约定」;
2. 成员子会话(kind='sub' 且继承 room_meta) → 提示词含成员视角约定,
   **不得**出现委派指引(子代理嵌套深度恒 1, 指引会诱发失败调用);
3. 普通会话(room_meta 为 NULL) → 提示词**完全不含**会议室约定(零回归);
4. 会话行缺失 → 不抛异常(读路径健壮性)。

注入位置在 `_get_system_prompt` 末尾; 触发条件是 `sessions.room_meta` 非空,
而非 kind —— 这样主持人与成员两条路径共用同一段文本生成函数
(`core.room.build_room_contract`), 只有视角不同。
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import asyncpg
import pytest

from private_agent.storage import migrations

TEST_DSN = os.environ.get(
    "PA_TEST_DSN",
    "postgresql://postgres:123123@localhost:5432/private_agent_test",
)

#: 真实技能目录(不随 CWD 变化)。
REAL_SKILLS_DIR = Path(__file__).resolve().parents[1] / "skills"

ROOM_DIR = "D:/PA/rooms/20260911-153012-abcd"
ROOM_META = {
    "host_role": "office",
    "members": ["office", "frontend_design"],
    "goal": "做一份 Q3 经营分析汇报 PPT",
    "room_key": "20260911-153012-abcd",
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


@pytest.fixture
def patched_loader(monkeypatch):
    """SkillLoader.from_cfg → dev_dir 指向真实 skills 目录(与 CWD 无关)。"""
    from private_agent.skills.loader import SkillLoader

    def _fake_from_cfg(cfg):
        return SkillLoader(dev_dir=str(REAL_SKILLS_DIR))

    monkeypatch.setattr(SkillLoader, "from_cfg", _fake_from_cfg)


def _insert_session(
    *,
    kind: str,
    locked_skill: str | None,
    workspace: str | None,
    room_meta: dict | None = None,
) -> int:
    async def _run() -> int:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            return await conn.fetchval(
                "INSERT INTO sessions (title, kind, locked_skill_name, "
                "workspace, room_meta) "
                "VALUES ('t', $1, $2, $3, $4::jsonb) RETURNING id",
                kind,
                locked_skill,
                workspace,
                json.dumps(room_meta, ensure_ascii=False) if room_meta else None,
            )
        finally:
            await conn.close()

    return asyncio.run(_run())


def _get_prompt(session_id: int) -> str:
    import private_agent.main as main_mod

    async def _run() -> str:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            return await main_mod._get_system_prompt({}, session_id, conn)
        finally:
            await conn.close()

    return asyncio.run(_run())


class TestRoomSessionPrompt:
    def test_host_prompt_carries_room_contract(self, patched_loader):
        _setup_schema()
        sid = _insert_session(
            kind="room",
            locked_skill="office",
            workspace=ROOM_DIR,
            room_meta=ROOM_META,
        )
        prompt = _get_prompt(sid)

        assert "[会议室约定]" in prompt
        assert ROOM_DIR in prompt
        assert f"{ROOM_DIR}/artifacts/" in prompt
        assert f"{ROOM_DIR}/notes/" in prompt
        assert "做一份 Q3 经营分析汇报 PPT" in prompt
        # 主持人视角
        assert "主持人" in prompt
        assert "delegate_subtask" in prompt
        assert "subtasks[].role" in prompt
        assert "你是本次会议的**成员**" not in prompt

    def test_contract_follows_runtime_guidelines(self, patched_loader):
        """约定须在运行时约定之后(先讲工作区根, 再讲房间目录分工)。"""
        _setup_schema()
        sid = _insert_session(
            kind="room",
            locked_skill="office",
            workspace=ROOM_DIR,
            room_meta=ROOM_META,
        )
        prompt = _get_prompt(sid)
        assert prompt.index("[运行时约定]") < prompt.index("[会议室约定]")


class TestMemberPrompt:
    def test_member_prompt_carries_member_view(self, patched_loader):
        _setup_schema()
        sid = _insert_session(
            kind="sub",
            locked_skill="frontend_design",
            workspace=ROOM_DIR,
            room_meta=ROOM_META,
        )
        prompt = _get_prompt(sid)

        assert "[会议室约定]" in prompt
        assert f"{ROOM_DIR}/artifacts/" in prompt
        assert "**成员**" in prompt
        assert "清和(frontend_design)" in prompt
        assert "主持人（子瞻(office)）" in prompt

    def test_member_prompt_has_no_delegation_guidance(self, patched_loader):
        """成员不得被赋予委派指引(嵌套深度恒 1)。"""
        _setup_schema()
        sid = _insert_session(
            kind="sub",
            locked_skill="frontend_design",
            workspace=ROOM_DIR,
            room_meta=ROOM_META,
        )
        prompt = _get_prompt(sid)
        assert "delegate_subtask" not in prompt


class TestZeroRegression:
    def test_plain_session_has_no_room_contract(self, patched_loader):
        """普通场景会话(room_meta 为 NULL) → 零影响。"""
        _setup_schema()
        sid = _insert_session(
            kind="main",
            locked_skill="office",
            workspace=None,
            room_meta=None,
        )
        prompt = _get_prompt(sid)
        assert "[会议室约定]" not in prompt
        # 运行时约定仍照常注入(未被本次改动破坏)
        assert "[运行时约定]" in prompt

    def test_plain_sub_session_has_no_room_contract(self, patched_loader):
        _setup_schema()
        sid = _insert_session(
            kind="sub",
            locked_skill="office",
            workspace="D:/PA/zizhan",
            room_meta=None,
        )
        assert "[会议室约定]" not in _get_prompt(sid)

    def test_missing_session_does_not_raise(self, patched_loader):
        """会话行缺失时不抛异常(读路径健壮性), 且无房间约定。"""
        _setup_schema()
        prompt = _get_prompt(999999)
        assert isinstance(prompt, str)
        assert "[会议室约定]" not in prompt
