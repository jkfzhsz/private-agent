# M4 评估闭环 - 持续进化与样本扩充 Spec (m4-continuous-evolution)

> Status: ALIGNED
> Author: user
> Last updated: 2026-08-01

## Background

持续进化闭环的最后一环:从低分评估案例自动提取薄弱用例,人工审核队列支持两类筛选标准(模型能力限制丢弃 / Prompt 缺陷编辑后入库),闭环串联整个 M4 评估流程。依赖前四个 spec 的 EvalRunRepo / EvalComparator / SkillRollbackManager。

蓝图章节: §8.16(持续进化闭环与自动样本扩充)。

## In scope

### A. WeakSampleExtractor 低分案例提取 (蓝图 §8.16)
- 新建 `backend/private_agent/eval/weak_sample.py`:

```python
class WeakSampleExtractor:
    def __init__(
        self,
        eval_repo: EvalRunRepo,
        dataset_repo: EvalDatasetRepo,
        review_queue_repo: ReviewQueueRepo,
    ) -> None: ...

    async def extract_from_low_score_runs(
        self,
        *,
        threshold: float = 0.6,   # 任务完成率 < threshold 视为低分
        limit: int = 50,
    ) -> list[dict]:
        """从低分评估案例中提取薄弱用例
        1. eval_repo.get_low_score_samples(threshold, limit) 获取低分样本
        2. 每条加入 review_queue_repo,标记 suggested_as="boundary"
        3. 返回提取的候选列表
        低分判定:task_completion.completion_rate < threshold
        """
```

### B. ReviewQueueRepo 人工审核队列
- 在 `backend/private_agent/eval/repos.py` 新增 `ReviewQueueRepo`(复用同一文件):

```python
class ReviewQueueRepo:
    """低分案例人工审核队列存储
    MVP 用 JSON 文件存储(避免新增 DB 表),路径: {workspace_root}/.eval_review_queue.json
    V2 可迁移到 DB 表
    """

    def __init__(self, queue_file: str) -> None: ...

    async def add(self, item: dict) -> int:
        """添加审核项,返回 item_id
        item: {source_run_id, sample_input, actual_output, actual_events, failure_reason, suggested_as, status: "pending"}
        """

    async def list_pending(self, limit: int = 20) -> list[dict]:
        """列出待审核项(status="pending")"""

    async def list_all(self, status: str = None, limit: int = 50) -> list[dict]:
        """列出所有审核项(可按 status 过滤)"""

    async def update_status(
        self,
        item_id: int,
        *,
        status: str,                # "approved" | "rejected" | "edited"
        decision: str,              # "model_limitation_drop" | "prompt_defect_edit"
        edited_sample: EvalSample | None = None,   # decision="prompt_defect_edit" 时提供
    ) -> None:
        """更新审核状态
        - status="approved" + decision="prompt_defect_edit":编辑后的样本入库(eval_datasets)
        - status="rejected" + decision="model_limitation_drop":丢弃,不入库
        - status="edited":同 approved,样本入库
        """
```

### C. 两类筛选标准 (蓝图 §8.16)
- 人工审核界面支持两类筛选决策:
  1. **模型能力限制丢弃**(`decision="model_limitation_drop"`):低分原因为模型能力不足(非样本/Prompt 问题)→ 丢弃,不加入测试集
  2. **Prompt/Skill 缺陷编辑后入库**(`decision="prompt_defect_edit"`):低分原因为 Prompt/Skill 逻辑缺陷 → 编辑期望轨迹后入库作为边界样本(`case_type="boundary"`)

### D. 审核通过样本入库
- `ReviewQueueRepo.update_status(status="approved", decision="prompt_defect_edit", edited_sample=...)`
  - 调 `EvalDatasetRepo.insert(edited_sample)` 入库
  - edited_sample 的 `case_type` 设为 "boundary"(低分案例多为边界用例)
  - edited_sample 的 `split` 设为 "test"
  - 入库前调 `validate_expected_trace` Pydantic 校验

### E. 审核队列 API 端点
- 在 `backend/private_agent/api/eval.py` 新增端点:

```python
@router.get("/review-queue")
async def list_review_queue(status: str = "pending", limit: int = 20) -> dict:
    """列出审核队列"""

@router.post("/review-queue/{item_id}/decide")
async def decide_review_item(
    item_id: int,
    request: ReviewDecisionRequest,
) -> dict:
    """审核决策
    body: {decision: "model_limitation_drop"|"prompt_defect_edit", edited_sample?: EvalSample}
    """
```

### F. 持续进化闭环串联 (蓝图 §8.16 闭环图)
- 闭环流程:
  1. 数据集更新(人工编写 + 真实会话提取 + 自动扩充)
  2. 评估运行(手动 / 版本变更自动触发)
  3. 结果分析(版本对比 + 退化检测)
  4. 问题定位(低分样本分析 + 指标归因)
  5. 修改优化(Prompt/Skill/Harness 修改)
  6. 新版本快照(version_snapshots / Git Tag)
  7. 回归评估(自动触发快速回归)
  8. 发布 / 回滚
  9. **自动样本扩充**(本 spec:从低分案例提取薄弱用例)
  10. 回到步骤 1

- 闭环测试:验证从"评估运行 → 低分提取 → 人工审核 → 入库 → 下次评估含新样本"完整链路

### G. 渐进扩充数据集
- 本 spec 验证渐进扩充机制:低分案例 → 审核队列 → 编辑入库 → 数据集增长
- m4-eval-foundation 交付 12 条种子样本,本 spec 提供扩充到 20 条阈值的机制(依赖真实评估运行产生低分案例)

## Out of scope

- 用户在线反馈采集(V2)
- 样本自动生成(模型生成 + 人工筛选)(V2)
- 闭环全自动化(V2)
- ReviewQueueRepo 迁移到 DB 表(V2, MVP 用 JSON 文件)
- 样本质量自动评分(V2)
- 样本难度自动分级(V2)

## Assumptions

- EvalRunRepo.get_low_score_samples 已在 m4-eval-foundation 实现
- EvalDatasetRepo.insert 已在 m4-eval-foundation 实现(含 Pydantic 校验)
- 低分阈值 threshold=0.6 来自蓝图 §8.16 默认值
- ReviewQueueRepo 用 JSON 文件存储,MVP 避免新增 DB 表(单人开发场景,并发风险低)
- 审核队列文件路径:`{workspace_root}/.eval_review_queue.json`,从 `cfg["system"]["workspace_root"]` 读取
- 人工审核通过后,edited_sample 的 `case_type` 固定为 "boundary",`split` 固定为 "test"

## Solution

### 低分提取流程
```
EvalRun(评估运行完成)
    ↓
WeakSampleExtractor.extract_from_low_score_runs(threshold=0.6)
    ↓
    eval_repo.get_low_score_samples(threshold, limit)
    ↓ 每条低分样本
    review_queue_repo.add({
        source_run_id, sample_input, actual_output, actual_events,
        failure_reason, suggested_as: "boundary", status: "pending"
    })
    ↓
    返回候选列表
```

### 人工审核流程
```
GET /admin/eval/review-queue?status=pending
    ↓ 列出待审核项
人工审核(UI 显示 sample_input / actual_output / failure_reason)
    ↓ 决策
POST /admin/eval/review-queue/{item_id}/decide
    body: {decision: "prompt_defect_edit", edited_sample: {...}}
    ↓
    ReviewQueueRepo.update_status(item_id, status="approved", decision, edited_sample)
    ↓ decision="prompt_defect_edit"
    EvalDatasetRepo.insert(edited_sample)  → 入库(split="test", case_type="boundary")
    ↓
    下次评估运行 load_test_set 含新样本
```

### 关键实现细节

**低分样本判定**:
- `eval_repo.get_low_score_samples(threshold)` 查询 eval_runs 表,sample_results JSONB 中 task_completion.completion_rate < threshold 的样本
- SQL: `SELECT ... FROM eval_runs WHERE ... AND (sample_results->'task_completion'->>'completion_rate')::float < $1`

**ReviewQueueRepo JSON 文件格式**:
```json
{
  "items": [
    {
      "id": 1,
      "source_run_id": "uuid-xxx",
      "sample_input": "...",
      "actual_output": "...",
      "actual_events": [...],
      "failure_reason": "...",
      "suggested_as": "boundary",
      "status": "pending",
      "created_at": "2026-08-01T...",
      "decided_at": null,
      "decision": null
    }
  ],
  "next_id": 2
}
```

## Edge cases & risks

| Category | Notes |
|---|---|
| Boundary conditions | 无低分样本时 extract 返回空列表;审核队列为空时 list_pending 返回空;edited_sample 校验失败时 update_status 抛 InvalidSampleFormatError |
| Failure modes | ReviewQueueRepo JSON 文件读写失败(权限/磁盘)抛 IOError;并发写入可能丢数据(MVP 单人场景风险低) |
| Risks | 低分阈值 0.6 可能过高/过低(需实际评估校准);JSON 文件存储无事务保证;人工审核 UI 缺失时审核流程无法进行 |
| Mitigation | 阈值可通过 API 参数调整;JSON 文件写入用临时文件 + rename 原子操作;审核 API 支持纯命令行调用(不依赖 UI) |

## Acceptance criteria

- AC-1: `WeakSampleExtractor.extract_from_low_score_runs(threshold=0.6)` 从 eval_runs 提取低分样本(completion_rate < threshold)加入审核队列
- AC-2: `ReviewQueueRepo.add(item)` 添加审核项,返回 item_id,status="pending"
- AC-3: `ReviewQueueRepo.list_pending()` 返回 status="pending" 的审核项列表
- AC-4: `ReviewQueueRepo.update_status(item_id, status="approved", decision="prompt_defect_edit", edited_sample)` 将编辑后样本入库 eval_datasets(split="test", case_type="boundary")
- AC-5: `ReviewQueueRepo.update_status(item_id, status="rejected", decision="model_limitation_drop")` 丢弃样本,不入库
- AC-6: 入库前调 `validate_expected_trace`,非法样本抛 `InvalidSampleFormatError`
- AC-7: `GET /admin/eval/review-queue` 返回审核队列列表(可按 status 过滤)
- AC-8: `POST /admin/eval/review-queue/{item_id}/decide` 处理审核决策,支持两类筛选标准
- AC-9: 闭环测试:评估运行 → 低分提取 → 审核决策(prompt_defect_edit)→ 入库 → 下次评估 load_test_set 含新样本
- AC-10: 闭环测试:评估运行 → 低分提取 → 审核决策(model_limitation_drop)→ 不入库 → 数据集不变

## Open questions

(无)

## Core entities (ontology)

| Entity | Type | Key fields | Relationship |
|---|---|---|---|
| WeakSampleExtractor | Class | eval_repo, dataset_repo, review_queue_repo | 低分案例提取 |
| ReviewQueueRepo | Class | queue_file | 审核队列存储(JSON 文件) |
| ReviewItem | dict | id, source_run_id, sample_input, actual_output, failure_reason, suggested_as, status, decision | 审核项 |
| ReviewDecisionRequest | Pydantic Model | decision, edited_sample | API 请求体 |

## Interview metadata

- Mode: default
- Waves: 4
- Final ambiguity: 14%
- Status: PASSED

### Clarity breakdown

| Dimension | Score | Weight | Weighted |
|---|---|---|---|
| Goal | 0.88 | 0.40 | 0.352 |
| Scope | 0.90 | 0.25 | 0.225 |
| AC | 0.88 | 0.25 | 0.22 |
| Context | 0.85 | 0.10 | 0.085 |
| Ambiguity | | | 11.8% |
