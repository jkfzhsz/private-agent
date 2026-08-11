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
    frozen_hash            VARCHAR(64),
    -- 工作区选择(画地为牢): 会话级工作目录(agent 操作范围告知层)
    workspace              TEXT,
    -- 阶段三批次1(T1.2): 会话级权限模式(default/plan/acceptEdits/cautious/deny_all)
    permission_mode         VARCHAR(20) NOT NULL DEFAULT 'default'
                            CHECK (permission_mode IN
                            ('default','plan','acceptEdits','cautious','deny_all')),
    -- V1.1-3.1 会话管理闭环: 文件夹分组(NULL=未分组)
    folder                  VARCHAR(100),
    -- V1.1-3.5 上下文可控: 会话级记忆开关(默认开)
    memory_enabled          BOOLEAN NOT NULL DEFAULT TRUE,
    -- V1.3-7.2 工作流自动化: 自动连续执行(用户发一条消息后自动多轮, 默认关)
    auto_execute            BOOLEAN NOT NULL DEFAULT FALSE,
    -- V1.3-7.2 工作流自动化: 自动执行最大轮数(默认 3)
    max_rounds              INT NOT NULL DEFAULT 3,
    -- V1.5 项-5 流程级暂停: 生成中用户"暂停"挂起轮次(区别于 cancel 终止;
    -- 与 status 正交 —— paused=True 时 status 仍为 active, resume 后继续)
    paused                  BOOLEAN NOT NULL DEFAULT FALSE,
    -- V1.5 项-1 子代理(ADR-012 §3.1 决策 A): 会话类型。
    -- main=普通对话会话; sub=子代理独立会话(委派产生, 复用 ReactLoop 全部
    -- 上下文/压缩/checkpoint 机制, list_sessions 过滤 sub 防污染历史列表 R9)
    -- 0.5.0 P3: monitor=主智能体监控会话(系统指标感知 + 优化闭环工具)
    kind                    VARCHAR(10) NOT NULL DEFAULT 'main'
                            CHECK (kind IN ('main', 'sub', 'monitor'))
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
    -- 推理过程(DeepSeek V4 系要求 assistant 消息原样回传 reasoning_content,
    -- 含 tool_calls 的消息也必须回传, 否则上游报错; AI-Agents-in-Depth 2.3.1)
    reasoning_content TEXT,
    tool_calls      JSONB,
    tool_call_id    TEXT,
    name            TEXT,
    -- 上下文工程分区元数据 (§3.2)
    zone            VARCHAR(20) CHECK (zone IN ('frozen', 'stable', 'active')),
    compressed      BOOLEAN NOT NULL DEFAULT FALSE,
    compressed_from JSONB,
    -- V1.1-3.3 消息精细化操作: 收藏标记
    starred         BOOLEAN NOT NULL DEFAULT FALSE,
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
    reasoning_content TEXT,
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
        'sandbox_execution', 'memory_extracted', 'memory_evicted',
        'compress', 'token_usage',
        'injection_alert', 'injection_blocked',
        'tool_error', 'delta',
        'tool_confirmation_required', 'tool_confirmation_result',
        'tool_loop_detected',
        -- V1.5 项-1(ADR-012 M4): 子代理可观测事件(stalled/kill/zombie/心跳故障)
        'subagent'
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
    type                VARCHAR(20) NOT NULL,               -- preference/fact/todo/decision/correction(阶段三 T3.4)
    content             TEXT NOT NULL,
    importance          FLOAT DEFAULT 0.5,
    source_session_id   BIGINT REFERENCES sessions(id) ON DELETE SET NULL,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    last_accessed_at    TIMESTAMPTZ DEFAULT NOW(),
    access_count        INT DEFAULT 0,
    is_active           BOOLEAN DEFAULT TRUE,
    -- 0.5.0 M1 场景独立(2026-08-08): 记忆作用域。
    -- global=全局记忆(用户偏好/项目概况/协作规则, 所有场景可见);
    -- office/data_analysis/frontend_design=场景私有记忆(技术标识, 显示层映射子瞻/白圭/清和)。
    -- 存量数据默认 global(不迁移旧记忆归属, 可选按 source_session_id 回填)。
    scope               VARCHAR(20) NOT NULL DEFAULT 'global'
);

CREATE INDEX idx_memories_user_type ON user_memories(user_id, type) WHERE is_active = TRUE;
CREATE INDEX idx_memories_importance ON user_memories(user_id, importance DESC);
-- 0.5.0 M1: 场景记忆注入/隔离查询索引(全局/场景混合排序注入用)
CREATE INDEX idx_memories_scope ON user_memories(user_id, scope, importance DESC)
    WHERE is_active = TRUE;

-- ==============================================================================
-- 5b. user_memories_archive - 巩固归档(0.5.0 M3 B3, 驱逐前先归档再 deactivate)
-- ==============================================================================
CREATE TABLE user_memories_archive (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT NOT NULL DEFAULT 1,
    memory_id       BIGINT,                                 -- 原记忆 id(软删除前)
    scope           VARCHAR(20) DEFAULT 'global',           -- 继承原记忆 scope
    type            VARCHAR(20),                            -- 原记忆类型
    content         TEXT,                                   -- 原内容(可选截断)
    summary         TEXT,                                   -- 1 行摘要(模型压缩)
    importance      FLOAT,
    archived_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_memories_archive_user ON user_memories_archive(user_id, archived_at);
CREATE INDEX idx_memories_archive_scope ON user_memories_archive(user_id, scope);

-- ==============================================================================
-- 5c. user_profile - 用户画像聚合(0.5.0 M3 B1, 全局偏好/项目概况聚合常驻)
-- ==============================================================================
CREATE TABLE user_profile (
    id                      BIGSERIAL PRIMARY KEY,
    user_id                 BIGINT NOT NULL DEFAULT 1,
    name                    VARCHAR(100),                   -- 称呼
    collaboration_prefs     TEXT,                           -- 协作偏好(聚合摘要)
    common_tools            TEXT,                           -- 常用工具
    communication_style     TEXT,                           -- 沟通风格
    ongoing_projects        JSONB DEFAULT '[]'::jsonb,      -- 进行中项目: [{name, status, key_path}]
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX idx_user_profile_user ON user_profile(user_id);

-- ==============================================================================
-- 5d. system_metrics - 系统性能指标快照(四窗口架构 P1, 主智能体监控数据源)
--     采集: apscheduler 后台任务(默认 60s); 写入: metrics_collector
--     消费: system_metrics_query 工具(主智能体分析)
-- ==============================================================================
CREATE TABLE system_metrics (
    id          BIGSERIAL PRIMARY KEY,
    ts          TIMESTAMPTZ NOT NULL DEFAULT now(),
    kind        VARCHAR(20) NOT NULL,            -- system/session/provider
    session_id  BIGINT,                          -- kind=session 时归属会话
    name        VARCHAR(100) NOT NULL,           -- cpu_usage/ram_mb/ws_conns/turn_latency_ms/...
    value       DOUBLE PRECISION NOT NULL,
    meta        JSONB DEFAULT '{}'::jsonb
);
CREATE INDEX idx_system_metrics_ts ON system_metrics(ts DESC);
CREATE INDEX idx_system_metrics_name ON system_metrics(name, ts DESC);

-- ==============================================================================
-- 5e. optim_log - 主智能体优化建议与执行记录(四窗口架构 P1, 审批流)
--     状态机: pending → approved/rejected → applied/failed
-- ==============================================================================
CREATE TABLE optim_log (
    id          BIGSERIAL PRIMARY KEY,
    ts          TIMESTAMPTZ NOT NULL DEFAULT now(),
    proposal    TEXT NOT NULL,                   -- 优化建议(主智能体生成)
    category    VARCHAR(30),                     -- context/tool/model/memory/performance
    status      VARCHAR(20) NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending','approved','rejected','applied','failed')),
    plan_json   JSONB,                           -- 结构化执行步骤(工具调用序列)
    result      TEXT,                            -- 执行结果/验证数据
    session_id  BIGINT,                          -- 提出时的监控会话
    reviewed_at TIMESTAMPTZ
);
CREATE INDEX idx_optim_log_status ON optim_log(status, ts DESC);


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
    scope       VARCHAR(20) NOT NULL CHECK (scope IN ('prompt', 'skill', 'harness', 'config', 'kb', 'stable_zone')),
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

-- ==============================================================================
-- 14. subagents - 子代理/任务委派(ADR-012 §3.1 完整 DDL, V1.5 项-1)
-- ==============================================================================
CREATE TABLE subagents (
    id            BIGSERIAL PRIMARY KEY,
    session_id    BIGINT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    parent_turn   INT NOT NULL,              -- 主会话触发委派的轮次
    parent_task   TEXT,                      -- 主代理分配的任务 id(同轮可多个)
    prompt        TEXT NOT NULL,             -- 委派指令(模型生成)
    model_id      VARCHAR(50),               -- 子代理模型(默认继承主会话)
    status        VARCHAR(20) NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending','running','succeeded','failed','cancelled')),
    result        TEXT,                      -- 最终结果(final content / error)
    tool_calls    INT NOT NULL DEFAULT 0,    -- 子代理工具调用次数(统计)
    error         TEXT,                      -- 失败原因枚举(§3.6): heartbeat_timeout /
                  --   heartbeat_timeout_after_restart / max_lifetime_exceeded / 异常栈摘要
    -- 监听/心跳(统一 UTC, 禁止本地时区 —— 多实例时钟偏移会误判超时 R3):
    last_heartbeat_at TIMESTAMPTZ,           -- 心跳上报; 初始 NULL; 首次心跳后刷新
    started_at        TIMESTAMPTZ,           -- running 置位时写入(硬总时长计时起点)
    stalled_at        TIMESTAMPTZ,           -- watchdog 判 stale 时写入(grace 宽限起点)
    finished_at       TIMESTAMPTZ,           -- 终态时刻(统一记录)
    restart_attempts  INT NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_subagents_session ON subagents(session_id, parent_turn);
CREATE INDEX idx_subagents_heartbeat ON subagents(status, last_heartbeat_at)
    WHERE status = 'running';   -- watchdog 扫描"运行中但心跳过期"的子代理

-- ==============================================================================
-- 15. skill_lessons - Skill 经验沉淀表(自进化经验存储, 2026-08-11 双轨进化)
--     lesson_category 区分: domain_skill(领域技巧) / project_evolution(项目进化)
--     / cross_domain(跨领域可迁移, scope='global')
-- ==============================================================================
CREATE TABLE skill_lessons (
    id              BIGSERIAL PRIMARY KEY,
    scope           VARCHAR(20) NOT NULL,          -- office/data_analysis/frontend_design/monitor/global
    lesson_category VARCHAR(20) NOT NULL DEFAULT 'domain_skill',  -- domain_skill/project_evolution/cross_domain
    task_summary    TEXT NOT NULL,                 -- 任务一句话摘要
    lesson_type     VARCHAR(20) NOT NULL,          -- success/failure/correction
    lesson_content  TEXT NOT NULL,                 -- 经验内容(成功模式/失败教训/纠正)
    tool_chain      JSONB DEFAULT '[]',            -- 使用的工具链序列
    source_session_id BIGINT,                      -- 来源会话
    source_turn     INT,                           -- 来源轮次
    is_active       BOOLEAN DEFAULT TRUE,          -- 软删除标记(Discard 用)
    importance      REAL DEFAULT 0.5,              -- 重要性(0-1, 反思时打分)
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT chk_lesson_type CHECK (lesson_type IN ('success', 'failure', 'correction')),
    CONSTRAINT chk_lesson_category CHECK (lesson_category IN ('domain_skill', 'project_evolution', 'cross_domain')),
    -- 双轨规则: monitor 必配 project_evolution; office/data_analysis/frontend_design 必配 domain_skill;
    --          global 必配 cross_domain(约束在应用层 EvolutionRepo.add() 冗余校验)
    CONSTRAINT chk_scope_category_consistency CHECK (
        (scope = 'monitor' AND lesson_category = 'project_evolution') OR
        (scope IN ('office', 'data_analysis', 'frontend_design') AND lesson_category = 'domain_skill') OR
        (scope = 'global' AND lesson_category = 'cross_domain')
    )
);

CREATE INDEX idx_skill_lessons_scope ON skill_lessons(scope) WHERE is_active = TRUE;
CREATE INDEX idx_skill_lessons_category ON skill_lessons(lesson_category, scope) WHERE is_active = TRUE;
CREATE INDEX idx_skill_lessons_created ON skill_lessons(created_at DESC);
