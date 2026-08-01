# M4 评估闭环 - 评估执行与交互式回放 Spec (m4-eval-runner-replay)

> Status: ALIGNED
> Author: user
> Last updated: 2026-08-01

## Background

评估执行流程:离线批量(仅调模型,不执行工具)+ 交互式回放(完整 ReAct 循环 + Mock 模式)。依赖 m4-eval-foundation 的仓储层与 EvalSample,以及 m4-metrics-judge 的 HybridEvaluator。本 spec 复用现有 ReactLoop + 最小扩展 ContextManager,避免新建独立循环或抽象基类。

蓝图章节: §8.9(评估执行流程)、§8.10(交互式回放 + Mock 模式)。

## In scope

### A. ContextManager 扩展 (复用 ReactLoop 前置)
- 扩展 `backend/private_agent/core/context_manager.py`:

```python
async def reload_from_db(self, conn) -> None:
    """完整重放历史消息(Frozen+Stable+Active 三区)
    M1 ensure_initial 仅 reload Frozen Zone,本方法补全 Stable/Active reload
    用于 ReplayExecutor 重建评估会话上下文
    """

async def replace_frozen_zone(self, conn, *, system_prompt: str, tools: list[ToolDef]) -> None:
    """替换 Frozen Zone(版本切换/回滚时使用)
    1. 删除 messages 表中 session_id+zone='frozen' 的记录
    2. 重新 build_initial(conn)(用新 system_prompt + tools)
    3. 重新计算 frozen_hash
    用于版本回滚场景(Done Criteria AC-6)
    """
```

### B. SkillLoader 扩展: load_version
- 扩展 `backend/private_agent/skills/loader.py`:

```python
async def load_version(self, skill_name: str, version: str, conn=None) -> Skill:
    """按指定 version 加载历史 Skill 快照
    1. 从 version_snapshots 表读 scope='skill' + version 的 payload
    2. payload 反序列化为 Skill 模型
    3. Raises: SkillNotFoundError(版本不存在)
    用于版本回滚 + 回放历史版本
    """
```

### C. MockToolRegistry (蓝图 §8.10 Mock 模式)
- 新建 `backend/private_agent/eval/mock_tool_registry.py`:

```python
class MockToolRegistry:
    """mock 模式下替换真实工具 handler,返回预设结果
    mock 数据匹配规则:sample_id + tool_name 二级索引
    """

    def __init__(self, real_registry: ToolRegistry, mock_data_dir: str) -> None: ...

    def get_mock_handler(self, sample_id: str, tool_name: str) -> Callable[[dict], Awaitable[ToolResult]]:
        """返回 mock handler:读取 {mock_data_dir}/{tool_name}/{sample_id}.json
        mock JSON 格式: {"output": str, "error": str | None, "metadata": {}}
        """

    def list_tools_for_session(self, whitelist: list[str] | None) -> list[ToolDef]:
        """代理 real_registry.list_tools_for_session(),保持工具列表一致
        但 handler 替换为 mock 版本
        """
```

- mock 数据目录结构(蓝图 §8.10):
```
skills/{name}/test/mock_data/
├── file_read/
│   └── office_001_normal.json    # sample_id 索引
├── code_execution/
│   └── office_001_normal.json
└── web_search/
    └── office_001_normal.json
```

- mock 数据跟随 Skill 版本同步:mock_data 目录在 Skill 快照中(version_snapshots payload 含 mock_data 路径或内联数据)
- Skill 回滚时,ReplayExecutor 按 skill_version 加载对应版本 mock_data(AC-2)

### D. EvalRunner (蓝图 §8.9)
- 新建 `backend/private_agent/eval/runner.py`:

```python
class EvalRunner:
    def __init__(
        self,
        dataset_repo: EvalDatasetRepo,
        eval_repo: EvalRunRepo,
        snapshot_repo: VersionSnapshotRepo,
        skill_loader: SkillLoader,
        model_adapter: ModelAdapter,
        hybrid_evaluator: HybridEvaluator,
    ) -> None: ...

    async def run_evaluation(
        self,
        *,
        skill_name: str,
        skill_version: str,
        model_id: str,
        eval_mode: str,            # "offline" | "replay"
        mock_enabled: bool = False,
        sample_subset: str | None = None,   # None=全量, "quick"=前5条
        conn=None,
    ) -> str:
        """执行评估,返回 run_id
        1. 加载数据集(dataset_repo.load_test_set)
        2. sample_subset="quick" 时取前 regression_subset 条(从 config 读)
        3. 创建评估运行(eval_repo.create_run)
        4. 逐条执行样本(_eval_sample)
        5. 汇总 metrics(eval_repo.update_run_metrics)
        6. 完成(eval_repo.complete_run)
        失败时 eval_repo.fail_run
        """

    async def _eval_sample(self, sample: EvalSample, eval_mode: str, mock_enabled: bool, conn) -> dict:
        """评估单条样本
        offline: 仅调模型(不执行工具),actual_events=[]
        replay: 调 ReplayExecutor.run_replay(),获取 actual_output + actual_events
        返回 HybridEvaluator.evaluate_sample() 结果
        """
```

### E. ReplayExecutor (蓝图 §8.10)
- 新建 `backend/private_agent/eval/replay.py`:

```python
class ReplayExecutor:
    def __init__(
        self,
        skill_loader: SkillLoader,
        context_manager_cls: type,
        model_adapter: ModelAdapter,
        tool_registry: ToolRegistry,
        mock_registry_cls: type | None = None,   # mock 模式注入
        session_repo=None,
    ) -> None: ...

    async def run_replay(
        self,
        sample: EvalSample,
        skill: Skill,
        model_id: str,
        mock_enabled: bool,
        conn,
    ) -> tuple[str, list[dict]]:
        """交互式回放
        1. 创建临时评估会话(session_repo.create, is_eval_session=True)
        2. 构建 Frozen Zone(context_manager.build_initial,用 skill.system_prompt + tools)
        3. 注入用户输入(context_manager.append_user_message)
        4. 执行 ReAct 循环(复用 ReactLoop,但 event_sink=None 静默不推 WS)
           - mock_enabled=True: 用 MockToolRegistry 替换 tool_registry
           - mock_enabled=False: 真实执行 tool_def.handler(args)
        5. 记录所有 tool_call/tool_result 到 actual_events
        6. 清理临时会话(session_repo.delete)
        返回 (final_output, actual_events)
        """
```

### F. ReactLoop 扩展: event_sink 参数
- 扩展 `backend/private_agent/core/react_loop.py`:

```python
class ReactLoop:
    def __init__(
        self,
        ...,
        event_sink: Callable[[dict], Awaitable[None]] | None = None,
    ) -> None:
        """event_sink=None 时静默(不推 WS),用于 ReplayExecutor
        event_sink 非 None 时正常推 WS(真实会话)
        """
```

- 现有 WS 推送逻辑改为 `if self._event_sink: await self._event_sink(event)`
- 不破坏现有调用方(默认 event_sink=None,真实会话注入 WS 推送回调)

### G. 版本变更自动触发 (蓝图 §8.9)
- 新建 `backend/private_agent/eval/version_listener.py`:

```python
class SkillVersionListener:
    def __init__(self, eval_runner: EvalRunner, cfg: dict) -> None: ...

    async def on_skill_version_saved(self, skill_name: str, new_version: str, conn) -> None:
        """Skill 保存新版本后自动触发快速回归(仅当 cfg["eval"]["auto_trigger_on_version_change"]=True)
        - eval_mode="offline"(快速回归用离线模式)
        - sample_subset="quick"(前 regression_subset 条)
        - model_id="default"(用默认模型)
        - 失败仅记日志,不阻塞版本保存
        """
```

### H. mock 数据种子(3 场景 × 少量)
- 为 office 场景的种子样本创建 mock_data(验证 MockToolRegistry 管线)
- mock 数据文件:office 的 4 条种子样本 × 涉及的工具(file_read/code_execution/web_search)
- data_analysis / frontend_design 的 mock_data 由 §8.16 渐进填充

## Out of scope

- 版本对比 / 回滚机制(m4-version-compare-rollback spec)
- 低分案例自动提取(m4-continuous-evolution spec)
- 在线评估(V2)
- 分布式并行回放(V2)
- 回放过程可视化(V2)
- Mock 数据自动生成(V2)
- 完整 60 条样本的 mock_data(仅交付 office 场景种子 mock_data)

## Assumptions

- ReactLoop 现有构造函数支持新增 `event_sink` 参数(非破坏性扩展)
- ContextManager.build_initial 已存在(M1),reload_from_db / replace_frozen_zone 为新增方法
- 临时评估会话用 sessions 表 + 标记字段区分(如 title="eval-{run_id}" 或新增 is_eval 字段,MVP 用 title 前缀避免 schema 变更)
- MockToolRegistry 不继承 ToolRegistry,而是组合(持有 real_registry 引用),避免破坏现有继承链
- SkillVersionListener 由 SkillManager.activate_skill 或版本保存路径调用(具体集成点在 m4-version-compare-rollback spec)
- 安全事件用 event_type="error" + payload.subtype 表达(与 m4-metrics-judge spec 决策一致)

## Solution

### 离线批量评估流程
```
EvalRunner.run_evaluation(eval_mode="offline")
    ↓
    load_test_set(scenario, skill_version)
    ↓ create_run(status="running")
    for sample in dataset:
        _eval_sample(sample, "offline", mock_enabled=False)
            ↓ model_adapter.chat([{role:user, content:sample.input}])
            ↓ actual_output = response.content
            ↓ actual_events = []  (离线模式无工具调用)
            ↓ HybridEvaluator.evaluate_sample(sample, actual_output, actual_events)
    ↓ update_run_metrics(run_id, aggregated_metrics, sample_results)
    ↓ complete_run(run_id)
```

### 交互式回放流程(mock 模式)
```
EvalRunner.run_evaluation(eval_mode="replay", mock_enabled=True)
    ↓
    load_test_set(scenario, skill_version)
    ↓ create_run(status="running", mock_enabled=True)
    for sample in dataset:
        _eval_sample(sample, "replay", mock_enabled=True)
            ↓ ReplayExecutor.run_replay(sample, skill, model_id, mock_enabled=True)
                ↓ session = create_eval_session()
                ↓ ctx = ContextManager(session.id, skill.system_prompt, tools)
                ↓ ctx.build_initial(conn)
                ↓ ctx.append_user_message(turn=1, content=sample.input)
                ↓ mock_registry = MockToolRegistry(real_registry, mock_data_dir)
                ↓ loop = ReactLoop(ctx, model_adapter, mock_registry, event_sink=None)
                ↓ loop.run()  → 收集 actual_events
                ↓ delete_eval_session(session.id)
                ↓ return (final_output, actual_events)
            ↓ HybridEvaluator.evaluate_sample(sample, actual_output, actual_events)
    ↓ update_run_metrics + complete_run
```

### 关键实现细节

**ReactLoop event_sink 扩展**(最小侵入):
```python
# 现有:self._emit_event(event)  → 推 WS
# 改为:
if self._event_sink:
    await self._event_sink(event)
# event_sink=None 时不推,ReplayExecutor 用此模式
```

**MockToolRegistry handler 替换**:
```python
def list_tools_for_session(self, whitelist):
    tools = self._real_registry.list_tools_for_session(whitelist)
    return [
        replace(tool_def, handler=self._wrap_mock_handler(tool_def.name, current_sample_id))
        for tool_def in tools
    ]
```
- `current_sample_id` 通过 contextvars 或 ReplayExecutor 预先设置(每个 sample 切换前 set)

## Edge cases & risks

| Category | Notes |
|---|---|
| Boundary conditions | 离线模式 actual_events=[],tool_calls/efficiency/security 指标返回零值;mock_data 文件缺失时 mock handler 返回 error="mock_data_not_found" |
| Failure modes | 模型调用失败→sample 标记 failed,继续下一条;ReplayExecutor 循环异常→session 清理后 re-raise;version_listener 触发失败仅记日志 |
| Risks | ReactLoop event_sink 扩展可能遗漏某处 WS 推送;临时评估会话用 title 前缀区分可能在 list_sessions 时混入;MockToolRegistry 的 current_sample_id 上下文传递可能出错 |
| Mitigation | ReactLoop 改动后跑全量回归测试;临时会话 title 用 "eval-{run_id}" 前缀,session_repo.list 过滤;MockToolRegistry 用显式 set_sample_id() 方法(非 contextvars) |

## Acceptance criteria

- AC-1: `ContextManager.reload_from_db(conn)` 完整重载三区消息(Frozen+Stable+Active),与 build_initial 后的状态一致
- AC-2: `ContextManager.replace_frozen_zone(conn, system_prompt, tools)` 替换 Frozen Zone 后 frozen_hash 重新计算,sessions 表 locked_skill_version 更新
- AC-3: `SkillLoader.load_version(skill_name, version, conn)` 从 version_snapshots 表读历史 payload 返回 Skill,版本不存在抛 SkillNotFoundError
- AC-4: `MockToolRegistry.get_mock_handler(sample_id, tool_name)` 读取 `{mock_data_dir}/{tool_name}/{sample_id}.json` 返回 mock handler
- AC-5: `MockToolRegistry.list_tools_for_session(whitelist)` 返回工具列表与 real_registry 一致,但 handler 替换为 mock 版本
- AC-6: `ReactLoop` 新增 `event_sink` 参数,event_sink=None 时不推 WS,现有调用方(注入 WS 推送回调)行为不变
- AC-7: `EvalRunner.run_evaluation(eval_mode="offline")` 离线批量评估执行成功,每样本仅调模型不执行工具,actual_events=[]
- AC-8: `EvalRunner.run_evaluation(eval_mode="replay", mock_enabled=True)` 交互式回放执行成功,actual_events 含 tool_call/tool_result,Mock 数据按 sample_id+tool_name 索引
- AC-9: `ReplayExecutor.run_replay()` 创建临时评估会话,执行完毕后删除会话(title="eval-{run_id}" 前缀区分)
- AC-10: `SkillVersionListener.on_skill_version_saved()` 触发快速回归(offline + quick subset),auto_trigger_on_version_change=False 时不触发
- AC-11: office 场景 4 条种子样本的 mock_data 创建(file_read/code_execution/web_search 三工具)
- AC-12: 端到端测试:离线评估 + 交互式回放(mock 模式)均跑通,metrics 含五类指标,eval_runs 表记录完整

## Open questions

- SkillVersionListener 的集成点:由 SkillManager.activate_skill 调用,还是由 admin API 的版本保存端点调用?建议在 m4-version-compare-rollback spec 中确定(该 spec 负责版本管理 API)

## Core entities (ontology)

| Entity | Type | Key fields | Relationship |
|---|---|---|---|
| EvalRunner | Class | dataset_repo, eval_repo, model_adapter, hybrid_evaluator | 编排评估执行 |
| ReplayExecutor | Class | skill_loader, context_manager_cls, model_adapter, tool_registry | 交互式回放 |
| MockToolRegistry | Class | real_registry, mock_data_dir | mock 模式工具替换 |
| SkillVersionListener | Class | eval_runner, cfg | 版本变更自动触发 |
| EvalSession | Session | title="eval-{run_id}" | 临时评估会话 |

## Interview metadata

- Mode: default
- Waves: 4
- Final ambiguity: 14%
- Status: PASSED

### Clarity breakdown

| Dimension | Score | Weight | Weighted |
|---|---|---|---|
| Goal | 0.88 | 0.40 | 0.352 |
| Scope | 0.88 | 0.25 | 0.22 |
| AC | 0.88 | 0.25 | 0.22 |
| Context | 0.85 | 0.10 | 0.085 |
| Ambiguity | | | 12.3% |
