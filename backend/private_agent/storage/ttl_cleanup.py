"""蓝图 §2.10 第 2、3 条 TTL 清理调度 + M1 APScheduler 注册。

B4.3:react_events 默认保留 30 天,messages_archive 默认保留 90 天。
清理任务在 sidecar 启动时与每日定时执行(蓝图 §2.10 第 2 条)。

M1 Phase 1 step 3:schedule_ttl_cleanup 用 APScheduler CronTrigger(hour=3, minute=0)
注册每日 03:00 的清理任务(蓝图 §9.13 observability.disk 保留天数)。
"""
from __future__ import annotations

from typing import Any

import asyncpg

from private_agent.storage import db


async def cleanup_react_events(
    conn: asyncpg.Connection,
    *,
    retention_days: int,
) -> int:
    """删除 react_events 表中超期的记录(蓝图 §2.10 第 2 条)。

    Args:
        conn: Postgres 连接。
        retention_days: 保留天数(超期则删除)。

    Returns:
        删除的行数。
    """
    result = await conn.execute(
        "DELETE FROM react_events WHERE created_at < now() - ($1 || ' days')::interval",
        str(retention_days),
    )
    # asyncpg execute 返回 "DELETE N" 格式
    return _parse_row_count(result)


async def cleanup_messages_archive(
    conn: asyncpg.Connection,
    *,
    retention_days: int,
) -> int:
    """删除 messages_archive 表中超期的记录(蓝图 §2.10 第 3 条)。

    Args:
        conn: Postgres 连接。
        retention_days: 保留天数(超期则删除)。

    Returns:
        删除的行数。
    """
    result = await conn.execute(
        "DELETE FROM messages_archive "
        "WHERE archived_at < now() - ($1 || ' days')::interval",
        str(retention_days),
    )
    return _parse_row_count(result)


async def run_ttl_cleanup(
    conn: asyncpg.Connection,
    *,
    react_events_retention_days: int,
    messages_archive_retention_days: int,
) -> dict[str, int]:
    """同时执行两类清理,返回汇总(蓝图 §2.10 第 2、3 条)。

    Args:
        conn: Postgres 连接。
        react_events_retention_days: react_events 保留天数。
        messages_archive_retention_days: messages_archive 保留天数。

    Returns:
        {"react_events_deleted": N, "messages_archive_deleted": M}
    """
    react_deleted = await cleanup_react_events(
        conn, retention_days=react_events_retention_days
    )
    archive_deleted = await cleanup_messages_archive(
        conn, retention_days=messages_archive_retention_days
    )
    return {
        "react_events_deleted": react_deleted,
        "messages_archive_deleted": archive_deleted,
    }


async def _ttl_job(
    react_events_retention_days: int,
    messages_archive_retention_days: int,
) -> dict[str, int]:
    """APScheduler 注册的 TTL 清理任务(每日 03:00 触发)。

    获取连接 → 调用 run_ttl_cleanup → 关闭连接。
    """
    conn = await db.connect()
    try:
        return await run_ttl_cleanup(
            conn,
            react_events_retention_days=react_events_retention_days,
            messages_archive_retention_days=messages_archive_retention_days,
        )
    finally:
        await conn.close()


def schedule_ttl_cleanup(scheduler: Any, cfg: dict[str, Any]) -> None:
    """向 APScheduler 注册每日 03:00 的 TTL 清理任务(蓝图 §2.10 第 2 条)。

    cron `0 3 * * *`(每日 03:00),从 cfg['observability']['disk'] 读保留天数:
    - react_events_retention_days(默认 30)
    - messages_archive_retention_days(默认 90)

    Args:
        scheduler: APScheduler 实例(AsyncIOScheduler)。
        cfg: 配置 dict。
    """
    from apscheduler.triggers.cron import CronTrigger

    disk_cfg = cfg.get("observability", {}).get("disk", {})
    react_days = int(disk_cfg.get("react_events_retention_days", 30))
    archive_days = int(disk_cfg.get("messages_archive_retention_days", 90))

    scheduler.add_job(
        _ttl_job,
        CronTrigger(hour=3, minute=0),
        args=[react_days, archive_days],
        id="ttl_cleanup",
        replace_existing=True,
    )


def _parse_row_count(execute_result: str) -> int:
    """解析 asyncpg execute 返回值(如 'DELETE 5')的行数。"""
    try:
        return int(execute_result.split()[-1])
    except (IndexError, ValueError):
        return 0
