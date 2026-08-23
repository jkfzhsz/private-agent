"""B1 P1-2 AC-7 - main.py 启动后日志文件存在且含 JSON 行。

Source: plan/b1-foundation-compliance step 18 (AC-7)
"""
import json
import logging
import os
from unittest.mock import AsyncMock, MagicMock, patch


def test_main_startup_writes_log_file(tmp_path, monkeypatch):
    """AC-7: _on_startup 后 file_path 指定的日志文件存在且含 JSON 行。"""
    log_file = tmp_path / "logs" / "agent.log"

    # mock load_config 返回含 observability.logging.file_path 的 cfg
    mock_cfg = {
        "observability": {
            "logging": {
                "file_path": str(log_file),
                "level": "INFO",
            }
        },
        "database": {"host": "127.0.0.1", "port": 5432, "name": "test", "user": "u"},
    }
    monkeypatch.setattr(
        "private_agent.main.loader.load_config", lambda: mock_cfg
    )

    # mock db.create_pool 避免真实 DB 连接
    async def _fake_pool(cfg):
        return MagicMock()

    monkeypatch.setattr("private_agent.main.db.create_pool", _fake_pool)

    # mock APScheduler 避免真实调度
    fake_scheduler = MagicMock()
    fake_scheduler.running = False
    monkeypatch.setattr(
        "apscheduler.schedulers.asyncio.AsyncIOScheduler", lambda: fake_scheduler
    )
    monkeypatch.setattr(
        "private_agent.storage.ttl_cleanup.schedule_ttl_cleanup",
        lambda sched, cfg: None,
    )

    # 调用 _on_startup
    import asyncio
    from private_agent.main import _on_startup, _logger

    asyncio.run(_on_startup())

    # _on_startup 内部应调用 setup_logger 配置 file_path
    # 触发一条日志验证写入
    _logger.info("startup test message")

    # 刷盘
    for h in _logger.handlers[:]:
        h.flush()
        if isinstance(h, logging.FileHandler):
            h.close()
        _logger.removeHandler(h)

    assert log_file.exists(), "log file should be created at startup"
    content = log_file.read_text(encoding="utf-8").strip()
    assert content, "log file should not be empty"
    # 至少有一行可解析为 JSON
    lines = [l for l in content.splitlines() if l.strip()]
    last = json.loads(lines[-1])
    assert "timestamp" in last
    assert "level" in last
    assert "message" in last
