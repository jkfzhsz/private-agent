"""M4 m4-version-compare-rollback AC-3, AC-4, AC-5 - SkillRollbackManager 测试。

Source: spec/m4-version-compare-rollback AC-3, AC-4, AC-5 + plan step 10
- AC-3: rollback_prompt 仅回滚 Prompt,不影响工具白名单
- AC-4: rollback_skill 回滚整个 Skill,新会话加载 target_version
- AC-5: rollback_harness 返回 git revert 命令,不自动执行
"""
import asyncio
import json
import os
from unittest.mock import MagicMock

import asyncpg
import pytest

from private_agent.eval.repos import VersionSnapshotRepo
from private_agent.eval.rollback import SkillRollbackManager, VersionNotFoundError
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


async def _insert_skill_row(
    conn: "asyncpg.Connection",
    *,
    name: str = "office",
    version: str = "1.1.0",
    manifest: dict | None = None,
    system_prompt: str = "v1.1 prompt",
    tools: list | None = None,
) -> int:
    """插入 skills 表行。"""
    if manifest is None:
        manifest = {
            "name": name,
            "version": version,
            "scenario": "office",
            "dependencies": {"tools": [{"name": "file_read"}, {"name": "web_search"}]},
        }
    if tools is None:
        tools = [{"name": "file_read"}, {"name": "web_search"}]
    return await conn.fetchval(
        """
        INSERT INTO skills (name, version, manifest, system_prompt, tools)
        VALUES ($1, $2, $3::jsonb, $4, $5::jsonb)
        RETURNING id
        """,
        name,
        version,
        json.dumps(manifest),
        system_prompt,
        json.dumps(tools),
    )


async def _insert_snapshot(
    conn: "asyncpg.Connection",
    *,
    scope: str,
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


def test_rollback_prompt_only_updates_prompt_pointer():
    """AC-3: rollback_prompt 仅回滚 Prompt,不影响工具白名单。"""
    _setup_schema()

    async def _run() -> dict:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            # 当前 skills 表:office v1.1.0,工具白名单 [file_read, web_search]
            await _insert_skill_row(
                conn, name="office", version="1.1.0", system_prompt="v1.1 prompt"
            )
            # 历史快照:office v1.0.0 的 prompt(scope=prompt)
            await _insert_snapshot(
                conn,
                scope="prompt",
                version="1.0.0",
                payload={"skill_name": "office", "system_prompt": "v1.0 old prompt"},
            )
            repo = VersionSnapshotRepo(conn)
            skill_loader = MagicMock()
            manager = SkillRollbackManager(
                snapshot_repo=repo, skill_loader=skill_loader
            )
            result = await manager.rollback_prompt(
                skill_name="office", target_version="1.0.0", conn=conn
            )
            # 校验 skills 表 system_prompt 已更新,tools 不变
            row = await conn.fetchrow(
                "SELECT system_prompt, tools FROM skills WHERE name=$1", "office"
            )
            return {
                "result": result,
                "system_prompt": row["system_prompt"],
                "tools": json.loads(row["tools"]),
            }
        finally:
            await conn.close()

    out = asyncio.run(_run())
    assert out["result"]["rolled_back_to"] == "1.0.0"
    assert out["result"]["scope"] == "prompt"
    assert out["result"]["affected_sessions"] == 0
    # system_prompt 已回滚
    assert out["system_prompt"] == "v1.0 old prompt"
    # 工具白名单不变(AC-3 核心)
    assert out["tools"] == [{"name": "file_read"}, {"name": "web_search"}]


def test_rollback_skill_updates_version_and_tools():
    """AC-4: rollback_skill 回滚整个 Skill(元数据 + Prompt + 工具白名单)。"""
    _setup_schema()

    async def _run() -> dict:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            # 当前 skills 表:office v1.1.0
            await _insert_skill_row(
                conn, name="office", version="1.1.0", system_prompt="v1.1 prompt"
            )
            # 历史快照:office v1.0.0 的完整 Skill
            await _insert_snapshot(
                conn,
                scope="skill",
                version="1.0.0",
                payload={
                    "skill_name": "office",
                    "manifest": {
                        "name": "office",
                        "version": "1.0.0",
                        "scenario": "office",
                        "dependencies": {
                            "tools": [{"name": "file_read"}]  # 仅 file_read
                        },
                    },
                    "system_prompt": "v1.0 full prompt",
                    "tools_yaml": [{"name": "file_read"}],
                },
            )
            repo = VersionSnapshotRepo(conn)
            skill_loader = MagicMock()
            manager = SkillRollbackManager(
                snapshot_repo=repo, skill_loader=skill_loader
            )
            result = await manager.rollback_skill(
                skill_name="office", target_version="1.0.0", conn=conn
            )
            row = await conn.fetchrow(
                "SELECT version, system_prompt, tools FROM skills WHERE name=$1",
                "office",
            )
            latest_version = await conn.fetchval(
                "SELECT value #>> '{}' FROM config_runtime "
                "WHERE key=$1",
                "skill.office.latest_version",
            )
            return {
                "result": result,
                "version": row["version"],
                "system_prompt": row["system_prompt"],
                "tools": json.loads(row["tools"]),
                "latest_version": latest_version,
            }
        finally:
            await conn.close()

    out = asyncio.run(_run())
    assert out["result"]["rolled_back_to"] == "1.0.0"
    assert out["result"]["scope"] == "skill"
    assert out["result"]["affected_sessions"] == 0
    # skills 表已更新
    assert out["version"] == "1.0.0"
    assert out["system_prompt"] == "v1.0 full prompt"
    assert out["tools"] == [{"name": "file_read"}]
    # config_runtime 指针已设(AC-4 后半:新会话加载 target_version)
    assert out["latest_version"] == "1.0.0"


def test_rollback_raises_when_snapshot_not_found():
    """AC-3/4: 回滚版本不存在时抛 VersionNotFoundError。"""
    _setup_schema()

    async def _run():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            await _insert_skill_row(conn, name="office", version="1.1.0")
            repo = VersionSnapshotRepo(conn)
            skill_loader = MagicMock()
            manager = SkillRollbackManager(
                snapshot_repo=repo, skill_loader=skill_loader
            )
            await manager.rollback_skill(
                skill_name="office",
                target_version="0.0.0",  # 不存在
                conn=conn,
            )
        finally:
            await conn.close()

    with pytest.raises(VersionNotFoundError):
        asyncio.run(_run())


def test_rollback_harness_returns_command_without_execution():
    """AC-5: rollback_harness 返回 git revert 命令,不自动执行。"""
    repo = VersionSnapshotRepo.__new__(VersionSnapshotRepo)
    skill_loader = MagicMock()
    manager = SkillRollbackManager(snapshot_repo=repo, skill_loader=skill_loader)
    result = manager.rollback_harness(target_commit="abc123")
    assert result["command"] == "git revert abc123"
    assert "手动执行" in result["note"]
    # 不自动执行的标志:返回值是命令字符串,无 executed 字段
    assert "executed" not in result
