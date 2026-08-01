# M3 剩余 Done Criteria 补齐 Spec

> Status: ALIGNED
> Author: user
> Last updated: 2026-08-01

## Background

M3 三场景 Skill 核心框架已完成并提交（commit 13d6725 → ced655c），但蓝图 9.4 M3 Done Criteria 中 4 项细粒度标准未实现：超大文件分块读取、权限缓存 cache_key 含 skill_name、train/test 拆分、数据分析前端预览卡片。本 spec 补齐这 4 项。

## In scope

### A. file_read 工具增强（蓝图 §5.8 + §7.9）
- 新增 `max_lines` 参数（default 1000, min 1, max 10000），超过时截断返回前 max_lines 行 + 截断提示
- 新增文件大小检查：文件 > `max_file_size_mb`（default 10MB）时拒绝读取，返回错误提示"文件过大,请用 code_execution 分块处理"
- 读取结果 > 4000 token（len//4 估算）时截断 + 写入 artifact 文件（`.artifacts/` 目录），返回截断内容 + artifact 路径
- `max_file_size_mb` 从 skill.yaml `permissions.max_file_size_mb` 透传（office=50, data_analysis=20, frontend_design=20）

### B. 权限缓存 cache_key 函数（蓝图 §7.5）
- 新建 `private_agent/tools/permission.py`
- 实现 `get_permission_cache_key(skill_name: str, tool_name: str, args: dict) -> str`
- cache_key = `hashlib.sha256(f"{skill_name}::{tool_name}::{json.dumps(args, sort_keys=True, ensure_ascii=False)}".encode()).hexdigest()`
- 单测：不同 skill_name 同 tool_name 同 args → 不同 cache_key；同 skill_name 同 tool_name 同 args → 相同 cache_key

### C. train/ 子目录迁移（蓝图 §7.16）
- 现有 3 场景 × 2 文件 = 6 个 `examples/*.md` 迁移到 `examples/train/*.md`
- `ExampleLoader.load()` glob 路径改为 `examples/train/*.md`
- 补 `ExampleLoader.from_cfg(cls, cfg)` 类方法（admin.py 第 43 行已引用但未实现）
- 更新现有 ExampleLoader 测试 + 3 场景 E2E 测试适配新路径

### D. 数据分析前端预览卡片（蓝图 §7.12）
- 后端新增 `GET /files/outputs/{filename}` HTTP 端点，返回 `outputs/` 目录下图片文件（content-type: image/png 等）
- 前端 `App.tsx` 新增 `PreviewCard` 组件：解析 `tool_result.output` 中的 `outputs/*.png|jpg|svg` 路径，通过 HTTP 端点加载图片渲染 `<img>`
- `chat.html` 同步实现路径解析 + 图片渲染逻辑
- 路径解析正则：`outputs/[\w\-]+\.(png|jpg|jpeg|svg)`

## Out of scope

- file_read 工具自身的分块迭代读取（§7.9 的分块是给 code_execution 的 pandas chunksize 模板，非 file_read 工具自身）
- PermissionManager 类 + WS 确认流程 + 会话级 confirmation_cache（蓝图 §5.12 注明 MVP 不经此 ABC）
- `examples/test/` 目录创建 + `.json` 格式测试样本 + `eval_datasets` 表 + LLM-as-Judge（M4 评估闭环范围）
- Electron main 进程实现（main/index.ts 保持占位）
- 点击图片全屏弹窗（可选，非必须）
- plotly 交互式图表（V2）
- `read_artifact` 工具改造

## Assumptions

- `outputs/` 目录位于 workspace 根目录下，后端可通过 config `system.workspace_root` 定位
- file_read 的 `max_file_size_mb` 参数从调用方透传（非硬编码），MVP 从 skill.yaml `permissions.max_file_size_mb` 读取
- artifact 文件写入复用现有 `.artifacts/` 目录约定（蓝图 §5.15）
- 前端通过相对路径 `outputs/xxx.png` 解析，HTTP 端点提供 `/files/outputs/xxx.png` 访问

## Solution

### A. file_read 增强
扩展 `file_read_handler`：新增 `max_lines` 参数到 `parameters_schema`；读取前 `os.path.getsize()` 检查大小；读取后按 `max_lines` 截断；结果 token 估算 > 4000 时写 artifact + 返回截断内容。artifact 写入复用 `ArtifactManager` 或直接文件写入（最小实现）。

### B. cache_key 函数
独立模块 `permission.py`，纯函数无状态。被 ToolRegistry / 未来 PermissionManager 引用。MVP 仅提供函数 + 单测，不集成到运行时权限校验路径。

### C. train/ 迁移
文件系统操作：`git mv` 6 个 .md 文件到 `train/` 子目录。ExampleLoader 第 35 行 `ex_dir` 追加 `/train`。补 `from_cfg` 类方法读取 `cfg["skills"]["storage"]["dev_dir"]`。

### D. 预览卡片
后端 `api/files.py` 新增路由 `GET /files/outputs/{filename}`，读取 `{workspace_root}/outputs/{filename}` 返回 `FileResponse`。前端 `App.tsx` 在 `tool_result` 事件渲染处，用正则解析 `output` 字符串中的图片路径，匹配时渲染 `<PreviewCard path="..."/>` 组件，`<img src="/files/outputs/xxx.png">`。

## Edge cases & risks

| Category | Notes |
|---|---|
| Boundary conditions | file_read: 空文件、刚好 max_lines 行、刚好 max_file_size_mb、非 UTF-8 文件 |
| Failure modes | outputs/ 目录不存在时 HTTP 端点返回 404；artifact 目录不可写时降级返回未截断内容 |
| Risks | train/ 迁移可能破坏现有 E2E 测试（需同步更新测试中的 examples 路径断言） |
| Mitigation | 迁移后跑全量回归；file_read 截断时在 output 末尾追加 `[truncated, full content saved to artifact: {path}]` |

## Acceptance criteria

- AC-1: file_read 支持 `max_lines` 参数（default 1000），超过时返回前 max_lines 行 + 截断提示
- AC-2: file_read 文件大小 > `max_file_size_mb` 时拒绝读取，返回错误"文件过大,请用 code_execution 分块处理"
- AC-3: file_read 读取结果 > 4000 token 时截断 + 写入 artifact，返回截断内容 + artifact 路径
- AC-4: `get_permission_cache_key("office", "file_read", {"path": "/a"})` 返回 64 字符 sha256 hex
- AC-5: `get_permission_cache_key("office", ...)` ≠ `get_permission_cache_key("data_analysis", ...)` 同 tool 同 args
- AC-6: 3 场景 examples/*.md 已迁移到 examples/train/*.md，ExampleLoader 从 train/ 加载
- AC-7: `ExampleLoader.from_cfg(cfg)` 类方法可用，admin.py `_build_skill_manager` 调用不再报 AttributeError
- AC-8: `GET /files/outputs/test.png` 返回 200 + content-type: image/png；文件不存在返回 404
- AC-9: 前端 PreviewCard 组件解析 tool_result.output 中的 `outputs/*.png` 路径并渲染 `<img>`
- AC-10: 3 场景 E2E 测试在 train/ 迁移后全部通过（无回归）

## Open questions

None — 三个 scope 决策已通过 dev-grill-docs Wave 1-3 确认。

## Interview metadata

- Mode: default
- Waves: 3
- Final ambiguity: 15.75%
- Status: PASSED

### Clarity breakdown

| Dimension | Score | Weight | Weighted |
|---|---|---|---|
| Goal | 0.90 | 0.40 | 0.36 |
| Scope | 0.85 | 0.25 | 0.2125 |
| AC | 0.70 | 0.25 | 0.175 |
| Context | 0.95 | 0.10 | 0.095 |
| Ambiguity | | | 15.75% |
