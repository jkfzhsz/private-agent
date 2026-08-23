# 四窗口并发智能体架构设计（主智能体 + 三场景智能体）

> 版本：v1.1 | 日期：2026-08-08 | 状态：**已实施完成（P1-P4）**
> 前置：0.5.0 已完成（三场景子瞻/白圭/清和 + 场景独立记忆/KB）

---

## 1. 需求定义与可行性结论

### 1.1 需求

- **形态**：最多 4 个并发对话窗口 —— 1 个主智能体（系统监控/优化/进化）+ 3 个场景智能体（子瞻=工作学习、白圭=投资理财、清和=生活美学）
- **隔离**：每窗口对话内容完全独立（消息历史、记忆、KB、工具、工作区）
- **交互**：顶端工具栏自由切换窗口，切换即时无卡顿
- **主智能体职责**：监控系统运行状态 → 持续分析性能 → 提出优化方案 → 实施优化（自动/半自动）

### 1.2 可行性结论（基于现状探查）

| 维度 | 现状 | 结论 |
|------|------|------|
| 多会话并发 | WS 单端点按 `session_id` 路由，ReactLoop 每会话独立装配（MemoryManager/ContextManager/checkpoint） | ✅ 已支持，直接复用 |
| 数据隔离 | `sessions` 表 + `messages` 表按 session_id 隔离；场景记忆按 `scope` 隔离 | ✅ 已支持 |
| 会话类型 | sessions.kind 已有 `main/sub` 枚举（子代理委派用） | ✅ 可扩展 `kind='monitor'` 标识主智能体 |
| 系统监控 | **无 metrics 端点**，无性能采集 | ⚠️ 需新增（本章 §4 为主） |
| 自动执行 | ReactLoop 已支持 `auto_execute` 多轮 + 权限确认（elevated 工具 WS 60s 确认） | ✅ 主智能体自动执行复用此机制 |
| 工具栏切换 | 前端 Sidebar 已支持会话切换 + WS 重连 | ✅ 改为固定 4 窗口 tab 形态 |

**结论：技术上完全可行。** 核心工作量不在"多窗口"（已具备），而在两个新增维度：
1. **主智能体的监控数据链路**（系统指标采集 + 性能分析 + 优化建议生成）
2. **4 窗口固定形态的前端改造**（从"会话列表"升级为"固定窗口 tab"）

---

## 2. 总体架构

```
┌─────────────────────────────────────────────────────────────────┐
│                       Electron 渲染进程 (React)                  │
│  ┌──────┐ ┌──────┐ ┌──────┐ ┌──────┐   顶部工具栏(tab)          │
│  │ 监控 │ │ 子瞻 │ │ 白圭 │ │ 清和 │ ← 4 固定窗口, 点击切换       │
│  └──┬───┘ └──┬───┘ └──┬───┘ └──┬───┘                           │
│     │ 每窗口: 独立消息列表 + 输入框 + 事件流                      │
└─────┼────────┼────────┼────────┼───────────────────────────────┘
      │ WS     │        │        │
┌─────▼────────▼────────▼────────▼───────────────────────────────┐
│                   FastAPI (8765) 单进程                          │
│  WS /ws: 按 session_id 路由 → 每窗口独立 ReactLoop 实例          │
│  ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌────────────┐   │
│  │MonitorLoop │ │OfficeLoop  │ │DataLoop    │ │DesignLoop  │   │
│  │ kind=monitor│ │ kind=main │ │ kind=main  │ │ kind=main  │   │
│  └──────┬─────┘ └─────┬──────┘ └─────┬──────┘ └─────┬──────┘   │
│         │             │              │              │           │
│  ┌──────▼─────────────▼──────────────▼──────────────▼───────┐   │
│  │                 PostgreSQL (private_agent)                │   │
│  │  sessions/messages/user_memories/kb_*/react_events       │   │
│  │  + NEW: system_metrics(性能快照) / optim_log(优化记录)     │   │
│  └──────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

**关键设计原则**：
- 4 窗口共享同一 FastAPI 进程与 PostgreSQL —— **数据隔离靠 session_id + scope，不靠进程隔离**（避免 4 个后端进程的资源竞争与同步复杂度）
- 主智能体是一个**特殊 kind 的 ReactLoop**，其差异在：① 注入系统监控数据作为上下文 ② 具备系统级工具（读指标/执行优化脚本）③ 建议落库为 optim_log

---

## 3. 多对话窗口的状态管理与数据隔离

### 3.1 窗口模型

新增 `windows` 概念（轻量，不建新表，复用 sessions）：

| 窗口 | session 标识 | kind | locked_skill_name | 固定 slot |
|------|-------------|------|-------------------|-----------|
| 监控 | `main_window` | `monitor` | NULL（主智能体无场景） | slot=0 |
| 子瞻 | 常规 id | `main` | office | slot=1 |
| 白圭 | 常规 id | `main` | data_analysis | slot=2 |
| 清和 | 常规 id | `main` | frontend_design | slot=3 |

- **固定 slot 机制**：每个窗口绑定一个 slot（0-3），slot 与场景的映射恒定；用户切换场景 = 切换该 slot 绑定的会话 id
- **隔离边界**（沿用现有机制，无需改造）：
  - 消息历史：messages 按 session_id 隔离
  - 场景记忆：user_memories.scope（office/data_analysis/frontend_design/global）—— 已实现
  - KB：kb_documents.scenario 隔离 —— 已实现
  - 工作区：sessions.workspace 每会话独立目录（画地为牢）—— 已实现
- **新增约束**：kind='monitor' 会话仅 1 个（UNIQUE 部分索引 `WHERE kind='monitor'`），防止多开监控

### 3.2 前端状态管理

```typescript
// 每窗口独立状态, 不共享
interface WindowState {
  slot: number;            // 0=监控 1=子瞻 2=白圭 3=清和
  sessionId: number | null;
  events: ReactEvent[];    // 该窗口事件流
  input: string;
  isGenerating: boolean;
  status: ConnStatus;
}
const [windows, setWindows] = useState<WindowState[]>([initWindow(0), initWindow(1), ...]);
const [activeSlot, setActiveSlot] = useState<number>(1); // 当前显示窗口
```

- **WS 策略**：单连接复用（当前已按 session_id 路由），切换窗口不重连、不重建 —— 发送消息时带 `session_id: windows[activeSlot].sessionId`；接收消息按 `msg.session_id` 分发到对应窗口 state
- **懒加载**：窗口首次激活时才拉历史（`/admin/sessions/{id}`），避免 4 窗口全量加载拖慢启动
- **顶部工具栏**：固定 4 个 tab（监控📊/子瞻📄/白圭📈/清和🎨），含未读数/生成中动画标识

### 3.3 数据隔离风险与对策

| 风险 | 对策 |
|------|------|
| 场景记忆串扰 | 注入已按 scope 过滤（0.5.0 完成）；主智能体只读 global + 系统指标 |
| 文件写冲突 | 每窗口独立 workspace 目录；共享产物目录（outputs/）按会话前缀命名 |
| 事件流串窗口 | WS 消息必须带 session_id，前端按此分发；后端已保证（ReactLoop 每会话独立 event_queue） |

---

## 4. 主智能体：监控 / 分析 / 建议 / 自动执行

### 4.1 数据采集层（新增 system_metrics 表）

```sql
CREATE TABLE system_metrics (
    id          BIGSERIAL PRIMARY KEY,
    ts          TIMESTAMPTZ NOT NULL DEFAULT now(),
    kind        VARCHAR(20) NOT NULL,   -- system/session/provider
    session_id  BIGINT,                 -- kind=session 时归属会话
    name        VARCHAR(100) NOT NULL,  -- cpu_usage/ram_mb/ws_conns/turn_latency_ms/...
    value       DOUBLE PRECISION NOT NULL,
    meta        JSONB DEFAULT '{}'
);
CREATE INDEX idx_system_metrics_ts ON system_metrics(ts DESC);
CREATE INDEX idx_system_metrics_name ON system_metrics(name, ts DESC);
```

采集源（后台任务，默认 60s 间隔，`apscheduler` 已依赖）：

| 指标类别 | 指标 | 采集方式 |
|---------|------|---------|
| 系统级 | CPU%、内存 MB、磁盘、进程线程数 | `psutil`（新增依赖） |
| 服务级 | WS 连接数、活跃 turn 数、请求延迟 p50/p95 | main.py 装饰器计数 |
| 会话级 | 每窗口消息数、token 用量、工具调用失败率 | react_events 聚合（已入库） |
| 模型级 | provider 调用成功/失败、fallback 触发次数 | FallbackChain 计数 |

### 4.2 分析层（主智能体如何"看到"数据）

两种模式，按 token 预算动态选择：

1. **摘要模式（默认）**：启动时注入最近 N=20 条指标摘要（`[System Metrics] 最近1小时: CPU均值38% 峰值72% · WS连接4/4 · 工具失败率2.1% ...`）—— 主智能体无需工具即可感知状态
2. **按需查询模式**：主智能体可调用新增工具 `system_metrics_query`（范围过滤/聚合），深入分析具体时段

**新增工具**（挂在 monitor kind 专属白名单）：
```
system_metrics_query   # 查询历史指标(范围/聚合)
optim_plan             # 将优化建议落库 optim_log(供用户审批)
apply_optim            # 执行优化方案(elevated, 需 WS 60s 权限确认)
system_status          # 即时采集一次当前状态
```

### 4.3 优化闭环（提出 → 审批 → 实施 → 验证）

```
监控采集 → 主智能体分析(注入摘要) → 提出优化方案
  → optim_log 落库(状态=pending)
  → 前端监控窗口显示"待审批"卡片
  → 用户审批(同意/驳回/修改)
  → 批准后: apply_optim 执行(带权限确认)
  → 结果回填 optim_log(状态=applied/failed) + 新指标对比验证
```

**optim_log 表**：
```sql
CREATE TABLE optim_log (
    id          BIGSERIAL PRIMARY KEY,
    ts          TIMESTAMPTZ NOT NULL DEFAULT now(),
    proposal    TEXT NOT NULL,        -- 优化建议(主智能体生成)
    category    VARCHAR(30),          -- context/tool/model/memory/performance
    status      VARCHAR(20) DEFAULT 'pending',  -- pending/approved/rejected/applied/failed
    plan_json   JSONB,                -- 结构化执行步骤(工具调用序列)
    result      TEXT,                 -- 执行结果/验证数据
    session_id  BIGINT                -- 提出时的监控会话
);
```

### 4.4 自动执行的安全边界

- 遵循现有权限模型：`apply_optim` 标记 elevated → 触发 WS 60s 确认 + 会话缓存
- **保守优先**：V1 仅自动执行"只读/低风险"优化（缓存清理、上下文压缩参数、工具排序）；涉及配置修改/文件操作的方案必须人工审批
- 每次执行前主智能体生成 plan_json（明确的工具调用序列），审批时用户可见具体步骤

---

## 5. 四智能体通信与协调

### 5.1 通信方式（不引入消息总线，复用现有基础设施）

| 场景 | 机制 | 说明 |
|------|------|------|
| 主→场景 | 场景记忆注入（scope）+ 共享 KB | 主智能体可将优化结论写入 global 记忆，场景会话自动可见 |
| 场景→主 | react_events 聚合 | 主智能体通过指标查询感知各场景活动（无需直接对话） |
| 跨窗口对话 | **设计上禁止**（V1） | 保持简单：4 窗口是 4 个独立对话，不互相发消息 |
| 共享状态 | PostgreSQL 事务 | 所有窗口读写同一库，天然一致 |

### 5.2 协调机制

- **主智能体的"调度权"**：主智能体只读各场景会话的聚合指标，不直接插入其他窗口的对话（避免打断用户）；需要干预时通过 optim_log 提出，由用户决定
- **记忆协调**：跨场景共享信息（如"用户今天要出差"）写入 `scope='global'` 记忆，4 窗口均可见 —— 已有机制
- **并发安全**：4 窗口同时写入 messages 表由各自 session_id 事务隔离；共享表（user_memories 等）沿用现有 upsert（ON CONFLICT）

### 5.3 潜在冲突与对策

| 冲突 | 对策 |
|------|------|
| 主智能体优化误判 | optim_log 审批流 + apply_optim 权限确认 + 失败回滚（执行前快照） |
| 多窗口同时改同一配置 | 配置修改类优化串行化（optim_log 状态机：同一时间仅 1 个 approved 可执行） |
| 主智能体注入挤占上下文 | 监控摘要 ≤400 token，且仅注入 monitor 会话（不占场景窗口配额） |

---

## 6. 工具栏切换交互的技术实现

### 6.1 前端 UI

- 顶部工具栏改为**固定 4 tab**：`监控` | `子瞻` | `白圭` | `清和`
- 每 tab 显示：场景名 + 状态点（绿色=就绪/蓝色=生成中/红=错误）+ 未读数
- 切换交互：点击 tab → `setActiveSlot(slot)` → 立即渲染该窗口 state（**无网络请求**，因为状态已驻留内存）→ 若窗口未初始化则触发懒加载

### 6.2 WS 复用（关键优化）

```
当前实现: 切换会话 → 关闭 WS → 重连 → 拉历史(≈1-2s 卡顿)
新实现:   切换窗口 → 不发 WS 消息 → 渲染内存 state(≈0ms) 
          发消息时附带 window session_id → 后端路由到对应 ReactLoop
```

- 这解决了**问题 4 的根源**：当前"切换会话无输出"部分原因是切换触发 WS 重连的时序竞争；固定窗口 + 单 WS 复用后，切换不再涉及连接重建
- 后端 WS 已按 session_id 路由（`user_message.session_id`），单连接多会话天然支持 —— 无需后端改造

### 6.3 状态持久化与恢复

- 4 窗口的 sessionId 映射持久化：`sessions` 增加 `slot` 字段（NULL=非窗口会话）或前端 localStorage 映射
- 应用重启后：按 slot 映射恢复窗口 → 各窗口懒加载历史

---

## 7. 潜在架构风险与应对策略

| # | 风险 | 等级 | 应对 |
|---|------|------|------|
| R1 | 4 窗口并发 token 消耗高（多模型流式） | 中 | 非活跃窗口的流式输出继续但 UI 隐藏；限制总并发生成数（max_concurrent_turns=2，超出排队） |
| R2 | 主智能体监控注入的指标陈旧/失真 | 中 | 摘要模式限最近窗口 + 标注采集时间；`system_status` 即时采集兜底 |
| R3 | 自动优化误操作影响数据 | 高 | 三层防护：optim_log 审批 + apply_optim 权限确认 + 执行前备份快照（已有 backup 机制） |
| R4 | WS 单连接承载 4 会话消息风暴 | 低 | 消息按 session_id 分发（现有）；前端按窗口节流渲染（非活跃窗口延迟 flush） |
| R5 | 固定 4 窗口限制灵活性 | 低 | slot 机制可扩展为 N 窗口（V2）；V1 保持 4 简化 |
| R6 | 主智能体与场景智能体人格混淆 | 中 | kind='monitor' 的 system_prompt 明确"你是系统监控者，不是对话助手"，且工具集独立 |
| R7 | 前端重构风险（单会话→多窗口状态） | 中 | 分阶段：先加"多窗口 state"层保持单窗口渲染兼容，再迁移 UI |

---

## 8. 分阶段实施规划

### Phase 1：主智能体监控数据链路（后端，1-2 天）
1. 新增 `system_metrics` 表 + apscheduler 后台采集（psutil + WS 计数 + react_events 聚合）
2. 新增 4 个监控工具（system_metrics_query / optim_plan / apply_optim / system_status）
3. 新增 `optim_log` 表 + 审批流 API（GET/PUT /admin/optim-log/{id}）
4. 测试：test_system_metrics.py（采集落库/聚合/工具调用）

### Phase 2：多窗口前端改造（前端，1-2 天）
1. `windows[4]` 状态层 + 单 WS 复用（去掉切换重连）
2. 顶部固定 4 tab 工具栏 + 懒加载
3. 迁移现有单会话逻辑到 slot 窗口（会话列表保留为"历史归档"入口）
4. 测试：多窗口状态隔离（window1 消息不影响 window2）

### Phase 3：主智能体会话装配 + 优化闭环（1 天）
1. kind='monitor' ReactLoop 装配（注入指标摘要 + 监控工具白名单 + 专属 system_prompt）
2. 优化闭环前后端打通（建议→审批卡→执行→验证）
3. 端到端验证：监控窗口生成优化建议 → 审批 → 执行 → 指标对比

### Phase 4：协调与打磨（0.5-1 天）
1. global 记忆协调 + 跨窗口指标感知
2. 并发限制（max_concurrent_turns）+ 非活跃窗口渲染节流
3. 用户验收清单 + 文档

**总工期估计：4-6 天（单人）**，与 0.5.0 规模相当。

---

## 9. 设计确认（2026-08-08 蒋先生定案）

| # | 事项 | 决策 |
|---|------|------|
| 1 | 全局模型配置 | **主智能体与 3 场景智能体模型逻辑一致**（sessions.model_id，auto=fallback 链 / 具体 provider=锁定），支持用户手动修改，**不单独设计监控专属模型** |
| 2 | 自动执行边界 | V1 **允许"上下文压缩参数调整"类低风险配置修改自动执行**（可回滚）；文件操作/高危配置仍走审批 |
| 3 | 并行窗口限制 | 最多 4 窗口并行（4 智能体各独占 1 tab）；**关闭 tab → 会话自动归档至"历史任务"** → 支持从历史选择并继续对话（恢复 state 重新挂载窗口） |
| 4 | 主智能体主动对话 | 监控窗口支持用户主动对话；**入口=左上角头像点击进入对话界面**；主/场景智能体名称支持手动修改，设置收纳于"设置"新增卡片（智能体名称配置卡） |

### 9.1 补充问题解答：共享后端架构与模型隔离

**Q1: 共享 FastAPI + PostgreSQL 是否导致 LLM 调用混乱或状态冲突？**

不会。依据现状实现的隔离机制（已核实）：

| 层级 | 隔离机制 | 现状 |
|------|---------|------|
| 适配器层 | 每轮从 DB 读 `sessions.model_id` → `_build_session_adapter` 构建**独立 FallbackChain 实例**（无共享可变状态） | ✅ 已实现（main.py:1003-1005） |
| 会话层 | ReactLoop 每会话独立装配（ContextManager/checkpoint/event_queue） | ✅ 已实现 |
| 数据层 | messages/user_memories/kb 均按 session_id/scope 隔离 | ✅ 已实现 |
| 配置层 | provider 密钥/端点读取 `config_runtime`（只读共享，无写冲突） | ✅ 已实现 |

LLM 调用本身是**无状态 HTTP 请求**（请求-响应模型），适配器实例间零共享——并发 4 窗口调用相同/不同 provider 互不干扰。唯一共享点是配置读取（只读）与数据库连接池（asyncpg 连接级事务隔离，天然安全）。

**Q2: 不同窗口是否可独立配置并使用不同模型？如何实现模型级隔离？**

可以，且**数据模型已天然支持**——`sessions.model_id`（VARCHAR(50)）字段：

| model_id 值 | 语义 | 适配器行为 |
|------------|------|-----------|
| NULL / 'auto' | 跟随全局 fallback 链（deepseek-flash → …） | `build_fallback_chain(cfg)` 全链（自动降级） |
| 具体 provider 名（如 'qwen-plus'） | 手动锁定该模型 | 单模型 FallbackChain，失败不降级直接报错提示 |

**隔离实现（复用现有机制，无需新表）**：
1. 每轮 user_message：`SELECT model_id FROM sessions WHERE id=$1` → 构建独立 adapter → 该窗口本轮全程使用此模型，**与其他窗口零共享**
2. 修改即生效：前端窗口内模型下拉 → `PUT /admin/sessions/{id}/model`（写 sessions.model_id）→ 下一轮生效，不影响其他窗口
3. 前端每窗口独立持有 `sessionModel` state（App.tsx 已有机制，扩展为 per-window）

**V2 可选数据模型演进**（V1 不实施）：
```sql
-- 会话级 fallback 链覆盖(某窗口单独指定降级顺序)
ALTER TABLE sessions ADD COLUMN fallback_chain JSONB;
-- 会话级 provider 参数覆盖(max_tokens/temperature)
ALTER TABLE sessions ADD COLUMN model_params JSONB;
```
V1 判定：单 `model_id` 已完全满足"窗口间互不干扰"，以上为增强项。

---

## 10. 待确认事项

4 项确认 + 补充问题解答均已完成，**架构冻结**，可进入 P1 实施。

---

## 附录：与 0.5.0 的衔接

- 三个场景窗口 = 现有 office/data_analysis/frontend_design 会话，0.5.0 的场景记忆/KB/命名全部复用
- 主智能体 = 新增 kind='monitor' 会话，不与现有"主智能体改名"（agentName）冲突 —— 监控窗口显示"监控者"名，agentName 仍用于全局问候
- 本方案修复的"切换会话无输出"问题：Phase 2 的 WS 复用直接消除切换重连的时序竞争
