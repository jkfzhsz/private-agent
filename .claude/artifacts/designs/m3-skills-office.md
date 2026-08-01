# M3 Skills Framework + Office Scenario Spec

> Status: ALIGNED (spec drift 修正 v2,2026-08-01)
> Author: zongxin
> Last updated: 2026-08-01
>
> **Spec drift 修正记录**(基于 dev-plan Critic reservations):
> - In scope A「跨 Skill 权限缓存隔离」→ 改为「safety_level_override 元数据校验」(`tools/authorizer.py` 不存在,M2 §5.12 未实现)
> - In scope B「网页浏览(沙箱抓取)」→ 降级为「网页搜索摘要(web_search only)」(沙箱 `network_enabled=false` 全局关闭)
> - AC-10 同步修正为元数据校验;AC-3 工具名 `get_current_time` → `datetime`;AC-7 P0.1 修复点补 admin.py

## Background

M2 完成 RAG 知识库(第 4 章)、工具层双轨架构(第 5 章)、Python 沙箱(第 6 章),443 测试全过。M3 启动场景 Skills(蓝图第 7 章)。本 spec 切出 M3 第一刀:**Skills 框架(后端)+ 办公场景 Skill + P0.1 compress_adapter 修复**。其余 M3 项(数据分析/前端场景、P0.2 JS 沙箱、P0.3 流式输出、P0.4 UI 配置面板、P1.11 MCP 协议升级、UI 选择页)各自独立 spec,避免单一 delivery 过大。

蓝图第 7 章已对目录结构、skill.yaml schema、版本管理、加载激活、工具白名单、Prompt 框架、少样本机制、办公场景做出详细设计决策,本 spec 在蓝图框架内落地,不重新决策已定项。

## In scope

### A. Skills 框架(后端)
- **skill.yaml schema 解析 + 校验**:name 全局唯一 / version semver / dependencies.tools 必须在通用工具集或已加载 MCP 中存在 / safety_level_override ∈ {safe, elevated, dangerous}
- **SkillLoader**:PG 优先(`skills.storage.runtime_source: "db_first"`)+ 文件系统回退(`./skills/{name}/`);PG 无该 skill 但文件系统存在时回退成功
- **SkillManager.activate_skill**:模板变量替换(3.7)→ 少样本注入 → 工具白名单加载 → Frozen Zone 构建 → frozen_hash 计算 → sessions 锁定
- **会话锁定**:`sessions` 表迁移加 `locked_skill_name` / `locked_skill_version` / `frozen_hash` 三列(默认 NULL,不破坏现有数据);运行中重复 activate 不同 skill → 拒绝(409)
- **工具白名单 enforcement**:`ToolRegistry` 增加 per-session 过滤能力;ReactLoop 构造时取 session 过滤后的 tools 而非全局 `list_tools()`
- **safety_level_override 元数据校验**:manifest 中 `dependencies.tools[].safety_level_override` 校验枚举值 ∈ {safe, elevated, dangerous, None};实际 enforcement 延后至权限确认机制实现时(M2 §5.12 P1 缺口,`tools/authorizer.py` 未实现,独立 spec)
- **少样本加载**:`examples/*.md` 注入 Frozen Zone,token 预算 ≤ `max_frozen_token`(4000),超限自动减少示例数量

### B. 办公场景 Skill 内容
- `skills/office/` 目录:`skill.yaml` + `system_prompt.md`(四段式框架)+ `tools.yaml` + `examples/`(2-3 个 md)
- 覆盖文档处理(蓝图 7.9:Excel/Word via openpyxl/python-docx)+ 网页搜索摘要(蓝图 7.10 降级:仅 `web_search`,沙箱抓取延后至独立 spec,因沙箱 `network_enabled=false` 全局关闭)
- 沙箱依赖:`pandas`/`openpyxl`/`python-docx`/`matplotlib` 加入 `pyproject.toml` 可选依赖组 `[office]`(`beautifulsoup4`/`requests` 随沙箱抓取延后)

### C. P0.1 compress_adapter 修复
- 按 `config.yaml` `models.compress_model`(`glm-4-flash`)构造单 provider 适配器
- `main.py` `user_message` 处理处将 `compress_adapter` 注入 `MemoryManager`(替换当前 `compress_adapter=None`)
- 记忆提取端到端可用:`maybe_extract` / `on_session_end` / `manual_extract` 实际触发 LLM 调用
- 向后兼容:无 `compress_adapter` 时仍返回 `[]`(测试环境不破坏)

### D. admin API
- `GET /admin/skills` → 已加载 Skills 列表 `[{name, version, description, enabled}]`
- `GET /admin/skills/{name}` → Skill 详情(manifest + system_prompt 预览 + tools 白名单)
- `POST /admin/sessions/{id}/activate` `{"skill_name": "office"}` → `{locked_version, frozen_hash}`

## Out of scope

- 数据分析场景 Skill(独立 spec)
- 前端设计场景 Skill + P0.2 JavaScript 沙箱(独立 spec,JS 沙箱随前端场景)
- P0.3 沙箱流式输出(独立 spec)
- P0.4 沙箱 UI 配置面板(独立 spec)
- P1.11 MCP 2026-07-28 协议升级(独立 spec)
- UI Skill 选择页(Electron renderer,独立 spec)
- `version_snapshots` 写入与 UI 回滚(蓝图 7.3 该部分延后;本 spec 仅从 PG 读取 skill,不写快照不回滚)
- Skill 语义自动路由 / 多 Skill 并行 / 会话中途切换(V2)
- 办公场景 V2:日历 / 邮件 / IM / 任务看板 MCP(蓝图 7.8 V2)
- 网页浏览沙箱抓取(蓝图 7.10 完整版,需沙箱 network 白名单机制,独立 spec)
- 权限确认机制(蓝图 §5.12,M2 P1 缺口,`tools/authorizer.py` 未实现,独立 spec)
- 评估闭环(M4,第 8 章)

## Assumptions

- 沙箱执行 office 代码时,`pandas`/`openpyxl`/`python-docx`/`matplotlib` 已在 sidecar Python 环境安装(本 spec 通过 `pyproject.toml` 可选依赖组 `[office]` 保证);`beautifulsoup4`/`requests` 随网页抓取延后
- `compress_model`(`glm-4-flash`)的 API key 复用现有 GLM provider 配置,已就绪
- Frozen Zone 构建复用现有 `ContextManager` 机制(M1 已实现 `ensure_initial` / Frozen Zone 概念)
- `sessions` 表迁移(加列 NULL 默认)不破坏现有数据,迁移脚本走 `storage/migrations.py` 既有机制
- 工具白名单 enforcement 在 ReactLoop 构造时按 session 过滤 tools,不改动 `ToolRegistry` 注册逻辑(只加查询过滤)
- 蓝图 7.5 工具白名单矩阵为本 spec office skill.yaml 的 tools.yaml 依据
- 沙箱 `network_enabled=false` 全局关闭,办公网页浏览仅用 `web_search` 摘要,沙箱抓取(requests+bs4)延后至沙箱 network 白名单机制(独立 spec)
- P0.1 compress_adapter 修复点为**两处**:`main.py` user_message 处理 + `admin.py` extract_memory 处理(均当前 `compress_adapter=None`)

## Solution

### 模块布局

```
backend/private_agent/skills/
├── __init__.py
├── models.py          # Skill / SkillManifest / ToolDependency Pydantic schema
├── loader.py          # SkillLoader(PG db_first + 文件回退)
├── manager.py         # SkillManager.activate_skill(模板+少样本+白名单+锁定)
├── example_loader.py  # ExampleLoader(examples/*.md + token 预算)
└── errors.py          # SkillNotFoundError / SkillSwitchNotAllowedError / SkillValidationError

skills/office/         # 开发期 Git 管理(非 backend 包内,workspace_root 下)
├── skill.yaml
├── system_prompt.md
├── tools.yaml
└── examples/
    ├── excel_summary.md
    └── web_research.md
```

### 关键流程

**activate_skill(skill_name, session_id):**
1. `SkillLoader.load(skill_name)` → PG `skills` 表(优先)→ 回退 `./skills/{name}/skill.yaml`
2. 校验 manifest(schema + 工具存在性 + safety_level 枚举)
3. 模板变量替换(3.7):`{{user.name}}` / `{{now}}` / `{{session.id}}` / `{{session.created_at}}` / `{{skills.active}}` / `{{skills.tools}}`
4. `ExampleLoader.load(name, max=3)` 拼接 prompt + examples(token ≤ `max_frozen_token`)
5. 工具白名单加载:按 `skill.dependencies.tools` 过滤出 session 工具集
6. `ContextManager.build_frozen_zone(system_prompt + tools)` → 计算 `frozen_hash`
7. `sessions` UPDATE `locked_skill_name` / `locked_skill_version` / `frozen_hash`

**工具白名单 enforcement:**
- `ToolRegistry` 增加 `list_tools_for_session(session_id, whitelist: list[str])` 过滤方法
- `ReactLoop` 构造时传入 session 过滤后的 tools(而非全局 `list_tools()`)
- 权限缓存 `cache_key = sha256(f"{skill_name}::{tool_name}::{args_json}")`

**P0.1 compress_adapter:**
- `models/registry.py` 增加 `build_compress_adapter(cfg)` → 单 GLM 适配器(model = `cfg.models.compress_model`)
- `main.py` `user_message` 处理:`_build_compress_adapter(cfg)` 注入 `MemoryManager(compress_adapter=...)`

### admin API
- `GET /admin/skills` → `[{name, version, description, enabled}]`
- `GET /admin/skills/{name}` → `{manifest, system_prompt(预览前 500 字), tools}`
- `POST /admin/sessions/{id}/activate` `{"skill_name": "office"}` → `{locked_version, frozen_hash}`

## Edge cases & risks

| Category | Notes |
|---|---|
| Boundary | skill.yaml 不存在/格式错 → `SkillNotFoundError` / `SkillValidationError`;PG 与文件系统版本不一致 → db_first 取 PG;工具白名单引用不存在的工具 → 加载期校验失败返回 400 |
| Failure | compress_adapter 调用失败 → `MemoryManager` 捕获异常返回空列表(不阻塞会话,沿用现有 `_extract_memories` 容错);Frozen Zone token 超限 → 截断 examples(优先保留最具代表性的) |
| Risks | sessions 表迁移需在已有数据上执行(加列 NULL 默认,安全);office 沙箱依赖未装 → `code_execution` 报 `ModuleNotFoundError`,需 `pyproject.toml` + 文档说明;db_first 加载但 PG 无该 skill 而文件系统有 → 回退逻辑必须覆盖 |
| Mitigation | 迁移用 `ALTER TABLE ... ADD COLUMN ... DEFAULT NULL`;office 依赖用 `pyproject.toml` `[project.optional-dependencies] office = [...]`;回退逻辑用 `try PG → except → filesystem` |

## Acceptance criteria

- **AC-1**:`POST /admin/sessions/{id}/activate {"skill_name":"office"}` 返回 200 + `locked_version="1.0.0"` + `frozen_hash`(64 位 hex);`sessions` 表对应行 `locked_skill_name="office"`
- **AC-2**:`GET /admin/skills` 返回列表含 `office` 项;`GET /admin/skills/office` 返回 manifest 含 tools 白名单
- **AC-3**:activate 后,`ReactLoop.run_turn` 中 tools 列表仅含 office `skill.yaml` 声明的工具(`code_execution`/`file_read`/`file_write`/`web_search`/`search_knowledge`/`datetime`/`calculator`),不含 `http_request`(MVP 禁用)
- **AC-4**:同一 session 已 activate 后再次调用 `activate`(不同 skill)→ 返回 409 `SkillSwitchNotAllowedError`
- **AC-5**:`skill.yaml` 引用不存在的工具名(如 `"fake_tool"`)→ `activate` 返回 400 `SkillValidationError`
- **AC-6**:`examples` 总 token 超过 `max_frozen_token`(4000)→ 自动减少示例数量,`frozen_hash` 仍可计算
- **AC-7**(P0.1):`MemoryManager` 构造时 `compress_adapter` 非 None;`maybe_extract` 触发时实际调用 `glm-4-flash` 适配器(可用 mock 验证 `.chat` 被调用);无 `compress_adapter` 时仍返回 `[]`(向后兼容,现有测试不破坏);**两处**构造点(`main.py` user_message + `admin.py` extract_memory)均修复
- **AC-8**(办公端到端):activate office → 用户发"把 data/sales.xlsx 按地区汇总"→ Agent 调用 `file_read` + `code_execution`(pandas/openpyxl)→ 生成 `outputs/sales_summary.xlsx` → 回复含文件路径(沙箱依赖已装前提下)
- **AC-9**:PG 中无 office skill 但 `./skills/office/` 存在 → `SkillLoader` 回退文件系统成功加载
- **AC-10**(spec drift 修正):`safety_level_override` 作为 manifest 元数据保留并校验枚举值(∈ {safe, elevated, dangerous, None});实际 enforcement 延后至权限确认机制实现时(M2 §5.12 P1 缺口,`tools/authorizer.py` 未实现,独立 spec)

## Core entities (ontology)

| Entity | Type | Key fields | Relationship |
|---|---|---|---|
| Skill | aggregate | name, version, manifest, system_prompt, tools | 1 Skill → N ToolDependency |
| SkillManifest | value | name, version, description, scenario, dependencies, permissions, prompt_vars, knowledge_base, examples, max_frozen_token | Skill.manifest |
| ToolDependency | value | name, safety_level_override, enabled | SkillManifest.dependencies.tools[] |
| SkillLoader | service | — | loads Skill from PG / filesystem |
| SkillManager | service | — | activates Skill, locks to session |
| ExampleLoader | service | — | loads examples/*.md with token budget |
| Session | existing | + locked_skill_name, locked_skill_version, frozen_hash | 1 Session → 0..1 locked Skill |

## Interview metadata

- Mode: default
- Waves: 2
- Final ambiguity: 28%
- Status: PASSED

### Clarity breakdown

| Dimension | Score | Weight | Weighted |
|---|---|---|---|
| Goal | 0.85 | 0.40 | 0.34 |
| Scope | 0.75 | 0.25 | 0.1875 |
| AC | 0.40 | 0.25 | 0.10 |
| Context | 0.90 | 0.10 | 0.09 |
| Ambiguity | | | 28.25% |
