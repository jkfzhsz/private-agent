"""B4.2 - 磁盘分级告警(1.5/2/3GB 三级阈值)。

Source: plan/m0-implementation step 4 (蓝图 §2.10 第 6 条 + §9.4 Done Criteria 4)

蓝图 §2.10 第 6 条三级阈值:
- 1.5GB:预警,UI 黄色提示"存储空间即将不足,建议清理"
- 2GB:禁止新会话,UI 橙色提示"存储空间不足,无法新建会话,请清理后继续"
- 3GB:强制清理,UI 红色提示"已自动清理过期数据"
"""
import asyncio
import os

import asyncpg
import pytest

from private_agent.storage import disk_alert

TEST_DSN = os.environ.get(
    "PA_TEST_DSN",
    "postgresql://postgres:123123@localhost:5432/private_agent_test",
)


def test_evaluate_disk_alert_level_none_below_warning():
    """size_bytes < 1.5GB → level='none'。"""
    result = disk_alert.evaluate_disk_alert_level(
        size_bytes=int(1.4 * 1024 ** 3),
        warning_gb=1.5,
        block_new_session_gb=2.0,
        force_cleanup_gb=3.0,
    )
    assert result["level"] == "none"
    assert result["message"] == ""


def test_evaluate_disk_alert_level_yellow_at_warning():
    """1.5GB <= size_bytes < 2GB → level='yellow'。"""
    result = disk_alert.evaluate_disk_alert_level(
        size_bytes=int(1.5 * 1024 ** 3),
        warning_gb=1.5,
        block_new_session_gb=2.0,
        force_cleanup_gb=3.0,
    )
    assert result["level"] == "yellow"
    assert "存储空间即将不足" in result["message"]


def test_evaluate_disk_alert_level_orange_at_block():
    """2GB <= size_bytes < 3GB → level='orange'。"""
    result = disk_alert.evaluate_disk_alert_level(
        size_bytes=int(2.0 * 1024 ** 3),
        warning_gb=1.5,
        block_new_session_gb=2.0,
        force_cleanup_gb=3.0,
    )
    assert result["level"] == "orange"
    assert "无法新建会话" in result["message"]


def test_evaluate_disk_alert_level_red_at_force_cleanup():
    """size_bytes >= 3GB → level='red'。"""
    result = disk_alert.evaluate_disk_alert_level(
        size_bytes=int(3.0 * 1024 ** 3),
        warning_gb=1.5,
        block_new_session_gb=2.0,
        force_cleanup_gb=3.0,
    )
    assert result["level"] == "red"
    assert "已自动清理过期数据" in result["message"]


def test_evaluate_disk_alert_level_red_above_force_cleanup():
    """size_bytes > 3GB(远超)→ level='red'。"""
    result = disk_alert.evaluate_disk_alert_level(
        size_bytes=int(5.0 * 1024 ** 3),
        warning_gb=1.5,
        block_new_session_gb=2.0,
        force_cleanup_gb=3.0,
    )
    assert result["level"] == "red"


def test_evaluate_disk_alert_level_invalid_thresholds_raises():
    """阈值不满足 warning < block < force → ValueError。"""
    with pytest.raises(ValueError):
        disk_alert.evaluate_disk_alert_level(
            size_bytes=0,
            warning_gb=2.0,  # 反了
            block_new_session_gb=1.5,
            force_cleanup_gb=3.0,
        )


# ──────────────────────────────────────────────────────────────────────────────
# B4.2 - get_pg_data_dir_size:查询所有数据库总大小
# ──────────────────────────────────────────────────────────────────────────────


def test_get_pg_data_dir_size_returns_positive_int():
    """get_pg_data_dir_size 返回正整数(字节)。"""
    async def _run() -> int:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            return await disk_alert.get_pg_data_dir_size(conn)
        finally:
            await conn.close()

    size = asyncio.run(_run())
    assert isinstance(size, int)
    assert size > 0, f"PG 数据目录大小应 > 0,实际: {size}"


def test_get_pg_data_dir_size_includes_test_db():
    """get_pg_data_dir_size 至少 >= private_agent_test 库大小。"""
    async def _run() -> tuple[int, int]:
        conn = await asyncpg.connect(TEST_DSN)
        try:
            total = await disk_alert.get_pg_data_dir_size(conn)
            current = await conn.fetchval("SELECT pg_database_size(current_database())")
            return total, current
        finally:
            await conn.close()

    total, current = asyncio.run(_run())
    assert total >= current, (
        f"总大小 {total} 应 >= 当前库大小 {current}"
    )
