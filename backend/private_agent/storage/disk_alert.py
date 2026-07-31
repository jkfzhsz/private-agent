"""蓝图 §2.10 第 6 条 磁盘占用分级告警 + M1 get_disk_status 组合。

B4.2:Python Sidecar 检查 Postgres 数据目录大小,三级阈值响应:
- 1.5GB:预警(yellow)
- 2GB:禁止新会话(orange)
- 3GB:强制清理(red)

M1 Phase 1 step 2:get_disk_status 组合 size 查询 + 分级评估,
从 cfg['observability']['disk'] 读阈值(蓝图 §9.13)。
"""
from __future__ import annotations

from typing import Any

import asyncpg

from private_agent.config import loader

_GB = 1024 ** 3


def evaluate_disk_alert_level(
    *,
    size_bytes: int,
    warning_gb: float,
    block_new_session_gb: float,
    force_cleanup_gb: float,
) -> dict[str, str]:
    """根据数据目录大小评估告警级别(蓝图 §2.10 第 6 条)。

    Args:
        size_bytes: 数据目录大小(字节)。
        warning_gb: 预警阈值(GB)。
        block_new_session_gb: 禁止新会话阈值(GB)。
        force_cleanup_gb: 强制清理阈值(GB)。

    Returns:
        {"level": "none|yellow|orange|red", "message": "..."}
        level=none 时 message 为空字符串。

    Raises:
        ValueError: 阈值不满足 warning_gb < block_new_session_gb < force_cleanup_gb。
    """
    if not (warning_gb < block_new_session_gb < force_cleanup_gb):
        raise ValueError(
            f"阈值必须满足 warning_gb < block_new_session_gb < force_cleanup_gb, "
            f"实际: {warning_gb} < {block_new_session_gb} < {force_cleanup_gb}"
        )

    warning_bytes = warning_gb * _GB
    block_bytes = block_new_session_gb * _GB
    force_bytes = force_cleanup_gb * _GB

    if size_bytes >= force_bytes:
        return {
            "level": "red",
            "message": "已自动清理过期数据",
        }
    if size_bytes >= block_bytes:
        return {
            "level": "orange",
            "message": "存储空间不足,无法新建会话,请清理后继续",
        }
    if size_bytes >= warning_bytes:
        return {
            "level": "yellow",
            "message": "存储空间即将不足,建议清理",
        }
    return {"level": "none", "message": ""}


async def get_pg_data_dir_size(conn: asyncpg.Connection) -> int:
    """查询所有可连接数据库的总大小(字节,蓝图 §2.10 第 6 条 "Postgres 数据目录大小")。

    Args:
        conn: Postgres 连接(任意库均可,跨库查询 pg_database)。

    Returns:
        所有 datallowconn=TRUE 数据库大小之和(字节)。
    """
    size = await conn.fetchval(
        "SELECT COALESCE(SUM(pg_database_size(datname)), 0) "
        "FROM pg_database WHERE datallowconn"
    )
    return int(size)


async def get_disk_status(
    conn: asyncpg.Connection,
    cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """组合 size 查询 + 分级评估,返回磁盘状态(蓝图 §2.10 第 6 条 + §9.13)。

    Args:
        conn: Postgres 连接。
        cfg: 配置 dict(默认从 config.yaml 加载);读 observability.disk 阈值。

    Returns:
        {"level": "none|yellow|orange|red", "message": "...", "size_bytes": N}
    """
    if cfg is None:
        cfg = loader.load_config()
    disk_cfg = cfg.get("observability", {}).get("disk", {})
    warning_gb = float(disk_cfg.get("warning_gb", 1.5))
    block_new_session_gb = float(disk_cfg.get("block_new_session_gb", 2.0))
    force_cleanup_gb = float(disk_cfg.get("force_cleanup_gb", 3.0))

    size_bytes = await get_pg_data_dir_size(conn)
    level_info = evaluate_disk_alert_level(
        size_bytes=size_bytes,
        warning_gb=warning_gb,
        block_new_session_gb=block_new_session_gb,
        force_cleanup_gb=force_cleanup_gb,
    )
    return {
        "level": level_info["level"],
        "message": level_info["message"],
        "size_bytes": size_bytes,
    }
