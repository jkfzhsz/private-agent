# m3-skills-frontend-design Spec

> Status: ALIGNED
> Author: user
> Last updated: 2026-08-01

## Background

M3 阶段已完成 Skills 框架 + 办公场景(commit 13d6725 + 182a9c1)+ 数据分析场景(commit 84917ba)。本 spec 切 M3 第三刀:**前端设计场景 Skill**,复用现有框架,仅新增 skill 配置内容 + pyproject 依赖 + E2E 测试。蓝图第 7 章优先级:办公 → 数据分析 → 前端设计,本 spec 完成三场景最后一块。

## In scope

- `backend/skills/frontend_design/skill.yaml` — manifest(6 工具白名单,无 calculator/http_request)
- `backend/skills/frontend_design/system_prompt.md` — 四段式框架(角色定位/任务约束/工具规范/输出格式),基于蓝图 7.14
- `backend/skills/frontend_design/tools.yaml` — 与 skill.yaml dependencies.tools 一致
- `backend/skills/frontend_design/examples/landing_page.md` — 产品落地页(蓝图 7023,search_knowledge RAG + code_execution + file_write)
- `backend/skills/frontend_design/examples/react_component.md` — React 组件生成(设计系统 RAG + 组件化)
- `backend/pyproject.toml` — `[project.optional-dependencies] frontend_design` 加 jinja2(模板生成)
- `backend/tests/test_frontend_design_skill_e2e.py` — E2E 测试(activate + tools 过滤 + frozen_hash + yaml 矩阵)

## Out of scope

- Skills 框架改动(M3 已完成,复用 SkillLoader/Manager/ExampleLoader/admin API/main.py 过滤)
- calculator 工具(蓝图 7.5 矩阵:前端设计 calculator=✗)
- http_request 工具(蓝图 7.5 矩阵:V2)
- 浏览器预览(Electron Webview,V2,蓝图 7.13)
- Figma MCP(蓝图 7.13,V2)
- 截图工具(Playwright,V2)
- 组件库集成(Ant Design/Element,V2,蓝图 7.14)
- 设计稿转代码(Figma MCP,V2)
- UI 配置面板(P0.4,独立 spec)

## Assumptions

- SkillLoader.list_all / load 已支持文件系统回退,无需改动
- SkillManager.activate_skill 已实现全流程,无需改动
- admin API 已实现,自动发现新 skill
- main._get_tools 已支持 locked_skill 过滤,无需改动
- 工具名沿用 M2 实现:datetime(蓝图叫 get_current_time,实际工具名为 datetime)
- sandbox network_enabled=false,但 web_search 在 M2 实现中走独立 HTTP 通道(不经过沙箱),前端设计场景可用 web_search 检索设计灵感
- 设计系统 RAG 通过 search_knowledge(scenario=frontend_design)检索,知识库内容由用户单独上传

## Solution

创建 `backend/skills/frontend_design/` 目录,结构仿办公/数据分析场景:

```
frontend_design/
├── skill.yaml          # manifest: 6 工具白名单,permissions/knowledge_base/examples
├── system_prompt.md    # 四段式框架,基于蓝图 7.14
├── tools.yaml          # 与 skill.yaml dependencies.tools 一致
└── examples/
    ├── landing_page.md     # 产品落地页(蓝图 7023)
    └── react_component.md  # React 组件生成
```

**工具白名单**(蓝图 7.5 矩阵):

| 工具 | enabled | safety_level_override |
|---|---|---|
| code_execution | true | elevated |
| file_read | true | safe |
| file_write | true | elevated |
| web_search | true | safe |
| search_knowledge | true | safe |
| datetime | true | safe |

**system_prompt 四段式框架**(蓝图 7.14):
1. 角色定位:前端设计助手,擅长生成符合设计规范的 UI 代码(HTML/CSS/JS/React/Vue)
2. 任务约束:支持框架、设计规范遵循、响应式适配、代码质量
3. 工具使用规范:search_knowledge 检索设计系统 → code_execution 生成 → file_write 保存 → file_read 确认
4. 输出格式:代码文件路径 + 结构说明 + 预览方式提示

**E2E 测试**断言:
- activate frontend_design → 返回 locked_version=1.0.0 + frozen_hash(64 hex)
- tools 过滤:6 工具,无 calculator/http_request
- sessions 表写入锁定字段
- system_prompt 含四段式关键字
- yaml 矩阵匹配蓝图 7.5

## Edge cases & risks

| Category | Notes |
|---|---|
| Boundary conditions | 设计系统知识库未上传时 search_knowledge 返回空 → system_prompt 约束「检索为空时使用通用设计规范」 |
| Failure modes | 沙箱 network=false,若代码尝试 CDN 引入 React/Vue 会失败 → system_prompt 约束「使用本地构建或 CDN 链接由用户手动下载」 |
| Risks | web_search 在前端设计场景可能引入外部设计参考,与「遵循设计系统」冲突 → system_prompt 约束「web_search 仅用于灵感参考,最终代码必须遵循知识库设计系统」 |
| Mitigation | E2E 测试不触发真实工具执行(仅验证 activate 流程),不依赖运行时 |

## Acceptance criteria

- AC-1: `SkillLoader.load("frontend_design")` 成功返回 Skill,manifest.version="1.0.0",manifest.name="frontend_design"
- AC-2: `SkillManager.activate_skill("frontend_design", session_id, conn)` 返回 `{locked_version: "1.0.0", frozen_hash: <64 hex>, filtered_tools, system_prompt}`,sessions 表写入 locked_skill_name="frontend_design" + frozen_hash
- AC-3: filtered_tools 恰好 6 个:{code_execution, file_read, file_write, web_search, search_knowledge, datetime},不含 calculator/http_request
- AC-4: system_prompt 含「前端设计」「工具」「示例」关键字(四段式框架 + examples 注入)
- AC-5: skill.yaml dependencies.tools 匹配蓝图 7.5 矩阵(6 工具 enabled=true,无 calculator/http_request 条目)
- AC-6: permissions.allow_file_write=true,knowledge_base.scenario="frontend_design",examples.max_examples=2,max_frozen_token=4000
- AC-7: `GET /admin/skills` 返回列表含 frontend_design 条目
- AC-8: `GET /admin/skills/frontend_design` 返回详情(system_prompt_preview ≤ 500 字 + tools 列表)

## Open questions

无。

## Core entities (ontology)

| Entity | Type | Key fields | Relationship |
|---|---|---|---|
| frontend_design skill | Skill | name="frontend_design", version="1.0.0" | 复用 SkillManifest schema |
| examples | list[Example] | landing_page, react_component | 注入 frozen_zone |

## Interview metadata

- Mode: --quick
- Waves: 1
- Final ambiguity: <30%(蓝图 7.13-7.14 + 7.5 矩阵 + 7023 示例已明确,用户确认 examples 方向)
- Status: PASSED
