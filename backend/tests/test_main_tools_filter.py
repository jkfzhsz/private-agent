"""M3 spec AC-3: main._get_tools 按 session locked_skill 过滤工具。

Source: plan/m3-skills-office step 15
- session 未 activate (locked_skill_name IS NULL) → 返回全部(M1 行为)
- session 已 activate office → 返回 office 白名单内工具
- 用真实 DB + 内置 ToolRegistry,monkeypatch SkillLoader 回退文件系统加载 office skill
"""
import asyncio
import os
from pathlib import Path

import asyncpg
import pytest

from private_agent.storage import db, migrations
import private_agent.main as main_mod

TEST_DSN = os.environ.get(
    "PA_TEST_DSN",
    "postgresql://postgres:123123@localhost:5432/private_agent_test",
)

OFFICE_SKILL_YAML = """\
name: office
version: "1.0.0"
description: "办公场景"
scenario: office
enabled: true
dependencies:
  tools:
    - name: calculator
      safety_level_override: safe
    - name: datetime
      safety_level_override: safe
    - name: http_request
      safety_level_override: elevated
      enabled: false
max_frozen_token: 4000
"""

OFFICE_SYSTEM_PROMPT = "你是办公助手。"


def _setup_schema() -> None:
    """重建 schema + 跑 migrate_all。"""
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


def _create_session(locked_skill: str | None = None) -> int:
    """创建 session,可选锁定 skill。"""
    async def _run() -> int:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            sid = await conn.fetchval(
                "INSERT INTO sessions (title, model_id) VALUES ($1, $2) RETURNING id",
                "test", "mock-glm",
            )
            if locked_skill:
                await conn.execute(
                    "UPDATE sessions SET locked_skill_name=$1, locked_skill_version=$2 "
                    "WHERE id=$3",
                    locked_skill, "1.0.0", sid,
                )
            return sid
        finally:
            await conn.close()

    return asyncio.run(_run())


def _patch_db_connect(monkeypatch) -> None:
    """让 main 中使用的 db.connect 返回指向 TEST_DSN 的真实连接。"""
    async def _fake_connect(cfg=None):
        return await asyncpg.connect(TEST_DSN)

    monkeypatch.setattr(db, "connect", _fake_connect)


def _setup_office_skill_files(tmp_path: Path) -> None:
    """在 tmp_path 下创建 office skill 文件(SkillLoader 文件回退用)。"""
    skill_dir = tmp_path / "office"
    skill_dir.mkdir()
    (skill_dir / "skill.yaml").write_text(OFFICE_SKILL_YAML, encoding="utf-8")
    (skill_dir / "system_prompt.md").write_text(OFFICE_SYSTEM_PROMPT, encoding="utf-8")


class TestGetToolsFiltersByLockedSkill:
    """AC-3: _get_tools 按 session locked_skill 过滤。"""

    def test_unactivated_session_returns_all_tools(self, monkeypatch):
        """session 未 activate → 返回全部内置工具(M1 行为)。"""
        _setup_schema()
        session_id = _create_session(locked_skill=None)
        _patch_db_connect(monkeypatch)

        async def _run():
            conn = await asyncpg.connect(TEST_DSN)
            try:
                cfg = {}
                tools = await main_mod._get_tools(cfg, session_id, conn)
                # 内置工具至少含 calculator/datetime/http_request 等
                names = [t.name for t in tools]
                assert "calculator" in names
                assert "datetime" in names
                assert "http_request" in names
            finally:
                await conn.close()

        asyncio.run(_run())

    def test_activated_session_returns_filtered_tools(self, monkeypatch, tmp_path):
        """session 已 activate office → 仅返回 office 白名单内工具(http_request 排除)。"""
        _setup_schema()
        session_id = _create_session(locked_skill="office")
        _patch_db_connect(monkeypatch)
        _setup_office_skill_files(tmp_path)

        # monkeypatch SkillLoader 的 dev_dir 指向 tmp_path
        from private_agent.skills.loader import SkillLoader
        def _fake_from_cfg(cfg):
            return SkillLoader(dev_dir=str(tmp_path))
        monkeypatch.setattr(SkillLoader, "from_cfg", _fake_from_cfg)

        async def _run():
            conn = await asyncpg.connect(TEST_DSN)
            try:
                cfg = {}
                tools = await main_mod._get_tools(cfg, session_id, conn)
                names = [t.name for t in tools]
                assert "calculator" in names
                assert "datetime" in names
                # http_request enabled=false,应被过滤掉
                assert "http_request" not in names
            finally:
                await conn.close()

        asyncio.run(_run())
