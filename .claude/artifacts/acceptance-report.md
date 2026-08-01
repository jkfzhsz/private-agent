# Private Agent MVP 项目完整验收报告

**验收日期**: 2026-08-01
**验收范围**: M0-M4 五个里程碑(蓝图 §9.4 全部 Done Criteria + §9.7 MVP 验收标准 30 项)
**当前 HEAD**: `506e731` (master)
**核对方式**: 真实代码 + git log + 全量 pytest + 四类闭环验证,不凭记忆

---

## 0. 验收总览

### 0.1 全量测试统计

| 指标 | 数值 |
|---|---|
| 通过 | 692 |
| 失败 | 0 |
| 跳过 | 0 |
| 警告 | 5(均为 FastAPI on_event deprecation,非功能问题) |
| 执行时长 | 152.01s |
| 命令 | `$env:PA_DB_PASSWORD="123123"; $env:PA_TEST_DSN="postgresql://postgres:123123@localhost:5432/private_agent_test"; python -m pytest` |

**结论**: 测试套件全部绿,此前记忆中"15 个 DB 失败"实为环境变量未设置导致,环境就绪后全过。

### 0.2 里程碑完成度总览

| 阶段 | 名称 | 完全完成 | 部分完成 | 未完成 | 完成度 | 风险评级 |
|---|---|---|---|---|---|---|
| M0 | 基础骨架 | 3 | 2 | 0 | **60%** | 中 |
| M1 | 编排核心 | 1 | 2 | 4 | **14%** | **高** |
| M2 | 能力层 | 0 | 7 | 1 | **~50%** | **高** |
| M3 | 场景化 | 5 | 3 | 0 | **81%** | 中低 |
| M4 | 评估闭环 | 8 | 0 | 0 | **96%** | 中 |
| **总计** | — | **17 / 36** | **14 / 36** | **5 / 36** | **~63%** | **中高** |

**总体结论**: 项目按 dev-auto 流程推进 M0-M4 全部 5 个里程碑,5 个 spec 链完整,692 个测试全部通过,M4 评估闭环 8/8 达成且 54 条 AC 100% 测试覆盖。**但 M1-b 全部 4 项(上下文压缩/注入防护/checkpoint/计费)完全未实现,M2 RAG 核心链路(embedding/向量检索/HNSW/reranker)停留在 stub/mock 阶段**,这两项构成 MVP 真实可用的最大阻塞。详见第 10 节风险项分级。

### 0.3 Commit 链(实际 git log)

| 阶段 | 终态 commit | 备注 |
|---|---|---|
| M0 | `f1bbaeb` | 经 `333f281` → `b7f9ed2` → `42a2029` → `f1bbaeb`(60 tests green) |
| M1 | `abc39f5` | merge `codex/m1-react-loop` |
| M2 | `b8da0a4` | `c9fbfba`(tools) → `68f5e31`(merge) → `fc10315`(sandbox) → `b8da0a4`(RAG) |
| M3 | `070bbdb` | `13d6725`(framework) → `182a9c1`(office) → `84917ba`(data_analysis) → `ced655c`(frontend_design) → `070bbdb`(剩余 DC) |
| M4 | `506e731` | `fe3838c`(foundation) → `8ff3f5b`(metrics-judge) → `1883878`(runner-replay) → `506e731`(version-compare-rollback + continuous-evolution 合并) |

> ⚠️ `project_memory.md` 中记录的 M0=`d3d6f7c` / M1=`3e9b7d2` 与实际 `.git/logs/HEAD` 不符,本报告以 git log 实际为准。

---

## 1. M0 基础骨架层 Done Criteria 核对(5 条)

### AC-1 Electron 启动后能拉起 Python Sidecar,WS 连接建立成功 — ⚠️ 部分完成

| 子项 | 状态 | 证据 |
|---|---|---|
| WS `/ws` 端点注册 | ✅ | [main.py#L91](file:///d:/Private%20agent/backend/private_agent/main.py#L91) `@app.websocket("/ws")`,commit `333f281` |
| WS ping/pong 测试 | ✅ | `backend/tests/test_ws.py::test_ws_can_connect_and_ping_pong` |
| Python sidecar 独立启动 | ✅ | `backend/tests/test_main_startup.py::test_sidecar_startup_prints_http_port`(subprocess 拉起 `python -m private_agent.main`) |
| **Electron spawn Python sidecar** | ❌ | `frontend/main/sidecar.ts` 全文仅 3 行注释 + `export {};`,`frontend/main/index.ts` 同样为空占位。**未实现 spawn 逻辑** |

**备注**: Python 侧 WS 端点与独立启动均可用,但蓝图 §2.15 要求的 Electron 主进程拉起 Python Sidecar 的进程管理逻辑完全缺失。M0 spec(`m0-skeleton.md` plan)将此项列为 "B2.1 实现:spawn Python 进程",但实际未落地。

---

### AC-2 Postgres 全部表创建成功,可插入/查询基础数据 — ✅ 已完成

- **建表文件**: [schema.sql](file:///d:/Private%20agent/backend/private_agent/storage/schema.sql) (commit `b7f9ed2`)
- **表清单(13 张)**: sessions / messages / messages_archive / react_events / user_memories / kb_documents / kb_chunks / version_snapshots / eval_datasets / eval_runs / async_tasks / config_runtime / skills
- **测试**: `backend/tests/test_migrations.py::test_all_13_tables_exist`(EXPECTED_TABLES 列表 13 项,含 messages_archive)
- **migrate 函数**: [migrations.py](file:///d:/Private%20agent/backend/private_agent/storage/migrations.py) `migrate_all(conn)` 含幂等列迁移(locked_skill_name/version/frozen_hash、eval_datasets.split、eval_runs.sample_results)

**备注**: 蓝图 §9.4/§9.7 文字写"12 张",但 schema.sql 实际 13 张(多了 `messages_archive`,对应蓝图 §3.10 压缩归档表)。测试文件 `test_migrations.py:16` 注释明确写"蓝图 §9.14 全表清单(13 张)"。属蓝图文字与实际一致化,无偏离。

---

### AC-3 config.yaml 加载成功,API Key 加密存储可读写 — ✅ 已完成

- **loader**: [loader.py#L30](file:///d:/Private%20agent/backend/private_agent/config/loader.py#L30) `load_config()`(commit `b7f9ed2`),含 MVP 校验 + runtime override 合并(`load_config_with_overrides`)
- **加密实现**: [secrets.py#L22](file:///d:/Private%20agent/backend/private_agent/config/secrets.py#L22) `encrypt_api_key()` / `decrypt_api_key()`,使用 `cryptography.hazmat.primitives.ciphers.aead.AESGCM`,nonce=12 字节,master_key 从 `PA_MASTER_KEY` 环境变量读取
- **测试**:
  - `backend/tests/test_config.py`(10 个测试,含 protocol_version 锁定、mcp.servers 校验)
  - `backend/tests/test_secrets.py::test_encrypt_decrypt_roundtrip` / `::test_decrypt_with_wrong_key_raises` / `::test_encrypted_payload_has_nonce_and_ciphertext`

---

### AC-4 磁盘告警在 1.5GB/2GB/3GB 三级阈值触发 UI 提示 — ✅ 已完成

- **实现**: [disk_alert.py#L22](file:///d:/Private%20agent/backend/private_agent/storage/disk_alert.py#L22) `evaluate_disk_alert_level()`(commit `f1bbaeb`),三级返回 none/yellow/orange/red + `:88` `get_disk_status()` 组合查询
- **配置**: [config.yaml#L223-L226](file:///d:/Private%20agent/backend/config/config.yaml#L223) `observability.disk.warning_gb: 1.5` / `block_new_session_gb: 2.0` / `force_cleanup_gb: 3.0`
- **测试**: `backend/tests/test_disk_alert.py`(6 个测试,覆盖 none/yellow/orange/red/远超/非法阈值) + `test_disk_alert_status.py` + `test_disk_status_api.py`

**备注**: 告警函数与配置齐全,M0 spec 注明"磁盘告警 HTTP/WS 闭环"留 M1 闭环(GET /admin/disk-status 在 M1 补)。AC-4 本身(三级阈值触发)已满足。

---

### AC-5 日志写入本地文件 + stdout,含 trace_id 字段(留空) — ⚠️ 部分完成

| 子项 | 状态 | 证据 |
|---|---|---|
| stdout JSON 日志 | ✅ | [logging.py#L55](file:///d:/Private%20agent/backend/private_agent/observability/logging.py#L55) `StreamHandler(stream if stream is not None else sys.stdout)` |
| trace_id 字段(留空) | ✅ | [logging.py#L28](file:///d:/Private%20agent/backend/private_agent/observability/logging.py#L28) `"trace_id": getattr(record, "trace_id", None)` 默认 null |
| **本地文件写入** | ❌ | `logging.py` **无 FileHandler**;[config.yaml#L220](file:///d:/Private%20agent/backend/config/config.yaml#L220) `file_path: "${WORKSPACE}/logs/agent.log"` 配置存在但 `setup_logger` 从未读取或使用 |
| 测试 | ⚠️ | `backend/tests/test_logging.py` 5 个测试全部用 `io.StringIO` 缓冲,**无文件写入测试** |

**备注**: AC 明确要求"本地文件 + stdout"双通道,实际只实现 stdout 单通道。file_path 配置项是死配置。这是 M0 的真实缺口。

---

### M0 完成度

- **完全完成**: AC-2 / AC-3 / AC-4 = 3 条
- **部分完成**: AC-1 / AC-5 = 2 条
- **完成度**: 3/5 = **60%**
- **风险评级**: **中** — Electron sidecar 进程管理缺失影响端到端可用性,日志文件通道缺失影响可观测性留存

---

## 2. M1 编排核心层 Done Criteria 核对(7 条)

### AC-1 ReAct 循环完整执行 + WS 流式渲染 — ✅ 已完成

- **实现**: [react_loop.py#L132](file:///d:/Private%20agent/backend/private_agent/core/react_loop.py#L132) `run_turn()`,状态机 IDLE→THINKING→ACTING→OBSERVING→IDLE,产出 thinking/tool_call/tool_result/final 四类 event(commit `abc39f5`,M1 merge)
- **事件入库**: `react_loop.py:99` `insert_react_event()` 每步持久化
- **WS 流式推送**: [main.py#L233-L235](file:///d:/Private%20agent/backend/private_agent/main.py#L233) 从 `loop.event_queue` 排干逐条 `ws.send_json`
- **测试**: `backend/tests/test_react_loop.py::test_run_turn_with_tool_calls_produces_four_events_in_order` 断言事件顺序为 `["thinking","tool_call","tool_result","final"]`;另含 max_iterations、AllProvidersFailedError、未知工具等 ERROR 路径测试 + `test_react_loop_event_sink.py` + `test_react_loop_tool_error.py`

---

### AC-2 四家模型(GLM/DeepSeek/Agnes/KIMI)均可成功调用,某家不可用时降级到备选 — ⚠️ 部分完成

| 子项 | 状态 | 证据 |
|---|---|---|
| GLM 适配器 | ✅ | `backend/private_agent/models/adapters/glm.py` |
| DeepSeek 适配器 | ✅ | `backend/private_agent/models/adapters/deepseek.py` |
| Kimi 适配器 | ✅ | `backend/private_agent/models/adapters/kimi.py` |
| **Agnes 适配器** | ❌ | **无 `agnes.py` 文件**;[config.yaml#L57-L60](file:///d:/Private%20agent/backend/config/config.yaml#L57) `agnes: base_url: "待确认", enabled: false` |
| 降级 FallbackChain | ✅ | `backend/private_agent/models/base.py:69` `FallbackChain.chat()` 顺序尝试 + `AllProvidersFailedError`;[registry.py#L46](file:///d:/Private%20agent/backend/private_agent/models/registry.py#L46) `build_fallback_chain()` |
| 测试 | ✅ | `backend/tests/test_model_adapters.py`(3 家 capability + mock 200/503)、`test_react_loop.py::test_run_turn_all_providers_failed_produces_error_event` |

**备注**: M1 spec(`m1-react-loop.md` line 37/47)明确"Agnes 跳过,Done Criteria 2 降级为三家 mock 可调用 + capability 降级生效,Agnes stub 留 M1-b/M2 补"。但 M2-M4 均未补 Agnes。AC 文字要求"四家",实际 3/4。

---

### AC-3 会话启动构建 Frozen/Stable/Active 三区,hash 校验通过 — ⚠️ 部分完成

| 子项 | 状态 | 证据 |
|---|---|---|
| Frozen/Stable/Active 三区 | ✅ | [context_manager.py#L65-L67](file:///d:/Private%20agent/backend/private_agent/core/context_manager.py#L65) 三个 Zone 实例;`:88` `build_initial()` 持久化 Frozen Zone;`:141` `ensure_initial()` 幂等 |
| 每轮 Active 追加 | ✅ | `context_manager.py:277/306/356` append_user/assistant/tool_message |
| hash 计算 | ✅(M3 补) | [context_manager.py#L79](file:///d:/Private%20agent/backend/private_agent/core/context_manager.py#L79) `compute_frozen_hash()` SHA-256,`test_context_manager.py::TestComputeFrozenHash`(标注"M3 AC-1") |
| **hash 运行时校验(比对存储值)** | ❌ | 无校验逻辑:`sessions.frozen_hash` 列可写入(`replace_frozen_zone:261`),但**无任何代码读取并比对 hash 检测篡改** |

**备注**: M1 spec line 40/50 明确"hash 字段预留(M1-b step 10 实现校验逻辑),本次只存字段不做校验"。compute_frozen_hash 在 M3 补上(用于 Skill 锁定),但 AC 要求的"hash 校验通过"(运行时验证)仍缺失。

---

### AC-4 长会话触发压缩(任一条件),压缩后 hash 重新计算通过 — ❌ 未完成

- **压缩触发逻辑**: **不存在**。[context_manager.py](file:///d:/Private%20agent/backend/private_agent/core/context_manager.py) 文件头注释 line 10 明确"压缩留 M1-b step 11";Grep `compress|token_limit|should_compress` 在 `core/` 目录零命中
- **active_zone_token_limit=4000**: [config.yaml#L82](file:///d:/Private%20agent/backend/config/config.yaml#L82) 配置存在,**但无任何代码读取此阈值触发压缩**
- **三类压缩策略(滑动窗口/摘要/Stable 合并)**: 无实现
- **test_compress_adapter.py**: 仅测试 `build_compress_adapter()`(压缩**模型适配器**构造,用于记忆提取),**非上下文压缩逻辑**
- **memory/manager.py**: 是用户记忆提取/淘汰,非上下文分区压缩

**备注**: M1 spec line 35"step 11:三类压缩策略…(留 M1-b)"。确认 M1-b 未执行,压缩功能完全缺失。

---

### AC-5 注入防护拦截中英文高危输入,告警入 react_events — ❌ 未完成

- **injection_guard 模块**: **不存在**。Grep `injection_guard|InjectionGuard` 在整个 `backend/` 零文件命中
- **core/ 目录**: 仅 `__init__.py` / `context_manager.py` / `executor.py` / `react_loop.py`,**无 injection_guard.py**
- **测试**: 无 `test_injection*.py` 文件
- **react_events 告警**: 无注入告警事件类型(schema CHECK 约束 event_type 枚举无 `injection_blocked`)

**备注**: 此项完全缺失,无任何代码痕迹。属安全风险(中英文高危输入无拦截)。

---

### AC-6 用户主动取消触发 checkpoint 存储,会话标记 interrupted — ❌ 未完成

- **checkpoint 存储逻辑**: **不存在**。Grep `save_checkpoint|checkpoint` 在 `core/` 目录仅命中 react_events schema 注释
- **WebSocketDisconnect 处理**: [main.py#L253](file:///d:/Private%20agent/backend/private_agent/main.py#L253) `except WebSocketDisconnect: pass` —— **仅 pass,未标记 session interrupted,未存储 checkpoint**
- **sessions.status='interrupted'**: schema 支持([schema.sql#L16](file:///d:/Private%20agent/backend/private_agent/storage/schema.sql#L16) CHECK 含 'interrupted'),但**无代码在断连时 UPDATE 该字段**
- **react_events 'checkpoint' 事件类型**: schema CHECK 与 `react_events.py:15` 枚举中**预留**了 'checkpoint',但**无代码 emit 该事件**

**备注**: M1 spec line 48 明确"用户取消走 WebSocketDisconnect 粗中断,会话标记 interrupted,不做 checkpoint 恢复"。实际连"会话标记 interrupted"都未实现(只 pass)。确认缺失。

---

### AC-7 token 计费按对话/压缩/embedding 三类分别记录 — ❌ 未完成

- **billing 模块**: **不存在**。Grep `billing|Billing|record_billing|billing_record` 在 `backend/private_agent/` 零命中
- [config.yaml#L86-L88](file:///d:/Private%20agent/backend/config/config.yaml#L86) `context.compression.billing` 段有 `currency`/`price_snapshot_enabled` 配置,**但无代码读取或记录计费**
- **三类计费(对话/压缩/embedding)**: 无任何实现
- **测试**: 无 `test_billing*.py`

**备注**: 此项完全缺失,仅 config 死配置。

---

### M1 完成度

- **完全完成**: AC-1 = 1 条
- **部分完成**: AC-2(3/4 provider)/ AC-3(三区✓ + hash计算✅ 但运行时校验❌)= 2 条
- **未完成**: AC-4 / AC-5 / AC-6 / AC-7 = 4 条
- **完成度**: 1/7 = **14%**
- **风险评级**: **高** — M1-b 全部 4 项(压缩/注入防护/checkpoint/计费)均未实现,ReAct 核心虽跑通但上下文工程闭环(压缩)、安全闭环(注入防护)、异常恢复闭环(checkpoint)、成本闭环(计费)全部缺失,直接影响生产可用性

---

## 3. M2 能力层 Done Criteria 核对(8 条)

### AC-1 知识库文档端到端处理入库,bge-m3 embedding + HNSW 索引可用 — ⚠️ 部分完成

- **文档处理流水线** ✅ [document_processor.py](file:///d:/Private%20agent/backend/private_agent/knowledge/document_processor.py) `DocumentProcessor.process` 支持 markdown/pdf/code/plain 四类分块(commit `b8da0a4f`)
  - 测试: `test_document_processor.py` → `TestDetectType`、`chunk_markdown/chunk_pdf/chunk_code`
- **EmbeddingService** ❌ [embedding_service.py#L123-L129](file:///d:/Private%20agent/backend/private_agent/knowledge/embedding_service.py#L123):`_embed_texts`:worker_pool 为 None 时返回 `[[0.0]*dim]` mock 全 0 向量;L189-206 `_embed_worker_fn` 直接 `raise NotImplementedError("Worker embedding requires FlagEmbedding library")` — bge-m3 模型从未真正加载
- **HNSW 索引** ❌ [schema.sql#L127-L138](file:///d:/Private%20agent/backend/private_agent/storage/schema.sql#L127):`kb_chunks.embedding BYTEA NOT NULL DEFAULT '\x'::bytea`,L133 注释明写"M0 占位 BYTEA;M2 RAG 阶段需...ALTER 为 vector(1024) + HNSW 索引" — 该 ALTER 从未执行,`migrations.py` 中也无对应迁移
- **向量检索** ❌ [kb_repo.py#L375-L400](file:///d:/Private%20agent/backend/private_agent/knowledge/kb_repo.py#L375) `vector_search`:L400 `return []` 恒返回空,L397 注释"V2:ALTER 为 vector(1024) 后启用 HNSW 索引"
- **测试**:
  - `test_kb_repo.py`:仅覆盖 kb_documents/kb_chunks CRUD,**无 vector_search/HNSW/cosine 任何测试**
  - `test_knowledge_services.py::test_embed_chunks_no_worker_returns_mock` 断言返回 mock 全 0 向量

**备注**: `M2-COMPLETION-HANDOFF.md` L130 自称"PostgreSQL 16 + pgvector 0.8.6, 所有表结构已迁移",但磁盘上 schema.sql 仍是 BYTEA,二者矛盾。实际入库时 `kb_service._vector_to_bytes` 把 mock 向量 struct.pack 成 BYTEA 写入,根本走不了 pgvector。

---

### AC-2 search_knowledge 工具调用返回结果,RRF 融合 + reranker 重排生效,min_similarity 过滤低分 chunk — ⚠️ 部分完成

- **工具入口** ✅ [search_knowledge.py#L48-L53](file:///d:/Private%20agent/backend/private_agent/tools/builtins/search_knowledge.py#L48):调用 `svc.search_with_rerank(query, scenario, top_k, min_similarity=0.2)`
  - 测试: `test_search_knowledge_tool.py` → `TestHandlerWithMock`(全部用 AsyncMock 替代 KnowledgeBaseService,无真实检索)
- **RRF 融合** ✅(纯函数) [kb_repo.py#L21-L55](file:///d:/Private%20agent/backend/private_agent/knowledge/kb_repo.py#L21) `rrf_fusion`
  - 测试: `test_knowledge_services.py` → `TestRrfFusion`(纯函数,不连 DB)
- **reranker** ❌/⚠️ [reranker_service.py#L62-L70](file:///d:/Private%20agent/backend/private_agent/knowledge/reranker_service.py#L62):worker_pool 为 None 时跳过重排,把所有候选 `c.score = 1.0` 直接返回 top-k;L120-137 `_rerank_worker_fn` `raise NotImplementedError`,bge-reranker 未加载
- **min_similarity 过滤** ✅ `kb_service.py:171`:`filtered = [c for c in reranked if c.score >= min_similarity]`
- **关键缺陷**: `kb_repo.hybrid_search` L471-476 并行调用 `vector_search`(恒返回 `[]`)+ `keyword_search`(ILIKE),RRF 融合输入只有关键词路 → 实际退化为关键词单路检索

**备注**: 所有候选 score 被 reranker mock 置为 1.0,min_similarity=0.2 过滤形同虚设(全部通过)。

---

### AC-3 低配置环境(<6GB 可用内存)自动切换 bge-small,切换时 HNSW 索引重建成功 — ❌ 未完成

- [embedding_service.py#L56](file:///d:/Private%20agent/backend/private_agent/knowledge/embedding_service.py#L56):`self._auto_switch_gb = self._config.get("auto_switch_memory_gb", 6)` — 配置项被读取但**全文未再使用**,无任何内存检测/模型切换逻辑
- 全仓 grep `auto_switch|memory_gb|rebuild` 在 `backend/private_agent` 下仅命中这一行配置读取,无切换函数、无 HNSW 重建
- 无 `test_embedding*.py` 测试文件;`test_knowledge_services.py` 无 auto_switch 用例
- HNSW 本就不存在(AC-1 已述),无从"重建"

**备注**: 该 Done Criteria 完全停留在"读了配置变量"阶段,无功能实现、无测试。

---

### AC-4 9 类通用工具均可调用,MCP 工具(stdio + HTTP)双探活通过 — ⚠️ 部分完成

- **9 类内置工具** ✅ `backend/private_agent/tools/builtins/__init__.py:38-48` 注册:calculator / code_execution / datetime / file_read / file_write / http_request / search_knowledge / web_search / read_artifact(commit `68f5e31d`)
  - 测试: `test_tools_lifecycle.py::TestBuiltinToolLifecycle.test_tool_registry_contains_all_9_builtins` 断言 9 个 name 集合
- **与蓝图 §5 的 9 类对比**:蓝图列 file_read/file_write/**file_list**/http_request/web_search/code_execution/search_knowledge/**mcp_proxy**/…;实际缺 file_list、mcp_proxy,改用 calculator/datetime/read_artifact 填满 9 个 — 数量达标但类别有偏差
- **MCP stdio** ✅ [mcp_client.py#L78-L109](file:///d:/Private%20agent/backend/private_agent/tools/mcp_client.py#L78) `connect`/`disconnect`,L152-197 `discover_tools`/`call_tool` 全量实现
  - 测试: `test_mcp_client.py::TestMcpClientStdio`(connect/discover/call/disconnect/reconnect)
- **MCP HTTP** ❌ `mcp_client.py:85-88、117-120、162-165、186-189`:所有 HTTP 路径 `raise McpHttpStubNotImplementedError`,文件头注释 L5 明写"HTTP 模式:仅类型定义 + 配置解析 stub"
- **"双探活"(stdio ping + HTTP health)** ❌ 全仓 grep `ping|health_check|probe|liveness|heartbeat` 在 `backend/private_agent` 下仅命中 main.py 的 WS `ping/pong`(L96-104,与 MCP 无关)。MCPClient 无 ping/health 探活方法,"双探活通过"不成立

---

### AC-5 沙箱执行 Python/JavaScript 代码,stdout/stderr 流式输出,超 2k token 走 artifact — ⚠️ 部分完成

- **Python 执行** ✅ [executor.py#L81-L85](file:///d:/Private%20agent/backend/private_agent/sandbox/executor.py#L81):`_build_command` 对 `language=="python"` 返回 `[python_cmd, script_path]`(commit `fc103159`)
  - 测试: `test_sandbox_service.py::test_execute_end_to_end`、`test_sandbox_executor.py`
- **JavaScript** ❌ `executor.py:85-86`:`if language == "python": ...; raise ValueError(f"Unsupported language: {language}")` — 非 python 直接抛错;`service.py:70` docstring 明写"当前仅支持 python"
  - 无任何 JS 测试;`M2-COMPLETION-HANDOFF.md` P0 #2 自认"JavaScript 沙箱未实现"
- **流式输出** ❌ [executor.py#L53-L55](file:///d:/Private%20agent/backend/private_agent/sandbox/executor.py#L53):`stdout_bytes, stderr_bytes = await asyncio.wait_for(process.communicate(), timeout=timeout)` — `communicate()` 等子进程结束一次性返回全部输出,**非流式**;无 WS 分片推送
  - 符合 project_memory.md 硬约束"沙箱流式输出延后实现"
- **artifact 阈值** ✅ [service.py#L52-L53](file:///d:/Private%20agent/backend/private_agent/sandbox/service.py#L52) `stdout_artifact_threshold=2000`,L122-132 超 2k token(用 len//4 估算)截断并写 `artifacts/stdout_*.txt`
  - 测试: `test_sandbox_service.py::test_stdout_artifact`、`test_stdout_artifact_disabled`

---

### AC-6 沙箱资源限制生效(300s 超时 + 512MB 内存 + 100MB 磁盘 + 禁网络) — ⚠️ 部分完成

- **300s 超时** ✅ [executor.py#L53-L62](file:///d:/Private%20agent/backend/private_agent/sandbox/executor.py#L53):`asyncio.wait_for(process.communicate(), timeout=timeout)` + 超时 `process.terminate()`;[service.py#L44](file:///d:/Private%20agent/backend/private_agent/sandbox/service.py#L44) `cpu_timeout_sec=300`
  - 测试: `test_sandbox_service.py::test_execute_with_timeout_override`
- **100MB 磁盘** ⚠️ [service.py#L84-L90](file:///d:/Private%20agent/backend/private_agent/sandbox/service.py#L84) `WorkspaceManager.check_disk_usage` 仅在**执行前**检查,执行过程中不持续限制;`test_sandbox_service.py::test_disk_limit_exceeded` 用 `disk_limit_mb=0` 验证前置拦截
- **512MB 内存** ❌ 全仓 grep `memory_limit|resource\.|rlimit` 在 `backend/private_agent/sandbox` 下**无任何命中**;[service.py#L43-L45](file:///d:/Private%20agent/backend/private_agent/sandbox/service.py#L43) `__init__` 只读 `cpu_timeout_sec` 与 `disk_limit_mb`,**根本未读取** `memory_limit_mb`;`test_sandbox_config.py:47` 仅断言配置里存在该键,无实际限制测试
- **禁网络** ❌ 全仓 grep `seccomp|unshare|network|net_cls` 在 sandbox 下无命中;`asyncio.create_subprocess_exec`(executor.py L45-51)未做任何 network namespace 隔离,子进程继承宿主网络

**备注**: 记忆中"512MB 内存和禁网络可能未实现"已被坐实 — 二者代码层完全缺失,仅有 config.yaml 字段占位。

---

### AC-7 危险代码预扫描告警入 react_events,环境变量脱敏后 Agent 代码无法读取 API Key — ⚠️ 部分完成

- **CodeScanner** ✅ [security.py#L10-L50](file:///d:/Private%20agent/backend/private_agent/sandbox/security.py#L10):10 条危险正则(os.system/subprocess/shutil.rmtree/socket/eval/exec…),`scan` 返回 `CodeWarning` 列表(不阻断)
  - 测试: `test_sandbox_security.py::TestCodeScanner`(os_system/subprocess/shutil_rmtree/clean_code)
- **EnvSanitizer** ✅ [security.py#L53-L91](file:///d:/Private%20agent/backend/private_agent/sandbox/security.py#L53):过滤 KEY/SECRET/TOKEN/PASSWORD/API_KEY 等,保留 PATH/HOME/USER/LANG
  - 测试: `TestEnvSanitizer::test_sanitize_filters_api_key` / `test_sanitize_filters_token` / `test_sanitize_retains_basic_vars`
- **告警入 react_events** ⚠️ [service.py#L182-L195](file:///d:/Private%20agent/backend/private_agent/sandbox/service.py#L182) `_log_execution`:payload 含 `warnings`,但 L192 用 `event_type='sandbox_execution'`;而 [schema.sql#L79](file:///d:/Private%20agent/backend/private_agent/storage/schema.sql#L79) CHECK 约束仅允许 `('thinking','tool_call','tool_result','final','error','checkpoint')` — **该 INSERT 在真实 DB 上必违反 CHECK 约束失败**,L196 `except Exception` 静默吞掉
  - 测试: `test_sandbox_service.py::test_event_logging` 用 `AsyncMock` 的 conn,绕过了 DB CHECK 约束,仅断言 SQL 字符串含 `sandbox_execution`,**未验证真实可入库**
- **Agent 无法读 API Key** ✅ `service.py:96` `safe_env = self._env_sanitizer.sanitize(dict(os.environ))` 后传入子进程 env

**备注**: 同样的问题影响 M2-8 的 `memory_extracted` event_type([manager.py#L204](file:///d:/Private%20agent/backend/private_agent/memory/manager.py#L204)),均不在 schema CHECK 白名单内。

---

### AC-8 用户记忆每 8 轮 + 会话结束 + UI 手动触发三种方式均可提取,注入 Stable Zone — ⚠️ 部分完成

- **maybe_extract(每 8 轮)** ✅ [manager.py#L88-L92](file:///d:/Private%20agent/backend/private_agent/memory/manager.py#L88):`current_turn % self.extract_interval_turns == 0`(默认 8)
- **on_session_end** ✅ `manager.py:95-107`
- **manual_extract(UI 手动)** ✅ `manager.py:109-121`
- **Stable Zone 注入** ✅ `manager.py:145-158` `format_memories_for_stable` 生成 `[User Memories]` 文本
- **compress_adapter 绑定** ✅(修正 subagent 报告) [main.py#L199-L215](file:///d:/Private%20agent/backend/private_agent/main.py#L199) WS 接收消息路径中构造 `MemoryManager(compress_adapter=_build_compress_adapter(cfg), ...)`,**生产环境有真实 compress_adapter**
- **关键缺陷** ⚠️ [manager.py#L204](file:///d:/Private%20agent/backend/private_agent/memory/manager.py#L204) emit `event_type="memory_extracted"` 违反 schema CHECK 约束 → 写入失败但被静默吞掉;记忆提取本身可工作,但事件追溯链路断
- **测试** ✅ `test_memory_manager.py`:
  - `test_maybe_extract_triggers_at_interval`(turn=8 触发)
  - `test_on_session_end_always_extracts`
  - `test_manual_extract_always_extracts`
  - `test_format_memories_for_stable`
  - `test_extract_without_adapter_returns_empty`(测试环境无 adapter 返回空)

**备注**: 修正 subagent 报告:compress_adapter 在 main.py 中已绑定,记忆提取实际可工作。但 `memory_extracted` event_type 不在 schema CHECK 白名单,事件记录会失败(被静默吞掉),记忆本身仍写入 `user_memories` 表。

---

### M2 完成度

| # | 状态 |
|---|---|
| AC-1 知识库 + bge-m3 + HNSW | ⚠️ 部分(文档流水线可用;embedding/vector/HNSW 全为 stub) |
| AC-2 search_knowledge + RRF + reranker + min_similarity | ⚠️ 部分(逻辑齐备;reranker mock、RRF 退化为关键词单路) |
| AC-3 低配置自动切换 bge-small + HNSW 重建 | ❌ 未完成(仅读配置变量,无切换/重建逻辑) |
| AC-4 9 类工具 + MCP 双探活 | ⚠️ 部分(9 工具+stdio 可用;HTTP stub、无双探活) |
| AC-5 沙箱 Python/JS + 流式 + artifact | ⚠️ 部分(Python+artifact 可用;JS 与流式未实现,符合硬约束延后) |
| AC-6 资源限制(超时/内存/磁盘/网络) | ⚠️ 部分(超时可用;磁盘前置检查;内存+禁网络缺失) |
| AC-7 预扫描告警 + 环境变量脱敏 | ⚠️ 部分(扫描器+脱敏器可用;react_events event_type 违反 CHECK 约束) |
| AC-8 记忆三种触发 + Stable Zone | ⚠️ 部分(骨架+单测+compress_adapter 绑定齐备;event_type 违反 CHECK 约束) |

- **完全完成**: 0 条
- **部分完成**: 7 条
- **未完成**: 1 条
- **完成度**: **~50%**
- **风险评级**: **高** — 知识库检索的 embedding/向量/HNSW/reranker 四件套全部停留在 mock/NotImplementedError 阶段,`M2-COMPLETION-HANDOFF.md` 自称的"pgvector 已迁移、HNSW ✅"与磁盘 schema.sql(BYTEA)直接矛盾,存在文档失真;沙箱内存/网络限制完全缺失属安全风险

---

## 4. M3 场景化 Done Criteria 核对(8 条)

### AC-1 三场景 Skills 目录结构 + skill.yaml 元数据 + Git 版本管理就位 — ✅ 已完成

- **目录结构** ✅ [backend/skills/](file:///d:/Private%20agent/backend/skills) 下三场景齐全(commit `13d67254`/`182a9c1a`/`84917ba3`/`ced655ca`):
  - `office/` 含 skill.yaml + system_prompt.md + tools.yaml + examples/{train,test}/
  - `data_analysis/` 同上
  - `frontend_design/` 同上
- **skill.yaml 元数据** ✅ 三份均含 name/version/scenario/dependencies.tools/permissions/knowledge_base/examples/max_frozen_token
- **Git 版本管理** ✅ skills 目录在 git 仓库内,有独立 commit 记录;`version_snapshots` 表([schema.sql#L145-L152](file:///d:/Private%20agent/backend/private_agent/storage/schema.sql#L145))支持 scope='skill' 快照;`SkillLoader.load_version`(loader.py L64+)可按 version 回滚
- **测试**: `test_skills_loader.py`、`test_skills_models.py`、`test_skill_loader_version.py`

---

### AC-2 UI 选择 Skill 后会话锁定,运行中切换被拒绝并提示 — ✅ 已完成

- **activate_skill 锁定逻辑** ✅ [manager.py#L70-L80](file:///d:/Private%20agent/backend/private_agent/skills/manager.py#L70):查 `sessions.locked_skill_name`,若已锁定且与新 skill_name 不同 → `raise SkillSwitchNotAllowedError`(commit `13d67254`)
- **同 skill 幂等再激活** ✅ `manager.py:76`:`if locked is not None and locked != skill_name` — 同名放行
- **schema 字段** ✅ [schema.sql#L24-L26](file:///d:/Private%20agent/backend/private_agent/storage/schema.sql#L24):`locked_skill_name VARCHAR(100)`、`locked_skill_version VARCHAR(20)`、`frozen_hash VARCHAR(64)`;`migrations.py:26-34` 对老部署补列
- **UPDATE 锁定** ✅ `manager.py:114-121`:写回三字段
- **测试**: `test_skills_manager.py::TestActivateSkillSwitchRejected::test_switch_rejected` / `test_same_skill_reactivate_allowed` / `test_activate_writes_session_lock`

---

### AC-3 办公场景 Excel/Word 文档处理 + 网页调研 + 来源标注,超大文件(>10000 行或 >50MB)自动分块读取 — ⚠️ 部分完成

- **office skill** ✅ [backend/skills/office/system_prompt.md](file:///d:/Private%20agent/backend/skills/office/system_prompt.md):L16"优先用 pandas 处理表格,python-docx 处理文档"、L11"所有引用网页搜索结果必须标注来源链接"、L17 网页研究降级策略(commit `182a9c1a`)
- **file_read max_lines** ✅ [file_read.py#L38](file:///d:/Private%20agent/backend/private_agent/tools/builtins/file_read.py#L38) `max_lines=1000`,L136 schema `maximum: 10000`(commit `070bbdbe`)
- **file_read max_file_size_mb** ✅ L39 `max_file_size_mb=10`,office skill.yaml L33 设 `max_file_size_mb: 50`
- **超大文件"自动分块读取"** ❌ `file_read.py:86-87` 超 max_lines 仅 `"\n".join(lines[:max_lines]) + "[truncated at N lines]"`;L65-72 超大小直接返回 error "Use code_execution to process in chunks" — **是截断 + 拒绝,不是分块流式读取**(无 offset/pagination/分块迭代)
- **测试**: `test_office_skill_e2e.py` 仅测 activate(工具过滤 + frozen_hash),无文件处理;`test_builtins_file_read.py::TestFileReadMaxLines::test_max_lines_truncates_content`(验证截断,非分块)、`TestFileReadSizeCheck::test_large_file_rejected`(验证拒绝)

**备注**: spec 写"自动分块读取",实现是"截断 + 提示用 code_execution"。功能等价度有限,大文件实际拿不到完整内容。

---

### AC-4 数据分析场景 pandas + matplotlib + scipy 全栈可用,图表存入 outputs/ 目录,前端渲染预览卡片 — ✅ 已完成

- **data_analysis skill** ✅ [backend/skills/data_analysis/system_prompt.md](file:///d:/Private%20agent/backend/skills/data_analysis/system_prompt.md) L5/L17/L19 明确 pandas+matplotlib+scipy,图表存 `outputs/`(commit `84917ba3`)
- **GET /files/outputs/{filename}** ✅ [api/files.py#L60-L93](file:///d:/Private%20agent/backend/private_agent/api/files.py#L60):文件名正则白名单 + 路径穿越二次校验 + 图片 MIME 映射(commit `070bbdbe`)
  - 测试: `test_files_endpoint.py::test_returns_200_with_image_content_type` / `test_path_traversal_rejected` / `test_jpeg_content_type`
- **前端预览卡片** ✅ [App.tsx](file:///d:/Private%20agent/frontend/renderer/App.tsx) L57-78 `IMAGE_PATH_RE`/`extractImagePaths`/`imagePathToUrl` 解析 tool_result 文本中的 `outputs/*.png`;L551-562 渲染 `<img src="/files/outputs/{filename}">`
- **测试**: `test_data_analysis_skill_e2e.py::test_activate_data_analysis_filters_tools_and_writes_frozen_hash`(激活层,未跑真实 pandas/matplotlib)

**备注**: 端点 + 前端渲染链路完整且有测试;但无"真跑一段 pandas 生成 chart.png → 前端展示"的端到端测试。

---

### AC-5 前端设计场景 HTML/React/Vue 代码生成 + 设计系统 RAG 检索 + scenario 过滤生效 — ✅ 已完成

- **frontend_design skill** ✅ [backend/skills/frontend_design/system_prompt.md](file:///d:/Private%20agent/backend/skills/frontend_design/system_prompt.md)(commit `ced655ca`):
  - L9"支持框架:原生 HTML/CSS/JS、React(函数组件 + Hooks)、**Vue(3.x SFC 单文件组件)**" — Vue 明确声明支持
  - L21"code_execution 内用 Python 字符串拼接或 jinja2 模板生成 HTML/CSS/JS 文件" — Vue/React/HTML 均由 Python code_execution 生成**文本文件**输出到 outputs/,无需 JS 沙箱执行(与 M2-5 沙箱仅支持 Python 自洽)
- **设计系统 RAG** ✅ skill.yaml L30-33 `knowledge_base.enabled: true / scenario: frontend_design`;[search_knowledge.py#L95-L98](file:///d:/Private%20agent/backend/private_agent/tools/builtins/search_knowledge.py#L95) 支持 `scenario` 参数;`kb_repo.keyword_search:430-436` 按 scenario 过滤
- **scenario 过滤** ✅ `kb_service.py:151` `filters = {"scenario": scenario}` 传入 hybrid_search
- **测试**: `test_frontend_design_skill_e2e.py::test_activate_frontend_design_filters_tools_and_writes_frozen_hash` / `test_frontend_design_skill_yaml_matches_blueprint_matrix`(6 工具 + scenario=frontend_design 断言)

**备注**: Vue 并非"沙箱执行 Vue",而是"Python 生成 Vue SFC 文本" — 这是 spec 与沙箱能力的一致设计,非缺陷。

---

### AC-6 Skill 不存在时返回 UI 友好错误,跳转选择页 — ⚠️ 部分完成

- **SkillNotFoundError 兜底** ✅ [errors.py#L11-L12](file:///d:/Private%20agent/backend/private_agent/skills/errors.py#L11) 定义;`loader.py:60-62` 抛出;`manager.py:65` 透传(commit `13d67254`)
- **后端友好错误** ✅ [api/admin.py#L307-L308](file:///d:/Private%20agent/backend/private_agent/api/admin.py#L307):`except SkillNotFoundError: raise HTTPException(status_code=404, detail="skill_not_found")`;L382-383 GET 详情同样兜底
  - 测试: `test_admin_activate_skill.py::test_activate_skill_not_found_returns_404` / `test_skills_manager.py::TestActivateSkillNotFound::test_skill_not_found`
- **前端"跳转选择页"** ❌ [App.tsx](file:///d:/Private%20agent/frontend/renderer/App.tsx) 全文无 Skill 选择页/路由跳转逻辑,仅处理 WS 消息(react_event/error/ack)+ 评估面板切换;`error` 消息(L361-373)只把 message 渲染成红色事件块,无"跳转选择 Skill"UI

---

### AC-7 权限缓存 cache_key 含 skill_name,不同 Skill 同工具权限不互相覆盖 — ✅ 已完成

- **get_permission_cache_key** ✅ [permission.py#L17-L37](file:///d:/Private%20agent/backend/private_agent/tools/permission.py#L17):`sha256(f"{skill_name}::{tool_name}::{args_str}")`,skill_name 作前缀(commit `070bbdbe`)
- **隔离性** ✅ 不同 skill_name 同 tool 同 args → 不同 key
- **测试**: `test_permission_cache_key.py` 7 个用例:
  - `test_returns_64_char_sha256_hex`
  - `test_different_skill_name_produces_different_key`(office vs data_analysis vs frontend_design 三者互异)
  - `test_same_input_produces_same_key`(幂等)
  - `test_args_key_order_does_not_matter`(sort_keys=True)
  - `test_different_tool_name_produces_different_key` / `test_different_args_produce_different_key` / `test_empty_args`

**备注**: [permission.py#L7](file:///d:/Private%20agent/backend/private_agent/tools/permission.py#L7) 注释"MVP 仅提供纯函数 + 单测,不集成到运行时权限校验路径" — 函数本身满足 Done Criteria,但运行时未调用。符合 project_memory.md "V2 预留 API surface" 标注。

---

### AC-8 少样本示例注入 Frozen Zone,train/test 拆分规则生效 — ✅ 已完成

- **ExampleLoader.load** ✅ [example_loader.py#L45-L76](file:///d:/Private%20agent/backend/private_agent/skills/example_loader.py#L45):glob `examples/train/*.md` 按文件名排序,token 预算 `len//4` 截断(commit `070bbdbe` + `13d67254`)
- **注入 Frozen Zone** ✅ [manager.py#L88-L96](file:///d:/Private%20agent/backend/private_agent/skills/manager.py#L88):`activate_skill` 中 `system_prompt += "\n\n## 示例\n\n" + "\n\n".join(examples)`(拼入 system_prompt 即 Frozen Zone)
- **train/test 拆分** ✅ 三场景目录均含 `examples/train/*.md`(少样本)+ `examples/test/*.json`(评估样本);`example_loader.py:78-110` `load_test_set` 加载 test 集
- **测试**:
  - `test_skills_example_loader.py::test_load_all_examples_under_budget` / `test_truncate_when_over_budget` / `test_max_examples_limits_count` / `test_examples_sorted_by_filename` / `test_from_cfg_classmethod`
  - `test_eval_example_loader.py`(test 集 + EvalSample 校验)

---

### M3 完成度

| # | 状态 |
|---|---|
| AC-1 Skills 目录 + skill.yaml + Git 版本管理 | ✅ |
| AC-2 会话锁定 + 切换被拒绝 | ✅ |
| AC-3 办公场景 + 超大文件分块 | ⚠️ 部分(场景+来源标注可用;分块读取实为截断/拒绝) |
| AC-4 数据分析 + outputs/ + 预览卡片 | ✅ |
| AC-5 前端设计 + Vue + RAG scenario | ✅ |
| AC-6 Skill 不存在友好错误 + 跳转选择页 | ⚠️ 部分(后端 404 可用;前端跳转未实现) |
| AC-7 权限缓存 cache_key 含 skill_name | ✅ |
| AC-8 少样本 Frozen Zone + train/test 拆分 | ✅ |

- **完全完成**: 5 条
- **部分完成**: 2 条
- **完成度**: **81%**
- **风险评级**: **中低** — Skills 框架本体(加载/锁定/少样本/权限缓存/三场景)扎实且有真实 DB e2e 测试;主要缺口是前端 Skill 选择/跳转页缺失、办公场景大文件"分块读取"为截断

---

## 5. M4 评估闭环 Done Criteria 核对(8 条)

### AC-1 离线批量评估可执行,每场景 20 条样本(train/test 分离),规则校验 + LLM-as-Judge 双评判 — ✅ 已完成

- **实现**: [runner.py](file:///d:/Private%20agent/backend/private_agent/eval/runner.py) `EvalRunner.run_evaluation`,offline 模式 actual_events=[]
- **混合评判**: [hybrid_eval.py](file:///d:/Private%20agent/backend/private_agent/eval/hybrid_eval.py) `HybridEvaluator` 编排规则 + LLM-Judge
- **schema**: [schema.sql](file:///d:/Private%20agent/backend/private_agent/storage/schema.sql) `eval_datasets.split` 列 + CHECK 约束
- **测试**: `test_eval_runner.py::test_run_evaluation_offline_mode_actual_events_empty` / `test_run_evaluation_offline_sample_subset_quick` / `test_eval_hybrid.py::test_evaluate_sample_normal` / `test_eval_metrics.py`
- **样本数**: 当前 12 条种子(3 场景×4 条:office/data_analysis/frontend_design 各 1 normal + 2 normal + 1 boundary + 1 error),spec Open question 已说明 12 条种子验证管线 + §8.16 扩充机制就位即视为 AC-1 达成,20 条阈值作为持续进化目标
- commit: `fe3838c`(foundation) + `8ff3f5b`(metrics-judge) + `1883878`(runner-replay)

---

### AC-2 交互式回放可重建会话,Mock 数据按工具名一一对应,Skill 回滚时 mock 数据同步加载 — ✅ 已完成

- **实现**: [replay.py](file:///d:/Private%20agent/backend/private_agent/eval/replay.py) `ReplayExecutor.run_replay`,创建临时会话 title="eval-" 前缀
- **Mock 工具**: [mock_tool_registry.py](file:///d:/Private%20agent/backend/private_agent/eval/mock_tool_registry.py) `MockToolRegistry`,sample_id+tool_name 二级索引
- **Skill 回滚同步**: [loader.py](file:///d:/Private%20agent/backend/private_agent/skills/loader.py) `load_version` 从 version_snapshots 读历史 Skill 快照,mock_data 跟随
- **mock 数据**: [backend/skills/office/examples/test/mock_data/](file:///d:/Private%20agent/backend/skills/office/examples/test/mock_data) 下 9 个 JSON 文件,覆盖 file_read(3)/code_execution(3)/web_search(1)/http_request(1)/file_write(1)
- **测试**: `test_eval_replay.py::test_run_replay_mock_mode_collects_tool_call_and_result` / `test_run_replay_creates_and_deletes_eval_session` / `test_eval_mock_tool_registry.py::test_get_mock_handler_reads_json_and_returns_tool_result` / `test_skill_loader_version.py::test_load_version_returns_skill_from_snapshot`
- commit: `1883878`

---

### AC-3 eval_runs 表记录完整 metrics,含 mock_enabled 标记 — ✅ 已完成

- **schema**: [schema.sql](file:///d:/Private%20agent/backend/private_agent/storage/schema.sql) `eval_runs` 表含 `mock_enabled BOOLEAN` + `metrics JSONB` + `sample_results JSONB`
- **repo**: [repos.py](file:///d:/Private%20agent/backend/private_agent/eval/repos.py) `EvalRunRepo`: create_run/write mock_enabled、update_run_metrics/write metrics+sample_results、complete_run/fail_run
- **测试**: `test_eval_repos.py::test_eval_run_repo_create_run_returns_run_id` / `test_eval_run_repo_update_run_metrics` / `test_eval_e2e.py::test_e2e_offline_evaluation_eval_runs_record_complete`
- commit: `fe3838c` + `1883878`

**备注**: status 三态用 finished_at + metrics.error 联合判断(无 status 列),mock_mode 冗余列保留但不用。

---

### AC-4 版本对比双维度筛选(同模型 + 同 Skill 最新成功基线),差值计算 + UI 图表展示 — ✅ 已完成

- **实现**: [version_compare.py](file:///d:/Private%20agent/backend/private_agent/eval/version_compare.py) `EvalComparator.compare_versions` 双维度筛选 skill_version+model_id + 取最新 completed run + `_compute_diff` 标记 improved/degraded/stable
- **API**: [api/eval.py](file:///d:/Private%20agent/backend/private_agent/api/eval.py) `GET /admin/eval/versions/compare`
- **前端**: [App.tsx](file:///d:/Private%20agent/frontend/renderer/App.tsx) `EvalPanel` 组件:SVG polyline 趋势折线图 + 版本对比表格
- **测试**: `test_eval_version_compare.py::test_compare_versions_selects_latest_completed` / `test_compare_versions_raises_insufficient_data_when_no_completed_runs` / `test_compute_diff_marks_improved_degraded_stable` / `test_eval_api.py::test_compare_versions_endpoint_returns_diff` / `test_eval_frontend_panel.py::test_app_tsx_contains_trend_chart_svg`
- commit: `506e731`

---

### AC-5 Prompt/Skill/Harness 三类载体迭代闭环跑通,退化时 UI 告警 + eval_runs 记录,不自动阻断发布 — ✅ 已完成

- **回滚**: [rollback.py](file:///d:/Private%20agent/backend/private_agent/eval/rollback.py) `SkillRollbackManager`: `rollback_prompt` / `rollback_skill` / `rollback_harness`
- **版本监听**: [version_listener.py](file:///d:/Private%20agent/backend/private_agent/eval/version_listener.py) `SkillVersionListener.on_skill_version_saved` 触发快速回归
- **API**: [api/admin.py](file:///d:/Private%20agent/backend/private_agent/api/admin.py) `POST /admin/skills/{name}/save-version` 保存版本 + 触发 listener
- **退化告警**: `_compute_diff` 标记 degraded,无阻断逻辑
- **前端**: [App.tsx](file:///d:/Private%20agent/frontend/renderer/App.tsx) degraded 红色 badge
- **测试**: `test_eval_rollback.py::test_rollback_prompt_only_updates_prompt_pointer`(AC-3) / `test_rollback_skill_updates_version_and_tools`(AC-4) / `test_rollback_harness_returns_command_without_execution`(AC-5) / `test_eval_version_listener.py::test_on_skill_version_saved_triggers_when_auto_trigger_true`(AC-10) / `test_eval_api_save_version.py::test_save_version_persists_snapshot_and_triggers_listener`(AC-12) / `test_eval_version_compare.py::test_degradation_marked_in_diff_for_rollback_decision` / `test_eval_e2e_version_flow.py::test_e2e_version_compare_and_rollback_flow`(AC-1..AC-7 闭环)
- commit: `506e731` + `1883878`

---

### AC-6 Skill 回滚后新会话加载 latest_version,持续在线会话维持锁定版本 — ✅ 已完成

- **实现**: [rollback.py](file:///d:/Private%20agent/backend/private_agent/eval/rollback.py) `rollback_skill` 更新 `config_runtime` key=`skill.{name}.latest_version` 指针
- **schema**: [schema.sql](file:///d:/Private%20agent/backend/private_agent/storage/schema.sql) `sessions.locked_skill_version` 列(M3 已建)
- **context**: [context_manager.py](file:///d:/Private%20agent/backend/private_agent/core/context_manager.py) `replace_frozen_zone`,仅对新会话生效
- **测试**: `test_skill_manager_rollback.py::test_activate_skill_uses_latest_version_pointer_after_rollback` / `test_running_session_keeps_locked_version_after_rollback`
- commit: `506e731`

---

### AC-7 低分案例自动提取,人工审核队列支持两类筛选标准(模型能力限制丢弃 / Prompt 缺陷编辑后入库) — ✅ 已完成

- **提取**: [weak_sample.py](file:///d:/Private%20agent/backend/private_agent/eval/weak_sample.py) `WeakSampleExtractor.extract_from_low_score_runs`,threshold=0.6
- **审核队列**: [repos.py](file:///d:/Private%20agent/backend/private_agent/eval/repos.py) `ReviewQueueRepo`: add/list_pending/update_status,JSON 文件原子写入
- **API**: [api/eval.py](file:///d:/Private%20agent/backend/private_agent/api/eval.py) `GET /admin/eval/review-queue` + `POST /admin/eval/review-queue/{item_id}/decide`
- **两类筛选**:
  - `decision="model_limitation_drop"` → rejected 不入库
  - `decision="prompt_defect_edit"` → approved 入库(case_type=boundary, split=test)
- **测试**: `test_eval_weak_sample.py::test_extract_returns_candidates_from_low_score_samples` / `test_eval_review_queue_repo.py::test_update_status_approved_inserts_into_eval_datasets`(AC-4 入库) / `test_update_status_rejected_does_not_insert`(AC-5 丢弃) / `test_eval_review_queue_api.py::test_decide_review_item_prompt_defect_edit_inserts_sample` / `test_decide_review_item_model_limitation_drop_does_not_insert` / `test_eval_continuous_evolution_e2e.py::test_e2e_prompt_defect_edit_closed_loop`(AC-9) / `test_e2e_model_limitation_drop_closed_loop`(AC-10)
- commit: `506e731`

**备注**: WeakSampleExtractor 无独立 API 触发端点(spec §E 未定义),设计为程序化调用(如评估运行后)。审核队列用 JSON 文件存储(MVP 避免新增 DB 表),原子写入(临时文件+os.replace)。

---

### AC-8 expected_react_trace 入库前通过 Pydantic 校验,非法结构抛出样本格式错误 — ✅ 已完成

- **Pydantic 模型**: [models.py](file:///d:/Private%20agent/backend/private_agent/eval/models.py) `validate_expected_trace` 函数 + `InvalidSampleFormatError` 异常 + `ExpectedToolCall`/`ExpectedTrace`/`EvalSample` Pydantic 模型
- **入库前校验**: [repos.py](file:///d:/Private%20agent/backend/private_agent/eval/repos.py) `EvalDatasetRepo.insert` 入库前调 `validate_expected_trace`;`ReviewQueueRepo.update_status` 入库前调 `validate_expected_trace`
- **DB CHECK 兜底**: [schema.sql](file:///d:/Private%20agent/backend/private_agent/storage/schema.sql) `CHECK (jsonb_typeof(expected_react_trace->'tool_calls') = 'array')`
- **测试**: `test_eval_models.py::test_validate_expected_trace_missing_tool_calls_raises` / `test_tool_calls_not_list_raises` / `test_missing_expected_output_contains_raises` / `test_expected_output_contains_not_list_raises` / `test_not_dict_raises` / `test_eval_repos.py::test_eval_dataset_repo_insert_invalid_raises`(AC-3) / `test_eval_review_queue_repo.py::test_update_status_invalid_sample_raises`(AC-6)
- commit: `fe3838c`

---

### M4 完成度

- **完全完成**: 8 条
- **完成度**: **100%**(8/8 达成,DC-1 的 20 条样本为渐进目标,12 条种子已就位,扩充机制闭环)
- **风险评级**: **中** — 存在 2 个 P1 级潜在运行时风险(详见第 8 节闭环验证 A)

---

## 6. 全量测试统计

### 6.1 总体结果

```
692 passed, 5 warnings in 152.01s (0:02:32)
```

- **通过**: 692
- **失败**: 0
- **跳过**: 0
- **警告**: 5(均为 FastAPI `on_event` deprecation,非功能问题)

### 6.2 测试文件分布(95 个测试文件)

| 模块 | 测试文件数 | 关键文件 |
|---|---|---|
| M0 基础设施 | ~15 | test_migrations / test_config / test_secrets / test_disk_alert / test_logging / test_ws / test_main_startup / test_structure |
| M1 编排核心 | ~12 | test_react_loop / test_react_loop_event_sink / test_model_adapters / test_model_base / test_model_registry / test_context_manager / test_manual_router / test_compress_adapter |
| M2 能力层 | ~25 | test_kb_repo / test_knowledge_services / test_document_processor / test_search_knowledge_tool / test_tools_lifecycle / test_mcp_client / test_sandbox_* / test_memory_manager / test_memories_repo / test_builtins_* |
| M3 场景化 | ~12 | test_skills_loader / test_skills_manager / test_skills_models / test_skills_example_loader / test_skill_loader_version / test_skill_manager_rollback / test_admin_activate_skill / test_office_skill_e2e / test_data_analysis_skill_e2e / test_frontend_design_skill_e2e / test_files_endpoint / test_permission_cache_key |
| M4 评估闭环 | ~25 | test_eval_models / test_eval_repos / test_eval_metrics / test_eval_judge / test_eval_hybrid / test_eval_runner / test_eval_replay / test_eval_mock_tool_registry / test_eval_version_compare / test_eval_rollback / test_eval_version_listener / test_eval_weak_sample / test_eval_review_queue_repo / test_eval_review_queue_api / test_eval_continuous_evolution_e2e / test_eval_e2e / test_eval_e2e_version_flow / test_eval_api / test_eval_api_save_version / test_eval_frontend_panel |

### 6.3 失败根因分析

**无失败**。此前记忆中"15 个 DB 失败"实为环境变量未设置导致:

- 错误现象: `asyncpg.exceptions.ConnectionDoesNotExistError` / `ValueError: 环境变量 PA_DB_PASSWORD 未设置`
- 根因: 测试需要 `PA_DB_PASSWORD` 和 `PA_TEST_DSN` 两个环境变量,默认未设置
- 解决方案: ` $env:PA_DB_PASSWORD="123123"; $env:PA_TEST_DSN="postgresql://postgres:123123@localhost:5432/private_agent_test"`
- 设置后全量 692 通过

**已知技术债**(不影响测试通过,但影响生产可用性):
1. 测试中 `test_sandbox_service.py::test_event_logging` 用 `AsyncMock` 的 conn 绕过 DB CHECK 约束,未发现 `event_type='sandbox_execution'` 在真实 DB 上会违反约束
2. 测试中 `test_eval_api.py` 用 monkeypatch `_build_eval_runner` 规避了 `_build_hybrid_evaluator` / `_build_default_adapter` 的 ImportError 风险

---

## 7. 文档完整性检查

### 7.1 `.claude/artifacts/designs/` 设计文档(11 个)

| 文件 | 阶段 | 状态 |
|---|---|---|
| m0-skeleton.md | M0 | ✅ 回溯补写 |
| m1-react-loop.md | M1 | ✅ |
| m3-skills-office.md | M3 | ✅ |
| m3-skills-data-analysis.md | M3 | ✅ |
| m3-skills-frontend-design.md | M3 | ✅ |
| m3-remaining-done-criteria.md | M3 | ✅ |
| m4-eval-foundation.md | M4 | ✅ |
| m4-metrics-judge.md | M4 | ✅ |
| m4-eval-runner-replay.md | M4 | ✅ |
| m4-version-compare-rollback.md | M4 | ✅ |
| m4-continuous-evolution.md | M4 | ✅ |

### 7.2 `.claude/artifacts/plans/` 实施方案(11 个 + 2 辅助)

| 文件 | 阶段 | 状态 |
|---|---|---|
| m0-skeleton.md | M0 | ✅ 回溯补写 |
| m1-react-loop.md | M1 | ✅ |
| m3-skills-office.md | M3 | ✅ |
| m3-skills-data-analysis.md | M3 | ✅ |
| m3-skills-frontend-design.md | M3 | ✅ |
| m3-remaining-done-criteria.md | M3 | ✅ |
| m4-eval-foundation.md | M4 | ✅ |
| m4-metrics-judge.md | M4 | ✅ |
| m4-eval-runner-replay.md | M4 | ✅ |
| m4-version-compare-rollback.md | M4 | ✅ |
| **m4-continuous-evolution.md** | M4 | **❌ 缺失** |
| m0-m4-redesign-assessment.md | 辅助 | ✅ |
| mcp-2026-07-28-blueprint-revision.md | 辅助 | ✅ |
| _重排版_extract.txt | 辅助 | ✅ |

### 7.3 文档缺口清单

1. **❌ M2 spec 缺失**: designs/ 和 plans/ 下均**无 m2-*.md 文件**。M2 实际通过 3 个 commit(`c9fbfba` tools / `fc10315` sandbox / `b8da0a4` RAG)直接交付,跳过了 dev-grill-docs → dev-plan → dev-tdd 流程,违反硬约束"开发需遵循 dev-auto 流程,必须先产出设计文档和实施方案,禁止直接编码"。
2. **❌ m4-continuous-evolution.md plan 缺失**: designs/ 下有但 plans/ 下无,缺失实施方案。
3. **⚠️ `M2-COMPLETION-HANDOFF.md` 内容失真**: 自称"PostgreSQL 16 + pgvector 0.8.6, 所有表结构已迁移",但磁盘上 schema.sql 仍是 BYTEA,pgvector 未真正使用。建议删除或加 deprecated 标注。
4. **⚠️ `mcp-2026-07-28-impact-analysis.md`** 在根目录而非 artifacts/,位置不规范。

### 7.4 `CONTEXT.md` 术语表

[CONTEXT.md](file:///d:/Private%20agent/CONTEXT.md) 含 30 条术语,覆盖 M3/M4 全部核心概念(Skill/SkillManifest/ToolDependency/SkillLoader/SkillManager/ExampleLoader/会话锁定/Frozen Zone/Frozen Hash/compress_adapter/EvalSample/ExpectedTrace/EvalRun/离线批量评估/交互式回放/Mock 模式/LLM-as-Judge/五类指标/版本对比/回滚/退化告警/持续进化闭环/两类筛选标准)。

**缺口**: 缺少 M0/M1 部分核心术语(ReAct Loop/KV Cache 分区/ManualRouter/TokenEstimator/Agnes 适配器等)。

### 7.5 `docs/adr/` ADR

仅 1 个: [0001-m4-spec-split-strategy.md](file:///d:/Private%20agent/docs/adr/0001-m4-spec-split-strategy.md)(M4 spec 切分策略)。

**缺口**: 缺少以下关键 ADR:
- M2 跳过 dev-auto 流程的决策(技术债)
- BYTEA → vector(1024) 迁移延后决策
- 沙箱内存/网络限制延后决策
- compress_adapter 复用决策
- ReviewQueueRepo 用 JSON 文件而非 DB 表的决策

---

## 8. 四类闭环验证

### 8.A 公开符号闭环检查

| 模块 | __all__ 公开符号 | 调用者 | 状态 |
|---|---|---|---|
| eval/models.py | ExpectedToolCall, ExpectedTrace, EvalSample, InvalidSampleFormatError, validate_expected_trace | repos.py, hybrid_eval.py, runner.py, api/eval.py, 多个测试 | ✅ 闭环 |
| eval/repos.py | EvalDatasetRepo, EvalRunRepo, VersionSnapshotRepo, ReviewQueueRepo | runner.py, api/eval.py, weak_sample.py, rollback.py, 多个测试 | ✅ 闭环 |
| eval/metrics.py | compute_all_metrics, evaluate_task_completion, evaluate_tool_calls, evaluate_efficiency, evaluate_security | hybrid_eval.py, test_eval_metrics.py | ✅ 闭环 |
| eval/judge.py | LLMJudge, build_judge_adapter, load_judge_prompt | hybrid_eval.py, test_eval_judge.py | ✅ 闭环 |
| eval/hybrid_eval.py | HybridEvaluator | runner.py, api/eval.py, test_eval_hybrid.py | ⚠️ 闭环(见备注1) |
| eval/runner.py | EvalRunner | api/eval.py, version_listener.py, test_eval_runner.py | ✅ 闭环 |
| eval/replay.py | ReplayExecutor | runner.py, test_eval_replay.py | ✅ 闭环 |
| eval/mock_tool_registry.py | MockToolRegistry | replay.py, test_eval_mock_tool_registry.py | ✅ 闭环 |
| eval/version_compare.py | EvalComparator, InsufficientDataError | api/eval.py, test_eval_version_compare.py | ✅ 闭环 |
| eval/rollback.py | SkillRollbackManager, VersionNotFoundError | api/eval.py, test_eval_rollback.py | ✅ 闭环 |
| eval/version_listener.py | SkillVersionListener | api/admin.py, test_eval_version_listener.py | ✅ 闭环 |
| eval/weak_sample.py | WeakSampleExtractor | test_eval_weak_sample.py, test_eval_continuous_evolution_e2e.py | ✅ 闭环(仅测试调用,spec 设计为程序化调用) |
| tools/permission.py | get_permission_cache_key | test_permission_cache_key.py | ✅ 闭环(framework-invoked exemption,V2 预留 API surface) |
| skills/loader.py | SkillLoader.from_cfg | api/eval.py, api/admin.py, main.py | ✅ 闭环 |
| skills/manager.py | SkillManager.activate_skill | api/admin.py, main.py, test_skills_manager.py | ✅ 闭环 |

**备注 1(P1 闭环风险)**: `api/eval.py:88-92` 的辅助函数 `_build_hybrid_evaluator` 调用 `HybridEvaluator.from_cfg(cfg)`,但 `hybrid_eval.py` 中**未定义** `from_cfg` 类方法(仅有 `__init__`)。同样 `_build_default_adapter`([api/eval.py#L81-L85](file:///d:/Private%20agent/backend/private_agent/api/eval.py#L81)) 调用 `from private_agent.models.registry import build_default_adapter`,但 [registry.py](file:///d:/Private%20agent/backend/private_agent/models/registry.py) 中**未定义** `build_default_adapter` 函数(仅有 `build_fallback_chain` 和 `build_compress_adapter`)。这两个引用在测试中被 monkeypatch `_build_eval_runner` 规避(test_eval_api.py 第 86 行),但**生产环境调用 `POST /admin/eval/runs` 端点时会触发 ImportError / AttributeError**。

### 8.B API 端点闭环检查

`main.py:19-21` 确认 `app.include_router(admin.router)` + `app.include_router(eval.router)` + `app.include_router(files.router)`。

| 端点 | router 文件 | 实现函数 | 状态 |
|---|---|---|---|
| GET / | main.py:79 | root | ✅ |
| GET /health | main.py:85 | health | ✅ |
| WS /ws | main.py:91 | websocket_endpoint | ✅ |
| GET /admin/disk-status | admin.py | disk_status | ✅ |
| POST /admin/skills/{name}/activate | admin.py | activate_skill | ✅ |
| GET /admin/skills | admin.py | list_skills | ✅ |
| GET /admin/skills/{name} | admin.py | get_skill_detail | ✅ |
| POST /admin/skills/{name}/save-version | admin.py:407 | save_version | ✅ |
| POST /admin/eval/runs | eval.py:108 | trigger_eval_run | ⚠️ 注册但运行时 ImportError(from_cfg/build_default_adapter) |
| GET /admin/eval/runs | eval.py:136 | list_eval_runs | ✅ |
| GET /admin/eval/runs/{run_id} | eval.py:163 | get_eval_run | ✅ |
| GET /admin/eval/datasets | eval.py:183 | list_eval_datasets | ✅ |
| GET /admin/eval/versions/compare | eval.py:212 | compare_versions | ✅ |
| POST /admin/eval/rollback | eval.py:241 | trigger_rollback | ✅ |
| GET /admin/eval/review-queue | eval.py:284 | list_review_queue | ✅ |
| POST /admin/eval/review-queue/{item_id}/decide | eval.py:314 | decide_review_item | ✅ |
| GET /files/outputs/{filename} | files.py:60 | get_output_file | ✅ |

**结论**: 17/17 端点已注册,1 个端点(POST /admin/eval/runs)有运行时 ImportError 风险。

### 8.C DB 迁移闭环检查

| 表名 | schema.sql DDL | 查询代码位置 | migrations.py | 状态 |
|---|---|---|---|---|
| sessions | line 12 | context_manager.py, replay.py, admin.py, main.py | ADD COLUMN locked_skill_name/version/frozen_hash | ✅ |
| messages | line 35 | context_manager.py | - | ✅ |
| messages_archive | line 57 | ttl_cleanup.py (cleanup_messages_archive) | - | ✅ |
| react_events | line 75 | react_loop.py, ws_offset.py, ttl_cleanup.py | - | ⚠️(event_type CHECK 与代码 emit 不一致) |
| user_memories | line 90 | memories_repo.py | - | ✅ |
| kb_documents | line 109 | kb_repo.py | - | ✅ |
| kb_chunks | line 127 | kb_repo.py | - | ⚠️(embedding 仍 BYTEA,未迁移 vector(1024)) |
| version_snapshots | line 145 | repos.py (VersionSnapshotRepo), loader.py (load_version) | - | ✅ |
| eval_datasets | line 159 | repos.py (EvalDatasetRepo) | ADD COLUMN split + CHECK | ✅ |
| eval_runs | line 184 | repos.py (EvalRunRepo) | ADD COLUMN sample_results | ✅ |
| async_tasks | line 207 | ttl_cleanup.py | - | ✅ |
| config_runtime | line 225 | rollback.py (_upsert_config_runtime), ws_offset.py | - | ✅ |
| skills | line 234 | loader.py (_load_from_pg), admin.py, rollback.py | - | ✅ |

**结论**: 13/13 表有查询代码。migrations.py 含 eval_datasets.split 列幂等迁移(line 37-38)和 eval_runs.sample_results 列迁移(line 42)。

**风险点**:
1. `react_events.event_type` CHECK 约束(line 79)仅允许 `('thinking','tool_call','tool_result','final','error','checkpoint')`,但代码 emit 了 `'sandbox_execution'`([service.py#L192](file:///d:/Private%20agent/backend/private_agent/sandbox/service.py#L192))和 `'memory_extracted'`([manager.py#L204](file:///d:/Private%20agent/backend/private_agent/memory/manager.py#L204)),真实 DB 上会违反 CHECK 约束,被 `except Exception` 静默吞掉。
2. `kb_chunks.embedding` 仍是 BYTEA,蓝图要求的 `vector(1024)` + HNSW 索引从未迁移。

**回滚机制**: 无独立回滚脚本(MVP 用 schema.sql 单文件 + migrations.py 增量补列)。如需回滚需手动 DROP TABLE,符合 MVP 简化原则。

### 8.D Spec AC 测试覆盖检查

| Spec | AC 总数 | 已覆盖 AC | 测试文件 | 覆盖率 |
|---|---|---|---|---|
| m4-eval-foundation | 10 | 10/10 | test_eval_models.py(AC-2), test_eval_repos.py(AC-3..AC-6), test_eval_judge_prompts.py(AC-9), test_ttl_cleanup.py(AC-10), test_skills_example_loader.py(AC-7) | 100% |
| m4-metrics-judge | 10 | 10/10 | test_eval_metrics.py(AC-1..AC-5), test_eval_judge.py(AC-6..AC-8), test_eval_hybrid.py(AC-9), 全部含边界用例(AC-10) | 100% |
| m4-eval-runner-replay | 12 | 12/12 | test_context_manager_replay.py(AC-1,AC-2), test_skill_loader_version.py(AC-3), test_eval_mock_tool_registry.py(AC-4,AC-5), test_react_loop_event_sink.py(AC-6), test_eval_runner.py(AC-7,AC-8), test_eval_replay.py(AC-8,AC-9), test_eval_version_listener.py(AC-10), mock_data 文件(AC-11), test_eval_e2e.py(AC-12) | 100% |
| m4-version-compare-rollback | 12 | 12/12 | test_eval_version_compare.py(AC-1,AC-2,AC-6), test_eval_rollback.py(AC-3..AC-5), test_eval_api.py(AC-7..AC-10), test_eval_frontend_panel.py(AC-11), test_eval_api_save_version.py(AC-12), test_eval_e2e_version_flow.py(AC-1..AC-7 闭环) | 100% |
| m4-continuous-evolution | 10 | 10/10 | test_eval_weak_sample.py(AC-1), test_eval_review_queue_repo.py(AC-2..AC-6), test_eval_review_queue_api.py(AC-7,AC-8), test_eval_continuous_evolution_e2e.py(AC-9,AC-10) | 100% |

**5 个 spec 共 54 条 AC,全部有对应测试,覆盖率 100%**。

**注**: M0/M1/M2/M3 因 spec 缺失或简化(M0/M0 回溯、M1 跳过 M1-b、M2 完全跳过 spec、M3 spec 完整),未纳入此 AC 覆盖统计。

---

## 9. 蓝图硬约束遵守情况

| 硬约束 | 遵守状态 | 证据 |
|---|---|---|
| PostgreSQL 16 + pgvector 0.8.6 必需环境 | ⚠️ 部分 | PostgreSQL 16 可用,pgvector 0.8.6 安装但未真正使用(kb_chunks.embedding 仍是 BYTEA) |
| server.http.port 必须从 load_config() 读取,禁止硬编码 | ✅ | [main.py#L293](file:///d:/Private%20agent/backend/private_agent/main.py#L293) `http_port = cfg["server"]["http"]["port"]` |
| 日志系统必须使用 setup_logger 而非 print | ✅ | [main.py#L23](file:///d:/Private%20agent/backend/private_agent/main.py#L23) `_logger = setup_logger("private_agent.main")` |
| messages 表 Frozen Zone 仅会话启动时插入一次 | ✅ | [context_manager.py#L141](file:///d:/Private%20agent/backend/private_agent/core/context_manager.py#L141) `ensure_initial` 幂等 |
| context_manager ensure_initial 幂等性 | ✅ | 同上,检查 Frozen Zone 存在性,存在则 reload,不存在才 build_initial |
| 沙箱执行通过 config.yaml + runtime 配置覆盖 | ✅ | [service.py#L24-L57](file:///d:/Private%20agent/backend/private_agent/sandbox/service.py#L24) 从 config 读取 |
| 沙箱流式输出延后实现 | ✅ | 采用执行完成后一次性返回,符合硬约束 |
| 沙箱语言支持优先 Python,JS 延后 | ✅ | 仅 Python,JS 抛 ValueError |
| 开发遵循 dev-auto 流程 | ⚠️ | M0/M1/M3/M4 遵循,M2 **跳过 spec 直接编码**,违反 |
| eval_datasets 表 CHECK 约束 + Pydantic 入库前校验 | ✅ | schema.sql CHECK + models.py validate_expected_trace |
| 评估指标五类必须全量实现 | ✅ | [metrics.py](file:///d:/Private%20agent/backend/private_agent/eval/metrics.py) 五类全实现 |
| 退化评估仅 UI 告警 + eval_runs 记录,不阻断发布 | ✅ | `_compute_diff` 标记 degraded,无强制拦截 |

---

## 10. 关键风险项分级(按 P0/P1/P2/P3)

### P0 — 阻塞 MVP 真实可用(必须修复才能交付)

| # | 风险 | 影响 | 修复建议 |
|---|---|---|---|
| P0-1 | **M1-AC-4 上下文压缩完全缺失** | 长会话必然 token 爆炸,无法处理多轮对话 | 实现 active_zone_token_limit 触发 + 三类压缩策略(滑动窗口/摘要/Stable 合并)+ 压缩后 hash 重算 |
| P0-2 | **M1-AC-5 注入防护完全缺失** | 中英文高危输入无拦截,安全风险 | 实现 injection_guard.py(三层 + 中英文 + 高低风险分级 + 沙箱差异化)+ injection_blocked event_type 加入 CHECK 白名单 |
| P0-3 | **M1-AC-6 checkpoint + interrupted 标记缺失** | 用户断线后会话无法恢复,WebSocketDisconnect 仅 pass | WebSocketDisconnect 时 UPDATE sessions.status='interrupted' + emit checkpoint 事件 |
| P0-4 | **M1-AC-7 token 计费完全缺失** | 无法成本管控,生产环境无法追溯 LLM 调用成本 | 实现 billing 模块,按对话/压缩/embedding 三类记录到 react_events 或独立表 |
| P0-5 | **M2-AC-1 知识库 embedding/vector/HNSW 全为 stub** | RAG 检索实际退化为关键词单路,核心能力缺失 | ALTER kb_chunks.embedding 为 vector(1024) + 建 HNSW 索引 + 实现 `_embed_worker_fn`(加载 bge-m3) + 实现 `vector_search` |
| P0-6 | **M2-AC-3 bge-small 自动切换完全未实现** | 低配置环境无法运行,违反蓝图 §4.10 | 实现内存检测 + 模型切换 + HNSW 索引重建 |
| P0-7 | **M2-AC-6 沙箱 512MB 内存 + 禁网络缺失** | Agent 代码可耗尽内存 + 任意网络访问,安全风险 | 用 `resource.setrlimit(RLIMIT_AS, …)` 内存限制 + 网络隔离(unshare/firejail/iptables) |
| P0-8 | **react_events event_type CHECK 约束与代码不一致** | `sandbox_execution` / `memory_extracted` 写入真实 DB 必失败,被静默吞掉,事件追溯链路断 | 扩容 CHECK 白名单含 `sandbox_execution`/`memory_extracted`/`warning`/`injection_blocked`,或改用约束内已有类型 |

### P1 — 严重功能缺陷(影响特定场景可用)

| # | 风险 | 影响 | 修复建议 |
|---|---|---|---|
| P1-1 | **M0-AC-1 Electron spawn Python sidecar 缺失** | 前端无法拉起后端,端到端不通 | 实现 frontend/main/sidecar.ts 的 spawn 逻辑 |
| P1-2 | **M0-AC-5 日志文件通道缺失** | 仅 stdout,无文件留存,故障无法追溯 | setup_logger 读取 config.file_path 并加 FileHandler |
| P1-3 | **M1-AC-2 Agnes 适配器缺失** | 四家模型只支持 3 家 | 补 agnes.py adapter(待 base_url 确认) |
| P1-4 | **M1-AC-3 hash 运行时校验缺失** | Frozen Zone 篡改无法检测 | ensure_initial / replace_frozen_zone 时比对 sessions.frozen_hash |
| P1-5 | **M2-AC-2 reranker mock + vector_search 恒返回空** | search_knowledge 退化为关键词单路 | 等 P0-5 修复后,reranker 也接入真实 bge-reranker |
| P1-6 | **M2-AC-4 MCP HTTP + 双探活缺失** | HTTP MCP server 不可用,无双探活 | 实现 HTTP MCP 路径 + ping/health 探活 |
| P1-7 | **M2-AC-5 JavaScript 沙箱缺失** | 仅 Python,JS 代码无法执行 | 实现 node 命令路径(符合硬约束"JS 延后",但蓝图 AC 要求) |
| P1-8 | **M3-AC-3 大文件"分块读取"实为截断/拒绝** | 大文件实际拿不到完整内容 | 实现 offset/pagination 分块迭代读取 |
| P1-9 | **M3-AC-6 前端 Skill 选择页缺失** | 用户无法在 UI 选择 Skill,只能 API 调用 | App.tsx 补 Skill 选择页 + 404 跳转逻辑 |
| P1-10 | **api/eval.py `_build_hybrid_evaluator` / `_build_default_adapter` ImportError** | 生产环境 POST /admin/eval/runs 会失败 | 在 hybrid_eval.py 补 `from_cfg` 类方法 + registry.py 补 `build_default_adapter` 函数 |

### P2 — 体验/文档问题(不阻塞功能)

| # | 风险 | 影响 | 修复建议 |
|---|---|---|---|
| P2-1 | **M2 spec 缺失** | 违反 dev-auto 流程,M2 三个 commit 跳过 spec | 回溯补写 m2-tools.md / m2-sandbox.md / m2-rag.md design + plan |
| P2-2 | **m4-continuous-evolution plan 缺失** | 实施方案不完整 | 回溯补写 plan |
| P2-3 | **M2-COMPLETION-HANDOFF.md 内容失真** | 自称 pgvector 已迁移但实际未迁移,误导 | 删除或加 deprecated 标注 |
| P2-4 | **CONTEXT.md 缺 M0/M1 术语** | ReAct Loop / KV Cache 分区 / ManualRouter 等核心术语缺失 | 补充术语条目 |
| P2-5 | **docs/adr/ 仅 1 个 ADR** | 关键决策无记录 | 补 M2 跳过 spec / BYTEA 延后 / 沙箱内存延后 / ReviewQueueRepo JSON 等决策 ADR |
| P2-6 | **project_memory.md commit hash 错误** | M0=d3d6f7c / M1=3e9b7d2 与实际 git log 不符 | 更正为 M0=f1bbaeb / M1=abc39f5 |
| P2-7 | **mcp-2026-07-28-impact-analysis.md 位置不规范** | 在根目录而非 artifacts/ | 移动到 .claude/artifacts/ |
| P2-8 | **9 类工具类别有偏差** | 蓝图列 file_list/mcp_proxy,实际用 calculator/datetime/read_artifact 填满 9 个 | 补 file_list / mcp_proxy 或与蓝图对齐 |
| P2-9 | **M3-AC-4 数据分析无端到端测试** | 仅测 activate,未跑真实 pandas/matplotlib 生成 chart.png → 前端展示 | 补 e2e 测试 |

### P3 — 锦上添花(可纳入 V2)

| # | 风险 | 影响 | 修复建议 |
|---|---|---|---|
| P3-1 | 20 条样本阈值未达 | 当前 12 条种子,扩充机制闭环 | 依赖真实评估运行渐进填充 |
| P3-2 | WeakSampleExtractor 无 API 触发端点 | 需手动调用 extract_from_low_score_runs | 补 POST /admin/eval/extract-weak-samples 端点(V2) |
| P3-3 | ReviewQueueRepo 用 JSON 文件存储 | 不支持并发,无事务 | 迁移到 DB 表 review_queue(V2) |
| P3-4 | FastAPI on_event deprecation | 5 个警告 | 改用 lifespan event handlers(V2) |

---

## 11. 遗留问题清单

### 11.1 已知 spec drift(实现偏离 spec)

1. **M3-AC-3 大文件"分块读取"实为截断/拒绝** —— spec 写"自动分块读取",实现是 file_read.py:86-87 截断 + 提示用 code_execution。功能等价度有限。
2. **M3-AC-6 前端"跳转选择页"未实现** —— spec 写"跳转选择页",后端 404 OK 但前端无跳转 UI。
3. **M3-AC-7 permission cache_key 未接入运行时** —— spec 写"权限缓存 cache_key 含 skill_name",函数实现且单测齐备,但运行时权限校验路径未调用(per mission.py:7 注释为 V2 预留 API surface)。
4. **M2-AC-4 9 类工具类别有偏差** —— 蓝图列 file_read/file_write/file_list/http_request/web_search/code_execution/search_knowledge/mcp_proxy/...,实际用 calculator/datetime/read_artifact 填满 9 个,缺 file_list/mcp_proxy。
5. **M2-AC-8 修正** —— 之前 subagent 报告"生产无 compress_adapter 提取不工作"不准确,实际 main.py:199-215 已绑定 compress_adapter,记忆提取可工作,但 `memory_extracted` event_type 不在 schema CHECK 白名单导致事件记录失败。

### 11.2 已知 MVP 简化项

1. **沙箱内存/网络限制延后** —— 符合 project_memory.md 硬约束"沙箱流式输出延后实现",但内存/网络未列入延后清单,属真实缺失。
2. **JavaScript 沙箱延后** —— 符合硬约束"沙箱语言支持优先实现 Python,JavaScript 延后实现"。
3. **沙箱流式输出延后** —— 符合硬约束"采用执行完成后一次性返回 stdout/stderr,实时分片推送(WS 事件)延后实现"。
4. **ReviewQueueRepo JSON 文件存储** —— MVP 避免新增 DB 表,V2 迁移。
5. **Agnes 适配器 stub** —— base_url 待确认,M1 spec 明确"Agnes stub 留 M1-b/M2 补"但未补。
6. **MCP HTTP 模式 stub** —— `McpHttpStubNotImplementedError`,符合"MVP 锁定旧协议"原则。
7. **WeakSampleExtractor 无 API 端点** —— spec §E 未定义,程序化调用。

### 11.3 已知文档失真

1. **`M2-COMPLETION-HANDOFF.md` 自称 pgvector 已迁移但实际未迁移** —— 与 schema.sql(BYTEA)直接矛盾,建议删除或加 deprecated 标注。
2. **`project_memory.md` commit hash 错误** —— M0=d3d6f7c / M1=3e9b7d2 与实际 git log(M0=f1bbaeb / M1=abc39f5)不符。

### 11.4 未跟踪文件清单

根目录存在以下未跟踪文件(需单独处理):
- `CONTEXT.md`(已更新到 M4 状态,可纳入 git)
- `M2-COMPLETION-HANDOFF.md`(内容失真,建议删除)
- `mcp-2026-07-28-impact-analysis.md`(建议移动到 .claude/artifacts/)
- `private-agent-blueprint.md`(已纳入 git,蓝图源文件)

---

## 12. 验收结论

### 12.1 总体评估

| 维度 | 评分 | 说明 |
|---|---|---|
| 测试覆盖 | 优 | 692 通过 / 0 失败,M4 spec 54 条 AC 100% 覆盖 |
| 架构完整性 | 良 | 四层骨架 + 13 张表 + ReAct 循环 + 三区 + Skills 框架 + 评估闭环齐备 |
| Dev 流程合规 | 中 | M0/M1/M3/M4 遵循 dev-auto,M2 跳过 spec |
| 真实可用性 | 差 | M1-b 4 项全缺 + M2 RAG 核心 stub,长会话/安全/成本三大闭环均断 |
| 文档完整性 | 中 | 11 design + 11 plan,但 M2 spec 缺失,m4-continuous-evolution plan 缺失 |
| 闭环验证 | 良 | 13/13 表有查询,17/17 端点已注册,1 个 P1 运行时风险 |

### 12.2 MVP 是否完成?

**按蓝图 §9.4 字面 Done Criteria**: 17/36 完全完成,约 63%。

**按蓝图 §9.7 MVP 30 项验收标准**:
- ✅ 完全通过:架构完整性 / 持久层 / 配置 / ReAct 循环 / KV Cache(部分)/ 工具层 / 场景 Skills / Skill 版本管理 / Skill 加载兜底 / 评估环境 / 数据集 / 评估指标 / 版本对比 / 迭代闭环 / 回滚机制 / 持续进化 ≈ 16 项
- ⚠️ 部分通过:模型适配(3/4)/ 沙箱执行(仅 Python)/ 沙箱安全(超时+磁盘,无内存+网络)/ 跨平台(未三平台测试)/ 权限确认(纯函数未接入)/ 检索质量(reranker mock) ≈ 6 项
- ❌ 未通过:上下文压缩 / 注入防护 / 计费感知 / 异常处理(checkpoint)/ 用户记忆(event_type 失败)/ 知识库 RAG(核心 stub)/ Embedding 降级 ≈ 7 项

**结论**: **MVP 尚未达到蓝图 §9.7 全部 30 项验收标准通过的要求**,主要阻塞在 M1-b 4 项(压缩/注入防护/checkpoint/计费)和 M2 RAG 核心链路(embedding/vector/HNSW/reranker)。M0/M3/M4 基本达成,M4 评估闭环尤为扎实(8/8 DC + 54 AC 100% 测试覆盖)。

### 12.3 推荐下一步

1. **优先修复 P0 8 项**(M1-b 4 项 + M2 RAG 3 项 + event_type CHECK 1 项),否则 MVP 真实不可用。
2. **修复 P1 10 项**,特别是 P1-10(api/eval.py ImportError)—— 生产环境评估 API 会直接失败。
3. **回溯补齐 M2 spec**(P2-1),恢复 dev-auto 流程合规性。
4. **更新 project_memory.md**(P2-6)commit hash + 实际完成度。
5. **完成 V2 roadmap**(下一阶段),把 P0/P1 缺失项纳入 V2 优先级最高的 spec。

---

**报告产出位置**: [.claude/artifacts/acceptance-report.md](file:///d:/Private%20agent/.claude/artifacts/acceptance-report.md)

**等待用户审阅**: 阶段一验收报告完成,等待用户确认是否进入阶段二(V2 roadmap 产出)。
