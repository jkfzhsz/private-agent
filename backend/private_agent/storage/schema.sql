-- ==============================================================================
-- Private Agent - Postgres Schema (蓝图 §2.10 + §9.14 全表)
-- 13 张表 + 索引 + 扩展
-- ==============================================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;

-- ==============================================================================
-- 1. sessions - 会话元信息 (§2.10, §9.14 软删除:archived_at)
-- ==============================================================================
CREATE TABLE sessions (
    id              BIGSERIAL PRIMARY KEY,
    title           TEXT,
    status          VARCHAR(20) NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'interrupted', 'archived', 'error')),
    model_id        VARCHAR(50),
    skill_set       JSONB DEFAULT '[]'::jsonb,
    summary         TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    archived_at     TIMESTAMPTZ,
    -- M3 §7.3 会话锁定:Skill 激活后锁定版本,运行中拒绝切换
    locked_skill_name      VARCHAR(100),
    locked_skill_version   VARCHAR(20),
    frozen_hash            VARCHAR(64)
);

CREATE INDEX idx_sessions_status ON sessions(status) WHERE archived_at IS NULL;
CREATE INDEX idx_sessions_created ON sessions(created_at);

-- ==============================================================================
-- 2. messages - 会话消息历史 (§2.10, §3.2 分区元数据, §3.10 压缩标记, §9.14 软删除:compressed)
-- ==============================================================================
CREATE TABLE messages (
    id              BIGSERIAL PRIMARY KEY,
    session_id      BIGINT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    turn            INT NOT NULL DEFAULT 0,
    role            VARCHAR(20) NOT NULL CHECK (role IN ('system', 'user', 'assistant', 'tool')),
    content         TEXT,
    tool_calls      JSONB,
    tool_call_id    TEXT,
    name            TEXT,
    -- 上下文工程分区元数据 (§3.2)
    zone            VARCHAR(20) CHECK (zone IN ('frozen', 'stable', 'active')),
    compressed      BOOLEAN NOT NULL DEFAULT FALSE,
    compressed_from JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_messages_session_turn ON messages(session_id, turn);
CREATE INDEX idx_messages_session_created ON messages(session_id, created_at);

-- ==============================================================================
-- 3. messages_archive - 压缩归档消息 (§3.10, §9.14 无软删除,90 天物理删除)
-- ==============================================================================
CREATE TABLE messages_archive (
    id              BIGSERIAL PRIMARY KEY,
    original_msg_id BIGINT NOT NULL,
    session_id      BIGINT NOT NULL,
    turn            INT,
    role            VARCHAR(20),
    content         TEXT,
    tool_calls      JSONB,
    zone            VARCHAR(20),
    archived_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_messages_archive_session ON messages_archive(session_id);
CREATE INDEX idx_messages_archive_archived ON messages_archive(archived_at);

-- ==============================================================================
-- 4. react_events - ReAct 事件流 (§2.10, §2.13 完整 DDL, §9.14 7天TTL)
-- ==============================================================================
CREATE TABLE react_events (
    id          BIGSERIAL PRIMARY KEY,
    session_id  BIGINT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    turn        INT NOT NULL,
    event_type  TEXT NOT NULL CHECK (event_type IN (
        'thinking', 'tool_call', 'tool_result', 'final', 'error', 'checkpoint',
        'sandbox_execution', 'memory_extracted',
        'compress', 'token_usage',
        'injection_alert', 'injection_blocked',
        'tool_error', 'delta'
    )),
    payload     JSONB NOT NULL,
    created_at  TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_react_session_turn ON react_events(session_id, turn);
CREATE INDEX idx_react_created ON react_events(created_at);

-- ==============================================================================
-- 5. user_memories - 用户长期记忆 (§4.3 完整 DDL, §9.14 无TTL, 软删除:is_active)
-- ==============================================================================
CREATE TABLE user_memories (
    id                  BIGSERIAL PRIMARY KEY,
    user_id             BIGINT NOT NULL,                    -- 单人场景固定为 1
    type                VARCHAR(20) NOT NULL,               -- preference/fact/todo/decision
    content             TEXT NOT NULL,
    importance          FLOAT DEFAULT 0.5,
    source_session_id   BIGINT REFERENCES sessions(id) ON DELETE SET NULL,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    last_accessed_at    TIMESTAMPTZ DEFAULT NOW(),
    access_count        INT DEFAULT 0,
    is_active           BOOLEAN DEFAULT TRUE
);

CREATE INDEX idx_memories_user_type ON user_memories(user_id, type) WHERE is_active = TRUE;
CREATE INDEX idx_memories_importance ON user_memories(user_id, importance DESC);

-- ==============================================================================
-- 6. kb_documents - 知识库文档元数据 (§2.10, §4.6, §9.14 无TTL, 软删除:is_active)
-- ==============================================================================
CREATE TABLE kb_documents (
    id          BIGSERIAL PRIMARY KEY,
    source      VARCHAR(500) NOT NULL,          -- 文件名/URL/手动输入
    content     TEXT,                            -- 原始内容(MVP 保留)
    scenario    VARCHAR(50),                     -- 场景:office/data_analysis/frontend_design
    metadata    JSONB DEFAULT '{}'::jsonb,
    hash        VARCHAR(64),                     -- SHA-256 内容 hash(增量更新判断)
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_active   BOOLEAN DEFAULT TRUE
);

CREATE INDEX idx_kb_documents_source ON kb_documents(source) WHERE is_active = TRUE;
CREATE INDEX idx_kb_documents_scenario ON kb_documents(scenario) WHERE is_active = TRUE;

-- ==============================================================================
-- 7. kb_chunks - 知识库分块 + 向量 (§4.12 完整 DDL, §9.14 无TTL, 软删除:is_active)
-- ==============================================================================
CREATE TABLE kb_chunks (
    id          BIGSERIAL PRIMARY KEY,
    doc_id      BIGINT NOT NULL REFERENCES kb_documents(id) ON DELETE CASCADE,
    scenario    VARCHAR(50),
    source      VARCHAR(200),
    chunk_text  TEXT NOT NULL,
    embedding   vector(1024) NOT NULL,
    metadata    JSONB DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    is_active   BOOLEAN DEFAULT TRUE
);

CREATE INDEX idx_kb_chunks_doc ON kb_chunks(doc_id) WHERE is_active = TRUE;
-- HNSW 向量索引(蓝图 §4.11 line 3037-3039)
CREATE INDEX idx_kb_chunks_embedding_hnsw ON kb_chunks
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 128);

-- ==============================================================================
-- 8. version_snapshots - 版本快照 (§2.10, §7.3 Skill, §4.16 KB, §9.14 保留最近20个)
-- ==============================================================================
CREATE TABLE version_snapshots (
    id          BIGSERIAL PRIMARY KEY,
    scope       VARCHAR(20) NOT NULL CHECK (scope IN ('prompt', 'skill', 'harness', 'config', 'kb')),
    version     VARCHAR(20) NOT NULL,            -- 语义化版本
    payload     JSONB NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (scope, version)
);

CREATE INDEX idx_version_snapshots_scope ON version_snapshots(scope, created_at DESC);

-- ==============================================================================
-- 9. eval_datasets - 评估数据集 (§8.3 完整 DDL, §9.14 无TTL)
-- ==============================================================================
CREATE TABLE eval_datasets (
    id                      SERIAL PRIMARY KEY,
    sample_id               VARCHAR(100) NOT NULL UNIQUE,
    scenario                VARCHAR(50) NOT NULL,
    skill_name              VARCHAR(50) NOT NULL,
    skill_version           VARCHAR(20) NOT NULL,
    case_type               VARCHAR(20) NOT NULL CHECK (case_type IN ('normal', 'boundary', 'error')),
    difficulty              VARCHAR(20) NOT NULL CHECK (difficulty IN ('easy', 'medium', 'hard')),
    input                   TEXT NOT NULL,
    expected_react_trace    JSONB NOT NULL,
    expected_output         TEXT,
    split                   VARCHAR(10) NOT NULL DEFAULT 'test' CHECK (split IN ('train', 'test')),
    created_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at              TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    -- JSONB 结构强约束 (§8.3)
    CHECK (
        jsonb_typeof(expected_react_trace->'tool_calls') = 'array'
    )
);

CREATE INDEX idx_eval_datasets_scenario ON eval_datasets(scenario, skill_name);

-- ==============================================================================
-- 10. eval_runs - 评估运行 (§8.11 完整 DDL, §9.14 保留最近100次)
-- ==============================================================================
CREATE TABLE eval_runs (
    id              SERIAL PRIMARY KEY,
    run_id          UUID NOT NULL UNIQUE DEFAULT gen_random_uuid(),
    skill_name      VARCHAR(50) NOT NULL,
    skill_version   VARCHAR(20) NOT NULL,
    model_id        VARCHAR(50) NOT NULL,
    dataset_version VARCHAR(20) NOT NULL,
    eval_mode       VARCHAR(20) NOT NULL CHECK (eval_mode IN ('offline', 'replay')),
    mock_mode       BOOLEAN DEFAULT FALSE,
    variant         VARCHAR(20),                 -- A/B 测试预留 (V2)
    mock_enabled    BOOLEAN DEFAULT FALSE,
    metrics         JSONB,
    sample_results  JSONB,                       -- §8.16 list[{sample_id, metrics:{task_completion:{completion_rate}}}]
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ
);

CREATE INDEX idx_eval_runs_skill ON eval_runs(skill_name, skill_version);
CREATE INDEX idx_eval_runs_started ON eval_runs(started_at DESC);

-- ==============================================================================
-- 11. async_tasks - 异步任务状态 (§5.14 完整 DDL, §9.14 7天TTL)
-- ==============================================================================
CREATE TABLE async_tasks (
    id              BIGSERIAL PRIMARY KEY,
    session_id      BIGINT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    tool_name       VARCHAR(100) NOT NULL,
    status          VARCHAR(20) DEFAULT 'pending' CHECK (status IN ('pending', 'running', 'completed', 'failed', 'cancelled')),
    progress        FLOAT DEFAULT 0,
    result          JSONB,
    error           TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    completed_at    TIMESTAMPTZ
);

CREATE INDEX idx_async_tasks_session ON async_tasks(session_id, status);

-- ==============================================================================
-- 12. config_runtime - 运行时配置 + API Key 密文 + ws_offset (§2.10, §2.12, §9.14 无TTL)
-- ==============================================================================
CREATE TABLE config_runtime (
    key         TEXT PRIMARY KEY,
    value       JSONB NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ==============================================================================
-- 13. skills - Skills 元数据(PG 运行时副本) (§2.10, §2.11, §7.2, §9.14 无TTL)
-- ==============================================================================
CREATE TABLE skills (
    id              BIGSERIAL PRIMARY KEY,
    name            VARCHAR(100) NOT NULL UNIQUE,
    version         VARCHAR(20) NOT NULL,
    description     TEXT,
    preferred_model_tag VARCHAR(50),
    manifest        JSONB NOT NULL,              -- 完整 manifest.yaml 内容
    system_prompt   TEXT,                         -- system_prompt.md 内容
    tools           JSONB DEFAULT '[]'::jsonb,   -- tools.yaml 内容
    is_enabled      BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_skills_enabled ON skills(is_enabled) WHERE is_enabled = TRUE;
