"""M1 Phase 1 step 2 - get_disk_status:组合 size 查询 + 分级评估。

Source: plan/m1-react-loop step 2 (蓝图 §2.10 第 6 条 + §9.13 observability.disk)

get_disk_status(conn, cfg) 组合 get_pg_data_dir_size + evaluate_disk_alert_level,
从 cfg['observability']['disk'] 读阈值(默认 1.5/2.0/3.0)。
"""
import asyncio
import os

import asyncpg

from private_agent.storage import disk_alert

TEST_DSN = os.environ.get(
    "PA_TEST_DSN",
    "postgresql://postgres:123123@localhost:5432/private_agent_test",
)


def _make_fake_size(size_bytes: int):
    """构造 mock get_pg_data_dir_size(磁盘大小不可控,用 mock 注入固定值)。"""
    async def _fake(conn: asyncpg.Connection) -> int:
        return size_bytes
    return _fake


def test_get_disk_status_returns_dict_with_required_fields():
    """get_disk_status 返回 {level, message, size_bytes} 三个字段。"""
    async def _run() -> dict:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            return await disk_alert.get_disk_status(conn)
        finally:
            await conn.close()

    result = asyncio.run(_run())
    required = {"level", "message", "size_bytes"}
    assert required.issubset(result.keys()), (
        f"缺少字段: {required - result.keys()}"
    )
    assert result["level"] in {"none", "yellow", "orange", "red"}
    assert isinstance(result["size_bytes"], int)
    assert result["size_bytes"] > 0


def test_get_disk_status_level_none_when_small(monkeypatch):
    """size < warning_gb(1.5GB)→ level='none'。"""
    monkeypatch.setattr(
        disk_alert, "get_pg_data_dir_size",
        _make_fake_size(int(1.0 * 1024 ** 3)),
    )

    async def _run() -> dict:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            return await disk_alert.get_disk_status(conn)
        finally:
            await conn.close()

    result = asyncio.run(_run())
    assert result["level"] == "none"
    assert result["size_bytes"] == int(1.0 * 1024 ** 3)


def test_get_disk_status_level_yellow_when_warning(monkeypatch):
    """1.5GB <= size < 2.0GB → level='yellow'。"""
    monkeypatch.setattr(
        disk_alert, "get_pg_data_dir_size",
        _make_fake_size(int(1.6 * 1024 ** 3)),
    )

    async def _run() -> dict:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            return await disk_alert.get_disk_status(conn)
        finally:
            await conn.close()

    result = asyncio.run(_run())
    assert result["level"] == "yellow"
    assert "存储空间即将不足" in result["message"]


def test_get_disk_status_uses_config_thresholds(monkeypatch):
    """从 cfg['observability']['disk'] 读阈值(非默认值能改变 level)。"""
    # size=0.5GB:默认阈值(1.5/2.0/3.0)→ none;自定义阈值(0.1/0.4/0.7)→ orange
    monkeypatch.setattr(
        disk_alert, "get_pg_data_dir_size",
        _make_fake_size(int(0.5 * 1024 ** 3)),
    )
    cfg = {
        "observability": {
            "disk": {
                "warning_gb": 0.1,
                "block_new_session_gb": 0.4,
                "force_cleanup_gb": 0.7,
            }
        }
    }

    async def _run() -> dict:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            return await disk_alert.get_disk_status(conn, cfg)
        finally:
            await conn.close()

    result = asyncio.run(_run())
    assert result["level"] == "orange", (
        f"自定义阈值下 0.5GB 应为 orange,实际: {result['level']}"
    )
