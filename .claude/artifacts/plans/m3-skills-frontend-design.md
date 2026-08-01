# m3-skills-frontend-design Implementation Plan

> Status: APPROVED
> Source: spec/m3-skills-frontend-design
> Mode: --quick
> Author: user
> Last updated: 2026-08-01

## Requirements summary

复用 M3 Skills 框架,新增前端设计场景 Skill 配置内容(skill.yaml/system_prompt.md/tools.yaml/2 examples)+ pyproject [frontend_design] 依赖 + E2E 测试。无框架改动。

## Acceptance criteria

(继承 spec AC-1 ~ AC-8,详见 .claude/artifacts/designs/m3-skills-frontend-design.md)

## Implementation steps

1. 创建 `backend/skills/frontend_design/skill.yaml` — manifest(name="frontend_design", version="1.0.0", 6 工具白名单,permissions/knowledge_base/examples/max_frozen_token)
2. 创建 `backend/skills/frontend_design/system_prompt.md` — 四段式框架(基于蓝图 7.14)
3. 创建 `backend/skills/frontend_design/tools.yaml` — 与 skill.yaml dependencies.tools 一致(6 工具)
4. 创建 `backend/skills/frontend_design/examples/landing_page.md` — 产品落地页(蓝图 7023,search_knowledge RAG + code_execution + file_write)
5. 创建 `backend/skills/frontend_design/examples/react_component.md` — React 组件生成(设计系统 RAG + 组件化)
6. 修改 `backend/pyproject.toml` — `[project.optional-dependencies] frontend_design` 加 jinja2
7. 创建 `backend/tests/test_frontend_design_skill_e2e.py` — E2E 测试(activate + tools 过滤 + frozen_hash + yaml 矩阵)

## Workspace setup

- Run `git status --short` before implementation.
- 当前 master 分支干净(上一 commit 84917ba 已合并),无需 worktree。

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| web_search 引入外部参考偏离设计系统 | system_prompt 约束「web_search 仅灵感参考,最终遵循知识库设计系统」 |
| 沙箱 network=false 导致 CDN 引入失败 | system_prompt 约束「CDN 链接由用户手动下载,沙箱不联网」 |
| 工具名 datetime vs get_current_time 漂移 | 沿用 M2 实际工具名 datetime |

## Verification steps

- 验证 AC-1: `pytest tests/test_frontend_design_skill_e2e.py::TestFrontendDesignSkillE2E::test_frontend_design_skill_yaml_matches_blueprint_matrix`(SkillLoader.load 成功)
- 验证 AC-2/3/4: `pytest tests/test_frontend_design_skill_e2e.py::TestFrontendDesignSkillE2E::test_activate_frontend_design_filters_tools_and_writes_frozen_hash`(activate + tools + hash + prompt)
- 验证 AC-5/6: yaml 矩阵断言(6 工具 + permissions/kb/examples)
- 验证 AC-7/8: `pytest tests/test_admin_skills_query.py`(已有测试,加载 frontend_design 后列表含 frontend_design)
- 回归: `pytest tests/test_skills_*.py tests/test_admin_*.py tests/test_office_skill_e2e.py tests/test_data_analysis_skill_e2e.py tests/test_structure.py`

## Quick mode rationale

复用 M3 Skills 框架,单一实现路径(创建配置文件),无架构决策需 Architect/Critic 权衡。仿办公/数据分析场景 commit 模式,直接产出最小 plan。

## ADR

- **Decision**: 复用 M3 Skills 框架,前端设计场景仅新增配置内容,无框架改动
- **Drivers**: 框架已就绪(commit 13d6725 + 182a9c1 + 84917ba),蓝图 7.5 矩阵明确工具白名单,7.14 提供 prompt 框架
- **Alternatives considered**: 无(唯一可行路径)
- **Why chosen**: 最小代码原则,避免不必要的框架改动
- **Consequences**: 前端设计 skill 自动被 admin API 发现,无需额外注册
- **Follow-ups**: M3 三场景全部完成,后续可推进 P0.2 JS 沙箱 / P0.3 流式输出 / P0.4 UI 配置面板(各自独立 spec)
