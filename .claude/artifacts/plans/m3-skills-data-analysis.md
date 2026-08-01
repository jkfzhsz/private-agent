# m3-skills-data-analysis Implementation Plan

> Status: APPROVED
> Source: spec/m3-skills-data-analysis
> Mode: --quick
> Author: user
> Last updated: 2026-08-01

## Requirements summary

复用 M3 Skills 框架,新增数据分析场景 Skill 配置内容(skill.yaml/system_prompt.md/tools.yaml/2 examples)+ pyproject [data_analysis] 依赖 + E2E 测试。无框架改动。

## Acceptance criteria

(继承 spec AC-1 ~ AC-8,详见 .claude/artifacts/designs/m3-skills-data-analysis.md)

## Implementation steps

1. 创建 `backend/skills/data_analysis/skill.yaml` — manifest(name="data_analysis", version="1.0.0", 6 工具白名单,permissions/knowledge_base/examples/max_frozen_token)
2. 创建 `backend/skills/data_analysis/system_prompt.md` — 四段式框架(角色定位/任务约束/工具规范/输出格式)
3. 创建 `backend/skills/data_analysis/tools.yaml` — 与 skill.yaml dependencies.tools 一致(6 工具)
4. 创建 `backend/skills/data_analysis/examples/data_visualization.md` — CSV 清洗 + matplotlib 图表(蓝图 5811 工作流)
5. 创建 `backend/skills/data_analysis/examples/statistical_test.md` — scipy t 检验/相关性分析
6. 修改 `backend/pyproject.toml` — `[project.optional-dependencies] data_analysis` 加 scipy(复用 office 的 pandas/openpyxl/matplotlib)
7. 创建 `backend/tests/test_data_analysis_skill_e2e.py` — E2E 测试(activate + tools 过滤 + frozen_hash + yaml 矩阵 + admin API)

## Workspace setup

- Run `git status --short` before implementation.
- 当前 master 分支干净(上一 commit 182a9c1 已合并),无需 worktree。

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| scipy 依赖体积大 | E2E 不触发真实工具执行,仅验证 activate 流程,不依赖 scipy 运行时 |
| 工具名 datetime vs get_current_time 漂移 | 沿用 M2 实际工具名 datetime(蓝图 get_current_time 为别名) |

## Verification steps

- 验证 AC-1: `pytest tests/test_data_analysis_skill_e2e.py::TestDataAnalysisSkillE2E::test_office_skill_yaml_matches_blueprint_matrix`(SkillLoader.load 成功)
- 验证 AC-2/3/4: `pytest tests/test_data_analysis_skill_e2e.py::TestDataAnalysisSkillE2E::test_activate_data_analysis_filters_tools_and_writes_frozen_hash`(activate + tools + hash + prompt)
- 验证 AC-5/6: yaml 矩阵断言(6 工具 + permissions/kb/examples)
- 验证 AC-7/8: `pytest tests/test_admin_skills_query.py`(已有测试,加载 data_analysis 后列表含 data_analysis)
- 回归: `pytest tests/test_skills_*.py tests/test_admin_*.py tests/test_office_skill_e2e.py tests/test_structure.py`

## Quick mode rationale

复用 M3 Skills 框架,单一实现路径(创建配置文件),无架构决策需 Architect/Critic 权衡。仿办公场景 commit 182a9c1 模式,直接产出最小 plan。

## ADR

- **Decision**: 复用 M3 Skills 框架,数据分析场景仅新增配置内容,无框架改动
- **Drivers**: 框架已就绪(commit 13d6725 + 182a9c1),蓝图 7.5 矩阵明确工具白名单
- **Alternatives considered**: 无(唯一可行路径)
- **Why chosen**: 最小代码原则,避免不必要的框架改动
- **Consequences**: 数据分析 skill 自动被 admin API 发现,无需额外注册
- **Follow-ups**: 前端设计场景 Skill(独立 spec)
