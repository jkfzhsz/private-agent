"""蓝图 §2.15 storage/migrations.py - Postgres schema 迁移。

B4.1:执行 schema.sql 创建 13 张表(蓝图 §9.14 全表清单)。
后续:版本化迁移(M1+ 需要时引入 alembic)。
"""
from __future__ import annotations

from pathlib import Path

import asyncpg

SCHEMA_FILE = Path(__file__).parent / "schema.sql"


async def migrate_react_events_event_type_check(conn: asyncpg.Connection) -> None:
    """B1 P0-8: 扩容 react_events.event_type CHECK 约束至 13 种事件类型。

    幂等:用 pg_get_constraintdef 反查当前 CHECK 内容是否已含 'sandbox_execution',
    若无则 DROP + ADD CONSTRAINT(不依赖约束名,避免命名差异导致重复 ALTER)。

    新增事件类型:
    - sandbox_execution, memory_extracted(现有代码已 emit)
    - compress, token_usage(B4 P0-1/P0-4 新增)
    - injection_alert, injection_blocked(B3 P0-2 新增)
    - tool_error(§4.10 异常入库告警)
    """
    # 查 react_events 表上所有 CHECK 约束的定义
    rows = await conn.fetch(
        """
        SELECT conname, pg_get_constraintdef(oid) AS def
        FROM pg_constraint
        WHERE conrelid = 'react_events'::regclass AND contype = 'c'
        """
    )
    for r in rows:
        def_text = r["def"] or ""
        # 已含 sandbox_execution 说明 CHECK 已扩容,跳过
        if "sandbox_execution" in def_text:
            continue
        # 旧 CHECK(仅 6 种事件),DROP 后 ADD 新 CHECK(13 种)
        conname = r["conname"]
        await conn.execute(f'ALTER TABLE react_events DROP CONSTRAINT "{conname}"')
        await conn.execute(
            """
            ALTER TABLE react_events ADD CONSTRAINT react_events_event_type_check
            CHECK (event_type IN (
                'thinking', 'tool_call', 'tool_result', 'final', 'error', 'checkpoint',
                'sandbox_execution', 'memory_extracted',
                'compress', 'token_usage',
                'injection_alert', 'injection_blocked',
                'tool_error'
            ))
            """
        )


async def migrate_all(conn: asyncpg.Connection) -> None:
    """执行 schema.sql 创建全部表与索引(蓝图 §9.14)。

    幂等:CREATE TABLE/INDEX 无 IF NOT EXISTS 时会在重复执行时报错;
    调用方应先 DROP SCHEMA public CASCADE 再调用(见 test_migrations.py fixture)。

    M3: 末尾追加幂等 ALTER,为老部署(已有 sessions 表但无锁定列)补列。
    """
    sql = SCHEMA_FILE.read_text(encoding="utf-8")
    await conn.execute(sql)
    # M3 §7.3 会话锁定列(老部署补列,新部署 schema.sql 已含)
    await conn.execute(
        "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS locked_skill_name VARCHAR(100)"
    )
    await conn.execute(
        "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS locked_skill_version VARCHAR(20)"
    )
    await conn.execute(
        "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS frozen_hash VARCHAR(64)"
    )
    # M4 §8.4 eval_datasets.split 列(老部署补列,新部署 schema.sql 已含)
    await conn.execute(
        "ALTER TABLE eval_datasets ADD COLUMN IF NOT EXISTS "
        "split VARCHAR(10) NOT NULL DEFAULT 'test' CHECK (split IN ('train', 'test'))"
    )
    # M4 §8.11/§8.16 eval_runs.sample_results 列(老部署补列,新部署 schema.sql 已含)
    await conn.execute(
        "ALTER TABLE eval_runs ADD COLUMN IF NOT EXISTS sample_results JSONB"
    )
    # B1 P0-8: react_events.event_type CHECK 扩容(老部署补丁,新部署 schema.sql 已含)
    await migrate_react_events_event_type_check(conn)
