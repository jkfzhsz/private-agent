# 会议室（Meeting Room）：多智能体协作与产物交接方案

> 日期：2026-09-11
> 状态：**设计稿 v1（待蒋先生审定后开工）**
> 关联：next-phase-plan-2026-08-28-wuya-long-task-orchestration.md（Mission 层）、
> ADR-012（子代理委派）、next-phase-plan-2026-08-23-skill-binding-tool-assembly.md
> 证据基线：2026-09-11 代码级核实（delegate_subtask.py / subagent.py / mission_runner.py /
> mission_assembly.py / react_loop.py / main.py / schema.sql / admin.py / Sidebar.tsx /
> MissionPanel.tsx / App.tsx）

---

## 一、结论（先行）

1. **不需要新建多智能体框架。** 所需机制已有四块地基：角色五维绑定
   （`sessions.locked_skill_name`）、子代理执行（`SubagentRunner`）、异步编排
   （Mission 层，已 wire 进 `main.py`）、进度卡片（`MissionPanel`，已挂载）。
   缺口收敛为 **6 项可枚举的改动**（§3.2），不是一套新架构。

2. **产物交接不需要改动安全边界。** 这是本次最关键的发现：把「房间会话」的
   `workspace` 指向共享目录后，**现有继承链会自动让全体成员写进同一目录**——
   子代理在 `_create_sub_session` 取父会话 workspace 覆盖 `self._cfg.workspace_root`，
   该 cfg 直接传给 `ReactLoop`，而 `ReactLoop` 用它强制注入 `file_write.data_dir`。
   链路已逐行核实（§4.3）。**`file_write` 一行都不用改。**

3. **真正必须动的是「角色」这一维。** 委派协议（`delegate_subtask` schema）只有
   `{id, prompt, type}`，无角色参数；子代理无条件继承父会话的
   `locked_skill_name`（`subagent.py:404`）→ **子代理只能是主持人的克隆**。
   这是「子瞻委托清和」在当前代码下做不到的唯一原因。

4. **量级估计**：后端新增/改动约 550~700 行，前端约 350~420 行，配套单测约
   250~300 行；分 4 个阶段交付（§8）。**其中 P1 阶段（后端闭环）可独立验证**，
   不依赖任何 UI 改动。

5. **明确不做**（沿用 2026-07 架构原则，避免过度设计）：
   - 不做常驻成员会话（成员按里程碑按需 spawn，产物留在房间目录）
   - 不做里程碑并行调度（MissionRunner 现为串行，`depends_on` 未被解析；本次不引入 DAG 调度）
   - 不做成员间直连对话（星型拓扑，经主持人中转）
   - 不做无涯参与（无涯保持全局监督角色，不进房间）
   - 不做多根写权限（v1 单根=房间目录；v2 见 §4.3 权衡）
   - 不新建消息总线 / 不新增执行引擎（复用 ReactLoop + SubagentRunner）

---

## 二、已确认的产品决策（蒋先生 2026-09-11 裁决）

| # | 决策项 | 裁决 |
|---|---|---|
| 1 | M1「兼任」方案（`/召唤` 附加技能） | **排除**。每个智能体须有自身特色、独立工作区与技能，不做全能智能体 |
| 2 | 本次升级核心痛点 | **产物交接**。建立会议室的目的就是让不同智能体的产物暴露在同一工作区「房间共享目录」，且可**直接阅读、调用、修改** |
| 3 | 主持人 | **无涯不参与具体工作**。主持人由子瞻／清和／白圭其中之一担任，**每次协同任务由用户指定** |
| 4 | 执行模式 | **异步**；进度视图**本次一并设计** |
| 5 | 入口与 UI | 桌面单独建立**会议室入口**；进入后可勾选召唤子瞻／白圭／清和的任意组合，并指定本次主持人；**主持人统筹任务分配、执行与后续记录** |
| 6 | 退出后 | 历史会话保存**需要再多一项**（会议室类别） |

---

## 三、现状盘点：地基与缺口

### 3.1 四块可复用地基（已核实）

| 地基 | 位置 | 能力 | 对会议室的意义 |
|---|---|---|---|
| 角色五维绑定 | `sessions.locked_skill_name` → `_get_system_prompt`(main.py:702) / `_get_tools` / 记忆 scope / KB / `skills/*/skill.yaml:workspace` | 一次绑定：人格 + 工具白名单 + 记忆范围 + 知识库 + 工作区 | 换角色的全部维度已有归属，不需要新概念 |
| 子代理执行 | `core/subagent.py:181-488` | 独立子会话（`kind='sub'`）+ 复用 ReactLoop + 心跳 task + watchdog（stale/grace/kill）+ 硬总时长 | 房间成员的执行载体，生命周期机制全免费 |
| 异步编排 | `missions` 表（schema.sql:334）+ `MissionRunner`（main.py:1788 `_mission_spawn` 已接通） | 宪章/计划/预算/台账/状态机、pause/resume、escalated 待裁决、断点续跑 | 满足需求 4「异步」，且**已对全部会话类型开放**（装配点在 monitor 守卫之外，main.py:1775 vs 1710） |
| 进度卡片 | `components/MissionPanel.tsx`（已挂载 App.tsx:3299）、事件处理 App.tsx:2271-2325 | 状态徽标 / 里程碑列表 / 台账尾 5 条 / 裁决按钮 / 清除已完成 | 进度视图是**补齐**而非新建 |
| 项目列举 | `api/admin.py:3606 GET /admin/missions` | 返回 `plan`(含 milestones) / `journal` / `state`，**含断线重建所需的全部字段** | 进度视图的可靠性兜底，后端已完成 |

### 3.2 缺口清单（含证据）

| # | 缺口 | 证据 | 影响 |
|---|---|---|---|
| **G1** | **委派协议无角色维度** | `tools/builtins/delegate_subtask.py:42-77` schema 仅 `{id, prompt, type}` | 「子瞻委托清和」结构上做不到 |
| **G2** | **子代理无条件继承父会话角色** | `core/subagent.py:404` `row.get("locked_skill_name")`；`409` 同值写父 workspace | 子代理 = 主持人克隆 |
| **G3** | **无房间会话类型** | `storage/schema.sql:48-49` `kind CHECK (kind IN ('main','sub','monitor'))` | 房间无法与普通会话区分，历史树无法归类（需求 6） |
| **G4** | **里程碑无角色维度** | `core/mission_runner.py:277-286` `executor_type ∈ {subagent, script, wait}` 是**机制**维度；`:314-326` 派发时同样走 SubagentRunner | 异步编排里无法指定「这一棒交给清和」 |
| **G5** | **里程碑进度在 WS 链路上无数据源** | `mission_runner.py:186-191` `mission_created` 只发 `{mission_id, session_id, state}`（**无 `goal`、无 `plan`**）；`_push_update`(:563) 不发 milestones；而 `App.tsx:2279` 读 `msg.goal`（恒 undefined）、MissionPanel 的里程碑列表因此**恒为空** | 进度视图的「逐里程碑进度」不可用 |
| **G6** | **REST 兜底未接线** | `GET /admin/missions` 存在（admin.py:3606），但前端**仅在注释中引用**（MissionPanel.tsx:14、App.tsx:53），从未调用 | 断线/重开界面后进度丢失 |

### 3.3 三个关键机制事实（决定设计走向）

1. **写权限链是「会话级 workspace → 子代理继承 → ReactLoop 强制注入」**：
   `main.py:1546-1548` 用会话 workspace 覆盖 `cfg.system.workspace_root` →
   `subagent.py:384-393` 取父会话 workspace 覆盖 `self._cfg` →
   `subagent.py:474-481` `ReactLoop(cfg=self._cfg)` →
   `react_loop.py:1269-1273` 对 `file_write` 强制 `args["data_dir"] = ws_root` →
   `file_write.py:33-39` 校验 `resolved.startswith(safe_dir)`。
   **推论：房间共享目录只要成为会话 workspace，成员写权限自动落在同一目录。**

2. **读权限已全局放开**：`react_loop.py:1258-1268` 明确「file_read 全局读取——
   不再注入 data_dir 限定工作区」，当前只对 `read_artifact` 注入。
   **推论：需求 2 的「直接阅读」部分已经成立，无需任何改动。**

3. **Mission 里程碑是严格串行的**：`mission_runner.py:215` `for ms in plan:`
   顺序执行；schema 声明的 `depends_on[]`（schema.sql:338）**在运行器中未被解析**。
   **推论：v1 的「自由组合」是角色组合，不是并行执行；并行里程碑需另立工作项（§10-Q3）。**

---

## 四、架构设计

### 4.1 拓扑：星型，主持人为中枢

```
┌──────────────────────────────────────────────────────────────────────┐
│ 房间共享目录  D:\PA\rooms\<room_key>\                                 │
│   README.md      任务契约（目标/交付定义/成员）                         │
│   artifacts/     产物落地区（所有成员的产物都写这里）                     │
│   notes/         交接说明（谁交给谁、为什么、待办）                       │
├──────────────────────────────────────────────────────────────────────┤
│ 房间会话  kind='room'   locked_skill_name = 主持人角色                   │
│   workspace = D:\PA\rooms\<room_key>   ← 关键：全体成员写权限由此继承      │
│   room_meta = {host_role, members[], goal, created_by}                 │
│                                                                       │
│   用户 ──消息──▶ 主持人（房间主会话，主循环=ReactLoop）                   │
│                     │ 统筹：分配 / 执行 / 记录                          │
│                     ▼                                                 │
│                 Mission（异步编排，1 个房间可含多个 mission）             │
│                   里程碑① executor_type=subagent role=office           │
│                   里程碑② executor_type=subagent role=frontend_design  │
│                       └─ depends_on: [①]                              │
│                     ▼                                                 │
│                 子代理会话 kind='sub'  locked_skill_name=<该里程碑 role>  │
│                   workspace = 房间目录（继承）                          │
│                   tools = 该角色 skill 白名单（重新解析）                  │
├──────────────────────────────────────────────────────────────────────┤
│ 无涯（monitor）· 全程不参与 —— 保持全局监督角色                           │
└──────────────────────────────────────────────────────────────────────┘
```

**为什么是星型而不是网状**：子代理嵌套深度恒 1（`delegate_subtask.py:16-17` 明确
设计约束）。星型让主持人位于主循环位、成员位于子代理位，深度恰为 1，**零改动
规避该限制**；网状需要突破深度限制，代价远高于收益。成员之间的"协作"通过
**房间目录这一介质**发生（A 写 → B 读改），不需要成员间直接发消息。

### 4.2 房间会话模型

**选型**：`sessions` 表加 `kind='room'` + 新列 `room_meta JSONB`，
**不新建 `rooms` 表**。

理由：房间会话天然需要会话的全部既有机制——消息流、checkpoint 断点恢复、
react_events 埋点、WS replay、归档、`_session_locks` 串行锁。新建表会重复
`sessions` 的大部分字段并切断这些机制。

| 字段 | 值 | 说明 |
|---|---|---|
| `kind` | `'room'` | 需迁移 CHECK 约束（`VARCHAR(10)` 容量足够） |
| `locked_skill_name` | 主持人技能名 | `_get_system_prompt` 据此装配主持人人格与工具 |
| `workspace` | `D:\PA\rooms\<room_key>` | §4.3 的核心 |
| `room_meta` | `{host_role, members[], goal}` | 新列 |

**迁移语句**（沿用 `storage/migrations.py` 的幂等模式）：

```sql
ALTER TABLE sessions DROP CONSTRAINT IF EXISTS sessions_kind_check;
ALTER TABLE sessions ADD CONSTRAINT sessions_kind_check
  CHECK (kind IN ('main', 'sub', 'monitor', 'room'));
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS room_meta JSONB;
```

**历史可见性**：`admin.py:3546-3547` 的历史树查询是 `AND (s.kind IS NULL OR s.kind <> 'sub')`
→ 房间会话**天然进入历史树**，正合需求 6。

### 4.3 房间共享目录（本次核心）

**目录约定**：

```
D:\PA\rooms\<room_key>\
  README.md        任务契约（主持人创建时写入：目标、交付定义 DoD、成员名单）
  artifacts/       产物落地区（PPT/HTML/报告/图表）
  notes/           交接说明（上游→下游的交接记录、待办、已知问题）
```

`<room_key>` 建议 `YYYYMMDD-HHMMSS-<4位短hash>`，保证可读且唯一。

**打通机制（零改动写路径）**——已逐行核实：

```
房间会话 workspace = D:\PA\rooms\<key>
  │
  ├─ main.py:1546-1548   会话 workspace → cfg.system.workspace_root
  │     └─ 主持人主循环：file_write.data_dir = 房间目录     ✓
  │
  └─ subagent.py:384-393 取父会话 workspace → self._cfg.workspace_root
        └─ subagent.py:474-481  ReactLoop(cfg=self._cfg)
              └─ react_loop.py:1269-1273  file_write.data_dir = 房间目录  ✓
                    └─ 成员子代理：写权限落在房间目录      ✓
```

**结论：`file_write` / `react_loop` / `file_read` 全部不需要改动。**

**权衡（须蒋先生确认）**：

| 方案 | 成员可写范围 | 改动面 | 代价 |
|---|---|---|---|
| **v1（本稿推荐）** | 仅房间目录 | **零**（安全边界不动） | 成员在房间任务期间写不到自己私有工作区（**读仍全局可用**） |
| v2（延后） | 私有目录 + 房间目录（多根） | 需扩展 `file_write` 支持多根 | 动安全边界，需重新审计越权面 |

v1 符合需求 2 的字面要求（产物暴露在同一工作区、可读改），且不动安全边界；
v2 的收益（成员同时维护私有草稿）在 v1 下可用 `notes/` 变通。

### 4.4 角色装配：五维原子替换

**为什么必须原子替换**：子代理当前直接拿父会话的工具列表
（`delegate_subtask.py:269` `tools=tools`）。若只把人格换成清和而工具仍是子瞻的，
就会出现「清和的脑子配子瞻的手」——可能触发 skill 权限规则或越权。

**五维装配表**：

| 维度 | 装配时机 | 是否需改动 |
|---|---|---|
| 人格（system prompt） | `_get_system_prompt` 读子会话 `locked_skill_name` | **零改动**（子会话行写入后自动生效） |
| 知识库 | `skill.yaml:knowledge_base.scenario` 按 `locked_skill_name` 绑定 | **零改动** |
| 记忆 scope | 按 `locked_skill_name` 打标（main.py:1825-1829） | **零改动**（子代理本就不注入记忆，`subagent.py:457`） |
| 工作区 | 继承父会话 workspace = 房间目录 | **零改动**（§4.3） |
| **工具白名单** | 子代理继承父会话 tools 列表 | **必须新增**：按 role 重新解析 |

**唯一的实质性新增**是一个角色工具解析函数：

```python
async def resolve_role_tools(cfg, conn, role: str, *, room_scope: bool) -> list:
    """按 role 解析该角色的工具白名单(复用 skill 白名单机制)。

    room_scope=False → 拒绝(非会议室会话不得指定他角色, 防越权)。
    复用 skills 白名单解析路径, 保证与场景会话工具集一致。
    """
```

**角色白名单**：`office`(子瞻) / `data_analysis`(白圭) / `frontend_design`(清和)。
显式枚举而非开放字符串，防止模型传入任意 skill 名。

### 4.5 异步执行与进度视图

**异步链路**（已存在，仅需补 role）：

```
用户消息 → 主持人主循环 → mission_create(plan 含 role)
              → _mission_spawn → MissionRunner 后台 task（脱离 turn 生命周期）
              → mission_* WS 事件 → MissionPanel 实时刷新
              → 用户可离开界面；GET /admin/missions 兜底重建
```

**进度视图补齐清单**（对应 G5/G6/G4）：

| # | 改动 | 位置 |
|---|---|---|
| V1 | `mission_created` payload 补 `goal` + `plan`（含 milestones/role） | `mission_runner.py:186-191` |
| V2 | 前端事件处理填充 `milestones`（当前只填 state/detail/journal） | `App.tsx:2271-2312` |
| V3 | 接通 `GET /admin/missions?session_id=` 轮询兜底（断线/重开重建） | `App.tsx` 新增 effect |
| V4 | 里程碑行显示 **role 徽标**（子瞻/白圭/清和 + 头像），`EXECUTOR_ICON` 扩为 role-aware | `MissionPanel.tsx:82-86, 175-185` |
| V5 | 房间信息条：主持人 / 成员 / 房间目录（可点击打开） | 新组件 |

### 4.6 退出与会话保存（需求 6）

**模型**：退出会议室 = 房间会话 `status='archived'`（复用现有归档语义），
**不删除**任何东西；`room_meta` 与房间目录路径保留，产物可回溯。

**历史树新增一项**（`Sidebar.tsx:382-387` `SCENE_GROUPS` 增加分组）：

```tsx
{ key: "room", name: "会议室", icon: "🏛" }
```

分组规则同步扩展（`Sidebar.tsx:391-398`）：`s.kind === "room"` → `"room"` 组。

**条目显示**：房间主题 + `主持人·成员数` 标签（如「Q3 经营分析汇报 · 主持人 子瞻 · 3 人」）。

---

## 五、数据模型变更汇总

| 对象 | 变更 | 类型 | 迁移 |
|---|---|---|---|
| `sessions.kind` | 枚举加 `'room'` | CHECK 约束 | ✅ 需迁移 |
| `sessions.room_meta` | 新列 `JSONB` | 加列 | ✅ 需迁移 |
| `missions.plan` | milestone 加 `role` 键 | JSONB 内键 | ❌ **零 DDL** |
| `subagents` | 无需变更（`session_id` 即房间会话，归属天然成立） | — | ❌ |
| 房间目录 | `D:\PA\rooms\<room_key>\{README.md, artifacts/, notes/}` | 文件系统 | ❌ |

---

## 六、接口契约

### 6.1 委派协议（`delegate_subtask`）

```jsonc
{
  "subtasks": [{
    "id": "ppt",
    "prompt": "把 artifacts/report.md 转成商务 PPT…",
    "type": "code",
    "role": "frontend_design"        // 新增·可选·枚举(office|data_analysis|frontend_design)
  }]
}
```

- 缺省 → 继承主持人角色（向后兼容，现有调用零影响）
- **非会议室会话传入 `role` → 拒绝**（错误提示明确），防越权

### 6.2 Mission 里程碑（`mission_create`）

```jsonc
{
  "goal": "…",
  "plan": [{
    "id": "m1",
    "milestone": "撰写经营分析汇报稿",
    "executor_type": "subagent",
    "role": "office",                // 新增·可选
    "prompt_template": "…"
  }, {
    "id": "m2",
    "milestone": "转制商务 PPT",
    "executor_type": "subagent",
    "role": "frontend_design",       // 新增·可选
    "depends_on": ["m1"]
  }]
}
```

### 6.3 房间管理（新增 admin 端点）

| 方法 | 路径 | 作用 |
|---|---|---|
| `POST` | `/admin/rooms` | 创建房间：`{host_role, members[], goal}` → 建会话（`kind='room'`）+ 建房间目录 + 写 `README.md` → 返回 `{session_id, room_dir}` |
| `GET` | `/admin/rooms/{session_id}` | 读 `room_meta` + 目录文件清单 |

---

## 七、前端设计

### 7.1 入口（需求 5）

`Sidebar.tsx:38-86` `WORKSPACE_ITEMS` 新增：

```tsx
{ key: "meeting", label: "会议室", icon: <svg …/> }   // 置于「对话」之后
```

### 7.2 会议室视图

```
┌─ 会议室 ────────────────────────────────────────────────┐
│  已有房间列表（可进入 / 恢复已归档）                        │
│  [+ 新建会议室]                                        │
├────────────────────────────────────────────────────────┤
│  新建弹层：                                             │
│    会议主题  [________________]                        │
│    参会成员  [x]子瞻  [x]白圭  [ ]清和                  │
│    本次主持人 ○子瞻  ○白圭        ← 仅从已选成员中选      │
│    [创建并进入]                                        │
└────────────────────────────────────────────────────────┘
```

### 7.3 房间内布局

```
┌ 房间信息条 ────────────────────────────────────────────┐
│ 🏛 Q3 经营分析汇报 | 主持人 子瞻 | 成员 子瞻·白圭·清和    │
│ 📁 D:\PA\rooms\20260911-153012-a3f1   [打开目录]        │
├────────────────────────────────────────────────────────┤
│ MissionPanel（进度：状态徽标 + 里程碑[含 role 徽标] + 台账） │
├────────────────────────────────────────────────────────┤
│ 对话区（与主持人对话；主持人统筹分配与记录）                │
│ [输入框]                                               │
└────────────────────────────────────────────────────────┘
```

**UI 反模式规避**（沿用既定偏好）：房间信息条单行紧凑、不悬浮堆图标；
MissionPanel 默认**折叠**（点击标题展开里程碑与台账）；里程碑列表不默认全展开。

---

## 八、分阶段实施计划

| 阶段 | 内容 | 依赖 | 可独立验证 |
|---|---|---|---|
| **P1 后端闭环** ✅ | W1 角色透传（`delegate_subtask` + `SubagentRunner` + `_create_sub_session`）、W2a 房间会话后端（迁移 + `POST /admin/rooms`）、W2b 建房/读房端点、W3 房间目录与约定注入 | 无 | ✅ **CLI 可全量验证**：建房 → 主持人委派 `role=frontend_design` → 断言产物落在房间目录 |
| **P2 可用性** ✅ | 会议室入口（侧边栏）+ 新建弹层 + 房间信息条 + 历史「会议室」分组 | P1 | ✅ 已交付：`MeetingRoomView` / `RoomInfoBar` / `openPath` IPC；前端 83 passed |
| **P3 异步与可观** ✅ | W4 里程碑 `role` + W5 进度视图补齐（V1~V4） | P1 | ✅ 已交付：role 里程碑端到端（建房会话→Mission→角色子代理）+ 里程碑实时进度 + REST 兜底 |
| **P4 收尾** ✅ | 退出归档语义（离开 vs 关闭房间）+ 历史分组（已提前至 P2） | P2 | ✅ 已交付：`closeRoom` + 房间语义确认弹层 + 3 条集成用例 |

**W1 是本方案的关键路径**：它一次验证全部四个装配维度（人格/知识库/记忆/工作区，
工具白名单为唯一新增），而 P3 的里程碑 `role` 只是把同一函数在 Mission 链路再调一次。
**故 P1 通过后，P3 的风险大幅下降。**

### 8.1 P1 实施记录（2026-09-11 完成）

| 层 | 文件 | 改动要点 |
|---|---|---|
| 核心 | `core/room.py`（新增） | 角色枚举/`normalize_roles`/`room_key`/`rooms_root`/`room_dir`/`ensure_room_layout`/`parse_room_meta`/`resolve_role_whitelist`；W3 新增 `ROLE_LABELS`、`role_display`、`build_room_contract` |
| 迁移 | `storage/schema.sql`、`storage/migrations.py` | `kind` CHECK 扩为 `('main','sub','monitor','room')`（单一来源 `SESSION_KINDS`，幂等累积式迁移）+ `room_meta JSONB` 列 |
| 角色 | `core/subagent.py` | `SubagentRunner(role_skill=...)`；`_create_sub_session` 覆写 `locked_skill_name`（版本置 None）；**无条件继承父会话 workspace**；**继承 `room_meta`**（W3 成员侧上下文） |
| 委派 | `tools/builtins/delegate_subtask.py` | schema 增 `role` 枚举；准入校验（非房间会话拒绝 / 非法角色拒绝 / 解析器缺失拒绝）；**解析先于任何副作用**；逐子任务对齐 `(role, tools)` |
| 装配 | `main.py` | 注入 `_resolve_role_tools` 闭包（角色白名单 ∪ 基础工具 ∪ MCP）；`_get_system_prompt` 合并查询 + 注入「会议室约定」 |
| 端点 | `api/admin.py` | `POST /admin/rooms`（先建目录后插行）、`GET /admin/rooms/{id}`、`list_sessions` 携带 `room_meta`、通用建会话端点显式降级 `'room'` |
| 测试 | `tests/test_room_core.py`(51) / `test_room_role_delegation.py`(16) / `test_room_admin_api.py`(12) / `test_room_contract_injection.py`(7) / `test_room_p1_end_to_end.py`(4) | **97 条**会议室用例，含端到端闭环 |

**全量回归（P1 收口）**：`1988 passed / 0 failed`，20m03s。
> 首次运行时曾出现 1 个失败 `test_git_tools::test_resolve_backend_dir` —— 该用例断言
> `CWD/config/config.yaml` 存在，当时 CWD 是工作树而非 `D:\Private agent\backend`，
> **属环境问题而非回归**（同 CWD 单独复跑 15/15 通过）。全量回归必须在
> `D:\Private agent\backend` 作为 CWD 下运行。

**W3 的设计修订（相对本稿 v1）**：v1 只写了「人格零改动」，未说明**成员侧**如何得知
目录分工。实际实现让成员子会话**继承 `room_meta`**，于是主持人与成员共用同一个
`build_room_contract`，仅视角（主持人视角 vs 成员视角）不同 —— 成员提示词不含
`delegate_subtask`（嵌套深度恒 1，指引会诱发失败调用）。

**零回归设计**：所有房间能力均以 `room_meta` 非空 / `role` 显式传入为触发条件，
普通会话路径完全不走新代码；`test_room_contract_injection` 与
`test_room_role_delegation` 各有专门用例守这一条。

### 8.2 P2 实施记录（2026-09-11 完成）

| 层 | 文件 | 改动要点 |
|---|---|---|
| 共享层 | `renderer/utils/rooms.ts`（新增） | 角色表 `ROOM_ROLES`（子瞻/白圭/清和 + emoji + 定位）+ `roleName`/`roleLabel`/`roleBadge`；`listRooms`/`getRoom`/`createRoom`；`RoomMeta`/`RoomSummary`/`RoomInfo` 类型 |
| 入口 | `renderer/components/Sidebar.tsx` | `ViewKey` 增 `"meeting"`；`WORKSPACE_ITEMS` 在「对话」之后插入「会议室」（users 图标）；**历史分组新增「🏛 会议室」组**，`kind='room'` 优先于 `locked_skill_name` 判定；房间行副标题改「主持人 X · N 人」 |
| 视图 | `renderer/views/MeetingRoomView.tsx`（新增） | 房间列表（进行中/已归档徽标、点击进入）+ 新建弹层（主题 / 成员三选多 / 主持人仅从已选成员单选 / 创建并进入） |
| 信息条 | `renderer/components/RoomInfoBar.tsx`（新增） | 单行紧凑：← 房间列表 / 主持人 / 成员 / 产物·交接计数 / 房间目录（点击复制）/ 📁 打开目录 |
| 接线 | `renderer/App.tsx` | `roomInfo` 状态；`enterMeetingRoom(id)`（`activeSlot = -1`，房间不占四窗口）+ `exitRoomToList()` + `handleNavigate`（任何侧边栏导航都退出房间呈现）；`view === "meeting" && roomInfo` 复用既有 chat 块并在头部下方插入信息条；房间空态文案；`handleSwitchSession` 遇 `kind='room'` 转走 `enterMeetingRoom` |
| 主进程 | `main/index.ts`、`main/preload.ts`、`renderer/vite-env.d.ts` | 新增 `app:open-path` IPC（`shell.openPath`，仅接受**绝对且已存在**路径）+ `window.pa.openPath` |
| 测试 | `MeetingRoomView.test.tsx`(9) / `RoomInfoBar.test.tsx`(6) / `SidebarGrouping.test.ts`(7) + 后端 `TestRoomListForMeetingView`(1) | **前端 83 passed / 0 failed**（13 文件全绿，含既有 App/Windows 四窗口集成）；**会议室后端 98 passed** |

**三处设计细化（相对本稿 v1）**：

1. **房间不占用四个单智能体窗口**。主持人可能是子瞻/白圭/清和任一，若复用其
   槽位会顶掉该智能体自己的窗口快照 → `enterMeetingRoom` 置 `activeSlot = -1`
   （不属于 `WINDOW_SLOTS`），审批面板等 `activeSlot === 0` 门控自然不触发。
2. **房间内复用既有对话区**。整块 chat 视图约 900 行，复制一份的维护成本与
   漂移风险都不可接受 → 条件改为 `view === "chat" || (view === "meeting" && roomInfo)`，
   信息条插在对话头部之后、断点横幅之前。
3. **历史分组提前到 P2**（原稿列为 P4-W6）。原因是 P2 一上线就会暴露误分类：
   房间的 `locked_skill_name` = 主持人角色，不先判 `kind='room'` 就会落进
   「子瞻/白圭/清和」组 —— 用户看不出这是一次协同。故先落地「🏛 会议室」组与
   `historyGroupKey` 纯函数（含 7 条单测），P4 只剩「退出归档」语义。

**列表契约**：会议室视图用 `GET /admin/sessions?has_messages=false` 取房间 ——
默认 `has_messages=true` 会按"至少一条 assistant 回复"过滤，**刚建好还没对话的
房间会直接消失**（用户会以为建房失败）。该参数语义由后端
`TestRoomListForMeetingView` 守住。历史树仍用默认值（只收有对话的房间）——
两者是有意的分工：会议室列表 = 全部房间，历史树 = 有对话的房间。

**交付前提**：前端 `dist/` 与主进程 `dist-main/` 已重新产出（本次改动同时涉及
渲染进程与主进程 IPC）。若在普通 CMD 执行 `npm run build` 或
`build-electron.bat` 会得到干净产物（`dist/` 累积的旧 chunk 会被清掉）。

### 8.3 P3 实施记录（2026-09-12 完成）

| 层 | 文件 | 改动要点 |
|---|---|---|
| W4 校验 | `tools/builtins/mission_tools.py` | `plan[].role` 入 schema（枚举三角色，可选）；`_validate_plan(plan, room_scope)` —— 非会议室会话携带 role **创建时即拒绝**（不留半成品 mission 行）；`build_mission_tools(room_scope=)` |
| W4 装配 | `core/mission_runner.py` | `MissionRunner(spawn)(role_tools_resolver=)`；`_run_subagent_milestone` 读 `ms.role` → **解析先于任何副作用**（不建子代理行/不占配额）→ `SubagentRunner(role_skill, tools=角色白名单)` |
| W4 接线 | `main.py` | `_mission_spawn` 传 `role_tools_resolver=_resolve_role_tools`（复用 delegate 闭包）；`build_mission_tools(room_scope=session_kind == "room")` |
| V1 (G5) | `core/mission_runner.py` | `spawn` 的 UPDATE 改 `RETURNING charter, plan`（同连接同语句）→ `mission_created` payload 补 `goal` + `plan`（`_mission_plan_view` 只留 id/milestone/executor_type/role/status） |
| V2 | `core/mission_runner.py` | `_push_update(..., plan=)`：每个里程碑状态落库后随 `mission_update` 下发 —— 否则前端 doneCount 只能等 `mission_done`，长任务中段恒 0/N |
| V2 前端 | `renderer/App.tsx` | `mission_created` 填 `milestones`（此前恒空）；`mission_update` 合并 `plan`；`WSMessage` 增 `plan?: unknown` |
| V3 (G6) | `renderer/App.tsx` | 新增 `fetchMissions`（`GET /admin/missions?session_id=`），在 **WS 重连**与**会话切换**两处与 `fetchSubagents` 并列调用；归一化 `missionStateFromRow`/`normalizePlan`/`normalizeJournal`（JSONB str/dict 双形态，脏数据降级不抛） |
| V4 | `renderer/components/MissionPanel.tsx` | 里程碑行 role 徽标（`📄子瞻`/`📈白圭`/`🎨清和`，未知角色原样展示不静默丢弃）；仅展开区渲染、不占额外行 |
| 测试 | 后端 `test_mission_runner.py` +3、`test_mission_tools.py` +6；前端 `MissionPanel.test.tsx` +10 | **后端全量 2001 passed / 0 failed**（25m09s）；**前端 93 passed / 0 failed**（13 文件全绿） |

**两个语义裁决（与 delegate 的 role 准入对齐）**：

1. **role 仅会议室会话可用，且在 `mission_create` 时拒绝**，而非执行期降级 ——
   静默降级会让主持人以为派给了清和、实际是子瞻在干（比失败更糟）。
2. **`role=""` 与 `None` 同义（未指定）**：与 runner 侧 `if role:` 真值判定一致；
   schema enum 已约束模型不会传空串，校验层的宽松只是防御性兼容。

**G5/G6 缺陷闭环**：V1 修复 G5（`mission_created` 缺 goal/plan → 前端里程碑列表
恒为空）；V3 修复 G6（`GET /admin/missions` 后端早已就绪但前端从未调用）。
两者叠加后，进度视图首帧由 WS 事件渲染、断线/重开由 REST 全量重建 ——
「异步任务可关界面、重开可重建进度」的验收达成。

### 8.4 P4 实施记录（2026-09-12 完成）—— 会议室四阶段全部收口

| 层 | 文件 | 改动要点 |
|---|---|---|
| 语义 | `renderer/App.tsx` | 新增 `closeRoom`：归档房间（`PUT status='archived'`）→ 清 `roomInfo` → 回会议室列表，并通知「可在历史树『🏛 会议室』组恢复；产物仍保留在房间共享目录」。**不走 `closeWindow`** —— 房间 `activeSlot=-1` 不占窗口槽位，走窗口切换逻辑会跳到无关窗口 |
| UI | `renderer/App.tsx` | 关闭确认弹层房间感知：房间内标题「关闭会议室」/ 按钮「关闭房间」/ 文案说明产物保留；普通会话文案不变（零回归） |
| 测试 | `__tests__/MeetingRoomClose.test.tsx`（新增 3 条 App 级集成） | ①「← 房间列表」仅离开、**零 PUT**；②房间内关闭 → `PUT {"status":"archived"}` + 回列表；③普通会话弹层文案不变 |
| 健壮性 | `frontend/test-setup.ts` | testing-library `asyncUtilTimeout` 1s→3s：并行负载下 HomeView 懒加载渲染可能超 1s（AC-17 实测 1704ms 失败→修复后稳定通过）。只影响"等多久"，不改断言语义 |

**两个出口的最终语义**（§4.6 落地）：

| 动作 | 入口 | 效果 |
|---|---|---|
| 离开会议室 | 房间信息条「← 房间列表」 | 仅切换视图；房间与产物不动，随时可回 |
| 关闭房间 | 房间内「⋯ 更多 → 关闭对话」 | `status='archived'` → 历史树「🏛 会议室」组带「已归档」标；再次进入自动恢复（`enterMeetingRoom` 置回 active）；**产物/交接文件不删** |

**前端测试规模**：14 文件 / **96 passed / 0 failed**（含 App 级四窗口集成与房间关闭语义集成）。

---

## 九、风险与验收

| 风险 | 等级 | 说明 | 缓解 |
|---|---|---|---|
| 只换人格不换工具 | **中高** | 「清和的脑子配子瞻的手」，可能越权或触发 skill 权限规则 | 五维**原子替换**；`resolve_role_tools` 与场景会话走同一白名单解析路径；单测断言工具集与独立场景会话**逐项一致** |
| 非会议室会话滥用 `role` | 中 | 模型可能在普通会话里指定他角色越权 | `room_scope` 硬校验 + 拒绝并返回明确错误；单测覆盖 |
| 成员写不到私有工作区 | 中 | v1 单根方案的固有代价 | 文档明示；`notes/` 变通；v2 多根作为后续选项（§10-Q2） |
| 里程碑串行成为瓶颈 | 中 | 多人任务实际排队执行 | v1 明确不做并行（§1.5）；`depends_on` 未被解析的现状一并记录（§10-Q3） |
| 房间目录膨胀 | 低 | 产物长期累积 | 归档时提示目录体积；后续可加清理工具 |
| 深度恒 1 无法链式 | 低（架构约束） | 子瞻→清和→子瞻 不可行 | 星型拓扑已规避；链式需求走 Mission 多次 spawn |

**P1 验收标准**（2026-09-11 全部达成）：

| # | 标准 | 状态 | 证据 |
|---|---|---|---|
| 1 | 房间会话创建后 `kind='room'`、`workspace` 指向房间目录、`room_meta` 完整 | ✅ | `test_room_admin_api::TestCreateRoom`、`test_room_p1_end_to_end::test_room_creation_lays_out_shared_dir` |
| 2 | 主持人在房间内委派 `role=frontend_design`，子会话 `locked_skill_name='frontend_design'` | ✅ | `test_room_role_delegation::TestSubSessionRolePersistence`、`test_room_p1_end_to_end::test_member_session_writes_into_room_dir` |
| 3 | 子代理的工具集与该角色独立场景会话**逐项一致** | ✅ | `test_room_core::TestResolveRoleWhitelist::test_parity_with_scene_session_frozen_tools`（三角色参数化） |
| 4 | 子代理写入目标 = 房间目录 | ✅ | `ReactLoop._cfg.system.workspace_root == room_dir`（即 `file_write.data_dir`，`react_loop.py:1249-1273`） |
| 4b | 越出房间目录的写入被拒（安全边界未松动） | ✅ 既有机制 | P1 **未改动** `file_write`/`react_loop` 的校验逻辑，越界拒绝由 `file_write.py:33-39` 原有实现保证，其自身用例覆盖；本次仅改变 `data_dir` 的取值来源 |
| 5 | 非房间会话传 `role` 被拒 | ✅ | `test_room_role_delegation::TestRoleAdmission` |
| 6 | 后端全量回归 0 failed | ✅ | **1988 passed / 0 failed**（2026-09-11，CWD=`D:\Private agent\backend`，耗时 20m03s）|

**成员侧提示词**：`test_room_contract_injection` 断言成员段含 `artifacts/` 路径、
不含 `delegate_subtask`；普通会话断言**完全不含**「会议室约定」（零回归守门）。

---

## 十、打开问题（Q1–Q5 已按本稿倾向裁决，2026-09-11）

| # | 问题 | 选项 | 裁决 |
|---|---|---|---|
| **Q1** | 「直接调用」的语义 | (a) 通过房间目录文件路径引用（下游读上游产物）；(b) 允许成员读取上游子代理的**结果文本**（现 `subagents.result` 已落库） | **(a) 为主、(b) 为辅**——文件是唯一可靠的交接介质，文本受 `InjectionGuard` 截断（约 8k）。已落地：提示词明示「引用上游产物写相对路径、不要粘贴全文」 |
| **Q2** | 是否需要 v2 多根写权限 | (a) 不做，v1 够用；(b) 做，成员可同时写私有目录 | **先不做**，视 P2~P3 实际使用反馈再定 |
| **Q3** | 是否引入里程碑并行调度 | (a) 不做，串行够用；(b) 解析 `depends_on` 做拓扑排序 + 并行批次 | **先不做**。本次痛点是交接而非吞吐；并行会引入新的并发审计面 |
| **Q4** | 房间内是否允许直接 @ 某个成员（不经主持人） | (a) 不允许，严格星型；(b) 允许，等于给主持人一条系统提示 | **(a)**，保持单一统筹者，避免职责混乱 |
| **Q5** | 房间目录根路径 | (a) `D:\PA\rooms\`（与各成员工作区同级）；(b) 其他位置 | **(a)**，符合 D 盘约定且与 `D:\PA\zizhan`/`baigui`/`qinghe` 同级 |

---

## 附录 A：核实过的代码坐标速查

| 事实 | 坐标 |
|---|---|
| 委派 schema（无 role） | `tools/builtins/delegate_subtask.py:42-77` |
| 委派 handler 传父 tools | `tools/builtins/delegate_subtask.py:269` |
| 子代理角色继承 | `core/subagent.py:404` |
| 子代理 workspace 继承 + cfg 覆盖 | `core/subagent.py:384-393` |
| 子代理 ReactLoop(cfg=self._cfg) | `core/subagent.py:474-481` |
| 子代理不注入记忆 | `core/subagent.py:457` |
| 里程碑 executor_type 分支 | `core/mission_runner.py:277-286` |
| 里程碑子代理派发 | `core/mission_runner.py:288-332` |
| 里程碑串行执行 | `core/mission_runner.py:215` |
| `mission_created` payload（缺 goal/plan） | `core/mission_runner.py:186-191` |
| `_push_update`（不含 milestones） | `core/mission_runner.py:563-570` |
| Mission 工具装配（在 monitor 守卫之外） | `main.py:1710`（守卫）vs `main.py:1775-1820`（装配） |
| Mission spawn 工厂注册 | `main.py:1810` |
| 会话 workspace → cfg 覆盖 | `main.py:1546-1548` |
| `file_write.data_dir` 强制注入 | `core/react_loop.py:1269-1273` |
| `file_read` 全局放开 | `core/react_loop.py:1258-1268` |
| `file_write` 路径校验 | `tools/builtins/file_write.py:33-39` |
| sessions.kind CHECK 约束 | `storage/schema.sql:48-49` |
| missions 表 DDL（plan 为 JSONB） | `storage/schema.sql:334-351` |
| 历史树过滤 `kind<>'sub'` | `api/admin.py:3546-3547` |
| `GET /admin/missions`（含 plan） | `api/admin.py:3606-3654` |
| MissionPanel 已挂载 | `frontend/renderer/App.tsx:3299` |
| Mission 事件处理 | `frontend/renderer/App.tsx:2271-2325` |
| MissionPanel 里程碑渲染（缺 role） | `frontend/renderer/components/MissionPanel.tsx:82-86, 175-185` |
| 历史树场景分组 | `frontend/renderer/components/Sidebar.tsx:382-398` |
| 侧边栏导航项 | `frontend/renderer/components/Sidebar.tsx:38-86` |
| 中间件白名单双保险 | `core/mission_assembly.py:29` |
| 三角色工作区 | `backend/skills/{office,data_analysis,frontend_design}/skill.yaml:workspace` |
