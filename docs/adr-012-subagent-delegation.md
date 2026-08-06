# ADR-012 子代理 / 任务委派（V1.5 项-1）

> 状态: **M3+M4 已完成（2026-08-06，子代理全链路闭环：数据面/执行面/协议面/加固 4 里程碑全部落地）**  
> 日期: 2026-08-05  
> 关联: `docs/agent-vs-skill.md`（子代理 ≠ Skill 界定）、V2 P2 同轮工具并行、  
> `core/react_loop.py`、`core/checkpoint.py`、`observability/logging.py`

## 1. 背景与目标

一次任务内派生子代理**并行**执行子任务，聚合结果回主对话（类似  
Claude Code subagent / OpenCode spawn）。现状：

- 主会话 ReAct 循环是单线程的：多工具可并行（V2 P2），但"让模型分头  
  研究几个独立问题"只能串行追问，无法并行展开，且长上下文互相污染。
- 目标：主模型通过 `delegate_subtask` 工具一次性委派 1~3 个独立子任务，  
  子代理并行执行（独立上下文），结果聚合后回主对话继续推理。

约束与边界：

- **子代理 ≠ Skill**：Skill 是主代理的工具集（被动）；子代理是独立  
  推理循环（主动执行一段完整任务）。不新增概念层，复用 ReactLoop。
- 架构原则（2026-07 决策）：外部能力走 MCP、参数跟随模型、避免过度设计。
- 复用：ReactLoop 已支持独立 session 的完整上下文管理/压缩/checkpoint，  
  子代理直接挂独立 session 复用，不发明新执行引擎。

## 2. 术语

| 术语            | 含义                                                |
| ------------- | ------------------------------------------------- |
| 主会话（parent）   | 用户正在对话的会话，负责委派与聚合                                 |
| 子代理（subagent） | 一次委派产生的独立 ReAct 循环，独立 ctx 与事件流                    |
| 委派（delegate）  | 主模型调用 `delegate_subtask` 工具触发                     |
| 心跳（heartbeat） | 子代理周期上报的存活信号（DB 时间戳 + WS 事件），与执行 task 分离          |
| watchdog      | 主对话侧的监听者：轮询扫描子代理心跳，超时判 stale → grace → kill（§3.3） |

## 3. 架构设计

### 3.1 数据面：`subagents` 表

```sql
CREATE TABLE subagents (
    id            BIGSERIAL PRIMARY KEY,
    session_id    BIGINT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
    parent_turn   INT NOT NULL,              -- 主会话触发委派的轮次
    parent_task   TEXT,                      -- 主代理分配的任务 id(同轮可多个)
    prompt        TEXT NOT NULL,             -- 委派指令(模型生成)
    model_id      VARCHAR(50),               -- 子代理模型(默认继承主会话)
    status        VARCHAR(20) NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending','running','succeeded','failed','cancelled')),
    result        TEXT,                      -- 最终结果(final content / error)
    tool_calls    INT NOT NULL DEFAULT 0,    -- 子代理工具调用次数(统计)
    error         TEXT,                      -- 失败原因枚举, 见 §3.6 状态枚举
    -- V1.5 项-1 监听/心跳:
    -- last_heartbeat_at: 心跳上报时间戳(watchdog 判 stale 依据; 统一 UTC)
    -- started_at      : 子代理开始运行时间(硬总时长上限的计时起点)
    -- stalled_at      : 首次检出 stale 的时刻(grace 宽限从此刻起算)
    -- finished_at     : 终态时刻(failed/succeeded/cancelled 统一记录)
    last_heartbeat_at TIMESTAMPTZ,           -- UTC; 初始 NULL; 首次心跳后刷新
    started_at        TIMESTAMPTZ,           -- UTC; running 置位时写入
    stalled_at        TIMESTAMPTZ,           -- UTC; watchdog 判 stale 时写入
    finished_at       TIMESTAMPTZ,           -- UTC; 终态写入
    restart_attempts  INT NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_subagents_session ON subagents(session_id, parent_turn);
CREATE INDEX idx_subagents_heartbeat ON subagents(status, last_heartbeat_at)
    WHERE status = 'running';   -- watchdog 扫描"运行中但心跳过期"的子代理
```

> **时间戳约定（硬约束）**：`last_heartbeat_at` / `started_at` / `stalled_at` /  
> `finished_at` / `created_at` / `updated_at` **一律 UTC**（应用层  
> `datetime.now(timezone.utc)` 或数据库 `now()`）。禁止使用机器本地时区  
> 时间 —— 多实例/多机时钟偏移会直接造成心跳超时误判。

> **状态枚举（error 字段取值）**：

| status    | error                             | 含义                            |
| --------- | --------------------------------- | ----------------------------- |
| running   | `null`                            | 运行中（心跳持续刷新）                   |
| failed    | `heartbeat_timeout`               | 运行中心跳超时，watchdog 触发           |
| failed    | `heartbeat_timeout_after_restart` | 后端重启发现僵尸 running 记录（§3.3e 兜底） |
| failed    | `max_lifetime_exceeded`           | 超过最大总生命周期硬上限（§3.3d）           |
| failed    | `(异常栈摘要)`                         | 子代理执行异常自然失败                   |
| cancelled | `null`                            | 父会话取消传播                       |

子代理的对话历史存哪？**两个选项**：

- **A（推荐）独立 session 行**：子代理创建时 `INSERT sessions (status='active',
  locked_skill_name=父技能, model_id=父模型)`，消息全部落在该 session 的  
  messages 表，ReactLoop/checkpoint/压缩**零改动**复用；主会话通过  
  `subagents.session_id` 关联子代理的 session。清理：父会话删除时子会话  
  cascade（需在 sessions 上记 parent 关系，或用 subagents 行手动级联）。
- **B 内存 ctx**：子代理仅内存建 ContextManager，不落库。省一张关联表，  
  但 checkpoint/压缩/断点恢复全部失效，且进程重启丢中间态。

> **决策：A**。代价是子 session 会出现在历史会话列表 —— 需在  
> `list_sessions` 过滤 `id IN (SELECT session_id FROM subagents)` 或给  
> sessions 加 `kind VARCHAR(10) DEFAULT 'main' CHECK (kind IN ('main','sub'))`。

### 3.2 执行面：复用 ReactLoop，独立 ctx

```
main ReactLoop (parent turn N)
  ├─ delegate_subtask(prompt=..., subtasks=[{id, prompt}])
  │    ├─ 校验: 本轮子代理数 ≤ max_parallel(3), 嵌套深度 < 2
  │    ├─ 创建 N 个 subagents 行 (pending)
  │    ├─ WS 推送 subagent_start × N
  │    └─ 并行 spawn SubagentRunner × N (asyncio.Semaphore(3))
  └─ 等全部子代理完成(主循环挂起等待, 复用 pause_controller 思路:
       per-session asyncio.Event 集合, 不阻塞 WS 主循环)
       └─ **监听: 轮询式 wait + 心跳扫描 + stale/grace/kill(§3.3)**
            —— 绝不用裸 await wait, 确保子代理挂掉时主对话能及时释放
```

**SubagentRunner**（新模块 `core/subagent.py`，~200 行）：

```python
class SubagentRunner:
    def __init__(self, conn, cfg, subagent_id, prompt,
                 parent_session_id, parent_turn, event_sink):
        ...
    async def run(self):
        # 1. 创建子 session(继承父 skill/model/workspace)
        # 2. 复用 main.py 的 _handle_user_message 逻辑: ContextManager +
        #    ReactLoop(resume_from_turn=None) + run_turn(prompt)
        # 3. run_turn 内事件改推 subagent_* 事件(事件前缀区分)
        # 4. final → 写 subagents.result, status='succeeded'
        # 5. 异常 → status='failed', error 记录
        # 6. WS 推送 subagent_result / subagent_error
```

关键点：

- **事件前缀**：子代理的 ReactLoop 事件（thinking/tool_call/…）推为  
  `{"type":"subagent_event","subagent_id":N,"event_type":...,"payload":...}`，  
  与主会话事件流隔离；前端子任务卡片只展示 final/error + 精简过程。
- **取消传播**：父会话 cancel 时，取消所有运行中子代理 task  
  （复用 `_session_tasks` 扩展为包含子代理 task，或独立 `_subagent_tasks`）。
- **上下文隔离**：子代理 build_messages 只含子 session 内容；主会话  
  只看到子代理的 `result`（由 delegate 工具返回值带回）。

### 3.3 监听/心跳：主对话如何确保子代理没有挂掉

**要解决的问题**：delegate handler 阻塞等待全部子代理完成，若子代理  
挂掉（异常退出、LLM 调用无限挂起、工具死锁、进程崩溃），主循环会  
**无限等待 → 主对话永久卡死**。必须有"子代理活着吗"的可观测信号 +  
"确认挂掉"后的处置路径。

**五个核心机制**：心跳上报 → 超时判定 → 分级处置 → 硬总时长兜底 →  
崩溃清理。

#### (a) 心跳上报（子代理侧）

SubagentRunner 内部**独立起一个心跳 task**，与执行 task 分离：

<span style="background-color:rgb(243, 245, 247)">进入 M2 开发</span>

**关键决策 1：心跳 task 与执行分离** —— 即使模型调用阻塞 60s / 工具卡住，  
心跳 task 仍在刷新 `last_heartbeat_at`。"慢但活着"不会误判为挂掉。

**关键决策 2：心跳故障可观测** —— 心跳协程自身挂掉与"子代理卡死"是  
两个不同故障。心跳异常必须打 ERROR 日志（subagent_id + trace_id）并  
埋点 `subagent.heartbeat_task_failure`，供监控区分：  
"心跳协程坏了但业务正常"（业务不应被误杀） vs "业务真的卡死"  
（业务无心跳 + 执行无进展）。**心跳静默降级 = 排查黑洞，禁止。**

#### (b) 超时判定（主对话侧 watchdog）

delegate handler **不用裸 `await asyncio.wait(tasks)`**，改为轮询式等待 +  
心跳扫描：

```python
# delegate handler 主循环
while running_tasks:
    done, running_tasks = await asyncio.wait(
        running_tasks, timeout=cfg.heartbeat_poll_sec)   # 默认 5s
    # 已完成的子代理: 收集结果
    # 心跳扫描(条件更新保证幂等, 见关键决策 3)
    stale = await _scan_stale_subagents(conn, session_id,
                                        timeout=cfg.heartbeat_timeout_sec)
    for s in stale:
        await _mark_stalled(s)   # 原子置 stalled_at(WHERE status='running')
        await event_sink({"type": "subagent_stalled", "subagent_id": s.id})
    # grace 已耗尽仍未恢复 → kill(关键决策 4: cancel + 等待窗口)
    for s in await _grace_expired(stale, grace_sec):    # 从 stalled_at 起算
        await _kill_stale(s)
        running_tasks -= {s.task}
```

判定规则（**计时语义明确，写入 spec**）：

```
1) now(UTC) - last_heartbeat_at > heartbeat_timeout_sec(90s)
   → 置 status 保持 running + stalled_at=now(UTC), 推送 subagent_stalled;
   → grace 宽限窗口从 stalled_at 这一刻开始计时(不是从最后一次心跳起算);
2) now(UTC) - stalled_at > grace_sec(30s) 且仍无新心跳
   → 执行 cancel + 标记 failed(heartbeat_timeout)。
```

`heartbeat_timeout_sec` 默认 **90s**（必须大于单次模型调用/工具的最大  
耗时上限，避免"正常长调用"被误杀）。

**关键决策 3：状态流转原子条件更新（幂等，支持多实例）** —— 所有  
watchdog 对子代理 DB 状态的修改必须带 `WHERE status='running'` 条件：

```sql
UPDATE subagents
SET status='failed', error='heartbeat_timeout', finished_at=now()
WHERE id = $1 AND status = 'running';
```

返回受影响行数：**0 行说明已被其他 worker 处理，直接跳过**（不再  
重复推 WS 事件、重复 cancel、重复写状态）。用条件更新实现简单幂等，  
M2 单实例已足够安全，**不引入分布式锁**（避免过度设计）。

#### (c) 分级处置（stale → grace → kill）

| 阶段                               | 动作                                                                           | 目的                 |
| -------------------------------- | ---------------------------------------------------------------------------- | ------------------ |
| 1. stale 首次检出                    | 原子置 `stalled_at`(WHERE status='running') + WS 推 `subagent_stalled`，前端卡片转黄色警示 | 可见性：用户知道有子代理"疑似挂起" |
| 2. grace 窗口（30s，自 stalled_at 起算） | 不立即杀，等待最后一次心跳                                                                | 覆盖 LLM 偶发慢响应/网络抖动  |
| 3. grace 后仍无心跳                   | cancel + 等待窗口(5s) + `failed(heartbeat_timeout)`（关键决策 4）                      | 释放主循环，失败结果照常回主模型   |

**关键决策 4：cancel 无法终止任务的边界（M2 必做，asyncio 底层限制）**：

`task.cancel()` 只设置取消标记，必须协程走到下一个 await 挂起点才抛出  
`CancelledError`。若子代理卡在**同步阻塞调用 / 纯 CPU 密集循环 / 第三方  
库无 await 的阻塞 IO**，cancel 后任务不会结束、后台继续跑，造成：  
DB 已标 failed 但内存 task 僵尸残留，持续烧 token、占连接池；watchdog  
下一轮仍读到旧 task，重复 cancel，资源泄漏。

处置协议：

```python
async def _kill_stale(s):
    s.task.cancel()
    try:
        await asyncio.wait_for(s.task, timeout=5.0)   # 等待窗口
    except asyncio.TimeoutError:
        # 任务拒绝终止(zombie): 打 Error 日志 + 埋点, 业务按失败处理
        logger.error(
            "zombie_task_detected: subagent_id=%s 无法终止(同步阻塞协程), "
            "DB 已置 failed(heartbeat_timeout), 资源泄漏由观测系统告警",
            s.subagent_id)
        metrics.inc("subagent.zombie_task_detected",
                    {"subagent_id": s.subagent_id})
    except asyncio.CancelledError:
        pass   # 正常终止
    # DB 状态无论如何置 failed(业务层按失败处理, 不等内存 task 真正退出)
```

**asyncio 取消限制说明（spec 显式记录，勿假设 cancel 一定杀掉任务）**：  
调用 `task.cancel()` 无法强制终止处于同步阻塞 / 纯 CPU 计算的协程。  
系统执行 cancel 并等待 5s 窗口期；若任务仍未退出，DB 状态标记为失败、  
结果向上返回，但内存可能残留僵尸 task。**观测系统须监控  
`zombie_task_detected` 指标告警**，用于后续排查根因（M4 做内存 dump  
辅助）。这是 Python asyncio 的底层约束，不是实现缺陷。

**挂掉后的失败结果**与正常失败完全同路径：主模型收到该子任务的  
失败文本，可自行决定重试/换策略/降级——**不中断整轮委派**。

#### (d) 硬总时长兜底（M2 必做，防御心跳 bug）

心跳机制的正常运转依赖心跳协程本身。极端场景：心跳逻辑 bug 导致  
心跳一直刷新成功，但子代理进入死循环无限执行 —— 心跳永不超时，  
子代理永远跑。**必须增加子代理总生命周期硬上限**：

```
配置: tools.subagent.max_total_lifetime_sec (M2 默认 300s)
判定: now(UTC) - started_at > max_total_lifetime_sec
处置: 无论心跳是否正常, 到达上限强制置 failed(max_lifetime_exceeded)
      (同样走 WHERE status='running' 原子更新 + cancel + 5s 等待窗口)
```

`started_at` 在 status 置 running 时写入（§3.1）。

#### (e) 进程级崩溃兜底

backend 整体重启后，子 session + messages + checkpoint 均已落库；启动时  
扫描 `status='running'` 且心跳过期的残留子代理，统一原子更新为  
`failed(error='heartbeat_timeout_after_restart', finished_at=now())`  
（同样 `WHERE status='running'` 幂等）。主会话续聊时可见子任务失败原因，  
不会出现"永远 running"的僵尸行。

#### (f) 配置项（`tools.subagent` 下）

```yaml
tools:
  subagent:
    heartbeat_interval_sec: 10   # 心跳周期
    heartbeat_timeout_sec: 90    # 无心跳判 stale 阈值(> 单次模型/工具调用上限)
    heartbeat_poll_sec: 5        # watchdog 轮询间隔
    grace_sec: 30                # stale 后宽限窗口(自 stalled_at 起算)
    max_total_lifetime_sec: 300  # M2 新增: 子代理最大总生命周期硬上限(防御心跳 bug)
    max_restarts: 0              # 自动重启次数上限(M4, 默认关)
    cancel_wait_sec: 5           # cancel 后等待任务退出的窗口期(zombie 判定)
```

### 3.4 协议面：WS 事件 + 前端子任务卡片

```
WS 推送(父会话):
  {"type":"subagent_start",      "subagent_id":N, "task_id":"t1", "prompt":...}
  {"type":"subagent_heartbeat",  "subagent_id":N, "phase":"thinking|tool_exec|idle", "iteration":k}
  {"type":"subagent_event",      "subagent_id":N, "event_type":"tool_call", "tool_name":...}
  {"type":"subagent_stalled",    "subagent_id":N, "stale_sec":92}
  {"type":"subagent_result",     "subagent_id":N, "status":"succeeded", "result":...}
  {"type":"subagent_error",      "subagent_id":N, "error":"heartbeat_timeout"}
```

前端：对话流内渲染"🧩 子任务卡片"（参考现有任务状态抽屉 TaskPanel 样式）：  
任务 id、状态徽标（运行中/成功/失败/**停滞**）、**"最后心跳 Ns 前"** 计时  
（每收到 heartbeat 刷新）、展开后显示工具调用序列与最终结果；`stalled`  
时卡片转黄色警示并提示"等待宽限中…"。

**WS 推送可靠性（前端兜底）**：`subagent_heartbeat` / `subagent_stalled`  
是 WS 事件，WS 断开会丢消息——**但 watchdog 判定依赖 DB  
（last_heartbeat_at / stalled_at / status），不依赖 WS**，这点是好的。  
前端需兼容：子任务卡片数据**以轮询 DB 兜底**（新增  
`GET /admin/subagents?session_id=&parent_turn=` 返回 status /  
last_heartbeat_at / stalled_at / error），WS 事件仅用于即时刷新；  
重连后从 DB 全量重建卡片，不完全依赖 WS 事件流。

### 3.5 主代理侧：`delegate_subtask` 工具

```python
TOOL_SCHEMA = {
    "name": "delegate_subtask",
    "description": (
        "将 1~3 个相互独立的子任务并行委派给子代理执行(各自独立上下文)。"
        "适合: 并行调研/多文件独立修改/多路检索。返回每个子任务的结果文本,"
        "必须明确子任务边界(输入给什么、要求输出什么、长度上限)。"
        "嵌套深度上限 2, 每轮最多 3 个并行。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "subtasks": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string", "description": "子任务标识"},
                        "prompt": {"type": "string", "description": "自包含的委派指令"}
                    },
                    "required": ["id", "prompt"]
                }
            }
        },
        "required": ["subtasks"]
    }
}
```

handler 流程：建行 → spawn runners → **轮询式等待全部完成 + 心跳扫描**  
（§3.3(b)：`asyncio.wait(tasks, timeout=poll)` 循环 + stale 判定与处置，  
绝不用裸 `await asyncio.wait(tasks)` 以免挂死）→ 聚合结果字符串返回。  
主循环等待期间用独立 task 运行 runners（不阻塞 WS 消息接收）。

> 注：delegate 工具 handler 是**阻塞式**（等子代理全部完成才返回），  
> 与现有工具语义一致（工具执行期间主循环挂起）；并行发生在 runner 层。  
> 工具超时类别 `tools.timeout.categories.delegate_subtask` 建议 300s，  
> 且仅作为兜底上限 —— 子代理心跳机制保证挂掉时提前释放，不等满 300s。

## 4. 里程碑拆分（每步可独立交付）

| 里程碑          | 内容                                                                                                                                                                         | 验收                                       |
| ------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------- |
| M1 存储+工具     | subagents 表 + migration + `delegate_subtask` 工具定义（单子任务，串行执行）                                                                                                               | 主模型可委派一个子任务并收到结果                         |
| M2 并行执行+监听闭环 | SubagentRunner + Semaphore(3) + 独立子 session + 取消传播 + **心跳闭环全部 M2 必做项**（§3.3 a~f：独立心跳 task + 心跳故障日志/埋点 + 轮询式 wait + stale/grace/kill + 原子条件更新 + cancel 等待窗口(zombie) + 硬总时长） | 见下方 M2 验收清单                              |
| M3 协议+前端     | WS subagent\_* 事件（含 heartbeat/stalled）+ 子任务卡片（心跳计时 + 停滞警示）+ **GET /admin/subagents DB 轮询兜底**                                                                               | 对话流内可视化子任务状态/结果；停滞黄警示；WS 断开重连后卡片从 DB 重建  |
| M4 加固        | 分布式 watchdog 选主（多实例场景）、max_restarts 自动重启 + side-effect 工具幂等、zombie 监控告警 + 内存 dump 辅助、基于线上指标自动调参                                                                            | 长任务中断恢复后子代理状态一致；无"永远 running"残留；多实例无重复处理 |

**M2 验收清单（M2 必须全部满足）**：

1. 原验收：人为 kill 子代理**执行 task**（模拟真实卡死），90s+grace 窗口后  
   DB 状态变 failed(heartbeat_timeout)，主对话收到失败结果，主对话不阻塞。
2. **AC-1 心跳协程故障可观测**：仅 kill 心跳 task，业务 task 继续运行。  
   观测：日志打出 `heartbeat_task_failure`、指标 `subagent.heartbeat_task_failure`  
   计数，**业务 task 不被误杀**（执行正常但心跳停止 → 90s 后照常触发  
   stale 逻辑，且通过日志能区分"心跳协程挂了"而非"业务卡死"）。
3. **AC-2 硬总时长兜底**：子代理进入死循环但心跳持续正常上报 → 到达  
   `max_total_lifetime_sec`(300s) 后被强制终止，  
   status='failed', error='max_lifetime_exceeded'。
4. **AC-3 原子幂等**：并发/重复扫描同一 running 记录，条件更新  
   （WHERE status='running'）只成功一次，不重复推 stalled 事件、不重复  
   写终态。
5. **AC-4 cancel 拒绝终止边界**：子代理卡在纯同步阻塞（模拟），cancel 后  
   5s 窗口未退出 → 日志打 `zombie_task_detected` + 埋点，DB 仍置  
   failed(heartbeat_timeout)（业务不阻塞，资源泄漏留给观测）。

## 5. 风险与缓解

| #   | 风险                                                                                                                               | 缓解（M2 必做，标注 M4 的延后）                                                                                                                                                                                       |
| --- | -------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| R1  | **task.cancel() 不保证终止任务**：卡在同步阻塞/纯 CPU/无 await 阻塞 IO 的协程，cancel 后不退出，DB 标 failed 但内存 task 僵尸残留，持续烧 token、占连接池；watchdog 重复 cancel | §3.3c 关键决策 4：cancel 后 `await asyncio.wait_for(task, 5s)` 等待窗口；超时打 ERROR `zombie_task_detected` + 埋点；**DB 无论如何置 failed，业务按失败处理**；spec 显式记录 asyncio 取消限制（底层约束，不假装 cancel 一定杀掉）；僵尸资源泄漏监控告警 → M4（含内存 dump 辅助） |
| R2  | **心跳 task 自身故障**：心跳协程挂掉后 last_heartbeat_at 不再更新，业务正常但被误判 stale；静默降级无日志，线上无法区分"业务卡死" vs "心跳协程坏"                                   | §3.3a 关键决策 2：心跳异常打 ERROR（subagent_id + trace_id）+ 埋点 `subagent.heartbeat_task_failure`；"心跳故障 ≠ 业务卡死"通过日志/指标区分（M2 必做）                                                                                      |
| R3  | **时间戳时区不一致**：混用 DB 时间与应用本地时间，多实例时钟偏移 → 超时误判                                                                                      | §3.1 硬约束：所有 `*_at` 字段统一 UTC（应用 `datetime.now(timezone.utc)` 或 DB `now()`），禁止机器本地时区                                                                                                                        |
| R4  | **watchdog 竞争/幂等**：多实例同时扫描同一 running 记录 → 重复 WS 事件/重复 cancel/重复写终态；状态流转缺少状态机约束（已 failed 仍被重复处理）                                  | §3.3b 关键决策 3：所有状态变更 `UPDATE ... WHERE status='running'` 条件更新，受影响行数 0 则跳过（简单幂等，不引入分布式锁）；M2 单实例已安全；多实例 watchdog 选主 → M4                                                                                     |
| R5  | **grace 计时起点模糊**：stale 后 30s 从"检出时刻"还是"最后心跳+90+30"起算不清                                                                           | §3.3b 判定规则写入 spec：置 `stalled_at`（检出时刻，条件更新）+ WS 警示；grace 从 `stalled_at` 起算；`now - stalled_at > grace_sec` 且无新心跳才 kill                                                                                     |
| R6  | **缺全局硬最大时长**：心跳 bug 一直刷成功但子代理死循环 → 心跳永不超时、永远跑                                                                                    | §3.3d：`max_total_lifetime_sec`(300s) 硬上限，`now - started_at > 上限` 强制 failed(max_lifetime_exceeded)，与心跳无关（M2 必做）                                                                                            |
| R7  | **WS 推送不可靠**：heartbeat/stalled 丢消息，前端收不到黄警示                                                                                      | watchdog 判定依赖 DB（不依赖 WS）；前端子任务卡片以 `GET /admin/subagents` 轮询兜底，WS 仅即时刷新（§3.4）                                                                                                                              |
| R8  | **M2 验收只覆盖"杀执行 task"**，未覆盖心跳协程故障与死循环场景                                                                                           | M2 验收清单 AC-1（杀心跳不误杀业务）/ AC-2（死循环+心跳正常 → 硬时长强制终止）/ AC-3（原子幂等）/ AC-4（cancel 拒绝终止 zombie）                                                                                                                    |
| R9  | 子 session 污染历史列表                                                                                                                 | sessions.kind 列（main/sub），list_sessions 过滤 sub                                                                                                                                                            |
| R10 | 成本失控（并行烧 token）                                                                                                                  | 每轮上限 3、每子代理 max_iterations 沿用父配置、硬总时长兜底、计费归属子 session                                                                                                                                                     |
| R11 | 主循环等待期间无法暂停/取消                                                                                                                   | 复用 pause_controller + 子代理 task 注册进 \_session_tasks 取消传播                                                                                                                                                   |
| R12 | 结果聚合超长                                                                                                                           | delegate 返回值截断（复用 injection_guard.truncate_tool_result）                                                                                                                                                   |
| R13 | 子代理幻觉独立 ctx（看不到主对话历史）                                                                                                            | 委派 prompt 由主模型生成，工具描述强制要求"自包含"                                                                                                                                                                            |

## 6. 打开问题（动工前需确认）

1. 子代理是否继承父会话的 `workspace`（画地为牢目录）？→ 建议继承。
2. 子代理结果是否写入父会话消息历史？→ 建议仅存 subagents.result，  
   由主模型决定是否引用（避免重复落盘）。
3. 子代理是否可用 MCP 工具？→ 建议默认同父会话全量工具（含 MCP）。
4. sessions.kind 列 vs list_sessions 子查询过滤？→ 建议 kind 列（清晰、可索引）。
5. 指标埋点落点：`subagent.heartbeat_task_failure` / `zombie_task_detected`  
   走项目 `observability` 现有机制（日志/事件），是否接外部 metrics  
   （prometheus 等）？→ M2 先以 ERROR 日志 + react_events 事件落地，  
   外部指标按需接入。
6. 心跳参数校准：90s timeout / 300s 硬时长默认值，M2 落地后用真实模型  
   调用时长实测校准；自动调参辅助工具 → M4。

## 6.1 评审记录（2026-08-05）

- 用户评审提出 8 项风险（R1~R8，见 §5），全部吸收：
  - M2 必做：cancel 无法终止的 zombie 边界（§3.3c 决策 4 + AC-4）、  
    心跳协程故障可观测（§3.3a 决策 2 + AC-1）、时间戳统一 UTC（§3.1）、  
    状态原子条件更新幂等（§3.3b 决策 3 + AC-3）、grace 计时语义明确  
    （§3.3b 判定规则）、硬总时长兜底（§3.3d + AC-2）、前端 DB 轮询兜底  
    （§3.4）。
  - M4 延后：分布式 watchdog 选主、max_restarts 完整逻辑 + side-effect  
    工具幂等、zombie 监控告警 + 内存 dump、自动调参工具。
- 结论：**方案可行，可进入 M2 开发**。最大风险为 Python asyncio 协程  
  取消语义限制（R1），spec 已显式记录该 limitation，不假设 cancel  
  一定杀掉任务。

## 6.2 M2 实施记录（2026-08-05，落地）

**实施清单**（M1 地基一并补齐，因 M1 未单独落地）：
- 数据面：`schema.sql` 第 14 张表 `subagents`（§3.1 完整 DDL）+ `sessions.kind`  
  列（main/sub，R9）；`migrations.py` 幂等补丁（老部署补列 + CREATE TABLE  
  IF NOT EXISTS）
- 配置：`config.yaml tools.subagent` 全量参数（§3.3(f)）+  
  `tools.timeout.categories.delegate_subtask: 300`
- 执行面：`core/subagent.py`（~550 行）：
  - `SubagentRunner`：独立子 session（kind='sub'，继承 model/skill/workspace/  
    permission_mode）+ 复用 ReactLoop（零改动）+ 独立心跳 task（§3.3a 决策 1）  
    + final/error/tool_call 捕获 + 终态条件更新（WHERE status='running'）
  - watchdog 模块函数：`scan_and_mark_stalled` / `grace_expired_ids` /  
    `lifetime_exceeded_ids` / `kill_tasks`（zombie 检测，§3.3c 决策 4） /  
    `cleanup_zombies_on_startup`（§3.3e）
- 工具：`tools/builtins/delegate_subtask.py` —— `build_delegate_subtask_tool`  
  闭包注入（conn/cfg/session_id/event_sink/tools），**无模块级全局串扰**  
  （区别于 code_execution 的 set_* 模式）；校验 1~3 + 嵌套深度；建行 +  
  WS subagent_start；并行 spawn；轮询式等待（绝不用裸 await wait）+  
  心跳扫描；CancelledError 级联取消 + DB 批量置 cancelled
- 集成：`main.py` 构建 ReactLoop 前附加 delegate 工具（**不进 frozen hash**，  
  子代理 tools 不含 delegate → 嵌套深度恒 1）；`_on_startup` 僵尸清理；  
  `admin.py list_sessions` 过滤 kind='sub'

**M2 关键实现决策**（spec 补充）：
- 闭包注入 vs 模块级全局：delegate handler 需要 session/conn/event_sink 等  
  上下文，模块级全局多会话并发会串扰 → 闭包注入（每会话构建 ToolDef）
- 子代理工具列表 = 父 tools（不含 delegate，因 delegate 由 main 附加且  
  闭包捕获的是附加前的旧列表）→ 嵌套深度恒 1（< max_nesting_depth=2）
- 子代理 permission_manager=None（委派即授权，权限确认留主会话）；  
  memory_manager=None（不污染用户记忆）；不挂 pause_controller/hook_runner
- 心跳故障埋点：M2 以 ERROR 日志（`subagent.heartbeat_task_failure` /  
  `zombie_task_detected` 标识）落地，react_events 入库扩容 CHECK → M3
- asyncpg 陷阱：`execute()` 返回 "UPDATE N" 状态串，`== 0` 比较恒 False  
  → 统一 `_rowcount()` 解析（曾致 pending→running 判定失效）

**验收结果**（`tests/test_subagent.py` 12 项全过，含 3 次稳定性复跑；
全量回归 1172 passed / 6 failed —— 6 个失败均为沙箱 safe-delete 拦截删除
操作与 WinError 64 网络盘环境问题，与本次改动无关，零回归）：
- 原验收 ✓ 心跳停 → stale(stalled_at 置位 + subagent_stalled) → grace 耗尽  
  → failed(heartbeat_timeout) + runner 终止 + 主对话不阻塞（失败结果回主模型）
- AC-1 ✓ 仅 kill 心跳 task → heartbeat_task_failure ERROR 日志 + 业务照常 succeeded
- AC-2 ✓ 死循环 + 心跳正常 → max_total_lifetime_sec 强制 failed(max_lifetime_exceeded)
- AC-3 ✓ 并发条件更新只成功一次（两连接并发 scan/grace 均幂等）
- AC-4 ✓ cancel 拒绝终止 → zombie_task_detected ERROR 日志
- 附加 ✓ 成功路径（子 session + 结果落库）、delegate 校验（空/超 3/缺字段/嵌套）、  
  双任务并行聚合、list_sessions 过滤 sub、启动僵尸清理幂等

**已知边界**（M3/M4 承接）：
- 前端子任务卡片 + WS heartbeat/stalled 即时刷新 + GET /admin/subagents 轮询兜底 → M3
- 嵌套深度 >1（子代理再委派）→ M3/M4（当前恒 1）
- 分布式 watchdog 选主、max_restarts、zombie 监控告警 → M4

## 6.3 M3 协议+前端 实施记录（2026-08-06）

**后端**：
- `GET /admin/subagents?session_id=&parent_turn=`（admin.py）：DB 轮询兜底
  （R7），返回 status/last_heartbeat_at/stalled_at/error/result/tool_calls/
  prompt/parent_task/sub_session_id；parent_turn 可选过滤
- WS `subagent_heartbeat`（core/subagent.py）：心跳循环每次刷新 DB 后推送
  （含 phase：thinking/tool_exec/idle，由 ReactLoop 事件流推断），
  前端"最后心跳 Ns 前"计时刷新

**前端**（renderer）：
- `components/SubagentPanel.tsx`（新）：🧩 子任务卡片面板 —— 状态徽标
  （运行中/成功/失败/取消/**停滞黄警示**）、"最后心跳 Ns 前"本地计时
  （1s interval）、展开显示指令/工具调用序列/最终结果/错误、"清除已完成"
- `App.tsx`：`subagents` state + handleMessage 6 个 subagent_* case
  （start/heartbeat/event/stalled/result/error）+ 重连 ws.onopen 与切会话后
  `fetchSubagents()` 从 DB 全量重建（WS 仅即时刷新，R7）+ 消息流底部渲染面板

**验收**：对话流内可视化子任务状态/结果 ✓；停滞黄警示 ✓；WS 断开重连后
卡片从 DB 重建 ✓（fetchSubagents）；tsc 通过 + 前端 13 测试全过

## 6.4 M4 加固 实施记录（2026-08-06）

按**单实例桌面应用**裁剪（避免过度设计）：
- **react_events 埋点入库**（ADR §6 问题 5 落地）：event_type 扩容
  `'subagent'`（schema.sql + migrations 幂等 + react_events.py），
  payload.kind 细分 stalled / killed / max_lifetime_exceeded /
  heartbeat_task_failure / zombie_task_detected / restart —— watchdog 与
  runner 在关键节点写父会话 react_events，管理端/日志可观测闭环
- **max_restarts 自动重启**（默认 0 关闭）：业务异常(非取消)且重启次数
  未达上限 → 同一子 session 续跑(新 turn, 复用已建 session)；restart_attempts
  落库 + restart 埋点；**副作用工具可能重复执行 —— 默认关闭, 开启为用户
  显式选择**(spec 记录限制)
- **不实施项**(记录理由)：分布式 watchdog 选主 —— 单实例下原子条件更新
  (WHERE status='running') 已保证幂等(AC-3)，多实例部署不存在(桌面应用)；
  自动调参工具 —— 参数已在 config 可调，单用户无线上指标池

**验收**：无"永远 running"残留 ✓（cleanup_zombies_on_startup + 原子终态）；
单实例无重复处理 ✓（AC-3 幂等）；zombie 监控告警 ✓（zombie_task_detected
ERROR 日志 + react_events 埋点）

**测试**（`tests/test_subagent_m34.py` 6 项全过 + M2 12 项无回归）：
GET /admin/subagents 序列化/过滤/422；heartbeat WS 事件(phase)；max_restarts
重试成功(restart_attempts=1 + restart 埋点)/耗尽/默认关；watchdog stalled+
killed 埋点入库

## 7. 关联文档

- `docs/agent-vs-skill.md`（项-3）：子代理 ≠ Skill 的权威界定
- `docs/next-phase-plan-2026-08-05.md`：V1.5 总计划（项-1 为独立里程碑）
- `core/checkpoint.py`：子代理断点恢复复用主会话机制
