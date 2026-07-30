"""B5.2 - config_runtime 运行时覆盖。

Source: plan/m0-implementation step 5 (蓝图 §2.12 + §9.13 [runtime] 项)

蓝图 §2.12:静态 yaml 默认值 + config_runtime 运行时覆盖(标注 [runtime] 的项支持)。
加载优先级:config_runtime > config.yaml。
"""
import asyncio
import os

import asyncpg

from private_agent.config import loader
from private_agent.storage import migrations

TEST_DSN = os.environ.get(
    "PA_TEST_DSN",
    "postgresql://postgres:postgres@localhost:5432/private_agent_test",
)


def _setup_and_clean() -> None:
    """建表 + 清空 config_runtime。"""
    async def _run() -> None:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            await conn.execute("DROP SCHEMA public CASCADE")
            await conn.execute("CREATE SCHEMA public")
            await conn.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
            await migrations.migrate_all(conn)
            await conn.execute("DELETE FROM config_runtime")
        finally:
            await conn.close()

    asyncio.run(_run())


def test_load_config_with_overrides_applies_runtime_value():
    """config_runtime 的覆盖值优先于 yaml 默认值(蓝图 §2.12)。"""
    _setup_and_clean()

    async def _run() -> None:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            # 插入运行时覆盖:system.sidecar.log_level = "DEBUG"(yaml 默认 "INFO")
            await conn.execute(
                "INSERT INTO config_runtime (key, value) VALUES ($1, $2)",
                "system.sidecar.log_level",
                '"DEBUG"',  # JSONB 字符串值
            )
            cfg = await loader.load_config_with_overrides(conn)
            assert cfg["system"]["sidecar"]["log_level"] == "DEBUG"
        finally:
            await conn.close()

    asyncio.run(_run())


def test_load_config_with_overrides_returns_static_when_no_overrides():
    """无 config_runtime 覆盖时,返回 yaml 静态值。"""
    _setup_and_clean()

    async def _run() -> None:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            cfg = await loader.load_config_with_overrides(conn)
            # yaml 默认值
            assert cfg["system"]["sidecar"]["log_level"] == "INFO"
        finally:
            await conn.close()

    asyncio.run(_run())
