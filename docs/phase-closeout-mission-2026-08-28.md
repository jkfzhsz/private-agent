# Mission 层版本收尾（0.6.0，2026-08-28）

> 设计文档：`docs/next-phase-plan-2026-08-28-wuya-long-task-orchestration.md`（v4）
> F1 交接文档：`docs/handoff-mission-F1-2026-08-28.md`
> 执行：WorkBuddy 本对话（GLM-5.3-FLASH 全程；D 批原定 DeepSeek，蒋先生裁定
> FLASH 上下文足够后继续，实际全程完成）
> 状态：**D 批 4/4 完成；全量回归绿（存量 6 失败与 Mission 无关）**

---

## 一、D 批 commit 清单

| commit | 任务 | 内容 |
|---|---|---|
| `（D-1）` | D-1 | MissionRunner 后台编排器（subagent/script/wait 三执行体 + 宪章锚定委派 + watchdog 复用 + BudgetLedger + registry 释放）+ main.py 装配（W1/W2/W3 + startup 重启恢复）+ approve 重新 spawn 续跑闭环 |
| `（D-2）` | D-2 | 监督轮（失败触发单次 LLM 纠偏判定）+ runner 预算内重试循环 + W8 mission_report 落库 + GET /admin/missions 轮询兜底（W9） |
| `（D-3）` | D-3 | adjust_plan 裁决 + plan 修正重派 + W10 前端裁决 WS 化（mission_control case）+ registry 名额泄漏根因修复 |
| `（D-4）` | D-4 | mission_lessons 结构化经验（p50/p75 增量加权 + 样本<3 回退 + 30 天窗）+ skill_lessons 文本教训 + runner 终态挂载 |

## 二、D 批关键实施决策（与设计文档的差异记录）

1. **监督轮实施裁剪**：v4 设计"一次性 ReactLoop"→ 实施确认为**单次 chat
   调用**（合成摘要 → JSON 裁决）。理由：纠偏动作由 MissionRunner 确定性
   执行，监督者无工具循环需求；省多轮 token、无 frozen hash 参与、无权限面。
   判定三问（§4.3 第三道防线）写入监督 system prompt。
2. **裁决枚举扩 adjust_plan**（设计文档纠偏白名单五动作之一）：milestone_fix
   JSON 修正 plan 中对应里程碑后重派；缺 milestone_fix 保守回退 wait_user。
3. **registry 名额泄漏根因修复（D-3）**：spawn 后立即 abort → runner task
   未被调度即被 asyncio.run 取消 → 协程体未执行 → finally 的 release 不跑 →
   名额泄漏。修复：release 移至 `task.add_done_callback`（task 终结唯一可靠
   释放点），MissionRegistry.acquire 改轮询语义（0.2s，Condition.wait/notify
   需 async 上下文不兼容同步 callback）。
4. **失败处置路径**：里程碑失败 → BudgetLedger 预算判定 → 消耗一次 fallback →
   监督轮判定（redelegate/adjust_plan=重派，wait_user=escalated）→ 预算耗尽
   强制 escalated 等用户。与 §4.3 三道防线一致。
5. **重启恢复语义**：运行中 mission → escalated(interrupted_by_restart)；
   用户 approve_fallback → 重新 spawn 续跑（已完成里程碑按 plan.status 跳过）。
   自动续跑被明确否决（副作用重复风险）。

## 三、接线点消项（对照 F1 交接文档 W1~W11）

| # | 状态 |
|---|---|
| W1 spawn 接线 | ✅ main.py mission 工具装配 + runner_factory 闭包 |
| W2 EnvProfile 注入 | ✅ mission_create 工具 description 拼接环境片段（schema 缓存注入） |
| W3 install_preflight 装配 | ✅ main.py 与 mission 工具同批装配 |
| W4 白名单装配 | ✅ runner._run_subagent_milestone 调 filter_tools_for_task_type |
| W5 registry acquire/release | ✅ spawn acquire（False 拒绝）/ done_callback release |
| W6 间隔挂载 | ✅ 事件驱动（§4.4.1 一/二级纯函数就绪；定时模型监督在事件驱动裁剪下为将来扩展点，get_initial_interval 接口就绪） |
| W7 BudgetLedger | ✅ _handle_milestone_failure 消费 + WS approve_fallback 追加 |
| W8 mission_report | ✅ 纠偏/终态经 append_mission_report 落库（msg_kind 隔离） |
| W9 WS mission_* + 轮询兜底 | ✅ 五类事件推送 + GET /admin/missions |
| W10 前端裁决 WS 化 | ✅ App.tsx onApproveFallback/onAbort → WS mission_control → mission_control_result Toast |
| W11 admin.py 注释 | ⏳ 随 0.5.1 在途批（仍未提交，蒋先生定时机） |

## 四、测试基线

- Mission 层专项：**124 passed**（11 个测试文件，合跑）
  - test_missions_migration(5) / test_mission_tools(18) / test_mission_budget(14)
  - test_supervision_interval(20) / test_mission_report_isolation(3)
  - test_environment_profile(8) / test_install_preflight(13) / test_mission_assembly(11)
  - test_mission_runner(8) / test_mission_supervisor(9) / test_mission_lessons(11)
  - 前端 MissionPanel(7)
- 前端全量：tsc 0 错 + vitest 显式清单 34 passed
- 后端全量回归（D 批后）：**待回填**
- F1 存量失败 6 个（react_loop 相关，与 Mission 无关）已由 worktree 判定归 0.5.1 在途批

## 五、全量回归结果（回填区）

- 后端 pytest：**1784 passed / 6 failed（35m13s）** —— 6 失败与 F1 批后基线
  完全一致（test_pause_turn ×1 / test_react_loop limit ×2 /
  test_react_loop_billing ×2 / test_react_loop_parallel ×1，均为 0.5.1 在途
  react_loop 批次存量，worktree 判定与 Mission 零相关）；较 F1 基线
  **新增 28 测全过，零回归**。

## 六、遗留与后续

1. **0.5.1 在途改动仍未提交**（admin.py 等 8 文件 + react_loop 存量 6 失败同源，
   由蒋先生决定提交与修复时机；提交后顺手补 W11 注释）。
2. **真实链路验收 V1~V7**：依赖后端启动 + 真实 LLM 会话（走用户实际触发路径），
   AI 侧 mock/集成测试已全绿；真实验收建议蒋先生打包后执行（打包按铁律由您
   手动 build-electron.bat）。
3. 定时模型监督（§4.4.1 定时触发的 LLM 方向检查）当前为事件驱动替代——
   get_initial_interval 接口就绪，语料积累后可开启。
4. 版本号：0.6.0（新能力域）。
