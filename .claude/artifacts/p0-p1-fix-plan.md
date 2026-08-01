# P0 阻塞项 + P1 严重缺陷修复方案

**产出日期**: 2026-08-01
**前置文档**: [.claude/artifacts/acceptance-report.md](file:///d:/Private%20agent/.claude/artifacts/acceptance-report.md)
**当前 HEAD**: `506e731` (master)
**蓝图源**: [private-agent-blueprint.md](file:///d:/Private%20agent/private-agent-blueprint.md)
**修复范围**: P0 八项 + P1 十项,共 18 项
**遵循流程**: dev-auto(每批先 dev-grill-docs → dev-plan → dev-tdd → dev-verify → dev-code-review → dev-finish)

---

## 0. 修复总览

### 0.1 18 项清单

| 编号 | 类别 | 阶段-AC | 一句话描述 | 蓝图章节 | 批次 |
|---|---|---|---|---|---|
| P0-1 | P0 | M1-AC-4 | 上下文压缩(三类策略 + hash 重算) | §3.9 / §3.10 / §3.11 / §3.14 | B4 |
| P0-2 | P0 | M1-AC-5 | 注入防护(三层 + 中英文 + 高低风险) | §3.12 | B3 |
| P0-3 | P0 | M1-AC-6 | checkpoint + interrupted 标记 | §2.14 | B3 |
| P0-4 | P0 | M1-AC-7 | token 计费(三类: 对话/压缩/embedding) | §3.13 / §3.14 | B4 |
| P0-5 | P0 | M2-AC-1 | RAG embedding/vector/HNSW 全栈 | §4.10 / §4.11 | B6 |
| P0-6 | P0 | M2-AC-3 | bge-small 自动切换 + 索引重建 | §4.10(轻量降级) | B6 |
| P0-7 | P0 | M2-AC-6 | 沙箱 512MB 内存 + 禁网络 | §6.7 / §6.8 | B5 |
| P0-8 | P0 | 闭环-CHECK | react_events event_type CHECK 扩容 | §3.12 / §3.13 / §6.13 | B1 |
| P1-1 | P1 | M0-AC-1 | Electron spawn Python Sidecar | §2.2 / §2.15 | B2 |
| P1-2 | P1 | M0-AC-5 | 日志文件通道(FileHandler) | §9.13 | B1 |
| P1-4 | P1 | M1-AC-3 | Frozen hash 运行时校验 | §3.4 | B1 |
| P1-5 | P1 | M2-AC-2 | reranker 接入真实 bge-reranker | §4.14 | B6 |
| P1-6 | P1 | M2-AC-4 | MCP HTTP + 双探活 | §5.4 | B2 |
| P1-7 | P1 | M2-AC-5 | JavaScript 沙箱(node 命令路径) | §6.2 / §6.15 | B2 |
| P1-8 | P1 | M3-AC-3 | file_read 大文件分块(offset/pagination) | §5.2 / M3 spec | B2 |
| P1-9 | P1 | M3-AC-6 | 前端 Skill 选择页 + 404 跳转 | §7.3 | B2 |
| P1-10 | P1 | 闭环-API | api/eval.py ImportError(from_cfg / build_default_adapter) | M4 spec | B1 |

### 0.2 修复批次与依赖关系图

```
                     ┌──────────────────────────────────────────┐
                     │  批次 B1 (基础合规修复,无依赖,4 项)        │
                     │  P0-8 (CHECK 扩容) ←─┐                    │
                     │  P1-2 (日志文件)     │                    │
                     │  P1-10 (ImportError) │                    │
                     │  P1-4 (hash 校验)    │                    │
                     └─────────┬────────────┘                    │
                               │                                 │
                               ▼                                 │
            ┌─────────────────────────────────────┐              │
            │  批次 B3 (M1-b 安全/恢复,2 项)      │              │
            │  P0-2 (注入防护) 依赖 P0-8           │              │
            │  P0-3 (checkpoint) 依赖 P0-8         │              │
            └─────────┬───────────────────────────┘              │
                      │                                          │
                      ▼                                          │
            ┌─────────────────────────────────────┐              │
            │  批次 B4 (M1-b 上下文/计费,2 项)    │              │
            │  P0-1 (压缩) 依赖 P0-8              │              │
            │  P1-4 (hash 校验,已在 B1 完成)      │              │
            │  P0-4 (计费) 依赖 P0-8 + P0-1       │              │
            └─────────────────────────────────────┘              │
                                                                     │
┌─────────────────────────────────┐    ┌──────────────────────────┘
│  批次 B2 (独立能力补全,5 项)   │    │
│  P1-1 (Electron spawn)          │    │  批次 B5 (沙箱安全,1 项)
│  P1-6 (MCP HTTP + 双探活)       │    │  P0-7 (内存+网络) 与 B1-B4 独立
│  P1-7 (JS 沙箱)                 │    └──────────────────────────────
│  P1-8 (file_read 分块)          │    ┌──────────────────────────────
│  P1-9 (前端 Skill 选择页)       │    │  批次 B6 (M2 RAG 链路,3 项串行)
└─────────────────────────────────┘    │  P0-5 (embedding/vector/HNSW)
                                       │   ↓
                                       │  P0-6 (bge-small 切换) 依赖 P0-5
                                       │   ↓
                                       │  P1-5 (reranker) 依赖 P0-5
                                       └──────────────────────────────
```

**并行性说明**:
- **B1 / B2 / B5 / B6 可同时启动**(四个独立工作流,无交叉依赖)
- **B3 等 B1 的 P0-8 完成后启动**
- **B4 等 B1 的 P0-8 + B3 的 P0-2 完成后启动**(P0-1 压缩产出的 `compressed` event_type 依赖 CHECK 扩容)
- **B6 内部串行**:P0-5 → P0-6 → P1-5

### 0.3 推荐执行顺序

单人开发,串行推进建议:

1. **B1(0.5 天)** — 快速清掉 4 项基础合规,解锁后续 B3/B4
2. **B5(0.5 天,与 B1 并行)** — 沙箱安全独立,可单独 spec
3. **B3(1 天)** — M1-b 注入防护 + checkpoint,中等复杂度
4. **B4(1.5 天)** — M1-b 压缩 + 计费,最复杂,依赖 B3
5. **B6(2 天)** — M2 RAG 全栈,环境依赖最重(pgvector + FlagEmbedding)
6. **B2(1.5 天,可穿插)** — 6 项独立能力补全,可分散在各批次间隙

总计预估 7 天(单人,含 dev-auto 全流程),其中 B6 受 FlagEmbedding 模型下载时间影响最大。

---

## 1. 批次 B1:基础合规修复(4 项,无依赖)

### P0-8 react_events event_type CHECK 约束扩容

**蓝图引用**: §3.12(注入告警 event_type)、§3.13(token_usage event_type)、§6.13(sandbox_execution event_type)、M2-AC-8(memory_extracted event_type)

**现状**:
- [schema.sql#L79](file:///d:/Private%20agent/backend/private_agent/storage/schema.sql#L79) CHECK 约束仅允许 `('thinking','tool_call','tool_result','final','error','checkpoint')`
- 代码已 emit 但写入必失败的 event_type:
  - `sandbox_execution`([service.py#L192](file:///d:/Private%20agent/backend/private_agent/sandbox/service.py#L192))
  - `memory_extracted`([manager.py#L204](file:///d:/Private%20agent/backend/private_agent/memory/manager.py#L204))
- 后续 P0-1/P0-2/P0-3/P0-4 还会新增:`compress`、`token_usage`、`injection_alert`、`injection_blocked`

**修复步骤**:
1. 修改 [schema.sql#L79](file:///d:/Private%20agent/backend/private_agent/storage/schema.sql#L79) CHECK 约束,扩容为:
   ```sql
   event_type VARCHAR(30) NOT NULL CHECK (event_type IN (
       'thinking','tool_call','tool_result','final','error','checkpoint',
       'sandbox_execution','memory_extracted',          -- 现有代码已 emit
       'compress','token_usage',                         -- P0-1/P0-4 新增
       'injection_alert','injection_blocked',            -- P0-2 新增
       'tool_error'                                       -- §4.10 异常入库告警
   ))
   ```
2. 在 [migrations.py](file:///d:/Private%20agent/backend/private_agent/storage/migrations.py) 新增幂等迁移函数 `migrate_react_events_event_type_check(conn)`:
   - 检测当前 CHECK 约束是否含 `sandbox_execution`(用 `pg_constraint` 系统表查询)
   - 若无,执行 `ALTER TABLE react_events DROP CONSTRAINT react_events_event_type_check, ADD CONSTRAINT react_events_event_type_check CHECK (...)` (新约束)
3. 在 `migrate_all` 中调用该函数

**影响文件**:
- `backend/private_agent/storage/schema.sql`(改 CHECK)
- `backend/private_agent/storage/migrations.py`(加迁移函数)

**测试**:
- 新增 `test_migrations_event_type_check.py`:
  - `test_migrate_react_events_event_type_check_idempotent`(连续调用两次不报错)
  - `test_after_migration_can_insert_sandbox_execution_event`(真实 DB 插入 `sandbox_execution` 成功)
  - `test_after_migration_can_insert_memory_extracted_event`
  - `test_after_migration_can_insert_injection_blocked_event`
- 修正 `test_sandbox_service.py::test_event_logging`:去掉 AsyncMock conn,改用真实 DB 验证 `sandbox_execution` 可入库

**依赖**: 无

**回退方案**: `ALTER TABLE react_events DROP CONSTRAINT react_events_event_type_check, ADD CONSTRAINT react_events_event_type_check CHECK (event_type IN ('thinking','tool_call','tool_result','final','error','checkpoint'))`

---

### P1-2 日志文件通道缺失

**蓝图引用**: §9.13(配置 file_path)、M0-AC-5(本地文件 + stdout 双通道)

**现状**:
- [config.yaml#L220](file:///d:/Private%20agent/backend/config/config.yaml#L220) `file_path: "${WORKSPACE}/logs/agent.log"` 配置存在
- [logging.py](file:///d:/Private%20agent/backend/private_agent/observability/logging.py) `setup_logger` 仅 `StreamHandler`,从未读取 `file_path`
- 测试 `test_logging.py` 全部用 `io.StringIO`,无文件写入测试

**修复步骤**:
1. 修改 [logging.py](file:///d:/Private%20agent/backend/private_agent/observability/logging.py) `setup_logger`:
   - 新增可选参数 `file_path: str | None = None`
   - 若 `file_path` 提供,创建 `logging.FileHandler(file_path, encoding='utf-8')`,与现有 StreamHandler 共用同一 formatter
   - 文件目录自动创建:`os.makedirs(os.path.dirname(file_path), exist_ok=True)`
2. 修改 [main.py](file:///d:/Private%20agent/backend/private_agent/main.py) 启动入口读取 config 并传入:
   ```python
   cfg = loader.load_config()
   file_path = cfg.get("observability", {}).get("log", {}).get("file_path")
   file_path = os.path.expandvars(file_path) if file_path else None
   _logger = setup_logger("private_agent.main", file_path=file_path)
   ```
3. config.yaml 的 `${WORKSPACE}` 变量已在 loader 中展开,复用现有机制

**影响文件**:
- `backend/private_agent/observability/logging.py`(加 FileHandler)
- `backend/private_agent/main.py`(传入 file_path)

**测试**:
- 新增 `test_logging_file_handler.py`:
  - `test_setup_logger_with_file_path_writes_file`(tmp_path,验证文件存在且含 JSON 日志)
  - `test_setup_logger_creates_parent_directory`(tmp_path/nonexistent/sub.log,验证目录自动创建)
  - `test_setup_logger_without_file_path_only_stdout`(无 FileHandler)
  - `test_file_handler_appends_to_existing`(两次 setup_logger 同一文件,内容追加)

**依赖**: 无

**回退方案**: `setup_logger` 的 `file_path` 默认 None,不传则行为不变

---

### P1-10 api/eval.py ImportError(from_cfg / build_default_adapter)

**蓝图引用**: M4 spec(eval/runner-replay AC-7、version-compare-rollback AC-9)

**现状**:
- [api/eval.py#L88-L92](file:///d:/Private%20agent/backend/private_agent/api/eval.py#L88) `_build_hybrid_evaluator` 调用 `HybridEvaluator.from_cfg(cfg)`,但 [hybrid_eval.py](file:///d:/Private%20agent/backend/private_agent/eval/hybrid_eval.py) 无 `from_cfg` 类方法
- [api/eval.py#L81-L85](file:///d:/Private%20agent/backend/private_agent/api/eval.py#L81) `_build_default_adapter` 调用 `build_default_adapter`,但 [registry.py](file:///d:/Private%20agent/backend/private_agent/models/registry.py) 无此函数
- 测试 `test_eval_api.py:86` 用 `monkeypatch _build_eval_runner` 规避了真实调用

**修复步骤**:
1. 在 [hybrid_eval.py](file:///d:/Private%20agent/backend/private_agent/eval/hybrid_eval.py) `HybridEvaluator` 类添加 `from_cfg` 类方法:
   ```python
   @classmethod
   def from_cfg(cls, cfg: dict) -> "HybridEvaluator":
       """从 config 构造 HybridEvaluator(spec AC-9)。"""
       from private_agent.eval.judge import build_judge_adapter, load_judge_prompt
       from private_agent.eval.metrics import compute_all_metrics
       judge_adapter = build_judge_adapter(cfg)
       judge_prompt = load_judge_prompt(cfg)
       return cls(
           judge_adapter=judge_adapter,
           judge_prompt=judge_prompt,
           metrics_fn=compute_all_metrics,
       )
   ```
2. 在 [registry.py](file:///d:/Private%20agent/backend/private_agent/models/registry.py) 添加 `build_default_adapter` 函数:
   ```python
   def build_default_adapter(cfg: dict) -> ModelAdapter:
       """构造默认模型适配器(M4 spec:评估回放用,复用 fallback chain 的首选)。"""
       chain = build_fallback_chain(cfg)
       # 返回 chain 中的第一个 provider(默认主模型)
       return chain._providers[0] if chain._providers else chain
   ```
   *(具体实现按 FallbackChain 内部结构调整,需先 Read base.py 确认)*
3. 修正 `test_eval_api.py`:移除 `_build_eval_runner` 的 monkeypatch,改用真实路径(用 mock adapter)

**影响文件**:
- `backend/private_agent/eval/hybrid_eval.py`(加 from_cfg)
- `backend/private_agent/models/registry.py`(加 build_default_adapter)
- `backend/tests/test_eval_api.py`(解除 monkeypatch,验证真实路径)

**测试**:
- 新增 `test_hybrid_evaluator_from_cfg.py`:`test_from_cfg_constructs_with_judge_adapter_and_prompt`
- 新增 `test_registry_build_default_adapter.py`:`test_build_default_adapter_returns_first_provider`
- 修正 `test_eval_api.py::test_trigger_eval_run_endpoint`:不再 monkeypatch,改用真实 _build_eval_runner + mock 底层 adapter

**依赖**: 无

**回退方案**: 若 `from_cfg` 实现复杂度超预期,可暂时在 `api/eval.py` 内联构造逻辑(从 `_build_hybrid_evaluator` 直接 new HybridEvaluator),但建议优先按 spec 实现 `from_cfg`

---

### P1-4 Frozen hash 运行时校验缺失

**蓝图引用**: §3.4(hash 校验机制)、M1-AC-3(hash 校验通过)

**现状**:
- [context_manager.py#L79](file:///d:/Private%20agent/backend/private_agent/core/context_manager.py#L79) `compute_frozen_hash()` 已实现 SHA-256
- [context_manager.py#L261](file:///d:/Private%20agent/backend/private_agent/core/context_manager.py#L261) `replace_frozen_zone` 写入 `sessions.frozen_hash`
- **无任何代码读取 `sessions.frozen_hash` 并比对计算值检测篡改**

**修复步骤**:
1. 在 [context_manager.py](file:///d:/Private%20agent/backend/private_agent/core/context_manager.py) `ensure_initial` 方法中,当 Frozen Zone 已存在(reload 路径)时增加校验:
   ```python
   async def ensure_initial(self, conn) -> None:
       existing_hash = await conn.fetchval(
           "SELECT frozen_hash FROM sessions WHERE id=$1", self._session_id
       )
       if existing_hash:
           # reload 路径:校验 hash 一致性
           await self._reload_frozen_zone(conn)
           computed = self.compute_frozen_hash()
           if computed != existing_hash:
               raise FrozenHashMismatchError(
                   f"Frozen Zone hash mismatch: stored={existing_hash}, "
                   f"computed={computed} — possible tampering"
               )
           return
       await self.build_initial(conn)
   ```
2. 新增异常类 `FrozenHashMismatchError` 在 `core/errors.py`(或复用现有 errors.py)
3. `replace_frozen_zone` 后也校验:写入新 hash 后,立即读取并比对
4. 在 [react_loop.py](file:///d:/Private%20agent/backend/private_agent/core/react_loop.py) `run_turn` 开头(每次循环开始)调用 `context_manager.verify_frozen_hash()`,作为运行时篡改检测

**影响文件**:
- `backend/private_agent/core/context_manager.py`(加校验逻辑 + verify_frozen_hash 方法)
- `backend/private_agent/core/errors.py` 或 `backend/private_agent/errors.py`(加 FrozenHashMismatchError)
- `backend/private_agent/core/react_loop.py`(可选:每轮校验)

**测试**:
- 新增 `test_context_manager_hash_verify.py`:
  - `test_ensure_initial_rejects_tampered_frozen_hash`(手动改 DB 中的 frozen_hash,ensure_initial 抛 FrozenHashMismatchError)
  - `test_ensure_initial_passes_when_hash_consistent`
  - `test_replace_frozen_zone_verifies_after_write`
  - `test_verify_frozen_hash_runtime_check`(react_loop 每轮调用的mock 验证)

**依赖**: 无

**回退方案**: 校验逻辑通过环境变量 `PA_FROZEN_HASH_VERIFY=0` 可关闭(默认开启),便于调试期绕过

---

## 2. 批次 B2:独立能力补全(6 项,可与其他批次并行)

### P1-1 Electron spawn Python Sidecar

**蓝图引用**: §2.2(进程模型)、§2.15(目录结构 sidecar.ts)、M0-AC-1

**现状**:
- [frontend/main/sidecar.ts](file:///d:/Private%20agent/frontend/main/sidecar.ts) 全文仅 3 行注释 + `export {};`
- [frontend/main/index.ts](file:///d:/Private%20agent/frontend/main/index.ts) 同样为空占位
- Python 侧 `python -m private_agent.main` 已可独立启动([main.py#L287](file:///d:/Private%20agent/backend/private_agent/main.py#L287) `run_sidecar`)

**修复步骤**:
1. 实现 `frontend/main/sidecar.ts`:
   - `spawnSidecar(config: SidecarConfig): ChildProcess` — 用 `child_process.spawn` 启动 `python -m private_agent.main`
   - `waitForHealth(port: number, timeoutMs: number): Promise<void>` — 轮询 `GET /health` 直到 200
   - `stopSidecar(proc: ChildProcess): Promise<void>` — 优雅停止(SIGTERM → 30s → SIGKILL)
   - 监听 stdout/stderr,转发到 Electron 日志
   - 崩溃自动重启(最多 3 次,指数退避)
2. 实现 `frontend/main/index.ts`:
   - `app.whenReady()` → 读取 `config.yaml`(或环境变量 `PA_CONFIG_PATH`)→ spawnSidecar → waitForHealth → 创建 BrowserWindow 加载 renderer
   - `app.on('before-quit')` → stopSidecar
   - `app.on('window-all-closed')` → quit(单人桌面端非 macOS 行为)
3. 新增 `frontend/main/config-loader.ts` — TypeScript 读取 config.yaml(用 `js-yaml`)

**影响文件**:
- `frontend/main/sidecar.ts`(实现 spawn/health/stop)
- `frontend/main/index.ts`(实现 Electron 入口)
- `frontend/main/config-loader.ts`(新增,读 config.yaml)
- `frontend/package.json`(加 electron 依赖,若尚未加)

**测试**:
- 新增 `frontend/main/__tests__/sidecar.test.ts`:
  - `test_spawn_sidecar_starts_python_process`(mock child_process.spawn,验证参数)
  - `test_wait_for_health_polls_until_200`(mock fetch,验证轮询)
  - `test_stop_sidecar_sends_sigterm_then_sigkill`(验证优雅停止 + 超时强杀)
  - `test_crash_triggers_restart_within_max_retries`
- 端到端测试:手动启动 Electron,验证 `GET /health` 返回 200 且 WS 连接建立

**依赖**: 无(纯前端,与后端独立)

**回退方案**: 保留现有的"手动启动 sidecar"路径,Electron spawn 失败时降级提示用户手动启动

---

### P1-6 MCP HTTP + 双探活

**蓝图引用**: §5.4(MCP Client)、M2-AC-4(MCP 双探活)

**现状**:
- [mcp_client.py#L85-L88](file:///d:/Private%20agent/backend/private_agent/tools/mcp_client.py#L85) HTTP 路径全 `raise McpHttpStubNotImplementedError`
- 全仓无 `ping/health_check/probe/liveness/heartbeat` 方法(MCP 相关)
- WS `ping/pong` 是另一回事(蓝图 §2.3,与本项无关)

**修复步骤**:
1. 在 [mcp_client.py](file:///d:/Private%20agent/backend/private_agent/tools/mcp_client.py) 实现 HTTP transport:
   - `MCPClient.connect(transport="http")`:用 httpx.AsyncClient 连接 HTTP MCP server
   - `discover_tools(http)`:GET `/tools` 或 POST `/rpc`(按 MCP 协议规范)
   - `call_tool(http)`:POST `/rpc` with tool_name + args
2. 实现双探活方法:
   - `ping() -> bool`(stdio:发 `{"jsonrpc":"2.0","method":"ping"}`;HTTP:GET `/health`)
   - `health_check() -> McpHealthStatus`(组合 ping + discover_tools 探测)
   - `liveness_loop(interval_sec=30)`:后台 asyncio task,定期 ping,失败触发 `on_unhealthy` 回调
3. 在 `connect` 完成后自动调一次 `ping`,失败则 disconnect + 抛 `McpConnectError`
4. config.yaml 增加 `mcp.servers[].health_check_interval_sec: 30`

**影响文件**:
- `backend/private_agent/tools/mcp_client.py`(HTTP 实现 + ping/health)
- `backend/config/config.yaml`(health_check_interval_sec)

**测试**:
- 新增 `test_mcp_client_http.py`:
  - `test_http_connect_discovers_tools`(mock httpx 200)
  - `test_http_call_tool_returns_result`
  - `test_http_disconnect_closes_client`
- 新增 `test_mcp_client_health.py`:
  - `test_ping_returns_true_when_server_healthy`
  - `test_ping_returns_false_when_server_down`
  - `test_health_check_combines_ping_and_discover`
  - `test_liveness_loop_triggers_on_unhealthy`(用 asyncio.sleep mock)

**依赖**: 无

**回退方案**: HTTP 模式可通过 config `mcp.servers[].transport: "stdio"` 显式禁用,默认仍走 stdio

---

### P1-7 JavaScript 沙箱

**蓝图引用**: §6.2(语言支持)、§6.15(跨平台)、M2-AC-5

**现状**:
- [executor.py#L85-L86](file:///d:/Private%20agent/backend/private_agent/sandbox/executor.py#L85) 非 python 直接 `raise ValueError`
- project_memory.md 硬约束:"沙箱语言支持优先实现 Python,JavaScript 延后实现"
- **冲突点**:M2-AC-5 蓝图要求 Python/JavaScript,但硬约束允许 JS 延后

**修复步骤(重新评估硬约束后纳入)**:
1. **先与用户确认**:是否解除"JS 延后"硬约束(本修复方案默认解除,因 M2-AC-5 已列入 P1)
2. 在 [executor.py](file:///d:/Private%20agent/backend/private_agent/sandbox/executor.py) `_build_command` 添加 `language == "javascript"` 分支:
   ```python
   if language == "python":
       return [python_cmd, script_path]
   if language == "javascript":
       node_cmd = self._find_node_cmd()  # which node / where node
       return [node_cmd, script_path]
   raise ValueError(f"Unsupported language: {language}")
   ```
3. config.yaml `sandbox.languages.javascript.command: "node"` + `extensions: [".js"]`
4. WorkspaceManager 创建 `.js` 文件(已有支持,只需扩展 language config)
5. CodeScanner 增加 JS 危险模式:`child_process.exec`、`require("child_process")`、`eval(`、`Function(`、`fs.unlinkSync`

**影响文件**:
- `backend/private_agent/sandbox/executor.py`(JS 命令路径)
- `backend/private_agent/sandbox/security.py`(JS 危险模式正则)
- `backend/config/config.yaml`(javascript language config)

**测试**:
- 新增 `test_sandbox_executor_js.py`:
  - `test_build_command_javascript_returns_node`
  - `test_execute_javascript_end_to_end`(真实执行 `console.log("hello")`,验证 stdout)
- 扩展 `test_sandbox_security.py`:`test_scan_javascript_dangerous_patterns`

**依赖**: 无(但需用户确认解除"JS 延后"硬约束)

**回退方案**: config `sandbox.languages.javascript.enabled: false`,沙箱拒绝 JS 仍走原路径

---

### P1-8 file_read 大文件分块读取

**蓝图引用**: §5.2(file_read)、M3-AC-3(超大文件自动分块读取)

**现状**:
- [file_read.py#L86-L87](file:///d:/Private%20agent/backend/private_agent/tools/builtins/file_read.py#L86) 超 max_lines 仅 `"\n".join(lines[:max_lines]) + "[truncated at N lines]"`(截断)
- [file_read.py#L65-L72](file:///d:/Private%20agent/backend/private_agent/tools/builtins/file_read.py#L65) 超大小直接返回 error "Use code_execution to process in chunks"(拒绝)
- spec 要求"自动分块读取",实现是"截断 + 拒绝"

**修复步骤**:
1. 在 [file_read.py](file:///d:/Private%20agent/backend/private_agent/tools/builtins/file_read.py) 新增 `offset` + `limit` 参数(分块迭代):
   ```python
   async def handler(args: FileReadArgs) -> ToolResult:
       path = args.path
       offset = args.offset or 0       # 起始行号,0-indexed
       limit = args.limit or max_lines  # 本次读取行数
       max_lines_per_call = 1000        # 单次硬上限
       if limit > max_lines_per_call:
           limit = max_lines_per_call
       # ...
       lines = all_lines[offset:offset+limit]
       has_more = (offset + limit) < total_lines
       return ToolResult(
           content="\n".join(lines),
           metadata={
               "offset": offset,
               "limit": limit,
               "total_lines": total_lines,
               "has_more": has_more,
               "next_offset": offset + limit if has_more else None,
           }
       )
   ```
2. schema 增加 `offset: int = 0`、`limit: int | None = None`(JSON Schema)
3. 超大文件(>max_file_size_mb)不再直接拒绝,而是要求 Agent 提供 offset/limit 分块读取
4. 在 tool description 中明确说明:"For large files, use offset and limit parameters to read in chunks. Check metadata.has_more and metadata.next_offset for pagination."

**影响文件**:
- `backend/private_agent/tools/builtins/file_read.py`(加 offset/limit)
- `backend/skills/office/system_prompt.md`(可选:更新使用说明)

**测试**:
- 扩展 `test_builtins_file_read.py`:
  - `test_read_with_offset_returns_partial_content`
  - `test_read_with_limit_caps_at_max_lines_per_call`
  - `test_read_returns_has_more_true_when_not_exhausted`
  - `test_read_returns_next_offset_for_pagination`
  - `test_large_file_no_longer_rejected_when_offset_provided`
  - `test_full_iteration_reads_all_chunks`(循环调用 offset=0,1000,2000... 直到 has_more=False)

**依赖**: 无

**回退方案**: 默认 `offset=0` + `limit=max_lines`,行为与现状等价(单次读取 + 截断提示)

---

### P1-9 前端 Skill 选择页 + 404 跳转

**蓝图引用**: §7.3(Skill 激活)、M3-AC-6(Skill 不存在友好错误 + 跳转选择页)

**现状**:
- 后端 `SkillNotFoundError` 兜底已就位([admin.py#L307-L308](file:///d:/Private%20agent/backend/private_agent/api/admin.py#L307) 返回 404)
- [App.tsx](file:///d:/Private%20agent/frontend/renderer/App.tsx) 全文无 Skill 选择页/路由跳转,WS error 消息只渲染为红色事件块

**修复步骤**:
1. 在 [App.tsx](file:///d:/Private%20agent/frontend/renderer/App.tsx) 新增 `SkillSelectionPanel` 组件:
   - `useEffect` 调用 `GET /admin/skills` 获取可用 Skill 列表
   - 渲染三场景卡片(office/data_analysis/frontend_design),点击触发 `POST /admin/skills/{name}/activate`
   - 激活成功后切换到 chat 视图;失败显示错误
2. 新增 view state:`'skill_selection' | 'chat' | 'eval_panel'`
3. WS 收到 `error` 消息且 `message == "skill_not_found"` 时,自动切换到 `'skill_selection'` 视图(404 跳转)
4. chat 视图顶部显示当前 locked skill 名称 + "切换 Skill" 按钮(调用切换会触发 SkillSwitchNotAllowedError,提示用户结束当前会话)

**影响文件**:
- `frontend/renderer/App.tsx`(加 SkillSelectionPanel + view state)
- 可选新增 `frontend/renderer/SkillSelectionPanel.tsx`(若组件过大拆分)

**测试**:
- 新增 `test_skill_selection_frontend.py`(用 Playwright 或 DOM 快照):
  - `test_initial_view_shows_skill_selection_panel`
  - `test_click_skill_card_triggers_activate_api`
  - `test_activate_success_switches_to_chat_view`
  - `test_skill_not_found_error_switches_to_skill_selection`
  - `test_chat_view_shows_locked_skill_name`

**依赖**: 无

**回退方案**: 通过 URL hash `#skill-select` 手动触发选择页,默认仍进 chat 视图

---

## 3. 批次 B3:M1-b 安全/恢复(2 项,依赖 B1 的 P0-8)

### P0-2 注入防护完全缺失

**蓝图引用**: §3.12(提示注入防护机制 — 三层防护 + 中英文 + 高低风险分级 + 沙箱差异化)、M1-AC-5

**现状**:
- 无 `injection_guard.py` 模块(Grep `injection_guard|InjectionGuard` 在 backend/ 零命中)
- 无 `InjectionAlert` / `InjectionScanResult` 数据类
- 无 `test_injection*.py` 测试
- react_events CHECK 无 `injection_alert` / `injection_blocked` event_type(P0-8 修复后可用)

**修复步骤**:
1. 新增 `backend/private_agent/core/injection_guard.py`:
   - `InjectionAlert` dataclass:`pattern: str, call_id: str, risk: Literal["high","low"], source: Literal["mcp","sandbox"], snippet: str`
   - `InjectionScanResult` dataclass:`high_alerts: list[InjectionAlert], low_alerts: list[InjectionAlert]`
   - `InjectionGuard` 类:
     - `HIGH_RISK_PATTERNS`(蓝图 §3.12 line 2048-2058 原文):
       ```python
       HIGH_RISK_PATTERNS = [
           r"ignore\s+(previous|above|prior)\s+(instructions?|prompt)",
           r"disregard\s+(above|prior|previous)",
           r"you\s+are\s+now\s+(a|an)\s+\w+",
           r"<\s*system\s*>",
           r"忽略(前面|以上|上文|全部)指令",
           r"无视前文所有设定",
           r"你现在切换成(管理员|开发者|系统)",
       ]
       LOW_RISK_PATTERNS = [
           r"system\s*:\s*",
           r"系统指令[:：]",
       ]
       ```
     - `MAX_TOOL_RESULT_TOKENS_MCP = 4000`、`MAX_TOOL_RESULT_TOKENS_SANDBOX = 2000`
     - `truncate_tool_result(result: str, source: Literal["mcp","sandbox"]) -> str`(按 token 截断)
     - `scan(tool_result: str, call_id: str, source: Literal["mcp","sandbox"]) -> InjectionScanResult`
     - `handle_scan_result(scan_result: InjectionScanResult, conn, session_id: int, turn: int) -> None`:
       - high_alerts:推 WS 告警 + 入 `react_events`(event_type=`injection_alert`,risk=high)
       - low_alerts:仅日志,不入 react_events
2. 在 [executor.py](file:///d:/Private%20agent/backend/private_agent/core/executor.py)(工具执行器)的工具结果回灌前调用 `InjectionGuard.scan`:
   - 每次工具执行完成,先 `truncate_tool_result(result, source)`,再 `scan`,再 `handle_scan_result`
   - source 判定:工具名 `code_execution` → `"sandbox"`,其他 → `"mcp"`
3. config.yaml 增加 `injection_guard.enabled: true`(可关)
4. **不阻断**:蓝图 §3.12 明确"告警不阻断",即使 high_alert 也仍把结果回灌给模型,仅记录 + UI 告警

**影响文件**:
- `backend/private_agent/core/injection_guard.py`(新增,核心模块)
- `backend/private_agent/core/executor.py`(集成 scan + truncate)
- `backend/config/config.yaml`(injection_guard.enabled)

**测试**:
- 新增 `test_injection_guard.py`:
  - `test_scan_high_risk_english_ignore_previous`(英文高危命中)
  - `test_scan_high_risk_chinese_ignore_instructions`(中文高危命中)
  - `test_scan_low_risk_system_colon`(低风险命中)
  - `test_scan_clean_text_returns_empty_result`
  - `test_truncate_mcp_4000_tokens_limit`
  - `test_truncate_sandbox_2000_tokens_limit`(沙箱更严格)
  - `test_handle_scan_result_high_alert_writes_react_events`(真实 DB 写入 `injection_alert`)
  - `test_handle_scan_result_low_alert_only_logs`
- 新增 `test_executor_injection_integration.py`:
  - `test_tool_result_truncated_before_injecting_to_ctx`
  - `test_injection_alert_emitted_to_ws`

**依赖**: P0-8(CHECK 扩容后才能写入 `injection_alert` event_type)

**回退方案**: `injection_guard.enabled: false`,executor 跳过 scan/truncate,行为退回现状

---

### P0-3 checkpoint + interrupted 标记

**蓝图引用**: §2.14(checkpoint 机制)、M1-AC-6

**现状**:
- [main.py#L253](file:///d:/Private%20agent/backend/private_agent/main.py#L253) `except WebSocketDisconnect: pass` — 仅 pass,未标记 session interrupted,未存储 checkpoint
- schema 支持 `sessions.status='interrupted'`([schema.sql#L16](file:///d:/Private%20agent/backend/private_agent/storage/schema.sql#L16))
- react_events CHECK 已含 `'checkpoint'`(P0-8 不影响,本项用现有 event_type)
- 但**无代码 emit checkpoint 事件**

**修复步骤**:
1. 新增 `backend/private_agent/core/checkpoint.py`:
   - `CheckpointManager` 类:
     - `save_checkpoint(conn, session_id: int, turn: int, ctx_summary: dict) -> None`:
       ```python
       # 蓝图 §2.14:payload 包含 turn + ctx 序列化摘要(不含完整 messages)
       payload = {
           "turn": turn,
           "ctx_summary": {
               "frozen_zone_len": ...,
               "stable_zone_len": ...,
               "active_zone_msg_count": ...,
               "active_zone_turn_range": [min_turn, max_turn],
           }
       }
       await insert_react_event(
           conn, session_id, turn,
           event_type="checkpoint",
           payload=payload,
       )
       ```
     - `mark_session_interrupted(conn, session_id: int) -> None`:`UPDATE sessions SET status='interrupted' WHERE id=$1`
2. 在 [react_loop.py](file:///d:/Private%20agent/backend/private_agent/core/react_loop.py) `run_turn` 结束后(每次循环结束)调 `CheckpointManager.save_checkpoint`(蓝图 §2.14 "每轮结束自动写入")
3. 在 [main.py#L253](file:///d:/Private%20agent/backend/private_agent/main.py#L253) `except WebSocketDisconnect` 分支:
   ```python
   except WebSocketDisconnect:
       # 蓝图 §2.14:用户断线 → 标记 interrupted + 存 checkpoint
       try:
           conn = await db.connect()
           try:
               await CheckpointManager.mark_session_interrupted(conn, session_id)
               # 最终 checkpoint 已在 run_turn 末尾写入,此处不重复
           finally:
               await conn.close()
       except Exception:
           _logger.exception("Failed to mark session interrupted on disconnect")
       pass
   ```
4. WS 错误路径(模型全 fail / 进程崩溃)也调 `mark_session_interrupted`

**影响文件**:
- `backend/private_agent/core/checkpoint.py`(新增)
- `backend/private_agent/core/react_loop.py`(每轮结束调 save_checkpoint)
- `backend/private_agent/main.py`(WebSocketDisconnect 分支调 mark_session_interrupted)

**测试**:
- 新增 `test_checkpoint_manager.py`:
  - `test_save_checkpoint_writes_react_event_with_correct_payload`
  - `test_save_checkpoint_payload_excludes_full_messages`
  - `test_mark_session_interrupted_updates_sessions_status`
- 新增 `test_main_ws_disconnect.py`:
  - `test_websocket_disconnect_marks_session_interrupted`(模拟 WS 断连,验证 sessions.status='interrupted')
  - `test_websocket_disconnect_does_not_raise_on_db_failure`(DB 异常不冒泡)
- 扩展 `test_react_loop.py`:`test_run_turn_writes_checkpoint_at_end`

**依赖**: 无强依赖(react_events CHECK 已含 'checkpoint'),但建议在 B1 后启动(逻辑隔离)

**回退方案**: config `checkpoint.enabled: false`,react_loop 跳过 save_checkpoint,WSDisconnect 仍 pass

---

## 4. 批次 B4:M1-b 上下文/计费(2 项,依赖 B1 + B3)

### P0-1 上下文压缩(三类策略 + hash 重算)

**蓝图引用**: §3.9(触发条件矩阵)、§3.10(三类压缩策略)、§3.11(压缩模型选型)、§3.14(TokenEstimator)、M1-AC-4

**现状**:
- [context_manager.py](file:///d:/Private%20agent/backend/private_agent/core/context_manager.py) 文件头注释 line 10 明确"压缩留 M1-b step 11"
- Grep `compress|token_limit|should_compress` 在 `core/` 零命中
- [config.yaml#L82](file:///d:/Private%20agent/backend/config/config.yaml#L82) `active_zone_token_limit: 4000` 配置存在,但无代码读取
- `test_compress_adapter.py` 仅测试压缩模型适配器构造,非上下文压缩逻辑

**修复步骤**:
1. 新增 `backend/private_agent/core/token_estimator.py`:
   - `TokenEstimator` 类(蓝图 §3.14 line 2198-2226 原文):
     - `estimate(text: str, model_id: str | None = None) -> int`(用 3.0 字符/token 兜底)
     - `estimate_messages(messages: list[Message], model_id: str | None = None) -> int`(跳过 compressed 消息)
     - `register_tokenizer(model_id, tokenizer)`(预留 tiktoken 注册,V2 用)
2. 在 [context_manager.py](file:///d:/Private%20agent/backend/private_agent/core/context_manager.py) 新增 `Compressor` 类(或集成进 ContextManager):
   - `maybe_compress(ctx: list[Message]) -> list[Message]`(蓝图 §3.9 line 1790-1800):
     - 检查 (a) token 超限:`estimate_messages(ctx) > context_window * 0.8`
     - 检查 (b) Active Zone 轮次超限:`active_turns > 10`
     - 触发则 `_apply_compression(ctx, triggers, aggressive=False)`
   - `handle_context_overflow(ctx) -> list[Message]`(蓝图 §3.9 line 1802-1804):API 103 错误紧急压缩
   - `_apply_compression(ctx, triggers, aggressive) -> list[Message]`:顺序执行 滑动窗口 → 摘要 → (条件)Stable 合并
   - `_sliding_window(ctx, keep_turns=6) -> list[Message]`(蓝图 §3.10.1 line 1829-1868):
     - 全局 call_id → (assistant_turn, tool_turn) 映射
     - 跨边界配对扩展 keep_from_turn
     - 兜底:unpaired tool_call 多保留 2 轮
     - 标记 `m.compressed = True`(保留原文,不删)
   - `_summarize(ctx, compressed_msgs) -> Message`(蓝图 §3.10.2 line 1880-1893):
     - 调 `compress_adapter.chat(summary_prompt, tools=[])`
     - 返回 `Message(role="assistant", content="[Previous Context Summary]\n...", zone="active", compressed_from=[msg_ids])`
   - `_merge_stable_zone(ctx) -> list[Message]`(蓝图 §3.10.3 line 1935-1963):
     - 触发条件:每 5 轮 或 Stable Zone 检索片段 > 20
     - 调 compress_adapter 合并所有 stable 消息
     - 旧 stable 标记 compressed,存档到 version_snapshots(scope=stable_zone)
     - 新 stable 消息追加,更新 base_stable_hash
3. 在 [react_loop.py](file:///d:/Private%20agent/backend/private_agent/core/react_loop.py) `run_turn` 末尾(每轮结束后)调 `context_manager.maybe_compress(ctx)`
4. 在 [context_manager.py](file:///d:/Private%20agent/backend/private_agent/core/context_manager.py) `ensure_initial` 中读 `active_zone_token_limit` 配置,初始化 Compressor
5. 模型适配器捕获 103 错误时调 `handle_context_overflow`(在 [react_loop.py](file:///d:/Private%20agent/backend/private_agent/core/react_loop.py) error 处理路径)
6. **压缩后 hash**:Stable Zone 合并会改 hash,通过 `stable_zone_merging` 标志允许变更,合并完成后更新 `base_stable_hash`(蓝图 §3.10.3);Active Zone 压缩不改 hash(滑动窗口只标 compressed)

**影响文件**:
- `backend/private_agent/core/token_estimator.py`(新增)
- `backend/private_agent/core/context_manager.py`(加 Compressor + maybe_compress + 三类策略)
- `backend/private_agent/core/react_loop.py`(每轮调 maybe_compress + 103 错误调 handle_context_overflow)
- `backend/private_agent/models/base.py`(可选:ModelResponse 加 usage 字段,为 P0-4 铺路)

**测试**:
- 新增 `test_token_estimator.py`:
  - `test_estimate_default_ratio_3_chars_per_token`
  - `test_estimate_messages_skips_compressed`
  - `test_estimate_messages_includes_tool_calls`
  - `test_register_tokenizer_overrides_default`
- 新增 `test_context_compression.py`:
  - `test_maybe_compress_no_trigger_when_under_limits`
  - `test_maybe_compress_triggers_on_token_limit`(mock estimate 返回 >0.8 阈值)
  - `test_maybe_compress_triggers_on_turn_limit`(11 轮 active)
  - `test_sliding_window_marks_old_messages_compressed`
  - `test_sliding_window_aligns_tool_call_result_pair`(配对不被拆分)
  - `test_sliding_window_extends_for_unpaired_tool_call`
  - `test_summarize_calls_compress_adapter`
  - `test_summarize_returns_message_with_compressed_from`
  - `test_merge_stable_zone_triggers_every_5_turns`
  - `test_merge_stable_zone_triggers_when_kb_chunks_over_20`
  - `test_merge_stable_zone_archives_to_version_snapshots`
  - `test_merge_stable_zone_updates_base_stable_hash`
  - `test_handle_context_overflow_aggressive_mode`(阈值降到 0.5,keep_turns 降到 3)
- 扩展 `test_react_loop.py`:`test_run_turn_calls_maybe_compress_at_end`

**依赖**:
- P0-8(压缩会产生 `compress` event_type,需 CHECK 扩容)
- P1-4(压缩后 hash 重算依赖 hash 校验机制已就位)

**回退方案**: config `compression.enabled: false`,maybe_compress 直接 return ctx,行为退回现状

---

### P0-4 token 计费(三类:对话/压缩/embedding)

**蓝图引用**: §3.13(上下文预算与计费感知)、§3.14(TokenEstimator)、M1-AC-7

**现状**:
- 无 billing 模块(Grep `billing|Billing|record_billing` 在 backend/private_agent/ 零命中)
- [config.yaml#L86-L88](file:///d:/Private%20agent/backend/config/config.yaml#L86) `context.compression.billing` 段有 `currency`/`price_snapshot_enabled` 配置,但无代码读取
- 无 `test_billing*.py`

**修复步骤**:
1. 新增 `backend/private_agent/core/billing.py`:
   - `TokenUsage` dataclass(蓝图 §3.13 line 2123-2128):
     ```python
     @dataclass
     class TokenUsage:
         input_tokens: int
         output_tokens: int
         total_tokens: int
         cached_tokens: int = 0
     ```
   - `BillingRecorder` 类:
     - `__init__(self, conn, pricing_config: dict)` — 读 config.yaml 各模型 pricing
     - `record_usage(session_id, turn, model_id, usage: TokenUsage, cost_type: Literal["dialogue","compress","eval"]) -> None`(蓝图 §3.13 line 2157-2173):
       ```python
       cost = self._calculate_cost(model_id, usage, cost_type)
       await insert_react_event(
           conn, session_id, turn,
           event_type="token_usage",
           payload={
               "model_id": model_id,
               "cost_type": cost_type,
               "input_tokens": usage.input_tokens,
               "output_tokens": usage.output_tokens,
               "cached_tokens": usage.cached_tokens,
               "currency": pricing.currency,
               "cost": cost,
           }
       )
       ```
     - `_calculate_cost(model_id, usage, cost_type) -> float`:
       - input_cost = (input_tokens - cached_tokens) / 1000 * input_per_1k + cached_tokens / 1000 * cached_input_per_1k
       - output_cost = output_tokens / 1000 * output_per_1k
       - return input_cost + output_cost
2. 在 [models/base.py](file:///d:/Private%20agent/backend/private_agent/models/base.py) `ChatResult` 加 `usage: TokenUsage | None = None` 字段(若已有则复用)
3. 各适配器(GLM/DeepSeek/Kimi)从 API 响应提取 usage 字段填充到 ChatResult
4. 在 [react_loop.py](file:///d:/Private%20agent/backend/private_agent/core/react_loop.py) `_emit_thinking_event` 后(模型调用完成后)调 `BillingRecorder.record_usage(cost_type="dialogue")`
5. 在 [context_manager.py](file:///d:/Private%20agent/backend/private_agent/core/context_manager.py) `_summarize` / `_merge_stable_zone` 后调 `record_usage(cost_type="compress")`(压缩调用)
6. 在 [embedding_service.py](file:///d:/Private%20agent/backend/private_agent/knowledge/embedding_service.py) 云端降级路径调 `record_usage(cost_type="eval")`(蓝图 §4.10 line 3020:云端 embedding 计费记入 compress 类别,实际归类见 spec — 本方案按蓝图原文归 compress)
7. 价格版本快照:每次 config.yaml 改 pricing 时,在 `version_snapshots` 表写 scope=`model_pricing` 快照(蓝图 §3.13 line 2152)
8. 新增 API `GET /admin/billing/summary?session_id=...` 返回三类成本汇总(供前端展示)

**影响文件**:
- `backend/private_agent/core/billing.py`(新增)
- `backend/private_agent/models/base.py`(ChatResult 加 usage)
- `backend/private_agent/models/adapters/glm.py`(提取 usage)
- `backend/private_agent/models/adapters/deepseek.py`(同)
- `backend/private_agent/models/adapters/kimi.py`(同)
- `backend/private_agent/core/react_loop.py`(dialogue 计费)
- `backend/private_agent/core/context_manager.py`(compress 计费)
- `backend/private_agent/knowledge/embedding_service.py`(eval/embedding 计费)
- `backend/private_agent/api/admin.py`(GET /admin/billing/summary 端点)

**测试**:
- 新增 `test_billing.py`:
  - `test_record_usage_writes_token_usage_event`(真实 DB 写入)
  - `test_calculate_cost_input_output_cached`(input/output/cached 三类单价)
  - `test_calculate_cost_caches_discount_applied`(cached_tokens 用 cached_input_per_1k)
  - `test_record_usage_cost_type_dialogue`
  - `test_record_usage_cost_type_compress`
  - `test_record_usage_cost_type_eval`
  - `test_pricing_snapshot_saved_on_config_change`(version_snapshots 写入)
- 新增 `test_billing_api.py`:
  - `test_get_billing_summary_returns_three_categories`
  - `test_get_billing_summary_aggregates_by_model`
- 扩展 `test_model_adapters.py`:验证各适配器正确提取 usage 字段

**依赖**:
- P0-8(`token_usage` event_type 需 CHECK 扩容)
- P0-1(compress cost_type 由压缩调用产生)

**回退方案**: config `billing.enabled: false`,适配器不提取 usage,BillingRecorder 不写入,行为退回现状

---

## 5. 批次 B5:沙箱安全(1 项,独立)

### P0-7 沙箱 512MB 内存 + 禁网络

**蓝图引用**: §6.7(沙箱资源限制)、§6.8(沙箱安全边界)、M2-AC-6

**现状**:
- [executor.py](file:///d:/Private%20agent/backend/private_agent/sandbox/executor.py) `asyncio.create_subprocess_exec` 无任何 RLIMIT 设置
- [service.py#L43-L45](file:///d:/Private%20agent/backend/private_agent/sandbox/service.py#L43) `__init__` 只读 `cpu_timeout_sec` 与 `disk_limit_mb`,**根本未读取** `memory_limit_mb`
- Grep `memory_limit|resource\.|rlimit` 在 sandbox 下零命中
- Grep `seccomp|unshare|network|net_cls` 在 sandbox 下零命中,无网络隔离

**修复步骤**:
1. 新增 `backend/private_agent/sandbox/resource_limiter.py`:
   - `ResourceLimiter` 类(蓝图 §6.7 line 5397-5426):
     ```python
     import resource
     import os
     
     class ResourceLimiter:
         def __init__(self, memory_limit_mb: int, cpu_timeout_sec: int):
             self.memory_limit = memory_limit_mb * 1024 * 1024
             self.cpu_timeout = cpu_timeout_sec
         
         def get_preexec_fn(self):
             """返回 preexec_fn(仅 Linux/macOS,Windows 返回 None)"""
             if os.name == "nt":
                 return None
             def _set_limits():
                 resource.setrlimit(resource.RLIMIT_AS, (self.memory_limit, self.memory_limit))
                 resource.setrlimit(resource.RLIMIT_CPU, (self.cpu_timeout, self.cpu_timeout))
             return _set_limits
     ```
2. 修改 [service.py](file:///d:/Private%20agent/backend/private_agent/sandbox/service.py) `__init__` 读取 `memory_limit_mb`(蓝图 §6.7 line 5386 默认 512):
   ```python
   self.memory_limit_mb = config.get("sandbox", {}).get("limits", {}).get("memory_limit_mb", 512)
   self.resource_limiter = ResourceLimiter(self.memory_limit_mb, self.cpu_timeout_sec)
   ```
3. 修改 [executor.py](file:///d:/Private%20agent/backend/private_agent/sandbox/executor.py) `_build_command` / `execute`:
   - `asyncio.create_subprocess_exec(..., preexec_fn=self.resource_limiter.get_preexec_fn())`
   - Windows 下 preexec_fn=None,仅依赖超时兜底(蓝图 §6.7 line 5405-5406 明确)
4. 网络隔离(应用层,蓝图 §6.7 line 5432-5444):
   - 新增 `_disable_network(env: dict) -> dict`:
     ```python
     env["HTTP_PROXY"] = "invalid"
     env["HTTPS_PROXY"] = "invalid"
     env["NO_PROXY"] = "*"
     env["http_proxy"] = "invalid"
     env["https_proxy"] = "invalid"
     return env
     ```
   - 在 `_build_sandbox_env` 中调用(已有 EnvSanitizer,在 sanitize 后追加 _disable_network)
5. Windows 内存限制兜底(蓝图 §6.15 line 6069-6082):
   - 用 `psutil` 监控子进程内存,超 512MB 主动 `process.terminate()`
   - 这是 V2 Job Object API 前的 MVP 兜底
6. 资源超限处理(蓝图 §6.7 line 5449-5454):
   - CPU 超时:`asyncio.wait_for` 捕获 TimeoutError → terminate(已实现)
   - 内存超限(Linux):RLIMIT_AS 触发 MemoryError,子进程崩溃,返回 stderr
   - 磁盘超限:执行前 check_disk_usage(已实现,但仅前置)
   - 网络访问:代理无效,Agent 收到错误

**影响文件**:
- `backend/private_agent/sandbox/resource_limiter.py`(新增)
- `backend/private_agent/sandbox/service.py`(读 memory_limit_mb + 构造 ResourceLimiter)
- `backend/private_agent/sandbox/executor.py`(传 preexec_fn + _disable_network)
- `backend/private_agent/sandbox/security.py` 或 `workspace.py`(_disable_network 函数位置)

**测试**:
- 新增 `test_resource_limiter.py`:
  - `test_get_preexec_fn_returns_callable_on_linux`(mock os.name='posix')
  - `test_get_preexec_fn_returns_none_on_windows`(mock os.name='nt')
  - `test_preexec_fn_sets_rlimit_as`(mock resource.setrlimit,验证调用参数)
  - `test_preexec_fn_sets_rlimit_cpu`
- 新增 `test_sandbox_memory_limit.py`(Linux only,用 skipif):
  - `test_memory_limit_kills_oom_process`(子进程分配 600MB,触发 MemoryError)
  - `test_memory_limit_allows_normal_process`(子进程分配 100MB,正常完成)
- 新增 `test_sandbox_network_disable.py`:
  - `test_disable_network_sets_invalid_proxy`
  - `test_disable_network_sets_no_proxy_star`
  - `test_subprocess_env_has_invalid_proxy`(端到端验证子进程无法访问网络,用 mock socket)
- Windows 专项:`test_sandbox_memory_psutil_terminate`(mock psutil,验证超 512MB 调 terminate)

**依赖**: 无(沙箱模块独立)

**回退方案**:
- Linux/macOS:`config.sandbox.limits.memory_limit_mb: 0` 跳过 RLIMIT_AS(但 cpu_timeout 仍生效)
- 网络隔离:config `sandbox.limits.network_isolation: false` 跳过 _disable_network
- 蓝图明确"V2 用 Docker `--network=none` 内核级隔离",MVP 仅应用层防护

---

## 6. 批次 B6:M2 RAG 核心链路(3 项,串行依赖)

### P0-5 RAG embedding/vector/HNSW 全栈

**蓝图引用**: §4.10(Embedding 模型与 Worker 集成)、§4.11(pgvector HNSW 索引配置)、M2-AC-1

**现状**:
- [schema.sql#L127-L138](file:///d:/Private%20agent/backend/private_agent/storage/schema.sql#L127) `kb_chunks.embedding BYTEA NOT NULL DEFAULT '\x'::bytea`,注释明写"M0 占位 BYTEA;M2 RAG 阶段需 ALTER 为 vector(1024) + HNSW 索引" — ALTER 从未执行
- [embedding_service.py#L123-L129](file:///d:/Private%20agent/backend/private_agent/knowledge/embedding_service.py#L123) worker_pool None 时返回 `[[0.0]*dim]` mock 全 0 向量
- [embedding_service.py#L189-L206](file:///d:/Private%20agent/backend/private_agent/knowledge/embedding_service.py#L189) `_embed_worker_fn` 直接 `raise NotImplementedError("Worker embedding requires FlagEmbedding library")`
- [kb_repo.py#L375-L400](file:///d:/Private%20agent/backend/private_agent/knowledge/kb_repo.py#L375) `vector_search`:L400 `return []` 恒返回空
- [kb_service.py](file:///d:/Private%20agent/backend/private_agent/knowledge/kb_service.py) `_vector_to_bytes` 把 mock 向量 struct.pack 成 BYTEA

**修复步骤**:
1. **DB schema 迁移**:
   - 在 [migrations.py](file:///d:/Private%20agent/backend/private_agent/storage/migrations.py) 新增 `migrate_kb_chunks_embedding_to_vector(conn)`:
     ```sql
     -- 1. 添加新列 vector_embedding vector(1024)
     ALTER TABLE kb_chunks ADD COLUMN vector_embedding vector(1024);
     -- 2. 创建 HNSW 索引
     CREATE INDEX idx_kb_chunks_vector_embedding_hnsw ON kb_chunks
         USING hnsw (vector_embedding vector_cosine_ops)
         WITH (m = 16, ef_construction = 128);
     -- 3. (可选)删除旧 BYTEA 列,或保留兼容
     ```
   - 检测 pgvector 扩展是否已安装:`CREATE EXTENSION IF NOT EXISTS vector`
   - 幂等:检查 `information_schema.columns` 是否已有 `vector_embedding` 列
2. **schema.sql 更新**:将 `embedding BYTEA` 改为 `embedding vector(1024) NOT NULL`,CREATE INDEX 语句加入 HNSW(蓝图 §4.11 line 3037-3039)
3. **FlagEmbedding 集成**:
   - 在 [embedding_service.py](file:///d:/Private%20agent/backend/private_agent/knowledge/embedding_service.py) `_embed_worker_fn` 实现:
     ```python
     def _embed_worker_fn(texts: list[str]) -> list[list[float]]:
         from FlagEmbedding import BGEM3FlagModel
         model = BGEM3FlagModel("BAAI/bge-m3", use_fp16=True)
         embeddings = model.encode(texts, batch_size=32, max_length=8192)["dense_vecs"]
         return embeddings.tolist()
     ```
   - Worker 进程池初始化时预热模型(蓝图 §4.10 line 2920)
4. **kb_repo.vector_search 实现**(蓝图 §4.11 line 3054-3078):
   ```python
   async def vector_search(self, query_vector: list[float], limit: int = 20,
                           ef_search: int = 64, filters: dict = None) -> list[Chunk]:
       async with self.db.transaction():
           await self.db.execute(f"SET LOCAL hnsw.ef_search = {ef_search}")
           sql = """
               SELECT id, doc_id, scenario, source, chunk_text,
                      1 - (embedding <=> $1) AS similarity
               FROM kb_chunks WHERE 1=1
           """
           params = [str(query_vector)]
           if filters and "scenario" in filters:
               sql += f" AND scenario = ${len(params)+1}"
               params.append(filters["scenario"])
           sql += f" ORDER BY embedding <=> $1 LIMIT {limit}"
           rows = await self.db.fetch(sql, *params)
           return [Chunk.from_row(r) for r in rows]
   ```
5. **kb_service 适配**:
   - 删除 `_vector_to_bytes` / `_bytes_to_vector` 辅助(BYTEA 专用)
   - embed_chunks 直接返回 list[list[float]],写入 `embedding` 列(pgvector 自动处理)
6. **hybrid_search 修正**:[kb_repo.py#L471-L476](file:///d:/Private%20agent/backend/private_agent/knowledge/kb_repo.py#L471) 现在的 `vector_search` 恒返回 `[]` 修复后,RRF 融合能真正双路输入
7. **pyproject.toml**:加 `FlagEmbedding>=1.2.5` 到 RAG 可选依赖

**影响文件**:
- `backend/private_agent/storage/schema.sql`(embedding 改 vector(1024) + HNSW 索引)
- `backend/private_agent/storage/migrations.py`(加 migrate_kb_chunks_embedding_to_vector)
- `backend/private_agent/knowledge/embedding_service.py`(实现 _embed_worker_fn + Worker 预热)
- `backend/private_agent/knowledge/kb_repo.py`(实现 vector_search + 删除 BYTEA 辅助)
- `backend/private_agent/knowledge/kb_service.py`(适配 vector 列)
- `backend/pyproject.toml`(加 FlagEmbedding 依赖)

**测试**:
- 新增 `test_migrations_vector_embedding.py`:
  - `test_migrate_kb_chunks_embedding_to_vector_idempotent`
  - `test_after_migration_hnsw_index_exists`(查 pg_indexes)
  - `test_after_migration_can_insert_vector`(INSERT vector(1024) 成功)
- 新增 `test_embedding_worker_real.py`(需 FlagEmbedding 已下载,用 skipif):
  - `test_embed_worker_returns_1024_dim_vector`
  - `test_embed_worker_batch_consistent`
- 新增 `test_kb_repo_vector_search.py`(需真实 pgvector):
  - `test_vector_search_returns_top_k_by_cosine_similarity`
  - `test_vector_search_filters_by_scenario`
  - `test_vector_search_ef_search_runtime_override`
  - `test_vector_search_empty_table_returns_empty_list`
- 修正 `test_knowledge_services.py::test_embed_chunks_no_worker_returns_mock`:保留为降级路径测试,但加 `test_embed_chunks_with_worker_returns_real_vector`
- 修正 `test_kb_repo.py`:删除 BYTEA 相关测试,加 vector 相关测试

**依赖**:
- PostgreSQL 16 + pgvector 0.8.6 已安装(蓝图硬约束)
- FlagEmbedding 模型需下载(约 2GB,首次启动慢)

**回退方案**:
- 若 FlagEmbedding 安装失败,保留 worker_pool=None 路径(mock 全 0 向量),但 vector_search 仍能工作(只是相似度全是 1.0)
- 若 pgvector 不可用,migrate_kb_chunks_embedding_to_vector 抛错,启动失败(硬约束要求 pgvector 必需)

---

### P0-6 bge-small 自动切换 + 索引重建

**蓝图引用**: §4.10(轻量模型降级方案,line 2959-2983)、M2-AC-3

**现状**:
- [embedding_service.py#L56](file:///d:/Private%20agent/backend/private_agent/knowledge/embedding_service.py#L56) `self._auto_switch_gb = self._config.get("auto_switch_memory_gb", 6)` — 配置被读取但全文未再使用
- Grep `auto_switch|memory_gb|rebuild` 在 backend/private_agent 下仅命中这一行
- 无 `test_embedding*.py` 测试文件
- HNSW 本就不存在(P0-5 修复前),无从"重建"

**修复步骤**:
1. 在 [embedding_service.py](file:///d:/Private%20agent/backend/private_agent/knowledge/embedding_service.py) 实现 `select_model_by_memory()`(蓝图 §4.10 line 2972-2980):
   ```python
   @staticmethod
   def select_model_by_memory() -> str:
       import psutil
       avail_gb = psutil.virtual_memory().available / (1024 ** 3)
       if avail_gb < 6.0:
           logger.warning(f"Available memory {avail_gb:.1f}GB < 6GB, using light model")
           return "BAAI/bge-small-zh-v1.5"
       return "BAAI/bge-m3"
   ```
2. Worker 进程启动时调 `select_model_by_memory()` 选择模型,加载对应模型
3. **维度兼容约束处理**(蓝图 §4.10 line 2983):
   - bge-small 为 384 维,bge-m3 为 1024 维,**不兼容**
   - 切换模型时触发 HNSW 索引重建:`kb_chunks.embedding` 全量重算
   - 实现 `rebuild_index_after_model_switch(new_dim: int)`:
     - 读取所有 kb_chunks 的 chunk_text
     - 用新模型重新 embedding
     - UPDATE kb_chunks.embedding
     - DROP + CREATE HNSW 索引(若 dim 变化)
4. config.yaml 更新(蓝图 §4.10 line 2961-2968):
   ```yaml
   kb:
     embedding:
       local_default: "BAAI/bge-m3"
       local_light: "BAAI/bge-small-zh-v1.5"
       fallback_cloud: "glm-embedding"
       auto_switch_memory_gb: 6
   ```
5. query LRU 缓存(蓝图 §4.10 line 2987-3001):
   - Worker 进程内 `@lru_cache(maxsize=512)` 缓存 query 向量
   - Sidecar 每 10 分钟触发 `cache_clear()`
6. 异常入库告警(蓝图 §4.10 line 3003-3018):Worker 崩溃时 emit `tool_error` event_type 到 react_events

**影响文件**:
- `backend/private_agent/knowledge/embedding_service.py`(select_model_by_memory + rebuild_index + LRU + 异常告警)
- `backend/private_agent/knowledge/kb_repo.py`(rebuild_index_after_model_switch 辅助函数)
- `backend/config/config.yaml`(local_default / local_light / auto_switch_memory_gb)

**测试**:
- 新增 `test_embedding_auto_switch.py`:
  - `test_select_model_by_memory_returns_light_when_under_6gb`(mock psutil,available=4GB)
  - `test_select_model_by_memory_returns_default_when_over_6gb`(mock psutil,available=8GB)
- 新增 `test_embedding_rebuild_index.py`(需真实 pgvector):
  - `test_rebuild_index_after_model_switch_updates_all_embeddings`
  - `test_rebuild_index_drops_and_creates_hnsw_when_dim_changes`(1024 → 384)
- 新增 `test_embedding_lru_cache.py`:
  - `test_lru_cache_hits_same_query`(同一 query 两次,第二次零推理)
  - `test_lru_cache_clear_resets_cache`
- 扩展 `test_embedding_service.py`:异常路径写入 `tool_error` react_event

**依赖**: P0-5(必须有真实的 bge-m3 加载 + HNSW 索引才能切换 + 重建)

**回退方案**: config `kb.embedding.auto_switch_enabled: false`,始终用 local_default,不切换

---

### P1-5 reranker 接入真实 bge-reranker

**蓝图引用**: §4.14(reranker 模型加载)、M2-AC-2

**现状**:
- [reranker_service.py#L62-L70](file:///d:/Private%20agent/backend/private_agent/knowledge/reranker_service.py#L62) worker_pool None 时跳过重排,把所有候选 `c.score = 1.0` 直接返回 top-k
- [reranker_service.py#L120-L137](file:///d:/Private%20agent/backend/private_agent/knowledge/reranker_service.py#L120) `_rerank_worker_fn` `raise NotImplementedError`,bge-reranker 未加载
- 所有候选 score 被 mock 置为 1.0,min_similarity=0.2 过滤形同虚设

**修复步骤**:
1. 在 [reranker_service.py](file:///d:/Private%20agent/backend/private_agent/knowledge/reranker_service.py) 实现 `_rerank_worker_fn`(蓝图 §4.14 line 3227-3265):
   ```python
   def _rerank_worker_fn(query: str, candidate_texts: list[str]) -> list[float]:
       from FlagEmbedding import FlagReranker
       model = FlagReranker("BAAI/bge-reranker-v2-m3", use_fp16=True)
       scores = model.compute_score(
           [[query, text] for text in candidate_texts],
           normalize=True  # 归一化到 [0,1]
       )
       return scores.tolist() if hasattr(scores, 'tolist') else list(scores)
   ```
2. Worker 进程池预热 bge-reranker 模型(蓝图 §4.14 line 3227)
3. `RerankerService.rerank(query, candidates, top_k)` 调用 Worker:
   ```python
   async def rerank(self, query: str, candidates: list[Chunk], top_k: int = 5) -> list[Chunk]:
       if self._worker_pool is None:
           # 降级:不重排,直接返回 top-k(蓝图 §4.14 line 3267)
           logger.warning("Reranker worker unavailable, skipping rerank")
           for c in candidates[:top_k]:
               c.score = 1.0
           return candidates[:top_k]
       texts = [c.text for c in candidates]
       scores = await loop.run_in_executor(self._worker_pool, _rerank_worker_fn, query, texts)
       for c, score in zip(candidates, scores):
           c.score = score
       candidates.sort(key=lambda x: x.score, reverse=True)
       return candidates[:top_k]
   ```
4. 降级路径保留:Worker 崩溃时跳过重排,日志告警(蓝图 §4.14 line 3267)
5. 异常入库告警:reranker 不可用时 emit `tool_error` event_type

**影响文件**:
- `backend/private_agent/knowledge/reranker_service.py`(实现 _rerank_worker_fn + Worker 预热)
- `backend/pyproject.toml`(FlagEmbedding 已含 reranker,无需新依赖)

**测试**:
- 新增 `test_reranker_worker_real.py`(需 FlagEmbedding 已下载,用 skipif):
  - `test_rerank_worker_returns_normalized_scores`
  - `test_rerank_worker_higher_score_for_relevant_doc`
- 扩展 `test_reranker_service.py`:
  - `test_rerank_with_worker_reorders_by_score`
  - `test_rerank_without_worker_falls_back_to_no_op`(降级路径)
  - `test_rerank_emits_tool_error_on_worker_crash`
- 端到端 `test_search_knowledge_e2e.py`(需真实 RAG):
  - `test_search_knowledge_returns_reranked_results`
  - `test_search_knowledge_min_similarity_filters_low_score`(score<0.2 被过滤)

**依赖**: P0-5(Worker 进程池已就位,bge-m3 加载机制复用)

**回退方案**: config `kb.reranker.enabled: false`,始终走降级路径(score=1.0)

---

## 7. 验收与回归策略

### 7.1 每批次验收标准

每批次完成需满足:
1. **dev-auto 全流程**:dev-grill-docs → dev-plan → dev-tdd → dev-verify → dev-code-review → dev-finish
2. **测试全绿**:新增测试 + 修改的现有测试全部通过
3. **全量 pytest**:692 个现有测试 + 新增测试全部通过(`PA_DB_PASSWORD=123123 PA_TEST_DSN=... python -m pytest`)
4. **闭环验证**:公开符号闭环(__all__ 必须有调用者)、API 端点闭环、DB 迁移幂等
5. **代码评审 READY**:无 P0/P1 级 review 意见

### 7.2 P0/P1 完成后总验收

按验收报告 §10 P0/P1 列表逐项核验:

| 编号 | 验收方式 | 通过标准 |
|---|---|---|
| P0-1 | 真实长会话(20 轮)+ 查询 messages 表 compressed 标记 | 至少触发一次压缩,compressed_from 字段非空 |
| P0-2 | 发送中英文注入测试用例 + 查询 react_events | injection_alert 事件入库,UI 收到告警 |
| P0-3 | 模拟 WS 断连 + 查询 sessions.status | status='interrupted',checkpoint 事件存在 |
| P0-4 | 跑一轮 ReAct + 查询 react_events | token_usage 事件三类(dialogue/compress/eval)有记录 |
| P0-5 | 上传测试文档 + 调 search_knowledge | 返回真实向量检索结果(非空 + similarity 分数有差异) |
| P0-6 | 模拟低内存环境 + 查询日志 | "using light model" 警告,HNSW 索引重建成功 |
| P0-7 | 沙箱执行 OOM 代码 + 网络访问代码 | 内存超限崩溃,网络访问失败 |
| P0-8 | INSERT sandbox_execution/memory_extracted 事件 | 真实 DB 写入成功 |
| P1-1 | 启动 Electron + GET /health | 200 + WS 连接建立 |
| P1-2 | 启动 sidecar + 检查 logs/agent.log | 文件存在 + 含 JSON 日志 |
| P1-4 | 手动改 DB frozen_hash + ensure_initial | 抛 FrozenHashMismatchError |
| P1-5 | search_knowledge 返回结果 | reranker score 有差异(非全 1.0) |
| P1-6 | config 配 HTTP MCP server + ping | ping 成功,discover_tools 返回工具列表 |
| P1-7 | 沙箱执行 `console.log("hello")` | stdout 含 "hello" |
| P1-8 | file_read 大文件 + offset=1000 | 返回第 1000-2000 行,has_more=true |
| P1-9 | 启动前端 + 触发 skill_not_found | 自动跳转 Skill 选择页 |
| P1-10 | POST /admin/eval/runs(无 monkeypatch) | 真实路径执行成功,eval_runs 入库 |

### 7.3 回归测试矩阵

每批次提交前必跑:
- 全量 pytest(692 + 新增)
- 受影响模块的端到端测试
- DB 迁移幂等测试(连续 migrate_all 两次不报错)
- 公开符号闭环检查(grep `__all__` → 验证调用者)

### 7.4 文档同步

每批次完成后更新:
- `project_memory.md`:Phase Status + Lessons Learned
- `CONTEXT.md`:新增术语(如 InjectionGuard / BillingRecorder / ResourceLimiter / TokenEstimator)
- `.claude/artifacts/`:补 design + plan 文档(dev-auto 流程产物)
- `docs/adr/`:补关键决策 ADR(如 P0-8 CHECK 扩容策略、P0-5 BYTEA→vector 迁移)

---

## 8. 风险与回退预案

### 8.1 高风险项

| 风险 | 概率 | 影响 | 缓解措施 |
|---|---|---|---|
| FlagEmbedding 模型下载失败/超时(P0-5) | 中 | RAG 全栈阻塞 | 提前手动下载到 HF cache;保留 mock 降级路径 |
| pgvector HNSW 索引创建耗时长(10 万 chunk 约 5-10 分钟) | 中 | P0-5 测试慢 | 测试用小数据集(100 chunk);生产用 REINDEX CONCURRENTLY |
| RLIMIT_AS 导致 psutil/asyncio 子进程异常崩溃(P0-7) | 低 | 沙箱不可用 | 限制设为 512MB(蓝图默认),测试用独立进程验证 |
| Windows 下 preexec_fn=None 内存限制完全失效(P0-7) | 高 | Windows 端沙箱无内存隔离 | 用 psutil 监控 + terminate 兜底(蓝图 §6.15 明确) |
| 压缩策略破坏 ReAct 循环(配对断裂)(P0-1) | 中 | 长会话上下文错乱 | 严格按蓝图 §3.10.1 全局 call_id_map 实现 + 配对测试 |

### 8.2 整体回退方案

若某批次引入严重回归:
1. `git revert <batch_merge_commit>` 回滚该批次
2. 在 project_memory.md 记录回退原因
3. 重新进入 dev-grill-docs 重新设计

### 8.3 不修复项说明

以下验收报告 P2/P3 项**不在本方案范围**,留 V2:
- P2-1 M2 spec 回溯补写(独立文档任务,非功能)
- P2-2 m4-continuous-evolution plan 缺失(同上)
- P2-3 M2-COMPLETION-HANDOFF.md 内容失真(删除即可,非功能)
- P2-4 CONTEXT.md 缺 M0/M1 术语(文档补全)
- P2-5 docs/adr 仅 1 个(文档补全)
- P2-6 project_memory.md commit hash 错误(本方案每批次会同步修正)
- P2-7 mcp-2026-07-28-impact-analysis.md 位置(文件移动)
- P2-8 9 类工具类别偏差(file_list/mcp_proxy 缺失)— 留 V2
- P2-9 M3-AC-4 数据分析无端到端测试 — 留 V2
- P3-1/P3-2/P3-3/P3-4 全部留 V2

---

## 9. 执行检查清单

### 9.1 启动前确认

- [ ] 用户审阅本修复方案,确认批次顺序与依赖关系
- [ ] 用户确认是否解除"JS 延后"硬约束(P1-7)
- [ ] 确认 PostgreSQL 16 + pgvector 0.8.6 环境可用(P0-5/P0-6 必需)
- [ ] 确认 FlagEmbedding 可安装(P0-5/P0-6/P1-5 必需)

### 9.2 批次执行顺序(单人推荐)

1. **B1(4 项,0.5 天)**:P0-8 / P1-2 / P1-10 / P1-4
2. **B5(1 项,0.5 天,与 B1 并行)**:P0-7
3. **B3(2 项,1 天)**:P0-2 / P0-3
4. **B4(2 项,1.5 天)**:P0-1 / P0-4
5. **B6(3 项,2 天)**:P0-5 → P0-6 → P1-5
6. **B2(5 项,1.5 天,可穿插)**:P1-1 / P1-6 / P1-7 / P1-8 / P1-9

总计:7 天(单人,含 dev-auto 全流程 + 测试 + 评审)

### 9.3 完成后交付物

- 17 项修复全部 commit 到 master
- 6 个批次 spec 文档(design + plan)在 `.claude/artifacts/`
- project_memory.md 更新(Phase Status + commit hash + Lessons Learned)
- CONTEXT.md 补全术语
- docs/adr/ 补关键决策 ADR
- 全量 pytest 通过(692 + 新增,预计 800+)
- 验收报告 §10 P0/P1 项全部 ✅

---

**等待用户审阅**:本修复方案完整,等待确认后按批次顺序启动 dev-auto 流程。
