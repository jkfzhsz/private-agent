# B2 独立能力补全 Implementation Plan

> Status: APPROVED
> Source: .claude/artifacts/designs/b2-remaining-features.md
> Mode: default
> Iterations: 1 / 3
> Author: Private Agent
> Last updated: 2026-08-01

## Requirements summary

修复计划最后批次 B2，5 项相互独立的 P1 能力：P1-1 Electron 拉起 Python Sidecar、P1-6 MCP HTTP transport + 双探活、P1-7 JavaScript 沙箱、P1-8 file_read 分块读取、P1-9 前端 Skill 选择页。用户已确认解除 JS 延后约束；P1-3 Agnes 适配器已从计划删除(无真实 base_url,保持 enabled=false 现状)。

## Acceptance criteria

- AC-1 `spawnSidecar` 用正确参数启动 `python -m private_agent.main`(mock 验证)
- AC-2 `waitForHealth` 轮询 /health 直到 200,超时抛错
- AC-3 `stopSidecar` 先 SIGTERM,超时 SIGKILL
- AC-4 SidecarManager 崩溃重启 ≤3 次 + 指数退避
- AC-5 MCP http connect 后自动 ping,失败抛 McpConnectError
- AC-6 MCP http discover_tools / call_tool 经 POST /rpc 正常(mock httpx)
- AC-7 `ping()` 健康 True / 宕机 False；`health_check()` 组合 ping+discover
- AC-8 `liveness_loop` 不健康时触发 on_unhealthy
- AC-9 `_build_command("javascript")` 返回 node 命令
- AC-10 真实执行 JS `console.log("hello")` stdout 含 "hello"(node 存在时)
- AC-11 CodeScanner 对 JS 危险代码返回告警
- AC-12 file_read offset/limit 返回对应行切片
- AC-13 file_read metadata 含 total_lines/has_more/next_offset
- AC-14 大文件提供 offset 可读,不提供被拒
- AC-15 SkillSelectionPanel 渲染列表,点击触发 activate
- AC-16 WS skill_not_found 自动切 skill_selection
- AC-17 chat 视图显示 locked_skill_name
- AC-18 全量 pytest 通过(757+新增),前端 vitest 全绿

## RALPLAN-DR

### Principles

- 最小代码:每个 P1 只做修复计划 §2 定义的改动,不顺手重构
- 外科手术式改动:改动文件与计划"影响文件"一一对应
- 向后兼容:file_read 默认行为(无 offset)与现状等价;MCP stdio 路径不回归
- 可验证:每条 AC 有具体测试名/命令
- 前端测试最小引入:仅 vitest 组件级,不启动 Electron 真机

### Decision drivers

- 现有 MCPClient 是统一入口(registry 按 MCPClient 类型操作) → http 分支内联优先
- 前端当前无任何测试设施 → 引入 vitest 是唯一轻量路径
- file_read 已有 metadata 字段 → 分页信息零成本承载
- config 已有 tools.mcp.probe_interval_sec → 作为 liveness 间隔兜底

### Viable options

**Option A: MCP http 内联进现有 MCPClient(推荐)**
- 实现思路:connect/discover/call 加 http 分支,httpx.AsyncClient 作为 _http_client,ping/health_check/liveness_loop 为通用方法
- 改动文件:errors.py、mcp_client.py、config.yaml、2 个新测试文件
- Pros:调用方(registry/tool_registry)零改动;stdio/http 共享 discover/call 逻辑;单一生命周期入口
- Cons:MCPClient 类体量增大(~+120 行);http/stdio 分支在方法内交错

**Option B: 新建 HttpMcpClient 子类/组合类**
- 实现思路:独立 http 客户端类,继承 ModelAdapter 式协议,registry 按接口操作
- 改动文件:新增 http_mcp_client.py、mcp_client.py 抽象基类、registry 调用点
- Pros:职责分离,类更小
- Cons:需要抽象基类 + 改 registry 调用点,改动面翻倍;两个类行为一致性靠接口保证,回归风险高
- **Invalidation rationale**: registry.py / tools/registry.py 均直接引用 `MCPClient` 具体类,抽象化成本 > 内联成本;P1-6 验收不要求独立类。Option A 胜出。

**Option C: file_read 大文件完全放开(不拒绝)**
- 实现思路:任何文件都可分块读,去掉 max_file_size_mb 拒绝逻辑
- Cons:大文件意外全量读入内存风险;偏离修复计划 §2 明确语义(提供 offset 才放行)
- **Invalidation rationale**: 计划与 spec 均规定"提供 offset 才放行",放开会引入新风险且超出验收范围。

## Implementation steps

### 后端 P1-6 (MCP HTTP + 双探活)

5. `backend/private_agent/errors.py` — 新增 `McpConnectError(PrivateAgentError)`
6. `backend/private_agent/tools/mcp_client.py`:
   - `MCPClientConfig` 加 `health_check_interval_sec: float = 30.0`
   - `__init__` 加 `self._http_client: httpx.AsyncClient | None = None`
   - 新增 `McpHealthStatus` dataclass(ping_ok/tools_count/latency_ms/detail)
   - `connect()` http 分支:建 httpx.AsyncClient → 立即 `ping()` → 失败 disconnect + 抛 McpConnectError
   - `disconnect()` http 分支:关闭 httpx client,幂等
   - `discover_tools()` / `call_tool()` http 分支:POST `{base_url}/rpc`,JSON-RPC 2.0
   - 新增 `ping() -> bool`(stdio:JSON-RPC ping 方法;http:POST /rpc ping)
   - 新增 `health_check() -> McpHealthStatus`
   - 新增 `liveness_loop(interval_sec, on_unhealthy)` — asyncio task 定期 ping
7. `backend/config/config.yaml` — mcp.servers 注释补充 `health_check_interval_sec` 可选字段说明
8. 新建 `backend/tests/test_mcp_client_http.py`(3 测试)+ `backend/tests/test_mcp_client_health.py`(4 测试)

### 后端 P1-7 (JS 沙箱)

9. `backend/private_agent/sandbox/executor.py`:
   - `__init__` 加 `node_command: str = "node"`
   - `_write_script`:`ext = ".py" if python else ".js" if javascript else ".txt"`
   - `_build_command`:javascript 分支 `[self._find_node_cmd(), script_path]`
   - 新增 `_find_node_cmd()` — shutil.which(self._node_cmd),None 抛 ValueError
10. `backend/private_agent/sandbox/security.py`:
    - 新增 `JS_DANGEROUS_PATTERNS` 常量(child_process 全家/eval/Function/fs 写删/process.env)
    - `CodeScanner.scan(code, language="python")` — 按语言选 patterns
11. `backend/private_agent/sandbox/service.py`:
    - 读 `languages.javascript.command`(默认 "node"),传入 `SandboxExecutor(node_command=...)`
    - `self._code_scanner.scan(code, language)`
12. 新建 `backend/tests/test_sandbox_executor_js.py`(2 测试)+ 扩展 `backend/tests/test_sandbox_security.py`(1 测试)

### 后端 P1-8 (file_read 分块)

13. `backend/private_agent/tools/builtins/file_read.py`:
    - `_MAX_LINES_PER_CALL = 1000`
    - handler 读 `offset`(default 0)/ `limit`(default None,兜底 max_lines,钳制 1000)
    - 大文件逻辑:`size > max_file_size_mb` 且 `args.get("offset")` 为空 → 拒绝并提示用 offset/limit;否则放行
    - 返回 `ToolResult(output="\n".join(slice), metadata={offset, limit, total_lines, has_more, next_offset})`
    - schema 加 offset/limit;description 注明分页
14. 扩展 `backend/tests/test_builtins_file_read.py`(6 测试,见测试计划)

### 前端 P1-1 (Electron spawn)

15. `frontend/package.json` — devDeps 加 `vitest`/`jsdom`/`@testing-library/react`/`@testing-library/jest-dom`;scripts.test = "vitest run"
16. 新建 `frontend/vite.config.ts` — vitest 配置(test.environment="jsdom")
17. 新建 `frontend/main/config-loader.ts` — `loadSidecarConfig(configPath?)` 读 config.yaml 的 server.http.port + system.sidecar.memory_limit_mb(js-yaml)
18. `frontend/main/sidecar.ts`:
    - `spawnSidecar(cfg)` — child_process.spawn(python, ["-m","private_agent.main"])
    - `waitForHealth(port, timeoutMs)` — 轮询 /health 500ms 间隔
    - `stopSidecar(proc)` — SIGTERM → 30s → SIGKILL
    - `SidecarManager` 类 — start/waitForHealth/崩溃重启 ≤3 + 指数退避(1s/2s/4s)/stop
19. `frontend/main/index.ts` — whenReady → loadSidecarConfig → SidecarManager.start → createWindow;before-quit → stop
20. 新建 `frontend/main/__tests__/sidecar.test.ts`(4 测试,mock child_process/fetch)

### 前端 P1-9 (Skill 选择页)

21. `frontend/renderer/SkillSelectionPanel.tsx` — GET /admin/skills → 三场景卡片 → POST activate → onActivated 回调
22. `frontend/renderer/App.tsx`:
    - view state `"skill_selection" | "chat"`
    - WS error payload.message == "skill_not_found" → setView("skill_selection")
    - chat 顶部显示 locked_skill_name + 切换按钮
23. 新建 `frontend/renderer/__tests__/SkillSelectionPanel.test.tsx`(4 测试,jsdom + @testing-library/react)

## Workspace setup

- 当前分支 master,工作树无真实内容变更(5 个 `M` 文件为 LF/CRLF 行尾符伪差异,`git diff HEAD` 为空)
- 沿用 B3-B6 既有工作流:直接在 master 开发 + 逐批次 commit,不使用 worktree(项目历史惯例)
- 开发前 `git status --short` 确认除行尾符伪差异外无其他改动

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| 前端首次引入 node_modules,依赖安装慢/失败 | 安装失败重试;electron 仅 devDeps;测试不启动真机 |
| 现有 test_mcp_client.py 对 http stub 抛错断言 → 回归 | 先跑该测试文件,同步更新断言 |
| 现有 test_builtins_file_read.py 大文件拒绝断言 → 回归 | 保持默认行为(无 offset 仍拒绝),仅更新提示文案相关断言 |
| CodeScanner.scan 签名变更影响其他调用方 | grep 全部调用点,仅 service.py 一处 |
| JS 沙箱 node 不存在时 E2E 测试失败 | 用 pytest.skipif 跳过真实执行测试 |

## Verification steps

- 后端单模块:`PA_DB_PASSWORD=123123 PA_TEST_DSN=postgresql://postgres:123123@localhost:5432/private_agent_test python -m pytest backend/tests/test_mcp_client_http.py backend/tests/test_mcp_client_health.py backend/tests/test_sandbox_executor_js.py backend/tests/test_sandbox_security.py backend/tests/test_builtins_file_read.py -v`
- 受影响回归:`python -m pytest backend/tests/test_mcp_client.py backend/tests/test_model_adapters.py backend/tests/test_builtins_file_read.py -v`
- 全量:`PA_DB_PASSWORD=123123 PA_TEST_DSN=... python -m pytest backend/tests`
- 前端:`cd frontend && npm install && npm test`
- 闭环:`grep` 验证新公开符号(spawnSidecar/McpHealthStatus 等)有测试调用者
- DB 迁移:全量测试已覆盖 migrate_all 幂等

## ADR

- **Decision**: MCP http transport 内联进现有 MCPClient(Option A);file_read 大文件仅在提供 offset 时放行(Option C rejected);前端测试引入 vitest 组件级
- **Drivers**: 最小改动(registry 零改动)、向后兼容(file_read 默认行为不变)、测试可跑(vitest 是前端唯一轻量路径)
- **Alternatives considered**: Option B(HttpMcpClient 拆类)— rejected,registry 直接引用 MCPClient 具体类,抽象化成本 > 内联;Option C(大文件放开)— rejected,超出验收范围且有全量读内存风险
- **Why chosen**: 内联分支使 6 个 http 桩位替换为真实实现且调用方零改动;file_read 条件放行兼顾分页需求与内存安全
- **Consequences**: MCPClient 类体量 +~120 行(可维护性代价);前端 node_modules 增加(仅 devDeps);CodeScanner.scan 签名向后兼容(language 默认值)
- **Follow-ups**: MCP 新协议 2026-07-28(V2);Electron 打包分发

## Review trail

- Planner draft v1: 6 项独立步骤组,每项对应修复计划 §2 定义,Option A/B/C 已列
- Architect challenge v1: steelman 反对内联(类膨胀 + 分支交错)→ 反驳:registry 具体类引用使拆类成本更高;tension: 前端测试设施引入 vs 最小依赖 → 取舍:vitest 仅 devDeps 组件级
- Critic verdict v1: APPROVED with 2 improvements(CodeScanner.scan 调用点 grep 全量核查;test_mcp_client.py 旧断言回归列入验证)
- Final iterations: 1 / 3
