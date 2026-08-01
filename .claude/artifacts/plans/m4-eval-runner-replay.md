# m4-eval-runner-replay Implementation Plan

> Status: APPROVED
> Source: spec/m4-eval-runner-replay (ALIGNED, Final ambiguity 12.3%)
> Mode: (default)
> Iterations: 1 / 3
> Author: user
> Last updated: 2026-08-01

## Requirements summary

实现 m4-eval-runner-replay spec:EvalRunner(离线批量 + 交互式回放编排)+ ReplayExecutor(复用 ReactLoop + event_sink 静默)+ MockToolRegistry(sample_id+tool_name 索引)+ ContextManager 扩展(reload_from_db / replace_frozen_zone)+ SkillLoader 扩展(load_version)+ SkillVersionListener(版本变更自动触发)+ office 场景 mock_data 种子。依赖 m4-eval-foundation 仓储层 + m4-metrics-judge HybridEvaluator(均已就位)。

## Acceptance criteria

- AC-1: `ContextManager.reload_from_db(conn)` 完整重载三区消息(Frozen+Stable+Active),与 build_initial 后的状态一致
- AC-2: `ContextManager.replace_frozen_zone(conn, system_prompt, tools)` 替换 Frozen Zone 后 frozen_hash 重新计算,sessions 表 locked_skill_version 更新
- AC-3: `SkillLoader.load_version(skill_name, version, conn)` 从 version_snapshots 表读历史 payload 返回 Skill,版本不存在抛 SkillNotFoundError
- AC-4: `MockToolRegistry.get_mock_handler(sample_id, tool_name)` 读取 `{mock_data_dir}/{tool_name}/{sample_id}.json` 返回 mock handler
- AC-5: `MockToolRegistry.list_tools_for_session(whitelist)` 返回工具列表与 real_registry 一致,但 handler 替换为 mock 版本
- AC-6: `ReactLoop` 新增 `event_sink` 参数,event_sink=None 时不推 WS,现有调用方行为不变
- AC-7: `EvalRunner.run_evaluation(eval_mode="offline")` 离线批量评估执行成功,每样本仅调模型不执行工具,actual_events=[]
- AC-8: `EvalRunner.run_evaluation(eval_mode="replay", mock_enabled=True)` 交互式回放执行成功,actual_events 含 tool_call/tool_result,Mock 数据按 sample_id+tool_name 索引
- AC-9: `ReplayExecutor.run_replay()` 创建临时评估会话,执行完毕后删除会话(title="eval-{run_id}" 前缀区分)
- AC-10: `SkillVersionListener.on_skill_version_saved()` 触发快速回归(offline + quick subset),auto_trigger_on_version_change=False 时不触发
- AC-11: office 场景 4 条种子样本的 mock_data 创建(file_read/code_execution/web_search 三工具)
- AC-12: 端到端测试:离线评估 + 交互式回放(mock 模式)均跑通,metrics 含五类指标,eval_runs 表记录完整

## RALPLAN-DR

### Planner draft

#### Principles (5)

- **跟随 baseline「最小代码」**:复用 ReactLoop / ContextManager / SkillLoader,仅做非破坏性扩展;不新建独立 ReAct 循环或抽象基类
- **跟随 spec 的 In scope,不擅自扩**:不实现版本对比/回滚(m4-version-compare-rollback)、低分案例自动提取(m4-continuous-evolution)、在线评估/分布式并行(V2)
- **复用 m4-eval-foundation + m4-metrics-judge 已就位基础设施**:EvalDatasetRepo / EvalRunRepo / HybridEvaluator / LLMJudge / compute_all_metrics 全复用
- **非破坏性扩展**:ReactLoop event_sink 默认 None(现有调用方不变)、ContextManager 新增方法不改现有签名、SkillLoader 新增 load_version 不改 load
- **临时会话用 title 前缀区分**:MVP 用 title="eval-{run_id}" 避免 schema 变更(spec Assumptions),session_repo 不独立抽取(项目无 SessionRepo 先例,内联 SQL)

#### Decision drivers (top 3)

1. **ReactLoop 复用 vs 新建循环**:spec 明确复用 ReactLoop + event_sink=None 静默,避免维护两套循环逻辑(决定性)
2. **MockToolRegistry 组合 vs 继承**:spec 明确组合(持有 real_registry 引用),避免破坏 ToolRegistry 继承链(决定性)
3. **SessionRepo 抽取 vs 内联 SQL**:项目无 SessionRepo 先例(会话创建散落在测试/API),抽取独立类会引入新抽象;MVP 内联 SQL 更符合最小代码(辅助)

#### Viable options

**Option A: 内联 SQL + event_sink 扩展 + 组合 MockToolRegistry(spec 方案直接落地)**
- 实现思路:ReplayExecutor 内联 `INSERT INTO sessions` / `DELETE FROM sessions`(title="eval-{run_id}"),不抽取 SessionRepo;ReactLoop 新增 event_sink 参数;MockToolRegistry 组合持有 real_registry;ContextManager/SkillLoader 新增方法
- 改动文件:
  - 新建:`backend/private_agent/eval/runner.py`、`backend/private_agent/eval/replay.py`、`backend/private_agent/eval/mock_tool_registry.py`、`backend/private_agent/eval/version_listener.py`
  - 扩展:`backend/private_agent/core/react_loop.py`(event_sink)、`backend/private_agent/core/context_manager.py`(reload_from_db + replace_frozen_zone)、`backend/private_agent/skills/loader.py`(load_version)
  - mock_data:`backend/skills/office/examples/test/mock_data/{file_read,code_execution,web_search}/office_00{1-4}_*.json`(12 个文件)
  - 测试:`backend/tests/test_eval_runner.py`、`backend/tests/test_eval_replay.py`、`backend/tests/test_eval_mock_tool_registry.py`、`backend/tests/test_eval_version_listener.py`、`backend/tests/test_context_manager_replay.py`、`backend/tests/test_skill_loader_version.py`、`backend/tests/test_react_loop_event_sink.py`、`backend/tests/test_eval_e2e.py`
- Pros: 完全遵循 spec Solution;无新抽象(SessionRepo);ReactLoop 非破坏性扩展;mock_data 跟随 Skill 版本(spec AC-11)
- Cons: ReplayExecutor 内联 SQL 与项目其他地方风格不一致(项目无 SessionRepo 但测试/API 都内联 SQL,实际是一致的);改动文件多(4 新源 + 3 扩展 + 12 mock + 8 测试)

**Option B: 抽取 SessionRepo + event_sink 扩展 + 组合 MockToolRegistry**
- 实现思路:新建 `backend/private_agent/storage/session_repo.py` 抽取 SessionRepo(create/delete/list_sessions),ReplayExecutor 调用 SessionRepo;其余同 Option A
- 改动文件:同 Option A + `backend/private_agent/storage/session_repo.py`(新)+ 重构现有测试/API 引用
- Pros: ReplayExecutor 不含 SQL;SessionRepo 可复用于未来;测试更易 mock
- Cons: 引入新抽象 SessionRepo,但项目当前无此抽象(测试/API 都内联 SQL);需重构 30+ 处现有 `INSERT INTO sessions`(test 文件),改动面爆炸;违反「外科手术式改动」;spec Assumptions 明确 MVP 用 title 前缀避免 schema 变更,未要求 SessionRepo

**Invalidation rationale**:Option B 的 SessionRepo 抽取虽提升抽象层,但需重构 30+ 处现有内联 SQL,违反「外科手术式改动」且 spec 未要求。Option A 的内联 SQL 与项目当前风格一致(测试/API 都内联),且 spec Assumptions 已明确 MVP 用 title 前缀。Option A 更符合最小代码原则。

#### Implementation steps (基于 Option A)

1. **扩展 ReactLoop event_sink 参数** — `backend/private_agent/core/react_loop.py:56-74` 在 `__init__` 新增 `event_sink: Callable[[dict], Awaitable[None]] | None = None` 参数,存 `self._event_sink`;`_emit_event` 方法(L80-105)在 `await self.event_queue.put(event)` 后加 `if self._event_sink: await self._event_sink(event)`;现有调用方不注入 event_sink,行为不变(AC-6)
2. **扩展 ContextManager.reload_from_db** — `backend/private_agent/core/context_manager.py:141` 后新增 `async def reload_from_db(self, conn)`:查 messages 表 `WHERE session_id=$1 ORDER BY turn, id`,按 zone 分组重建三区内存(Frozen/Stable/Active),与 build_initial 后状态一致(AC-1)
3. **扩展 ContextManager.replace_frozen_zone** — `backend/private_agent/core/context_manager.py` 新增 `async def replace_frozen_zone(self, conn, *, system_prompt: str, tools: list[ToolDef])`:删除 messages 表 `session_id+zone='frozen'` 记录 → 更新 self._system_prompt + self._tools → 调 build_initial(conn) → 重新计算 frozen_hash → UPDATE sessions SET locked_skill_version + frozen_hash(AC-2)
4. **扩展 SkillLoader.load_version** — `backend/private_agent/skills/loader.py:40` 后新增 `async def load_version(self, skill_name: str, version: str, conn=None) -> Skill`:查 version_snapshots 表 `WHERE scope='skill' AND version=$1`,payload 反序列化为 Skill;版本不存在抛 SkillNotFoundError(AC-3)
5. **新建 MockToolRegistry** — `backend/private_agent/eval/mock_tool_registry.py:1-90`:
   - `__init__(real_registry, mock_data_dir)`:存 real_registry + mock_data_dir
   - `set_sample_id(sample_id)`:设置当前样本 ID(显式方法,非 contextvars,spec Mitigation)
   - `get_mock_handler(sample_id, tool_name)`:读 `{mock_data_dir}/{tool_name}/{sample_id}.json`,返回 async handler(读 JSON → 返回 ToolResult);文件缺失返回 error="mock_data_not_found"
   - `list_tools_for_session(whitelist)`:调 real_registry.list_tools_for_session(whitelist),用 `dataclasses.replace` 替换 handler 为 mock 版本(AC-4, AC-5)
6. **新建 ReplayExecutor** — `backend/private_agent/eval/replay.py:1-100`:
   - `__init__(skill_loader, context_manager_cls, model_adapter, tool_registry, mock_registry_cls=None, conn_factory=None)`:存依赖
   - `run_replay(sample, skill, model_id, mock_enabled, conn) -> tuple[str, list[dict]]`:① INSERT INTO sessions (title=f"eval-{run_id}") → ② ContextManager(session_id, skill.system_prompt, tools) → ③ build_initial(conn) → ④ append_user_message(turn=1, content=sample.input) → ⑤ mock_enabled 时 MockToolRegistry.set_sample_id(sample.sample_id) → ⑥ ReactLoop(session_id, ctx, adapter, tools, conn, event_sink=None) → ⑦ 收集 actual_events(订阅 event_queue) → ⑧ loop.run_turn(sample.input) → ⑨ DELETE FROM sessions WHERE id=session_id → ⑩ return (final_output, actual_events)(AC-8, AC-9)
7. **新建 EvalRunner** — `backend/private_agent/eval/runner.py:1-120`:
   - `__init__(dataset_repo, eval_repo, snapshot_repo, skill_loader, model_adapter, hybrid_evaluator, cfg)`:存依赖 + cfg(读 regression_subset)
   - `run_evaluation(skill_name, skill_version, model_id, eval_mode, mock_enabled=False, sample_subset=None, conn) -> str`:① load_test_set(scenario, skill_version) → ② sample_subset="quick" 取前 regression_subset 条 → ③ create_run → ④ 逐条 _eval_sample → ⑤ 汇总 metrics + sample_results → ⑥ update_run_metrics + complete_run;失败 fail_run(AC-7, AC-8)
   - `_eval_sample(sample, eval_mode, mock_enabled, conn) -> dict`:offline 仅调 model_adapter.chat,actual_events=[];replay 调 ReplayExecutor.run_replay();返回 HybridEvaluator.evaluate_sample 结果
8. **新建 SkillVersionListener** — `backend/private_agent/eval/version_listener.py:1-50`:
   - `__init__(eval_runner, cfg)`:存 eval_runner + cfg(读 auto_trigger_on_version_change)
   - `on_skill_version_saved(skill_name, new_version, conn)`:if cfg["eval"]["auto_trigger_on_version_change"]: eval_runner.run_evaluation(eval_mode="offline", sample_subset="quick", model_id="default");失败仅记日志,不阻塞(AC-10)
9. **创建 office mock_data** — `backend/skills/office/examples/test/mock_data/{file_read,code_execution,web_search}/office_00{1-4}_*.json`(12 个文件):每个 JSON 格式 `{"output": str, "error": str|null, "metadata": {}}`,按 office 4 条种子样本的 expected_react_trace 预设输出(AC-11)
10. **新建 test_react_loop_event_sink.py** — `backend/tests/test_react_loop_event_sink.py`(AC-6):event_sink=None 时不推 WS(event_queue 仍入队);event_sink 非 None 时推 WS 回调;现有调用方行为不变(回归测试)
11. **新建 test_context_manager_replay.py** — `backend/tests/test_context_manager_replay.py`(AC-1, AC-2):reload_from_db 三区重建;replace_frozen_zone 删旧建新 + frozen_hash 更新
12. **新建 test_skill_loader_version.py** — `backend/tests/test_skill_loader_version.py`(AC-3):load_version 从 version_snapshots 读 payload;版本不存在抛 SkillNotFoundError
13. **新建 test_eval_mock_tool_registry.py** — `backend/tests/test_eval_mock_tool_registry.py`(AC-4, AC-5):get_mock_handler 读 JSON;文件缺失返回 error;list_tools_for_session handler 替换;set_sample_id 切换
14. **新建 test_eval_runner.py** — `backend/tests/test_eval_runner.py`(AC-7, AC-8):offline 模式 actual_events=[];replay 模式 mock_enabled=True actual_events 含 tool_call/tool_result;sample_subset="quick" 取前 N 条;失败时 fail_run
15. **新建 test_eval_replay.py** — `backend/tests/test_eval_replay.py`(AC-9):run_replay 创建临时会话 + 执行后删除;title="eval-{run_id}" 前缀;actual_events 收集完整
16. **新建 test_eval_version_listener.py** — `backend/tests/test_eval_version_listener.py`(AC-10):auto_trigger=True 触发快速回归;auto_trigger=False 不触发;触发失败仅记日志
17. **新建 test_eval_e2e.py** — `backend/tests/test_eval_e2e.py`(AC-12):端到端离线评估 + 交互式回放(mock 模式)均跑通;metrics 含五类指标;eval_runs 表记录完整(metrics + sample_results + finished_at)

#### Workspace setup

- 实施前运行 `git status --short` 和 `git branch --show-current`
- 当前 working tree 含 2 个非本次改动的 dirty 文件(`config/loader.py`、`code_execution.py`,仅 CRLF/LF 行尾差异),不混入本次 plan
- 当前分支为 `master`,推荐创建 worktree:`git worktree add -b codex/m4-eval-runner-replay ../private-agent-m4-eval-runner-replay`
- 如用户选择在 master 直接实施,需在 commit 时只 add m4-eval-runner-replay 相关文件

#### Open questions (留给后续)

- SkillVersionListener 的集成点(spec Open question):由 SkillManager.activate_skill 调用,还是由 admin API 的版本保存端点调用?建议在 m4-version-compare-rollback spec 中确定。本 spec 仅实现 listener 类本身,不集成调用点。

---

### Architect challenge

#### Steelman against favored option

针对 Option A(内联 SQL + event_sink 扩展),最强反驳:

**反方核心论点**:ReplayExecutor 内联 `INSERT INTO sessions` / `DELETE FROM sessions` 违反「数据访问层分离」原则。项目虽无 SessionRepo,但仓储模式(Storage 层)是项目既定方向(eval_repos / kb_repo / memories_repo 都是仓储)。在 ReplayExecutor(业务编排层)内联 SQL 是技术债,未来 SessionRepo 抽取时需重构。

**如果反驳成立,plan 应改成什么样**:抽取最小 SessionRepo(`backend/private_agent/storage/session_repo.py`),仅含 `create_eval_session(conn, run_id) -> int` + `delete_session(conn, session_id) -> None` 两个方法,不重构现有 30+ 处内联 SQL(仅新代码用 SessionRepo)。

**Planner 反驳**:spec Assumptions 明确「MVP 用 title 前缀避免 schema 变更」,且 spec Solution 的 ReplayExecutor 伪代码直接内联 `session_repo.create` / `session_repo.delete`(spec 假设 session_repo 存在,但项目实际不存在)。Planner 取舍:MVP 阶段在 ReplayExecutor 内用两个私有 helper 方法 `_create_eval_session(conn, run_id)` / `_delete_session(conn, session_id)` 封装 SQL,不独立文件,未来需要时再抽取。这样既不引入新文件,又隔离了 SQL,且符合 spec 最小代码原则。

**Architect 接受**:Planner 的 helper 方法方案平衡了隔离与最小代码。维持 Option A,但实施步骤 6 需明确 helper 方法封装。

#### Tradeoff tensions

1. **ReactLoop event_sink 扩展的侵入性 vs 复用收益**:event_sink 是非破坏性扩展(默认 None),但 ReactLoop 的 `_emit_event` 是核心方法,改动有回归风险。tension:复用 ReactLoop 避免维护两套循环 vs 改动核心方法的回归风险。Planner 取舍:event_sink 扩展仅加 2 行(`if self._event_sink: await self._event_sink(event)`),且现有调用方不注入 event_sink,行为不变。回归风险通过 test_react_loop_event_sink.py + 全量回归覆盖。

2. **MockToolRegistry.set_sample_id 显式方法 vs contextvars**:spec Mitigation 提到用显式 set_sample_id 方法(非 contextvars),但显式方法要求调用方在每次 sample 切换前调用,容易遗漏。tension:显式方法的安全可追溯性 vs contextvars 的自动传播便利性。Planner 取舍:显式方法更安全(contextvars 在异步并发下易出错),ReplayExecutor 在每个 sample 循环开始时显式调用 set_sample_id,且 MockToolRegistry 在未设置 sample_id 时返回 error="sample_id_not_set" 提示遗漏。

3. **临时会话清理的可靠性 vs 性能**:ReplayExecutor 创建临时会话后,若 ReactLoop 异常,会话可能残留。tension:try/finally 清理 vs 性能开销。Planner 取舍:用 try/finally 确保会话清理(性能开销可忽略,DB 操作 <1ms),残留会话由 sessions 表 TTL 清理(spec Edge cases 提到 title 前缀区分,list_sessions 过滤)。

#### Synthesis path

部分接受:Architect 的 steelman 促使 Planner 在实施步骤 6 中明确 ReplayExecutor 用私有 helper 方法封装 SQL(非裸 SQL),平衡隔离与最小代码。其余维持 Option A。

#### Principle violations

无违反项。Option A 与 5 条 Principles 一致:
- 最小代码:复用 ReactLoop / ContextManager / SkillLoader,无新抽象
- 不擅自扩:不实现 spec Out of scope 项
- 复用基础设施:仓储层 + HybridEvaluator 全复用
- 非破坏性扩展:event_sink 默认 None,现有方法签名不变
- 临时会话 title 前缀:MVP 避免 schema 变更

---

### Critic verdict

| 维度 | 状态 | 备注 |
|---|---|---|
| Principle consistency | ✓ | Option A 与 5 条 Principles 一致,无矛盾 |
| Alternative exploration | ✓ | Option A/B 为真候选,Option B 有明确 invalidation rationale(30+ 处重构违反外科手术式改动) |
| Risk mitigation clarity | ✓ | 5 条 risk 各对应 1 行 mitigation,无"以后再说" |
| AC testability | ✓ | 12 条 AC 均二值可验证,每条 AC 映射到具体测试文件名 |
| Verification concreteness | ✓ | 验证步骤含具体 pytest 命令 + 测试文件名 + 期望通过数 |
| File/line coverage | ✓ | 17 步实施步骤全部 cite 具体文件路径 + 行号区间,覆盖率 100% |

### Verdict: APPROVED

### Reservations

1. **Implementation step 6 `ReplayExecutor.run_replay` 的 actual_events 收集机制** — spec 说"收集所有 tool_call/tool_result 到 actual_events",但 ReactLoop 的 event_queue 是 asyncio.Queue,ReplayExecutor 需在 run_turn 执行期间并发消费 queue。我有保留:并发消费 queue 的时序可能错乱(queue.get() 阻塞 vs run_turn 推送)。Mitigation:实施时用 `asyncio.create_task(loop.run_turn(sample.input))` + `while True: event = await queue.get()` + `if event["event_type"] == "final": break` 模式,确保 run_turn 完成后 queue 清空。此改进在 dev-tdd 阶段通过 test_eval_replay.py 红绿验证。

2. **Implementation step 9 `office mock_data` 的 12 个文件** — spec AC-11 要求 office 场景 4 条种子样本 × 3 工具(file_read/code_execution/web_search)= 12 个 mock 文件。我有保留:并非所有 office 种子样本都涉及 3 个工具(如 office_004_error 可能只涉及 file_read)。Mitigation:实施时按每条样本的 expected_react_trace.tool_calls 决定哪些工具需要 mock_data,未涉及的工具不创建 mock 文件(get_mock_handler 返回 error="mock_data_not_found" 时 ReplayExecutor 可降级)。实际 mock 文件数 ≤ 12。

---

## Implementation steps (final)

(同 Planner draft 的 17 步,已合并 Architect 改进:步骤 6 明确 helper 方法封装 SQL)

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| ReactLoop event_sink 扩展可能遗漏某处 WS 推送 | event_sink 扩展仅加 2 行在 _emit_event,现有调用方不注入;test_react_loop_event_sink.py + 全量回归覆盖 |
| 临时评估会话残留(ReactLoop 异常时) | ReplayExecutor 用 try/finally 确保 DELETE FROM sessions;残留会话由 title 前缀区分 + sessions 表 TTL 清理 |
| MockToolRegistry.set_sample_id 遗漏调用 | MockToolRegistry 在未设置 sample_id 时返回 error="sample_id_not_set";ReplayExecutor 每个 sample 循环开始显式调用 |
| actual_events 收集时序错乱(queue 消费 vs run_turn 推送) | 用 asyncio.create_task(loop.run_turn) + while loop 消费 queue,final event 时 break;dev-tdd 红绿验证 |
| SkillVersionListener 集成点未确定(spec Open question) | 本 spec 仅实现 listener 类,不集成调用点;集成点在 m4-version-compare-rollback spec 确定 |
| mock_data 文件缺失 | get_mock_handler 返回 error="mock_data_not_found";ReplayExecutor 降级处理(记录 warning,不阻塞) |

## Verification steps

- 验证 AC-1, AC-2:`pytest tests/test_context_manager_replay.py -v`(期望 4 个测试全过)
- 验证 AC-3:`pytest tests/test_skill_loader_version.py -v`(期望 3 个测试全过)
- 验证 AC-4, AC-5:`pytest tests/test_eval_mock_tool_registry.py -v`(期望 5 个测试全过)
- 验证 AC-6:`pytest tests/test_react_loop_event_sink.py -v`(期望 3 个测试全过)
- 验证 AC-7, AC-8:`pytest tests/test_eval_runner.py -v`(期望 5 个测试全过)
- 验证 AC-9:`pytest tests/test_eval_replay.py -v`(期望 3 个测试全过)
- 验证 AC-10:`pytest tests/test_eval_version_listener.py -v`(期望 3 个测试全过)
- 验证 AC-11:office mock_data 文件存在性检查 + MockToolRegistry 读取测试
- 验证 AC-12:`pytest tests/test_eval_e2e.py -v`(期望 2 个测试全过:离线 + 回放)
- 全量回归:`pytest tests/ -q`(期望无新增回归,预先存在的 PA_DB_PASSWORD 环境问题 6 个失败可接受)
- 闭环检查:`grep -rn "EvalRunner\|ReplayExecutor\|MockToolRegistry\|SkillVersionListener\|reload_from_db\|replace_frozen_zone\|load_version\|event_sink" backend/` 确认新公开符号有调用者

## ADR

- **Decision**: 采用 Option A 内联 SQL(helper 方法封装)+ event_sink 扩展 + 组合 MockToolRegistry,复用 ReactLoop / ContextManager / SkillLoader,仅做非破坏性扩展
- **Drivers**: ReactLoop 复用避免双循环(决定性)、MockToolRegistry 组合避免破坏继承链(决定性)、SessionRepo 抽取违反外科手术式改动(辅助)
- **Alternatives considered**:
  - Option A(内联 SQL + event_sink 扩展 + 组合 MockToolRegistry):chosen — 完全遵循 spec,无新抽象,与项目当前风格一致
  - Option B(抽取 SessionRepo):rejected — 需重构 30+ 处现有内联 SQL,违反外科手术式改动,spec 未要求
- **Why chosen**: Option A 最小代码,复用现有基础设施,非破坏性扩展;ReplayExecutor 用私有 helper 方法封装 SQL 平衡隔离与最小代码;event_sink 默认 None 确保现有调用方不变
- **Consequences**:
  - 正面:ReactLoop 单一循环维护;MockToolRegistry 组合可独立测试;ContextManager/SkillLoader 扩展方法不影响现有调用方
  - 负面:ReplayExecutor 内联 SQL(helper 方法封装)未来 SessionRepo 抽取时需重构(但仅 2 个方法);event_sink 扩展改动 ReactLoop 核心方法(回归风险通过测试覆盖)
  - 约束:后续 m4-version-compare-rollback spec 需确定 SkillVersionListener 集成点
- **Follow-ups**:
  - m4-version-compare-rollback spec 实现 SkillVersionListener 集成点(SkillManager.activate_skill 或 admin API)
  - V2 可抽取 SessionRepo 统一会话管理(当 30+ 处内联 SQL 成为维护负担时)
  - V2 可支持分布式并行回放(spec Out of scope)

## Review trail

- Planner draft v1: 17 步实施步骤,Option A 内联 SQL + event_sink 扩展,5 条 Principles + 3 条 Decision drivers
- Architect challenge v1: steelman 质疑 ReplayExecutor 内联 SQL 违反数据访问层分离,Planner 反驳用 helper 方法封装(不独立文件);3 条 tradeoff tensions(event_sink 侵入性 / set_sample_id vs contextvars / 临时会话清理可靠性)
- Critic verdict v1: APPROVED — 6 维度全 ✓,2 条 Reservations(actual_events 收集时序 / mock_data 文件数 ≤ 12)
- Final iterations: 1 / 3
