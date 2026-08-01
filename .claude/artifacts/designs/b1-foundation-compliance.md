# B1 Foundation Compliance Spec

> Status: ALIGNED
> Author: user
> Last updated: 2026-08-01

## Background

B1 是 P0/P1 修复方案(`.claude/artifacts/p0-p1-fix-plan.md`)的第一批,共 4 项基础合规修复,无外部依赖,完成后解锁后续 B3(M1-b 安全/恢复)与 B4(M1-b 上下文/计费)。

4 项修复均源自验收报告(`.claude/artifacts/acceptance-report.md`)识别的缺口:
- **P0-8**:react_events.event_type CHECK 约束仅允许 6 种事件,但代码已 emit `sandbox_execution`/`memory_extracted`,后续 B3/B4 还需 `compress`/`token_usage`/`injection_alert`/`injection_blocked`。当前 emit 必然写入失败。
- **P1-2**:`setup_logger` 仅 StreamHandler,`config.yaml` 的 `file_path: "${WORKSPACE}/logs/agent.log"` 是死配置,M0-AC-5 要求"本地文件 + stdout 双通道"。
- **P1-10**:`api/eval.py` 的 `_build_default_adapter` 调用 `build_default_adapter`、`_build_hybrid_evaluator` 调用 `HybridEvaluator.from_cfg`,两者均不存在,真实调用必 ImportError;测试用 monkeypatch 规避。
- **P1-4**:`compute_frozen_hash` 已实现且 `replace_frozen_zone` 写入 `sessions.frozen_hash`,但无任何代码读取并比对,M1-AC-3 "hash 校验通过"(运行时验证)缺失。

## In scope

- **P0-8**:扩容 `react_events.event_type` CHECK 约束,新增 6 种事件类型(`sandbox_execution`/`memory_extracted`/`compress`/`token_usage`/`injection_alert`/`injection_blocked`/`tool_error`);`schema.sql` + `migrations.py` 幂等迁移函数
- **P1-2**:`setup_logger` 新增 `file_path` 可选参数,提供时创建 `FileHandler`(UTF-8,目录自动创建);`main.py` 启动入口读 `observability.logging.file_path` 配置并传入
- **P1-10**:在 `models/registry.py` 新增 `build_default_adapter(cfg)` 函数(返回 FallbackChain 首个 adapter);在 `eval/hybrid_eval.py` 新增 `HybridEvaluator.from_cfg(cls, cfg)` 类方法(用 `build_judge_adapter` + `load_judge_prompt` 构造 `LLMJudge`,再 `cls(judge=...)`);解除 `test_eval_api.py` 的 `_build_eval_runner` monkeypatch,改用真实路径 + mock 底层 adapter
- **P1-4**:`ensure_initial` 的 reload 路径增加 hash 校验(读 `sessions.frozen_hash` → reload frozen → `compute_frozen_hash()` 比对,不一致抛 `FrozenHashMismatchError`);`replace_frozen_zone` 写入后立即校验;新增 `FrozenHashMismatchError` 异常类(继承 `PrivateAgentError`);环境变量 `PA_FROZEN_HASH_VERIFY=0` 可关(默认开)

## Out of scope

- B2/B3/B4/B5/B6 的 14 项修复(各自独立 spec)
- 注入防护模块 `injection_guard.py`(B3/P0-2)
- checkpoint 模块 `checkpoint.py`(B3/P0-3)
- 上下文压缩 `Compressor` + `TokenEstimator`(B4/P0-1)
- token 计费 `BillingRecorder`(B4/P0-4)
- 沙箱 `ResourceLimiter`(B5/P0-7)
- RAG embedding/vector/HNSW 迁移(B6/P0-5)
- `react_loop.py` 每轮调 `verify_frozen_hash`(留 B3,与 checkpoint 集成时一起加)
- 日志轮转(RotatingFileHandler)— 蓝图 §9.13 未要求,留 V2
- `docs/adr/`(CHECK 扩容是幂等迁移标准操作,无 tradeoff,不写 ADR)
- `CONTEXT.md` 新增术语(`FrozenHashMismatchError` / `build_default_adapter` 是实现符号,非领域术语)

## Assumptions

- PostgreSQL 16 + pgvector 0.8.6 环境已就绪(验收报告 §0.1 确认 692 测试全绿)
- `p0-p1-fix-plan.md` 已用户审阅通过,B1 范围与方案无异议
- `event_type` 列当前为 `TEXT NOT NULL CHECK(...)`(非 VARCHAR(30)),CHECK 约束名为 `react_events_event_type_check`(PostgreSQL 默认命名)
- `FallbackChain` 内部属性为 `_adapters`(非 `_providers`),`build_default_adapter` 返回 `chain._adapters[0]`
- `HybridEvaluator.__init__` 签名为 `(*, judge: LLMJudge)`,`from_cfg` 需用 `build_judge_adapter` + `load_judge_prompt` 构造 `LLMJudge` 后传入
- `config.yaml` 的 `${WORKSPACE}` 变量已在 `loader.py` 展开,`file_path` 读取后需 `os.path.expandvars`
- `ensure_initial` 当前 reload 路径仅读 `messages` 表的 frozen 行,不读 `sessions.frozen_hash`;`sessions.frozen_hash` 可能为 NULL(老会话未写入)

## Solution

### P0-8 CHECK 扩容

1. 修改 `schema.sql` L79 的 CHECK 约束,扩容为 13 种事件类型(原 6 + 新 7:`sandbox_execution`/`memory_extracted`/`compress`/`token_usage`/`injection_alert`/`injection_blocked`/`tool_error`)
2. 在 `migrations.py` 新增 `migrate_react_events_event_type_check(conn)`:
   - 查 `pg_constraint` 检测当前 CHECK 是否已含 `sandbox_execution`
   - 若无,`ALTER TABLE react_events DROP CONSTRAINT react_events_event_type_check, ADD CONSTRAINT react_events_event_type_check CHECK (event_type IN (...))`
3. 在 `migrate_all` 末尾调用该函数

### P1-2 日志文件通道

1. `logging.py` `setup_logger` 新增 `file_path: str | None = None` 参数;若提供,`os.makedirs(parent, exist_ok=True)` 后创建 `FileHandler(file_path, encoding='utf-8')`,共用 `JsonFormatter`,标记 `_pa_json=True`
2. `main.py` 模块级 `_logger = setup_logger(...)` 改为延迟初始化:在 `run_sidecar` / `_on_startup` 读 `cfg["observability"]["logging"]["file_path"]`,`os.path.expandvars` 后传入 `setup_logger`

### P1-10 ImportError

1. `models/registry.py` 新增 `build_default_adapter(cfg) -> ModelAdapter`:`chain = build_fallback_chain(cfg); return chain._adapters[0] if chain._adapters else None`(空 chain 返回 None,api/eval.py 需处理 None)
2. `eval/hybrid_eval.py` 新增 `HybridEvaluator.from_cfg(cls, cfg)`:`adapter = build_judge_adapter(cfg); prompt = load_judge_prompt(cfg); return cls(judge=LLMJudge(adapter=adapter, prompt_template=prompt))`(adapter 为 None 时仍构造,LLMJudge 内部降级)
3. `test_eval_api.py::test_trigger_eval_run_endpoint`:移除 `_build_eval_runner` 的 monkeypatch,改用 `monkeypatch.setattr("private_agent.api.eval._build_default_adapter", lambda cfg: _MockAdapter())` + `monkeypatch.setattr("private_agent.api.eval._build_hybrid_evaluator", lambda cfg: _MockEvaluator())`(只 mock 底层依赖,走真实 `_build_eval_runner` 路径)

### P1-4 Frozen hash 校验

1. `errors.py` 新增 `FrozenHashMismatchError(PrivateAgentError)`
2. `context_manager.py` `ensure_initial` reload 路径:读 `sessions.frozen_hash`;若非 NULL,`compute_frozen_hash()` 比对,不一致抛 `FrozenHashMismatchError`;若 NULL,跳过校验(老会话兼容)
3. `context_manager.py` 新增 `verify_frozen_hash(conn) -> None` 方法:读 DB hash + 计算当前 hash + 比对(供 react_loop 每轮调用,本 spec 不集成,留 B3)
4. `replace_frozen_zone` 写入后:`computed = self.compute_frozen_hash()`;若 `computed != new_hash`(刚写入的值),抛 `FrozenHashMismatchError`(理论上不会触发,作为写入完整性兜底)
5. 环境变量 `PA_FROZEN_HASH_VERIFY`:默认 `"1"`(开启),`"0"` 时 `ensure_initial` / `verify_frozen_hash` / `replace_frozen_zone` 校验跳过

## Edge cases & risks

| Category | Notes |
|---|---|
| Boundary conditions | `sessions.frozen_hash` 为 NULL(老会话)→ 跳过校验,不抛错;`FallbackChain._adapters` 为空 → `build_default_adapter` 返回 None,`api/eval.py` 需处理;`file_path` 父目录不存在 → `os.makedirs` 创建 |
| Failure modes | CHECK 扩容迁移失败(pg_constraint 查询异常)→ 抛错,启动失败(硬约束);hash 校验失败 → `FrozenHashMismatchError` 冒泡到 `main.py` WS handler,返回 error event;FileHandler 创建失败(权限)→ 降级仅 stdout,日志 warning |
| Risks | `migrate_react_events_event_type_check` 幂等性(连续调用两次不报错);`test_eval_api.py` 解除 monkeypatch 后可能暴露其他隐藏依赖;`build_default_adapter` 返回 None 时 `EvalRunner` 是否能处理(需验证 runner.py 对 None adapter 的行为) |
| Mitigation | 迁移函数用 `pg_constraint` 检测避免重复 ALTER;`test_eval_api.py` 改造后跑全量测试;`build_default_adapter` 返回 None 时在 `_build_eval_runner` 加 `assert model_adapter is not None` 早失败 |

## Acceptance criteria

- AC-1 (P0-8): 执行 `migrate_all(conn)` 后,`INSERT INTO react_events (session_id, turn, event_type, payload) VALUES (1, 1, 'sandbox_execution', '{}')` 成功(不再违反 CHECK 约束)
- AC-2 (P0-8): `migrate_all(conn)` 连续调用两次均不报错(幂等)
- AC-3 (P0-8): `INSERT INTO react_events ... event_type='compress'` / `'token_usage'` / `'injection_alert'` / `'injection_blocked'` / `'memory_extracted'` / `'tool_error'` 均成功
- AC-4 (P1-2): `setup_logger("test", file_path=str(tmp_path/"agent.log"))` 后,`logger.info("hello")` → 文件存在且内容含 `"hello"` JSON 行
- AC-5 (P1-2): `setup_logger("test", file_path=str(tmp_path/"nonexistent"/"sub"/"agent.log"))` → 父目录自动创建,文件写入成功
- AC-6 (P1-2): `setup_logger("test")`(不传 file_path)→ 仅 StreamHandler,无 FileHandler(行为与现状等价)
- AC-7 (P1-2): `main.py` 启动后,`${WORKSPACE}/logs/agent.log` 文件存在且含 JSON 日志(集成测试或手动验证)
- AC-8 (P1-10): `from private_agent.models.registry import build_default_adapter` 不抛 ImportError;`build_default_adapter(cfg)` 返回非 None(配置含 enabled provider 时)
- AC-9 (P1-10): `from private_agent.eval.hybrid_eval import HybridEvaluator; HybridEvaluator.from_cfg(cfg)` 不抛 ImportError,返回 `HybridEvaluator` 实例
- AC-10 (P1-10): `test_eval_api.py::test_trigger_eval_run_endpoint` 移除 `_build_eval_runner` monkeypatch 后,用 mock 底层 adapter 走真实 `_build_eval_runner` 路径,测试通过
- AC-11 (P1-4): 手动修改 DB 中 `sessions.frozen_hash` 为错误值,`await cm.ensure_initial(conn)` 抛 `FrozenHashMismatchError`
- AC-12 (P1-4): `sessions.frozen_hash` 为正确值时,`await cm.ensure_initial(conn)` 正常返回(不抛错)
- AC-13 (P1-4): `sessions.frozen_hash` 为 NULL 时,`await cm.ensure_initial(conn)` 正常返回(老会话兼容,跳过校验)
- AC-14 (P1-4): `PA_FROZEN_HASH_VERIFY=0` 时,即使 `sessions.frozen_hash` 错误,`ensure_initial` 也不抛错(环境变量开关)
- AC-15 (P1-4): `cm.replace_frozen_zone(conn, system_prompt=..., tools=...)` 写入后,若 DB 中 `frozen_hash` 与 `compute_frozen_hash()` 不一致,抛 `FrozenHashMismatchError`(写入完整性兜底)
- AC-16 (闭环): 全量 `python -m pytest` 通过(692 现有 + B1 新增,无新增失败)

## Open questions

无(需求完全清晰,方案已在 p0-p1-fix-plan.md 确认)

## Core entities (ontology)

| Entity | Type | Key fields | Relationship |
|---|---|---|---|
| FrozenHashMismatchError | Exception | extends PrivateAgentError | P1-4 新增,context_manager 抛出 |
| build_default_adapter | Function | (cfg) -> ModelAdapter \| None | P1-10 新增,registry 模块 |
| HybridEvaluator.from_cfg | ClassMethod | (cfg) -> HybridEvaluator | P1-10 新增,复用 build_judge_adapter + load_judge_prompt |
| migrate_react_events_event_type_check | AsyncFunction | (conn) -> None | P0-8 新增,migrations 模块,幂等 |

## Interview metadata

- Mode: --quick(需求已清晰,p0-p1-fix-plan.md 已详述,代码现状已验证)
- Waves: 0(无需 grill,直接产出 spec)
- Final ambiguity: <10%(方案 + 现状均已确认)
- Status: PASSED

### Clarity breakdown

| Dimension | Score | Weight | Weighted |
|---|---|---|---|
| Goal | 1.0 | 0.40 | 0.40 |
| Scope | 1.0 | 0.25 | 0.25 |
| AC | 1.0 | 0.25 | 0.25 |
| Context | 1.0 | 0.10 | 0.10 |
| Ambiguity | | | 0% |
