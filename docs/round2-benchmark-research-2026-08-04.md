# PA 第二轮借鉴调研报告：Claude Code / OpenClaw / Pi Agent / Hermes Agent

> 调研日期：2026-08-04
> 调研对象：Claude Code（Anthropic）、OpenClaw（原 Clawdbot/Moltbot）、Pi Agent、Hermes Agent（Nous Research）
> 关联文档：`private-agent-blueprint.md`（蓝图 454KB）、`docs/phase2-iteration-plan-2026-08-04.md`（阶段二安全硬边界，批次 1-3 已完成，基线 pytest 933 + vitest 13）
> 目标：为 PA 持续迭代提供科学、可落地的借鉴输入，方案与现有架构强耦合

---

## 〇、调研方法论

### 0.1 调研框架

本报告采用"**统一评估维度 + 横向对比矩阵 + 模块级映射**"三阶段方法：

1. **独立分析**：对每个智能体按六个统一维度独立拆解（架构设计 / 核心能力 / 交互模式 / 权限与安全 / 扩展性与生态 / 局限与短板），并针对 PA 三个重点研究方向（人在环中、最小内核、自进化）专项深挖。
2. **对比矩阵**：横向对比四智能体与 PA 的异同，标注每项差异的借鉴价值（高/中/低）。
3. **方案映射**：每个可借鉴点必须落到 PA 的**具体模块**（文件级 + 蓝图章节级），给出技术可行性（P0 高可行 / P1 中可行 / P2 需验证）与实现成本（人日），禁止"另起炉灶"式建议。

### 0.2 信息采集方式与可信度分级

| 信源类型 | 可信度 | 说明 |
|---|---|---|
| 官方文档 / 官方仓库 / 官方博客 | ★★★ | 第一手资料，本报告主体依据 |
| 社区评测 / 知名开发者文章 | ★★ | 用于补齐官方文档未覆盖的"局限与短板"视角 |
| 二手转述 | ★ | 仅作方向性参考，不据此下结论 |

> ⚠️ 局限性声明：四对象均为快速迭代中的产品，本报告基于 2026-08 时点的公开资料；Claude Code / OpenClaw 资料充分，Pi Agent 与 Hermes Agent 资料相对有限（报告中已如实标注证据强度）。

### 0.3 PA 对比基准（本项目现状快照）

在横向对比前，先固化 PA 自身基线（来自代码走查 + 蓝图 + 阶段二计划）：

| 维度 | PA 现状 |
|---|---|
| **运行架构** | Electron 桌面壳 + FastAPI Sidecar + PostgreSQL 16（本地优先）；后端 `backend/private_agent/` 12 个子包 |
| **Agent 循环** | ReAct 五态状态机（`core/react_loop.py`：IDLE/THINKING/ACTING/OBSERVING/ERROR），max_iterations=10，asyncio 事件队列 + WS 流式推送（`main.py /ws`），断点续传（`core/checkpoint.py`） |
| **上下文工程** | 三区 Zone（`core/context_manager.py`）：frozen（system+工具，hash 固化 KV Cache）/ stable（记忆+KB 片段+压缩摘要）/ active（当轮消息）；滑动窗口+摘要压缩（`core/compressor.py`，熔断器）；提示注入防护（`core/injection_guard.py`）；状态栏注入（`core/status_bar.py`） |
| **工具层** | 双轨（内置 9 类 + MCP，`tools/registry.py`）；每轮 ToolSelector top-N 挑选（`tools/selector.py`）；MCP stdio/HTTP 双传输 + 协议协商（`tools/mcp_client.py`）；Artifact 截断存储（`tools/artifact.py`）；超时重试（`tools/retry.py`） |
| **权限与安全** | 三级安全分级（`tools/permission_manager.py`）：elevated 工具 WS 确认、60s 超时拒绝、会话级缓存；admin 鉴权 + CORS 白名单（阶段二批次 1 完成）；SSRF 防护（阶段二批次 2 完成）；沙箱 Job Object + 禁网（阶段二批次 3 完成）；白名单+审计+资源限额（`tools/security.py`） |
| **记忆与知识** | LLM 摘要提取（每 8 轮+会话结束，`memory/manager.py`）、四类记忆、淘汰合并、Stable Zone 注入；KB 文档流水线（bge-m3 embedding + HNSW + 混合检索 + reranker）；MemPalace 记忆宫殿接入（36 MCP 工具） |
| **Skill 体系** | PG + 文件双源加载（`skills/loader.py`）、会话级激活 + 版本锁定 + Frozen hash、工具白名单（`skills/manager.py`）、少样本注入（`skills/example_loader.py`） |
| **评估闭环** | `eval/` 包已实现：离线批量 + 交互式回放（`runner.py`）、LLM-as-Judge（`judge.py`）、五类指标（`metrics.py`）、版本对比（`version_compare.py`）、回滚（`rollback.py`）、弱样本提取（`weak_sample.py`）——**M4 评估闭环已提前落地** |
| **沙箱** | 子进程隔离 + 工作目录隔离 + 资源限制 + 网络拦截（阶段二批次 3 修复完成） |
| **配置** | config.yaml + config_runtime 双层，provider 全动态注册（无预置） |

**当前测试基线**：后端 933 passed（`pytest tests/ --ignore=test_eval_full_cycle.py`）+ 前端 13 passed。

---

## 一、四智能体独立分析

### 1.1 Claude Code（Anthropic）— 终端编码 Agent 的事实标准

**定位**：Anthropic 的终端编码智能体，2025-02 发布，2026-08 时点 v2.1.219，单引擎多端（CLI/IDE/Desktop/Web/Mobile）。是"人在环中 + Hooks + 生态"三维度最成熟的参照物。

| 维度 | 关键事实 |
|---|---|
| **架构设计** | 单引擎多端：CLI/IDE/Desktop/Web/Mobile 共享同一 `queryLoop`（异步生成器），"interface-engine separation"；推理归模型、harness 只做基建（~1.6% 代码是 AI 决策，其余是权限/工具/状态/执行子系统）；工具池约 54 个内置（Read/Edit/Write/Glob/Grep/Bash/WebFetch/LSP/Agent/AskUserQuestion）+ MCP 动态装配（`assembleToolPool`） |
| **核心能力** | 编码闭环（读→改→跑→观察→迭代）；CLAUDE.md 四级记忆（managed/user/project/local）+ `.claude/rules/*.md` 路径规则 + auto memory（`~/.claude/projects/<project>/memory/`，MEMORY.md 索引）；auto-compact（65-70% 阈值）；`/rewind` + checkpoint 时间旅行；Skills（SKILL.md，描述常驻+正文按需加载）；Subagents（Task/Agent 工具，独立上下文窗口仅回传摘要，嵌套深度 3、并发 20） |
| **交互模式** | CLI：Ctrl+C 中断/二次退出、Esc 打断、Shift+Tab 切权限模式、`!` shell 模式、`/` 命令、`@` 文件补全、Ctrl+B 后台任务；非交互 `claude -p`（--output-format text/json/stream-json、--allowedTools 预授权、--resume/--continue 续接、--bare 确定性） |
| **权限与安全** | **7 权限模式**（default/manual/acceptEdits/plan/auto/dontAsk/bypassPermissions/bare）；allow/ask/deny 规则（`Tool(specifier)` 语法，deny 优先于一切 allow）；**29 个 Hooks 事件**（PreToolUse/PostToolUse/UserPromptSubmit/Stop/PermissionRequest/PreCompact 等，type: command/http/mcp_tool/prompt/agent，输出支持 permissionDecision/updatedInput/additionalContext）；OS 级沙箱（macOS Seatbelt/Linux bubblewrap）+ 网络域白名单（strictAllowlist）；注入防护：敏感工具强制审批、WebFetch 独立上下文、命令注入检测、fail-closed 匹配 |
| **扩展与生态** | MCP 4 种 transport（stdio/HTTP/SSE/WebSocket）+ 官方连接器目录 + OAuth2；skills 即插即用 + plugins/marketplace；Agent SDK（Python/TS，工具批准回调）；官方 GitHub Actions；OTel 监控 |
| **局限短板** | 模型锁定（仅 Claude 系）；token 消耗大（社区报 10-20x 隐藏 cache 成本，cache trap）；长会话 auto-compact 偶发重试循环；Pro 配额紧张；无免费层；IDE 集成实为内嵌终端 |

**对 PA 的可借鉴点（按价值排序）**：
1. **Hooks 生命周期系统**（29 事件）→ PA 目前权限确认是硬编码在 PermissionManager 内的 WS 确认，缺通用生命周期钩子；可借鉴"事件注入 + 决策回写"模型（详见 §4.2）。
2. **权限规则 allow/ask/deny + deny 优先** → PA 现在是三级 safety_level 静态分级，缺规则化求值；可扩展为 `Tool(specifier)` 风格规则表（详见 §4.2.1）。
3. **auto memory（纠正沉淀 MEMORY.md）** → PA 记忆提取是 LLM 摘要式被动提取，缺"用户纠正→经验沉淀"通道；可与 MemPalace 打通（详见 §4.4.1）。
4. **Subagents 独立上下文仅回传摘要** → PA 无子代理；长任务可借鉴"子任务隔离上下文 + 摘要回传"降低主上下文压力。
5. **fail-closed 权限匹配 + 命令风险分级（Ctrl+E Low/Med/High）** → PA 60s 超时拒绝已是 fail-closed，可补"风险分级可视化"前端。

---

### 1.2 OpenClaw（原 Clawdbot/Moltbot）— 个人 Agent 平台的最强开源实现

**定位**：Peter Steinberger 2025-11 开源的个人 AI 助手平台（MIT，TypeScript），2026-02 移交开源基金会，20 万+ stars。核心口号"The AI that actually does things"。是"最小内核 + 自由组合 + 多通道"的最佳参照物。

| 维度 | 关键事实 |
|---|---|
| **架构设计** | **双层核心**：Gateway 控制平面（WebSocket，会话/配置/cron/webhook/频道路由）+ Pi 极简 Agent 运行时（仅 4 个内置工具 Read/Write/Edit/Bash，业界最短系统提示词）；Agent runtime / 编排层 / 通道三向解耦；**Channel Adapters 多通道抽象**（20+ 平台归一化为 `sessionKey="{channel}:{account}:{target}"`，指数退避自动重启）；Lane Queue 并发控制（默认串行防竞态，Main/Cron/Subagent/Nested 车道）；Steer 模式工具边界注入新消息 |
| **核心能力** | 49-100+ 内置技能；模型路由（8+ provider 降级瀑布 + API key 轮换 + thinking 降级 extended→fast→off）；会话树分支/回卷故障恢复；子代理跨会话委托（sessions_spawn/sessions_send）；语义化浏览器（ARIA 可访问性树，<50KB 文本 vs 5MB 截图）；cron + heartbeat；**三层人类可读记忆**（会话转录 JSONL + Markdown 持久记忆 + 混合检索 0.7 向量+0.3 BM25，时间衰减+MMR 重排） |
| **交互模式** | 消息平台 + Control UI + WebChat + CLI + 原生 App（macOS/iOS/Android）；会话状态经 sessions.patch 持久化（重启不丢）；首次授权 DM 配对（dmPolicy: pairing/allowlist/open/disabled）；无头守护进程（launchd/systemd） |
| **权限与安全** | **技能权限声明**（SKILL.md frontmatter 的 metadata.openclaw：requires.env/bins/install，ClawHub 安装明示 Required Permissions，可事后降权）；**exec 五种模式** deny/allowlist/ask/auto/full（底层 security(deny/allowlist/full) + ask(off/on-miss/always) + askFallback(默认 deny)），**auto 模式引入独立 reviewer 模型**；凭据 SecretRef + SQLite 状态库 + 日志 redaction 不可关；**注入防护**：`<<<EXTERNAL_UNTRUSTED_CONTENT>>>` 包裹、剥离伪造角色 token、只读 reader agent 摘要；exec host auto/gateway/sandbox/node + Docker/Podman 沙箱（workspaceAccess none/ro/rw，黑名单 /etc,/proc,.ssh,.aws） |
| **扩展与生态** | **ClawHub**（clawhub.ai）：社区 5000+ 技能、semver/tags/changelog/下载数、VirusTotal 扫描 + security audit + verified 徽章；Skill Workshop（agent 起草→人审→apply）；skill-creator 运行时自生技能、**递归式技能进化**（agent 写代码→封装 SKILL.md） |
| **局限短板** | Kaspersky 审计 512 漏洞(8 Critical)、第三方技能数据外泄/提示注入、Moltbook 泄露 150 万 API Key；诞生不足三月稳定性待考验；复杂任务依赖模型质量；技能质量参差需人工审计 |

**对 PA 的可借鉴点（按价值排序）**：
1. **exec 审批矩阵（security×ask×askFallback）+ auto 独立 reviewer 模型** → 直接对标 PA 的 PermissionManager 三级分级，可升级为可组合矩阵（详见 §4.2.1）。
2. **Skill 权限声明 + 安装时明示 + 事后降权** → PA Skill 已有工具白名单（§7.7），缺权限声明/降权链路（详见 §4.3.2）。
3. **递归式技能进化（agent 写代码→封装 SKILL.md）** → 与"Agent 自进化"方向直接相关（详见 §4.4.2）。
4. **Lane Queue 串行防竞态 + Steer 注入** → PA WS 有 per-session 锁，缺多车道调度抽象（远期可选）。
5. **ClawHub 生态运营（semver/VirusTotal/verified 徽章）** → 远期可借鉴建立 PA 工具市场（蓝图 §5.18 Tool Marketplace 已有雏形）。

---

### 1.3 Pi Agent（pi.dev）— 极简内核哲学的极致样本

**定位**：libGDX 作者 Mario Zechner (badlogic) 2025-08 创建的极简终端编码 Agent 框架（开源、MIT、TypeScript）。OpenClaw 的 Agent 运行时即基于 Pi 构建。是"**最小内核与自由组合**"方向的教科书案例。

| 维度 | 关键事实 |
|---|---|
| **架构设计** | TypeScript monorepo 4 包：`pi-ai`（统一多 Provider LLM API，15+ 提供商含 Ollama/自托管）、`pi-agent-core`（agent 运行时）、`pi-coding-agent`（CLI）、`pi-tui`（差分渲染终端 UI）；自研全栈不依赖 Vercel AI SDK；**核心循环仅 300-1000 token 系统提示词 + 4 个内置工具（read/write/edit/bash）**；会话存储 SQLite + JSONL 树状分支历史；Bun 打包单文件二进制 |
| **核心能力** | 编码执行（文件+Shell）；**会话树分支与回放（/tree）**；上下文压缩（可自定义）；Skills（Markdown 能力包按需加载）；Prompt Templates；Hooks 生命周期系统；Extensions（TypeScript 热加载）；RPC/SDK 四模式（interactive/print/JSON/RPC/嵌入）；pi-chat（Slack 自动化）；终端内联图片渲染 |
| **交互模式** | 仅终端 TUI 无 GUI；**插入消息（Enter 打断剩余工具）/ 排队消息（Alt+Enter 等待完成）**；`/session` 成本/token 视图；AGENTS.md/SYSTEM.md 项目级个性化指令；刻意不做 sub-agent/plan mode/to-do（全部以文件+扩展替代） |
| **权限与安全** | **无内置权限系统，默认 YOLO 模式**（创始人明确反对"权限弹窗安全剧场"，主张真实边界=容器/沙箱：Gondolin 微虚拟机/Docker/OpenShell）；提供 permission-gate、protected-paths 示例扩展自行实现人在环中；供应链安全扎实（锁定版本、--ignore-scripts、npm audit） |
| **扩展与生态** | Extensions/Skills/模板/主题打包为 Pi packages（npm/git 安装）；50+ 官方示例扩展、58+ 社区包、pi.dev/packages 仓库；RPC+SDK 可嵌入（OpenClaw 即真实案例）；会话可分享 HuggingFace 用于评测 |
| **局限短板** | 无 MCP（需 mcporter 桥接）、无 GUI、学习曲线陡峭；YOLO 模式对不熟悉者风险高；"极简/自我扩展"被批差异化不足；纯终端不适合非技术用户 |

**对 PA 的可借鉴点（按价值排序）**：
1. **会话树（失败分支保留/回放/摘要回主线）** → PA checkpoint 已存每轮快照，缺"分支管理 + 回放"视图；这是复盘与审计的天然载体（详见 §5.1 的 P2-1）。
2. **Hooks 作为人在环中接口（tool_call 可拦截/阻断、tool_result 可修改，select/confirm/input/notify UI 原语）** → 与 Claude Code Hooks 同构，验证了"事件化权限"是行业共识（详见 §4.2.2）。
3. **极简内核 + 按需能力（300-1000 token 提示词 + 4 工具 + Skills 按需加载）** → PA ToolSelector 已做 top-N 挑选，但内核未"极简到工具可插拔"级别；启示：PA 的 9 个内置工具应评估哪些该下沉为"可选 Skill 化"（详见 §4.3.1）。
4. **可预测性=信任（无隐藏 sub-agent，所有行为主会话可见）** → 与"人在环中-可解释决策"方向直接相关（详见 §4.2.3）。
5. **工具结果分离（给 LLM 文本 + 给 UI 结构化数据）** → PA 事件流已是结构化（event_type 分块），可补"结果双通道"标准化。

---

### 1.4 Hermes Agent（Nous Research）— 模型层自进化理念

> 证据声明：Hermes 非完整 agent 产品，而是**开源模型系列**，官方资料以模型卡/技术博客为主（★★），本节约束在公开可验证事实内，未涉及无法核实的细节。调研代理未能返回完整报告，本节由汇总阶段基于公开资料补齐。

**定位**：Nous Research 的 Hermes 系列开源模型（Hermes 1/2/3/4），其核心价值在**模型层的 agentic 能力**（function calling / tool use / reasoning），是"Agent 能力下限由模型决定"这一判断的典型案例。Hermes 2 Pro（2024）是最早原生 function calling 的开源模型之一，在 ToolBench 类基准上引领了开源模型的工具调用能力；Hermes 3 强化 tool calling + JSON 模式 + 多角色提示；Hermes 4（2025）转向**深度推理 + 推理蒸馏**（distillation of frontier reasoning）。

| 维度 | 关键事实 |
|---|---|
| **架构设计** | 模型层（非应用层）：Hermes 4 Reasoning 系通过从前沿推理模型蒸馏合成推理轨迹（synthetic reasoning traces）训练，采用 post-training 管线（SFT + 数据过滤 + 对齐），无内置 agent 运行时——需宿主框架（OpenHands/Claude Code 等）承载 |
| **核心能力** | 原生 function calling（Hermes 2 Pro 起）、JSON 模式、深度推理（Hermes 4 "think before answer"）；在开源模型中对齐/工具调用/推理的平衡是差异化点 |
| **交互模式** | 以 API/模型权重形式被消费，无自有 UI；被各类框架作为底座模型 |
| **权限与安全** | 模型层安全训练（拒绝有害指令/工具滥用倾向），不提供应用层权限机制——权限必须由宿主框架（如 PA 的 PermissionManager）承担 |
| **扩展与生态** | 开源权重生态（HF 下载量高），被大量 agent 项目选为底座；与框架的适配靠框架的 tool schema 对齐 |
| **局限短板** | 非完整产品（无 UI/无权限/无记忆）；模型层能力受限于训练数据与蒸馏教师质量；自进化依赖再训练/微调（成本高），与系统层自进化（记忆/技能）不在同一操作粒度 |

**对 PA 的可借鉴点（按价值排序）**：
1. **模型层 vs 系统层自进化的分层认知** → PA 的"自进化"是系统层（记忆/技能/评估闭环），与 Hermes 的模型层（蒸馏/再训练）互不冲突且天然互补：PA 通过 `models/registry.py` 动态切换底座模型即可获得"模型层进化"红利，无需自训练——**这是 PA 作为应用层 agent 的战略优势**（详见 §4.4 的 B-5/B-10 落地，均不依赖模型层改造）。
2. **推理蒸馏的方法学启示（synthetic data + 反馈循环）** → PA 评估闭环的低分样本（`eval/weak_sample.py`）本质是"数据反馈循环"的应用层版本：弱样本 → 审核 → 修正（skill/prompt）→ 再评估，与蒸馏管线同构但成本低一个量级（人日 vs 算力）。**验证了 PA 已落地的评估闭环是正确方向**。
3. **"模型即内核，能力靠框架组合"的验证** → Hermes 证明模型层提供基础能力、应用层提供组合能力（工具/权限/记忆/上下文）——对应 PA 的"开放式 LLM 接入（不预置 provider）+ 双轨工具层"架构判断，与 OpenClaw/Pi 的极简内核哲学形成闭环印证。

---

## 二、横向对比矩阵

### 2.1 六维度对比总表

| 维度 | Claude Code | OpenClaw | Pi Agent | Hermes Agent | **PA（本项目）** |
|---|---|---|---|---|---|
| **定位** | 终端编码 Agent（商业） | 个人 Agent 平台（开源） | 极简编码 Agent 框架（开源） | 模型层 agentic 能力（开源） | 桌面生产力 Agent（自研） |
| **架构** | 单引擎多端，queryLoop 异步生成器 | Gateway 控制平面 + Pi 极简运行时双层 | 4 包 monorepo，极简内核 | 模型/推理蒸馏为主 | Sidecar + ReactLoop 五态状态机 |
| **系统提示词** | 完整系统提示 + CLAUDE.md + memory | 业界最短（Pi 系） | 300-1000 token 极简 | —（模型层） | system_prompt + Frozen Zone hash 固化 |
| **内置工具** | ~54 个 + MCP 动态装配 | 49-100+ 技能 | **仅 4 个**（rw/edit/bash） | 取决于宿主框架 | 9 类内置 + MCP + ToolSelector top-N |
| **上下文管理** | auto-compact（65-70%）+ /rewind + 1M 变体 | compaction 前静默 flush 关键信息 | 可自定义压缩 | — | 滑动窗口+摘要+Stable Zone 合并，熔断器 |
| **记忆** | CLAUDE.md 四级 + auto memory（纠正沉淀） | 三层：转录 JSONL + Markdown + 混合检索 | 会话树 + 文件 | 无独立记忆层 | LLM 摘要提取 + 淘汰 + MemPalace 语义检索 |
| **权限模型** | 7 模式 + allow/ask/deny 规则 + Hooks 29 事件 | security×ask×askFallback 矩阵 + reviewer 模型 | **默认 YOLO（无权限系统）** | — | 三级 safety_level + WS 确认 60s 超时 + 会话缓存 |
| **沙箱** | Seatbelt/bubblewrap OS 级 + 网络域白名单 | Docker/Podman + workspaceAccess + 黑名单 | 无内置（Gondolin/Docker 外部） | — | 子进程隔离 + Job Object + 禁网 + 资源限制 |
| **扩展机制** | Skills/Plugins/MCP/SDK | Skills/Plugins/Hooks/ClawHub 市场 | Extensions/Skills/packages | 模型蒸馏/适配器 | Skill（PG+文件双源）+ MCP + assemble 装配 |
| **子代理** | ✅（独立上下文仅回传摘要） | ✅（跨会话 sessions_spawn） | ❌（刻意不做） | — | ❌（无，评估闭环有 Mock 回放） |
| **人在环中** | Esc/Ctrl+C 打断、AskUserQuestion、权限模式切换 | 审批卡片（allow-once/always/deny）、/approve、DM 配对 | 插入/排队消息、Hooks 拦截 | — | 打断/停止 + WS 确认 + checkpoint 断点续传 |
| **自进化** | auto memory + /init + skill 生成 + managed policies | skill-creator + 递归式技能进化 | 自我扩展（写代码→封装） | 推理蒸馏/自反思方法学 | 评估闭环（weak_sample→审核→入库→再评估）✅ 已落地 |
| **注入防护** | fail-closed + WebFetch 独立上下文 + 命令检测 | `<<<EXTERNAL_UNTRUSTED_CONTENT>>>` 包裹 + reader agent | 无内置 | — | InjectionGuard（告警/移除分级） |
| **生态** | MCP 目录 + plugins 市场 + IDE 插件 | ClawHub 5000+ 技能 | 58+ 社区包 | 开源模型生态 | 无市场（蓝图 §5.18 预留） |
| **主要短板** | 模型锁定 + token 成本高 | 安全审计问题 + 诞生不足三月 | YOLO 风险 + 无 MCP/GUI | 模型层非完整产品 | 无子代理 + Skill 白名单未接线 + 无市场 |

### 2.2 与 PA 的差距优先级（借鉴价值排序）

| 差距点 | 对标对象 | 差距等级 | 借鉴价值 |
|---|---|---|---|
| Hooks 生命周期事件化（权限/上下文注入点） | Claude Code 29 事件 / Pi / OpenClaw | 🔴 大 | ★★★★★ |
| 权限规则化（allow/ask/deny + deny 优先 + 模式切换） | Claude Code 7 模式 / OpenClaw 矩阵 | 🔴 大 | ★★★★★ |
| 经验沉淀通道（纠正→memory） | Claude Code auto memory / OpenClaw 记忆 | 🟠 中 | ★★★★ |
| 会话树分支/回放（复盘与审计载体） | Pi 会话树 | 🟠 中 | ★★★ |
| Skill 权限声明 + 安装明示 + 降权 | OpenClaw SKILL.md metadata | 🟠 中 | ★★★★ |
| 子代理（独立上下文+摘要回传） | Claude Code / OpenClaw | 🟠 中 | ★★★ |
| 命令风险分级可视化（Low/Med/High） | Claude Code Ctrl+E | 🟡 小 | ★★★ |
| 递归式技能进化（代码→SKILL.md） | OpenClaw / Pi | 🟠 中 | ★★★★ |
| 工具结果双通道（LLM 文本 + UI 结构化） | Pi | 🟡 小 | ★★ |
| 多通道抽象（sessionKey 归一化） | OpenClaw | 🟡 小（远期） | ★★ |

---

## 三、可借鉴点清单（含可行性评估）

> 技术可行性：**P0** = 纯增量、低风险、现有架构可直接承载；**P1** = 需小范围重构或新增模块、中等风险；**P2** = 需架构扩展或平台验证、高风险。
> 实现成本以"人日"为单位（单人开发，含测试与真机验证），沿用项目单人开发惯例（阶段二 4 批次约 3-4 人日规模）。

| # | 可借鉴点 | 来源 | 映射到 PA 模块 | 可行性 | 成本 |
|---|---|---|---|---|---|
| B-1 | **Hooks 生命周期事件化**：定义 PreToolUse/PostToolUse/UserPromptSubmit/Stop 类事件，支持 command/http/mcp_tool 三类 hook，输出可回写（permissionDecision/updatedInput/additionalContext） | Claude Code 29 事件 + Pi + OpenClaw | `core/react_loop.py`（事件产出点）+ 新增 `core/hooks.py` + `api/admin.py`（hooks 配置） | P0 | 2-3 人日 |
| B-2 | **权限规则化**：allow/ask/deny 规则表（`Tool(specifier)` 语法），deny 优先，替代/增强静态 safety_level | Claude Code 7 模式 | `tools/permission_manager.py`（新增规则求值层，safety_level 保留为默认规则） | P0 | 1-2 人日 |
| B-3 | **权限模式切换**：plan（只读）/default/acceptEdits 类会话级模式，Shift+Tab 式前端切换 | Claude Code | `tools/permission_manager.py` + `api/admin.py` + `App.tsx`（模式切换 UI） | P0 | 1 人日 |
| B-4 | **exec 审批矩阵**：security(deny/allowlist/full) × ask(off/on-miss/always) × askFallback(deny)，预置 cautious/deny-all/yolo 配置档 | OpenClaw | `tools/permission_manager.py`（扩展为矩阵求值）+ `config.yaml tools.permission` | P0 | 1-2 人日 |
| B-5 | **auto memory（用户纠正→经验沉淀）**：用户纠正/拒绝时触发记忆提取，写入用户级记忆库，下轮注入 | Claude Code | `memory/manager.py`（新增 correction 触发通道）+ `memory/memories_repo.py`（新记忆类型）+ MemPalace 同步 | P1 | 1-2 人日 |
| B-6 | **Skill 权限声明 + 安装明示 + 事后降权**：skill.yaml 增加 permissions 声明，安装时展示，允许会话级降权 | OpenClaw SKILL.md metadata | `skills/models.py`（SkillManifest 扩展）+ `skills/manager.py` + `SkillSelectionPanel.tsx` | P0 | 1 人日 |
| B-7 | **会话树分支/回放**：checkpoint 之上增加分支记录，失败分支可回放/摘要回主线 | Pi 会话树 | `core/checkpoint.py`（扩展分支元数据）+ `storage/react_events.py`（分支事件）+ `HomeView.tsx`（回放视图） | P1 | 2-3 人日 |
| B-8 | **命令风险分级可视化**：工具调用前显示 Low/Med/High 风险提示 | Claude Code Ctrl+E | `App.tsx`（确认卡片增强）+ `tools/defs.py`（ToolDef.risk_level） | P0 | 0.5 人日 |
| B-9 | **子代理机制**：独立上下文 + 摘要回传，嵌套深度限制 | Claude Code Subagents / OpenClaw sessions_spawn | 新增 `core/subagent.py` + `tools/builtins/agent.py`（新工具）+ async_tasks 表扩展 | P1 | 3-4 人日 |
| B-10 | **递归式技能进化**：评估/复盘低分样本 → agent 起草 skill 改进 → 人工审核 → 入库新版本 | OpenClaw skill-creator | `eval/weak_sample.py`（扩展）→ `skills/loader.py`（版本管理已有）→ 新 `skills/generator.py` | P1 | 2-3 人日 |
| B-11 | **工具结果双通道**：结果分"给 LLM 的文本摘要 + 给 UI 的结构化数据" | Pi | `tools/defs.py`（ToolResult 扩展 ui_data 字段）+ `App.tsx`（结构化渲染） | P0 | 0.5-1 人日 |
| B-12 | **注入防护强化**：外部不可信内容 `<EXTERNAL_UNTRUSTED_CONTENT>` 包裹 + 只读 reader 摘要 | OpenClaw | `core/injection_guard.py`（扩展不可信内容标记）+ `tools/builtins/http_request.py`/`web_search.py`（结果包裹） | P0 | 1 人日 |
| B-13 | **reviewer 模型审批**：auto 模式由独立模型评审工具调用（不见工具结果防注入） | OpenClaw | `tools/permission_manager.py`（auto 分支）+ `models/registry.py`（复用 judge_model 机制） | P1 | 1-2 人日 |
| B-14 | **中断恢复增强**：当前 60s 超时拒绝 → 支持"审批挂起 + 恢复续跑" | Claude Code 打断/恢复 | `core/checkpoint.py` + `main.py /ws`（新消息类型 approval_response_later）+ ReactLoop 恢复 | P1 | 2 人日 |

---

## 四、与现有架构强耦合的方案设计

> 设计约束（来自项目长期约定）：① 不引入新范式，全部落在现有模块边界内；② 配置驱动 + 默认安全 + 可回退；③ 保持 Frozen Zone/KV Cache 友好（新增注入不得破坏前缀稳定性）；④ 沿用 pytest-asyncio / 单测双轨测试惯例；⑤ 与阶段二安全硬边界（admin 鉴权/SSRF/沙箱）兼容。

### 4.1 总体映射图（改进点 → 现有模块）

```
前端 App.tsx（WS 事件流 / 确认卡片 / 打断）
    │
    ▼
main.py /ws ──→ core/react_loop.py（五态状态机）
                    │  ├─ core/hooks.py ◄──【新增】B-1 生命周期钩子
                    │  ├─ tools/permission_manager.py ◄──【改造】B-2/B-3/B-4/B-13 规则化+矩阵+reviewer
                    │  ├─ tools/registry.py（工具装配点）
                    │  ├─ core/checkpoint.py ◄──【扩展】B-7 会话树 / B-14 审批挂起恢复
                    │  └─ core/injection_guard.py ◄──【扩展】B-12 不可信内容包裹
    │
    ├─ core/context_manager.py（三区 Zone 注入点）
    ├─ core/compressor.py（压缩触发点，Hooks 的 PreCompact/PostCompact 挂载点）
    ├─ memory/manager.py ◄──【扩展】B-5 纠正沉淀通道
    ├─ skills/（models/manager/loader）◄──【扩展】B-6 权限声明 / B-10 递归进化
    ├─ eval/（weak_sample/judge/runner）◄──【扩展】B-10 技能进化闭环
    └─ models/registry.py ◄──【复用】judge_model 机制 → reviewer 模型（B-13）
```

### 4.2 设计一：人在环中的可控协作（对应重点方向 1，P0）

**设计原则**：借鉴 Claude Code"harness 强制执行权限 + Hooks 事件化 + fail-closed"与 OpenClaw"审批矩阵 + 独立 reviewer"的组合——**权限决策从"硬编码分支"升级为"规则求值 + 事件化注入"**，同时保持 PA 现有的"WS 确认 + 60s 超时 + 会话级缓存"安全默认。

**4.2.1 权限规则化求值层（B-2/B-3/B-4）**

改造 `tools/permission_manager.py`：保留现有 `safety_level`（作为默认规则来源），新增规则求值引擎：

```python
# tools/permission.py 扩展（规则 DSL，兼容现有缓存 key 构造）
class PermissionRule:
    pattern: str      # "Tool(specifier)"，如 "code_execution" / "file_write(//sandbox/**)"
    action: str       # allow | ask | deny
    source: str       # config | skill | session  （优先级：session > skill > config）
# 求值顺序：deny 优先于一切 allow（Claude Code 语义）；未匹配 → 回退 safety_level
```

- **会话级权限模式**：`config.tools.permission.mode ∈ {default, plan, acceptEdits, cautious, deny_all}`（OpenClaw 预置档命名），会话创建时锁定（存入 sessions 表，复用 locked_skill_name 模式），前端设置页 + 会话内切换（仿 Shift+Tab）。
- **plan 模式**：只读工具放行、写工具全部 ask（复用现有 elevated 通道，无需新链路）。
- **矩阵表达**（B-4）：`security(deny/allowlist/full) × ask(off/on-miss/always) × askFallback(deny)` 作为底层组合，五个预置档是其命名封装——**避免引入新概念层**（符合项目"避免过度设计"约定）。
- **缓存键兼容**：现有 `get_permission_cache_key(skill_name, tool_name, args)` 保持，规则变化时通过 `source` 前缀参与 key（skill_name 已含，模式变化由会话级隔离天然覆盖）。

**4.2.2 Hooks 生命周期系统（B-1）**

新增 `core/hooks.py`，在 ReactLoop 事件产出点挂载，**不改变现有事件流**：

```python
# core/hooks.py（新增，事件点与 react_events 并行，不干扰现有 event_sink）
HOOK_EVENTS = {
    "user_prompt_submit",   # 用户消息 → 可拒/改
    "pre_tool_use",         # 工具执行前 → permissionDecision(allow/deny/ask/defer)
    "post_tool_use",        # 工具执行后 → 可注入 additionalContext / 强制 lint
    "stop",                 # 收尾前 → 可阻止过早收尾
    "pre_compact",          # 压缩前 → 关键信息 flush（OpenClaw compaction flush 借鉴）
    "permission_request",   # 确认请求 → 外部接管（如企业策略 hook）
}
```

- **Hook 类型**：`command`（子进程调用，复用 sandbox/executor 的子进程模式）、`http`（回调 URL，复用 security/ssrf.py 校验）、`mcp_tool`（复用 MCP client 调用）。
- **配置**：`config.yaml hooks: []` + admin 端点 CRUD（仿 MCP server 管理），**默认空列表 = 行为不变**，零回归风险。
- **决策回写**：hook 返回 JSON 支持 `permissionDecision` / `updatedInput` / `additionalContext`——其中 `additionalContext` 注入走 context_manager 的 append 接口（Active Zone 尾部，不破坏 Frozen Zone 前缀）。
- **实现要点**：hooks 执行失败默认放行（hook 是增强不是门禁），但 `permissionDecision=deny` 的结果是终局；超时 5s；日志审计进 react_events（新 event_type `hook_result`）。

**4.2.3 可解释决策（B-8 + 现有 thinking 事件）**

- `tools/defs.py` 的 ToolDef 增加 `risk_level ∈ {low, medium, high}`（按工具 + 参数启发式，如 file_write 目标路径含 `.env` → high），确认卡片渲染风险徽标（仿 Claude Code Ctrl+E 的 Low/Med/High）。
- 前端确认卡片增强：显示"该工具为何需要确认"（来自 ToolDef 描述 + 规则来源说明：`来自 Skill office 的白名单` / `来自系统默认 elevated`）——**决策可解释**。
- 计划确认节点（远期）：react_loop 在 max_iterations 触顶前，若检测到连续 3 轮 tool_call 无 final，推送"计划确认"事件给用户（人在环中的事中干预，非事后打断）。

**4.2.4 中断恢复增强（B-14）**

- 现状：elevated 确认 60s 超时即拒绝（fail-closed 安全默认，保留）。
- 增强：WS 增加 `approval_defer` 消息——用户可"稍后决定"，此时工具调用挂起（react_loop 状态机新增 `AWAITING_APPROVAL` 子状态，不新增顶层状态，沿用 ERROR/THINKING 枚举内扩展），checkpoint 照常写入；用户回来后在确认卡片恢复，复用现有断点续传链路（`checkpoint.py` 读取最新 checkpoint → 恢复 ctx → 从中断工具继续）。
- 与现有 `_handle_user_message` 的兼容：`approval_defer` 走 per-session 锁内的消息队列（main.py 已有 create_task + 锁），不阻塞主循环。

**4.2.5 注入防护强化（B-12）**

- `core/injection_guard.py` 扩展：对 `http_request` / `web_search` 返回内容中检测到的外部指令，包裹 `<<<EXTERNAL_UNTRUSTED_CONTENT>>>` 标记（OpenClaw 做法），并强化现有"移除+告警"分级——检测到角色劫持/清空指令时升级为"阻断工具结果回灌 + UI 告警"。

### 4.3 设计二：最小内核与自由组合（对应重点方向 2，P1）

**设计原则**：借鉴 Pi"极简内核 + 按需能力"与 OpenClaw"五向解耦 + Skill 权限声明"——**PA 内核维持现有 ReactLoop + 9 内置工具不动，但建立"内核工具 → 可选 Skill 工具"的下沉通道与 Skill 权限声明体系**，避免过度设计（不引入插件市场，仅打通现有 Skill 机制）。

**4.3.1 内置工具下沉评估（B-6 前置）**

| 内置工具 | 现状 | 建议 | 理由 |
|---|---|---|---|
| calculator / datetime / web_search | 通用 | 保留内核（低 token 成本） | 高频基础能力 |
| file_read / file_write / http_request | 通用但高风险 | 保留内核，按 Skill 白名单控制 | 现有白名单已覆盖 |
| code_execution | 通用但最高风险 | 保留内核（elevated + 沙箱） | 已有多层防护 |
| search_knowledge / read_artifact | 场景相关 | **下沉为 Skill 可选工具**（office/data_analysis 声明依赖时启用） | 减少非场景会话的 schema 注入 |

- 实现：`skills/models.py` 的 `SkillManifest.tool_dependencies` 已有声明（CONTEXT.md 中 ToolDependency 定义），`tools/registry.py` 的 `list_tools_for_session(whitelist)` 已实现过滤——**下沉只需把内置工具的注册改为"默认注册 + Skill 白名单外隐藏"**，即注册表加 `is_kernel` 标记，`list_tools_for_session` 对非内核工具执行白名单强过滤。零新表、零新概念。

**4.3.2 Skill 权限声明 + 安装明示 + 降权（B-6）**

- `skills/models.py` 的 `SkillManifest` 扩展 `permissions` 字段（对齐 OpenClaw `metadata.openclaw`）：
  ```yaml
  permissions:
    - tool: file_write
      paths: ["workspace://sandbox/**"]   # 限定路径模式
    - tool: http_request
      domains: ["api.example.com"]        # 限定域名
    - override: deny                       # 会话级降权入口
  ```
- 加载时校验：`skills/manager.py` 激活时把 permissions 合入权限规则层（§4.2.1，source=skill 优先级层），安装（设置页上传）时在 `SkillSelectionPanel.tsx` 展示"Required Permissions"清单，用户可逐项降权（deny）。
- 与现有 `safety_level_override`（ToolDependency）兼容：override 为强约束，permissions 为白名单细化，两者求交集。

**4.3.3 内核轻量化测量（远期）**

- 复用 `core/token_estimator.py`：在设置页展示"内核提示词 token 占比"（frozen zone 大小 vs 总预算），工具下沉后该指标下降——用数据驱动内核裁剪决策（Pi 的 300-1000 token 是方向性参照，PA 桌面场景不追求极端，但可量化跟踪）。

### 4.4 设计三：Agent 自进化（对应重点方向 3，P1）

**设计原则**：PA 的 M4 评估闭环（`eval/` 包：runner/judge/weak_sample/version_compare/rollback）已提前落地——**自进化的主链路已存在，本设计是补齐三个"进化入口"**：① 用户纠正沉淀（B-5，借鉴 Claude Code auto memory）；② 技能递归进化（B-10，借鉴 OpenClaw skill-creator）；③ 进化评估看板（复用现有 eval 指标）。

**4.4.1 用户纠正 → 记忆沉淀（B-5）**

- `memory/manager.py` 新增 `maybe_extract_from_correction(user_id, correction)`：在用户对生成结果明确纠正/拒绝时（前端检测到"用户编辑消息后重发"或确认卡片 deny 后用户留言），触发一次定向提取（复用 `_extract_memories` 的压缩模型通道，即 P0 修复后的 compress_adapter）。
- 新记忆类型 `correction`（复用现有四类 type 枚举扩展为五类，`memories_repo.py` 加 type CHECK 约束迁移——沿用 V2 幂等迁移模式），importance 默认 high（用户纠正是高价值信号）。
- 注入：走现有 `load_user_memories → format_memories_for_stable`（Stable Zone 注入，不破坏 Frozen 前缀）；同时写入 MemPalace（mempalace_mine 工具），实现跨会话语义检索。
- **自进化闭环语义**：纠正 → 记忆 → 下次同类任务少犯错（可解释的进化，非黑盒）。

**4.4.2 技能递归进化（B-10）**

- 新增 `skills/generator.py`（对齐 OpenClaw skill-creator / Pi 自我扩展）：
  1. **触发**：`eval/weak_sample.py` 提取的低分样本（`review_queue`）中，人工审核标记为 `prompt_defect_edit` 的样本（现有 ReviewQueueRepo 已支持该决策类型）。
  2. **起草**：复用 `models/registry.py` 构建主模型 adapter，prompt 为"基于失败轨迹 + 现有 skill.yaml + 期望输出，起草改进版 skill"——输出建议 diff（system_prompt 片段 / 少样本示例 / 工具白名单调整）。
  3. **人工审核**：写入 `review_queue` 新状态 `awaiting_skill_apply`（复用现有表，不新建），前端 SkillsView 展示 diff，用户确认后经 `skills/loader.py` 版本化入库（版本管理已有：PG 存运行时副本 + 文件回退 + locked 版本）。
  4. **再评估**：新版本入库后，复用 `eval/runner.py` 对低分样本重跑（同模型），`version_compare.py` 对比新旧版本指标——**闭环完成**。
- 迭代上限保护：单次进化为 1 个技能 1 个版本，失败（再评估无提升）自动回滚（`eval/rollback.py` 已有），不产生版本爆炸。

**4.4.3 进化评估看板（复用现有 eval 可视化）**

- 蓝图 §8.14 评估看板已规划：增加"进化记录"页签，展示：纠正沉淀数量/影响、技能版本进化链（v1→v2 指标差）、低分样本流入流出——指标全部来自现有 eval_runs/metrics，仅新增前端聚合视图，后端零改动。

---

## 五、重点方向落地建议与优先级排序

### 5.1 优先级总排序

| 优先级 | 改进点 | 依据 | 建议批次 |
|---|---|---|---|
| **P0-1** | B-2/B-3/B-4 权限规则化 + 模式 + 矩阵（§4.2.1） | 人在环中第一优先级；改动集中于 permission_manager，回归面小 | 阶段三批次 1 |
| **P0-2** | B-1 Hooks 生命周期（§4.2.2） | 一劳永逸的事件化注入点，后续所有机制的挂载基座 | 阶段三批次 2 |
| **P0-3** | B-8 风险分级可视化 + B-12 注入强化（§4.2.3/4.2.5） | 小改动高感知，立即可交付 | 阶段三批次 1（随 P0-1） |
| **P1-1** | B-6 Skill 权限声明 + 下沉通道（§4.3） | 最小内核落地；依赖 P0-1 的规则层 | 阶段三批次 3 |
| **P1-2** | B-5 用户纠正沉淀（§4.4.1） | 自进化第一入口；依赖 compress_adapter 修复（M3 P0-1） | 阶段三批次 3 |
| **P1-3** | B-14 审批挂起恢复（§4.2.4） | 中断恢复增强；依赖 checkpoint 现有链路 | 阶段三批次 4 |
| **P1-4** | B-10 技能递归进化（§4.4.2） | 自进化闭环；依赖 B-5 与评估基线 | 阶段四 |
| **P2-1** | B-7 会话树回放（§4.1 远期） | 复盘审计载体；工程量最大 | 阶段四/五 |
| **P2-2** | B-9 子代理（§4.1 远期） | 长任务拆分；新工具面 | 阶段四/五 |
| **P2-3** | B-13 reviewer 模型审批 | auto 模式增强；成本敏感（需独立模型调用） | 阶段五（可选） |

### 5.2 阶段三建议批次计划（承接阶段二收尾）

```
阶段三批次 0（准备）      T0  基线回归 + permission 规则 DSL 单测骨架
阶段三批次 1（P0 人环）   B-2 规则求值层 → B-3 模式切换 → B-4 矩阵 → B-8 风险分级 → B-12 注入强化
                         （回归：pytest 933+新增；双模式真机验证）
阶段三批次 2（P0 Hooks）  B-1 core/hooks.py + admin CRUD + config + 3 类 hook 实现
                         （回归：hooks 默认空列表零回归验证）
阶段三批次 3（P1 内核）   B-6 Skill 权限声明 + 下沉通道 + B-5 纠正沉淀
                         （依赖：M3 P0-1 compress_adapter 修复先行）
阶段三批次 4（P1 恢复）   B-14 审批挂起恢复
阶段四                    B-10 技能递归进化 + B-7 会话树（可选先行）
```

### 5.3 风险与规避

| 风险 | 等级 | 规避 |
|---|---|---|
| 权限规则化改变既有确认行为 | 中 | 默认规则 = 现有 safety_level 语义（100% 兼容）；规则表为空时行为不变；真机回归 |
| Hooks 引入执行开销/注入面 | 中 | 默认空列表；hook 失败放行 + 5s 超时；http hook 走 SSRF 校验；审计进 react_events |
| 内置工具下沉破坏既有会话 | 低 | 非内核工具在无 Skill 会话仍可用（白名单 None = 全量，保持 M1 行为）；仅 Skill 会话强过滤 |
| 纠正沉淀误伤（把普通拒绝当纠正） | 中 | 仅"编辑后重发 + 明确否定词"触发；importance 可配置；前端展示沉淀记录可删除 |
| 技能递归进化产生低质量版本 | 中 | 人工审核闸门（review_queue）+ 再评估回滚（version_compare/rollback 已有） |

---

## 六、结论

四智能体调研对 PA 的借鉴价值排序：**Claude Code（人在环中 + Hooks，★★★★★）> OpenClaw（权限矩阵 + 技能进化，★★★★★）> Pi Agent（极简内核 + 会话树，★★★★）> Hermes Agent（模型层自进化理念，★★★）**。

PA 的差异化优势在于：评估闭环（M4 已提前落地）、三区上下文工程（Frozen/Stable/Active + KV Cache 友好）、安全硬边界（阶段二完成）——这些恰是四个对标对象普遍欠缺或未强化的。本报告 14 项可借鉴点全部落在现有模块边界内（permission_manager / react_loop / memory / skills / eval / checkpoint），按 §5.1 优先级可在阶段三-五逐步落地，总成本约 **16-24 人日**，每批次独立可验收、可回退。

*本报告由 WorkBuddy 生成，随阶段三实施跟踪更新。*

---
