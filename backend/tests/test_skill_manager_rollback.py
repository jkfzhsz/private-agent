"""M4 m4-version-compare-rollback AC-4 后半 - SkillManager 回滚集成测试。

Source: spec/m4-version-compare-rollback AC-4 + plan step 13
- AC-4 后半: rollback_skill 后新会话 activate_skill 加载 target_version
- 运行中会话(locked_skill_version 已设)维持锁定版本
"""
import asyncio
import json
import os
from pathlib import Path
from unittest.mock import MagicMock

import asyncpg
import pytest

from private_agent.eval.repos import VersionSnapshotRepo
from private_agent.eval.rollback import SkillRollbackManager
from private_agent.skills.example_loader import ExampleLoader
from private_agent.skills.loader import SkillLoader
from private_agent.skills.manager import SkillManager
from private_agent.storage import migrations
from private_agent.tools.builtins import register_all_builtins
from private_agent.tools.registry import ToolRegistry

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


def _make_skill_files(tmp_path: Path, version: str = "1.1.0") -> Path:
    """创建 office skill 文件(v1.1.0)。"""
    skill_dir = tmp_path / "office"
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "skill.yaml").write_text(
        f"""
name: office
version: "{version}"
description: office skill
scenario: office
dependencies:
  tools:
    - name: file_read
""",
        encoding="utf-8",
    )
    (skill_dir / "system_prompt.md").write_text(
        f"office v{version} prompt", encoding="utf-8"
    )
    (skill_dir / "tools.yaml").write_text(
        "- name: file_read\n", encoding="utf-8"
    )
    examples_dir = skill_dir / "examples" / "train"
    examples_dir.mkdir(parents=True, exist_ok=True)
    (examples_dir / "example_001.md").write_text(
        "# Example\nUser: hi", encoding="utf-8"
    )
    return skill_dir


def test_activate_skill_uses_latest_version_pointer_after_rollback(tmp_path):
    """AC-4 后半: rollback_skill 后新会话 activate_skill 加载 target_version。"""
    _setup_schema()
    _make_skill_files(tmp_path, version="1.1.0")  # 文件系统当前版本

    async def _run() -> dict:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            # 1. 插入 skills 表 v1.1.0
            await conn.execute(
                """
                INSERT INTO skills (name, version, manifest, system_prompt, tools)
                VALUES ('office', '1.1.0', $1::jsonb, 'v1.1', '[]'::jsonb)
                """,
                json.dumps({"name": "office", "version": "1.1.0", "scenario": "office"}),
            )
            # 2. 插入 v1.0.0 历史 snapshot
            await conn.execute(
                """
                INSERT INTO version_snapshots (scope, version, payload)
                VALUES ('skill', '1.0.0', $1::jsonb)
                """,
                json.dumps(
                    {
                        "manifest": {
                            "name": "office",
                            "version": "1.0.0",
                            "scenario": "office",
                            "dependencies": {"tools": [{"name": "file_read"}]},
                        },
                        "system_prompt": "v1.0 old prompt",
                        "tools_yaml": [{"name": "file_read"}],
                    }
                ),
            )
            # 3. 创建新 session
            session_id = await conn.fetchval(
                "INSERT INTO sessions (title, status) VALUES ('test', 'active') RETURNING id"
            )
            # 4. 执行回滚到 v1.0.0
            repo = VersionSnapshotRepo(conn)
            manager_rollback = SkillRollbackManager(snapshot_repo=repo)
            await manager_rollback.rollback_skill(
                skill_name="office", target_version="1.0.0", conn=conn
            )
            # 5. 新会话 activate_skill — 应加载 v1.0.0(latest_version 指针)
            loader = SkillLoader(dev_dir=str(tmp_path))
            example_loader = ExampleLoader.from_cfg(
                {"skills": {"dev_dir": str(tmp_path), "examples": {"enabled": False}}}
            )
            registry = ToolRegistry()
            register_all_builtins(registry)
            mgr = SkillManager(
                loader=loader, example_loader=example_loader, tool_registry=registry
            )
            result = await mgr.activate_skill(
                skill_name="office", session_id=session_id, conn=conn
            )
            # 验证 sessions 表的 locked_skill_version
            locked_version = await conn.fetchval(
                "SELECT locked_skill_version FROM sessions WHERE id=$1", session_id
            )
            return {
                "activate_result": result,
                "locked_version": locked_version,
            }
        finally:
            await conn.close()

    out = asyncio.run(_run())
    # 新会话加载 v1.0.0(latest_version 指针指向 1.0.0)
    assert out["locked_version"] == "1.0.0"
    assert out["activate_result"]["locked_version"] == "1.0.0"


def test_running_session_keeps_locked_version_after_rollback(tmp_path):
    """AC-4 后半: 运行中会话(已 locked_skill_version)维持锁定版本,不受回滚影响。"""
    _setup_schema()
    _make_skill_files(tmp_path, version="1.1.0")

    async def _run() -> dict:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            # 1. 插入 skills 表 v1.1.0
            await conn.execute(
                """
                INSERT INTO skills (name, version, manifest, system_prompt, tools)
                VALUES ('office', '1.1.0', $1::jsonb, 'v1.1', '[]'::jsonb)
                """,
                json.dumps({"name": "office", "version": "1.1.0", "scenario": "office"}),
            )
            await conn.execute(
                """
                INSERT INTO version_snapshots (scope, version, payload)
                VALUES ('skill', '1.0.0', $1::jsonb)
                """,
                json.dumps(
                    {
                        "manifest": {
                            "name": "office",
                            "version": "1.0.0",
                            "scenario": "office",
                            "dependencies": {"tools": [{"name": "file_read"}]},
                        },
                        "system_prompt": "v1.0",
                        "tools_yaml": [{"name": "file_read"}],
                    }
                ),
            )
            # 2. 创建 session 并锁定 v1.1.0
            session_id = await conn.fetchval(
                "INSERT INTO sessions (title, status, locked_skill_name, locked_skill_version) "
                "VALUES ('running', 'active', 'office', '1.1.0') RETURNING id"
            )
            # 3. 执行回滚(不影响运行中会话)
            repo = VersionSnapshotRepo(conn)
            manager_rollback = SkillRollbackManager(snapshot_repo=repo)
            await manager_rollback.rollback_skill(
                skill_name="office", target_version="1.0.0", conn=conn
            )
            # 4. 运行中会话仍为 v1.1.0
            locked_version = await conn.fetchval(
                "SELECT locked_skill_version FROM sessions WHERE id=$1", session_id
            )
            return {"locked_version": locked_version}
        finally:
            await conn.close()

    out = asyncio.run(_run())
    # 运行中会话维持 v1.1.0(不受回滚影响)
    assert out["locked_version"] == "1.1.0"
