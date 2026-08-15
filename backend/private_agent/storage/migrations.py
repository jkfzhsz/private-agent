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
            and "tool_loop_detected" in def_text
            # V1.5 项-1(ADR-012 M4): subagent 事件类型(新部署 schema.sql 已含)
            and "subagent" in def_text
            # 0.5.1 A-1(2026-08-15): context_injected 上下文注入审计
            and "context_injected" in def_text
        ):
            continue
        # 旧 CHECK, DROP 后 ADD 新 CHECK(20 种, 含子代理可观测事件 + 注入审计)
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
                'tool_confirmation_required', 'tool_confirmation_result',
                'tool_loop_detected',
                'subagent',
                'context_injected'
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
    # 工作区选择(画地为牢): sessions.workspace 会话级工作目录
    await conn.execute(
        "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS workspace TEXT"
    )
    # 阶段三批次1(T1.2, 调研 round2 §4.2.1): 会话级权限模式
    # default/plan/acceptEdits/cautious/deny_all(老部署补列,新部署 schema.sql 已含)
    await conn.execute(
        "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS permission_mode "
        "VARCHAR(20) NOT NULL DEFAULT 'default' "
        "CHECK (permission_mode IN "
        "('default','plan','acceptEdits','cautious','deny_all'))"
    )
    # §3.10.3 [MVP]: version_snapshots.scope CHECK 扩容(老部署补丁,含 stable_zone)
    await _migrate_version_snapshots_scope_check(conn)
    # V1.1-3.1 会话管理闭环: sessions.folder 文件夹分组(老部署补列,新部署 schema.sql 已含)
    await conn.execute(
        "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS folder VARCHAR(100)"
    )
    # V1.1-3.3 消息精细化操作: messages.starred 收藏标记(老部署补列,新部署 schema.sql 已含)
    await conn.execute(
        "ALTER TABLE messages ADD COLUMN IF NOT EXISTS starred BOOLEAN NOT NULL DEFAULT FALSE"
    )
    # V1.1-3.5 上下文可控: sessions.memory_enabled 会话级记忆开关
    await conn.execute(
        "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS memory_enabled BOOLEAN NOT NULL DEFAULT TRUE"
    )
    # V1.3-7.2 工作流自动化: sessions.auto_execute / max_rounds 自动连续执行
    await conn.execute(
        "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS auto_execute BOOLEAN NOT NULL DEFAULT FALSE"
    )
    await conn.execute(
        "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS max_rounds INT NOT NULL DEFAULT 3"
    )
    # V1.5 项-5 流程级暂停: sessions.paused 生成中挂起标记(与 status 正交)
    await conn.execute(
        "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS paused BOOLEAN NOT NULL DEFAULT FALSE"
    )
    # V1.5 项-1 子代理(ADR-012 §3.1): sessions.kind 会话类型(main/sub),
    # 老部署补列; 新部署 schema.sql 已含
    await conn.execute(
        "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS kind "
        "VARCHAR(10) NOT NULL DEFAULT 'main' "
        "CHECK (kind IN ('main', 'sub'))"
    )
    # V1.5 项-1 子代理: subagents 表(ADR-012 §3.1 完整 DDL, 幂等)
    await _migrate_subagents_table(conn)
    # 0.5.0 M1(2026-08-08): 场景独立记忆 —— user_memories.scope 列 + 索引、
    # user_memories_archive / user_profile 表(老部署补丁, 新部署 schema.sql 已含)
    await _migrate_memory_scope(conn)
    # 0.5.0 P1(2026-08-08): 四窗口架构 —— system_metrics / optim_log 表
    await _migrate_monitor_tables(conn)
    # 0.5.0 P3: sessions.kind 扩容(monitor 主智能体会话)
    await _migrate_sessions_kind_monitor(conn)
    # 2026-08-11 Phase 1: skill_lessons 经验沉淀表(双轨进化, 幂等)
    await _migrate_add_skill_lessons(conn)
    # 2026-08-12 Phase 2: 会话附加技能表(多技能调用 —— 主技能 locked_skill_name
    # 不变, 附加技能可叠加; 供 _get_system_prompt/_get_frozen_tools 合并注入)
    await _migrate_add_session_supplementary_skills(conn)
    # 2026-08-13 类型感知限流: subagents.task_type 列(任务类型标注, 可观测/过滤)
    await conn.execute(
        "ALTER TABLE subagents ADD COLUMN IF NOT EXISTS "
        "task_type VARCHAR(20) NOT NULL DEFAULT 'other'"
    )


async def _migrate_add_session_supplementary_skills(conn: asyncpg.Connection) -> None:
    """2026-08-12 Phase 2: 新增 session_supplementary_skills 表(多技能叠加)。

    老部署补丁; 新部署 schema.sql 已含完整 DDL。幂等: 表已存在时跳过。
    """
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS session_supplementary_skills (
            id          BIGSERIAL PRIMARY KEY,
            session_id  BIGINT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            skill_name  VARCHAR(100) NOT NULL,
            added_turn  INT NOT NULL DEFAULT 0,
            added_by    VARCHAR(20) NOT NULL DEFAULT 'picker',
            created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE(session_id, skill_name)
        )
    """)
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sss_session "
        "ON session_supplementary_skills(session_id)"
    )


async def _migrate_add_skill_lessons(conn: asyncpg.Connection) -> None:
    """2026-08-11: 新增 skill_lessons 表用于经验沉淀（双轨进化）。

    老部署补丁; 新部署 schema.sql 已含完整 DDL。幂等: 表已存在时跳过。
    """
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS skill_lessons (
            id              BIGSERIAL PRIMARY KEY,
            scope           VARCHAR(20) NOT NULL,
            lesson_category VARCHAR(20) NOT NULL DEFAULT 'domain_skill',
            task_summary    TEXT NOT NULL,
            lesson_type     VARCHAR(20) NOT NULL,
            lesson_content  TEXT NOT NULL,
            tool_chain      JSONB DEFAULT '[]',
            source_session_id BIGINT,
            source_turn     INT,
            is_active       BOOLEAN DEFAULT TRUE,
            importance      REAL DEFAULT 0.5,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT chk_lesson_type CHECK (lesson_type IN ('success', 'failure', 'correction')),
            CONSTRAINT chk_lesson_category CHECK (lesson_category IN ('domain_skill', 'project_evolution', 'cross_domain')),
            CONSTRAINT chk_scope_category_consistency CHECK (
                (scope = 'monitor' AND lesson_category = 'project_evolution') OR
                (scope IN ('office', 'data_analysis', 'frontend_design') AND lesson_category = 'domain_skill') OR
                (scope = 'global' AND lesson_category = 'cross_domain')
            )
        )
    """)
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_skill_lessons_scope
        ON skill_lessons(scope) WHERE is_active = TRUE
    """)
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_skill_lessons_category
        ON skill_lessons(lesson_category, scope) WHERE is_active = TRUE
    """)
    await conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_skill_lessons_created
        ON skill_lessons(created_at DESC)
    """)


async def _migrate_sessions_kind_monitor(conn: asyncpg.Connection) -> None:
    """0.5.0 P3: sessions.kind 枚举扩容, 支持 monitor(主智能体监控会话)。

    老部署 CHECK 约束仅含 ('main','sub'), 需重建约束加入 'monitor'。
    """
    constraint = await conn.fetchval(
        "SELECT conname FROM pg_constraint "
        "WHERE conrelid = 'sessions'::regclass "
        "AND contype = 'c' AND pg_get_constraintdef(oid) ILIKE '%kind%'"
    )
    if constraint:
        await conn.execute(f"ALTER TABLE sessions DROP CONSTRAINT {constraint}")
    await conn.execute(
        "ALTER TABLE sessions ADD CONSTRAINT sessions_kind_check "
        "CHECK (kind IN ('main', 'sub', 'monitor'))"
    )


async def _migrate_monitor_tables(conn: asyncpg.Connection) -> None:
    """0.5.0 P1: 主智能体监控数据链路表(system_metrics / optim_log)。

    老部署补表(幂等), 新部署 schema.sql 已含。
    """
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS system_metrics (
            id          BIGSERIAL PRIMARY KEY,
            ts          TIMESTAMPTZ NOT NULL DEFAULT now(),
            kind        VARCHAR(20) NOT NULL,
            session_id  BIGINT,
            name        VARCHAR(100) NOT NULL,
            value       DOUBLE PRECISION NOT NULL,
            meta        JSONB DEFAULT '{}'::jsonb
        )
        """
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_system_metrics_ts "
        "ON system_metrics(ts DESC)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_system_metrics_name "
        "ON system_metrics(name, ts DESC)"
    )
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS optim_log (
            id          BIGSERIAL PRIMARY KEY,
            ts          TIMESTAMPTZ NOT NULL DEFAULT now(),
            proposal    TEXT NOT NULL,
            category    VARCHAR(30),
            status      VARCHAR(20) NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending','approved','rejected','applied','failed')),
            plan_json   JSONB,
            result      TEXT,
            session_id  BIGINT,
            reviewed_at TIMESTAMPTZ
        )
        """
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_optim_log_status "
        "ON optim_log(status, ts DESC)"
    )


async def _migrate_memory_scope(conn: asyncpg.Connection) -> None:
    """0.5.0 M1: user_memories.scope 列 + 部分索引 + 归档/画像表(幂等)。

    - scope VARCHAR(20) NOT NULL DEFAULT 'global'(存量数据默认 global, 不阻断);
    - idx_memories_scope(user_id, scope, importance DESC) WHERE is_active(注入/隔离);
    - user_memories_archive(巩固归档 B3) / user_profile(画像聚合 B1) 表补建。
    """
    await conn.execute(
        "ALTER TABLE user_memories ADD COLUMN IF NOT EXISTS "
        "scope VARCHAR(20) NOT NULL DEFAULT 'global'"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_memories_scope "
        "ON user_memories(user_id, scope, importance DESC) "
        "WHERE is_active = TRUE"
    )
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_memories_archive (
            id              BIGSERIAL PRIMARY KEY,
            user_id         BIGINT NOT NULL DEFAULT 1,
            memory_id       BIGINT,
            scope           VARCHAR(20) DEFAULT 'global',
            type            VARCHAR(20),
            content         TEXT,
            summary         TEXT,
            importance      FLOAT,
            archived_at     TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_memories_archive_user "
        "ON user_memories_archive(user_id, archived_at)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_memories_archive_scope "
        "ON user_memories_archive(user_id, scope)"
    )
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS user_profile (
            id                      BIGSERIAL PRIMARY KEY,
            user_id                 BIGINT NOT NULL DEFAULT 1,
            name                    VARCHAR(100),
            collaboration_prefs     TEXT,
            common_tools            TEXT,
            communication_style     TEXT,
            ongoing_projects        JSONB DEFAULT '[]'::jsonb,
            updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    await conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_user_profile_user "
        "ON user_profile(user_id)"
    )


async def _migrate_subagents_table(conn: asyncpg.Connection) -> None:
    """V1.5 项-1(ADR-012 §3.1): 创建 subagents 表(幂等, 老部署补建)。

    与 schema.sql 中第 14 张表完全同构; 已有库(老部署)直接补建,
    新部署 schema.sql 已含 → IF NOT EXISTS 跳过。索引同步补建。
    """
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS subagents (
            id            BIGSERIAL PRIMARY KEY,
            session_id    BIGINT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            parent_turn   INT NOT NULL,
            parent_task   TEXT,
            prompt        TEXT NOT NULL,
            model_id      VARCHAR(50),
            status        VARCHAR(20) NOT NULL DEFAULT 'pending'
                          CHECK (status IN ('pending','running','succeeded','failed','cancelled')),
            result        TEXT,
            tool_calls    INT NOT NULL DEFAULT 0,
            error         TEXT,
            last_heartbeat_at TIMESTAMPTZ,
            started_at        TIMESTAMPTZ,
            stalled_at        TIMESTAMPTZ,
            finished_at       TIMESTAMPTZ,
            restart_attempts  INT NOT NULL DEFAULT 0,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_subagents_session "
        "ON subagents(session_id, parent_turn)"
    )
    await conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_subagents_heartbeat "
        "ON subagents(status, last_heartbeat_at) WHERE status = 'running'"
    )


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
