# M0 基础骨架层 Spec (m0-skeleton)

> Status: RETROACTIVE (回溯补写,基于 commit 152780d → f1bbaeb 实际交付内容)
> Author: zongxin
> Last updated: 2026-08-01
> 蓝图章节: §9.4 M0 节 + §9.6 step 1-6 + §2.1/§2.2/§2.3/§2.7/§2.10/§2.12/§2.13/§2.15

## Background

本 spec 为 M0 阶段的回溯文档。M0 实际开发早于 dev-grill-docs 流程建立,未独立产出 design/plan 文件,现根据 commit 链(152780d → 333f281 → b7f9ed2 → 42a2029 → f1bbaeb)与蓝图 §9.4 M0 节回溯补写,用于闭合 `.claude/artifacts/` 文档链。

M0 目标:搭建可运行的最小骨架,前后端能通信、Postgres 能读写、配置能加载。

## In scope(实际交付)

### A. 四层骨架 + 目录结构(§2.1、§2.15)
- `backend/private_agent/` Python 包结构:api/config/core/eval/knowledge/memory/models/observability/sandbox/skills/storage/tools 子包
- `frontend/{main,preload,renderer,static}` Electron 结构
- `backend/tests/` 测试目录
- `backend/pyproject.toml` 项目依赖

### B. 进程模型 + 通信协议(§2.2、§2.3)
- `main.py`:FastAPI Sidecar 启动入口 + uvicorn 监听(端口从 config 读取)
- HTTP 控制面:`GET /` + `GET /health`
- WS 数据面:`/ws` 端点,支持 ping/pong + replay(ws_offset 补发)+ ack(回写 config_runtime)+ user_message
- `storage/ws_offset.py`:build_replay_messages + handle_ack

### C. Postgres 全表结构(§2.10、§9.14)
- `storage/schema.sql`:13 张表(sessions/messages/messages_archive/react_events/user_memories/kb_documents/kb_chunks/version_snapshots/eval_datasets/eval_runs/async_tasks/config_runtime/skills)
- `storage/migrations.py`:migrate_all(conn) 幂等执行 schema.sql
- 扩展:pgcrypto(密码生成)
- kb_chunks.embedding M0 占位 BYTEA(M2 迁移 vector)

### D. 磁盘分级告警 + TTL 清理(§2.10)
- `storage/disk_alert.py`:evaluate_disk_alert_level(1.5GB yellow / 2GB orange / 3GB red)+ get_disk_status + get_pg_data_dir_size
- `storage/ttl_cleanup.py`:run_ttl_cleanup(react_events 30 天 + messages_archive 90 天)
- 3GB 强制清理时收紧 react_events 保留为 7 天

### E. 配置分层 + API Key 加密(§2.7、§2.12)
- `config/loader.py`:load_config() 读 config.yaml
- `config/secrets.py`:AES-256-GCM 加密/解密 API Key
- `config/config.yaml`:全局配置骨架(§9.13)
- `config_runtime` 表:运行时配置覆盖(key-value JSONB)

### F. 结构化日志(§2.13)
- `observability/logging.py`:setup_logger(name) → 文件 + stdout,含 trace_id 字段(留空)
- 全模块使用 setup_logger,禁用 print

## Out of scope(留 M1+)

- ReAct 核心循环(§2.4,M1 step 7)
- 模型适配器(§2.7,M1 step 8)
- 上下文管理器三区构建(§3.1-3.2,M1 step 9)
- 磁盘告警 HTTP/WS 闭环(留 M1 AC-4 闭环)
- TTL 清理 APScheduler 调度(留 M1 startup hook)
- ws_offset ACK 协议(留 M1 AC-6 闭环)
- 知识库 RAG(第 4 章,M2)
- 工具层(第 5 章,M2)
- 沙箱(第 6 章,M2)
- Skills(第 7 章,M3)
- 评估闭环(第 8 章,M4)

## Acceptance criteria(蓝图 §9.4 M0 Done Criteria)

- AC-1: Electron 启动后能拉起 Python Sidecar,WS 连接建立成功
- AC-2: Postgres 全部表创建成功,可插入/查询基础数据
- AC-3: config.yaml 加载成功,API Key 加密存储可读写
- AC-4: 磁盘告警在 1.5GB/2GB/3GB 三级阈值触发 UI 提示
- AC-5: 日志写入本地文件 + stdout,含 trace_id 字段(留空)

## 交付证据

| AC | commit | 文件 | 测试 |
|---|---|---|---|
| AC-1 | f1bbaeb / 816f528 | main.py + frontend/main/sidecar.ts | test_main_startup.py / test_ws.py / test_ws_user_message.py |
| AC-2 | b7f9ed2 | storage/schema.sql + migrations.py | test_migrations.py::test_all_13_tables_exist |
| AC-3 | b7f9ed2 + 42a2029 | config/loader.py + config/secrets.py | test_config.py / test_secrets.py / test_config_runtime.py |
| AC-4 | f1bbaeb | storage/disk_alert.py | test_disk_alert.py / test_disk_alert_status.py / test_disk_status_api.py |
| AC-5 | b7f9ed2 + 42a2029 | observability/logging.py | test_logging.py |

## Core entities

| Entity | Type | Key fields | Relationship |
|---|---|---|---|
| FastAPI app | module | app, /health, /ws | Sidecar 入口 |
| asyncpg Pool | module | _pool singleton | DB 连接池 |
| migrate_all | function | conn | schema 初始化 |
| evaluate_disk_alert_level | function | size_bytes | 三色分级 |
| run_ttl_cleanup | function | conn, retention | TTL 清理 |
| setup_logger | function | name | 结构化日志 |
| load_config | function | — | config.yaml 加载 |
| SecretsBox | class | key | AES-256-GCM |

## Notes

- M0 实际测试数:60 tests green(commit f1bbaeb)
- code-review P1/P2 修复在 commit 42a2029(wire setup_logger + config-driven port + drop unused imports)
- 本 spec 为回溯文档,不参与 dev-grill-docs 流程评审
