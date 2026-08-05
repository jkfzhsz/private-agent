# V1.5 下一阶段规划（2026-08-05 立项）

> 用户 2026-08-05 提出 8 项规划内容；本文记录**难度评估、完成状态与实施要点**。
> 极简调整项已当场完成（第 3、8 项），其余项供新对话启动时按序实施。

## 完成难度总览

| # | 规划项 | 难度 | 状态 | 一句话评估 |
|---|---|---|---|---|
| 3 | 理清 skill 与智能体区别 | ★☆☆☆☆ | ✅ 已完成 | 概念文档 `docs/agent-vs-skill.md`；代码已正确区分（UI 已叫技能库） |
| 8 | 记忆来源跳转 | ★★☆☆☆ | ✅ 已完成 | MemoryView 加来源按钮 + App 回调，切换会话（有 skill 直达对话视图） |
| 5 | 流程级暂停 | ★★☆☆☆ | ⏳ 待实施 | 前端暂停/继续按钮 + 后端 turn 级等待；复用现有 WS 通道 |
| 4 | 任务级断点恢复 | ★★★☆☆ | ⏳ 待实施 | checkpoint 存储已就绪（core/checkpoint.py），补恢复端点+WS+前端按钮 |
| 7 | 网页入库落地 | ★★★☆☆ | ⏳ 待实施 | 三选一：粘贴文本引导 / 轻量抓取(httpx+readability) / MCP 抓取对接 |
| 6 | 打包架构收敛 | ★★★☆☆ | ⏳ 待实施 | 打包版内置后端(extraResources) 或单目录收敛，消除双目录同步 |
| 2 | 连接器"开箱即用" | ★★★★☆ | ⏳ 待实施 | 预置 MCP 配置模板 + 引导 UI，降低 90% 配置门槛 |
| 1 | 子代理/任务委派 | ★★★★★ | ⏳ 待实施 | 新架构：子任务编排/并发/结果聚合/上下文隔离，独立设计 |

---

## ✅ 已完成项（本轮直接落地）

### 项-3：skill 与智能体区别（概念澄清）
- 交付：`docs/agent-vs-skill.md`（权威认知文档，含对比表/混淆点/开发指导）
- 结论：PA 是智能体本体，Skill 是 PA 工作中使用的技能集合；代码/UI 已正确区分，
  `AgentLibraryView.tsx` 仅剩历史文件名（可选改名，低风险）

### 项-8：记忆来源跳转
- 后端：无需改动（`source_session_id` 已在 GET /memories 返回）
- 前端：`MemoryView` 新增 `onOpenSession` 回调 + 列表行"↪ 来源会话 #id"按钮；
  `App.tsx` 传入 `handleSwitchSession`（有 skill 直达对话视图，无则回首页选模式）
- 验证：tsc + 前端 13 测试通过

---

## ⏳ 待实施项（按性价比排序，供新对话取用）

### 项-5：流程级暂停（★★☆☆☆，约 0.5 天）
- 目标：对话进行中可"暂停 → 人工检查 → 继续"，区别于现有"停止=终止"
- 方案：
  1. 后端：`run_turn` 循环内检查会话 `paused` 状态（sessions 表加 `paused` BOOL 或
     复用 `status='paused'`），暂停时轮次挂起不消耗、WS 发 `turn_paused` 事件
  2. WS：新增 `pause` / `resume` 消息
  3. 前端：发送区"⏸ 暂停 / ▶ 继续"按钮（生成中可暂停；暂停态可继续）
- 风险点：超时控制（挂起轮次需处理连接断开回退 interrupted）

### 项-4：任务级断点恢复（★★★☆☆，约 1 天）
- 现状：`core/checkpoint.py` 已实现**存储层**（每轮结束写 checkpoint 到 react_events +
  `mark_session_interrupted`），**恢复逻辑缺失**（注释明确"V2 断点续传待实现"）
- 方案：
  1. 后端：`POST /admin/sessions/{id}/resume` —— 读最新 checkpoint 事件 → 从 messages
     恢复完整 ctx → 从中断 turn 继续 ReAct（复用 run_turn，初始轮次=checkpoint.turn+1）
  2. WS：`resume` 消息（或复用 user_message + resume 标记）
  3. 前端：会话状态为 interrupted 时显示"▶ 断点继续"按钮
- 前提确认：需先读 `react_loop.py` 的 run_turn 签名确认恢复注入点

### 项-7：网页入库落地（★★★☆☆，约 0.5~1 天，三选一）
- 现状：知识库 UI 无网页入口；方案上"不内置抓取器"
- 推荐路径（按投入递增）：
  a. **粘贴文本入库**（最简）：KnowledgeView 上传区加"网页文本"标签页提示文案，
     引导用户粘贴正文入库 —— 0.5 天，纯前端
  b. **MCP 抓取对接**：文档说明如何用已有 MCP（如 iFind/企查查的资讯检索）抓取后入库 —— 0.5 天，文档
  c. **轻量内置抓取**：`POST /admin/knowledge/fetch_url`（httpx + readability 提取正文
     转 markdown 入库）+ URL 输入框 —— 1 天，需评估防滥用与超时
- 建议：先做 a+b 形成入口闭环，c 视需求再上

### 项-6：打包架构收敛（★★★☆☆，约 0.5~1 天）
- 现状：打包版 sidecar 探测顺序 `resourcesPath/backend > ... > D:\PA1.0\backend >
  D:\Private agent\backend`；当前**无 extraResources**，实际用 `D:\PA1.0\backend`
  → 双目录需 `build-electron.bat` 每轮同步（405 事故的根源）
- 方案 A（推荐）：electron-builder 配 `extraResources: [{from: ../backend, to: backend,
  filter: 排除 .venv/.env/outputs/logs}]` + sidecar 探测改为 resourcesPath 优先且唯一
  → 打包版自包含，不再依赖磁盘目录（.env 需提供默认/首次启动引导）
- 方案 B：统一单目录（去掉 PA1.0，探测只保留 D:\Private agent\backend）
- 权衡：A 更干净但 .env 与 venv 打包策略要设计（venv 体积 ~200MB+，建议排除 venv
  改由打包版带 requirements + 首次启动建 venv，或探测系统 python）

### 项-2：连接器"开箱即用"（★★★★☆，约 2~3 天）
- 目标：把常用 MCP 服务做成**预置模板**（名称/命令/参数/协议/说明），设置页"一键添加"
- 方案：
  1. 后端：`GET /admin/mcp/templates` 返回内置模板列表（如 agent-mail、deepseek 检索、
     腾讯文档等 10~20 个常用项；模板为纯配置不代凭证）；`POST /admin/mcp/servers`
     支持 `from_template` 字段一键实例化
  2. 前端：McpAddForm 加"从模板添加"下拉（选中即填充，用户只补凭证/URL）
  3. 文档：`docs/mcp-templates.md` 记录模板清单与维护方式
- 依赖：需先盘点常用连接器的标准 MCP 配置（官方文档），工作量主要在模板收集

### 项-1：子代理/任务委派（★★★★★，约 5~10 天，需独立设计）
- 目标：一次任务内派生子代理并行执行子任务，聚合结果回主对话
- 方案框架：
  1. 数据面：`subagents` 表（id/session_id/parent_turn/prompt/status/result）+ 上下文隔离
     （子代理独立 ctx，不复用主会话 messages）
  2. 执行面：子代理复用现有 ReactLoop 但挂独立 session/ctx；并发 Semaphore 限制
     （参照 V2 P2 工具并行模式）；子代理可再嵌套（深度上限 2~3）
  3. 协议面：WS 新增 `subagent_start/subagent_result/subagent_error` 事件，前端
     对话流内展示子任务卡片（参照任务状态抽屉）
  4. 主代理侧：模型以特殊 tool（`delegate_subtask`）触发委派，工具描述声明
     子任务边界与结果回传格式
- 建议：先出设计文档（ADR）+ 最小可用（单层、最多 3 并行）再扩展；与项-3 文档的
  "子代理 ≠ Skill" 界定保持一致

---

## 附：执行顺序建议

新对话可按 **项-4 → 项-5 → 项-7 → 项-6 → 项-2 → 项-1** 推进：
- 前四项（断点/暂停/网页入库/打包收敛）均为 1 天内可闭环的中小项，能快速提升可用性
- 项-2（连接器模板）适合单独阶段（含文档盘点）
- 项-1（子代理）作为独立里程碑，前置产出一份设计文档再动工
