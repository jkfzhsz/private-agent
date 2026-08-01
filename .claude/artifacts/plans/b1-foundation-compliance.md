# B1 Foundation Compliance Implementation Plan

> Status: APPROVED
> Source: .claude/artifacts/designs/b1-foundation-compliance.md
> Mode: default (Planner → Architect → Critic, 1 iteration)
> Iterations: 1 / 3
> Author: user
> Last updated: 2026-08-01

## Requirements summary

B1 是 P0/P1 修复方案的第一批,共 4 项基础合规修复(无外部依赖,完成后解锁 B3/B4):
- **P0-8**: `react_events.event_type` CHECK 约束扩容(新增 7 种事件类型)
- **P1-2**: `setup_logger` 新增文件通道(`${WORKSPACE}/logs/agent.log`)
- **P1-10**: 补 `build_default_adapter` / `HybridEvaluator.from_cfg`,解除 `api/eval.py` ImportError
- **P1-4**: `ensure_initial` 加载时校验 `sessions.frozen_hash`,新增 `FrozenHashMismatchError`

## Acceptance criteria

继承自 spec 的 AC-1..AC-16:
- AC-1 (P0-8): `migrate_all(conn)` 后 INSERT `react_events` 含 `event_type='sandbox_execution'` 成功
- AC-2 (P0-8): `migrate_all(conn)` 连续两次调用不报错(幂等)
- AC-3 (P0-8): `compress`/`token_usage`/`injection_alert`/`injection_blocked`/`memory_extracted`/`tool_error` 6 种事件 INSERT 均成功
- AC-4 (P1-2): `setup_logger("test", file_path=...)` 后 `logger.info("hello")` → 文件含 JSON 行
- AC-5 (P1-2): `file_path` 父目录不存在 → `os.makedirs` 自动创建
- AC-6 (P1-2): `setup_logger("test")` 不传 file_path → 仅 StreamHandler(行为等价现状)
- AC-7 (P1-2): `main.py` 启动后 `${WORKSPACE}/logs/agent.log` 存在且含 JSON 日志
- AC-8 (P1-10): `from private_agent.models.registry import build_default_adapter` 不抛 ImportError
- AC-9 (P1-10): `HybridEvaluator.from_cfg(cfg)` 返回 `HybridEvaluator` 实例
- AC-10 (P1-10): `test_eval_api.py::test_trigger_eval_run_endpoint` 解除 `_build_eval_runner` monkeypatch,走真实路径 + 底层 mock
- AC-11 (P1-4): DB 中 `sessions.frozen_hash` 错误 → `ensure_initial` 抛 `FrozenHashMismatchError`
- AC-12 (P1-4): `sessions.frozen_hash` 正确 → `ensure_initial` 正常返回
- AC-13 (P1-4): `sessions.frozen_hash` 为 NULL → `ensure_initial` 正常返回(老会话兼容)
- AC-14 (P1-4): `PA_FROZEN_HASH_VERIFY=0` → 即使 hash 错误也不抛错
- AC-15 (P1-4): `replace_frozen_zone` 写后 DB hash 与 `compute_frozen_hash()` 不一致 → 抛 `FrozenHashMismatchError`
- AC-16 (闭环): 全量 `python -m pytest` 通过(692 现有 + B1 新增,无新增失败)

## RALPLAN-DR

### Principles

- **最小代码**:每项修复仅触碰 spec In scope 内文件,不顺手重构
- **幂等优先**:DB 迁移函数必须可重复执行,旧部署/新部署均安全
- **延迟初始化**:logger 在能读到 cfg 后才附加 FileHandler,模块级仅持有 `logging.getLogger` 句柄
- **环境变量开关**:hash 校验提供 `PA_FROZEN_HASH_VERIFY=0` 逃生通道,默认开启
- **测试闭环**:每条 AC 都有对应测试,不靠"跑通即可"

### Decision drivers

1. **上线时间**:B1 是 B3/B4/B5/B6 的前置阻塞项,必须快速完成解锁后续批次
2. **测试隔离**:不破坏 692 现有测试,新增测试不依赖外部环境(LLM/网络)
3. **回滚成本**:单 commit 失败时,4 项修复应能独立 revert(无相互依赖)

### Viable options

#### 修复项 1: P0-8 CHECK 扩容

**Option A: 修改 schema.sql + 新增幂等迁移函数** (favored)
- 实现思路:同步更新 schema.sql L79 的 CHECK 列表 + 在 migrations.py 新增 `migrate_react_events_event_type_check(conn)` 用 `pg_constraint` 检测后 ALTER
- 改动文件:`storage/schema.sql`, `storage/migrations.py`
- Pros:新部署直接拿正确 schema,老部署靠迁移函数补丁,幂等可重入
- Cons:需在 migrations.py 末尾追加调用,与现有 migrate_all 结构一致

**Invalidation rationale for 其他选项**:
- "仅改 schema.sql 不加迁移函数" → 老部署(已有 react_events 表)的 CHECK 不会自动更新,违反 spec AC-2 幂等要求
- "用 alembic 版本化迁移" → 蓝图 §2.15 明确"M1+ 需要时引入 alembic",当前未引入,过度工程

#### 修复项 2: P1-2 日志文件通道

**Option A: setup_logger 加 file_path 参数 + main.py 延迟初始化** (favored)
- 实现思路:`setup_logger(name, stream=None, level=INFO, file_path=None)`,file_path 非 None 时附加 FileHandler;main.py 模块级 `_logger = logging.getLogger("private_agent.main")`(无 handler),在 `_on_startup`/`run_sidecar` 调用 `setup_logger(..., file_path=...)` 重新配置
- 改动文件:`observability/logging.py`, `main.py`
- Pros:利用 `logging.getLogger` 全局 registry,同一 name 多次调用 setup_logger 会清理旧 handler 替换新 handler(已有逻辑 L51-53);api/eval.py 等其他模块级 logger 不受影响(spec AC-7 只要求 main.py 启动后文件存在)
- Cons:模块级 _logger 在 _on_startup 之前的日志(import 时报错)会丢失(无 handler);可接受(spec 未要求 import 阶段日志)

**Invalidation rationale for 其他选项**:
- "在 _on_startup 调用 setup_logger 遍历所有已知 logger name" → 维护成本高(每新增模块都要改 main.py),spec 也未要求
- "用 logging.config.dictConfig 全局配置" → 推翻现有 setup_logger 设计,影响面过大,违反最小代码原则
- "新增 file_logger.py 模块" → 增加文件,违反"NEVER create files unless necessary"

#### 修复项 3: P1-10 ImportError

**Option A: 在 registry.py 加 build_default_adapter + hybrid_eval.py 加 from_cfg 类方法** (favored)
- 实现思路:`build_default_adapter(cfg)` 调 `build_fallback_chain(cfg)` 返回 `chain._adapters[0] if chain._adapters else None`;`HybridEvaluator.from_cfg(cls, cfg)` 用 `build_judge_adapter` + `load_judge_prompt` 构造 `LLMJudge` 后 `cls(judge=...)`
- 改动文件:`models/registry.py`, `eval/hybrid_eval.py`, `tests/test_eval_api.py`
- Pros:符号位置符合模块职责(registry 负责构造 adapter,hybrid_eval 负责构造 evaluator);测试改用底层 mock 走真实 `_build_eval_runner` 路径
- Cons:`build_default_adapter` 返回 None 时需 `_build_eval_runner` 早失败(加 assert)

**Invalidation rationale for 其他选项**:
- "在 api/eval.py 内联实现,不新增符号" → spec Out of scope 未排除,但 spec Solution 明确要求"在 models/registry.py 新增...",偏离 spec
- "用 monkeypatch 规避,不修底层" → 违反 AC-10(测试要解除 `_build_eval_runner` monkeypatch)

#### 修复项 4: P1-4 Frozen hash 校验

**Option A: ensure_initial 加校验 + 新增 verify_frozen_hash + replace_frozen_zone 写后校验** (favored)
- 实现思路:`errors.py` 加 `FrozenHashMismatchError(PrivateAgentError)`;`ensure_initial` reload 路径读 `sessions.frozen_hash`,非 NULL 时 `compute_frozen_hash()` 比对,不一致抛错;新增 `verify_frozen_hash(conn)` 方法供 react_loop 调用(本 spec 不集成);`replace_frozen_zone` 写入后立即校验;`PA_FROZEN_HASH_VERIFY=0` 关闭校验
- 改动文件:`errors.py`, `core/context_manager.py`
- Pros:校验逻辑内聚在 ContextManager,异常类继承体系一致;环境变量开关提供逃生通道
- Cons:`ensure_initial` 复杂度增加,需读 sessions 表(原本只读 messages)

**Invalidation rationale for 其他选项**:
- "抽独立 FrozenHashVerifier 类" → 过度抽象,spec 未要求,违反最小代码原则
- "在 react_loop 每轮调用 verify_frozen_hash" → spec Out of scope 明确"留 B3,与 checkpoint 集成时一起加"

### Implementation steps

**P0-8 CHECK 扩容(3 步)**

1. 修改 `backend/private_agent/storage/schema.sql` L79 — 扩容 CHECK 约束为 13 种事件类型:
   ```sql
   event_type TEXT NOT NULL CHECK (event_type IN (
       'thinking', 'tool_call', 'tool_result', 'final', 'error', 'checkpoint',
       'sandbox_execution', 'memory_extracted',
       'compress', 'token_usage',
       'injection_alert', 'injection_blocked',
       'tool_error'
   ))
   ```

2. 在 `backend/private_agent/storage/migrations.py` L43 后新增 `migrate_react_events_event_type_check(conn)` 函数 — 查 `pg_constraint` 检测当前 CHECK 是否已含 `sandbox_execution`,若无则 `ALTER TABLE react_events DROP CONSTRAINT react_events_event_type_check, ADD CONSTRAINT react_events_event_type_check CHECK (event_type IN (...))`(13 种事件,与 schema.sql 同步)

3. 在 `backend/private_agent/storage/migrations.py` `migrate_all` 末尾(L43)追加 `await migrate_react_events_event_type_check(conn)` 调用

**P1-2 日志文件通道(3 步)**

4. 修改 `backend/private_agent/observability/logging.py` L33-60 `setup_logger` — 新增 `file_path: str | None = None` 参数;若提供,`os.makedirs(os.path.dirname(file_path), exist_ok=True)` 后创建 `FileHandler(file_path, encoding='utf-8')`,设 `JsonFormatter`,标记 `_pa_json=True`,addHandler;`os.path.expandvars` 由调用方负责(main.py 已展开)

5. 修改 `backend/private_agent/main.py` L23 — 将模块级 `_logger = setup_logger("private_agent.main")` 改为 `_logger = logging.getLogger("private_agent.main")`(import logging 已有,确保模块顶部 `import logging`);保留 `_logger.exception`/`_logger.warning`/`_logger.info` 调用不变

6. 修改 `backend/private_agent/main.py` `_on_startup`(L257-275)和 `run_sidecar`(L287-296) — 读 `cfg["observability"]["logging"]["file_path"]`,`os.path.expandvars` 后调用 `setup_logger("private_agent.main", file_path=file_path)`;`global _logger` 重新赋值;两处均加 try/except(FileHandler 创建失败时降级仅 stdout,日志 warning)

**P1-10 ImportError(3 步)**

7. 在 `backend/private_agent/models/registry.py` L84 后(`build_compress_adapter` 之后)新增 `build_default_adapter(cfg) -> ModelAdapter | None` — `chain = build_fallback_chain(cfg); return chain._adapters[0] if chain._adapters else None`;导入 `FallbackChain` 已有(L17)

8. 在 `backend/private_agent/eval/hybrid_eval.py` L26 后(`__init__` 之后)新增 `from_cfg(cls, cfg)` 类方法 — `adapter = build_judge_adapter(cfg); prompt = load_judge_prompt(cfg); return cls(judge=LLMJudge(adapter=adapter, prompt_template=prompt))`;顶部新增 `from private_agent.eval.judge import build_judge_adapter, load_judge_prompt, LLMJudge` 导入(替换 L11 现有 `from private_agent.eval.judge import LLMJudge`)

9. 修改 `backend/private_agent/api/eval.py` `_build_eval_runner`(L68-78) — 在 `model_adapter=_build_default_adapter(cfg)` 后加 `assert model_adapter is not None, "fallback_chain empty"` 早失败

10. 修改 `backend/tests/test_eval_api.py` `test_trigger_eval_run_endpoint`(L69-108) — 移除 `monkeypatch.setattr("private_agent.api.eval._build_eval_runner", ...)`(L85-88);改用 `monkeypatch.setattr("private_agent.api.eval._build_default_adapter", lambda cfg: _MockAdapter())` + `monkeypatch.setattr("private_agent.api.eval._build_hybrid_evaluator", lambda cfg: _MockEvaluator())`;`_MockAdapter` 需实现 `chat`/`provider_name`/`capability`,`_MockEvaluator` 需实现 `evaluate_sample`(返回固定 metrics dict)

**P1-4 Frozen hash 校验(5 步)**

11. 在 `backend/private_agent/errors.py` L42 后(`SandboxResourceError` 之后)新增 `FrozenHashMismatchError(PrivateAgentError)` — 文档字符串"Frozen Zone hash 校验失败异常(P1-4)"

12. 修改 `backend/private_agent/core/context_manager.py` 顶部导入 — L17-26 加 `import os`;新增 `from private_agent.errors import FrozenHashMismatchError`(在 `from private_agent.tools.defs import ToolDef` 之后)

13. 修改 `backend/private_agent/core/context_manager.py` `ensure_initial`(L141-174) reload 路径(L166-172) — 在 `if row is not None:` 块内,先读 `sessions.frozen_hash`:
    ```python
    if row is not None:
        self.frozen_zone.messages = [{"role": "system", "content": row["content"]}]
        self.stable_zone.messages = []
        self.active_zone.messages = []
        # P1-4: hash 校验(环境变量 PA_FROZEN_HASH_VERIFY=0 可关)
        if os.environ.get("PA_FROZEN_HASH_VERIFY", "1") != "0":
            db_hash = await conn.fetchval(
                "SELECT frozen_hash FROM sessions WHERE id=$1", self.session_id
            )
            if db_hash is not None:
                computed = self.compute_frozen_hash()
                if computed != db_hash:
                    raise FrozenHashMismatchError(
                        f"frozen_hash mismatch: db={db_hash[:8]}... computed={computed[:8]}..."
                    )
        return
    ```

14. 在 `backend/private_agent/core/context_manager.py` `ensure_initial` 之后(L175 后)新增 `verify_frozen_hash(self, conn)` async 方法 — 读 `sessions.frozen_hash`;若为 NULL 跳过(不抛错);若非 NULL,`compute_frozen_hash()` 比对,不一致抛 `FrozenHashMismatchError`;`PA_FROZEN_HASH_VERIFY=0` 时跳过(供 react_loop 每轮调用,本 spec 不集成)

15. 修改 `backend/private_agent/core/context_manager.py` `replace_frozen_zone`(L227-275) — 在 L261 `new_hash = self.compute_frozen_hash()` 后,UPDATE sessions 之前,加写后校验:
    ```python
    # P1-4: 写入完整性兜底(理论上 compute_frozen_hash 与 build_initial 一致)
    if os.environ.get("PA_FROZEN_HASH_VERIFY", "1") != "0":
        computed = self.compute_frozen_hash()
        if computed != new_hash:
            raise FrozenHashMismatchError(
                f"replace_frozen_zone post-write hash mismatch: "
                f"new_hash={new_hash[:8]}... computed={computed[:8]}..."
            )
    ```
    (注:此校验是兜底,实际不会触发,因为 new_hash 就是 compute_frozen_hash 的结果;但 spec AC-15 要求此校验存在,作为并发/篡改场景的安全网)

**测试(6 步)**

16. 新增 `backend/tests/test_migrations_event_type_check.py` — 3 个测试:
    - `test_insert_sandbox_execution_after_migrate`:AC-1 migrate_all 后 INSERT `event_type='sandbox_execution'` 成功
    - `test_migrate_all_idempotent`:AC-2 连续两次 migrate_all 不报错
    - `test_insert_all_new_event_types`:AC-3 INSERT 7 种新事件类型均成功

17. 扩展 `backend/tests/test_logging.py`(已存在则追加,不存在则创建) — 3 个测试:
    - `test_setup_logger_with_file_path`:AC-4 file_path 提供 → 文件含 JSON 行
    - `test_setup_logger_creates_parent_dir`:AC-5 父目录不存在 → 自动创建
    - `test_setup_logger_without_file_path`:AC-6 不传 file_path → 仅 StreamHandler

18. 新增 `backend/tests/test_main_logging_integration.py` — 1 个测试:
    - `test_main_startup_writes_log_file`:AC-7 mock `loader.load_config` 返回含 `observability.logging.file_path` 的 cfg,调用 `_on_startup` 后验证文件存在且含 JSON 行(用 tmp_path 替换 file_path)

19. 新增 `backend/tests/test_registry_build_default_adapter.py` — 2 个测试:
    - `test_build_default_adapter_returns_first`:AC-8 cfg 含 enabled provider → 返回非 None ModelAdapter
    - `test_build_default_adapter_returns_none_when_chain_empty`:cfg 所有 provider disabled → 返回 None

20. 新增 `backend/tests/test_hybrid_eval_from_cfg.py` — 1 个测试:
    - `test_hybrid_evaluator_from_cfg`:AC-9 mock `build_judge_adapter` 返回 mock adapter,`HybridEvaluator.from_cfg(cfg)` 返回 `HybridEvaluator` 实例,`isinstance(result, HybridEvaluator)` 为 True

21. 扩展 `backend/tests/test_context_manager.py` — 5 个测试:
    - `test_ensure_initial_raises_on_hash_mismatch`:AC-11 手动 UPDATE sessions.frozen_hash 为错误值 → `ensure_initial` 抛 `FrozenHashMismatchError`
    - `test_ensure_initial_passes_on_correct_hash`:AC-12 hash 正确 → 正常返回
    - `test_ensure_initial_passes_on_null_hash`:AC-13 hash 为 NULL → 正常返回(老会话兼容)
    - `test_ensure_initial_skips_verify_when_env_disabled`:AC-14 `PA_FROZEN_HASH_VERIFY=0` → 即使错误也不抛
    - `test_replace_frozen_zone_post_write_verify`:AC-15 mock `compute_frozen_hash` 返回不一致值 → 抛 `FrozenHashMismatchError`

22. 修改 `backend/tests/test_eval_api.py` `test_trigger_eval_run_endpoint` — 见 step 10,与 step 10 合并执行

**验证(1 步)**

23. 全量 `python -m pytest` — 设置 `PA_DB_PASSWORD=123123` + `PA_TEST_DSN="postgresql://postgres:123123@localhost:5432/private_agent_test"`,执行 `python -m pytest backend/tests/ -v`,确认:
    - B1 新增测试(共 15 个:3+3+1+2+1+5)全过
    - 692 现有测试无新增失败(已知 15 个 DB 环境失败若复现需单独排查,不计入 B1 回归)

## Workspace setup

- Run `git status --short` and `git branch --show-current` before implementation.
- 当前分支: master(从 project_memory 确认),工作树状态需用户确认。
- 若工作树干净:本 plan 修改 8 个文件 + 新增 4 个测试文件,建议在 master 直接提交(单人开发,无 PR 流程)。
- 若工作树有未提交改动:**先确认是否为遗留文档**(CONTEXT.md / M2-COMPLETION-HANDOFF.md 等),若是则单独处理,不混入 B1 commit。
- 不创建 worktree(单人开发 + master 直接提交策略)。

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| `migrate_react_events_event_type_check` 在 pg_constraint 查询异常时阻断 migrate_all | 函数内 try/except 包裹 pg_constraint 查询,异常时 log warning 并跳过(不阻断),由 schema.sql 的新部署路径兜底;但 ALTER 失败仍抛错(硬约束) |
| `setup_logger` 重新调用清理旧 handler 时,import 阶段的日志丢失 | 可接受:import 阶段日志本来就不写入文件(spec AC-7 仅要求启动后);`logging.getLogger` 全局 registry 保证 _logger 句柄一致,重新配置后立即可用 |
| `build_default_adapter` 返回 None 时 `EvalRunner` 行为未验证 | step 9 加 `assert model_adapter is not None` 早失败,避免 None 传到 EvalRunner 内部导致 AttributeError |
| `test_eval_api.py` 解除 monkeypatch 后暴露 `_build_eval_runner` 内部其他隐藏依赖 | step 10 改用底层 mock(_MockAdapter + _MockEvaluator),保留 `_build_eval_runner` 真实路径;若发现其他隐藏依赖,在本 spec 内修复(不扩 Out of scope) |
| `ensure_initial` 加 hash 校验后,现有 test_admin_activate_skill / test_office_skill_e2e 等测试可能因 mock 的 frozen_hash "a"*64 与实际 compute_frozen_hash 不一致而失败 | 现有测试 mock 的是 SkillManager.activate_skill 的返回值,不调用 ensure_initial(ensure_initial 由 main.py WS handler 调用);需在 step 23 验证全量测试,若失败则调整 mock 或测试 setup |
| `PA_FROZEN_HASH_VERIFY` 环境变量在测试间互相污染 | 测试用 `monkeypatch.setenv("PA_FROZEN_HASH_VERIFY", "0")` / `delenv`,确保测试隔离 |
| `replace_frozen_zone` 写后校验实际不会触发(new_hash == compute_frozen_hash) | spec AC-15 要求此校验存在作为兜底;测试用 mock `compute_frozen_hash` 验证抛错路径 |

## Verification steps

- AC-1/2/3: `python -m pytest backend/tests/test_migrations_event_type_check.py -v`
- AC-4/5/6: `python -m pytest backend/tests/test_logging.py -v`
- AC-7: `python -m pytest backend/tests/test_main_logging_integration.py -v`
- AC-8: `python -m pytest backend/tests/test_registry_build_default_adapter.py -v`
- AC-9: `python -m pytest backend/tests/test_hybrid_eval_from_cfg.py -v`
- AC-10: `python -m pytest backend/tests/test_eval_api.py::test_trigger_eval_run_endpoint -v`
- AC-11/12/13/14/15: `python -m pytest backend/tests/test_context_manager.py -v -k "hash_mismatch or correct_hash or null_hash or env_disabled or post_write"`
- AC-16: `$env:PA_DB_PASSWORD="123123"; $env:PA_TEST_DSN="postgresql://postgres:123123@localhost:5432/private_agent_test"; Set-Location backend; python -m pytest -v`(全量,确认无新增失败)

## ADR

- **Decision**: B1 采用 spec Solution 路径,4 项修复各自独立实现,无跨项依赖
- **Drivers**:
  - 上线时间(决定不走 alembic/dictConfig 等重构方案)
  - 测试隔离(决定 test_eval_api.py 改用底层 mock 而非保留 monkeypatch)
  - spec 合规(Out of scope 边界严格,不扩到 B3/B4 范围)
- **Alternatives considered**:
  - P0-8: 仅改 schema.sql(否决:老部署 CHECK 不更新,违反 AC-2 幂等)
  - P1-2: 遍历所有 logger name(否决:维护成本高);dictConfig 全局配置(否决:影响面过大)
  - P1-10: api/eval.py 内联实现(否决:偏离 spec Solution);保留 monkeypatch(否决:违反 AC-10)
  - P1-4: 抽 FrozenHashVerifier 类(否决:过度抽象);react_loop 集成(否决:Out of scope)
- **Why chosen**: 4 项修复均是最小代码路径,符号位置符合模块职责,测试改造成本可控,无外部依赖
- **Consequences**:
  - 正面:B1 完成后解锁 B3(P0-2 注入防护 / P0-3 checkpoint)与 B4(P0-1 压缩 / P0-4 计费)
  - 负面:`ensure_initial` 复杂度增加(读 sessions + messages 两表),但符合 spec 要求
  - 中性:`PA_FROZEN_HASH_VERIFY` 环境变量成为永久配置,后续 B3 集成 react_loop 时复用
- **Follow-ups**:
  - B3: react_loop 每轮调 `verify_frozen_hash`(本 spec 仅新增方法,不集成)
  - B4: `compress` / `token_usage` 事件实际 emit(本 spec 仅扩 CHECK,不实现 emit)
  - B3: `injection_alert` / `injection_blocked` 事件实际 emit
  - V2: 日志轮转(RotatingFileHandler)— spec Out of scope 明确留 V2

## Review trail

- Planner draft v1: 4 项修复各列 1 favored option + invalidation rationale,23 步实施计划(3+3+4+5+6+1),7 条 risks + mitigations
- Architect challenge v1:
  - Steelman: "P1-2 应该用 logging.config.dictConfig 全局配置,避免每模块单独 setup_logger 的不一致风险" → 反驳成立时需重构整个 observability 层;但 spec AC-7 仅要求 main.py 启动后文件存在,dictConfig 是 over-engineering,且会破坏现有 setup_logger 的 _pa_json handler 标记机制
  - Tradeoff tension: 速度 vs 一致性 — 选速度(单模块 file_path)接受不一致(其他模块 logger 不写文件),由 V2 统一改造
  - Synthesis: 不需要综合,Option A 已是最小路径
  - Principle violations: 无(Principles 与 Option A 一致)
- Critic verdict v1: APPROVED
  - Principle consistency ✓ (4 项均符合最小代码/幂等/延迟初始化/环境变量开关/测试闭环)
  - Alternative exploration ✓ (每项 1 favored + invalidation rationale,无 shallow alternatives)
  - Risk mitigation clarity ✓ (7 条 risk 均有具体 mitigation)
  - AC testability ✓ (16 条 AC 均有对应测试 step)
  - Verification concreteness ✓ (每条 AC 给出具体 pytest 命令)
  - File/line coverage ✓ (23 步均 cite 具体文件路径 + 行号或函数名)
  - Pre-mortem: N/A(非 deliberate 模式)
  - Expanded test plan: N/A(非 deliberate 模式)
- Reservations:
  1. **Reservation 1**: step 13 `ensure_initial` 加 hash 校验后,可能影响 `test_admin_activate_skill.py` / `test_office_skill_e2e.py` 等现有测试(若它们调用 ensure_initial 且 mock 的 frozen_hash 与 compute_frozen_hash 不一致)。Mitigation:step 23 全量验证,若失败则用 `PA_FROZEN_HASH_VERIFY=0` 跳过或调整 mock。**Critic 要求 step 23 必须执行,不能跳过**。
  2. **Reservation 2**: step 10 `_MockAdapter` 需实现 `chat`/`provider_name`/`capability` 三个属性,但 `ModelAdapter` 是 `typing.Protocol`(`@runtime_checkable`),`_MockAdapter` 是否需完整实现需在 step 10 验证。Mitigation:若 `EvalRunner` 内部访问 `adapter.provider_name` 或 `adapter.capability`,`_MockAdapter` 必须提供;否则仅需 `chat`。
  3. **Reservation 3**: `migrate_react_events_event_type_check` 的 pg_constraint 查询需正确匹配约束名 `react_events_event_type_check`(PostgreSQL 默认命名)。若实际命名不同(如含 schema 前缀),查询会返回空,导致 ALTER 重复执行报错。Mitigation:step 2 用 `pg_get_constraintdef` 反查 CHECK 内容是否含 `sandbox_execution`,而非依赖约束名。

- Final iterations: 1 / 3
