"""项目优化(Hermes SOUL.md 借鉴) - 身份段测试。

覆盖:
- _identity_prompt: 默认内置身份 / config 覆盖
- _get_system_prompt: 身份段置于 system prompt 首位
"""
import asyncio
import os

import asyncpg
import pytest

from private_agent import main as main_mod
from private_agent.storage import migrations

TEST_DSN = os.environ.get(
    "PA_TEST_DSN",
    "postgresql://postgres:123123@localhost:5432/private_agent_test",
)


def test_identity_prompt_default():
    """未配置时返回内置默认身份(含 Private Agent 定位 + 协作规则)。"""
    text = main_mod._identity_prompt({"context": {}})
    assert "Private Agent" in text
    assert "协作规则" in text
    assert "给出明确选项" in text


def test_identity_prompt_config_override():
    """config context.identity 覆盖内置默认。"""
    custom = "你是测试身份的智能体。"
    text = main_mod._identity_prompt({"context": {"identity": custom}})
    assert text == custom
    assert "Private Agent" not in text


@pytest.mark.asyncio
async def test_get_system_prompt_identity_first():
    """_get_system_prompt 返回的 prompt 以身份段开头。"""
    # 建 schema + 会话
    conn = await asyncpg.connect(TEST_DSN)
    try:
        await conn.execute("DROP SCHEMA public CASCADE")
        await conn.execute("CREATE SCHEMA public")
        await conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
        await migrations.migrate_all(conn)
        session_id = await conn.fetchval(
            "INSERT INTO sessions (status) VALUES ('active') RETURNING id"
        )
        cfg = {"context": {}, "tools": {"mcp": {"servers": []}}}
        prompt = await main_mod._get_system_prompt(cfg, session_id, conn)
        assert prompt.startswith("你是 Private Agent")
        assert "协作规则" in prompt
    finally:
        await conn.close()
