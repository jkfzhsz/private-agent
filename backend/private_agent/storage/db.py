"""蓝图 §2.10、§9.13 数据库连接管理。

B4.4:从 config.yaml + 环境变量构造 DSN,提供 asyncpg 连接获取。
密码从环境变量 database.password_env 读取(不入 yaml)。
"""
from __future__ import annotations

import os
from typing import Any

import asyncpg

from private_agent.config import loader


def build_dsn(cfg: dict[str, Any] | None = None) -> str:
    """从 config 构造 Postgres DSN(蓝图 §9.13 database 段)。

    2026-08-06: env 优先覆盖连接参数 —— PA_DB_HOST/PA_DB_PORT/
    PA_DB_NAME/PA_DB_USER/PA_DB_PASSWORD > config.yaml。打包版首次
    配置(DB 密码未设时)由保存端点写入 user_env(PA_DB_*), 重启即生效,
    无需 DB 可用(解决"首次配置鸡生蛋": 保存端点不再依赖 DB)。

    Args:
        cfg: 配置 dict(默认从 config.yaml 加载)。

    Returns:
        postgresql://user:password@host:port/name

    Raises:
        ValueError: 环境变量 password_env 未设置或为空。
    """
    if cfg is None:
        cfg = loader.load_config()
    db_cfg = cfg["database"]
    host = os.environ.get("PA_DB_HOST") or str(db_cfg.get("host", "127.0.0.1"))
    port = os.environ.get("PA_DB_PORT") or str(db_cfg.get("port", 5432))
    name = os.environ.get("PA_DB_NAME") or str(db_cfg.get("name", "private_agent"))
    user = os.environ.get("PA_DB_USER") or str(db_cfg.get("user", "postgres"))
    password = os.environ.get("PA_DB_PASSWORD") or os.environ.get(
        str(db_cfg.get("password_env", "PA_DB_PASSWORD"))
    )
    if not password:
        raise ValueError(
            f"环境变量 {db_cfg['password_env']} 未设置(蓝图 §9.13 database.password_env)"
        )
    return (
        f"postgresql://{user}:{password}"
        f"@{host}:{port}/{name}"
    )


async def connect(cfg: dict[str, Any] | None = None) -> asyncpg.Connection:
    """获取 Postgres 连接(蓝图 §2.10)。

    Args:
        cfg: 配置 dict(默认从 config.yaml 加载)。

    Returns:
        asyncpg Connection。
    """
    dsn = build_dsn(cfg)
    return await asyncpg.connect(dsn)


# 模块级连接池单例(蓝图 §2.10,sidecar 共享)
_pool: asyncpg.Pool | None = None


async def create_pool(cfg: dict[str, Any] | None = None) -> asyncpg.Pool:
    """创建 asyncpg 连接池(蓝图 §2.10)。

    Args:
        cfg: 配置 dict(默认从 config.yaml 加载)。

    Returns:
        asyncpg.Pool 实例(min_size=1, max_size=10)。
    """
    dsn = build_dsn(cfg)
    return await asyncpg.create_pool(dsn, min_size=1, max_size=10)


async def get_pool(cfg: dict[str, Any] | None = None) -> asyncpg.Pool:
    """获取模块级连接池单例(懒创建,蓝图 §2.10)。

    Args:
        cfg: 配置 dict(仅在首次创建时使用)。

    Returns:
        asyncpg.Pool 实例(多次调用返回同一实例)。
    """
    global _pool
    if _pool is None:
        _pool = await create_pool(cfg)
    return _pool


async def close_pool() -> None:
    """关闭连接池并重置单例(蓝图 §2.10)。"""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
