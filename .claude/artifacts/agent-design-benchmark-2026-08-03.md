# Agent 设计基准调研与借鉴落地报告（opencode / Hermes / OpenCowork）

**调研日期**: 2026-08-03
**调研对象**: GitHub 优秀开源智能体项目 —— opencode(SST)、Hermes(Nous Research)、OpenCowork(AIDotNet/OpenCoworkAI)
**调研维度**: LLM 提示词设计 / 多轮对话响应机制 / Agent 使用体验
**落地目标**: 在不破坏 Private Agent 原有架构（三区 KV Cache、ReAct 状态机、frozen hash、沙箱、MCP 双轨）的前提下，让对话更流畅、功能更完善

---

## 结论

**三项高价值借鉴已落地（提交 `53a6922`）**：
1. **Doom Loop 死循环检测**（源自 opencode）——工具反复调用死循环提前终止，省 token 且防卡死
2. **运行时环境注入**（源自 opencode）——状态栏注入工作目录/运行平台
3. **SOUL 身份段**（源自 Hermes SOUL.md）——system prompt 首位稳定身份 + 协作规则

其余借鉴点经评估为**已有等价实现 / 与当前单机桌面架构不符 / 过度设计**而跳过（详见 §5）。后端全量 901 passed。

---

## 一、调研对象与架构亮点

### 1.1 opencode（SST 出品，终端 AI 编码 Agent，Rust）
| 设计点 | 做法 | 对我们的启发 |
|---|---|---|
| **动态 System Prompt 组装** | 按 Provider/模型定制（Claude→PROMPT_ANTHROPIC、GPT→PROMPT_BEAST…）；注入运行时环境：工作目录/Git 状态/OS/日期 | 运行时环境信息对工具行为质量影响大（如知道 branch 才能正确 commit） |
| **Agentic Loop 事件流** | 每迭代 = LLM 思考 → 工具调用 → 结果；流式事件 text-delta/reasoning-delta/tool-call/finish-step | 与我们的 react_event 事件流同构 |
| **Doom Loop 检测** | 跟踪工具调用历史，识别重复模式提前终止（不只靠 max_steps 硬限） | **高价值**：循环在 2-3 次迭代就能拦下，不必等 10 次上限烧 token |
| **模型感知工具过滤** | GPT 系列用 apply_patch、其他用 edit/write | 多模型下工具格式适配 |
| **metadata 实时 UI** | 工具执行中回调更新 UI（bash 流式输出进度） | 我们已有 sandbox_output 流式 |
| **Edit 工具 9 层 fallback** | LLM 输出不确定性 → 9 级宽松匹配兜底 | 我们无文件编辑工具（沙箱执行），跳过 |

### 1.2 Hermes（Nous Research，自进化 Agent，10 万 star）
| 设计点 | 做法 | 对我们的启发 |
|---|---|---|
| **SOUL.md 身份层** | 单文件静态 persona，永远在 system prompt **#1 位置**，跨会话一致 | **高价值**：身份稳定，不随场景切换漂移 |
| **三层记忆** | Tier1：MEMORY.md(≈800t)+USER.md(≈500t) 冻结注入、满自动合并；Tier2：SQLite FTS 会话全文检索+LLM 摘要；Tier3：外部记忆提供商 | Tier1 ≈ 我们的 Stable Zone+记忆注入；Tier3 ≈ 已接入的 mempalace |
| **技能沉淀（程序性记忆）** | 完成任务自动固化 SKILL.md（触发条件+步骤+坑），渐进式披露 | 我们有静态 skills 体系；自动学习偏重，未做 |
| **中断支持** | 运行中新消息触发 agent.interrupt() | 我们有 WS confirmation（B2），用户输入打断未做 |
| **智能消息切分** | 流式分块保持代码块边界 | 我们前端纯文本累积，无 markdown 渲染，暂不需要 |
| **90 轮硬上限+共享预算** | 防 stuck loop 静默烧光额度 | 我们已有 max_iterations=10 硬上限 |

### 1.3 OpenCowork（开源桌面多智能体协作平台）
| 设计点 | 做法 | 对我们的启发 |
|---|---|---|
| **多 Agent 模式** | chat/clarify/cowork/code/acp，每次对话选模式 | 我们已有三场景 Skill 模式（办公/数据分析/前端设计） |
| **人在回路** | 透明工具调用审批 | 我们已有权限确认（60s 超时+会话缓存） |
| **markstream 增量渲染** | LLM 返回 md 时只渲染新增部分 | 我们 delta 累积显示已等效 |
| **Plan Mode** | EnterPlanMode→写计划→ExitPlanMode 结构化可审阅 | 契合用户"先探索→再决策→再执行"习惯；实现成本高，列入可选 |
| **TracePanel 推理轨迹** | AI 推理过程可视化面板 | 我们已有 thinking 事件+前端"查看推理过程" |
| **Goal Tracking** | 会话级目标 + token 预算 | 可选增强 |

---

## 二、可借鉴点全景对照（落地决策）

| # | 借鉴点 | 来源 | 本项目现状 | 决策 |
|---|---|---|---|---|
| 1 | Doom Loop 死循环检测 | opencode | 仅 max_iterations=10 硬限 | ✅ **落地** |
| 2 | 运行时环境注入 | opencode | 状态栏只有时间/轮次/工具计数 | ✅ **落地** |
| 3 | SOUL 身份段 | Hermes | system prompt 直接是 skill 提示词 | ✅ **落地** |
| 4 | 三层记忆体系 | Hermes | Stable Zone 注入 + mempalace 已接入 | ✅ 已覆盖 |
| 5 | 动态 prompt 按 provider 定制 | opencode | 单 prompt 结构 | ⏭ 暂缓（单模型为主） |
| 6 | 模型感知工具过滤 | opencode | 工具集与模型解耦 | ⏭ 暂缓 |
| 7 | 运行中用户消息中断 | Hermes | 仅 confirmation 消息 | ⏭ 中风险（状态机并发），可选 |
| 8 | 任务后自动技能沉淀 | Hermes | 静态 skills 三场景 | ⏭ 过度设计 |
| 9 | 代码块感知流式切分 | Hermes | 前端纯文本渲染 | ⏭ 无 markdown 渲染，暂不需要 |
| 10 | Plan Mode / Goal Tracking | OpenCowork | 三场景模式 | ⏭ 成本高，可选后续 |
| 11 | 多 Agent 模式 / 人在回路 / TracePanel | OpenCowork | 已有等价（场景 Skill/权限确认/thinking） | ✅ 已覆盖 |

---

## 三、落地实现明细（提交 `53a6922`）

### 3.1 Doom Loop 死循环检测（`core/react_loop.py`）
- **跟踪**：本轮内工具调用 trace 累积 `"tool_name:args_hash"`（按**整个对话轮**累积，跨迭代；跨轮重置）
- **两种循环模式**：
  - `same_args`：同工具 + 同参数重复 ≥3 次（默认，`same_args_threshold`）
  - `same_tool`：最近 8 次调用中同工具 ≥5 次（默认，`same_tool_threshold`）
- **收敛机制（先礼后兵）**：
  1. 首次检测到循环 → 向模型上下文注入 `[System Note]` 提示（仅内存、不持久化、不污染对话历史），引导换参数/换工具/直接回答
  2. 提示满 `max_warnings`（默认 2）后仍循环 → **本轮强制终止**：emit final（"检测到工具调用死循环…"）+ 不执行当前工具
- **可观测**：触发时写 `tool_loop_detected` 事件（react_events 白名单 + CHECK 扩容）
- **配置**（`config.yaml context.loop.*`）：`enabled / max_warnings / same_args_threshold / same_tool_threshold`，`enabled=false` 整体关闭
- **收益**：模型陷入"反复调同一工具重试"的典型 stuck loop 时，第 3-5 次即被提示收敛，不再烧到 10 次上限

### 3.2 运行时环境注入（`core/status_bar.py` + `core/react_loop.py`）
- 状态栏 `render()` 新增 `workspace` / `platform` 参数
- ReactLoop 启动时从 cfg 读 `system.workspace_root`（expandvars 展开）+ `platform.system()`
- 状态栏每轮注入上下文末尾：
  ```
  当前时间: …
  对话轮次: …
  工具迭代: …
  当前状态: …
  工作目录: D:\Private agent\backend      ← 新增
  运行平台: Windows                        ← 新增
  工具调用: …
  工具失败: …
  ```
- **收益**：模型"瞥一眼"即知当前项目目录与平台，无需猜测环境（对路径类工具调用、平台差异判断有帮助）

### 3.3 SOUL 身份段（`main.py`）
- `_DEFAULT_IDENTITY`：内置稳定身份 + 协作规则（源自用户记忆中的协作偏好）：
  ```
  你是 Private Agent —— 运行在用户本机的个人桌面智能体。
  协作规则:
  1. 提建议时给出明确选项 + 理由, 不要列开放式菜单。
  2. 安全明确的任务直接执行再汇报, 不先请示; 危险/不可逆操作先确认。
  3. 回答基于证据、结构清晰、没有废话; 不确定时明确承认。
  4. 使用与用户一致的语言交流。
  ```
- `_identity_prompt(cfg)`：`config context.identity` 配置覆盖 > 内置默认
- 注入位置：`_get_system_prompt` 顶层拼接，**永远在 system prompt 首位**（身份 → 场景/skill 提示词 → MCP 工具速查指南）
- **架构兼容**：system prompt 变化会触发 frozen_hash 重建 → 旧会话走 `replace_frozen_zone` 自动重建（已有机制，无需改存储）

---

## 四、测试与验证

| 项 | 结果 |
|---|---|
| `tests/test_react_loop_loop_detection.py`（新增 6） | ✅ same_args/same_tool 检测、交替调用不误报、提示注入（仅内存不落库）、超限强制终止 |
| `tests/test_main_identity.py`（新增 3） | ✅ 默认身份/配置覆盖/prompt 首位 |
| `tests/test_react_loop.py::test_run_turn_max_iterations_default_is_ten`（更新 1） | ✅ 显式 `loop.enabled=false` 保持纯测 max_iterations 硬上限 |
| 受影响相关测试（9 文件 57 项） | ✅ 全过 |
| **后端全量 pytest** | ✅ **901 passed**（原 892 + 9 新增） |
| 前端 | 无改动（纯后端优化） |

**实现中的坑（已解决）**：
1. trace 误放 while 迭代内（每迭代清空 → 永远检测不到跨迭代循环）→ 移到 `run_turn` 开头按轮累积
2. 注入提示与强制终止共用同一阈值 → 首次注入后立即满足终止条件（第 3 次迭代就误终止）→ 改为"注入后继续执行，**下一次**再检测到循环才终止"
3. 原 max_iterations 测试用"永远同一工具调用"场景，被新检测提前终止 → 测试显式关闭 loop 检测，职责分离

---

## 五、未落地借鉴点及理由

| 借鉴点 | 跳过理由 | 后续条件 |
|---|---|---|
| 运行中用户消息中断 | 与 ReAct 状态机并发复杂度高，当前仅 confirmation 消息；B2 已保证运行期可收确认消息 | 用户明确需要"打字打断"时再做 |
| 任务后自动技能沉淀 | 我们已有静态三场景 skills + 手动管理；自动写 SKILL.md 质量不可控 | Hermes 的渐进式披露模式可参考 |
| 代码块感知流式切分 | 前端为纯文本累积渲染，无 markdown 渲染库；切分优化无对象 | 前端引入 markdown 渲染时 |
| Plan Mode / Goal Tracking | 实现成本高（新会话模式+UI）；当前三场景模式已覆盖"按模式对话" | 用户"先探索再执行"习惯可迁移为 clarify 模式 |
| 动态 prompt 按 provider 定制 | 当前以 deepseek-flash 单模型为主；多模型并重时再按 provider 定制 | 引入第二主力模型时 |

---

## 六、后续可选项（按价值排序）

1. **Plan/Clarify 会话模式**：对齐用户"先探索→再决策→再执行"的分阶段指挥习惯（OpenCowork clarify 模式）
2. **前端 markdown 渲染 + 代码块感知流式切分**：提升长回答/代码展示体验（Hermes）
3. **会话历史语义检索工具**：基于 mempalace（Tier3 已就位）把历史对话检索能力暴露给模型
4. **运行中用户消息中断**：体验增强，需谨慎设计状态机并发

---

## 附：参考来源
- opencode 架构剖析（zengineer.blog 中英版）、opencode.ai/docs
- Hermes Agent 深度解析（agentarchitectures.com / 钛媒体 / Tencent Cloud）、NousResearch/hermes-agent
- OpenCowork GitHub（AIDotNet/OpenCowork、OpenCoworkAI/open-cowork）
