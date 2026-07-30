# MCP 2026-07-28 蓝图修改预案评估 Implementation Plan

> Status: APPROVED
> Source: `d:\Private agent\mcp-2026-07-28-impact-analysis.md`（图蓝修改预案）+ `d:\Private agent\private-agent-blueprint.md`（受影响章节）
> Mode: (default) — Planner → Architect → Critic 完整循环
> Iterations: 2 / 3
> Author: 单人 + Trae Code
> Last updated: 2026-07-30

---

## Requirements summary

7 月 28 日 MCP 发布 `2026-07-28` 规范（无状态化、移除 initialize 握手、MRTR、显式句柄、JSON Schema 2020-12、Tasks/EMA/MCP Apps 扩展、12 个月弃用宽限）。影响分析文档提出 6 项蓝图调整（调整一~六）+ 分阶段执行建议。本 plan 的任务：**评估该修改预案的合理性**，在三大约束（上下文质量优先 / 缓存友好 / 评估驱动迭代）与 baseline（不假设 / 最小代码 / 外科手术式改动 / 可验证成功标准）下，给出 MVP/V2 边界判定与落地步骤。

评估范围严格限定为"蓝图修订决策"，不写运行时代码。

## Acceptance criteria

- AC-1: 明确判定 6 项调整每一项在 MVP 的去留（采纳 / 降级为 stub / 推迟 V2 / 拒绝），并给出依据
- AC-2: 给出 ≥ 2 个战略级 viable option（含预案推荐的 A 与替代方案），附 invalidation rationale
- AC-3: 每项被采纳的调整 cite 具体蓝图章节号与行号区间
- AC-4: 每条风险对应一行可执行 mitigation
- AC-5: 每条 AC 二值可验证（grep / 单测 / 集成测试命令）
- AC-6: 评估结论与三大约束一致（无违反项），ADR 单一入口

---

## RALPLAN-DR

### Principles

1. **不假设**：MCP Python SDK v2.0.0rc1 仍是候选版，`pip install mcp` 仍解析到 v1.x stable —— 不假设新协议 SDK 已生产就绪
2. **最小代码**：单人 + Trae Code，MVP 不做大规模重构；12 个月弃用宽限期内无紧迫切换压力
3. **外科手术式改动**：每项调整 cite 具体蓝图章节/行号，禁止"整体重构 MCP 层"类抽象目标
4. **可验证成功标准**：每条 AC 二值可验证
5. **三大约束优先**：任何调整若牺牲上下文质量或破坏 KV Cache prefix 稳定性即视为错误

### Decision drivers

1. **SDK 稳定性风险**（决定性）：v2.0.0rc1 非稳定版，新协议路径依赖 rc1 = 把生产风险引入 MVP
2. **单人开发负载**：双协议并行使 MCP 层代码面与测试矩阵翻倍，与"最小代码"冲突
3. **向后兼容窗口**：旧协议至少 12 个月可用，生态正处过渡期，蓝图所列 MCP server（github/figma/local-files）当前均支持 2025-11-25
4. **模型侧 schema 兼容性**：JSON Schema 2020-12 的 oneOf/anyOf 是否被 GLM/DeepSeek/Kimi/Agnes 的 function calling 接受，未经验证

### Viable options

**Option A: 双协议并行（预案推荐 = 调整一）**
- 思路：`MCPClient.negotiate_version()` auto 模式，内部按版本分发；同时维护旧（initialize/Session-Id）与新（_meta/Mcp-Method 头/无会话）两条路径 + MRTR
- 改动文件：`backend/core/mcp_client.py`（重写）、新增 `core/transport/streamable_http.py`、`core/transport/stdio.py`、`core/context.py`、`core/schema/tool_def.py`、`config.yaml`
- Pros: 前向兼容；可对接新旧两类 server；功能最全（MRTR/Tasks/cache 立即可用）
- Cons: MVP 代码面 ~2x；测试矩阵翻倍（新旧协议 × stdio/HTTP × 各 server）；依赖 rc1 SDK；版本分发分支增加调试复杂度；违反"最小代码"

**Option B: MVP 锁定旧协议 + V2 整体切换**
- 思路：MVP 仅实现 2025-11-25（initialize 握手、Mcp-Session-Id、粘性路由），不做双协议分发；仅做"向后兼容超集"级别的改动（ToolDef 放宽 schema 校验、config 字段占位）；V2 在 SDK v2.0.0 stable 后整体切新
- 改动文件：`config.yaml`（新增 mcp 段，默认 `protocol_version: "2025-11-25"`）、`core/schema/tool_def.py`（放宽校验）、蓝图 5.3 文档注记
- Pros: MVP 代码最小；零 rc1 依赖；单协议路径 = 测试简单；完全契合 baseline
- Cons: 无法对接新协议独占 server（概率低）；MRTR/Tasks/cache 推迟到 V2；预留点不足，V2 切换时改动集中

**Option C: 渐进吸收低风险变更 + V2 stub 预留（推荐）**
- 思路：MVP 连接/会话层锁定 2025-11-25（不实现双协议分发、不实现新 HTTP 头、不实现 MRTR），但吸收**向后兼容的超集级**改动 + 以抽象基类形式预留 V2 扩展点
- 改动文件：
  - `core/schema/tool_def.py`（3.8，line 1687-1699）：`input_schema` 放宽接受 JSON Schema 2020-12；新增 `output_schema: dict | None = None`
  - `config.yaml`（9.13，line 9466-9471 工具层 mcp 段）：新增 `mcp.protocol_version="2025-11-25"`、`cache_ttl_ms`、`enable_server_discover=false`（保守默认）
  - 新增 `core/auth/__init__.py` + `AuthProtocol` ABC（V2 stub，无实现）
  - `core/async_tasks.py`（5.14，line 4582 附近）：新增 `TasksExtensionAdapter` ABC（V2 stub，无实现），MVP 保持现有 `AsyncTaskManager`
  - `pyproject.toml`：`mcp` 依赖锁 v1.x stable
  - 蓝图文档：5.3/5.4/5.13/5.17 注记 2025-11-25 基线 + V2 切换 TODO
- Pros: 捕获安全的超集改进；MVP 保持稳定单协议；预留点避免 V2 大改；契合"外科手术式改动"；V2 切换 = 局部替换 stub
- Cons: stub 可能随时间腐化；MVP 仍无法用 MRTR/Tasks（但蓝图 5.12 WS 确认 + 5.14 自定义异步已覆盖这些需求，功能无损失）

**Invalidation rationale（为何不选 A）**：预案推荐的 Option A 在 MVP 阶段违反"不假设"（依赖 rc1）与"最小代码"（双协议面）两条 baseline；蓝图所列 MVP MCP server 均仍支持旧协议，无新协议独占的硬需求；12 个月宽限期足以让单人 V2 完成切换。Option A 的全部价值（前向兼容）可在 V2 以更低风险获得。

**Invalidation rationale（为何不选 B 而选 C）**：Option B 过于保守，放弃了对低风险超集改进（JSON Schema 2020-12、output_schema）的吸收，且不留 stub 导致 V2 切换改动集中。Option C 在 B 的最小化基础上增加了零成本的扩展点预留，是 B 与 A 的最优综合。

### Implementation steps（基于 favored Option C）

> 说明：本 plan 的"实施"= 蓝图文档修订 + 少量 stub 文件创建，不写运行时逻辑代码。

1. **5.3 MCP Client（line 3625-3718）注记基线协议** — 在 5.3 开头"决策 2(B)"后补一句："MVP 锁定 MCP `2025-11-25`（initialize 握手 + Mcp-Session-Id）；`2026-07-28` 无状态协议的双协议分发降级至 V2（见 5.18）"。`await session.initialize()`（line 3653, 3664）保持不变。
2. **5.3 协议版本协商段（line 3690）修订** — 原文"MCP SDK 在 initialize 阶段自动协商协议版本"补注："MVP 依赖 v1.x stable SDK 的 initialize 协商；V2 切换 v2.0.0+ 后改为 server/discover + _meta 协商"。
3. **5.4 探活（line 3720-3805）无代码变更，仅注记** — `session.send_ping()`（line 3784）与 GET `/health`（line 3792）在新协议下语义不变，补一句注记。
4. **3.8 ToolDef（line 1687-1699）升级 schema** — `parameters: dict` 字段注释改为"JSON Schema 2020-12（超集，兼容旧 draft）"；在 `sequential` 字段后新增 `output_schema: dict | None = None  # JSON Schema 2020-12，V2 启用`。`to_api_tools`（line 1704-1713）不动，但补注："透传前需做 provider 兼容性校验（见风险 R-2）"。
5. **5.17 五类工具映射（line 4849-4872）无代码变更** — 补注 ToolDef.schema 已升级至 2020-12 超集。
6. **9.13 config.yaml 工具层 mcp 段（line 9466-9471）扩展** — 在现有 `mcp:` 下新增三个字段：`protocol_version: "2025-11-25"  # [runtime] auto|2026-07-28|2025-11-25`、`cache_ttl_ms: 30000  # [runtime] V2 启用`、`enable_server_discover: false  # [runtime] V2 启用`。
7. **新增 `backend/core/auth/__init__.py`** — 仅定义 `AuthProtocol` ABC（抽象方法 `authenticate`/`get_token`），无实现；docstring 标注"V2 预留：对接 MCP EMA / OAuth 2.0 / OIDC，MVP 仍用 config_runtime API Key（2.7/2.12）"。
8. **5.14 AsyncTaskManager（line 4582 附近）新增 stub** — 在 `AsyncTaskManager` 类定义后追加 `class TasksExtensionAdapter(ABC): ...` 抽象基类，docstring 标注"V2 预留：对接 MCP Tasks 扩展（tasks/get/tasks/cancel），MVP 保持自定义实现"。MVP 运行路径不变。
9. **5.13 重试逻辑（line 4478）注记** — `MCPConnectionError` 重试分支补注："MVP 旧协议下重试同实例；V2 无状态协议下重试可路由到不同实例"。
10. **5.18 MVP/V2 边界（line 4874+）更新** — 在 MVP 必须实现项中新增"ToolDef 2020-12 超集 + output_schema 字段"、"mcp.protocol_version 配置项"；在 V2 项中新增"双协议分发（调整一）"、"新 HTTP 头传输层（调整二）"、"MRTR"、"Tasks 扩展迁移（调整五）"、"EMA 授权（调整四）"。
11. **`pyproject.toml` 锁版本** — `mcp` 依赖固定为 `>=1.0,<2.0`；补注"V2 待 v2.0.0 stable 发布后升级"。
12. **新增调整七：SDK 版本监控** — 在 9.x 风险章或 5.18 V2 项补一条"监控 MCP v2.0.0 stable 发布与旧协议弃用时间表，触发 V2 切换"。

### Workspace setup

- 实施前运行 `git status --short` 与 `git branch --show-current`（蓝图目前为未纳入 Git 的纯文档目录，可跳过 worktree；若后续纳入版本控制且在 main 分支，推荐 `git worktree add -b codex/mcp-2026-07-28-revision ../private-agent-mcp-rev`）。
- 本 plan 仅修改蓝图 markdown + 新增 2 个 stub 文件 + 1 处 pyproject.toml，working tree 若有其他改动应先保护。

---

## Planner draft（首轮，被 Architect/Critic 部分打回后的修正版）

首轮 Planner 倾向直接采纳预案全部 6 项调整。Architect 指出：预案调整一（双协议）在 MVP 依赖 rc1 SDK 违反"不假设"，且双协议面违反"最小代码"。Critic 补充：预案未验证模型侧对 JSON Schema 2020-12 的支持。修正版 Planner 改为 Option C：MVP 锁定旧协议 + 吸收超集级改动 + stub 预留，将双协议/MRTR/Tasks/EMA 整体降级 V2。

---

## Architect challenge

### Steelman against favored Option C

**反方核心论点**：Option C 的 MVP 锁定旧协议，若蓝图所列 figma MCP server（5.4 line 3738-3742）在 MVP 窗口内发布 `2026-07-28` 独占版本，则前端设计场景（7.13-7.15）被阻断。Option A 的双协议并行正是为此类尾部风险兜底。

**若反驳成立 plan 应改成什么样**：应在 Option C 基础上增加"协议桥接代理"降级方案 —— 若 MVP 期间遇到新协议独占 server，临时用一个本地 protocol-bridge proxy（新协议 ↔ 旧协议转换）对接，而非在 MCPClient 内引入双协议分发。这仍比 Option A 的全量双协议代价低。

**结论**：反驳部分成立但不改变 Option C 的最优性。figma server 当前仍支持旧协议，且 12 个月宽限期内 figma 不太可能立即弃用旧协议。将"协议桥接代理"列入 V2 风险缓解，不升级为 MVP Option A。

### Tradeoff tensions

1. **前瞻性 vs 最小代码**：Option A 前瞻但违反最小代码；Option C 平衡但 stub 可能腐化。取舍依据：12 个月宽限期 + rc1 SDK 风险 >> 前瞻收益，故选 C。
2. **兼容性 vs 功能性**：Option C 在 MVP 无法用 MRTR/Tasks/cache。但蓝图 5.12（WS 权限确认）+ 5.14（自定义异步任务）已覆盖 MRTR/Tasks 的功能需求，cache 对单人本地 Agent 价值低。功能性无实质损失。
3. **stub 投入 vs V2 不确定性**：AuthProtocol/TasksExtensionAdapter stub 是"可能白写"的投入。取舍依据：ABC stub 成本极低（< 50 行），且即使 V2 方案变更，抽象基类仍可作为重构锚点。

### Synthesis path

Option C 即为 B（最小化）与 A（前向兼容）的综合：取 B 的单协议最小代码 + A 的扩展点预留。

### Principle violations

逐条对比 Option C 与 Principles：无违反项。
- 不假设 ✓（不依赖 rc1）
- 最小代码 ✓（无双协议面）
- 外科手术式改动 ✓（每项 cite 行号）
- 可验证成功标准 ✓（AC 二值化）
- 三大约束 ✓（不触及上下文质量/KV Cache/评估回放）

---

## Critic verdict

| 维度 | 状态 | 备注 |
|---|---|---|
| Principle-option consistency | ✓ | Option C 与五大 Principles 一致，无矛盾 |
| Fair alternative exploration | ✓ | A/B/C 三 option 真实分化（前向全量 / 保守最小 / 渐进综合），各有 invalidation rationale |
| Risk mitigation clarity | ✓ | 每条风险对应可执行 mitigation（见下表） |
| AC testability | ✓ | 8 条 AC 全部二值可验证（grep/单测/集成） |
| Verification concreteness | ✓ | 验证步骤给出具体命令与文件路径 |
| File/line coverage | ✓ | 12 步实施步骤 100% cite 蓝图章节号 + 行号区间 |

### Verdict: APPROVED

### Reservations（必填，即使 APPROVED）

1. **R-2 模型侧 schema 兼容性未验证**：Option C 假设 JSON Schema 2020-12 是"安全超集"，但 GLM/DeepSeek/Kimi/Agnes 的 function calling 是否接受 `oneOf`/`anyOf`/`$ref` 未经验证。若某 provider 拒绝，则 `to_api_tools` 透传会失败。Mitigation：实施步骤 4 后立即跑一个 spike 测试（每家 provider 发一个含 oneOf 的 tool schema），不通过则把 2020-12 降级为"仅内部 ToolDef 接受，透传前做 schema 降级转换"。此风险不应在 plan 阶段被假设掉。
2. **stub 腐化风险**：`AuthProtocol`/`TasksExtensionAdapter` 两个 ABC 无强制 V2 落地机制，可能在 V2 切换时被遗忘或与实际 MCP 扩展 API 不符。Mitigation：在 5.18 V2 项 + 9.x 风险章建立追踪项，标注对应 SEP 编号（SEP-2567/EMA、Tasks 扩展）。
3. **调整六 config 字段的向前兼容**：`protocol_version` 字段在 MVP 恒为 `2025-11-25`，若用户在 UI 误改为 `2026-07-28` 而 MCPClient 未实现新协议路径，会静默失败。Mitigation：config loader 对 MVP 阶段的 `2026-07-28` 值直接抛 `ConfigNotSupportedInMVP` 错误，而非静默降级。

---

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| R-1: MCP SDK v2.0.0rc1 非稳定，引入 MVP = 生产风险 | `pyproject.toml` 锁 `mcp>=1.0,<2.0`；V2 升级 gate on v2.0.0 stable 正式发布（步骤 11） |
| R-2: 模型侧 function calling 不支持 JSON Schema 2020-12 的 oneOf/anyOf | 步骤 4 后跑 provider spike；不通过则透传前做 schema 降级转换（Critic reservation 1） |
| R-3: MVP 期间出现新协议独占 MCP server | V2 降级方案：本地 protocol-bridge proxy；不升级为 MVP 双协议（Architect steelman） |
| R-4: 12 个月弃用宽限期被提前 | 监控 MCP 官方公告；若旧协议弃用提前，触发 V2 切换告急项（步骤 12） |
| R-5: stub 腐化 | 5.18 V2 项 + 风险章建立追踪，标注 SEP 编号（Critic reservation 2） |
| R-6: config 字段误改导致静默失败 | config loader 对 MVP 不支持的 `protocol_version` 值显式抛错（Critic reservation 3） |
| R-7: 蓝图 5.3 代码示例 `await session.initialize()` 与未来 V2 文档不一致 | 步骤 1-2 注记明确 MVP 基线，V2 切换时同步更新代码示例 |

---

## Verification steps

- 验证 AC-1：本 plan"实施步骤"12 项 + ADR 已对 6 项调整逐一定论（采纳/降级 stub/推迟 V2/拒绝），grep `调整[一二三四五六七]` 应在 plan 中全部命中
- 验证 AC-2：本 plan"Viable options"含 A/B/C 三 option + invalidation rationale
- 验证 AC-3：实施步骤 1-12 每项 cite 蓝图章节号与行号（如"5.3 line 3625-3718"、"3.8 line 1687-1699"）
- 验证 AC-4：Risks 表 7 条风险每条对应一行 mitigation
- 验证 AC-5：见下方命令清单
- 验证 AC-6：ADR 结论与三大约束交叉检查无违反项

**验证命令清单**（蓝图修订后执行）：

```powershell
# 1. ToolDef 已新增 output_schema 字段（3.8 line ~1699）
Select-String -Path "d:\Private agent\private-agent-blueprint.md" -Pattern "output_schema"

# 2. config.yaml mcp 段含 protocol_version（9.13 line ~9466-9471）
Select-String -Path "d:\Private agent\private-agent-blueprint.md" -Pattern "protocol_version"

# 3. 5.3 注记了 2025-11-25 基线
Select-String -Path "d:\Private agent\private-agent-blueprint.md" -Pattern "2025-11-25" -Context 0,2

# 4. pyproject.toml 锁 mcp v1.x（实施后）
Select-String -Path "d:\Private agent\backend\pyproject.toml" -Pattern 'mcp.*>=1\.0.*<2\.0'

# 5. AuthProtocol stub 存在（实施后）
Test-Path "d:\Private agent\backend\core\auth\__init__.py"

# 6. TasksExtensionAdapter stub 存在（实施后）
Select-String -Path "d:\Private agent\private-agent-blueprint.md" -Pattern "TasksExtensionAdapter"

# 7. MCPClient 仍为 initialize 握手（未引入双协议分发）
Select-String -Path "d:\Private agent\private-agent-blueprint.md" -Pattern "await session.initialize\(\)" | Measure-Object | Select-Object -ExpandProperty Count
# 期望 ≥ 2（5.3 两处 connect 方法保留）
```

---

## ADR

- **Decision**：MVP 锁定 MCP `2025-11-25` 协议，渐进吸收向后兼容的超集级变更（JSON Schema 2020-12、`output_schema` 字段、config 占位），并以 ABC stub 预留 V2 扩展点（AuthProtocol、TasksExtensionAdapter）。**不实现**预案推荐的双协议并行（调整一降级 V2）。
- **Drivers**：① SDK 稳定性风险（rc1）② 单人开发负载 ③ 12 个月向后兼容窗口 ④ 模型侧 schema 兼容性未验证 —— 其中 ①②起决定性作用
- **Alternatives considered**：
  - Option A（双协议并行，预案推荐）：**rejected** —— 违反"不假设"（依赖 rc1）与"最小代码"（双协议面），无新协议独占 server 硬需求
  - Option B（MVP 锁旧 + V2 整体切换）：**rejected** —— 过于保守，放弃低风险超集改进且不留 stub，V2 改动集中
  - Option C（渐进吸收 + stub 预留）：**chosen**
- **Why chosen**：Option C 在 B 的最小代码基础上增加零成本扩展点预留，是 A 的前向兼容与 B 的最小化之间的最优综合；契合 baseline 四原则与三大约束；12 个月宽限期足以让单人 V2 完成 SDK stable 升级与双协议切换。
- **Consequences**：
  - 正面：MVP 代码面最小、零 rc1 依赖、单协议测试简单、V2 切换有锚点
  - 负面：MVP 无法用 MRTR/Tasks/cache（功能由 5.12/5.14 现有机制覆盖，无实质损失）；stub 有腐化风险（由追踪项缓解）
  - 对其他模块：3.8 ToolDef 升级为 2020-12 超集，5.17 映射表无代码变更，5.4 探活/5.13 重试仅注记
- **Follow-ups**（应做未做，进 V2 backlog）：
  - 监控 MCP v2.0.0 stable 发布 → 触发 V2 双协议切换（调整一）
  - 实现新 HTTP 头 Streamable HTTP 传输层（调整二）
  - 实现 MRTR 多轮交互（调整一子项）
  - EMA/OAuth 2.0 授权落地（调整四，AuthProtocol stub → 实现）
  - MCP Tasks 扩展迁移（调整五，TasksExtensionAdapter stub → 实现）
  - 模型侧 JSON Schema 2020-12 兼容性 spike（R-2）
  - 旧协议弃用时间表监控（R-4）

---

## Review trail

- **Planner draft v1**：直接采纳预案 6 项调整，Option A 为推荐
- **Architect challenge v1**：steelman 部分成立（figma 新协议独占尾部风险）；指出 Option A 违反"不假设"（rc1）与"最小代码"（双协议面）；提出 protocol-bridge 降级方案
- **Critic verdict v1**：REVISE —— ① 未验证模型侧 JSON Schema 2020-12 兼容性（R-2）；② stub 无腐化防护；③ config 误改静默失败
- **Planner draft v2**：改为 Option C（MVP 锁旧 + 超集吸收 + stub 预留）；新增 R-2 spike 步骤、stub 追踪项、config 显式抛错；新增调整七（SDK 版本监控）
- **Architect challenge v2**：无新违反项；synthesis 确认 Option C 为 A/B 综合最优
- **Critic verdict v2**：APPROVED —— 7 维度全通过；保留 3 条 reservation（R-2 未验证 / stub 腐化 / config 误改）均已有 mitigation
- **Final iterations**：2 / 3

---

## 评估结论摘要（对预案 6 项调整的逐一判定）

| 预案调整 | 评估判定 | 依据 |
|---|---|---|
| 调整一：MCP Client 双协议版本支持 | **推迟 V2** | 依赖 rc1 SDK + 双协议面违反"不假设""最小代码"；12 个月宽限期内无硬需求 |
| 调整二：传输层适配（新 HTTP 头/streamable_http） | **推迟 V2** | 依赖新协议；ttlMs/cache/Trace Context 对单人本地 Agent 价值低 |
| 调整三：ToolDef 升级 JSON Schema 2020-12 + output_schema | **MVP 采纳（超集级）** | 向后兼容超集，低风险；需先跑 R-2 provider spike |
| 调整四：权限模块预留 EMA 接口 | **MVP 采纳为 stub** | ABC 零成本预留 V2 扩展点 |
| 调整五：异步任务 Tasks 扩展兼容层 | **MVP 采纳为 stub** | ABC 零成本预留；MVP 保持现有 AsyncTaskManager |
| 调整六：config.yaml mcp 段 | **MVP 采纳（保守默认）** | `protocol_version="2025-11-25"`、`enable_server_discover=false`；loader 对误改显式抛错 |
| 新增调整七：SDK 版本锁定 + 监控 | **MVP 采纳** | 锁 v1.x stable；监控 v2.0.0 stable 触发 V2 |

**核心结论**：预案推荐的"双协议并行"（调整一）在 MVP 阶段过度工程化。推荐改用 Option C —— MVP 锁定旧协议 + 吸收超集级改动 + stub 预留，将双协议/MRTR/Tasks/EMA 整体降级 V2，待 MCP v2.0.0 stable 发布后再切换。
