# 无涯长任务编排能力：方案论证与可行性分析

> 日期：2026-08-28
> 状态：**设计论证稿 v4（待蒋先生最终审定后开工）**
> v2（蒋先生裁决）：监督触发间隔弃用固定 120s → 三级自适应（§4.4.1）；其余五项打开问题落定（§八）
> v3（蒋先生决策）：实施拟用 GLM-5.3-FLASH 省积分 → §6.1 推理密度三档 + 11 任务切分
> v4（蒋先生拍板执行路径）：**WorkBuddy 本对话双阶段模型切换**——阶段一 GLM-5.3-FLASH
> 实施 F1 批（D1/D2 档 9 任务，逻辑层+独立单测）→ 停止交检查点 → 阶段二蒋先生切
> DeepSeek 实施 D 批（D3 档 4 任务，并发核心+接线）；§6.1 重排批次、§6.2 交接协议
> 关联：ADR-012（子代理委派）、next-phase-plan-2026-08-13-subagent-type-concurrency.md、
> next-phase-plan-2026-08-27-v051-deepening-priority.md
> 证据基线：2026-08-28 代码级核实（delegate_subtask.py / subagent.py / main.py / schema.sql / monitor_tools.py）

---

## 一、结论（先行）

1. **方案可行，且复用度高**：现有 SubagentRunner（心跳/watchdog/类型限流）、
   checkpoint、react_events 埋点、`async_tasks` 表（已建未用）、skill_lessons
   （文本经验通道）可直接复用；核心新增是 **Mission 层**（长任务对象 +
   后台编排器 + 监督轮），估计新增代码 ~2070 行（后端 ~1700 + 前端 ~300），
   约 5.5 个工作日分三阶段交付。
2. **根本差距只有一个**：当前 `delegate_subtask` 是**阻塞式工具**——工具执行
   期间主循环挂起、300s 硬总时长封顶、同会话 user_message 被运行锁串行
   （`main.py:1230` per-session lock）。长任务（分钟~小时级）在现有"单 turn
   内委派"模型下**结构上不可能**。出路不是放宽超时，而是把任务生命周期
   **从 turn 中解耦出来**（Mission 后台运行 + 监督轮 + 汇报通道）。
3. **五项需求全部可满足**，但满足方式有本质区别：
   - 需求 1（理解/设计/安排/控制）→ Mission 对象 + 计划/里程碑/状态机
   - 需求 2（环境感知）→ EnvironmentProfile 静态档案 + install_preflight 预检工具
   - 需求 3（防漂移）→ 任务宪章锚定 + 改道预算 + 台账审计
   - 需求 4（并行对话）→ 汇报消息隔离（kind='mission_report'）+ 只读汇报工具
   - 需求 5（子代理范围）→ 三类执行体（subagent/script/wait）+ 能力白名单
4. **明确不做的**（避免过度设计，2026-07 架构原则沿用）：不做嵌套委派（深度
   恒 1）、不做分布式 watchdog（单机桌面）、不做自动重启（默认关）、不新增
   执行引擎（复用 ReactLoop）。

---

## 二、现状与差距（证据链）

### 2.1 现有能力盘点

| 模块 | 现状 | 证据 |
|---|---|---|
| 子代理执行 | SubagentRunner：独立子 session（kind='sub'）+ 复用 ReactLoop + 独立心跳 task + 原子条件更新终态 | `core/subagent.py`（789 行） |
| 子代理监控 | 轮询式 watchdog：90s 心跳超时 → 30s grace → kill；300s 硬总时长；zombie 检测；启动僵尸清理 | ADR-012 §3.3，M2~M4 已验收 |
| 委派限流 | 类型感知（search/analysis/code/other）：同轮同类型去重 + 进程级类型配额（search 全局 1） | 2026-08-13 方案已实施（d93264f） |
| 会话控制 | pause/resume（流程级挂起）、cancel（级联取消）、continue_iteration（迭代上限扩展）、断点恢复（checkpoint） | `main.py:912-1080` |
| 可观测 | react_events 埋点（subagent stalled/killed/zombie 等）+ subagent_status / session_events 跨会话查询工具 | `monitor_tools.py` |
| 未用资产 | `async_tasks` 表（schema.sql:329，含 status/progress/result JSONB）——仅 admin.py 引用，执行链路完全未用 | grep 证实 |

### 2.2 五项需求 vs 差距

| # | 需求 | 现状 | 差距（根因） |
|---|---|---|---|
| 1 | 理解/设计/安排/控制实时长任务 | delegate 阻塞在单 turn 内，300s 硬上限 | **无跨 turn 任务对象**：无计划/里程碑/预算/状态机抽象；300s 上限是刻意设计（防失控），放宽即危险 |
| 2 | 网络环境（境内）+ 硬件环境感知 | code_execution 可装包但无预检；规划层不知镜像/域名/内存约束 | **无环境档案**：模型不知道 pip 要走阿里云镜像、google/github raw 不可达、本机可用内存仅 ~1.9GB |
| 3 | 防偏离工作中心/丢主任务 | 无任何机制 | **无锚定**：子任务失败 → 模型在主对话上下文里自由发挥换方案，n 次改道后上下文漂移，原始目标丢失 |
| 4 | 委派+监控+与用户并行对话（强约束不发散） | turn 运行期间用户消息被 per-session 锁串行排队；监控=delegate handler 阻塞等待 | **无后台执行面 + 无隔离汇报通道**：长任务运行时无涯"不在场"（挂起在工具等待），用户问状态只能等 |
| 5 | 子代理范围/能力/架构 | 类型限流已有；子代理继承父工具全集（permission_manager=None） | **无能力白名单**：search 类子代理理论上可 file_write；无脚本类轻量执行体；委派指令由主对话上下文生成（漂移源头） |

### 2.3 关键机制事实（决定设计走向）

1. **阻塞语义**：delegate handler 是阻塞式工具，并行发生在 runner 层
   （delegate_subtask.py 模块注释）。主循环挂起期间 WS 仍可收消息
   （V2 P1 修复），但新 user_message task 会阻塞在 `_session_locks` 上。
2. **300s 不是 bug 是护栏**：2026-08-12 事故（3 搜索子代理 300s 全灭 +
   Searchpin 通道拖死）证明放并行的代价；类型限流已治本。**长任务方案必须
   保留此护栏用于单子任务，另立任务级总预算，而非简单调大数值。**
3. **监督者的"在场"问题**：watchdog 是纯机制层（DB 时间戳判定），不含
   语义判断——"方向是否正确"必须由模型在监督轮里看台账判断，这是机制
   层无法替代的。
4. **污染风险真实存在**：子代理独立 ctx（好消息）；但主对话若被 mission
   汇报刷屏，会进入压缩/检索链路污染后续对话（坏消息）→ 必须消息级隔离。

---

## 三、总体架构：Mission 层（三层解耦）

```
┌────────────────────────────────────────────────────────────────┐
│ 对话面（用户 ↔ 无涯）                                            │
│   主对话 turn（正常聊天/规划/创建 mission）                        │
│   mission_report 消息（kind 隔离, 只读汇报卡片, 压缩时单独归档）     │
│   mission_status 工具（只读, 用户问进度时无涯调用）                  │
├────────────────────────────────────────────────────────────────┤
│ 监督面（Supervisor）                                             │
│   监督轮: 事件触发(子任务终态/停滞/里程碑) + 定时触发(自适应间隔,    │
│            §4.4.1: 类型分级初始值 + 运行内自校正 + 经验沉淀)        │
│   输入: mission 宪章 + 台账摘要 + 子任务状态(从 DB 读, 非主对话)     │
│   输出: 纠偏动作白名单 {redelegate | adjust_plan | escalate |      │
│          wait_continue | abort}                                  │
│   约束: 无 file_write 等副作用工具; 上下文为合成摘要非会话历史        │
├────────────────────────────────────────────────────────────────┤
│ 执行面（MissionRunner 后台 task, 脱离 turn 生命周期）              │
│   按 plan 派发执行体:                                             │
│   - subagent: 复用 SubagentRunner(心跳/watchdog/类型限流全保留)     │
│   - script:   code_execution 后台 detached(无 LLM 循环, 纯计算)    │
│   - wait:     定时/条件等待                                       │
│   每步写入 mission journal(台账): 意图/委派指令/结果/改道计数        │
├────────────────────────────────────────────────────────────────┤
│ 数据面（PG, 复用 async_tasks 表扩展为 missions 语义）              │
│   mission: charter(goal/DoD/constraints/env_snapshot)            │
│            plan(milestones[]) budget(max_fallbacks/time/tokens)  │
│            state machine + journal(JSONB 追加)                    │
│   子任务: subagents 表(已有) + async_tasks(script 类复用)          │
└────────────────────────────────────────────────────────────────┘
```

**核心解耦**：MissionRunner 是 `asyncio.create_task` 的后台任务（同
`_session_tasks` 注册模式，进程重启后靠 startup 扫描恢复——复用
`cleanup_zombies_on_startup` 思路）。无涯的 turn 在 `mission_create`
工具返回后即正常结束，**不再挂起等待**。监督轮是按需构建的短 turn
（不是常驻循环），每次注入合成摘要（charter + journal 尾部 N 条 +
运行中子任务状态），判定后即释放——token 成本可控。

---

## 四、五项需求的详细设计

### 4.1 需求 1：理解、设计、安排、控制长任务

**Mission 对象**（复用 `async_tasks` 表改名扩展为 missions，或新建表——
见 §八打开问题 1）：

```sql
-- 方案: 扩展现有 async_tasks 表(已建未用, 字段高度吻合)
ALTER TABLE async_tasks RENAME TO missions;  -- 或保留表名仅扩列
-- 扩展列:
--   charter    JSONB  -- {goal, dod[], constraints[], env_snapshot}
--   plan       JSONB  -- [{id, milestone, executor_type, prompt_template,
--                       depends_on[], status}]
--   budget     JSONB  -- {max_fallbacks(默认2), max_total_sec, max_subagents}
--   journal    JSONB  -- 追加式台账 [{ts, kind, detail}]
--   state      VARCHAR -- planning→executing→supervising→
--                        done/failed/cancelled/escalated(等用户裁决)
```

**无涯的入口工具集**（全部 is_kernel=True，闭包注入同 delegate）：

| 工具 | 语义 | 阻塞性 |
|---|---|---|
| `mission_create` | 提交宪章+计划 → 建 mission 行 → spawn MissionRunner → 立即返回 mission_id | **非阻塞** |
| `mission_status` | 读 mission 状态/台账/子任务摘要（用户问进度时用） | 只读 |
| `mission_control` | 用户裁决通道：approve_fallback（批准改道）/ abort / pause | 写操作 |

**计划生成责任在无涯**：mission_create 的参数就是结构化计划（模型生成），
MissionRunner 只负责机械执行 + 把异常抛回监督轮。这保证"设计"是模型的
推理产物，"执行"是确定性代码——各司其职。

### 4.2 需求 2：网络与硬件环境感知

**EnvironmentProfile（静态档案，config.yaml + 缓存注入）**：

```yaml
environment:
  network:
    pip_index: https://mirrors.aliyun.com/pypi/simple/
    npm_registry: https://registry.npmmirror.com
    blocked_domains: [google.com, github.com(raw), huggingface.co, ...]
    reachable_domains: [mirrors.aliyun.com, hf-mirror.com, open.bigmodel.cn, ...]
    proxy_trap: "本机 HTTPS 代理指向失效端口 127.0.0.1:31181, 出网命令需 NO_PROXY=*"
  hardware:
    total_mem_mb: 7800
    mem_budget_mb: 500        # 新增常驻进程内存上限(超过需用户批准)
    cold_start_note: "Python 冷启动 ~16s(杀软扫描), 勿误判为卡死"
```

**两个落地点**：

1. **规划时注入**：无涯构建 mission 计划时，EnvironmentProfile 以缓存的
   自我描述片段注入 system prompt（沿用 v2 按需自省理念：不每轮全量注入，
   mission_create 轮次注入）。
2. **执行时预检**：新增 `install_preflight` 检查（作为 code_execution 装
   依赖路径的前置守卫，或独立工具）：目标域名连通性快测（HEAD 请求 3s
   超时）+ 当前内存水位 + 镜像可用性。**预检不过 → 拒绝安装并返回结构化
   原因**（如"域名不可达，请改用镜像 X"），而非让子代理在运行时反复试错。

**设计原则**：环境约束是**代码守卫**而非"提示词建议"——提示词会被模型
忽略，守卫不会。预检失败返回的错误信息面向模型可理解（给出替代镜像），
让一次改道即成功。

### 4.3 需求 3：防漂移（不偏离工作中心、不丢主任务）

三道防线，层层拦截：

**第一道：宪章锚定（源头）**
- 每个子任务的委派 prompt 由 MissionRunner **从 charter + 当前 milestone
  用模板生成**，不是从主对话上下文自由生成——子代理拿到的指令永远锚定
  在原始目标上，主对话后续跑题不影响已在执行的 mission。
- 模板：`[目标] charter.goal / [当前阶段] milestone / [边界] constraints /
  [完成标准] milestone.dod / [禁止] 不得自行更换数据源/方案, 失败即报告`

**第二道：改道预算（过程）**
- `budget.max_fallbacks`（默认 2）：子任务失败换替代方案（换镜像/换数据源/
  换工具）计一次改道；**超限 → mission 置 escalated，停止一切尝试，等用户
  裁决**（mission_control.approve_fallback 可追加预算）。
- 这直接治"网络问题反复试替代方案导致偏离"：改道是显式记账行为，不是
  上下文里的隐形漂移。

**第三道：台账审计（监督轮）**
- 监督轮每次注入：charter 摘要 + journal 尾部（最近 10 条）+ 各子任务
  状态。判定提示词强制回答三问：①当前动作与 goal 是否一致？②改道次数/
  预算消耗？③是否该 escalate？——**方向检查是监督轮的显式职责**，机制
  层只提供数据。
- 偏差判定标准写入监督轮 system prompt：如"子任务产出与 milestone 的 DoD
  无交集 → 判偏离，redelegate 或 escalate"。

### 4.4 需求 4：委派监控 + 与用户并行对话（强约束隔离）

**并行对话的实现路径**：

1. mission 创建后无涯 turn 即结束 → 会话空闲 → 用户随时可发消息（现有
   per-session 锁不再被阻塞式 delegate 占用）。
2. 用户问进度 → 无涯新 turn 调 `mission_status`（只读）→ 回复。**约束：
   mission_status 的输出是结构化状态（进度/台账/异常），无涯被指示仅作
   状态转述与解释，不得基于状态自行发起改道**（改道只走监督轮/用户裁决
   通道）。
3. mission 主动汇报 → `mission_update` WS 事件（前端任务卡片刷新，**不是
   对话消息**）+ 关键节点（里程碑完成/escalated/终态）落一条
   `kind='mission_report'` 的系统消息（前端渲染为状态卡片样式，视觉上与
   用户/assistant 气泡区分）。

**隔离的三层保障**：

| 层 | 机制 | 防什么 |
|---|---|---|
| 消息层 | mission_report 消息 kind 标记；`build_messages`（ReactLoop）过滤该 kind；压缩归档时 mission_report 单独通道 | 汇报刷屏污染主对话上下文 |
| 工具层 | mission_status 只读；mission_control 需用户侧触发（WS 消息或显式用户指令） | 无涯在汇报对话里"顺手"改任务 |
| 上下文层 | 监督轮用合成摘要（charter+journal+子任务状态），**不含主对话历史** | 主对话跑题反向污染监督判断 |

**前端**：任务面板复用 SubagentPanel 模式——mission 卡片（状态徽标/
进度条/里程碑列表/台账尾部/ escalated 时的裁决按钮），与子任务卡片同屏。

#### 4.4.1 监督触发策略：三级自适应（2026-08-28 蒋先生裁决，替代固定 120s）

固定间隔对异质任务不适用：script 类几十秒就该看一眼，复杂 code 类 120s
只会反复看到"还在跑"。三级策略——**分级起步 → 运行内校正 → 跨任务沉淀**：

**第一级：类型分级初始值**（mission 创建时确定，按 executor_type × 复杂度）：

| executor_type | 初始间隔 | 依据 |
|---|---|---|
| script | 30s | 纯计算无 LLM 循环，进展快，早发现早纠偏 |
| search subagent | 90s | 单模型调用 + 检索循环，节奏中等 |
| analysis subagent | 120s | 同上，计算占比更高 |
| code subagent | 180s | 工具循环长，改代码需消化时间 |
| 复杂任务（milestone 预计步骤 >10 或标记 complex） | 档位 ×1.5 | 复杂任务"无变化"是常态，频繁监督是浪费 |
| wait | 到点即触发 | 无监督意义 |

**第二级：运行内自校正**（同一次 mission 内，无需模型参与，纯规则）：
- 连续 2 次监督轮"无事件、台账零变化" → 间隔 ×1.5（上限 600s）
- 监督间隙内发生 stalled / 心跳超时 / 子任务失败 → 间隔 ×0.7（下限 30s）
- 每次调整写入 journal（`{kind: "interval_adjusted", from, to, reason}`，
  可审计可复盘）

**第三级：跨任务经验沉淀**（mission 终态时一次性执行）：

1. **数值经验（结构化）**：终态统计各子任务实际时长分布、监督触发次数、
   "无变化"比例、改道次数 → 按 `(task_type, executor_type)` 聚合写入新表
   `mission_lessons`（p50/p75 子任务时长、p50 最优监督间隔、样本数，增量
   更新）。**为什么不用 skill_lessons**：间隔是数值参数，需分位数查询做
   初始值，文本表（lesson_content TEXT）每次都要解析全文，不可靠；
   skill_lessons 保留承载**文本型**失败教训（见下）。
2. **文本经验（复用现有通道）**：监督轮在终态时从成功/失败子代理的子
   session 对话中提取一条失败模式摘要（如"搜索类子代理傍晚限流更严，
   建议 18-22 点拉长间隔"）→ 走 `skill_lessons`（EvolutionRepo，scope=
   monitor / lesson_category=project_evolution，约束已支持），下次规划时
   经验检索注入。
3. **下次 mission_create 同类型时**：查 `mission_lessons` 取 p50 间隔作为
   初始值；**样本数 <3 回退默认档**（小样本不信任，防一次异常带偏全局）；
   只统计最近 30 天样本（时间窗查询，过期经验自然淘汰——网络环境/限流
   策略会变，老经验失效是常态）。

**成本边界**：第一/二级零模型调用（纯规则 + 配置）；第三级每个 mission
终态仅一次模型调用（提取文本教训，输入为子 session 摘要非全文）。

### 4.5 需求 5：子代理范围、能力、架构

**三类执行体**（mission plan 的 executor_type）：

| 类型 | 实现 | 适用 | 成本 |
|---|---|---|---|
| `subagent` | 复用 SubagentRunner（独立子 session + 心跳 + watchdog + 类型限流全保留） | 需要多步推理的调研/分析/编码 | 高（LLM 循环） |
| `script` | code_execution 后台 detached（async_tasks 行跟踪状态/进度/结果） | 数据处理/批量计算/文件转换 | 低（无 LLM） |
| `wait` | asyncio 定时/条件等待 | 阶段间隔、限流冷却 | 零 |

**能力白名单**（子代理工具集从"继承父全集"收紧为按类型装配）：

| 子代理类型 | 工具白名单 | 显式排除 |
|---|---|---|
| search | web_search / http_request / MCP 检索类 | file_write、code_execution、delegate |
| analysis | code_execution / file_read / calculator / MCP 数据类 | file_write、web_search（防漂移到网上乱搜） |
| code | code_execution / file_read / file_write（限 workspace） | web_search、delegate |
| script | （无 LLM，白名单即 code_execution 本身的沙箱策略） | — |

架构原则不变：**不发明新执行引擎**。subagent 复用 ReactLoop；script 复用
code_execution；监督轮复用 ReactLoop（一次性构建、独立 ctx）。唯一新执行
体是 MissionRunner——它是纯 asyncio 编排代码，不含模型调用（模型调用只
发生在监督轮与子代理里）。

**边界（明确不做）**：
- 嵌套委派：子代理仍不含 mission_create/delegate → 深度恒 1（沿用现设计）
- 子代理自动重启：默认关（副作用幂等性无保证，ADR-012 M4 结论沿用）
- 分布式：单实例桌面，原子条件更新幂等已足够

---

## 五、可行性论证

### 5.1 复用度量化

| 组件 | 复用来源 | 新增量 |
|---|---|---|
| 数据面 | async_tasks 表（字段吻合度 ~70%）、subagents 表、skill_lessons（文本经验通道） | missions 扩列 ~40 行 + mission_lessons 表 ~30 行 |
| 执行面 | SubagentRunner 全量、code_execution | MissionRunner ~400 行 |
| 监督面 | ReactLoop 构建链路（system_prompt_factory/adapter_factory）、monitor_tools 查询模式、EvolutionRepo（文本经验） | supervisor ~250 行 + 间隔自校正/经验沉淀 ~200 行 |
| 工具面 | delegate 闭包注入模式 | mission 工具集 ~300 行 |
| 前端 | SubagentPanel 卡片模式 | MissionPanel ~300 行 |
| 测试 | test_subagent.py 模式（mock conn/monkeypatch） | ~550 行 |
| **合计** | | **~2070 行，约 5.5 个工作日** |

### 5.2 关键技术风险与对策

| # | 风险 | 评估 | 对策 |
|---|---|---|---|
| T1 | **监督轮 token 成本**（每次触发都调模型） | 中 | 合成摘要限长（charter 200 token + journal 尾部 10 条 + 状态表）；定时触发用**自适应间隔**（§4.4.1：无变化自动拉长至上限 600s，纯等待中不触发）；经验沉淀终态一次性 |
| T2 | **MissionRunner 与用户 turn 并发写同 session** | 中 | mission 的 journal 写 async_tasks 行（不写 messages）；mission_report 消息由独立连接写入；监督轮用独立 ctx 不碰主对话 messages —— 三条写路径互斥 |
| T3 | **进程重启后 mission 恢复** | 中 | startup 扫描 state∈{executing,supervising} 的 mission：子任务层复用 `cleanup_zombies_on_restart`（running→failed）；mission 层新增 `_resume_mission`（读 plan 进度，从当前 milestone 续跑或置 escalated 等用户） |
| T4 | **监督轮误判（假纠偏）** | 中低 | 纠偏动作白名单本身低危（redelegate 一个子任务可逆）；abort/追加预算走 escalate 用户裁决 —— 不可逆动作永不自动执行 |
| T5 | **300s 护栏与长任务子任务冲突** | 低 | 单子代理仍守 300s；长耗时工作拆为多 milestone（每段 ≤300s）或走 script 类型（无 LLM 循环，按 async_tasks 自身超时管理） |
| T6 | **mission 泛滥（用户随手创建大量后台任务）** | 低 | 进程级 running mission 上限（默认 2，config 可调）；超过拒绝创建 |
| T7 | **前端复杂度**（任务卡片+裁决交互） | 低 | 复用 SubagentPanel 已验证的卡片模式；escalated 裁决按钮复用 tool_confirmation 的确认卡片交互 |

### 5.3 本机约束下的取舍（铁律对齐）

- 内存：MissionRunner 是纯 asyncio 协程（KB 级内存）；监督轮/子代理复用
  已有进程，**零新增常驻内存**——符合"新增常驻 >0.5GB 即风险区"裁决线。
- 网络：EnvironmentProfile + 预检把网络约束前移到规划层，运行时试错次数
  由改道预算硬性封顶——直接响应"境内网络不可盲目尝试"约束。
- 硬件：script 类执行体走 code_execution 既有沙箱，不新增依赖；涉及装包
  的 code_execution 步骤必须过 install_preflight。

---

## 六、分阶段实施（A/B/C，每阶段独立可交付可回退）

| 阶段 | 内容 | 工作量 | 验收核心 |
|---|---|---|---|
| **A 最小闭环** | missions 表扩展 + mission_create/status/control 工具 + MissionRunner（subagent/script 串行派发，无监督轮）+ WS mission_update 事件 + 前端 MissionPanel 基础卡片 | ~1.5 天 | ①mission 创建后 turn 立即返回，用户可继续对话；②子任务在后台执行完毕，结果入 journal；③进程重启后 mission 可恢复或干净置 escalated |
| **B 监督与隔离** | 监督轮（事件+定时触发，§4.4.1 一/二级：类型分级初始值 + 运行内自校正）+ 纠偏动作白名单 + mission_report 消息隔离（build_messages 过滤 + 压缩归档分离）+ escalated 裁决交互 | ~2 天 | ①子任务失败 → 监督轮自动重派（改道计数+1）；②改道超限 → escalated，前端出裁决按钮，用户批准后续跑；③mission_report 消息不出现在无涯后续 turn 的上下文里（代码级验证 build_messages 过滤）；④连续 2 次无变化 → 间隔 ×1.5 入 journal；子任务失败 → 间隔 ×0.7 |
| **C 环境感知与经验沉淀** | EnvironmentProfile（config + mission_create 轮注入）+ install_preflight 预检 + 白名单化子代理工具装配 + 进程级 mission 并发上限 + **§4.4.1 第三级跨任务经验沉淀**（mission_lessons 表 + 终态统计 + 同类型初始间隔查询 + skill_lessons 文本教训提取） | ~2 天 | ①计划含装依赖的子任务未过预检 → 结构化拒绝并给出镜像建议；②search 子代理调用 file_write → 工具不存在；③并发第 3 个 mission → 拒绝；④**首个 mission（无历史）用默认档，第二个同类型 mission 初始间隔取 mission_lessons p50；样本 <3 回退默认档**；⑤skill_lessons 出现 monitor/project_evolution 教训记录 |

**依赖关系**：A→B 串行（B 的监督轮依赖 A 的数据面）；C 的环境感知独立于 B
可并行，C 的经验沉淀仅依赖 A 的 mission 终态统计（journal），与 B 并行无冲突。
测试策略沿用 test_subagent.py 模式：mock conn + monkeypatch 隔离 MCP/模型
调用；真实链路验收走"用户实际触发路径"（前端创建 mission → 观察后台执行
→ 重启恢复），不走 test 接口。

### 6.1 原子任务切分与双批次执行路由（v4：本对话模型切换编排）

**执行路径（蒋先生已拍板）**：WorkBuddy 本对话直接实施——阶段一切换
GLM-5.3-FLASH 执行 **F1 批**（D1/D2 档全部任务，交付逻辑层 + 独立单测），
完成后**停止**；蒋先生检查交接物、切换模型至 DeepSeek 执行 **D 批**
（D3 档并发核心 + F1 逻辑层接线）。与 2026-08-15 分工修正一致（WorkBuddy
可直接实施全链路，Trae Code 不再必经）。

**批次划分依据（依赖关系）**：A3（MissionRunner）与 B1（监督轮）是 D3 档
并发核心，B2/B3/B4 的接线层依赖它们——故 F1 批只做**可独立单测的逻辑层**
（接口签名由本文档锁定，接线点留显式 TODO），D 批做核心 + 接线。避免 FLASH
在并发语义区作业，同时防止"逻辑层先行"返工：接口在 §4.3/§4.4.1 已定义。

**推理密度三档**：D1 模式化（有模板可抄）/ D2 常规（跨文件精确修改）/
D3 高推理（asyncio 生命周期/取消传播/原子幂等/调度时序——FLASH 高发错误区，
项目先例：asyncpg "UPDATE N" 比较恒 False、cancel 不 join 拖死 MCP 通道）。

#### F1 批（GLM-5.3-FLASH 执行，9 任务，~1100 行）

| # | 任务 | 内容 | 参考模板 | 验收（独立单测，不依赖 D 批） | 档 |
|---|---|---|---|---|---|
| F1-1 | missions 表扩展 | async_tasks 改名/扩列（charter/plan/budget/journal/state）+ migrations 幂等 + admin.py 引用更新 | subagents migration 模式 | migrate 幂等跑两遍 + DESC 结构正确 | D1 |
| F1-2 | mission 工具集 | mission_create/status/control ToolDef（闭包注入）+ charter 校验 + 并发上限 2；create 建行返回 id，**spawn 接线点留 TODO（A3 补）** | delegate_subtask.py build 模式 | 校验逻辑单测（mock conn）；status 读行/control 改状态可用 | D1 |
| F1-3 | 前端卡片+WS case | mission_update 等 6 类事件 case + MissionPanel 基础卡片 + App.tsx 接线（事件 schema 按本文档先行） | SubagentPanel.tsx | tsc 0 错 + vitest（含 mock 事件渲染） | D1 |
| F1-4 | EnvironmentProfile | config environment 节 + mission_create 轮注入片段（缓存） | v2 按需自省注入模式 | 注入内容快照单测 | D1 |
| F1-5 | install_preflight | 域名连通快测（HEAD 3s）+ 内存水位 + 镜像检查 + 结构化拒绝（含镜像建议） | http_request 工具模式 | 不可达域名→拒绝+建议 单测（mock 网络） | D2 |
| F1-6 | 白名单+并发注册表 | 子代理工具集按类型过滤 + 进程级 mission 注册表（acquire/release） | SubagentTypeRegistry 模式 | search 子代理无 file_write 单测；并发上限计数单测 | D1 |
| F1-7 | 改道预算逻辑层 | BudgetLedger 纯逻辑：fallback 计数/超限判定/追加（approve_fallback） | 类型限流计数模式 | 计数/超限/追加状态机单测 | D2 |
| F1-8 | 消息隔离逻辑层 | mission_report kind 标记 + build_messages 过滤 + 压缩归档分离（测试数据驱动，不等真实 mission） | 批次3 回放过滤模式 | kind 过滤单测（代码级验证不进上下文） | D2 |
| F1-9 | 间隔自适应纯函数 | 类型分级初始值表 + 校正函数（×1.5/×0.7、上限 600s/下限 30s）+ journal 记录结构 | heartbeat 参数模式 | 校正规则全分支单测 | D2 |

#### D 批（DeepSeek 执行，4 任务，~950 行）

| # | 任务 | 内容 | 接线对象（F1 产出） | 验收 | 档 |
|---|---|---|---|---|---|
| D-1 | MissionRunner 核心 | 后台 task 派发（subagent 串行复用 SubagentRunner + script detached）+ journal 追加 + 终态落库 + _session_tasks 注册 + startup 恢复扫描 + **mission_create spawn 接线（F1-2 TODO 点）** | F1-2 | 重启恢复/取消传播/无僵尸残留集成测；turn 立即返回 | **D3** |
| D-2 | 监督轮构建 | 一次性 ReactLoop（合成摘要 system prompt）+ 事件/定时触发调度器 + **F1-9 校正函数挂载**（无变化拉长/失败缩短） | F1-9 | 触发时机/摘要限长/无主对话污染单测 | **D3** |
| D-3 | 纠偏动作执行器 | redelegate/adjust_plan/escalate/abort 动作执行 + **F1-7 预算接线** + escalated 前端裁决按钮 | F1-7/F1-3 | 改道超限→escalated 集成测；裁决批准续跑 | **D3**（含 B3b 前端） |
| D-4 | 经验沉淀 | mission_lessons 表 + 终态统计（p50/p75 增量）+ 初始间隔查询（样本<3 回退）+ skill_lessons 文本教训提取 | F1-9 | V7 四项验收 | **D3** |

**任务大小约束**（"大小合适"操作定义）：单任务 ≤300 行、单文件为主、
spec 自包含（目标文件 + 参考模板路径 + 接口签名 + 验收命令 ≈10~15K token
任务包），保证 FLASH 单次上下文完整承载。

**降险四原则**：① spec 填空式（接口签名先锁，模型做填空不做设计）；
② TDD——F1 批每任务自带单测、D 批任务测试先行（spec 阶段先落测试）；
③ 原子提交（每任务一个 commit，根因/实现/验证三段式 message，可 revert）；
④ D3 全部隔离在 DeepSeek 批（积分结构：~53% 行数走免费 FLASH 档，
~47% 高推理行数走 DeepSeek，质量优先于极限省钱）。

### 6.2 双批次交接协议（F1 → D 检查点）

**F1 批停止条件（三者齐备才停）**：
1. 9 任务全部独立 commit 完成（含三段式 message）；
2. 全量基线绿：后端 pytest（cwd=backend、加载 .env、
   `--ignore=tests/test_eval_full_cycle.py`、勿两进程并发同一测试库）+
   前端 tsc 0 错 + vitest 全过；
3. 交接文档落盘 `docs/handoff-mission-F1-2026-08-XX.md`。

**交接文档内容（防模型切换后上下文稀释）**：
- commit 清单（hash + 任务号 + 一句话内容）
- **TODO 接线点精确清单**：F1-2 mission_create spawn 点（文件+行号+期望
  行为）、F1-9 校正函数挂载点、F1-7 预算接线点——D 批第一动作即按单消项
- 测试基线结果（数字留档，D 批结束对照零回归）
- D 批 4 任务 spec 索引（指向本文档表格）

**蒋先生检查点动作**：review 交接文档 → 本对话切换模型至 DeepSeek → 指令
"开始 D 批"（显式检查点驱动，与既有协作模式一致）。

**D 批收尾（全方案闭环）**：全量回归 + 真实链路验收 V1~V7（走用户实际触发
路径：前端创建 mission → 观察后台执行/并行对话 → 重启恢复）→
phase-closeout-mission-<date>.md → 记忆宫殿双写 → **打包由蒋先生手动
build-electron.bat（铁律，AI 不执行）**。

**本机执行注意事项（WorkBuddy 会话内）**：pytest 冷启动约 16s（杀软扫描）
正常勿误判重试；git 操作前 `git status`/`git fsck` 确认健康、**禁 stash**
（8-19/8-23 两次 objects 损坏先例）；无管理员权限。

---

## 七、备选方案对比（为什么选 Mission 层而非其他）

| 方案 | 思路 | 优点 | 致命缺陷 | 裁决 |
|---|---|---|---|---|
| **甲：放宽现有 delegate** | 调大 300s、去掉阻塞（后台化 delegate） | 改动最小 | ①无计划/预算/监督抽象，需求 1/3/4 全不满足；②watchdog 语义（心跳）与小时级任务不匹配；③主对话上下文仍是唯一锚点，漂移照旧 | 否 |
| **乙：Mission 层（本文）** | 任务生命周期从 turn 解耦 + 监督轮 + 隔离汇报 | 五项需求全覆盖；复用度高；护栏保留 | 新增一层概念（学习/维护成本）；监督轮 token 成本需控制 | **推荐** |
| **丙：多智能体黑板** | mission 状态放共享黑板，各 agent 自由读写 | 架构学界时髦 | 过度设计（违反 2026-07 原则）；单用户单机无竞争需求；黑板一致性成本远超收益 | 否 |

---

## 八、打开问题裁决记录（2026-08-28 蒋先生已全部拍板）

| # | 问题 | 裁决 |
|---|---|---|
| 1 | 表策略：扩展 async_tasks vs 新建 missions 表 | **扩展现有表**（已建未用、字段吻合、零迁移风险；改名涉及 admin.py 引用一并更新） |
| 2 | 监督轮模型 | **继承会话模型**（fallback 链首），config 可锁定；成本敏感后续可降级规则引擎 |
| 3 | mission 并发上限 | **默认 2**（内存/网络约束下保守起步，config 可调） |
| 4 | 监督触发间隔 | **弃用固定 120s，三级自适应**（§4.4.1）：类型分级初始值 + 运行内自校正 + 跨任务经验沉淀（mission_lessons 结构化 p50 + skill_lessons 文本教训，30 天时间窗，样本 <3 回退默认档） |
| 5 | mission_report 是否入 messages 表 | **入表 + kind 隔离**（可追溯、断线重连可见；过滤是代码层确定性行为） |
| 6 | script 类沙箱策略 | **与交互式 code_execution 共用沙箱单例**（语义不变），detached 脚本排队执行 |

---

## 九、验收标准（总）

| # | 验收项 | 量化标准 |
|---|---|---|
| V1 | 长任务并行对话 | mission 执行中（≥10min 场景模拟），用户发消息 3s 内无涯响应（调 mission_status 汇报），主对话上下文零 mission_report 污染 |
| V2 | 防漂移 | 子任务连续失败 2 次（模拟网络不可达）→ 第 3 次尝试前 mission 置 escalated 等用户，journal 记录改道计数 |
| V3 | 环境守卫 | 装依赖子任务未过预检（模拟 google.com 不可达）→ 拒绝执行 + 返回镜像建议，子代理零次盲目安装尝试 |
| V4 | 监督纠偏 | 子代理产出与 DoD 无交集（模拟）→ 监督轮 redelegate 附带纠偏指令，journal 记录判定依据 |
| V5 | 重启恢复 | mission 执行中 kill 后端进程 → 重启 → 子任务僵尸清理 + mission 恢复或 escalated，无"永远 executing"残留 |
| V6 | 回归基线 | 后端 pytest 全过 + 前端 tsc 0 错 + vitest 全过；现有 delegate_subtask 行为零回归 |
| V7 | 自适应监督 | ①同类型第二个 mission 初始间隔取 mission_lessons p50（代码级验证查询路径）；②连续 2 次无变化 → 间隔 ×1.5 且 journal 留痕；③样本 <3 回退默认档；④skill_lessons 出现终态提取的 monitor/project_evolution 教训 |

---

## 十、版本与流程

- 版本定位：**0.6.0**（新能力域，非深化，v4 已定）
- 执行流程（v4 定稿）：本文档最终审定 → **F1 批（本对话 GLM-5.3-FLASH，
  9 任务逻辑层 + 单测 + 交接文档）→ 停止 → 蒋先生检查点 + 切换 DeepSeek →
  D 批（4 任务并发核心 + 接线）** → 全量回归 + 真实链路验收 V1~V7 →
  phase-closeout-mission-<date>.md → 记忆宫殿双写
- 功能阶段 A/B/C（§六）与执行批次 F1/D（§6.1）是两个维度：前者按能力
  交付划分（验收视角），后者按执行模型划分（积分与风险视角）；任务映射
  见 §6.1 两张表（F1-1/2 对应 A，F1-3~9 与 D-2/3 对应 B/C，D-1 对应 A 核心）
- 打包：不执行，蒋先生手动 build-electron.bat（铁律）
