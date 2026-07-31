# m1-react-loop Spec

> Status: ALIGNED
> Author: user
> Last updated: 2026-07-31

## Background

M0 已完成(commit f1bbaeb,60 tests green):四层骨架 + 进程模型 + HTTP/WS 协议 + Postgres 全表 + 磁盘告警函数 + TTL 清理函数 + ws_offset replay 函数 + 配置分层 + AES-256-GCM + 结构化日志。M0 留下 5 项闭环缺口(磁盘告警 HTTP/WS、TTL 调度、ws_offset ACK/权威、messages 归档→M2/M3)。

M1 本次执行单元聚焦"编排核心可跑通":ReAct 循环 + 三家模型适配 + 上下文管理器三区构建 + M0 闭环缺口 1-4 收尾。完成后即可进入 M1-b(step 10-12:hash/压缩/注入/计费/异常 checkpoint)。

## In scope

**step 7-9 核心(蓝图 §9.6)**:
- ReAct 核心循环 + 状态机(§2.4):IDLE/THINKING/ACTING/OBSERVING 四态正常路径 + ERROR 态仅记录 react_events
- 模型适配器(§2.7):glm / deepseek / kimi 三家 + ModelCapability 映射 + capability 降级(fallback_chain)
- asyncio 协程模型 + 流式输出(§2.6):ReAct 循环基于 asyncio,WS 流式推送 thinking/tool_call/tool_result/final 事件
- 上下文管理器(§3.1-3.2):Frozen/Stable/Active 三区分区元数据 + 启动构建 + 每轮构建(不含压缩)
- ToolDef schema(§3.8):OpenAI 2020-12 兼容 + 扩展字段,M1 内置 1-2 个 mock 工具(echo/datetime)演示 tool_call/tool_result
- 轻量自研框架边界(§2.5):react_loop 不含压缩逻辑,压缩外置到 context_manager(M1-b)

**M0 移交清单 1-4 闭环**:
- 磁盘告警 HTTP/WS 闭环:GET /admin/disk-status 返回三色级别 + WS push 告警事件
- TTL 清理调度:sidecar startup hook 注入 APScheduler + 每日 03:00 触发(react_events 30 天 + messages_archive 90 天)
- ws_offset ACK 协议:WS 加 {type:"ack", session_id, turn} 消息 + 服务端回写 config_runtime
- ws_offset 服务端权威:replay 优先读 config_runtime,客户端 last_turn 作 fallback

**前端最小验证**:
- chat UI 流式渲染:thinking/tool_call/tool_result/final 分块显示 + ws_offset 重连补发验证

## Out of scope

- step 10:hash 校验 SHA-256 + 状态栏机制 + 模板变量体系(留 M1-b)
- step 11:三类压缩策略(滑动窗口/摘要/Stable 合并)+ 注入防护 + 计费感知(留 M1-b)
- step 12:异常分类体系(模型/工具/进程/用户)+ checkpoint 存储 + 用户取消 checkpoint(留 M1-b,本次取消走 WebSocketDisconnect 粗中断)
- Agnes 适配器(蓝图 §2.7 第四家,base_url/model_name 待确认,本次跳过)
- 真实 9 类通用工具(§5.x,M2 step 16-17)
- MCP Client(§5.3-5.4,M2 step 16)
- KV Cache 分区模型 SHA-256 hash 校验完整实现(§2.8,留 M1-b step 10)
- ManualRouter 完整实现(§2.9,本次用 fallback_chain 简化)
- messages 归档(§2.10 第 3 条,会话关闭 90 天后转储,留 M2/M3)

## Assumptions

- **三家模型用 mock HTTP 为主**:dev-tdd 全程 mock(glm/deepseek/kimi 各一份 fixture),CI 不依赖真实 API Key。dev-verify 阶段用户可选跑真实 Key 集成测试,不阻断 M1 完成。
- **Agnes 跳过**:Done Criteria 2"四家均可调用"本次降级为"三家 mock 可调用 + capability 降级生效",Agnes stub 留 M1-b/M2 补。
- **ReAct 异常路径简化**:ERROR 态仅记录 react_events(error 事件),不做 checkpoint(留 step 12)。用户取消走 WebSocketDisconnect 粗粒度中断,会话标记 interrupted,不做 checkpoint 恢复。
- **三区构建不含压缩**:启动构建(Frozen=system_prompt+工具定义,Stable=空,Active=空)+ 每轮构建(Active 追加用户/助手消息)。压缩触发判断与策略留 step 11(M1-b)。
- **hash 校验预留**:三区元数据含 hash 字段(M1-b step 10 实现校验逻辑),本次只存字段不做校验。
- **WS 协议一次性设计**:ack 消息与 react_event 消息共享 WS 协议层,避免 M1-b 重构。
- **前端最小**:chat UI 只验证 WS 流式渲染 + ws_offset 重连补发,完整前端(配置面板/工具确认/ artifact 展示)留 M3。
- **数据库环境**:PostgreSQL 16 + pgvector 0.8.6 @ localhost:5432,PA_DB_PASSWORD 环境变量,PA_TEST_DSN 指向 private_agent_test。

## Solution

**模块划分**(backend/private_agent/):
- `core/react_loop.py`:ReAct 状态机 + asyncio 循环 + 流式事件产出
- `core/context_manager.py`:三区管理 + 启动构建 + 每轮构建
- `models/adapters/{glm,deepseek,kimi}.py`:三家适配器 + ModelCapability
- `models/base.py`:ModelAdapter Protocol + ModelCapability dataclass + fallback 逻辑
- `models/registry.py`:provider 注册 + ManualRouter 简化版(fallback_chain)
- `tools/defs.py`:ToolDef schema(§3.8)+ mock 工具(echo/datetime)
- `storage/disk_alert.py`:扩展 get_disk_status(组合 evaluate + get_pg_data_dir_size)
- `storage/ttl_cleanup.py`:扩展 schedule_ttl_cleanup(APScheduler 封装)
- `storage/ws_offset.py`:扩展 handle_ack(回写 config_runtime)+ replay 优先读 config_runtime
- `api/admin.py`:GET /admin/disk-status
- `api/ws.py`:WS 协议扩展(ack + react_event 流式 + disk_alert push)
- `main.py`:startup hook 注入 APScheduler + 注册新路由

**WS 协议扩展**:
- 客户端→服务端:`{type:"ack", session_id, turn}`(确认收到)、`{type:"replay", session_id, last_turn}`、`{type:"ping"}`、`{type:"user_message", session_id, content}`(新增)
- 服务端→客户端:`{type:"react_event", session_id, turn, event_type, payload}`、`{type:"ack_confirm", session_id, turn}`、`{type:"replay_end", session_id, count}`、`{type:"disk_alert", level, message}`、`{type:"error", message}`、`{type:"pong"}`

**ReAct 流程**(单轮):
1. 收到 user_message → 状态 IDLE→THINKING
2. context_manager.build_per_turn() → 拼 Frozen+Stable+Active
3. adapter.chat(messages, tools) → 流式产出 thinking → WS push react_event(thinking)
4. 若 thinking 含 tool_call → 状态 THINKING→ACTING → 执行 mock 工具 → WS push react_event(tool_call/tool_result) → 状态 ACTING→OBSERVING
5. adapter.chat(追加 tool_result) → 流式产出 final → WS push react_event(final) → 状态 OBSERVING→IDLE
6. 每步 react_events 入库,turn 递增

**TTL 调度**:
- sidecar startup 注入 APScheduler(AsyncIOScheduler)
- cron `0 3 * * *`(每日 03:00)调用 run_ttl_cleanup(react_events_retention_days=30, messages_archive_retention_days=90)
- 3GB 强制清理时收紧 react_events 保留为 7 天(复用 evaluate_disk_alert_level red 分支)

## Edge cases & risks

| Category | Notes |
|---|---|
| Boundary conditions | ReAct 循环 max_iterations 防死循环(默认 10);WS 断连时 in-flight ReAct 中断,会话标 interrupted;mock 工具同步执行(不演示超时) |
| Failure modes | 模型 mock HTTP 503 → capability 降级到 fallback_chain 下一家 + react_events 记录;三家全 fail → 返回 error 事件给客户端;APScheduler 启动失败不阻断 sidecar(告警日志);disk_alert 查询失败 → WS push error |
| Risks | ① WS 协议设计不当导致 M1-b 重构 → 本次一次性设计 ack/react_event/disk_alert 三类消息;② mock 工具与真实 ToolDef schema 不一致 → M2 接入时严格按 §3.8 schema;③三家 mock fixture 与真实 API 响应结构不符 → dev-verify 阶段可选真实 API 验证 |
| Mitigation | ReAct max_iterations 配置化;WS 协议文档化(spec 内);mock fixture 标注"仅 M1 演示用";Adapter Protocol 化,真实 API 只换实现 |

## Acceptance criteria

- **AC-1** ReAct 循环完整执行:发送 user_message → 收到 thinking→tool_call→tool_result→final 四类 react_event(顺序正确,turn 递增) → 状态回归 IDLE
- **AC-2** 三家模型 mock 适配器 capability 降级:glm mock 返回 503 → 自动切换 deepseek mock + react_events 记录 `model_fallback` 事件;三家全 fail → 返回 error 事件
- **AC-3** 三区构建:会话启动后 messages 表有 Frozen Zone(system_prompt+工具定义)+ Stable Zone(空)+ Active Zone(空)记录;每轮结束后 Active Zone 追加用户/助手消息
- **AC-4** GET /admin/disk-status 返回 `{"level":"none|yellow|orange|red", "message":"...", "size_bytes":N}`;磁盘超阈值时 WS push `{type:"disk_alert", level, message}`
- **AC-5** sidecar 启动后 APScheduler 注册 TTL 任务(cron `0 3 * * *`);手动 trigger 可执行 cleanup,返回 `{"react_events_deleted":N, "messages_archive_deleted":M}`
- **AC-6** 客户端发 `{type:"ack", session_id, turn}` → 服务端回写 config_runtime `ws_offset:{session_id}=turn` + 返回 `{type:"ack_confirm", session_id, turn}`
- **AC-7** 客户端发 `{type:"replay", session_id, last_turn=N}` → 服务端优先读 config_runtime `ws_offset:{session_id}`,取 max(config_runtime, last_turn) 作为 offset,查询 turn > offset 的事件补发
- **AC-8** 前端 chat UI 发送消息后流式渲染 thinking/tool_call/tool_result/final 四块;WS 断开重连后补发的事件按顺序渲染

## Open questions

无外部阻塞项。以下为 M1-b 衔接备忘(不阻断本次):
- Agnes provider 信息(base_url/model_name)何时确认 → M1-b 或 M2 补 stub
- 真实三家 API Key 何时可用于 dev-verify → 用户决定是否跑真实集成测试

## Core entities (ontology)

| Entity | Type | Key fields | Relationship |
|---|---|---|---|
| ReactLoop | class | state, session_id, context_manager, adapter | 1:1 Session, 1:1 ContextManager, 1:1 ModelAdapter |
| ContextManager | class | frozen_zone, stable_zone, active_zone | 1:1 Session, 含 3 Zone |
| Zone | dataclass | name, messages, hash(预留) | ContextManager 含 3 个 |
| ModelAdapter | Protocol | chat(), capability | 实现:glm/deepseek/kimi |
| ModelCapability | dataclass | streaming, function_calling, vision, json_mode | 1:1 ModelAdapter |
| ToolDef | dataclass | name, description, parameters_schema, handler | ReactLoop 调用 |
| react_events | table | id, session_id, turn, event_type, payload | N:1 Session |

## Interview metadata

- Mode: --deep
- Waves: 4
- Final ambiguity: 17.5%
- Status: PASSED

### Clarity breakdown

| Dimension | Score | Weight | Weighted |
|---|---|---|---|
| Goal | 0.85 | 0.40 | 0.34 |
| Scope | 0.80 | 0.25 | 0.20 |
| AC | 0.80 | 0.25 | 0.20 |
| Context | 0.85 | 0.10 | 0.085 |
| Ambiguity | | | 17.5% |

### Ontology convergence

- Wave 1: ReactLoop, ModelAdapter, Zone, ToolDef, react_events (5 new)
- Wave 2: + ContextManager, ModelCapability (2 new, stable)
- Wave 3: + ws_offset, config_runtime (2 stable, from M0)
- Wave 4: stable, no new entity
- Stability ratio final: 7/7 = 100%
