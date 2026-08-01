# m3-skills-data-analysis Spec

> Status: ALIGNED
> Author: user
> Last updated: 2026-08-01

## Background

M3 阶段已完成 Skills 框架(SkillLoader/Manager/ExampleLoader + admin API + main.py 过滤)与办公场景 Skill(commit 13d6725 + 182a9c1)。本 spec 切 M3 第二刀:**数据分析场景 Skill**,复用现有框架,仅新增 skill 配置内容 + pyproject 依赖 + E2E 测试。蓝图第 7 章优先级:办公 → 数据分析 → 前端设计。

## In scope

- `backend/skills/data_analysis/skill.yaml` — manifest(6 工具白名单,无 web_search/http_request)
- `backend/skills/data_analysis/system_prompt.md` — 四段式框架(角色定位/任务约束/工具规范/输出格式)
- `backend/skills/data_analysis/tools.yaml` — 与 skill.yaml dependencies.tools 一致
- `backend/skills/data_analysis/examples/data_visualization.md` — CSV 清洗 + matplotlib 图表生成(蓝图 5811 工作流)
- `backend/skills/data_analysis/examples/statistical_test.md` — scipy 统计检验(t 检验/相关性)
- `backend/pyproject.toml` — `[project.optional-dependencies] data_analysis` 加 scipy
- `backend/tests/test_data_analysis_skill_e2e.py` — E2E 测试(activate + tools 过滤 + frozen_hash + yaml 矩阵)

## Out of scope

- Skills 框架改动(M3 已完成,复用 SkillLoader/Manager/ExampleLoader/admin API/main.py 过滤)
- web_search 工具(蓝图 7.5 矩阵:数据分析 web_search=✗)
- http_request 工具(蓝图 7.5 矩阵:数据分析 http_request=✗,V2)
- MCP servers(postgres-mcp/excel-mcp,蓝图 3839 示例含但属 V2)
- db_query / shell_exec(蓝图 7.5 标注 V2)
- UI 配置面板(P0.4,独立 spec)
- 前端设计场景 Skill(独立 spec)

## Assumptions

- SkillLoader.list_all / load 已支持文件系统回退(扫 dev_dir/*/skill.yaml),无需改动
- SkillManager.activate_skill 已实现全流程(load→validate→lock check→template→examples→whitelist→frozen hash→lock),无需改动
- admin API(GET /admin/skills + GET /admin/skills/{name} + POST /admin/sessions/{id}/activate)已实现,自动发现新 skill
- main._get_tools 已支持 locked_skill 过滤,无需改动
- 工具名沿用 M2 实现:datetime(蓝图叫 get_current_time,实际工具名为 datetime)
- sandbox network_enabled=false,数据分析专注本地数据,无网络需求

## Solution

创建 `backend/skills/data_analysis/` 目录,结构仿办公场景:

```
data_analysis/
├── skill.yaml          # manifest: 6 工具白名单,permissions/knowledge_base/examples
├── system_prompt.md    # 四段式框架,聚焦数据分析
├── tools.yaml          # 与 skill.yaml dependencies.tools 一致
└── examples/
    ├── data_visualization.md   # CSV 清洗 + matplotlib 图表
    └── statistical_test.md     # scipy t 检验/相关性
```

**工具白名单**(蓝图 7.5 矩阵):

| 工具 | enabled | safety_level_override |
|---|---|---|
| code_execution | true | elevated |
| file_read | true | safe |
| file_write | true | elevated |
| search_knowledge | true | safe |
| datetime | true | safe |
| calculator | true | safe |

**system_prompt 四段式框架**(蓝图 7.6):
1. 角色定位:数据分析助手,处理 CSV/Excel/pandas,生成图表与统计分析
2. 任务约束:数据大小限制、敏感数据脱敏、来源标注、中文输出
3. 工具使用规范:file_read→code_execution→file_write 流程,统计检验用 scipy,可视化用 matplotlib
4. 输出格式:图表类输出文件路径+摘要,统计类输出检验结果+结论

**E2E 测试**断言:
- activate data_analysis → 返回 locked_version=1.0.0 + frozen_hash(64 hex)
- tools 过滤:6 工具,无 web_search/http_request
- sessions 表写入锁定字段
- system_prompt 含四段式关键字
- yaml 矩阵匹配蓝图 7.5

## Edge cases & risks

| Category | Notes |
|---|---|
| Boundary conditions | scipy 未安装时 code_execution 执行统计检验会失败 → pyproject [data_analysis] 声明 scipy,但实际安装由用户 `pip install -e .[data_analysis]` 触发 |
| Failure modes | 沙箱 network=false,若数据分析代码尝试联网下载数据集会失败 → system_prompt 约束「仅处理用户上传的本地数据」 |
| Risks | scipy 依赖体积较大(~50MB),可能影响安装速度 → 可接受,数据分析场景必需 |
| Mitigation | E2E 测试不触发真实工具执行(仅验证 activate 流程),不依赖 scipy 运行时 |

## Acceptance criteria

- AC-1: `SkillLoader.load("data_analysis")` 成功返回 Skill,manifest.version="1.0.0",manifest.name="data_analysis"
- AC-2: `SkillManager.activate_skill("data_analysis", session_id, conn)` 返回 `{locked_version: "1.0.0", frozen_hash: <64 hex>, filtered_tools, system_prompt}`,sessions 表写入 locked_skill_name="data_analysis" + frozen_hash
- AC-3: filtered_tools 恰好 6 个:{code_execution, file_read, file_write, search_knowledge, datetime, calculator},不含 web_search/http_request
- AC-4: system_prompt 含「数据分析」「工具」「示例」关键字(四段式框架 + examples 注入)
- AC-5: skill.yaml dependencies.tools 匹配蓝图 7.5 矩阵(6 工具 enabled=true,无 web_search/http_request 条目)
- AC-6: permissions.allow_file_write=true,knowledge_base.scenario="data_analysis",examples.max_examples=2,max_frozen_token=4000
- AC-7: `GET /admin/skills` 返回列表含 data_analysis 条目
- AC-8: `GET /admin/skills/data_analysis` 返回详情(system_prompt_preview ≤ 500 字 + tools 列表)

## Open questions

无。

## Core entities (ontology)

| Entity | Type | Key fields | Relationship |
|---|---|---|---|
| data_analysis skill | Skill | name="data_analysis", version="1.0.0" | 复用 SkillManifest schema |
| examples | list[Example] | data_visualization, statistical_test | 注入 frozen_zone |

## Interview metadata

- Mode: --quick
- Waves: 1
- Final ambiguity: <30%(蓝图 7.5 矩阵 + 7.6 框架 + 5811 工作流已明确,用户确认 examples 方向)
- Status: PASSED
