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
        # 已含 sandbox_execution 与 delta 说明 CHECK 已是最新,跳过
        if (
            "sandbox_execution" in def_text
            and "delta" in def_text
            and "tool_confirmation_required" in def_text
            and "memory_evicted" in def_text
        ):
            continue
        # 旧 CHECK, DROP 后 ADD 新 CHECK(17 种, 含权限确认 + memory_evicted)
        conname = r["conname"]
        await conn.execute(f'ALTER TABLE react_events DROP CONSTRAINT "{conname}"')
        await conn.execute(
            """
            ALTER TABLE react_events ADD CONSTRAINT react_events_event_type_check
            CHECK (event_type IN (
                'thinking', 'tool_call', 'tool_result', 'final', 'error', 'checkpoint',
                'sandbox_execution', 'memory_extracted', 'memory_evicted',
                'compress', 'token_usage',
                'injection_alert', 'injection_blocked',
                'tool_error', 'delta',
                'tool_confirmation_required', 'tool_confirmation_result'
            ))
            """
        )


async def migrate_kb_chunks_embedding_to_vector(conn: asyncpg.Connection) -> None:
    """B6 P0-5: kb_chunks.embedding BYTEA → vector(1024) + HNSW 索引。

    幂等:检查 information_schema.columns 是否已有 vector 类型。
    """
    row = await conn.fetchrow(
        "SELECT udt_name FROM information_schema.columns "
        "WHERE table_name='kb_chunks' AND column_name='embedding'"
    )
    if row and row["udt_name"] == "vector":
        return

    await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    await conn.execute(
        "ALTER TABLE kb_chunks ALTER COLUMN embedding TYPE vector(1024) "
        "USING '\\x'::bytea::text::vector"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_kb_chunks_embedding_hnsw ON kb_chunks "
        "USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 128)"
    )


async def migrate_all(conn: asyncpg.Connection) -> None:
    """执行 schema.sql 创建全部表与索引(蓝图 §9.14)。

    幂等:对已有库(如生产启动自动迁移场景)sessions 表已存在时跳过
    schema.sql 的 CREATE TABLE/INDEX(非幂等),仅执行末尾的增量 ALTER 补丁;
    全新库则完整执行 schema.sql + 增量补丁。可安全在每次启动时调用。

    M3: 末尾追加幂等 ALTER,为老部署(已有 sessions 表但无锁定列)补列。
    """
    sql = SCHEMA_FILE.read_text(encoding="utf-8")
    # B6: 确保 pgvector 扩展在 schema.sql 执行前安装(vector(1024) 类型需要)
    await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    # 幂等判断:sessions 表存在视为 schema 已创建,跳过非幂等的 CREATE 段
    sessions_exists = await conn.fetchval(
        "SELECT EXISTS (SELECT 1 FROM pg_tables "
        "WHERE schemaname='public' AND tablename='sessions')"
    )
    if not sessions_exists:
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
    # B6 P0-5: kb_chunks embedding BYTEA→vector(1024) + HNSW 索引(老部署补丁)
    await migrate_kb_chunks_embedding_to_vector(conn)
    # V2 上下文工程质量: messages.reasoning_content 列(老部署补列,新部署 schema.sql 已含)
    await conn.execute(
        "ALTER TABLE messages ADD COLUMN IF NOT EXISTS reasoning_content TEXT"
    )
    await conn.execute(
        "ALTER TABLE messages_archive ADD COLUMN IF NOT EXISTS reasoning_content TEXT"
    )
    # §3.10.3 [MVP]: version_snapshots.scope CHECK 扩容(老部署补丁,含 stable_zone)
    await _migrate_version_snapshots_scope_check(conn)


async def _migrate_version_snapshots_scope_check(conn: asyncpg.Connection) -> None:
    """幂等扩容 version_snapshots.scope CHECK, 加入 'stable_zone'(§3.10.3 存档)。"""
    rows = await conn.fetch(
        """
        SELECT conname, pg_get_constraintdef(oid) AS def
        FROM pg_constraint
        WHERE conrelid = 'version_snapshots'::regclass AND contype = 'c'
        """
    )
    for r in rows:
        def_text = r["def"] or ""
        if "stable_zone" in def_text:
            continue
        conname = r["conname"]
        await conn.execute(f'ALTER TABLE version_snapshots DROP CONSTRAINT "{conname}"')
        await conn.execute(
            """
            ALTER TABLE version_snapshots ADD CONSTRAINT version_snapshots_scope_check
            CHECK (scope IN ('prompt', 'skill', 'harness', 'config', 'kb', 'stable_zone'))
            """
        )
