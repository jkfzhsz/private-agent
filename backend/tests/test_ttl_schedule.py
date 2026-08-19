"""M1 Phase 1 step 3 - schedule_ttl_cleanup:APScheduler 注册每日 03:00 TTL 清理任务。

Source: plan/m1-react-loop step 3 (蓝图 §2.10 第 2、3 条 + §9.13)

cron `0 3 * * *`(每日 03:00)调用 run_ttl_cleanup:
- react_events_retention_days(默认 30)
- messages_archive_retention_days(默认 90)
"""
import asyncio
from unittest.mock import AsyncMock

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from private_agent.storage import db, ttl_cleanup


def _test_cfg(react: int = 30, archive: int = 90) -> dict:
    return {
        "observability": {
            "disk": {
                "react_events_retention_days": react,
                "messages_archive_retention_days": archive,
            }
        }
    }


def test_schedule_ttl_cleanup_registers_job():
    """schedule_ttl_cleanup 后 scheduler 有 1 个 job,cron 0 3 * * *(每日 03:00)。"""
    scheduler = AsyncIOScheduler()
    ttl_cleanup.schedule_ttl_cleanup(scheduler, _test_cfg(30, 90))
    jobs = scheduler.get_jobs()
    assert len(jobs) == 1
    job = jobs[0]
    assert isinstance(job.trigger, CronTrigger)
    trigger_str = str(job.trigger)
    assert "hour='3'" in trigger_str, f"trigger 应含 hour='3',实际: {trigger_str}"
    assert "minute='0'" in trigger_str, f"trigger 应含 minute='0',实际: {trigger_str}"


def test_schedule_ttl_cleanup_job_func_callable(monkeypatch):
    """手动调用 job.func 触发 run_ttl_cleanup(用 mock conn 验证调用)。"""
    mock_conn = AsyncMock()

    async def _fake_connect():
        return mock_conn

    monkeypatch.setattr(db, "connect", _fake_connect)

    called = {}

    async def _spy(conn, *, react_events_retention_days, messages_archive_retention_days):
        called["conn"] = conn
        called["react"] = react_events_retention_days
        called["archive"] = messages_archive_retention_days

    monkeypatch.setattr(ttl_cleanup, "run_ttl_cleanup", _spy)

    scheduler = AsyncIOScheduler()
    ttl_cleanup.schedule_ttl_cleanup(scheduler, _test_cfg(30, 90))
    job = scheduler.get_jobs()[0]

    asyncio.run(job.func(*job.args))

    assert called.get("conn") is mock_conn, "run_ttl_cleanup 应收到 db.connect 返回的 conn"
    assert called.get("react") == 30
    assert called.get("archive") == 90
