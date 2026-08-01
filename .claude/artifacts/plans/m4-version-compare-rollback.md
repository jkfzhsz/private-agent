# m4-version-compare-rollback Implementation Plan

> Status: APPROVED
> Source: spec/m4-version-compare-rollback
> Mode: (default)
> Iterations: 2 / 3
> Author: user
> Last updated: 2026-08-01

## Requirements summary

实现 M4 评估闭环的版本对比、三类载体回滚、退化告警(仅 UI + eval_runs,不阻断)、A/B 字段预留、eval API 端点、前端评估面板、SkillVersionListener 集成。依赖前三个 spec 的 EvalRunRepo / VersionSnapshotRepo / EvalRunner / SkillLoader.load_version / SkillVersionListener。

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

## RALPLAN-DR

### Principles

1. 跟随 spec 的 In scope 全量实现 A-G 段,不擅自延后前端面板
2. 前端面板 MVP 用纯 SVG 折线图(避免引入 chart.js 新依赖,符合 baseline 最小代码)
3. 回滚仅对新会话生效,复用 sessions.locked_skill_version(M3 已实现),不破坏运行中会话
4. 退化告警仅 UI + eval_runs 记录,不自动阻断发布(蓝图 §8.13 明确)
5. API 端点统一在 /admin/eval 前缀下,与 admin.py 现有风格一致
6. 复用现有 EvalRunRepo / VersionSnapshotRepo / SkillLoader.load_version / SkillVersionListener,不重写

### Decision drivers

1. **M4 是最后一环**,上线速度优先,但 AC-11 要求前端面板,不能延后
2. **单人开发场景**,降低单次 PR 复杂度,前端面板 MVP 用 SVG 即可
3. **代码风格一致性**:APIRouter 风格 / pydantic 请求体 / 测试 pytest async
4. **测试覆盖度**:M4 spec 要求 AC 全量实现,每个 AC 至少 1 个测试

### Viable options

**Option A: 全量实现 A-G,前端面板用纯 SVG + React,API 端点全在 eval.py**
- 实现思路:后端 5 个新文件(version_compare.py / rollback.py / api/eval.py)+ 前端 App.tsx 嵌入 EvalPanel 组件 + chat.html 同步入口
- 改动文件:
  - 新建 `backend/private_agent/eval/version_compare.py`
  - 新建 `backend/private_agent/eval/rollback.py`
  - 新建 `backend/private_agent/api/eval.py`
  - 扩展 `backend/private_agent/api/admin.py`(save-version 端点)
  - 扩展 `backend/private_agent/main.py`(注册 eval router)
  - 扩展 `backend/private_agent/skills/manager.py`(activate_skill 调用 SkillVersionListener 集成点 — 但 spec G 说 save-version 端点触发)
  - 扩展 `frontend/renderer/App.tsx`(EvalPanel 组件)
  - 扩展 `frontend/static/chat.html`(评估面板入口 + 容器)
  - 新建 8 个测试文件
- Pros: AC-11 可立即验证;闭环完整;spec 全量交付
- Cons: 单次改动较大(~12 files +8 测试),前后端混在一个 commit

**Option B: 后端先全量实现(A,B,C,D,E,G),前端面板(F)延后到独立 spec**
- 实现思路:后端 5 个新文件 + 6 个测试,前端面板拆分到 m4-eval-frontend-panel spec
- 改动文件:仅后端
- Pros: 单次改动小,前后端分离,测试聚焦
- Cons: AC-11 无法验证,需再起一个 spec;spec 已 ALIGNED 不应再拆分;违反 spec In scope F

**Invalidation rationale for Option B**:spec 已 ALIGNED 且 In scope 明确包含 F 段,延后违反 spec 合同。AC-11 是 spec 强制项,延后会让本次 dev-tdd 无法闭环。选 Option A。

### Implementation steps (基于 Option A)

1. 新建 `backend/private_agent/eval/version_compare.py` — `EvalComparator` + `_compute_diff` + `InsufficientDataError`
   - `compare_versions(skill_name, base_version, target_version, model_id=None)`:list_runs 双版本 + 取最新 completed → diff
   - `_compute_diff(base, target)`:遍历 categories → metrics,delta = target - base,status improved/degraded/stable
2. 新建 `backend/private_agent/eval/rollback.py` — `SkillRollbackManager` + `VersionNotFoundError`
   - `rollback_prompt(skill_name, target_version, conn)`:snapshot_repo.get(scope="prompt", version=target_version) → 更新 latest_prompt_version 指针(暂用 config_runtime 表 key=`skill.{name}.latest_prompt_version`)
   - `rollback_skill(skill_name, target_version, conn)`:snapshot_repo.get(scope="skill", version) → UPDATE skills 表 version + UPDATE config_runtime key=`skill.{name}.latest_version`
   - `rollback_harness(target_commit)`:返回 `{command: f"git revert {target_commit}", note: "手动执行后重启 Sidecar"}`
3. 新建 `backend/private_agent/api/eval.py` — 6 个端点
   - `POST /admin/eval/runs` 触发评估(后台任务,返回 run_id)
   - `GET /admin/eval/runs` 列表
   - `GET /admin/eval/runs/{run_id}` 详情
   - `GET /admin/eval/datasets` 数据集列表
   - `GET /admin/eval/versions/compare` 版本对比
   - `POST /admin/eval/rollback` 回滚
   - Pydantic 请求体:`EvalRunRequest` / `RollbackRequest`
4. 扩展 `backend/private_agent/api/admin.py` — `POST /admin/skills/{name}/save-version` 端点
   - 保存新版本到 version_snapshots(scope="skill")
   - 调用 `SkillVersionListener.on_skill_version_saved`(失败仅日志,不阻塞)
   - Pydantic 请求体:`SaveVersionRequest`(manifest + system_prompt + tools_yaml + version)
5. 扩展 `backend/private_agent/main.py:20` — `app.include_router(eval.router)`
6. 扩展 `backend/private_agent/skills/manager.py` — `activate_skill` 检查 `config_runtime` 中 `skill.{name}.latest_version` 指针,若与请求版本不一致则用 latest_version 覆盖(回滚后新会话加载旧版本的核心机制)
7. 扩展 `frontend/renderer/App.tsx` — 嵌入 `EvalPanel` 组件
   - Props: `sessionId` / `onTriggerEval`
   - State: `runs[]` / `compareResult` / `loading`
   - 子组件:`RunsTable` / `TrendChart`(SVG 折线)/ `CompareTable` / `DegradedBadge`
8. 扩展 `frontend/static/chat.html` — 评估面板入口按钮 + 容器 div
9. 新建 `backend/tests/test_eval_version_compare.py` — AC-1, AC-2, AC-6
   - `compare_versions` 双维度筛选 + 取最新 + 缺数据抛 InsufficientDataError
   - `_compute_diff` 标记 improved/degraded/stable
   - 退化时 diff 含 degraded 标记(AC-6 后端部分)
10. 新建 `backend/tests/test_eval_rollback.py` — AC-3, AC-4, AC-5
    - `rollback_prompt` 仅更新 prompt 指针,工具白名单不变
    - `rollback_skill` 更新 skills 表 version + config_runtime latest_version
    - `rollback_harness` 返回 git revert 命令,不执行
11. 新建 `backend/tests/test_eval_api.py` — AC-8, AC-9, AC-10
    - `POST /admin/eval/runs` 返回 run_id
    - `GET /admin/eval/versions/compare` 返回 diff
    - `POST /admin/eval/rollback` 返回 rolled_back_to + affected_sessions
12. 新建 `backend/tests/test_eval_api_save_version.py` — AC-12
    - `POST /admin/skills/{name}/save-version` 保存 + 触发 listener
    - listener 失败仅日志,不阻塞
13. 新建 `backend/tests/test_skill_manager_rollback.py` — AC-4 后半
    - `activate_skill` 读取 latest_version 指针,回滚后新会话加载旧版本
    - 运行中会话(locked_skill_version 已设)维持锁定
14. 新建 `backend/tests/test_eval_e2e_version_flow.py` — AC-1..AC-7 闭环
    - 评估运行 v1.0.0 → 修改样本 → 评估运行 v1.1.0 → 版本对比 → 退化检测 → 回滚 v1.0.0 → 新会话加载 v1.0.0
15. 新建 `backend/tests/test_eval_frontend_panel.py` — AC-11 前端测试
    - 用 pytest 检查 App.tsx 中 EvalPanel 组件存在
    - 用 playwright 或简单 grep 验证 chat.html 含评估面板入口
    - (前端单元测试基础设施缺失,MVP 用静态检查 + grep 验证)

### Workspace setup

- Run `git status --short` and `git branch --show-current` before implementation.
- 当前在 master 分支,working tree 含历史 dirty 文件(config/loader.py + code_execution.py 的 CRLF/LF)
- 这些 dirty 文件与本次 plan 无关,不混入 commit
- 不创建 worktree(项目惯例:前序 spec 都直接提交 master)

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| `EvalRunRepo.list_runs` 返回空列表时 `compare_versions` 抛 InsufficientDataError,但测试数据集可能为空 | 测试中插入 mock eval_runs 行,确保有 completed run |
| `rollback_skill` 更新 skills 表 version 后,内存中 SkillLoader 缓存可能不一致 | SkillLoader 无内存缓存(每次 load 从文件系统读),回滚后下次 load 自动读到新 version |
| `config_runtime` 表的 `skill.{name}.latest_version` key 是新约定,需与 SkillManager.activate_skill 同步 | 在 SkillManager.activate_skill 中显式读取该 key,不存在时 fallback 到 skills 表 version |
| 前端 EvalPanel 用 SVG 折线图,若版本数据点 < 2 时图表空白 | 显示 "Insufficient data" 提示 |
| `POST /admin/eval/runs` 同步执行会阻塞 HTTP 请求(EvalRunner 可能跑数十秒) | MVP 同步执行 + 超时返回 run_id(单人开发场景可接受);V2 改为 asyncio.create_task 后台执行 |
| `save-version` 端点调用 SkillVersionListener 失败时,版本已保存但回归未触发 | listener 内部已 try/except + 日志(AC-10 m4-eval-runner-replay 已实现),不阻塞版本保存 |
| 退化告警 UI 标记需要前端识别 diff.degraded | diff 结构中每条 metric 含 status 字段,前端过滤 status="degraded" 显示红色 badge |

## Verification steps

- 验证 AC-1:`pytest tests/test_eval_version_compare.py::test_compare_versions_selects_latest_completed`
- 验证 AC-2:`pytest tests/test_eval_version_compare.py::test_compute_diff_marks_status`
- 验证 AC-3:`pytest tests/test_eval_rollback.py::test_rollback_prompt_only`
- 验证 AC-4:`pytest tests/test_eval_rollback.py::test_rollback_skill_updates_version` + `tests/test_skill_manager_rollback.py::test_activate_skill_uses_latest_version`
- 验证 AC-5:`pytest tests/test_eval_rollback.py::test_rollback_harness_returns_command`
- 验证 AC-6:`pytest tests/test_eval_version_compare.py::test_degradation_marked_in_diff`
- 验证 AC-7:`pytest tests/test_eval_api.py::test_variant_field_default_null`
- 验证 AC-8:`pytest tests/test_eval_api.py::test_trigger_eval_run`
- 验证 AC-9:`pytest tests/test_eval_api.py::test_compare_versions_endpoint`
- 验证 AC-10:`pytest tests/test_eval_api.py::test_rollback_endpoint`
- 验证 AC-11:`pytest tests/test_eval_frontend_panel.py`(静态检查 + grep)
- 验证 AC-12:`pytest tests/test_eval_api_save_version.py::test_save_version_triggers_listener`
- 闭环验证:`pytest tests/test_eval_e2e_version_flow.py`(全 7 AC 端到端)

## ADR

- **Decision**: Option A 全量实现 spec A-G 段,前端面板用纯 SVG,API 端点统一在 /admin/eval,回滚指针用 config_runtime 表 key=`skill.{name}.latest_version`
- **Drivers**: 上线速度(M4 最后一环)+ spec ALIGNED 不应拆分 + AC-11 强制前端面板 + 单人开发降低依赖
- **Alternatives considered**:
  - Option A(chosen):全量实现,前后端一个 commit — rationale:spec ALIGNED + AC-11 强制
  - Option B(rejected):后端先 + 前端延后 — rationale:违反 spec In scope F + AC-11 无法验证
- **Why chosen**: spec 已 ALIGNED,In scope F 明确要求前端面板;延后会破坏 dev-tdd 闭环;Option A 的 cons(单次改动大)在单人开发场景可接受
- **Consequences**:
  - 正面:M4 评估闭环后端 + 前端一次性交付,AC-1..AC-12 全量可验证
  - 负面:单次 commit 较大(~15 files +8 测试),review 需分轴检查
  - 对其他模块:SkillManager.activate_skill 需读取 config_runtime latest_version 指针(小改动)
- **Follow-ups**:
  - V2: 前端引入 chart.js 替换 SVG 折线图(更丰富的交互)
  - V2: POST /admin/eval/runs 改为后台任务(asyncio.create_task)
  - V2: ab_tests 表 + 流量分配
  - V2: 回滚前自动备份当前版本

## Review trail

- Planner draft v1: Option A vs B 对比,倾向 A
- Architect challenge v1: 质疑 Option A 单次改动过大,前端测试基础设施缺失;tradeoff: 速度 vs review 复杂度
- Critic verdict v1: REVISE — (1) AC-11 前端测试方式不明确;(2) `rollback_skill` 如何让新会话加载旧版本未说清;(3) `save-version` 端点请求体结构未定义
- Planner draft v2: 修复 3 点 — (1) 前端测试用静态检查 + grep;(2) `rollback_skill` 更新 config_runtime latest_version + SkillManager.activate_skill 读取该 key;(3) `SaveVersionRequest` 定义 manifest + system_prompt + tools_yaml + version
- Architect challenge v2: 同意 v2 修复,tradeoff tension: config_runtime key 约定是隐式合同,无 schema 约束
- Critic verdict v2: APPROVED with 1 reservation
- Final iterations: 2 / 3

## Critic reservations

1. **config_runtime key 约定是隐式合同**:`skill.{name}.latest_version` 是新约定的 key,无 DB schema 约束,若后续 SkillManager.activate_skill 重构可能漏读该 key 导致回滚失效。Mitigation:在 SkillManager.activate_skill 中显式读取 + 不存在时 fallback 到 skills 表 version;在 ADR Follow-ups 标注 V2 可考虑加 DB 列 `skills.latest_version` 替代 config_runtime key。
