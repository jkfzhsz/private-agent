# M4 评估闭环 - 基础设施 Spec (m4-eval-foundation)

> Status: ALIGNED
> Author: user
> Last updated: 2026-08-01

## Background

M4 评估闭环的底层基础设施。当前 `eval/` 子包全空、`eval_datasets` 表缺 `split` 列、`version_snapshots` 无 Python 仓储层、`examples/test/` 目录未创建、judge_prompts 目录不存在。本 spec 补齐数据存储、仓储层、测试样本加载、judge prompt 模板，为后续 4 个 spec 提供数据契约。

蓝图章节: §8.2(评估环境概述)、§8.3-8.4(数据集 + eval_datasets 表 + Pydantic 校验)、§8.11(eval_runs 表)。

## In scope

### A. eval_datasets 表 split 列 + CHECK 约束 (蓝图 §8.4)
- `eval_datasets` 表新增 `split VARCHAR(10) NOT NULL CHECK (split IN ('train', 'test'))` 列
- migrations.py 追加幂等 `ALTER TABLE eval_datasets ADD COLUMN IF NOT EXISTS split ...`
- 现有 schema.sql 同步更新(split 列 + CHECK 约束)
- 已有数据默认填 `test`(评估样本)

### B. Pydantic 校验模型 (蓝图 §8.4)
- 新建 `backend/private_agent/eval/models.py`
- `ExpectedToolCall(BaseModel)`: `tool: str`, `args: dict = {}`, `expected_result_type: str | None = None`
- `ExpectedTrace(BaseModel)`: `tool_calls: list[ExpectedToolCall]`, `expected_output_contains: list[str]`
- `EvalSample(BaseModel)`: `sample_id`, `scenario`, `skill_name`, `skill_version`, `case_type`(normal/boundary/error), `difficulty`(easy/medium/hard), `split`(train/test), `input`, `expected_react_trace: ExpectedTrace`, `expected_output: str | None`
- `InvalidSampleFormatError(Exception)`: 非法结构异常
- `validate_expected_trace(trace: dict) -> ExpectedTrace`: 入库前校验,失败抛 `InvalidSampleFormatError`

### C. Python 仓储层 (蓝图 §8.11 + §7.3 version_snapshots 复用)
- 新建 `backend/private_agent/eval/repos.py`,包含三个 Repo 类:

```python
class EvalDatasetRepo:
    def __init__(self, conn) -> None: ...
    async def insert(self, sample: EvalSample) -> int: ...           # 入库前调 validate_expected_trace
    async def load_test_set(self, scenario: str, skill_version: str) -> list[EvalSample]: ...
    async def load_by_split(self, scenario: str, split: str) -> list[EvalSample]: ...
    async def get_by_sample_id(self, sample_id: str) -> EvalSample | None: ...

class EvalRunRepo:
    def __init__(self, conn) -> None: ...
    async def create_run(self, *, skill_name, skill_version, model_id, dataset_version, eval_mode, mock_enabled) -> str: ...
    async def update_run_metrics(self, run_id: str, metrics: dict, sample_results: list[dict]) -> None: ...
    async def complete_run(self, run_id: str) -> None: ...
    async def fail_run(self, run_id: str, error: str) -> None: ...
    async def list_runs(self, *, skill_version: str | None, model_id: str | None, status: str | None, limit: int = 20) -> list[dict]: ...
    async def get_run(self, run_id: str) -> dict | None: ...
    async def get_low_score_samples(self, threshold: float = 0.6, limit: int = 50) -> list[dict]: ...   # §8.16 复用

class VersionSnapshotRepo:
    def __init__(self, conn) -> None: ...
    async def save(self, *, scope: str, version: str, payload: dict) -> int: ...   # scope: prompt/skill/harness/config/kb
    async def get(self, *, scope: str, version: str) -> dict | None: ...
    async def list_by_scope(self, scope: str, limit: int = 20) -> list[dict]: ...
    async def get_latest(self, scope: str, skill_name: str | None = None) -> dict | None: ...
```

### D. ExampleLoader 扩展: load_test_set (蓝图 §8.4)
- 扩展 `backend/private_agent/skills/example_loader.py`
- 新增 `async def load_test_set(self, skill_name: str) -> list[EvalSample]:`
  - glob `{dev_dir}/{skill_name}/examples/test/*.json`(按文件名排序)
  - 每个文件解析为 `EvalSample`(Pydantic 校验,失败抛 `InvalidSampleFormatError`)
  - 不做 token 截断(test 样本需完整结构)
- 现有 `load()` 方法不动(仍加载 train/*.md)

### E. 种子测试样本 (3 场景 × 4 条 = 12 条)
- 为 office / data_analysis / frontend_design 各创建 `examples/test/` 子目录
- 每场景 4 条 JSON 样本:
  - 2 条 normal(对应 train/ 中的核心示例变体)
  - 1 条 boundary(空数据/大文件/极端输入)
  - 1 条 error(工具调用失败/超时/权限拒绝)
- 样本格式遵循 §8.3 JSON schema(sample_id/scenario/skill_name/skill_version/case_type/difficulty/input/expected_react_trace/expected_output)
- split 字段固定为 "test"
- 文件命名: `{scenario}_{seq:03d}_{case_type}.json`(如 `office_001_normal.json`)
- 20 条阈值由 §8.16 低分案例自动提取渐进填充,本 spec 仅交付 12 条种子

### F. judge_prompts 目录 + 通用模板 (蓝图 §8.8)
- 创建 `backend/config/judge_prompts/` 目录
- 写 `judge_prompts/general.md`:蓝图 §8.8 的通用 Judge prompt 模板(评估响应质量 1-5 + 任务完成度 1-5,输出严格 JSON)
- 模板变量: `{user_input}`, `{agent_response}`, `{expected_output}`(由 m4-metrics-judge spec 的 Judge 模块填充)
- V2 场景化 prompt 预留目录结构(`judge_prompts/office.md` 等暂不创建)

### G. eval_runs TTL 清理 (蓝图 §8.11 注释)
- 扩展 `backend/private_agent/storage/ttl_cleanup.py`
- 新增 `cleanup_old_eval_runs(conn, keep_recent: int = 100) -> int`:保留最近 100 条 eval_runs,删除更老的记录
- 复用现有 TTL 调度入口(如已有)

## Out of scope

- 五类指标计算器(m4-metrics-judge spec)
- EvalRunner / ReplayExecutor(m4-eval-runner-replay spec)
- 版本对比 / 回滚机制 / eval API(m4-version-compare-rollback spec)
- 低分案例自动提取(m4-continuous-evolution spec)
- 60 条完整数据集(本 spec 仅交付 12 条种子,剩余由 §8.16 渐进填充)
- train 样本迁移(M3 已完成)
- eval_runs `mock_mode` vs `mock_enabled` 字段冗余清理(保留 `mock_enabled`,删除 `mock_mode` 列在 migration 中处理)
- ab_tests 表创建(V2)
- 在线评估(V2)

## Assumptions

- `eval_datasets` 表已存在(M0 创建),仅缺 `split` 列
- `eval_runs` 表已存在,字段 `mock_mode` 和 `mock_enabled` 冗余,本 spec 统一用 `mock_enabled`(语义清晰),`mock_mode` 列保留但不用(避免破坏性 schema 变更)
- 测试样本 JSON 文件存放在 Git 管理的 `skills/{name}/examples/test/` 目录
- judge_prompts 目录路径来自 `config.yaml [eval] judge_prompt_dir`(值 `./config/judge_prompts`)
- 仓储层用 asyncpg 直连,与现有 memories_repo / kb_repo 模式一致
- EvalSample Pydantic 模型与 eval_datasets 表 schema 字段一一对应

## Solution

### 数据流
```
skills/{name}/examples/test/*..json
    ↓ ExampleLoader.load_test_set()
    ↓ Pydantic 校验(ExpectedTrace)
    ↓ EvalDatasetRepo.insert()  (可选,DB 持久化)
EvalSample list
    ↓ EvalRunRepo.create_run()  (后续 spec 使用)
    ↓ metrics 计算 + sample_results
    ↓ EvalRunRepo.update_run_metrics()
eval_runs 表
```

### 关键实现细节

**split 列 migration**(幂等):
```python
# migrations.py migrate_all() 末尾追加
await conn.execute("""
    ALTER TABLE eval_datasets
    ADD COLUMN IF NOT EXISTS split VARCHAR(10) NOT NULL DEFAULT 'test'
    CHECK (split IN ('train', 'test'))
""")
# 现有数据默认 'test'
```

**EvalDatasetRepo.insert** 入库前校验:
```python
async def insert(self, sample: EvalSample) -> int:
    validate_expected_trace(sample.expected_react_trace.model_dump())
    # INSERT INTO eval_datasets (...) VALUES (...) RETURNING id
```

**VersionSnapshotRepo** scope 枚举:`prompt` / `skill` / `harness` / `config` / `kb`(与 schema.sql CHECK 约束一致)

## Edge cases & risks

| Category | Notes |
|---|---|
| Boundary conditions | test/ 目录为空时 load_test_set 返回空列表(不抛异常);样本 JSON 缺 expected_output 字段时 Pydantic 校验通过(None 允许) |
| Failure modes | Pydantic 校验失败抛 InvalidSampleFormatError,Repo 不吞异常;DB 连接失败由上层处理 |
| Risks | split 列 ALTER 在已有数据时默认 'test',若用户误将 train 样本入库需手动修正;mock_mode/mock_enabled 冗余可能让后续 spec 误用 |
| Mitigation | Repo 层明确只用 mock_enabled 字段;split 列 migration 加 DEFAULT 'test' 避免已有数据报错 |

## Acceptance criteria

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

## Open questions

- 20 条数据集阈值:本 spec 仅交付 12 条种子样本,剩余 48 条依赖 §8.16 低分案例自动提取 + 人工审核渐进填充。Done Criteria AC-1 要求"每场景 20 条"是否接受渐进达成?(建议:种子样本验证管线 + 扩充机制就位即视为 AC-1 达成,数量阈值作为持续进化目标)

## Core entities (ontology)

| Entity | Type | Key fields | Relationship |
|---|---|---|---|
| EvalSample | Pydantic Model | sample_id, scenario, skill_name, skill_version, case_type, difficulty, split, input, expected_react_trace, expected_output | 属于 EvalDataset |
| ExpectedTrace | Pydantic Model | tool_calls[], expected_output_contains[] | EvalSample 的字段 |
| ExpectedToolCall | Pydantic Model | tool, args, expected_result_type | ExpectedTrace 的字段 |
| EvalDatasetRepo | Repo Class | conn | 读写 eval_datasets 表 |
| EvalRunRepo | Repo Class | conn | 读写 eval_runs 表 |
| VersionSnapshotRepo | Repo Class | conn | 读写 version_snapshots 表 |
| InvalidSampleFormatError | Exception | message | 样本校验失败抛出 |

## Interview metadata

- Mode: default
- Waves: 4
- Final ambiguity: 14%
- Status: PASSED

### Clarity breakdown

| Dimension | Score | Weight | Weighted |
|---|---|---|---|
| Goal | 0.85 | 0.40 | 0.34 |
| Scope | 0.90 | 0.25 | 0.225 |
| AC | 0.85 | 0.25 | 0.2125 |
| Context | 0.80 | 0.10 | 0.08 |
| Ambiguity | | | 14.25% |
