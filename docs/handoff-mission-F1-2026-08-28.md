# F1 批交接文档（GLM-5.3-FLASH 阶段完成 → DeepSeek D 批开工包）

> 日期：2026-08-28
> 执行：WorkBuddy 本对话（GLM-5.3-FLASH 阶段一）
> 设计文档：`docs/next-phase-plan-2026-08-28-wuya-long-task-orchestration.md`（v4）
> 状态：F1 批 9/9 任务完成并独立提交，待全量基线绿后正式移交 D 批（DeepSeek）

---

## 一、F1 批 commit 清单

| commit | 任务 | 内容一句话 |
|---|---|---|
| `e5546bf` | F1-1 | async_tasks→missions 表迁移（幂等三分支 + state CHECK 枚举） |
| `32ad548` | F1-2 | mission 工具集 create/status/control（闭包注入；state 补 paused；runner_factory TODO 接线点） |
| `8efa5ad` | F1-7 | 改道预算逻辑层 BudgetLedger（计数/超限/消费/批准状态机；mission_status 已复用） |
| `9721efe` | F1-9 | 监督间隔自适应纯函数（类型分级 + 自校正 ×1.5/×0.7 + journal 工厂） |
| `74ab93e` | F1-8 | mission_report 消息隔离（messages.msg_kind + reload_from_db 过滤 + append_mission_report helper） |
| `a0d0743` | F1-4 | EnvironmentProfile（config environment 节 + 注入片段工厂） |
| `b873acb` | F1-5 | install_preflight 预检守卫（域名快测 + 内存水位 + 镜像建议） |
| `fc76c1f` | F1-6 | 子代理工具白名单装配 + MissionRegistry 并发注册表（wait_for 3.10 兼容） |
| `（本次）` | F1-3 | 前端 MissionPanel + 5 类 WS 事件接线（schema 先行契约在 MissionPanel.tsx 头注释） |

先导 commit：设计文档 v4（`docs(design)`）；另有一批 **0.5.1 在途改动未提交**
（admin.py/embedding_service/factory/SettingsView + 8 测试文件，见 `git status`），
属 8-27 收尾工作，D 批开工前由蒋先生决定提交或保留。

## 二、TODO 接线点精确清单（D 批第一动作按单消项）

| # | 接线点 | 位置 | 期望行为 |
|---|---|---|---|
| W1 | mission_create spawn | `tools/builtins/mission_tools.py` `build_mission_tools(runner_factory=...)`；main.py 构建处当前**未装配** mission 工具 | D-1：main.py 构建 ReactLoop 时附加 mission 工具（模式同 delegate，不进 frozen hash），注入 `runner_factory=lambda mid: MissionRunner.spawn(...)` |
| W2 | EnvironmentProfile 注入 | `core/environment_profile.py` `build_environment_fragment(cfg)` | D-1：mission_create 轮（装配 mission 工具的会话）system prompt 附加该片段（缓存一次构建） |
| W3 | install_preflight 装配 | `tools/builtins/install_preflight.py` `build_install_preflight_tool(cfg)` | D-1：与 mission 工具同批装配；D-3 可在 code_execution 装依赖路径编程调用 `preflight_check()` |
| W4 | 白名单装配 | `core/mission_assembly.py` `filter_tools_for_task_type(tools, task_type)` | D-1：SubagentRunner 由 mission 派发时按子任务 task_type 过滤工具集（普通 delegate 路径不变） |
| W5 | MissionRegistry acquire/release | `core/mission_assembly.py` `mission_registry`（默认 2，config `tools.mission.max_running` 可调） | D-1：spawn 时 `await acquire(30)`（False→拒绝/排队），终态 `release()` + `notify()` |
| W6 | 间隔自校正挂载 | `core/supervision_interval.py` `initial_interval()` / `adjust_interval()` / `make_journal_entry()` | D-2：监督轮初始化用 initial_interval（或 mission_lessons p50，样本<3 回退）；每轮监督后 adjust_interval，reason 非 None → journal 追加 + WS mission_journal |
| W7 | BudgetLedger 判定 | `core/mission_budget.py` `can_fallback/consume_fallback/approve_fallback` | D-3：redelegate 前 consume_fallback（False→置 escalated）；approve_fallback 已在 mission_control 落数据面 |
| W8 | mission_report 推送 | `tools/builtins/mission_tools.py` `append_mission_report(conn, session_id, mission_id, content)` | D-2/D-3：监督轮关键节点调用（前端已按 msg_kind='mission_report' 过滤上下文，独立查询渲染待 D 批前端接线） |
| W9 | WS mission_* 事件推送 | schema 契约：`frontend/renderer/components/MissionPanel.tsx` 头注释（mission_created/update/journal/escalated/done 五类，含字段） | D-1/D-2：后端按契约在 mission 生命周期节点推 WS（带 session_id）；另实现 `GET /admin/missions?session_id=` 轮询兜底（同 R7 模式） |
| W10 | 前端裁决回调 | `frontend/renderer/App.tsx` MissionPanel `onApproveFallback/onAbort` 当前为 console.info 占位 | D-3：改发 WS 请求调用 mission_control（后端需新增对应 WS 消息类型或复用工具执行路径） |
| W11 | admin.py 注释 | `api/admin.py` L5252 "不引入 async_tasks 空表" → 改 "missions" | 0.5.1 在途批提交后顺手补（一行） |

## 三、测试基线（F1 批结束时点）

- F1 新增测试文件 7 个：
  - `test_missions_migration.py`（5 测：迁移三分支/幂等/CHECK 枚举/命名约束）
  - `test_mission_tools.py`（18 测：工具 handler 全分支，mock conn）
  - `test_mission_budget.py`（14 测：预算状态机）
  - `test_supervision_interval.py`（20 测：间隔全分支）
  - `test_mission_report_isolation.py`（3 测：隔离代码级验证，真实 DB）
  - `test_environment_profile.py`（8 测：档案合并/片段快照）
  - `test_install_preflight.py`（13 测：mock 网络/psutil）
  - `test_mission_assembly.py`（11 测：白名单/注册表）
  - 前端 `MissionPanel.test.tsx`（7 测）
- 隔离测试库：`private_agent_f1test`（F1 批为避免与全量基线互踩新建；
  全量基线仍用默认 `private_agent_test`）
- 全量后端回归：跑批中，结果回填 §五
- 前端：tsc 0 错；vitest 显式 6 文件 **34 passed**（注：无参数 `npx vitest run`
  在本机静默 exit 1 且无输出，单文件/显式清单均绿——环境行为，与改动无关，
  D 批沿用显式文件清单跑法）

## 四、D 批任务索引（DeepSeek 执行，spec 见设计文档 §6.1 D 批表）

| 任务 | 依赖的 F1 产出 | 核心难点（推理密集区） |
|---|---|---|
| D-1 MissionRunner + spawn 接线 | W1~W5 | asyncio 后台任务生命周期/取消传播/重启恢复（startup 扫描复用 cleanup_zombies 模式） |
| D-2 监督轮构建 | W6/W8/W9 | 一次性 ReactLoop 构建 + 事件/定时触发调度 + 自适应间隔挂载 |
| D-3 纠偏动作执行器 | W7/W10 | redelegate/escalate 动作与 F1-7 预算接线 + 前端裁决 WS 化 |
| D-4 经验沉淀 | W6 | mission_lessons 表 + p50/p75 增量统计 + skill_lessons 文本教训（终态一次模型调用） |

D 批收尾：全量回归 + 真实链路验收 V1~V7 → phase-closeout-mission-<date>.md →
记忆宫殿双写 → 蒋先生手动 build-electron.bat。

## 五、全量基线结果（回填区）

- 后端 pytest（加载 .env，--ignore=test_eval_full_cycle.py）：**1756 passed / 6 failed（30m17s）**
- **6 failed 与 F1 批零相关（已用 git worktree 在 F1 前 commit 46f1eb6 复跑判定：
  同样 6 failed，存量）**——test_pause_turn ×1、test_react_loop limit ×2、
  test_react_loop_billing ×2、test_react_loop_parallel ×1；疑似 0.5.1 在途
  react_loop 测试批次遗留/环境性问题，**归 0.5.1 在途批处置，不阻塞 F1 移交**
  （F1 触碰文件 schema/migrations/context_manager/mission_tools 等均无 react_loop
  逻辑改动；F1 相关测试文件 9 个全绿）
- 备注：后台首跑（8-28 早）因 F1 改动进行中作废；本表以 F1 全部 commit 后的
  重跑为准。
