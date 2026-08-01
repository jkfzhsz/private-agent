"""M4 m4-eval-runner-replay AC-3 - SkillLoader.load_version 测试。

Source: spec/m4-eval-runner-replay AC-3 + plan step 4, step 12
- load_version 从 version_snapshots 表读 scope='skill' + version 的 payload
- payload 反序列化为 Skill 模型
- 版本不存在抛 SkillNotFoundError
- skill_name 不匹配抛 SkillNotFoundError(防止版本号全局唯一约束下的误读)
"""
import asyncio
import json
import os

import asyncpg
import pytest

from private_agent.skills.errors import SkillNotFoundError
from private_agent.skills.loader import SkillLoader
from private_agent.skills.models import Skill, SkillManifest
from private_agent.storage import migrations

TEST_DSN = os.environ.get(
    "PA_TEST_DSN",
    "postgresql://postgres:123123@localhost:5432/private_agent_test",
)


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


def _make_skill_payload(
    name: str = "office",
    version: str = "0.1.0",
    system_prompt: str = "You are an office assistant.",
) -> dict:
    """构造 version_snapshots.payload(skill scope 的序列化 Skill)。"""
    return {
        "manifest": {
            "name": name,
            "version": version,
            "description": "test skill",
            "scenario": "office",
            "author": "test",
            "created_at": "2026-08-01",
            "enabled": True,
            "dependencies": {"tools": []},
            "permissions": {"allow_file_write": False, "allow_network": False, "sandbox_enabled": False, "max_file_size_mb": 50},
            "prompt_vars": [],
            "knowledge_base": {"enabled": False, "scenario": None, "auto_retrieve": False},
            "examples": {"enabled": True, "max_examples": 3, "inject_to": "frozen_zone"},
            "max_frozen_token": 4000,
        },
        "system_prompt": system_prompt,
        "tools_yaml": [],
    }


async def _insert_snapshot(
    conn: asyncpg.Connection,
    *,
    scope: str = "skill",
    version: str,
    payload: dict,
) -> int:
    """插入 version_snapshots 行。"""
    return await conn.fetchval(
        """
        INSERT INTO version_snapshots (scope, version, payload)
        VALUES ($1, $2, $3::jsonb)
        RETURNING id
        """,
        scope,
        version,
        json.dumps(payload),
    )


def test_load_version_returns_skill_from_snapshot():
    """load_version 从 version_snapshots 读 payload 返回 Skill(AC-3)。"""
    _setup_schema()

    async def _run() -> Skill:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            payload = _make_skill_payload(name="office", version="0.1.0", system_prompt="office-prompt")
            await _insert_snapshot(conn, version="0.1.0", payload=payload)

            loader = SkillLoader()
            skill = await loader.load_version("office", "0.1.0", conn)
            return skill
        finally:
            await conn.close()

    skill = asyncio.run(_run())
    assert isinstance(skill, Skill)
    assert skill.manifest.name == "office"
    assert skill.manifest.version == "0.1.0"
    assert skill.manifest.scenario == "office"
    assert skill.system_prompt == "office-prompt"
    assert skill.tools_yaml == []


def test_load_version_raises_when_version_not_found():
    """load_version 版本不存在抛 SkillNotFoundError(AC-3)。"""
    _setup_schema()

    async def _run() -> Skill:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            loader = SkillLoader()
            # 数据库无任何 version_snapshots 记录
            skill = await loader.load_version("office", "9.9.9", conn)
            return skill
        finally:
            await conn.close()

    with pytest.raises(SkillNotFoundError):
        asyncio.run(_run())


def test_load_version_raises_when_skill_name_mismatch():
    """load_version skill_name 与 payload.manifest.name 不匹配抛 SkillNotFoundError(AC-3)。

    防止 version_snapshots 的 UNIQUE(scope, version) 约束下,
    用 A skill 的 version 误读 B skill。
    """
    _setup_schema()

    async def _run() -> Skill:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            # 插入 name="office" 的快照
            payload = _make_skill_payload(name="office", version="0.1.0")
            await _insert_snapshot(conn, version="0.1.0", payload=payload)

            loader = SkillLoader()
            # 用 "data_analysis" 名字去读 office 的 version
            skill = await loader.load_version("data_analysis", "0.1.0", conn)
            return skill
        finally:
            await conn.close()

    with pytest.raises(SkillNotFoundError):
        asyncio.run(_run())
