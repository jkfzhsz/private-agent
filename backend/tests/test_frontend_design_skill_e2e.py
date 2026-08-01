"""M3 plan/m3-skills-frontend-design step 7 - frontend_design skill E2E 测试。

Source: spec/m3-skills-frontend-design AC-1~8
- activate frontend_design → 6 工具过滤(无 calculator/http_request)
- frozen_hash 写入 sessions 表
- system_prompt 含四段式框架关键字
- examples 加载(landing_page + react_component)

用真实 DB + 真实 SkillLoader(dev_dir=backend/skills)+ 真实 ToolRegistry。
"""
import asyncio
import os
from pathlib import Path

import asyncpg

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

SKILLS_DEV_DIR = str(Path(__file__).parent.parent / "skills")


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


async def _create_session(conn: asyncpg.Connection) -> int:
    return await conn.fetchval(
        "INSERT INTO sessions (title, model_id) VALUES ($1, $2) RETURNING id",
        "frontend-design-e2e-test", "mock-glm",
    )


class TestFrontendDesignSkillE2E:
    """plan step 7: frontend_design skill 完整激活流程 E2E。"""

    def test_activate_frontend_design_filters_tools_and_writes_frozen_hash(self):
        """activate frontend_design → 6 工具(无 calculator/http_request)+ frozen_hash 写入 sessions。"""
        _setup_schema()

        async def _run():
            conn = await asyncpg.connect(TEST_DSN)
            try:
                session_id = await _create_session(conn)

                loader = SkillLoader(dev_dir=SKILLS_DEV_DIR)
                ex_loader = ExampleLoader(dev_dir=SKILLS_DEV_DIR)
                registry = ToolRegistry()
                register_all_builtins(registry)
                mgr = SkillManager(
                    loader=loader,
                    example_loader=ex_loader,
                    tool_registry=registry,
                )

                result = await mgr.activate_skill(
                    skill_name="frontend_design",
                    session_id=session_id,
                    conn=conn,
                )

                # AC-1: 返回 locked_version + frozen_hash
                assert result["locked_version"] == "1.0.0"
                assert len(result["frozen_hash"]) == 64

                # AC-3: tools 过滤 — 6 工具,无 calculator/http_request
                tool_names = [t.name for t in result["filtered_tools"]]
                expected_tools = {
                    "code_execution", "datetime", "file_read",
                    "file_write", "search_knowledge", "web_search",
                }
                assert set(tool_names) == expected_tools, (
                    f"expected {expected_tools}, got {set(tool_names)}"
                )
                assert "calculator" not in tool_names
                assert "http_request" not in tool_names

                # AC-2: sessions 表写入锁定字段
                row = await conn.fetchrow(
                    "SELECT locked_skill_name, locked_skill_version, frozen_hash "
                    "FROM sessions WHERE id = $1",
                    session_id,
                )
                assert row["locked_skill_name"] == "frontend_design"
                assert row["locked_skill_version"] == "1.0.0"
                assert row["frozen_hash"] == result["frozen_hash"]

                return result
            finally:
                await conn.close()

        result = asyncio.run(_run())

        # AC-4: system_prompt 含四段式框架关键字
        prompt = result["system_prompt"]
        assert "前端设计" in prompt
        assert "工具" in prompt
        # examples 注入
        assert "示例" in prompt

    def test_frontend_design_skill_yaml_matches_blueprint_matrix(self):
        """plan step 1: skill.yaml dependencies.tools 符合蓝图 7.5 矩阵。"""
        async def _run():
            conn = await asyncpg.connect(TEST_DSN)
            try:
                loader = SkillLoader(dev_dir=SKILLS_DEV_DIR)
                skill = await loader.load("frontend_design", conn)
                return skill
            finally:
                await conn.close()

        skill = asyncio.run(_run())
        tools = skill.manifest.dependencies.tools
        tool_names = {t.name: t.enabled for t in tools}

        # AC-5: 蓝图 7.5 矩阵:6 工具 enabled,无 calculator/http_request 条目
        assert tool_names["code_execution"] is True
        assert tool_names["datetime"] is True
        assert tool_names["file_read"] is True
        assert tool_names["file_write"] is True
        assert tool_names["search_knowledge"] is True
        assert tool_names["web_search"] is True
        assert "calculator" not in tool_names
        assert "http_request" not in tool_names

        # AC-6: permissions / knowledge_base / examples 配置
        assert skill.manifest.permissions.allow_file_write is True
        assert skill.manifest.knowledge_base.scenario == "frontend_design"
        assert skill.manifest.examples.enabled is True
        assert skill.manifest.examples.max_examples == 2
        assert skill.manifest.max_frozen_token == 4000
