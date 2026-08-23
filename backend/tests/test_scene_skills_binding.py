"""阶段1-d(agent-upgrader 设计文档 §14 + G2): 技能场景归类 + 装配展示。

覆盖:
- list_skills 透传 scenario 字段
- add_supplementary_skills 挂载校验: 场景允许类目外拒绝(计入 failed)
- 无 scene_skills 配置的场景 = 允许全部(向后兼容)
- tools-assembly 端点: 会话装配视图(scene/kind/workspace/mcp/monitor/anchor)
"""
import asyncio
import os

import asyncpg
import pytest

TEST_DSN = os.environ.get(
    "PA_TEST_DSN",
    "postgresql://postgres:123123@localhost:5432/private_agent_test",
)


@pytest.fixture(autouse=True)
def _patch_db_to_test(monkeypatch):
    # dev_dir=${PA_USER_DATA}/skills → 指向 backend(本测试 cwd=backend)
    monkeypatch.setenv("PA_USER_DATA", os.path.abspath("."))
    from private_agent.storage import db

    async def _fake_connect(cfg=None):
        return await asyncpg.connect(TEST_DSN)

    monkeypatch.setattr(db, "connect", _fake_connect)


async def _run_schema():
    conn = await asyncpg.connect(TEST_DSN)
    try:
        await conn.execute("DROP SCHEMA public CASCADE")
        await conn.execute("CREATE SCHEMA public")
        await conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
        from private_agent.storage import migrations

        await migrations.migrate_all(conn)
    finally:
        await conn.close()


def test_list_skills_includes_scenario(monkeypatch):
    """list_skills 返回含 scenario 字段(§14 ①)。"""
    asyncio.run(_run_schema())

    from private_agent.api import admin as admin_api

    # 直接验证响应结构: 用真实 loader 列出技能(仅断言字段存在)
    async def _run():
        from private_agent.config import loader as cfg_loader
        from private_agent.skills.loader import SkillLoader

        cfg = cfg_loader.load_config()
        conn = await asyncpg.connect(TEST_DSN)
        try:
            loader_ = SkillLoader.from_cfg(cfg)
            skills = await loader_.list_all(conn)
            names = {s.manifest.name: s.manifest for s in skills}
            assert "tdd" in names, "tdd 技能应存在"
            assert names["tdd"].scenario == "engineering"
            assert names["docx"].scenario == "documents"
            assert names["office"].scenario == "office"
        finally:
            await conn.close()

    asyncio.run(_run())


def test_add_supplementary_rejects_off_scene(monkeypatch):
    """白圭(data_analysis)挂 tdd(engineering) → 拒绝; 挂 docx(documents) → 允许。"""
    asyncio.run(_run_schema())

    from private_agent.api.admin import add_supplementary_skills

    async def _run():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            sid = await conn.fetchval(
                "INSERT INTO sessions (kind, locked_skill_name) "
                "VALUES ('main', 'data_analysis') RETURNING id"
            )
            # 白圭允许 documents, 不允许 engineering
            resp = await add_supplementary_skills(sid, None)
            # 直接调 handler 需 body 对象 —— 构造
            from private_agent.api.admin import SupplementarySkillsRequest

            body = SupplementarySkillsRequest(
                skill_names=["tdd", "docx"], added_by="test"
            )
            result = await add_supplementary_skills(sid, body)
            assert isinstance(result, dict), result
            assert result["ok"] is True
            assert "docx" in result["added"], result
            # tdd 被拒绝(计入 failed, 类目不匹配)
            failed_names = [f["name"] for f in result["failed"]]
            assert "tdd" in failed_names, result
            reason = next(
                f["reason"] for f in result["failed"] if f["name"] == "tdd"
            )
            assert "不属于场景" in reason or "类目" in reason, reason
        finally:
            await conn.close()

    asyncio.run(_run())


def test_add_supplementary_no_scene_allows_all(monkeypatch):
    """未锁定场景(locked_skill_name 为空) → 允许全部(向后兼容)。"""
    asyncio.run(_run_schema())

    from private_agent.api.admin import (
        SupplementarySkillsRequest,
        add_supplementary_skills,
    )

    async def _run():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            sid = await conn.fetchval(
                "INSERT INTO sessions (kind) VALUES ('main') RETURNING id"
            )
            body = SupplementarySkillsRequest(
                skill_names=["tdd"], added_by="test"
            )
            result = await add_supplementary_skills(sid, body)
            assert isinstance(result, dict), result
            assert "tdd" in result["added"], result
            assert result["failed"] == [], result
        finally:
            await conn.close()

    asyncio.run(_run())


def test_tools_assembly_monitor(monkeypatch):
    """tools-assembly: monitor 会话返回专属工具 + 锚点 + MCP 绑定。"""
    asyncio.run(_run_schema())

    from private_agent.api.admin import get_session_tools_assembly

    async def _run():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            sid = await conn.fetchval(
                "INSERT INTO sessions (kind) VALUES ('monitor') RETURNING id"
            )
            resp = await get_session_tools_assembly(sid)
            assert isinstance(resp, dict), resp
            assert resp["kind"] == "monitor"
            # monitor 专属工具
            assert "optim_plan" in resp["monitor_tools"]
            assert "apply_optim" in resp["monitor_tools"]
            # MCP 绑定(monitor: mempalace/Searchpin)
            assert any("mempalace" in s for s in resp["mcp_servers"]), resp
            # 锚点含动手工具
            assert "file_write" in resp["anchor_tools"]
            assert "code_execution" in resp["anchor_tools"]
            # 场景会话无 monitor 专属
        finally:
            await conn.close()

    asyncio.run(_run())


def test_tools_assembly_scene(monkeypatch):
    """tools-assembly: 场景会话(office)返回 MCP 绑定, 无 monitor 专属。"""
    asyncio.run(_run_schema())

    from private_agent.api.admin import get_session_tools_assembly

    async def _run():
        conn = await asyncpg.connect(TEST_DSN)
        try:
            sid = await conn.fetchval(
                "INSERT INTO sessions (kind, locked_skill_name) "
                "VALUES ('main', 'office') RETURNING id"
            )
            resp = await get_session_tools_assembly(sid)
            assert isinstance(resp, dict), resp
            assert resp["scene"] == "office"
            assert resp["kind"] == "main"
            assert resp["monitor_tools"] == []
            assert resp["anchor_tools"] == []
        finally:
            await conn.close()

    asyncio.run(_run())
