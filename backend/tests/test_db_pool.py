"""M1 Phase 1 step 1 - db.py 连接池管理(蓝图 §2.10、§9.13)。

Source: plan/m1-react-loop step 1

create_pool / get_pool(单例)/ close_pool:为 sidecar 提供可复用的 asyncpg 连接池,
替代每次请求都 connect/close 的临时连接模式。
"""
import asyncio
import os

import asyncpg

from private_agent.storage import db

TEST_DSN = os.environ.get(
    "PA_TEST_DSN",
    "postgresql://postgres:123123@localhost:5432/private_agent_test",
)


def _test_cfg() -> dict:
    """构造指向 private_agent_test 的 cfg(build_dsn 读 database 段)。"""
    return {
        "database": {
            "host": "127.0.0.1",
            "port": 5432,
            "name": "private_agent_test",
            "user": "postgres",
            "password_env": "PA_DB_PASSWORD",
        }
    }


def test_create_pool_returns_pool_instance():
    """create_pool(cfg) 返回 asyncpg.Pool 实例。"""
    async def _run() -> asyncpg.Pool:
        await db.close_pool()  # 确保干净起点
        pool = await db.create_pool(_test_cfg())
        try:
            return pool
        finally:
            await db.close_pool()

    pool = asyncio.run(_run())
    assert isinstance(pool, asyncpg.Pool), (
        f"create_pool 应返回 asyncpg.Pool 实例,实际: {type(pool)}"
    )


def test_get_pool_returns_singleton():
    """get_pool() 多次调用返回同一实例(模块级单例)。"""
    async def _run() -> tuple[asyncpg.Pool, asyncpg.Pool, asyncpg.Pool]:
        await db.close_pool()
        p1 = await db.get_pool(_test_cfg())
        p2 = await db.get_pool(_test_cfg())
        p3 = await db.get_pool(_test_cfg())
        try:
            return p1, p2, p3
        finally:
            await db.close_pool()

    p1, p2, p3 = asyncio.run(_run())
    assert p1 is p2 is p3, "get_pool 应返回同一单例实例"


def test_close_pool_resets_singleton():
    """close_pool() 后 get_pool() 返回新实例(单例被重置)。"""
    async def _run() -> tuple[asyncpg.Pool, asyncpg.Pool]:
        await db.close_pool()
        p1 = await db.get_pool(_test_cfg())
        await db.close_pool()
        p2 = await db.get_pool(_test_cfg())
        try:
            return p1, p2
        finally:
            await db.close_pool()

    p1, p2 = asyncio.run(_run())
    assert p1 is not p2, "close_pool 后 get_pool 应返回新实例"


def test_pool_can_acquire_connection():
    """pool.acquire() 拿到的 conn 能执行 SELECT 1。"""
    async def _run() -> int:
        await db.close_pool()
        pool = await db.create_pool(_test_cfg())
        try:
            async with pool.acquire() as conn:
                return await conn.fetchval("SELECT 1")
        finally:
            await db.close_pool()

    result = asyncio.run(_run())
    assert result == 1, f"pool.acquire 后 SELECT 1 应返回 1,实际: {result}"
