# M3 剩余 Done Criteria 补齐 Implementation Plan

> Status: APPROVED
> Source: spec/m3-remaining-done-criteria
> Mode: default
> Iterations: 1 / 3
> Author: user
> Last updated: 2026-08-01

## Requirements summary

补齐 M3 蓝图 9.4 Done Criteria 中 4 项未实现标准：file_read 工具增强（max_lines + 大文件拒绝 + 截断 artifact）、权限缓存 cache_key 函数（含 skill_name）、examples train/ 子目录迁移、数据分析前端预览卡片（HTTP 端点 + PreviewCard 组件）。

## Acceptance criteria

- AC-1: file_read 支持 `max_lines` 参数（default 1000），超过时返回前 max_lines 行 + 截断提示
- AC-2: file_read 文件大小 > `max_file_size_mb` 时拒绝读取，返回错误提示
- AC-3: file_read 读取结果 > 4000 token 时截断 + 写入 artifact，返回截断内容 + artifact 路径
- AC-4: `get_permission_cache_key("office", "file_read", {"path": "/a"})` 返回 64 字符 sha256 hex
- AC-5: 不同 skill_name 同 tool 同 args → 不同 cache_key
- AC-6: 3 场景 examples/*.md 迁移到 examples/train/*.md，ExampleLoader 从 train/ 加载
- AC-7: `ExampleLoader.from_cfg(cfg)` 类方法可用
- AC-8: `GET /files/outputs/{filename}` 返回 200 + image content-type；不存在返回 404
- AC-9: 前端 PreviewCard 解析 tool_result.output 中 `outputs/*.png` 路径并渲染 `<img>`
- AC-10: 3 场景 E2E 测试在 train/ 迁移后全部通过

## RALPLAN-DR

### Principles

1. 最小代码：每子项只做 spec In scope 内的事，不扩
2. 外科手术式改动：每步 cite 具体文件/行号
3. 不破坏现有测试：train/ 迁移同步更新 E2E 测试
4. 复用现有约定：artifact 写入 `.claude/artifacts/`，HTTP 端点复用 FastAPI 路由模式

### Decision drivers

1. 实现速度：M3 收尾，4 子项需快速完成
2. 测试覆盖：每子项必须有单测
3. 与蓝图一致性：符合 §5.8/§7.5/§7.12/§7.16 规范

### Viable options

**Option A: 最小内联实现（favored）**
- file_read: 直接文件写入 `.claude/artifacts/file_read_{hash_short}.txt`（无 ArtifactManager 依赖）
- 前端: App.tsx 内联路径解析 + `<img>` 渲染（不新建独立组件文件）
- 改动文件: file_read.py, permission.py(新), example_loader.py, api/files.py(新), main.py, App.tsx, chat.html, 6 个 .md 迁移, 4 个测试文件
- Pros: 改动面最小，不引入新抽象，符合 MVP 最小代码原则
- Cons: App.tsx 略变长（+约 25 行）；artifact 无统一清理机制

**Option B: 模块化实现（rejected）**
- file_read: 新建 ArtifactManager 类管理 artifact 生命周期
- 前端: 新建 PreviewCard.tsx 独立组件
- 改动文件: 同 A + ArtifactManager.py, PreviewCard.tsx
- Pros: 代码更清晰，后续扩展方便
- Cons: 过度工程——MVP 阶段仅 file_read 写 artifact，ArtifactManager 无第二消费者；PreviewCard 独立组件增加文件但仅 20 行逻辑；违反"最小代码"原则
- Invalidation rationale: spec Out of scope 已排除 read_artifact 工具改造；ArtifactManager 无第二消费者属废码

## Implementation steps

### A. file_read 增强（AC-1/2/3）

1. `backend/private_agent/tools/builtins/file_read.py:55-68` — parameters_schema 新增 `max_lines`(int, default 1000, min 1, max 10000) 和 `max_file_size_mb`(int, default 10) 和 `workspace`(string, 可选，用于 artifact 写入定位) 参数
2. `backend/private_agent/tools/builtins/file_read.py:16-49` — file_read_handler 增强：
   - 读取前 `os.path.getsize(resolved)` 检查，> `max_file_size_mb * 1024 * 1024` 时返回 error="文件过大({size}MB > {max}MB),请用 code_execution 分块处理"
   - 读取后按 `max_lines` 截断：`lines = content.split("\n"); if len(lines) > max_lines: content = "\n".join(lines[:max_lines]) + f"\n[truncated at {max_lines} lines]"`
   - 结果 token 估算 `len(content) // 4 > 4000` 时：写 artifact 到 `{workspace}/.claude/artifacts/file_read_{hash_short}.txt`，返回截断前 4000 token 内容 + `"\n[truncated, full content saved to artifact: {path}]"`
3. `backend/tests/test_builtins_file_read.py` — 新增 3 个测试：max_lines 截断、大文件拒绝、artifact 写入 + 截断提示

### B. 权限缓存 cache_key 函数（AC-4/5）

4. `backend/private_agent/tools/permission.py`（新建）— 实现 `get_permission_cache_key(skill_name, tool_name, args) -> str`，sha256 三段拼接
5. `backend/tests/test_permission_cache_key.py`（新建）— 单测 AC-4（64 字符 hex）+ AC-5（不同 skill_name 不同 key）+ 幂等性（同输入同输出）

### C. train/ 子目录迁移（AC-6/7/10）

6. `git mv` 6 个文件：office/{excel_summary,web_research}.md、data_analysis/{data_visualization,statistical_test}.md、frontend_design/{landing_page,react_component}.md → 各自 `examples/train/` 子目录
7. `backend/private_agent/skills/example_loader.py:35` — `ex_dir` 改为 `Path(self.dev_dir) / skill_name / "examples" / "train"`
8. `backend/private_agent/skills/example_loader.py` — 新增 `from_cfg(cls, cfg)` 类方法，读取 `cfg["skills"]["storage"]["dev_dir"]`
9. `backend/tests/test_skills_example_loader.py` — 适配 train/ 路径（测试用例创建临时 examples/train/ 目录结构）
10. 验证 3 场景 E2E 测试无路径断言需更新（预期无需改动，E2E 测试用真实 SKILLS_DEV_DIR 加载）

### D. 数据分析前端预览卡片（AC-8/9）

11. `backend/private_agent/api/files.py`（新建）— FastAPI 路由 `GET /files/outputs/{filename}`，读取 `{workspace_root}/outputs/{filename}` 返回 `FileResponse`；文件不存在返回 404；路径穿越校验
12. `backend/private_agent/main.py:9,19` — 新增 `from private_agent.api import files` + `app.include_router(files.router)`
13. `frontend/renderer/App.tsx:81-82` — tool_result 分支增加图片路径解析：正则 `outputs/[\w\-]+\.(png|jpg|jpeg|svg)` 匹配 output 字符串
14. `frontend/renderer/App.tsx:309-338` — 事件渲染处 tool_result 含图片路径时渲染 `<img src="/files/outputs/{filename}">` 替代纯 `<pre>`
15. `frontend/static/chat.html:408-409,419-443` — renderEvent 同步实现路径解析 + `<img>` 渲染
16. `backend/tests/test_files_endpoint.py`（新建）— HTTP 端点测试 AC-8（200 + content-type、404、路径穿越拒绝）

## Workspace setup

- Run `git status --short` and `git branch --show-current` before implementation.
- 当前在 master 分支，working tree 有 2 个行尾符差异 + 2 个 untracked 文档（非本次范围，保留不动）。
- 本次改动涉及 10+ 文件（含新建），但都在 master 上直接开发（项目无 feature branch 惯例）。
- 不创建 worktree（项目历史显示直接在 master 开发：commit 13d6725 → ced655c 均在 master）。

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| train/ 迁移破坏 E2E 测试 | 迁移后立即跑 3 场景 E2E + ExampleLoader 测试 |
| file_read artifact 写入需 workspace 参数 | 新增 `workspace` 可选参数，从 react_loop 调用时透传；测试时用临时目录 |
| 前端图片路径解析正则误匹配 | 正则限定 `outputs/` 前缀 + 图片扩展名后缀，避免匹配普通文本 |
| outputs/ 目录不存在时 HTTP 端点 500 | 端点先检查目录存在性，不存在返回 404 |
| data_analysis max_file_size_mb=100 与 spec 写的 20 不符 | plan 修正：实际值 office=50, data_analysis=100, frontend_design=20（从 skill.yaml 读取，不硬编码） |

## Verification steps

- AC-1/2/3: `pytest tests/test_builtins_file_read.py -v`
- AC-4/5: `pytest tests/test_permission_cache_key.py -v`
- AC-6/7/10: `pytest tests/test_skills_example_loader.py tests/test_office_skill_e2e.py tests/test_data_analysis_skill_e2e.py tests/test_frontend_design_skill_e2e.py -v`
- AC-8: `pytest tests/test_files_endpoint.py -v`
- AC-9: 前端无自动化测试，手动验证 App.tsx + chat.html 渲染逻辑（代码审查确认正则 + img 标签）
- 全量回归: `pytest tests/ -v --tb=short`

## ADR

- **Decision**: Option A 最小内联实现 — file_read 直接文件写入 artifact，前端 App.tsx 内联 PreviewCard 逻辑
- **Drivers**: 实现速度（M3 收尾）、最小代码原则、不引入无第二消费者的抽象
- **Alternatives considered**: Option B 模块化实现 — rejected（ArtifactManager 无第二消费者属废码，PreviewCard 独立组件仅 20 行逻辑过度工程）
- **Why chosen**: MVP 阶段最小可行实现，spec Out of scope 已排除 artifact 管理改造；后续如需统一 artifact 管理可在 V2 重构
- **Consequences**: 正面—改动面最小、快速交付；负面—artifact 无统一清理机制（V2 问题）、App.tsx 略变长（可接受）
- **Follow-ups**: V2 可考虑统一 ArtifactManager + PreviewCard 独立组件化

## Review trail

- Planner draft v1: Option A 最小内联实现，15 步实施步骤，6 个风险项
- Architect challenge v1: steelman 指出 artifact 无统一清理机制，但 MVP 阶段仅 file_read 写 artifact 可接受；tradeoff tension 最小实现 vs 可维护性，MVP 选最小实现合理
- Critic verdict v1: APPROVED — 6 维度全部通过，1 条 reservation（file_read artifact 写入需 workspace 参数，已在 step 2 标注新增 workspace 可选参数解决）
- Final iterations: 1 / 3
