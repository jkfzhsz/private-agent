"""system_capabilities 工具测试 - PA 自身认知查询。

2026-08-13 新增: 让 PA 智能体按需查询自身说明书(三系统边界/工具边界/渠道/
运行时快照)。遵循项目工具模式: handler 自建 DB 连接, 测试通过 PA_DB_* 环境
变量指向测试库。
"""
from __future__ import annotations

import asyncio
import os

import asyncpg
import pytest

from private_agent.storage import migrations
from private_agent.tools.builtins.system_capabilities import (
    SYSTEM_CAPABILITIES_TOOL,
    _system_capabilities_handler,
)

TEST_DSN = os.environ.get(
    "PA_TEST_DSN",
    "postgresql://postgres:123123@localhost:5432/private_agent_test",
)

os.environ["PA_DB_HOST"] = "localhost"
os.environ["PA_DB_PORT"] = "5432"
os.environ["PA_DB_NAME"] = "private_agent_test"
os.environ["PA_DB_USER"] = "postgres"
os.environ["PA_DB_PASSWORD"] = "123123"


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


def test_tool_def():
    """工具定义: 名称 + aspect 参数 + 内核标记。"""
    assert SYSTEM_CAPABILITIES_TOOL.name == "system_capabilities"
    props = SYSTEM_CAPABILITIES_TOOL.parameters_schema["properties"]
    assert "aspect" in props
    assert props["aspect"]["enum"] == ["storage", "tools", "channels", "state", "all"]
    assert SYSTEM_CAPABILITIES_TOOL.is_kernel is True
    assert SYSTEM_CAPABILITIES_TOOL.safety_level == "none"


def test_storage_aspect_static():
    """storage 维度: 返回三系统边界(纯静态, 不查 DB)。"""
    async def _run() -> str:
        result = await _system_capabilities_handler({"aspect": "storage"})
        return result.output

    output = asyncio.run(_run())
    assert "原生记忆" in output
    assert "场景知识库" in output
    assert "记忆宫殿" in output
    assert "ChromaDB" in output
    assert "不是 PostgreSQL" in output


def test_tools_aspect_static():
    """tools 维度: 返回工具能力边界(含 memory_search 关键词匹配限制)。"""
    async def _run() -> str:
        result = await _system_capabilities_handler({"aspect": "tools"})
        return result.output

    output = asyncio.run(_run())
    assert "memory_search" in output
    assert "关键词" in output
    assert "非语义" in output
    # 持仓查询引导(2026-08-13: 结构化事实优先 kg_query/list_drawers, 不依赖 search)
    assert "mempalace_kg_query" in output
    assert "持仓" in output


def test_state_aspect_snapshot():
    """state 维度: 返回运行时快照(知识库/记忆统计)。"""
    _setup_schema()

    async def _run() -> str:
        result = await _system_capabilities_handler({"aspect": "state"})
        return result.output

    output = asyncio.run(_run())
    assert "知识库" in output
    assert "原生记忆" in output
    assert "embedding" in output


def test_all_aspect():
    """all(默认): 返回全部四节。"""
    _setup_schema()

    async def _run() -> str:
        result = await _system_capabilities_handler({})
        return result.output

    output = asyncio.run(_run())
    assert "存储系统" in output
    assert "工具能力边界" in output
    assert "操作渠道" in output
    assert "运行时状态" in output


def test_channels_include_self_repair():
    """channels 维度: 含自我修复链路指引。

    2026-09-29 修订: 原断言要求含"重新打包"(build-electron.bat) —— 那是**打包版
    时代**的指引, 现已弃用打包版(日常走 dev 链路)。更要紧的是它指导模型让用户
    "关闭 PA 并重新打包", 而重启正是打断自我改动流程的根源(蒋先生反馈: 改动开始后
    就断连、无法判断原因)。现断言新语义: 三层影子验证 + 落盘即完成 + 不要求立即重启。
    """
    async def _run() -> str:
        result = await _system_capabilities_handler({"aspect": "channels"})
        return result.output

    output = asyncio.run(_run())
    assert "自我修复链路" in output
    assert "file_write" in output
    assert "最多改 2 次" in output
    # 影子验证三层(改动后必须自证正确, 不得靠重启看结果)
    assert "影子验证" in output
    assert "py_compile" in output
    assert "pytest" in output
    # 不要求用户立即重启(重启会中断正在进行的对话)
    assert "不要要求用户立即重启" in output
    # 过时指引必须已移除(注意: "打包部署(仅用户手动执行 build-electron.bat)"
    # 属"你不能做"的合法说明, 保留; 要移除的是"让用户关 PA 去重新打包"这条)
    assert "关闭 PA 并重新打包" not in output
    assert "必须等用户重新打包" not in output


def test_unknown_aspect_falls_back_to_all():
    """未知 aspect: 兜底返回全部。"""
    _setup_schema()

    async def _run() -> str:
        result = await _system_capabilities_handler({"aspect": "bogus"})
        return result.output

    output = asyncio.run(_run())
    assert "存储系统" in output
