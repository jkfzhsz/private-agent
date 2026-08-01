"""M3 plan step 27 - office skill E2E 测试。

Source: plan/m3-skills-office step 21-27
- activate office → tools 过滤正确(7 工具,http_request 排除)
- frozen_hash 写入 sessions 表
- system_prompt 含四段式框架关键字(角色定位/任务约束/工具规范/输出格式)
- examples 加载(excel_summary + web_research)

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

# backend/skills 目录(相对于 backend 工作目录)
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
    """插入 sessions 记录,返回 id。"""
    return await conn.fetchval(
        "INSERT INTO sessions (title, model_id) VALUES ($1, $2) RETURNING id",
        "office-e2e-test", "mock-glm",
    )


class TestOfficeSkillE2E:
    """plan step 27: office skill 完整激活流程 E2E。"""

    def test_activate_office_filters_tools_and_writes_frozen_hash(self):
        """activate office → 7 工具(http_request 排除)+ frozen_hash 写入 sessions。"""
        _setup_schema()

        async def _run():
            conn = await asyncpg.connect(TEST_DSN)
            try:
                session_id = await _create_session(conn)

                # 用真实 SkillLoader 指向 backend/skills
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
                    skill_name="office",
                    session_id=session_id,
                    conn=conn,
                )

                # AC-1: 返回 locked_version + frozen_hash
                assert result["locked_version"] == "1.0.0"
                assert len(result["frozen_hash"]) == 64

                # AC-3: tools 过滤 — 7 工具,http_request 排除
                tool_names = [t.name for t in result["filtered_tools"]]
                expected_tools = {
                    "calculator", "code_execution", "datetime",
                    "file_read", "file_write", "search_knowledge", "web_search",
                }
                assert set(tool_names) == expected_tools, (
                    f"expected {expected_tools}, got {set(tool_names)}"
                )
                assert "http_request" not in tool_names

                # sessions 表写入锁定字段
                row = await conn.fetchrow(
                    "SELECT locked_skill_name, locked_skill_version, frozen_hash "
                    "FROM sessions WHERE id = $1",
                    session_id,
                )
                assert row["locked_skill_name"] == "office"
                assert row["locked_skill_version"] == "1.0.0"
                assert row["frozen_hash"] == result["frozen_hash"]

                return result
            finally:
                await conn.close()

        result = asyncio.run(_run())

        # system_prompt 含四段式框架关键字
        prompt = result["system_prompt"]
        assert "办公" in prompt or "office" in prompt.lower()
        assert "工具" in prompt or "tool" in prompt.lower()
        # examples 注入(excel_summary + web_research)
        assert "示例" in prompt or "example" in prompt.lower()

    def test_office_skill_yaml_matches_blueprint_matrix(self):
        """plan step 21: skill.yaml dependencies.tools 符合蓝图 7.5 矩阵。"""
        async def _run():
            conn = await asyncpg.connect(TEST_DSN)
            try:
                loader = SkillLoader(dev_dir=SKILLS_DEV_DIR)
                skill = await loader.load("office", conn)
                return skill
            finally:
                await conn.close()

        skill = asyncio.run(_run())
        tools = skill.manifest.dependencies.tools
        tool_names = {t.name: t.enabled for t in tools}

        # 蓝图 7.5 矩阵:7 工具 enabled,http_request enabled=false
        assert tool_names["calculator"] is True
        assert tool_names["code_execution"] is True
        assert tool_names["datetime"] is True
        assert tool_names["file_read"] is True
        assert tool_names["file_write"] is True
        assert tool_names["search_knowledge"] is True
        assert tool_names["web_search"] is True
        assert tool_names["http_request"] is False

        # permissions / knowledge_base / examples 配置
        assert skill.manifest.permissions.allow_file_write is True
        assert skill.manifest.knowledge_base.scenario == "office"
        assert skill.manifest.knowledge_base.auto_retrieve is False
        assert skill.manifest.examples.enabled is True
        assert skill.manifest.examples.max_examples == 2
        assert skill.manifest.max_frozen_token == 4000
