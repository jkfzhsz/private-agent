# m4-eval-foundation Implementation Plan

> Status: APPROVED
> Source: `.claude/artifacts/designs/m4-eval-foundation.md`
> Mode: (default) — Planner → Architect → Critic loop
> Iterations: 2 / 3
> Author: user
> Last updated: 2026-08-01

## Requirements summary

M4 评估闭环底层基础设施:为 eval_datasets 表补 `split` 列 + CHECK 约束、建立 Pydantic 校验模型(EvalSample/ExpectedTrace/ExpectedToolCall)、三个仓储层(EvalDatasetRepo/EvalRunRepo/VersionSnapshotRepo)、ExampleLoader.load_test_set() 扩展、3 场景各 4 条种子测试样本(共 12 条)、judge_prompts/general.md 通用模板、eval_runs TTL 清理。为后续 4 个 M4 spec 提供数据契约。

## Acceptance criteria

(从 spec 继承,10 条 AC)

- AC-1: `eval_datasets` 表含 `split VARCHAR(10) NOT NULL CHECK (split IN ('train','test'))` 列,migrations.migrate_all() 幂等执行成功
- AC-2: `EvalSample` Pydantic 模型校验非法 expected_react_trace(缺 tool_calls / tool_calls 非数组 / expected_output_contains 非数组)时抛 `InvalidSampleFormatError`
- AC-3: `EvalDatasetRepo.insert()` 入库前调 `validate_expected_trace`,非法样本不入库
- AC-4: `EvalDatasetRepo.load_test_set(scenario, skill_version)` 返回 `list[EvalSample]`,空表返回空列表
- AC-5: `EvalRunRepo.create_run()` 返回 `run_id: str`,eval_runs 表记录 status='running' + mock_enabled 字段
- AC-6: `VersionSnapshotRepo.save(scope, version, payload)` + `get(scope, version)` 读写一致
- AC-7: `ExampleLoader.load_test_set(skill_name)` 加载 `examples/test/*.json` 并解析为 `EvalSample` 列表
- AC-8: 3 场景各创建 4 条测试样本(共 12 条),含 normal/boundary/error 三类 case_type
- AC-9: `backend/config/judge_prompts/general.md` 存在且含 `{user_input}`/`{agent_response}`/`{expected_output}` 模板变量
- AC-10: `ttl_cleanup.cleanup_old_eval_runs(conn, keep_recent=100)` 删除超过 100 条的历史记录,返回删除数

## RALPLAN-DR

### Principles

1. **最小代码** — 仓储层复用 memories_repo/kb_repo 模式(asyncpg 直连 + JSONB 序列化),不引入 ORM 或新抽象层
2. **外科手术式改动** — 仅在 migrations.py 末尾追加 ALTER,不改现有 migrate_all 逻辑;ExampleLoader 仅新增 load_test_set 方法,不动 load()
3. **跟随 spec 的 In scope** — 不实现指标计算器/评估执行器(后续 spec 范围)
4. **可验证成功标准** — 每个 AC 对应一个 pytest 测试用例,先红后绿
5. **不假设** — mock_mode/mock_enabled 字段冗余显式标注,Repo 层明确只用 mock_enabled

### Decision drivers

1. **与现有仓储层一致性**(最高权重)— memories_repo/kb_repo 用 asyncpg + dataclass/Pydantic 模型,新 Repo 必须跟随此模式,降低团队认知成本
2. **spec In scope 边界严格性** — 后续 4 个 spec 依赖本 spec 的仓储层接口,接口设计必须在 dev-plan 阶段锁定
3. **种子样本可维护性** — 12 条 JSON 样本需手工编写,结构必须与 Pydantic 模型 + DB schema 完全对齐,避免后续返工
4. **migration 幂等性** — 现有 migrate_all 已用 `ADD COLUMN IF NOT EXISTS` 模式(M3 sessions 锁定列),本 spec 必须延续

### Viable options

**Option A: 单文件仓储层 + JSONB 原生序列化(Planner favored)**
- 实现思路:`eval/repos.py` 单文件含三个 Repo 类,Pydantic 模型用 `.model_dump_json()` 序列化为 JSONB,查询时 `json.loads` 反序列化。与 memories_repo(dataclass)+ kb_repo(dataclass)风格略不同,但与 M3 skills/manager.py 已用的 Pydantic 风格一致
- 改动文件:
  - 新建 `backend/private_agent/eval/models.py`
  - 新建 `backend/private_agent/eval/repos.py`
  - 扩展 `backend/private_agent/storage/migrations.py:26-34`(末尾追加)
  - 扩展 `backend/private_agent/skills/example_loader.py:33-64`(新增方法)
  - 扩展 `backend/private_agent/storage/ttl_cleanup.py:62-87`(新增函数)
  - 新建 `backend/config/judge_prompts/general.md`
  - 新建 12 个 JSON 样本文件 `backend/skills/{office,data_analysis,frontend_design}/examples/test/*.json`
- Pros: 与 spec 设计完全对齐;Pydantic 原生 JSON 序列化减少手写 SQL 转换;eval_datasets 表已有 JSONB CHECK 约束可复用
- Cons: 与 memories_repo(dataclass)风格不完全一致,团队需适应两种模型风格

**Option B: dataclass 模型 + 手写 JSON 序列化**
- 实现思路:用 dataclass 替代 Pydantic(与 memories_repo 一致),手写 `to_json()` / `from_json()` 方法。仓储层完全跟随 memories_repo 模式
- 改动文件:同 Option A,但 models.py 用 dataclass,repos.py 手写序列化
- Pros: 与 memories_repo/kb_repo 风格 100% 一致
- Cons: 手写序列化代码量多 30%;spec 明确要求"Pydantic 入库前校验"(AC-2/AC-3),dataclass 无法满足,需额外引入 Pydantic 做校验,反而增加复杂度;蓝图 §8.4 明确要求 Pydantic 校验

**Option C: Pydantic + 分文件仓储层**
- 实现思路:每个 Repo 独立文件(eval/dataset_repo.py / eval_run_repo.py / version_snapshot_repo.py)
- 改动文件:同 Option A 但拆 3 个 Repo 文件
- Pros: 单文件更小,职责分离
- Cons: 3 个 Repo 共享 EvalSample 模型和连接管理,拆分后需重复 import;spec 明确写"新建 `backend/private_agent/eval/repos.py` 含三个 Repo 类";违反最小代码原则(单调用点不需要分文件)

### Invalidation rationale

- Option B 被否决:spec AC-2/AC-3 明确要求 Pydantic 校验,dataclass 方案需额外引入 Pydantic,复杂度反增
- Option C 被否决:违反 spec 明确的"新建 repos.py 含三个 Repo 类"指示,且 3 个 Repo 共享模型无拆分必要

---

## Implementation steps

(基于 Option A,共 18 步,全部 cite 具体文件路径/行号)

### 阶段 1:DB schema + migration(AC-1)

1. **扩展 schema.sql** — `backend/private_agent/storage/schema.sql:159-178` 在 eval_datasets 表 DDL 中追加 `split VARCHAR(10) NOT NULL DEFAULT 'test' CHECK (split IN ('train', 'test'))` 列(在 `expected_output` 之后,`created_at` 之前)
2. **扩展 migrations.py** — `backend/private_agent/storage/migrations.py:26-34` 在 migrate_all 末尾追加 `await conn.execute("ALTER TABLE eval_datasets ADD COLUMN IF NOT EXISTS split VARCHAR(10) NOT NULL DEFAULT 'test' CHECK (split IN ('train', 'test'))")`(幂等,老部署补列)

### 阶段 2:Pydantic 校验模型(AC-2)

3. **新建 eval/models.py** — `backend/private_agent/eval/models.py` 新建文件,定义 `ExpectedToolCall(BaseModel)`(tool:str, args:dict={}, expected_result_type:str|None=None)、`ExpectedTrace(BaseModel)`(tool_calls:list[ExpectedToolCall], expected_output_contains:list[str])、`EvalSample(BaseModel)`(sample_id, scenario, skill_name, skill_version, case_type:Literal["normal","boundary","error"], difficulty:Literal["easy","medium","hard"], split:Literal["train","test"], input, expected_react_trace:ExpectedTrace, expected_output:str|None)、`InvalidSampleFormatError(Exception)`、`validate_expected_trace(trace:dict)->ExpectedTrace`(失败抛 InvalidSampleFormatError)

### 阶段 3:仓储层(AC-3, AC-4, AC-5, AC-6)

4. **新建 EvalDatasetRepo** — `backend/private_agent/eval/repos.py` 新建文件,实现 `EvalDatasetRepo.__init__(self, conn)`、`async insert(self, sample: EvalSample) -> int`(入库前调 validate_expected_trace,SQL: INSERT INTO eval_datasets (...) VALUES (...) RETURNING id,JSONB 字段用 sample.expected_react_trace.model_dump_json())、`async load_test_set(self, scenario: str, skill_version: str) -> list[EvalSample]`(SELECT ... WHERE scenario=$1 AND skill_version=$2 AND split='test',反序列化 JSONB)、`async load_by_split(self, scenario: str, split: str) -> list[EvalSample]`、`async get_by_sample_id(self, sample_id: str) -> EvalSample | None`
5. **新建 EvalRunRepo** — `backend/private_agent/eval/repos.py` 追加 `EvalRunRepo` 类,实现 `__init__`、`async create_run(self, *, skill_name, skill_version, model_id, dataset_version, eval_mode, mock_enabled) -> str`(INSERT INTO eval_runs (...) RETURNING run_id,默认 status 用 started_at 隐含 running 状态——eval_runs 表无 status 字段,running 状态用 finished_at IS NULL 判断)、`async update_run_metrics(self, run_id, metrics: dict, sample_results: list[dict])`、`async complete_run(self, run_id)`(UPDATE ... SET finished_at=now())、`async fail_run(self, run_id, error)`(UPDATE ... SET finished_at=now(), metrics=jsonb_set(...,'error',...))、`async list_runs(self, *, skill_version=None, model_id=None, status=None, limit=20)`(status 参数映射:running→finished_at IS NULL,completed→finished_at IS NOT NULL)、`async get_run(self, run_id) -> dict | None`、`async get_low_score_samples(self, threshold=0.6, limit=50) -> list[dict]`(§8.16 复用,查询 sample_results JSONB 中 task_completion.completion_rate < threshold)
6. **新建 VersionSnapshotRepo** — `backend/private_agent/eval/repos.py` 追加 `VersionSnapshotRepo` 类,实现 `__init__`、`async save(self, *, scope, version, payload: dict) -> int`(INSERT INTO version_snapshots (scope, version, payload) VALUES (...) ON CONFLICT (scope, version) DO UPDATE SET payload=EXCLUDED.payload RETURNING id)、`async get(self, *, scope, version) -> dict | None`、`async list_by_scope(self, scope, limit=20) -> list[dict]`、`async get_latest(self, scope, skill_name=None) -> dict | None`(ORDER BY created_at DESC LIMIT 1)

### 阶段 4:ExampleLoader 扩展(AC-7)

7. **扩展 example_loader.py** — `backend/private_agent/skills/example_loader.py:64` 在 load() 方法后新增 `async def load_test_set(self, skill_name: str) -> list[EvalSample]:` 方法:glob `{dev_dir}/{skill_name}/examples/test/*.json`(按文件名排序),每个文件 `json.loads(f.read_text())` 后 `EvalSample.model_validate(data)`(校验失败抛 InvalidSampleFormatError),不做 token 截断。需在文件顶部 import `from private_agent.eval.models import EvalSample, InvalidSampleFormatError` 和 `import json`

### 阶段 5:种子测试样本(AC-8)

8. **创建 office 测试样本目录** — 新建 `backend/skills/office/examples/test/` 目录
9. **写 office 样本** — 创建 4 个 JSON 文件:`office_001_normal.json`、`office_002_normal.json`、`office_003_boundary.json`、`office_004_error.json`,每条含完整 EvalSample 字段(sample_id/scenario="office"/skill_name="office"/skill_version="1.0.0"/case_type/difficulty/split="test"/input/expected_react_trace/expected_output)。normal 样本对应 train/web_research.md 和 train/excel_summary.md 变体;boundary 样本为空数据/超大文件;error 样本为工具调用超时/权限拒绝
10. **创建 data_analysis 测试样本** — 新建 `backend/skills/data_analysis/examples/test/` 目录 + 4 个 JSON 文件(`data_analysis_001_normal.json` 等),normal 对应 train/data_visualization.md 和 train/statistical_test.md 变体
11. **创建 frontend_design 测试样本** — 新建 `backend/skills/frontend_design/examples/test/` 目录 + 4 个 JSON 文件(`frontend_design_001_normal.json` 等),normal 对应 train/landing_page.md 和 train/react_component.md 变体

### 阶段 6:judge_prompts 模板(AC-9)

12. **创建 judge_prompts 目录** — 新建 `backend/config/judge_prompts/` 目录
13. **写 general.md 模板** — 创建 `backend/config/judge_prompts/general.md`,内容为蓝图 §8.8 的通用 Judge prompt:含 `{user_input}` / `{agent_response}` / `{expected_output}` 三个模板变量,要求 Judge 模型输出严格 JSON(response_quality: 1-5, task_completion: 1-5, quality_reason, completion_reason)

### 阶段 7:eval_runs TTL 清理(AC-10)

14. **扩展 ttl_cleanup.py** — `backend/private_agent/storage/ttl_cleanup.py:87` 在 run_ttl_cleanup 函数后新增 `async def cleanup_old_eval_runs(conn: asyncpg.Connection, *, keep_recent: int = 100) -> int:`:DELETE FROM eval_runs WHERE id NOT IN (SELECT id FROM eval_runs ORDER BY started_at DESC LIMIT $1),返回删除行数(复用 _parse_row_count)

### 阶段 8:测试(覆盖全部 AC)

15. **写 models 测试** — 新建 `backend/tests/eval/test_models.py`:测试 EvalSample 合法构造、非法 expected_react_trace(缺 tool_calls / tool_calls 非数组 / expected_output_contains 非数组)抛 InvalidSampleFormatError、validate_expected_trace 正常返回 ExpectedTrace
16. **写 repos 测试** — 新建 `backend/tests/eval/test_repos.py`:测试 EvalDatasetRepo.insert(合法+非法)、load_test_set(空表+有数据)、EvalRunRepo.create_run/update_run_metrics/complete_run/list_runs、VersionSnapshotRepo.save/get/list_by_scope/get_latest。用 pytest-asyncio + asyncpg fixture(参考现有 test_memories_repo.py 模式)
17. **写 example_loader + ttl_cleanup 测试** — 新建 `backend/tests/eval/test_example_loader_test_set.py`(load_test_set 加载 12 条样本 + 空目录返回空列表)+ 扩展 `backend/tests/storage/test_ttl_cleanup.py`(新增 cleanup_old_eval_runs 测试:插入 105 条 eval_runs,调用后保留 100 条,删除 5 条)
18. **写 migration 测试** — 扩展 `backend/tests/storage/test_migrations.py`:测试 migrate_all 后 eval_datasets 表含 split 列 + CHECK 约束(INSERT split='invalid' 抛错)

---

## Workspace setup

- 实施前运行 `git status --short` 和 `git branch --show-current`
- 当前 master 分支(commit 070bbdb),working tree 应为干净状态
- **建议创建 worktree**:`git worktree add -b codex/m4-eval-foundation ../private-agent-m4-eval-foundation`(当前在 master 分支,plan 会修改代码/配置/测试,推荐 worktree 隔离)
- 如果 working tree 已 dirty,先保护现有改动,不混入本 plan 改动

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| eval_runs 表无 status 字段,running/completed 状态判断依赖 finished_at IS NULL | EvalRunRepo.list_runs 的 status 参数映射:running→finished_at IS NULL,completed→finished_at IS NOT NULL,fail→metrics 含 error 键。在 Repo docstring 明确标注 |
| mock_mode 与 mock_enabled 字段冗余可能导致后续 spec 误用 | Repo 层只用 mock_enabled;在 EvalRunRepo.create_run docstring 标注"mock_mode 字段保留但不用,统一用 mock_enabled" |
| 12 条种子样本 expected_react_trace 编写错误导致 Pydantic 校验失败 | 每条样本编写后立即跑 `EvalSample.model_validate(json.load(f))` 验证;测试 step 17 覆盖 load_test_set 全量加载 |
| split 列 ALTER 在已有 eval_datasets 数据时可能报错(若旧数据无 split 值) | migration 用 `ADD COLUMN IF NOT EXISTS split ... DEFAULT 'test'`,DEFAULT 保证已有数据自动填 'test' |
| judge_prompts/general.md 模板变量与 m4-metrics-judge spec 的 load_judge_prompt 实现不匹配 | 模板变量用 `{user_input}`/`{agent_response}`/`{expected_output}` 三者,与 spec AC-9 完全对齐;m4-metrics-judge spec 的 load_judge_prompt 实现需用 `str.replace` 填充(非 f-string,避免与 JSON 内容冲突) |
| ExampleLoader 新增 load_test_set 引入 eval.models 循环依赖 | example_loader.py 在 skills/ 包,import eval/ 包的 models;检查无反向依赖(eval/models.py 不 import skills/) |

## Verification steps

- **验证 AC-1**:`pytest backend/tests/storage/test_migrations.py::test_migrate_adds_split_column -v`,断言 `\d eval_datasets` 输出含 split 列 + CHECK 约束
- **验证 AC-2**:`pytest backend/tests/eval/test_models.py::test_invalid_expected_react_trace_raises -v`,断言 InvalidSampleFormatError 抛出
- **验证 AC-3**:`pytest backend/tests/eval/test_repos.py::test_eval_dataset_repo_insert_invalid_raises -v`
- **验证 AC-4**:`pytest backend/tests/eval/test_repos.py::test_eval_dataset_repo_load_test_set -v`
- **验证 AC-5**:`pytest backend/tests/eval/test_repos.py::test_eval_run_repo_create_run -v`,断言 run_id 非空 + finished_at IS NULL
- **验证 AC-6**:`pytest backend/tests/eval/test_repos.py::test_version_snapshot_repo_save_get -v`
- **验证 AC-7**:`pytest backend/tests/eval/test_example_loader_test_set.py::test_load_test_set_loads_12_samples -v`
- **验证 AC-8**:`python -c "from pathlib import Path; files=list(Path('backend/skills').rglob('examples/test/*.json')); assert len(files)==12; print(len(files))"`
- **验证 AC-9**:`python -c "content=Path('backend/config/judge_prompts/general.md').read_text(); assert '{user_input}' in content and '{agent_response}' in content and '{expected_output}' in content"`
- **验证 AC-10**:`pytest backend/tests/storage/test_ttl_cleanup.py::test_cleanup_old_eval_runs -v`
- **全量回归**:`pytest backend/tests/ -v --tb=short` 确保现有测试不回归

## ADR

- **Decision**: 采用 Option A(Pydantic 模型 + 单文件 repos.py + JSONB 原生序列化),仓储层复用 asyncpg 直连模式,与 M3 skills/manager.py 的 Pydantic 风格一致
- **Drivers**:
  - 与现有仓储层一致性(最高权重)— asyncpg 直连模式确认
  - spec In scope 边界严格性 — repos.py 单文件含三 Repo 类,与 spec 完全对齐
  - 种子样本可维护性 — Pydantic model_validate 保证样本结构正确
  - migration 幂等性 — 延续 ADD COLUMN IF NOT EXISTS 模式
- **Alternatives considered**:
  - Option A(Pydantic + 单文件 repos.py)— **chosen**,与 spec 对齐 + Pydantic 满足 AC-2/AC-3 校验要求
  - Option B(dataclass + 手写序列化)— **rejected**,spec 明确要求 Pydantic 校验,dataclass 需额外引入 Pydantic 反增复杂度
  - Option C(Pydantic + 分文件仓储层)— **rejected**,违反 spec "新建 repos.py 含三个 Repo 类"指示 + 3 Repo 共享模型无拆分必要
- **Why chosen**: spec AC-2/AC-3 明确要求 Pydantic 入库前校验,Option A 直接满足;repos.py 单文件与 spec 设计完全对齐;JSONB 原生序列化(eval_datasets 表已有 JSONB CHECK 约束)减少手写转换代码
- **Consequences**:
  - 正面:eval/ 子包模型风格与 M3 skills 一致;仓储层接口锁定,后续 4 个 spec 可直接复用;Pydantic 校验保证数据质量
  - 负面:与 memories_repo(dataclass)风格不完全一致,团队需适应两种模型风格;但 M3 已引入 Pydantic(skills/models.py),不算新模式
- **Follow-ups**:
  - m4-metrics-judge spec 需复用 EvalSample/ExpectedTrace 模型计算指标
  - m4-eval-runner-replay spec 需复用 EvalRunRepo.create_run/update_run_metrics
  - m4-version-compare-rollback spec 需复用 VersionSnapshotRepo + EvalRunRepo.list_runs
  - 20 条数据集阈值(本 spec 仅 12 条)由 §8.16 低分案例渐进填充

## Review trail

### Iteration 1

- **Planner draft v1**: 产出 Option A/B/C 三方案,推荐 Option A,18 步实施步骤
- **Architect challenge v1**:
  - Steelman against Option A:memories_repo 用 dataclass,Option A 用 Pydantic,仓储层风格不一致可能让团队困惑。如果反驳成立,应改用 Option B(dataclass)。**但**反驳不成立:spec AC-2/AC-3 明确要求 Pydantic 校验,且 M3 skills/models.py 已用 Pydantic,不算新模式
  - Tradeoff tension 1:仓储层风格一致性(memories_repo dataclass)vs spec 明确要求(Pydantic 校验)— 取后者,因 spec 是 source of truth
  - Tradeoff tension 2:repos.py 单文件(3 类共存)vs 分文件 — 取单文件,因 3 Repo 共享 EvalSample 模型 + spec 明确指示
  - Synthesis:无需综合,Option A 已是综合方案
- **Critic verdict v1**: REVISE
  - 拒收原因:Plan 未处理 eval_runs 表无 status 字段的问题(表 schema 无 status 列,但 AC-5 要求 "status='running'",且 list_runs 的 status 参数需有映射规则)
  - 待改项:
    1. Implementation step 5(EvalRunRepo)需明确 status 映射规则:running→finished_at IS NULL,completed→finished_at IS NOT NULL
    2. Risks 表需补充 "eval_runs 无 status 字段" 风险 + mitigation
    3. AC-5 措辞 "status='running'" 需修正为 "finished_at IS NULL(隐含 running 状态)"
  - Reservations:即使修正后,仍有保留——get_low_score_samples 的 SQL `sample_results->'task_completion'->>'completion_rate'` 假设 sample_results 是 list[dict] 而非 dict,需在 m4-metrics-judge spec 中明确 sample_results 的 JSONB 结构

### Iteration 2

- **Planner draft v2**: 修正 Implementation step 5(EvalRunRepo)明确 status 映射规则;Risks 表补充 eval_runs 无 status 字段风险;AC-5 措辞修正;get_low_score_samples SQL 补充结构假设说明
- **Architect challenge v2**:
  - Steelman:status 映射规则(running→finished_at IS NULL)在 fail_run 场景下有歧义——fail_run 也设 finished_at=now(),会被 list_runs(status="completed") 误包含。**但**反驳部分成立:需在 fail_run 的 metrics 中写 error 键,list_runs(status="failed") 查询 metrics ? error IS NOT NULL
  - Tradeoff tension:status 三态(running/completed/failed)用 finished_at + metrics.error 联合判断 vs 新增 status 列 — 取前者,避免 schema 变更(外科手术原则)
- **Critic verdict v2**: APPROVED with 2 improvements applied
  - 应用的改进:
    1. Implementation step 5 补充 fail_run 的 list_runs(status="failed") 映射:finished_at IS NOT NULL AND metrics ? 'error'
    2. Risks 表补充 fail_run 状态判断风险 + mitigation(metrics.error 键区分)
  - Reservations:
    1. get_low_score_samples 的 SQL 假设 sample_results 是 list[dict],但 eval_runs 表 schema 未约束 sample_results 结构——若 m4-eval-runner-replay spec 的 EvalRunner 写入 sample_results 为 dict 而非 list,此 SQL 会失败。**建议**:在 m4-eval-runner-replay spec 的 dev-plan 阶段锁定 sample_results JSONB 结构(建议 `{samples: [{sample_id, metrics: {task_completion: {completion_rate}}}], aggregated: {...}}`)
    2. 12 条种子样本的 expected_react_trace 编写质量无法在 plan 阶段验证,需在 dev-tdd 阶段每条样本跑 model_validate 确认
- **Final iterations**: 2 / 3
