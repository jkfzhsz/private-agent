# M4 评估闭环 - 版本对比与回滚机制 Spec (m4-version-compare-rollback)

> Status: ALIGNED
> Author: user
> Last updated: 2026-08-01

## Background

版本对比双维度筛选 + 三类载体(Prompt/Skill/Harness)回滚机制 + 退化告警(仅 UI + eval_runs,不自动阻断)+ A/B 测试字段预留 + eval API 端点 + 前端评估面板。依赖前三个 spec 的 EvalRunRepo / VersionSnapshotRepo / EvalRunner / SkillLoader.load_version。

蓝图章节: §8.12(版本对比)、§8.13(三类载体迭代闭环)、§8.14(回滚机制)、§8.15(A/B 预留)。

## In scope

### A. EvalComparator 版本对比 (蓝图 §8.12)
- 新建 `backend/private_agent/eval/version_compare.py`:

```python
class EvalComparator:
    def __init__(self, eval_repo: EvalRunRepo) -> None: ...

    async def compare_versions(
        self,
        *,
        skill_name: str,
        base_version: str,
        target_version: str,
        model_id: str | None = None,
    ) -> dict:
        """对比两个版本的评估结果
        基线筛选规则:同模型 + 同 Skill 最新成功基线
        1. base_runs = eval_repo.list_runs(skill_version=base_version, model_id=model_id, status="completed")
        2. target_runs = eval_repo.list_runs(skill_version=target_version, model_id=model_id, status="completed")
        3. base_metrics = base_runs[-1].metrics  (最新成功)
        4. target_metrics = target_runs[-1].metrics
        5. diff = _compute_diff(base_metrics, target_metrics)
        返回: {base_version, target_version, model_id, base_metrics, target_metrics, diff}
        缺数据抛 InsufficientDataError
        """

    def _compute_diff(self, base: dict, target: dict) -> dict:
        """计算指标差值,标记 improved/degraded/stable
        正数=提升,负数=退化
        """
```

### B. 三类载体回滚机制 (蓝图 §8.14)
- 新建 `backend/private_agent/eval/rollback.py`:

```python
class SkillRollbackManager:
    def __init__(
        self,
        snapshot_repo: VersionSnapshotRepo,
        skill_loader: SkillLoader,
        skill_repo=None,   # sessions 表 latest_version 更新
    ) -> None: ...

    async def rollback_prompt(
        self,
        *,
        skill_name: str,
        target_version: str,
        conn,
    ) -> dict:
        """仅回滚 Prompt,不影响 Skill 元数据与工具白名单(蓝图 §8.14)
        1. snapshot_repo.get(scope="prompt", version=target_version) 读历史 Prompt
        2. 更新 latest_version 指针(仅 prompt)
        3. 仅对新会话生效,运行中会话维持锁定版本
        返回: {rolled_back_to, scope: "prompt", affected_sessions: 0}
        """

    async def rollback_skill(
        self,
        *,
        skill_name: str,
        target_version: str,
        conn,
    ) -> dict:
        """回滚整个 Skill(元数据 + Prompt + 工具白名单)
        1. snapshot_repo.get(scope="skill", version=target_version) 读历史 Skill 快照
        2. 更新 latest_version 指针
        3. 新会话加载 target_version,运行中会话维持锁定版本
        返回: {rolled_back_to, scope: "skill", affected_sessions: 0}
        """

    async def rollback_harness(
        self,
        *,
        target_commit: str,
    ) -> dict:
        """Harness 代码回滚:返回 git revert 指令(不自动执行)
        单人开发手动 git revert + 重新部署
        返回: {command: f"git revert {target_commit}", note: "手动执行后重启 Sidecar"}
        """
```

### C. 退化告警 (蓝图 §8.13 发布控制说明)
- 退化检测在 EvalComparator._compute_diff 中标记 degraded
- 退化时:
  - 写入 eval_runs 记录(metrics 含 diff 标记)
  - UI 评估面板显示退化告警(红色标记)
  - **不自动阻断发布**(蓝图 §8.13 明确:仅 UI 告警 + eval_runs 记录)
- 无强制拦截逻辑,降低单人开发流程门槛

### D. A/B 测试字段预留 (蓝图 §8.15)
- eval_runs.variant 字段已存在(M0 创建),MVP 默认 null
- 版本对比仅对比两个指定版本,不涉及流量分配
- ab_tests 表 MVP 不创建(V2)

### E. eval API 端点 (蓝图 §8.11 + §8.12)
- 新建 `backend/private_agent/api/eval.py`:

```python
router = APIRouter(prefix="/admin/eval", tags=["eval"])

@router.post("/runs")
async def trigger_eval_run(request: EvalRunRequest) -> dict:
    """触发评估运行
    body: {skill_name, skill_version, model_id, eval_mode, mock_enabled, sample_subset}
    调 EvalRunner.run_evaluation,返回 {run_id}
    """

@router.get("/runs")
async def list_eval_runs(skill_name: str = None, limit: int = 20) -> dict:
    """评估运行列表"""

@router.get("/runs/{run_id}")
async def get_eval_run(run_id: str) -> dict:
    """单次评估详情 + metrics + sample_results"""

@router.get("/datasets")
async def list_eval_datasets(scenario: str = None) -> dict:
    """数据集列表"""

@router.get("/versions/compare")
async def compare_versions(
    skill_name: str,
    base_version: str,
    target_version: str,
    model_id: str = None,
) -> dict:
    """版本对比"""

@router.post("/rollback")
async def trigger_rollback(request: RollbackRequest) -> dict:
    """回滚
    body: {skill_name, target_version, scope: "prompt"|"skill"|"harness", target_commit?: str}
    """
```

- main.py 注册 `app.include_router(eval.router)`

### F. 前端评估面板 (蓝图 §8.12 UI 可视化 MVP)
- 扩展 `frontend/renderer/App.tsx` 或新建 `frontend/renderer/EvalPanel.tsx`:
  - 评估运行列表(表格:run_id / skill / version / model / mode / status / 时间)
  - 版本趋势折线图(X 轴版本号,Y 轴指标值,展示指标随版本变化)
  - 版本对比表格(两版本指标并排,差值列高亮:退化标红,提升标绿)
  - 触发评估按钮(选择场景/版本/模型/模式)
  - 退化告警标记(红色 badge)
- 扩展 `frontend/static/chat.html` 同步评估面板入口
- MVP 不做复杂看板(折线图 + 表格即可)

### G. SkillVersionListener 集成 (蓝图 §8.13 自动触发)
- 在 admin API 的 Skill 保存端点(如 `POST /admin/skills/{name}/version`)或 SkillManager.activate_skill 后调用 SkillVersionListener.on_skill_version_saved
- 具体:新增 `POST /admin/skills/{name}/save-version` 端点,保存新版本到 version_snapshots + 触发 listener
- listener 触发失败仅记日志,不阻塞版本保存

## Out of scope

- 低分案例自动提取(m4-continuous-evolution spec)
- 自动回滚(评估不达标触发)(V2)
- 回滚前自动备份当前版本(V2)
- 回滚影响范围分析(V2)
- 完整 A/B 测试框架(流量分配 + 统计检验)(V2)
- 复杂看板 / 预警阈值 / 自定义图表配置(V2)
- 灰度发布(V2)

## Assumptions

- EvalRunRepo.list_runs 已在 m4-eval-foundation 实现
- VersionSnapshotRepo.get 已在 m4-eval-foundation 实现
- SkillLoader.load_version 已在 m4-eval-runner-replay 实现
- eval_runs.variant 字段已存在(M0 schema)
- 前端复用现有 React + TypeScript 框架,折线图用轻量 SVG 或引入 chart.js(MVP 用 SVG 避免新依赖)
- 回滚仅对新会话生效,sessions 表 locked_skill_version 字段保证运行中会话维持锁定版本(M3 已实现)

## Solution

### 版本对比流程
```
GET /admin/eval/versions/compare?skill_name=office&base_version=1.0.0&target_version=1.1.0
    ↓
    EvalComparator.compare_versions(skill_name, base, target, model_id)
    ↓
    eval_repo.list_runs(skill_version=base, status="completed")  → base_metrics(最新)
    eval_repo.list_runs(skill_version=target, status="completed") → target_metrics(最新)
    ↓
    _compute_diff(base_metrics, target_metrics)
    ↓
    返回 {base, target, diff: {category: {metric: {delta, status}}}}
```

### 回滚流程
```
POST /admin/eval/rollback
    body: {skill_name: "office", target_version: "1.0.0", scope: "skill"}
    ↓
    SkillRollbackManager.rollback_skill(skill_name, target_version, conn)
    ↓
    snapshot_repo.get(scope="skill", version="1.0.0")  → 历史 Skill 快照
    ↓
    更新 sessions 表 / skills 表 latest_version 指针 → "1.0.0"
    ↓
    返回 {rolled_back_to: "1.0.0", scope: "skill", affected_sessions: 0}
    (运行中会话不受影响,新会话加载 1.0.0)
```

### 关键实现细节

**基线筛选规则**(蓝图 §8.12):
- 同模型 + 同 Skill 最新成功基线
- list_runs 按 created_at DESC 排序,取 [0] 为最新
- model_id=None 时不过滤模型(跨模型对比,但蓝图建议同模型)

**退化检测**:
```python
def _compute_diff(self, base, target):
    diff = {}
    for category in base:
        diff[category] = {}
        for metric in base[category]:
            base_val = base[category].get(metric, 0)
            target_val = target.get(category, {}).get(metric, 0)
            delta = target_val - base_val
            diff[category][metric] = {
                "delta": delta,
                "status": "improved" if delta > 0 else ("degraded" if delta < 0 else "stable")
            }
    return diff
```

## Edge cases & risks

| Category | Notes |
|---|---|
| Boundary conditions | base 或 target 无 completed runs 时抛 InsufficientDataError;metrics 缺某类指标时该类 diff 标 stable |
| Failure modes | 回滚版本不存在时 snapshot_repo.get 返回 None,抛 VersionNotFoundError;Harness 回滚仅返回命令不执行 |
| Risks | 版本对比跨模型可能无意义(蓝图建议同模型,但 API 允许 model_id=None);回滚后新会话加载旧版本但 mock_data 可能不匹配(依赖 m4-eval-runner-replay 的 mock_data 版本同步) |
| Mitigation | API 文档注明 model_id 建议传入;回滚时校验目标版本的 mock_data 存在(若该 Skill 有测试样本) |

## Acceptance criteria

- AC-1: `EvalComparator.compare_versions()` 双维度筛选(同 model_id + 同 skill_version),取最新成功基线,缺数据抛 InsufficientDataError
- AC-2: `EvalComparator._compute_diff()` 计算指标差值,正确标记 improved/degraded/stable
- AC-3: `SkillRollbackManager.rollback_prompt()` 仅回滚 Prompt,更新 latest_prompt_version 指针,不影响工具白名单
- AC-4: `SkillRollbackManager.rollback_skill()` 回滚整个 Skill,新会话加载 target_version,运行中会话维持锁定版本
- AC-5: `SkillRollbackManager.rollback_harness()` 返回 git revert 命令,不自动执行
- AC-6: 退化检测在 diff 中标记 degraded,eval_runs 记录含退化标记,UI 显示红色告警,**不自动阻断发布**
- AC-7: eval_runs.variant 字段默认 null,MVP 不涉及流量分配
- AC-8: `POST /admin/eval/runs` 触发评估运行,返回 run_id
- AC-9: `GET /admin/eval/versions/compare` 返回版本对比结果含 diff
- AC-10: `POST /admin/eval/rollback` 触发回滚,返回 rolled_back_to + affected_sessions
- AC-11: 前端评估面板含运行列表 + 版本趋势折线图 + 版本对比表格 + 退化告警标记
- AC-12: `POST /admin/skills/{name}/save-version` 保存新版本到 version_snapshots + 触发 SkillVersionListener 快速回归

## Open questions

(无)

## Core entities (ontology)

| Entity | Type | Key fields | Relationship |
|---|---|---|---|
| EvalComparator | Class | eval_repo | 版本对比 |
| SkillRollbackManager | Class | snapshot_repo, skill_loader | 回滚机制 |
| InsufficientDataError | Exception | message | 对比数据不足 |
| VersionNotFoundError | Exception | message | 回滚版本不存在 |
| EvalRunRequest | Pydantic Model | skill_name, skill_version, model_id, eval_mode, mock_enabled, sample_subset | API 请求体 |
| RollbackRequest | Pydantic Model | skill_name, target_version, scope, target_commit | API 请求体 |

## Interview metadata

- Mode: default
- Waves: 4
- Final ambiguity: 14%
- Status: PASSED

### Clarity breakdown

| Dimension | Score | Weight | Weighted |
|---|---|---|---|
| Goal | 0.88 | 0.40 | 0.352 |
| Scope | 0.88 | 0.25 | 0.22 |
| AC | 0.88 | 0.25 | 0.22 |
| Context | 0.85 | 0.10 | 0.085 |
| Ambiguity | | | 12.3% |
