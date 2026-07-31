"""M1 Phase 4 Behavior 4-4 - startup/shutdown hook (AC-5)。

Source: plan/m1-react-loop Phase 4 (蓝图 §2.10 + §9.4 AC-5 + §9.13)

startup:
- db.create_pool() 创建连接池
- AsyncIOScheduler + schedule_ttl_cleanup(cron `0 3 * * *`)
- scheduler.start()
shutdown:
- scheduler.shutdown()
- db.close_pool()

测试用 TestClient 上下文管理器触发 startup/shutdown。
"""
import asyncio

from fastapi.testclient import TestClient

import private_agent.main as main_mod
from private_agent.main import app
from private_agent.storage import db


def _test_cfg() -> dict:
    """构造指向 private_agent_test 的 cfg(startup hook 用此创建 pool)。"""
    return {
        "database": {
            "host": "127.0.0.1",
            "port": 5432,
            "name": "private_agent_test",
            "user": "postgres",
            "password_env": "PA_DB_PASSWORD",
        },
        "observability": {
            "disk": {
                "react_events_retention_days": 30,
                "messages_archive_retention_days": 90,
            }
        },
    }


def _clean_state() -> None:
    """清理上一轮测试残留的 pool/scheduler 单例状态。"""
    # 停止遗留 scheduler
    sched = getattr(main_mod, "_scheduler", None)
    if sched is not None:
        if sched.running:
            async def _stop():
                sched.shutdown(wait=False)
            try:
                asyncio.run(_stop())
            except Exception:
                pass
        main_mod._scheduler = None
    # 关闭遗留 pool
    if db._pool is not None:
        asyncio.run(db.close_pool())


def _patch_load_config(monkeypatch) -> None:
    """让 main 中的 loader.load_config 返回测试 cfg(指向 private_agent_test)。"""
    monkeypatch.setattr(main_mod.loader, "load_config", lambda: _test_cfg())


# ──────────────────────────────────────────────────────────────────────────────
# startup 后 db._pool 不为 None
# ──────────────────────────────────────────────────────────────────────────────


def test_startup_creates_db_pool(monkeypatch):
    """startup 后 db._pool 不为 None。"""
    _clean_state()
    _patch_load_config(monkeypatch)

    with TestClient(app):
        assert db._pool is not None, "startup 应创建 db 连接池"


# ──────────────────────────────────────────────────────────────────────────────
# startup 后 scheduler.running == True
# ──────────────────────────────────────────────────────────────────────────────


def test_startup_starts_scheduler(monkeypatch):
    """startup 后 scheduler 已启动(running == True)。"""
    _clean_state()
    _patch_load_config(monkeypatch)

    with TestClient(app):
        assert main_mod._scheduler is not None, "startup 应创建 scheduler"
        assert main_mod._scheduler.running is True, "scheduler 应已启动"


# ──────────────────────────────────────────────────────────────────────────────
# shutdown 后 db._pool 为 None
# ──────────────────────────────────────────────────────────────────────────────


def test_shutdown_closes_db_pool(monkeypatch):
    """shutdown 后 db._pool 为 None。"""
    _clean_state()
    _patch_load_config(monkeypatch)

    with TestClient(app):
        assert db._pool is not None
    # 退出 context 后 shutdown 已执行
    assert db._pool is None, "shutdown 应关闭并重置 db._pool"


# ──────────────────────────────────────────────────────────────────────────────
# shutdown 后 scheduler.running == False
# ──────────────────────────────────────────────────────────────────────────────


def test_shutdown_stops_scheduler(monkeypatch):
    """shutdown 后 scheduler.running == False。"""
    _clean_state()
    _patch_load_config(monkeypatch)

    with TestClient(app):
        assert main_mod._scheduler is not None
        assert main_mod._scheduler.running is True
    assert main_mod._scheduler is not None
    assert main_mod._scheduler.running is False, "shutdown 应停止 scheduler"
