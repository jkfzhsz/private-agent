# B2 独立能力补全 Spec

> Status: ALIGNED
> Author: Private Agent
> Last updated: 2026-08-01

## Background

P0/P1 修复计划最后一批次 B2，补全 5 项独立 P1 能力：Electron Sidecar 拉起、MCP HTTP transport + 双探活、JavaScript 沙箱、file_read 大文件分块、前端 Skill 选择页。此 5 项相互独立，可并行实现。

用户已确认两个前置决策点：
- **P1-7**: 解除"JS 延后"硬约束，实现 JavaScript 沙箱
- **P1-3**: Agnes 适配器已从计划删除(无真实 base_url,保持 `enabled: false` 现状)

## In scope

- **P1-1** Electron 主进程拉起 Python Sidecar：`spawnSidecar` / `waitForHealth` / `stopSidecar` + 崩溃重启(≤3 次,指数退避) + `config-loader.ts` 读 config.yaml + `index.ts` 入口接线
- **P1-6** MCP HTTP transport + 双探活：HTTP connect/discover/call + `ping()` / `health_check()` / `liveness_loop()` + connect 后自动 ping
- **P1-7** JavaScript 沙箱：executor `_build_command` 支持 js + `_find_node_cmd` + `_write_script` `.js` 扩展 + CodeScanner JS 危险模式
- **P1-8** file_read 分块读取：新增 `offset`/`limit` 参数 + metadata(`total_lines`/`has_more`/`next_offset`) + 大文件在提供 offset 时不再拒绝
- **P1-9** 前端 Skill 选择页：view state + `SkillSelectionPanel` + `skill_not_found` 404 自动跳转 + chat 视图显示 locked skill 名

## Out of scope

- MCP 新协议 2026-07-28(V2,保持 2025-11-25)
- Electron 打包/分发(仅主进程 spawn 逻辑)
- 前端路由库引入(用简单 view state,不引 react-router)
- 沙箱 Docker 后端(V2)
- 前端 e2e Playwright 全链路(仅组件级测试)

## Assumptions

- `node` 存在于 PATH；缺失时 JS 执行报清晰错误,不崩溃
- MCP HTTP server 遵循 JSON-RPC 2.0 over HTTP(MCP 2025-11-25 协议)
- config.yaml `sandbox.languages.javascript` 已存在(command=node/.js/18.0),无需改动
- 前端引入 vitest + jsdom 作为测试设施(P1-1/P1-9 测试必需)
- `ToolResult.metadata` 字段已存在,可直接承载分页信息

## Solution

### P1-1 Electron spawn (frontend/main/)

`sidecar.ts`:
- `spawnSidecar(config): ChildProcess` — `child_process.spawn(pythonCmd, ["-m", "private_agent.main"], {env, stdio: pipe})`
- `waitForHealth(port, timeoutMs): Promise<void>` — 轮询 `GET /health`(500ms 间隔)直到 200
- `stopSidecar(proc): Promise<void>` — SIGTERM → 30s → SIGKILL
- 崩溃自动重启：包装类 `SidecarManager` 持有重启计数(≤3) + 指数退避(1s/2s/4s)

`config-loader.ts`:
- `loadSidecarConfig(configPath?): {pythonCommand, httpPort, ...}` — js-yaml 读 config.yaml 的 `server.http.port` / `system.sidecar`

`index.ts`:
- `app.whenReady()` → loadSidecarConfig → new SidecarManager → start() → waitForHealth → createWindow
- `app.on('before-quit')` → stopSidecar；`window-all-closed` → quit

### P1-6 MCP HTTP + 双探活 (backend/)

`tools/mcp_client.py`:
- `MCPClientConfig` 增加 `health_check_interval_sec: float = 30.0`
- `connect()` http 分支：`httpx.AsyncClient(base_url)` + 立即 `ping()`，失败抛 `McpConnectError` 并断开
- `discover_tools()` http 分支：`POST {base_url}/rpc` body `{"jsonrpc":"2.0","id":1,"method":"tools/list"}` → `result.tools`
- `call_tool()` http 分支：POST /rpc `tools/call`
- 新增 `ping() -> bool`：POST /rpc `{"method":"ping"}`，200 + 有 result → True；异常 → False
- 新增 `health_check() -> McpHealthStatus`：dataclass(ping_ok, tools_count, latency_ms, detail)
- 新增 `liveness_loop(interval_sec, on_unhealthy)`：后台 asyncio task 定期 ping，失败触发回调
- stdio 模式的 `ping()` 同样实现(JSON-RPC ping 方法)
- `McpHealthStatus` dataclass 与 `McpConnectError`(若不存在则加)

### P1-7 JavaScript 沙箱 (backend/)

`sandbox/executor.py`:
- `_write_script`：`ext = ".py" if language=="python" else ".js" if language=="javascript" else ".txt"`
- `_build_command`：javascript 分支 `[self._find_node_cmd(), script_path]`
- `_find_node_cmd()`：`shutil.which("node")`，None 时 raise ValueError("node not found")

`sandbox/security.py`:
- `JS_DANGEROUS_PATTERNS`：`child_process\.(exec|execSync|spawn|fork)`, `require\(["']child_process`, `eval\(`, `new Function\(`, `fs\.(unlinkSync|rmSync|writeFileSync)`, `process\.(env|exit)`, `globalThis\.fetch` 等
- `CodeScanner.scan(code, language="python")`：按语言选 patterns(python→DEFAULT, javascript→JS)

`sandbox/service.py`:
- `self._code_scanner.scan(code, language)` 传入语言

### P1-8 file_read 分块 (backend/)

`tools/builtins/file_read.py`:
- schema 增加 `offset: integer`(default 0, minimum 0)、`limit: integer`(default None)
- `_MAX_LINES_PER_CALL = 1000`：`limit > 1000` 时钳制为 1000
- 大文件逻辑：`size > max_file_size_mb` 且**未提供 offset** → 返回错误提示"Use offset/limit to read in chunks"；**提供了 offset** → 允许读取
- 返回 `ToolResult(output=lines, metadata={"offset", "limit", "total_lines", "has_more", "next_offset"})`
- description 注明分页用法

### P1-9 前端 Skill 选择页 (frontend/renderer/)

`App.tsx`:
- view state: `"skill_selection" | "chat"`(eval_panel 已另有处理,不引入)
- `SkillSelectionPanel` 组件：`GET /admin/skills` 列表 → 卡片点击 → `POST /admin/skills/{name}/activate` → 成功切 chat 视图
- WS `error` 且 payload.message == "skill_not_found" → 自动切 skill_selection
- chat 视图顶部显示 `locked_skill_name` + "切换 Skill" 按钮(点击提示需结束当前会话)

测试设施:package.json 增加 `vitest` + `@testing-library/react` + `jsdom` devDeps,`test` 脚本指向 vitest run

## Edge cases & risks

| Category | Notes |
|---|---|
| Boundary conditions | file_read limit 钳制上限 1000；offset 超出文件行数 → 返回空 content + has_more=false |
| Failure modes | node 不存在 → ValueError 明确报错；MCP HTTP 服务器不可达 → ping false + McpConnectError；Electron spawn 失败 → 降级提示手动启动 |
| Risks | 前端引入 vitest 可能与其他脚本冲突；Electron 依赖体积大(仅 devDeps,测试不启动) |
| Mitigation | vitest 仅用于单元测试,不启动 Electron 真机；electron 放 devDependencies |

## Acceptance criteria

- AC-1 `spawnSidecar` 用正确参数启动 `python -m private_agent.main`(mock 验证)
- AC-2 `waitForHealth` 轮询 /health 直到 200,超时抛错
- AC-3 `stopSidecar` 先 SIGTERM,30s 未退则 SIGKILL
- AC-4 SidecarManager 崩溃重启 ≤3 次且指数退避
- AC-5 MCP HTTP connect 成功后自动 ping,失败抛 McpConnectError
- AC-6 MCP HTTP discover_tools / call_tool 经 POST /rpc 正常工作(mock httpx)
- AC-7 `ping()` 健康时 True、宕机时 False；`health_check()` 组合 ping+discover
- AC-8 `liveness_loop` 在服务器不健康时触发 on_unhealthy 回调
- AC-9 `_build_command("javascript", ...)` 返回 node 命令
- AC-10 真实执行 JS `console.log("hello")` stdout 含 "hello"(node 存在时)
- AC-11 CodeScanner.scan 对 JS 危险代码返回告警
- AC-12 file_read 带 offset/limit 返回对应行切片
- AC-13 file_read metadata 含 total_lines/has_more/next_offset,has_more=true 时 next_offset 正确
- AC-14 大文件(>max_file_size_mb)提供 offset 时可读,不提供时被拒
- AC-15 SkillSelectionPanel 渲染技能列表,点击触发 activate API
- AC-16 WS skill_not_found error 自动切回 skill_selection 视图
- AC-17 chat 视图显示 locked_skill_name
- AC-18 全量 pytest 通过(757 + 新增),前端 vitest 全绿

## Open questions

无(两个决策点已确认,其余按修复计划 §2 执行)

## Interview metadata

- Mode: default(需求源自 p0-p1-fix-plan.md §2,已充分定义)
- Waves: 1(仅用户确认两个决策点)
- Final ambiguity: <10%
- Status: ALIGNED
