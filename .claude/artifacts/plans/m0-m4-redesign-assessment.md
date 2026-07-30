# M0-M4 开发步骤是否需要重新设计 评估 Plan

> Status: APPROVED
> Source: 蓝图 9.4(M0-M4 阶段定义)+ 9.6(27 步开发顺序)+ 9.7(验收标准);重排版 docx(技能组合与 Step 1-7 流程);MCP 2026-07-28 修改预案(已落地)
> Mode: (default) — Planner → Architect → Critic 完整循环
> Iterations: 1 / 3
> Author: 单人 + Trae Code
> Last updated: 2026-07-30

---

## Requirements summary

本次 MCP 2026-07-28 修改预案已落地到蓝图(3.8/5.3/5.4/5.12/5.13/5.14/5.17/5.18/9.9/9.13/2.15 共 12 处)。需评估:这些修改是否足以动摇蓝图 9.4 节的 M0-M4 五阶段划分、9.6 节的 27 步开发顺序,以至于需要**重新设计** M0-M4 独立开发步骤?还是只需在现有步骤上做外科手术式补丁?

本 plan 只做评估与微调,不写运行时代码。

## Acceptance criteria

- AC-1: 明确判定"是否需要重新设计 M0-M4 步骤"(是/否),给出依据
- AC-2: 给出 ≥ 2 个 viable option(含重新设计 vs 补丁 vs 其他),附 invalidation rationale
- AC-3: 对受 MCP 修改影响的 27 步中的每一步,标注"不变/微调/新增/移除"
- AC-4: 每处微调 cite 蓝图 9.4/9.6 具体行号
- AC-5: 每条风险对应一行 mitigation
- AC-6: 验证步骤可执行(grep 命令)

---

## RALPLAN-DR

### Principles

1. **不假设**:不假设 MCP 修改必然导致步骤重设计 —— 先逐步比对影响面
2. **最小代码**:27 步开发顺序是单人 9.4/9.5 DAG 的工程化落地,重写代价高;微调优先
3. **外科手术式改动**:每处调整 cite 9.4/9.6 具体行号,禁止"重构 M0-M4 体系"类抽象目标
4. **可验证成功标准**:每处微调可 grep 验证
5. **三大约束优先**:任何调整不得牺牲上下文质量/缓存友好/评估驱动

### Decision drivers

1. **阶段划分稳定性**(决定性):M0-M4 的五阶段划分基于四层架构依赖(2.1→2.4→3.1→5.1→7.1→8.1),MCP 修改未触及架构层依赖关系
2. **关键路径不变性**:9.6 关键路径(四层骨架→进程模型→通信协议→ReAct→上下文→模型适配→工具层→沙箱→Skills→评估)完全未变
3. **MCP 修改的性质**:全部为"注记 + ABC stub + config 字段",零运行时逻辑变更,不影响步骤的产出物与依赖关系
4. **27 步依赖图完整性**:步骤间的"前置依赖→产出物"链路未被 MCP 修改打断

### Viable options

**Option A: 重新设计 M0-M4 步骤(推翻 27 步重写)**
- 思路:基于 MCP 2026-07-28 重新梳理 27 步,可能新增"M0.5 MCP 兼容性预备阶段"或重组 M2 工具层步骤
- 改动文件:蓝图 9.4(line 8640-8813)+ 9.6(line 8965-9010)大范围重写
- Pros: 理论上更"干净";可显式编排 MCP 兼容性工作
- Cons: 27 步依赖关系未变,重写纯属返工;违反"最小代码";打断单人开发已熟悉的步骤编号;重写过程易引入新错误

**Option B: 外科手术式补丁(保留 27 步,微调 3 处)** ★ 推荐
- 思路:27 步编号/依赖/产出物全部不变,仅在受 MCP 修改影响的 3 个步骤(步骤 5/16/18)补注记 + 1 处 M0 实施范围微调
- 改动文件:蓝图 9.4 M0 实施范围(line 8664 1 行)+ 9.6 步骤 5/16/18(line 8977/8988/8990 3 行)+ M2 实施范围(line 8728-8730 注记)
- Pros: 代价最小;保留单人已熟悉的步骤编号;依赖图完整;契合 baseline 四原则
- Cons: MCP 兼容性工作"分散"在 3 个步骤中,不如独立阶段显式

**Option C: 新增 M0.5 预备阶段**
- 思路:在 M0 与 M1 之间插入"M0.5 MCP 兼容性 setup"阶段,集中处理 protocol_version 配置、SDK 锁版本、stub 创建
- 改动文件:蓝图 9.4 新增阶段 + 9.6 新增步骤 + 9.5 DAG 修改
- Pros: MCP 兼容性工作高度显式
- Cons: M0.5 的工作量极小(3 个 config 字段 + 2 个 ABC stub + 1 行 pyproject.toml),独立成阶段过度工程;打断 M0→M1 的紧邻依赖(M0.5 无实质产出);违反"最小代码"

**Invalidation rationale(为何不选 A)**:27 步的依赖关系基于架构 DAG,MCP 修改未触及架构层。重写 27 步等于把未变的依赖图重新表述一遍,纯粹返工,违反"最小代码"。

**Invalidation rationale(为何不选 C 而选 B)**:M0.5 的工作量(3 config 字段 + 2 ABC stub + 1 行依赖锁定)不值得独立成阶段。这些工作天然属于 M0(config)与 M2(stub),拆出反而打断阶段连贯性。

### Implementation steps(基于 favored Option B)

> 说明:本 plan 的"实施"= 蓝图 9.4/9.6 节的微调编辑,不写运行时代码。

1. **9.4 M0 实施范围 line 8664 微调** — 原"config.yaml 静态配置 + config_runtime 运行时配置(2.12)"补注:"含 mcp.protocol_version/cache_ttl_ms/enable_server_discover 三字段(9.13,MVP 锁定 2025-11-25)"
2. **9.4 M2 实施范围 line 8728-8730 补注** — 在"第 5 章全部(5.1-5.17)"后补一句:"含 5.3 MCP 锁定 2025-11-25 + 5.12 AuthProtocol stub + 5.14 TasksExtensionAdapter stub(MCP 2026-07-28 兼容)"
3. **9.6 步骤 5 line 8977 补注** — 原"config.yaml + config_runtime + AES-256-GCM"补注:"+ mcp.protocol_version='2025-11-25' + loader 对 2026-07-28 抛 ConfigNotSupportedInMVP"
4. **9.6 步骤 16 line 8988 补注** — 原"内置 + MCP 统一调度 + stdio/HTTP 双探活"补注:"+ MCPClient 锁定 2025-11-25(不实现双协议分发,V2)"
5. **9.6 步骤 18 line 8990 补注** — 原"三级分级 + 指数退避 + 长任务 + 截断 + 白名单"补注:"+ AuthProtocol/TasksExtensionAdapter 两个 ABC stub(MCP 2026-07-28 V2 预留)"
6. **9.6 步骤总览表后补一段 MCP 兼容性说明** — 在 line 8999 表格后、"单人开发实操建议"前,补一段:"**MCP 2026-07-28 兼容性说明**:本次 MCP 协议升级对 27 步开发顺序的影响为'零运行时变更 + 3 处注记'(步骤 5/16/18),不改变步骤编号、依赖关系与产出物。MVP 锁定 2025-11-25,双协议/MRTR/Tasks/EMA 降级 V2(见 5.18)。"

### Workspace setup

- 蓝图为未纳入 Git 的纯文档目录,跳过 worktree
- 本 plan 仅修改蓝图 9.4/9.6 节 6 处文本,working tree 无其他冲突风险

---

## Planner draft

首轮 Planner 直接产出 Option B(外科手术式补丁)。理由:MCP 修改全部为"注记 + ABC stub + config 字段",零运行时逻辑变更,27 步依赖图完整。Architect 与 Critic 一致通过,无需迭代。

---

## Architect challenge

### Steelman against favored Option B

**反方核心论点**:Option B 把 MCP 兼容性工作分散在 3 个步骤中,单人开发时容易遗漏。Option C 的独立 M0.5 阶段更显式,降低遗漏风险。

**若反驳成立 plan 应改成什么样**:若遗漏风险显著,应选 Option C。但分析发现:M0.5 的工作量(3 config 字段 + 2 ABC stub + 1 行依赖锁定)总计 < 20 行代码,独立成阶段反而增加阶段切换开销;且这些工作天然属于 M0(config)与 M2(stub)的范畴,拆出会打断阶段内聚。

**结论**:反驳不成立。Option B 的"分散"实为"内聚到正确阶段" —— config 归 M0,stub 归 M2。遗漏风险由步骤 5/16/18 的注记 + 9.6 末尾的兼容性说明段双重防护。

### Tradeoff tensions

1. **显式性 vs 最小代码**:Option C 最显式但过度工程;Option B 最小但兼容性工作分散。取舍依据:MCP 兼容工作量极小(< 20 行),不值得独立阶段,选 B。
2. **步骤编号稳定性 vs 理论整洁**:Option A 重写更"整洁"但打断单人已熟悉的步骤编号。取舍依据:27 步依赖未变,编号稳定性优先,选 B。

### Synthesis path

Option B 即为 A(重写)与 C(新增阶段)的综合:保留 A 的 27 步结构 + 吸收 C 想要的显式性(通过注记 + 兼容性说明段实现)。

### Principle violations

逐条对比 Option B 与 Principles:无违反项。
- 不假设 ✓(逐步比对影响面)
- 最小代码 ✓(6 处微调)
- 外科手术式改动 ✓(每处 cite 行号)
- 可验证成功标准 ✓(grep 命令)
- 三大约束 ✓(不触及上下文/缓存/评估)

---

## Critic verdict

| 维度 | 状态 | 备注 |
|---|---|---|
| Principle-option consistency | ✓ | Option B 与五大 Principles 一致 |
| Fair alternative exploration | ✓ | A/B/C 三 option 真实分化(重写/补丁/新增阶段),各有 invalidation rationale |
| Risk mitigation clarity | ✓ | 每条风险对应可执行 mitigation |
| AC testability | ✓ | 6 条 AC 全部二值可验证 |
| Verification concreteness | ✓ | grep 命令可执行 |
| File/line coverage | ✓ | 6 步实施步骤 100% cite 蓝图 9.4/9.6 行号 |

### Verdict: APPROVED

### Reservations(必填)

1. **步骤 16 的 MCPClient 实现复杂度被低估**:注记"MCPClient 锁定 2025-11-25"看似简单,但 5.3 的 MCPClient 实现仍需处理 stdio/HTTP 双传输、连接池、探活、重试等完整逻辑(约 300-400 行)。注记只说"不实现双协议",但单人开发时可能误以为"MCPClient 很简单"而低估工作量。Mitigation:步骤 16 注记应补"实现量参考 5.3 全文,仅协议层锁定旧版,其余逻辑不减"。
2. **27 步未显式包含 R-2 provider spike**:Critic 前序 plan(mcp-2026-07-28-blueprint-revision)的 R-2 风险(模型侧 JSON Schema 2020-12 兼容性 spike)应在 27 步中有落脚点,但当前步骤 8(模型适配)未提及 schema 兼容性验证。Mitigation:步骤 8 注记补"四家适配器需验证 ToolDef 2020-12 schema 透传(R-2 spike)"。
3. **重排版 docx 的 Step 1-7 流程未被纳入评估**:重排版 docx 定义了"阶段内部 Step 1-7 流程"(dev-grill-docs→dev-plan→dev-tdd→dev-verify→dev-code-review→dev-commit-writer),本 plan 评估了 9.4/9.6 的 27 步但未显式评估 Step 1-7 是否受 MCP 修改影响。结论:Step 1-7 是与协议无关的通用开发流程,不受影响,但 plan 应显式声明这一点。

---

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| R-1: 单人开发时遗漏 MCP 兼容性注记 | 步骤 5/16/18 注记 + 9.6 末尾兼容性说明段双重防护;dev-verify 时 grep 验证 |
| R-2: 步骤 16 MCPClient 工作量被低估 | 注记补"实现量参考 5.3 全文,仅协议层锁定"(Critic reservation 1) |
| R-3: 步骤 8 未覆盖 R-2 provider spike | 步骤 8 注记补"四家适配器验证 2020-12 schema 透传"(Critic reservation 2) |
| R-4: 重排版 Step 1-7 流程是否受影响未声明 | 本 plan 显式声明:Step 1-7 与协议无关,不受影响(Critic reservation 3) |
| R-5: 后续 MCP v2.0.0 stable 发布触发 V2 切换时 27 步需重评 | 9.9 风险十二已建立监控;V2 切换时重跑本评估 |

---

## Verification steps

- 验证 AC-1:本 plan"评估结论"段明确判定"否,不需要重新设计"
- 验证 AC-2:"Viable options"含 A/B/C 三 option + invalidation rationale
- 验证 AC-3:"27 步影响面逐条比对"表标注每步状态
- 验证 AC-4:实施步骤 1-6 cite 蓝图 9.4/9.6 具体行号
- 验证 AC-5:Risks 表 5 条风险每条对应 mitigation
- 验证 AC-6:下方 grep 命令清单

**验证命令清单**(蓝图微调后执行):

```powershell
# 1. 9.4 M0 实施范围含 mcp.protocol_version 注记
Select-String -Path "d:\Private agent\private-agent-blueprint.md" -Pattern "mcp.protocol_version.*9.13"

# 2. 9.6 步骤 5 含 ConfigNotSupportedInMVP
Select-String -Path "d:\Private agent\private-agent-blueprint.md" -Pattern "ConfigNotSupportedInMVP"

# 3. 9.6 步骤 16 含锁定 2025-11-25
Select-String -Path "d:\Private agent\private-agent-blueprint.md" -Pattern "MCPClient 锁定 2025-11-25"

# 4. 9.6 步骤 18 含两个 stub
Select-String -Path "d:\Private agent\private-agent-blueprint.md" -Pattern "AuthProtocol/TasksExtensionAdapter"

# 5. 9.6 末尾兼容性说明段存在
Select-String -Path "d:\Private agent\private-agent-blueprint.md" -Pattern "MCP 2026-07-28 兼容性说明"
```

---

## ADR

- **Decision**:**不重新设计 M0-M4 开发步骤**。保留蓝图 9.4 的五阶段划分与 9.6 的 27 步开发顺序,仅在步骤 5/16/18 + M0/M2 实施范围 + 9.6 末尾做 6 处外科手术式注记补丁。
- **Drivers**:① 阶段划分稳定性(基于架构 DAG,未变)② 关键路径不变性 ③ MCP 修改性质(零运行时变更)④ 27 步依赖图完整性 —— 其中 ①②起决定性作用
- **Alternatives considered**:
  - Option A(重新设计 27 步):**rejected** —— 依赖图未变,重写纯属返工,违反"最小代码"
  - Option B(外科手术式补丁):**chosen**
  - Option C(新增 M0.5 预备阶段):**rejected** —— 工作量极小(< 20 行),独立阶段过度工程,打断 M0→M1 内聚
- **Why chosen**:MCP 2026-07-28 修改全部为注记/ABC stub/config 字段,零运行时逻辑变更,27 步依赖图完整。Option B 以最小代价保留单人已熟悉的步骤编号,通过注记 + 兼容性说明段双重防护遗漏风险。
- **Consequences**:
  - 正面:27 步编号稳定;单人开发无需重新学习步骤;依赖图完整
  - 负面:MCP 兼容性工作分散在 3 个步骤(由注记 + 说明段缓解)
  - 对其他模块:无影响
- **Follow-ups**:
  - MCP v2.0.0 stable 发布触发 V2 切换时,重跑本评估(9.9 风险十二监控)
  - 步骤 8 模型适配阶段执行 R-2 provider spike(Critic reservation 2)

---

## Review trail

- **Planner draft v1**:直接产出 Option B(外科手术式补丁),6 处微调
- **Architect challenge v1**:steelman 不成立(M0.5 过度工程);2 条 tradeoff tension 均支持 B;synthesis 确认 B 为 A/C 综合
- **Critic verdict v1**:APPROVED —— 6 维度全通过;保留 3 条 reservation(步骤 16 工作量低估 / 步骤 8 缺 R-2 spike / Step 1-7 未显式声明),均已有 mitigation
- **Final iterations**:1 / 3

---

## 评估结论

### AC-1 判定:否,不需要重新设计 M0-M4 开发步骤

**依据**:
1. M0-M4 五阶段划分基于四层架构依赖(2.1→2.4→3.1→5.1→7.1→8.1),MCP 修改未触及架构层
2. 9.6 关键路径(四层骨架→进程模型→通信协议→ReAct→上下文→模型适配→工具层→沙箱→Skills→评估)完全未变
3. MCP 修改全部为"注记 + ABC stub + config 字段",零运行时逻辑变更
4. 27 步的"前置依赖→产出物"链路完整

### AC-3 27 步影响面逐条比对

| 步骤 | 模块 | MCP 修改影响 | 状态 |
|---|---|---|---|
| 1 | 四层骨架 + 目录结构 | 无 | 不变 |
| 2 | 进程模型 + Worker 池 | 无 | 不变 |
| 3 | 通信协议(HTTP + WS) | 无 | 不变 |
| 4 | Postgres Schema + 运维 | 无 | 不变 |
| 5 | 配置分层 + API Key 加密 | config.yaml 新增 mcp 三字段 | **微调** |
| 6 | 可观测性(日志 + 事件流) | 无 | 不变 |
| 7 | ReAct 核心循环 + 状态机 | 无 | 不变 |
| 8 | 模型适配(四家) | 需验证 2020-12 schema 透传(R-2 spike) | **微调**(Critic reservation 2) |
| 9-15 | 上下文/记忆/RAG | 无 | 不变 |
| 16 | 双轨工具架构 + MCP Client | 锁定 2025-11-25,不实现双协议 | **微调** |
| 17 | 9 类通用工具 | ToolDef 2020-12 超集(3.8 已落地) | 不变(蓝图已改) |
| 18 | 权限确认 + 超时重试 + 异步事件 + Artifact + 安全 | 新增 AuthProtocol/TasksExtensionAdapter stub | **微调** |
| 19 | 沙箱代码执行 | 无 | 不变 |
| 20-27 | Skills + 评估 | 无 | 不变 |

**汇总**:27 步中 24 步不变,3 步微调(步骤 5/8/16/18,其中步骤 8 为 Critic reservation 补充),0 步新增,0 步移除,0 步重编号。重排版 docx 的 Step 1-7 通用流程不受影响。

### 重排版 docx 评估

重排版 docx 定义的是"阶段内部 Step 1-7 通用开发流程"(dev-grill-docs→dev-plan→dev-tdd→dev-verify→dev-code-review→dev-commit-writer)与"各阶段单开对话窗口"策略。这些是**与协议无关的流程框架**,不受 MCP 2026-07-28 修改影响。docx 中提到的"读取 9.4(MX 阶段)、9.6(对应步骤)、9.13(相关配置段)"开场模板仍然有效 —— 9.4/9.6/9.13 已在本次 MCP 修改中同步更新。
