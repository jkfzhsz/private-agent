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
    password = os.environ.get(db_cfg["password_env"])
    if not password:
        raise ValueError(
            f"环境变量 {db_cfg['password_env']} 未设置(蓝图 §9.13 database.password_env)"
        )
    return (
        f"postgresql://{db_cfg['user']}:{password}"
        f"@{db_cfg['host']}:{db_cfg['port']}/{db_cfg['name']}"
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
