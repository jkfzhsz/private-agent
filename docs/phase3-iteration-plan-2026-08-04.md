# 阶段三迭代计划：人在环中的可控协作与最小内核落地

> 项目：私人智能体（Private Agent）· 后端 `backend/` + 前端 `frontend/`
> 日期：2026-08-04 · 编制依据：`docs/round2-benchmark-research-2026-08-04.md`（第二轮借鉴调研）+ `docs/phase-closeout-phase2-2026-08-04.md`（阶段二收尾）+ `private-agent-blueprint.md`（蓝图）
> 状态：🟢 **阶段三全部批次完成（1073 passed + 13 vitest + tsc 0 error）**；收尾报告 `docs/phase-closeout-phase3-2026-08-04.md`
> 文档版本：v1.1（2026-08-04 实施完成更新）

---

## 一、项目概述

### 1.1 阶段目标

阶段三以"**人在环中的可控协作（P0）+ 最小内核与自由组合（P1）**"为主题，将第二轮借鉴调研（对象：Claude Code / OpenClaw / Pi Agent / Hermes Agent）产出的 14 项可借鉴点中**技术可行、与现有架构强耦合的 9 项**落地为可交付能力，同时补齐一项阻塞性前置修复（`compress_adapter` 缺失）。

具体目标：

| 目标编号 | 目标描述 | 对应可借鉴点 |
|---|---|---|
| G-1 | **权限决策规则化**：从静态三级 safety_level 升级为 allow/ask/deny 规则求值 + 会话级权限模式 + 审批矩阵，保持现有"WS 确认 + 60s 超时 + 会话级缓存"安全默认 | B-2 / B-3 / B-4 |
| G-2 | **Hooks 生命周期系统**：建立 PreToolUse / PostToolUse / UserPromptSubmit / Stop / PreCompact / PermissionRequest 六类事件钩子，支持 command / http / mcp_tool 三类实现，决策可回写 | B-1 |
| G-3 | **可解释决策**：工具风险分级（Low/Med/High）可视化 + 确认卡片展示"为何需要确认" | B-8 |
| G-4 | **注入防护强化**：外部不可信内容包裹标记 + 高危注入升级阻断 | B-12 |
| G-5 | **Skill 权限声明与内核下沉**：skill.yaml 增加 permissions 声明、安装明示、会话级降权；场景相关内置工具下沉为 Skill 可选（`is_kernel` 标记） | B-6 |
| G-6 | **用户纠正沉淀**：纠正 → 记忆（新 `correction` 类型）→ 下轮注入，打通自进化第一入口 | B-5 |
| G-7 | **审批挂起与恢复**：elevated 确认从"60s 超时即拒"增强为"可挂起稍后决定 + checkpoint 恢复续跑" | B-14 |

### 1.2 阶段背景（阶段一、二成果）

| 阶段 | 成果 | 本阶段依赖 |
|---|---|---|
| **阶段一**（2026-08-04 上午） | 对话流畅度三方向优化（ToolSelector / context_window / 状态栏）+ 架构修订三批次（压缩 Zone 隔离、事务化写入、路径强制、超时分级、重放过滤、MCP 加固） | 规则求值层复用 `tools/selector.py` 的确定性评分模式；事务化写入保证工具配对稳定 |
| **阶段二**（2026-08-04，**973 passed + 13 vitest**） | 安全硬边界四批次：admin 鉴权 + CORS 收窄（43 端点 401）、SSRF 防护（28 用例）、沙箱失效修复（Job Object + 禁网 + 资源限制）、C-4 事件级去重 | Hooks 的 http 类型复用 `security/ssrf.py` 校验；权限规则层与 admin 鉴权正交不冲突；沙箱 executor 子进程模式复用为 command hook 载体 |

### 1.3 本阶段在整体项目中的定位

阶段三位于 **M3（Skill 场景能力）与 M4（评估闭环）之间的衔接期**：

```
M1 (基础对话) → M2 (记忆/工具/沙箱) → [阶段二 安全硬边界] → 阶段三 (人在环中+内核) → M3 (Skill 深化) → M4 (评估闭环)
                                                                   ↑
                                                   本阶段为 M3/M4 提供
                                                   "可插拔权限钩子 + 进化入口"
```

阶段三产出的 **Hooks 系统（G-2）与权限规则层（G-1）是后续一切机制（企业策略、自动审批、技能进化、沙箱策略）的挂载基座**；**纠正沉淀（G-6）与 Skill 下沉（G-5）是阶段四"技能递归进化（B-10）"的前置条件**。本阶段不实现 B-7（会话树）、B-9（子代理）、B-10（技能递归进化）、B-13（reviewer 模型）——它们依赖本阶段基座，列入阶段四/五。

---

## 二、范围定义

### 2.1 范围内工作（In-Scope）

| 编号 | 工作项 | 涉及模块 | 蓝图章节 |
|---|---|---|---|
| S-1 | 前置修复：`compress_adapter` 绑定（记忆提取依赖的 LLM 压缩模型适配器） | `memory/manager.py` + `main.py _build_compress_adapter` | §4.2 / §3.11 |
| S-2 | 权限规则求值层（PermissionRule DSL + deny 优先 + source 优先级） | `tools/permission.py` + `tools/permission_manager.py` | §5.12 |
| S-3 | 会话级权限模式（default/plan/acceptEdits/cautious/deny_all） | `tools/permission_manager.py` + `api/admin.py` + `App.tsx` | §5.12 |
| S-4 | 审批矩阵组合（security × ask × askFallback） | `tools/permission_manager.py` + `config.yaml tools.permission` | §5.12 |
| S-5 | Hooks 生命周期系统（六事件 + 三类实现 + 决策回写 + admin CRUD） | 新增 `core/hooks.py` + `core/react_loop.py` + `api/admin.py` | §2.6 / §5.14 |
| S-6 | 工具风险分级（risk_level + 确认卡片可视化 + 来源解释） | `tools/defs.py` + `App.tsx` | §5.12 |
| S-7 | 注入防护强化（不可信内容包裹 + 高危阻断） | `core/injection_guard.py` + `tools/builtins/http_request.py` / `web_search.py` | §3.12 |
| S-8 | Skill 权限声明 + 安装明示 + 会话级降权 | `skills/models.py` + `skills/manager.py` + `SkillSelectionPanel.tsx` | §7.2 / §7.7 |
| S-9 | 内置工具下沉（`is_kernel` 标记 + 白名单强过滤） | `tools/registry.py` + `tools/builtins/` | §5.2 / §7.5 |
| S-10 | 用户纠正沉淀（correction 记忆类型 + 触发通道 + MemPalace 同步） | `memory/manager.py` + `memory/memories_repo.py` + `App.tsx` | §4.17 V2 |
| S-11 | 审批挂起与恢复（approval_defer + AWAITING_APPROVAL 子状态） | `core/checkpoint.py` + `core/react_loop.py` + `main.py /ws` + `App.tsx` | §2.14 / §5.12 |

### 2.2 交付物清单（Deliverables）

| 编号 | 交付物 | 形态 | 验收入口 |
|---|---|---|---|
| D-1 | 权限规则求值层 + 规则 DSL | 后端代码 + `tests/test_permission_rules.py` | S-2~S-4 验收 |
| D-2 | Hooks 系统（含 admin 管理端点 + 配置） | `core/hooks.py` + `tests/test_hooks.py` + 文档章节 | S-5 验收 |
| D-3 | 前端确认卡片增强（风险分级 + 来源解释 + 模式切换） | `App.tsx` + `tests/App.test.tsx` 扩展 | S-6 验收 |
| D-4 | 注入防护强化 | `injection_guard.py` 扩展 + `tests/test_injection_guard.py` 扩展 | S-7 验收 |
| D-5 | SkillManifest.permissions 扩展 + 前端权限展示 | `skills/models.py` + `SkillSelectionPanel.tsx` + 迁移 | S-8 验收 |
| D-6 | 内置工具下沉（is_kernel） | `tools/registry.py` + `config.yaml` | S-9 验收 |
| D-7 | correction 记忆类型 + 触发链路 | `memory/manager.py` + DB 迁移（幂等）+ `tests/test_memory_correction.py` | S-10 验收 |
| D-8 | 审批挂起/恢复链路 | `core/checkpoint.py` + WS 消息 + `tests/test_approval_defer.py` | S-11 验收 |
| D-9 | 安全模型文档更新 + 阶段三收尾报告 | `docs/security-model.md` + `docs/phase-closeout-phase3-2026-08-04.md` | MS-5 |

### 2.3 范围外（Out-of-Scope，明确不做）

| 排除项 | 原因 | 去向 |
|---|---|---|
| B-7 会话树分支/回放 | 工程量最大（2-3 人日），非本阶段主题 | 阶段四/五（P2-1） |
| B-9 子代理机制（独立上下文+摘要回传） | 新工具面，依赖 Hooks 基座稳定 | 阶段四/五（P2-2） |
| B-10 技能递归进化（skills/generator.py） | 依赖 S-10 纠正沉淀 + 评估基线 | 阶段四（P1-4） |
| B-13 reviewer 模型自动审批 | 成本敏感（需独立模型调用），非 P0 | 阶段五（P2-3，可选） |
| Tool Marketplace / ClawHub 式技能市场 | 远期生态建设 | 蓝图 §5.18 V2 |
| 多通道抽象（sessionKey 归一化） | PA 为桌面单通道产品，无多平台诉求 | 远期评估 |
| 1M 上下文模型适配 / auto-compact 阈值调优 | 属 M3 上下文工程深化 | M3 |

---

## 三、里程碑与时间安排

### 3.1 总览

| 里程碑 | 内容 | 起止日期 | 关键交付节点 | 验收标准（摘要） |
|---|---|---|---|---|
| **MS-0 准备** | 基线回归 + 规则 DSL 骨架 + 前置修复 S-1 | 08-05 | 基线快照；DSL 单测骨架；compress_adapter 可用 | pytest 973 全绿；记忆提取端到端可用 |
| **MS-1 权限规则化（P0）** | S-2/S-3/S-4/S-6/S-7 | 08-06 ~ 08-08 | 规则求值层；模式切换 UI；风险分级卡片 | 见 3.2 AC-1~AC-8 |
| **MS-2 Hooks 系统（P0）** | S-5 | 08-11 ~ 08-13 | hooks.py + admin CRUD + 三类实现 | 见 3.2 AC-9~AC-14 |
| **MS-3 最小内核（P1）** | S-8/S-9/S-10 | 08-14 ~ 08-18 | Skill 权限声明；is_kernel 下沉；correction 记忆 | 见 3.2 AC-15~AC-20 |
| **MS-4 中断恢复（P1）** | S-11 | 08-19 ~ 08-20 | approval_defer 链路 | 见 3.2 AC-21~AC-23 |
| **MS-5 阶段收尾** | D-9 + 全量回归 + PA1.0 同步 + GitHub | 08-21 | 收尾报告；双模式真机 | 全量 pytest + vitest + tsc 全绿 |

**计划周期**：2026-08-05 至 2026-08-21，共 **13 个工作日**（含周末休整 08-09/08-10、08-15/08-16）。单人开发 + AI 协作模式，总工作量约 **15-19 人日**。

### 3.2 分里程碑验收标准（AC）

**MS-1（权限规则化）**：

| # | 验收项 | 判定 |
|---|---|---|
| AC-1 | 规则表为空时权限行为与现有 safety_level 完全一致 | 既有权限测试全绿（零回归） |
| AC-2 | 规则 `deny` 优先于一切 `allow`（同工具冲突规则） | deny 生效 |
| AC-3 | 规则 `Tool(specifier)` 语法（如 `file_write(//sandbox/**)`）匹配生效 | 路径模式命中/未命中两向断言 |
| AC-4 | 会话级权限模式切换（default/plan/acceptEdits/cautious/deny_all） | plan 模式写工具全部 ask；deny_all 全部拒绝 |
| AC-5 | 审批矩阵组合（security×ask×askFallback）求值正确，askFallback 默认 deny | 表驱动用例全过 |
| AC-6 | 权限模式存入 sessions 表，重启后保持 | 会话恢复验证 |
| AC-7 | 工具风险分级（risk_level）渲染到确认卡片，含"为何需要确认"来源说明 | 前端 vitest + 真机截图 |
| AC-8 | 注入防护：`http_request` 返回中角色劫持/清空指令 → 阻断回灌 + UI 告警 | 构造注入样本用例 |

**MS-2（Hooks 系统）**：

| # | 验收项 | 判定 |
|---|---|---|
| AC-9 | hooks 默认空列表，行为与无 hooks 完全一致 | 全量回归零回归 |
| AC-10 | PreToolUse hook 返回 permissionDecision=deny → 工具被阻断（终局） | 单测 + 集成 |
| AC-11 | PreToolUse hook 返回 ask → 走现有 elevated 确认通道 | 确认卡片触发 |
| AC-12 | PostToolUse hook 返回 additionalContext → 注入 Active Zone 尾部（不破坏 Frozen） | 注入位置断言 |
| AC-13 | command / http / mcp_tool 三类 hook 均可配置执行；http hook 走 SSRF 校验 | 三类各 1 用例 |
| AC-14 | hook 失败（超时 5s/退出码异常）默认放行 + 审计记录（react_events 新 event_type） | 故障注入用例 |

**MS-3（最小内核 + 纠正沉淀）**：

| # | 验收项 | 判定 |
|---|---|---|
| AC-15 | skill.yaml 解析 permissions 字段（tool/paths/domains/override）失败即校验拒绝 | 解析用例 |
| AC-16 | Skill 激活时 permissions 合入规则层（source=skill 优先级），安装 UI 展示 Required Permissions | 集成用例 + 前端 |
| AC-17 | 会话级降权（override: deny）生效，且可恢复 | 降权/恢复两向 |
| AC-18 | `is_kernel` 标记后：无 Skill 会话（白名单 None）行为不变；Skill 会话中非白名单非内核工具被过滤 | 回归 + 过滤断言 |
| AC-19 | search_knowledge / read_artifact 下沉后，无 Skill 会话仍可用 | 回归 |
| AC-20 | 用户编辑后重发（含否定词）→ 生成 correction 记忆（importance=high）→ 下轮 Stable Zone 注入；MemPalace 同步成功 | 端到端（mock 压缩模型） |

**MS-4（审批挂起恢复）**：

| # | 验收项 | 判定 |
|---|---|---|
| AC-21 | 用户发 approval_defer → 工具调用挂起（AWAITING_APPROVAL 子状态）→ checkpoint 照常写入 | 状态机断言 |
| AC-22 | 用户稍后恢复 → 从 checkpoint 恢复 ctx → 从中断工具继续执行 | 端到端 |
| AC-23 | 未响应（仍 60s 超时）→ 默认拒绝（fail-closed 不变） | 回归 |

**MS-5（收尾）**：

| # | 验收项 | 判定 |
|---|---|---|
| AC-24 | 全量回归：后端 pytest（973+新增）全绿 + 前端 vitest 全绿 + tsc 0 error | 两条命令执行 |
| AC-25 | Electron + vite dev 双模式真机走查（权限模式切换/确认卡片/hooks 配置页/纠正沉淀触发） | 人工走查清单 |
| AC-26 | `docs/security-model.md` 更新（hooks 面/hook http 出网）+ 收尾报告归档 | 文档存在且与代码一致 |
| AC-27 | PA1.0（D:/PA1.0/backend）代码同步 + GitHub 推送 | diff 核对 + 远端 main 更新 |

---

## 四、任务分解（WBS）

### 4.1 依赖图

```
批次 0（准备）        T0.1 基线回归 ──► T0.2 规则 DSL 骨架 ──► T0.3 compress_adapter 修复(S-1)
                                   │
批次 1（权限规则化）◄───┼── 依赖 T0.2
  T1.1 规则求值层(S-2) → T1.2 模式切换(S-3) → T1.3 审批矩阵(S-4) → T1.4 风险分级(S-6) → T1.5 注入强化(S-7) → T1.6 测试+真机
                                   │
批次 2（Hooks）◄───────┼── 依赖 T1.1(规则层联动)
  T2.1 hooks.py 框架 → T2.2 admin CRUD+config → T2.3 三类实现 → T2.4 决策回写 → T2.5 测试+真机
                                   │
批次 3（最小内核）◄─────┼── 依赖 T0.3(compress_adapter) + T1.1(规则层)
  T3.1 permissions 声明(S-8) → T3.2 安装明示+降权(S-8) → T3.3 is_kernel 下沉(S-9) → T3.4 纠正沉淀(S-10) → T3.5 测试
                                   │
批次 4（中断恢复）◄─────┼── 依赖 T2.4(hooks 联动) + 现有 checkpoint
  T4.1 WS 消息+子状态(S-11) → T4.2 恢复链路(S-11) → T4.3 测试
                                   │
批次 5（收尾）◄─────────┼── 依赖全部
  T5.1 文档 → T5.2 全量回归 → T5.3 双模式真机 → T5.4 PA1.0 同步 + GitHub
```

### 4.2 任务清单

| 任务 | 内容 | 责任 | 依赖 | 工作量 |
|---|---|---|---|---|
| T0.1 | 基线回归（973+13+tsc），快照基线 | 开发 | 无 | 0.5 人日 |
| T0.2 | PermissionRule DSL 数据类 + 求值器纯函数 + 单测骨架 | 开发 | T0.1 | 0.5 人日 |
| T0.3 | **前置修复 S-1**：`_build_compress_adapter` 绑定 compress_model → MemoryManager 初始化注入 | 开发 | T0.1 | 0.5-1 人日 |
| T1.1 | 规则求值层接入 permission_manager（保留 safety_level 默认回退）+ 缓存键兼容 | 开发 | T0.2 | 1-1.5 人日 |
| T1.2 | 会话级权限模式：sessions 表存模式 + 切换端点 + 前端切换 UI | 开发 + 前端 | T1.1 | 1 人日 |
| T1.3 | 审批矩阵组合求值 + 5 预置档（config.yaml tools.permission） | 开发 | T1.1 | 0.5-1 人日 |
| T1.4 | ToolDef.risk_level + 确认卡片风险徽标 + 来源解释 | 开发 + 前端 | T1.1 | 0.5-1 人日 |
| T1.5 | 注入强化：不可信内容包裹 + 高危阻断回灌 | 开发 | 无 | 1 人日 |
| T1.6 | 测试（test_permission_rules.py 表驱动 + 集成）+ 真机验证 | 开发 | T1.1~T1.5 | 1 人日 |
| T2.1 | `core/hooks.py`：事件定义 + HookRunner（dispatch/超时/审计） | 开发 | T1.1 | 1-1.5 人日 |
| T2.2 | admin Hooks CRUD + config.yaml hooks 段 + loader 校验 | 开发 | T2.1 | 0.5 人日 |
| T2.3 | command（复用 sandbox executor）/ http（复用 SSRF）/ mcp_tool 三类实现 | 开发 | T2.1 | 1 人日 |
| T2.4 | 决策回写：permissionDecision/updatedInput/additionalContext 接入 ReactLoop | 开发 | T2.1 | 0.5-1 人日 |
| T2.5 | 测试（test_hooks.py 含三类 + 故障注入）+ 真机 | 开发 | T2.1~T2.4 | 1 人日 |
| T3.1 | SkillManifest.permissions 字段 + 解析校验 + DB 迁移（幂等） | 开发 | T1.1 | 1 人日 |
| T3.2 | 激活时合入规则层 + 安装 UI 展示 + 会话级降权 | 开发 + 前端 | T3.1 | 0.5-1 人日 |
| T3.3 | 注册表 is_kernel 标记 + list_tools_for_session 强过滤 + config | 开发 | 无 | 0.5 人日 |
| T3.4 | correction 记忆：触发通道（前端编辑重发+否定词检测）+ 提取 + 注入 + MemPalace 同步 | 开发 + 前端 | T0.3 | 1-1.5 人日 |
| T3.5 | 测试（test_skill_permissions / test_memory_correction）+ 真机 | 开发 | T3.1~T3.4 | 1 人日 |
| T4.1 | WS approval_defer 消息 + AWAITING_APPROVAL 子状态 + 前端"稍后决定"按钮 | 开发 + 前端 | T2.4 | 1 人日 |
| T4.2 | 恢复链路：checkpoint 恢复 → 从中断工具续跑 | 开发 | T4.1 | 0.5-1 人日 |
| T4.3 | 测试（test_approval_defer.py 端到端 + 60s 超时回归） | 开发 | T4.1~T4.2 | 0.5 人日 |
| T5.1 | security-model.md 更新（hooks 面/http 出网/权限规则）+ 收尾报告起草 | 文档 | 全部 | 0.5 人日 |
| T5.2 | 全量回归（pytest 全量 + vitest + tsc） | 测试 | 全部 | 0.5 人日 |
| T5.3 | Electron + vite dev 双模式真机走查（AC-25 清单） | 测试 | T5.2 | 0.5 人日 |
| T5.4 | PA1.0 代码同步 + 文档同步 + GitHub 提交推送 | 运维 | T5.3 | 0.5 人日 |

**合计：约 15-19 人日**（含测试与真机验证；前端任务与后端并行时人日合并计算）。

---

## 五、资源与团队分工

### 5.1 资源清单

| 资源类型 | 明细 | 用途 |
|---|---|---|
| **人力** | 1 名开发者（用户本人，全栈）+ 1 名 AI 协作智能体（WorkBuddy，承担开发执行/测试/文档） | 全流程 |
| **技术栈** | Python 3.10（后端，复用现有 venv）；TypeScript/React 18 + Vite 5（前端）；PostgreSQL 16（含 pgvector）；pytest-asyncio + vitest | 开发与测试 |
| **运行环境** | Windows 普通用户环境（无管理员）；Electron `--no-sandbox`；PostgreSQL 非服务启动（pg-start.vbs 开机自启）；后端 cwd=backend（相对路径依赖） | 运行与真机验证 |
| **外部服务** | LLM：deepseek-flash（主模型，fallback 链）；compress_model（S-1 前置修复后可用，用于记忆提取与 correction 提取）；MCP：iFind / 企查查 / MemPalace | 功能依赖 |
| **工具** | GitHub（HTTPS+GCM 凭据缓存，远端 main=7eb013b）；PA1.0 便携版 exe（win-unpacked，免安装真机验证）；`start-desktop.bat` 双模式启动 | 交付与验证 |
| **文档基线** | `round2-benchmark-research-2026-08-04.md`（B-1~B-14 定义）；`phase2-iteration-plan-2026-08-04.md`（格式模板）；`security-model.md`（安全模型） | 设计与追溯 |

### 5.2 角色职责与协作方式

> 本项目为单人开发 + AI 协作模式，以下角色按"职责域"划分（一人多角色），协作协议明确如下：

| 角色 | 职责 | 执行者 |
|---|---|---|
| **产品（Product）** | 范围定义与优先级决策（B 项取舍）、验收标准确认、范围外变更控制 | 用户 |
| **开发（Dev）** | 批次任务实现（后端/前端）、规则 DSL 与 Hooks 架构设计、代码质量（类型检查/命名/注释遵循蓝图章节标注） | WorkBuddy 执行，用户审阅 |
| **测试（QA）** | 验收标准转测试用例（表驱动）、全量回归（pytest+vitest+tsc）、真机双模式走查 | WorkBuddy 执行，用户确认 |
| **运维（Ops）** | 环境启动（PG/后端/Electron）、PA1.0 同步、GitHub 提交推送、文档归档 | WorkBuddy 执行，用户授权推送 |
| **架构评审** | 关键设计决策确认（规则 DSL 语义、Hooks 事件集、权限模式命名、correction 触发规则） | 用户（最终拍板）+ WorkBuddy（方案起草） |

**协作方式**：

1. **批次制**：每批次独立提交 + 独立回归 + 批次收尾小结（沿用阶段一/二惯例），保证任意批次可独立验收、可回退。
2. **审查先行**：涉及架构决策的任务（T0.2/T1.1/T2.1/T3.4/T4.1）先产出设计摘要（≤1 页）供用户确认，再进入实现——沿用阶段一"审查先行，重构后置"原则。
3. **配置驱动 + 默认安全**：所有新增行为默认关闭/默认兼容（hooks 空列表、规则表空、权限模式默认 default），通过 config.yaml 显式开启。
4. **真机验证**：每个批次完成后先小范围真机验证（一个会话/一次浏览器操作），再全量回归（阶段二经验：Windows 行为与 POSIX 差异大）。

---

## 六、风险管理

| # | 风险 | 等级 | 可能性 | 影响 | 应对与缓解策略 | 负责人 |
|---|---|---|---|---|---|---|
| R1 | 权限规则化改变既有确认行为，破坏既有测试/用户体验 | 中 | 中 | 高 | 默认规则 = 现有 safety_level 语义（100% 兼容）；规则表为空时行为不变（AC-1 强断言）；真机回归；每批次独立提交可回退 | 开发 |
| R2 | Hooks 引入执行开销 / 新增注入面（http hook 被劫持） | 中 | 中 | 中 | 默认空列表（AC-9）；hook 失败放行 + 5s 超时（AC-14）；http hook URL 强制过 `security/ssrf.py` 校验（AC-13）；审计进 react_events | 开发 |
| R3 | 内置工具下沉（is_kernel）破坏既有无 Skill 会话 | 低 | 低 | 中 | 白名单 None = 全量（保持 M1 行为）；仅 Skill 会话强过滤（AC-18/19 回归） | 开发 |
| R4 | 纠正沉淀误伤（把普通重发/拒绝当纠正，污染记忆） | 中 | 中 | 中 | 触发条件收紧（编辑后重发 + 否定词双条件）；importance 可配置；前端记忆视图可删除；提取走 compress_model 有降级（熔断器已有） | 开发 + 产品 |
| R5 | **S-1 compress_adapter 前置修复受阻**（compress_model 不可用/无匹配 provider） | 中 | 低 | 高（阻塞 T3.4） | 修复置于批次 0 先行（T0.3）；无匹配 provider 时优雅降级 None（现有设计）；correction 提取降级为"不入库 + 日志"，不阻塞其他批次 | 开发 |
| R6 | 前端改动回归（App.tsx 确认卡片/模式切换/编辑重发检测） | 低-中 | 中 | 低 | vitest + tsc 0 error 门禁；双模式真机（AC-25）；前端改动与后端解耦（事件协议扩展向后兼容） | 测试 |
| R7 | 单人开发进度延误（计划 13 个工作日） | 中 | 中 | 中 | 批次可裁剪：P1 项（S-8~S-11）可整体延后至阶段三延长版或阶段四；每批次独立验收不互相阻塞；预算缓冲 20%（15-19 人日 vs 13 个工作日） | 产品 |
| R8 | 与既有机制冲突（权限模式 vs Skill 白名单；hooks vs 权限确认） | 低 | 低 | 中 | 设计上明确优先级（session > skill > config；deny 优先）；T1.1/T3.1 依赖规则层统一求值，避免双轨判定 | 架构评审 |
| R9 | 网络/外部服务不可用（GitHub 推送、MCP、LLM） | 低 | 低 | 中 | 推送类操作用户本机执行兜底（阶段二经验：HTTPS 阻断时 SSH 备用）；LLM 走 fallback 链；测试用 mock 隔离外部依赖 | 运维 |
| R10 | correction 记忆类型 DB 迁移与既有数据冲突 | 低 | 低 | 低 | 沿用 V2 幂等迁移模式（ALTER TYPE ... ADD VALUE 或 CHECK 重建，先查后加） | 开发 |

---

## 七、质量与验收标准

### 7.1 质量指标（贯穿全阶段）

| 指标 | 门槛 | 度量方式 |
|---|---|---|
| 测试通过率 | 后端 pytest **≥ 973 + 新增全绿**；前端 vitest **≥ 13 + 新增全绿**；`tsc` **0 error** | 两条命令执行，无 skip 掩盖（win32 平台用例除外） |
| 代码质量 | 类型标注完整；异常分支有明确降级路径；蓝图章节号标注注释（沿用项目惯例） | 代码走查 |
| 安全基线 | 新增面（hooks http / 规则 DSL）不引入新的未审计出网点；默认安全、显式开启 | `docs/security-model.md` 同步 |
| 配置可回退 | 每个新机制（hooks/规则/模式/correction）均可通过 config 关闭且行为回退到阶段二 | 开关验证用例 |
| 兼容性 | Electron 生产 + vite dev 双运行模式全功能可用 | AC-25 真机清单 |
| 文档同步 | 计划文档随实施跟踪更新（状态行）；收尾报告归档；PA1.0 同步 | 文档检查 |

### 7.2 评审流程

| 评审点 | 时机 | 参与人 | 输入 | 通过标准 |
|---|---|---|---|---|
| **设计评审** | 每批次架构任务前（T0.2/T1.1/T2.1/T3.4/T4.1） | 用户 + WorkBuddy | 设计摘要（≤1 页） | 语义/命名/边界达成一致；无未决架构问题 |
| **批次验收** | 每批次完成后 | 用户 + WorkBuddy | 批次小结 + 回归结果 + 真机记录 | 该批次 AC 全过；回归全绿；无遗留 P0 问题 |
| **里程碑评审** | MS-1/MS-2/MS-3/MS-4 | 用户 | 里程碑 AC 清单执行记录 | 里程碑 AC 全部通过 |
| **阶段验收** | MS-5 | 用户 | 收尾报告 + 全量回归 + 真机走查记录 | AC-24~AC-27 全过；PA1.0 同步 + GitHub 推送完成 |

### 7.3 测试策略

1. **表驱动单测**（沿用阶段二）：规则求值/矩阵组合/风险分级用数据表驱动，覆盖边界（deny 优先、未匹配回退、路径模式通配）。
2. **集成测试**：hooks 三类实现各 1 条；correction 端到端用 mock 压缩模型（不依赖真实 LLM）；审批挂起恢复端到端。
3. **平台敏感测试**：win32 专属用例 `@pytest.mark.skipif(os.name != "nt")`；真机验证兜底。
4. **回归纪律**：每批次先跑全量 pytest 确认基线，再新增用例；禁止"先改后测"。

---

## 八、沟通与汇报机制

### 8.1 例会与同步节奏

| 机制 | 频率 | 形式 | 内容 |
|---|---|---|---|
| **批次收尾快照** | 每批次完成时 | 计划文档状态行更新 + 简短小结 | 完成项/AC 结果/遗留项/下批次计划 |
| **里程碑汇报** | MS-1~MS-4 每里程碑 | 里程碑 AC 清单执行记录 | AC 逐项结果 + 风险状态 |
| **阶段收尾报告** | MS-5 | `docs/phase-closeout-phase3-2026-08-04.md` | 成果/指标/教训/阶段四建议 |
| **问题升级** | 即时 | 对话直报 + 升级路径（见 8.3） | 阻塞性问题/需求变更 |

### 8.2 汇报形式与干系人

- **进度汇报载体**：`docs/phase3-iteration-plan-2026-08-04.md`（本文件，状态行随批次更新，实现"文档即看板"）；工作日志 `D:\Private agent\.workbuddy\memory\2026-08-04.md`（追加式，记录每批次要点与教训）。
- **干系人**：本阶段主要干系人为**用户本人**（产品/架构决策/验收）+ **WorkBuddy**（开发/测试/运维执行）。无外部团队，汇报以本地文档 + 对话小结为主；GitHub 远端（main）作为代码同步载体，提交信息含批次标注（如 `phase3-batch1: permission rules`）。

### 8.3 问题升级路径

```
级别 1（自行解决）：实现细节/测试失败/文档问题
    → WorkBuddy 按"修复根因 > 降级 > 回退"顺序自行处理，记录到工作日志
级别 2（需确认）：架构决策 / 需求变更 / 范围增减 / AC 调整
    → 停止该任务，产出一页问题说明（影响/选项/建议），提交用户拍板
级别 3（阻塞性）：外部依赖不可用（LLM/MCP/网络）/ 环境损坏 / 数据风险
    → 立即升级：暂停受影响批次，评估替代路径（fallback/降级/用户本机操作），
       在对话中直报并给出可执行选项
```

### 8.4 变更控制

- 范围外项（§2.3）如需纳入，须用户确认后更新本文件"范围定义"并调整里程碑；已排期任务优先级调整遵循"P0 优先、可裁剪 P1、批次独立"原则。
- 验收标准（§3.2）的修改须在批次启动前完成评审，批次中途不随意改 AC。

---

## 附录 A：本阶段与调研报告的映射

| 本计划章节 | 调研报告依据 |
|---|---|
| G-1~G-7 目标 | `round2-benchmark-research` §5.1 优先级排序（P0-1/P0-2/P0-3/P1-1/P1-2/P1-3） |
| S-1~S-11 范围 | B-1~B-14 中本阶段选取的 9 项（B-2/B-3/B-4/B-1/B-8/B-12/B-6/B-5/B-14）+ 前置修复 |
| 批次划分 | 调研报告 §5.2 阶段三建议批次计划（4 批次 + 收尾） |
| 风险 R1~R10 | 调研报告 §5.3 风险与规避（扩展至 10 项，补入阶段二经验） |

## 附录 B：实施约定速查（沿用阶段一/二）

- 后端启动：cwd 必须为 `backend/`；需 `WORKSPACE=backend`、`PA_DB_PASSWORD`、`PA_MASTER_KEY`（Electron 自动加载 .env）。
- pytest 必须加载 `backend/.env`；勿两个 pytest 进程并发操作同一测试库。
- WS 与 HTTP 同端口 8765；config.yaml 的 8766 为蓝图遗留。
- provider name 标识符：`^[A-Za-z0-9][A-Za-z0-9_-]*$`。
- asyncpg JSONB 返回 str，使用前必须 `json.loads`。
- git add 前审查（避免误入 PDF/vbs）；推送阻塞时用户本机执行兜底。

---

*本计划由 WorkBuddy 基于第二轮借鉴调研报告编制，随阶段三实施跟踪更新。*
