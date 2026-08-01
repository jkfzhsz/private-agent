# M4 评估闭环 - 指标与 LLM-as-Judge Spec (m4-metrics-judge)

> Status: ALIGNED
> Author: user
> Last updated: 2026-08-01

## Background

五类指标计算器与 LLM-as-Judge 混合评判机制。蓝图 §8.5-8.8 提供完整代码样例,本 spec 实现五类指标(任务完成/工具准确/LLM-Judge/效率/安全)的纯函数计算器 + GLM-4-Flash Judge 模块。依赖 m4-eval-foundation 的 EvalSample / ExpectedTrace 模型与 judge_prompts/general.md 模板。

蓝图章节: §8.5(指标全景)、§8.6(任务完成率 + 工具调用准确率)、§8.7(效率 + 安全指标)、§8.8(LLM-as-Judge)。

## In scope

### A. 五类指标计算器 (蓝图 §8.6 + §8.7)
- 新建 `backend/private_agent/eval/metrics.py`,五类指标为纯函数:

```python
def evaluate_task_completion(expected: ExpectedTrace, actual_output: str) -> dict:
    """任务完成率:expected_output_contains 关键词匹配 actual_output
    返回: {completion_rate: float, matched_keywords: list[str], missing_keywords: list[str]}
    """

def evaluate_tool_calls(expected_trace: ExpectedTrace, actual_events: list[dict]) -> dict:
    """工具调用准确率:工具选择 + 顺序 + 参数三维度
    返回: {tool_selection_accuracy, order_correct: bool, param_accuracy, expected_calls_count, actual_calls_count}
    actual_events 格式: [{"event_type": "tool_call", "tool": str, "args": dict}, ...]
    """

def evaluate_efficiency(events: list[dict]) -> dict:
    """效率指标:react_turns / tool_calls_count / total_tokens / total_cost / duration_seconds
    events 格式同上,含 timestamp 字段
    """

def evaluate_security(events: list[dict]) -> dict:
    """安全指标:injection_alerts_count / permission_denied_count / sandbox_violations_count / security_score
    security_score = max(0, 100 - alerts*10 - denied*5)
    """

def compute_all_metrics(expected: ExpectedTrace, actual_output: str, actual_events: list[dict]) -> dict:
    """汇总五类指标(不含 LLM-Judge,LLM-Judge 由 judge.py 异步调用)
    返回: {task_completion, tool_calls, efficiency, security}
    """
```

### B. LLM-as-Judge 模块 (蓝图 §8.8)
- 新建 `backend/private_agent/eval/judge.py`:

```python
class LLMJudge:
    def __init__(self, adapter: ModelAdapter, prompt_template: str) -> None: ...
    async def judge(self, *, user_input: str, agent_response: str, expected_output: str | None) -> dict:
        """调用 Judge 模型,返回 {response_quality: 1-5, task_completion: 1-5, quality_reason, completion_reason}
        解析失败时返回 {response_quality: 0, task_completion: 0, quality_reason: "judge_parse_error", ...}
        """

def build_judge_adapter(cfg: dict) -> ModelAdapter:
    """工厂函数:读 cfg["eval"]["judge_model"](glm-4-flash),构造 GlmAdapter
    复用现有 GlmAdapter 实现,仅配置不同
    """

def load_judge_prompt(cfg: dict) -> str:
    """加载 cfg["eval"]["judge_prompt_dir"]/general.md,返回模板字符串
    """
```

### C. Judge prompt 模板填充
- Judge 调用时填充 `general.md` 模板变量:`{user_input}` / `{agent_response}` / `{expected_output}`
- Judge 模型输出严格 JSON(§8.8 schema),解析失败降级返回 0 分 + reason="judge_parse_error"

### D. 规避同模型自评偏见 (蓝图 §8.8)
- `build_judge_adapter` 读 `cfg["eval"]["judge_model"]`(固定 glm-4-flash),不读 `cfg["models"]["default"]`
- Judge 模型与被评估主模型分离(主模型可能是 glm/deepseek/kimi,Judge 固定 glm-4-flash)
- 配置中 Judge 模型可指定,确保与被评估模型不同

### E. 混合评判流程编排 (蓝图 §8.8)
- 新建 `backend/private_agent/eval/hybrid_eval.py`:

```python
class HybridEvaluator:
    def __init__(self, judge: LLMJudge) -> None: ...

    async def evaluate_sample(
        self,
        sample: EvalSample,
        actual_output: str,
        actual_events: list[dict],
    ) -> dict:
        """混合评判:规则指标(同步) + LLM-Judge(异步)
        返回: {
            sample_id, actual_output, actual_events,
            metrics: {task_completion, tool_calls, efficiency, security, llm_judge}
        }
        规则指标用 compute_all_metrics(),LLM-Judge 调 judge.judge()
        """
```

## Out of scope

- EvalRunner / ReplayExecutor 执行流程(m4-eval-runner-replay spec)
- metrics JSON 入库(m4-eval-foundation 的 EvalRunRepo.update_run_metrics 已提供)
- 场景差异化 Judge prompt(V2)
- 多 Judge 投票(V2)
- 综合评分 + 权重配置(V2)
- 模糊匹配(语义相似度,V2)

## Assumptions

- `actual_events` 格式与 react_events 表 payload 一复用(M1 已定义)
- `actual_events` 中的 event_type 取值:thinking / tool_call / tool_result / final / error / injection_alert / permission_denied
- Judge 模型 glm-4-flash API Key 复用 `PA_GLM_API_KEY` 环境变量(与主 GLM 适配器共享)
- Judge 调用失败(网络/超时/解析失败)降级返回 0 分,不阻塞评估流程
- 五类指标独立展示,MVP 不做综合评分(蓝图 §8.5)

## Solution

### 指标计算流程
```
EvalSample + actual_output + actual_events
    ↓
    ├─ compute_all_metrics()  [同步,纯函数]
    │   ├─ evaluate_task_completion()  [关键词匹配]
    │   ├─ evaluate_tool_calls()       [序列对比]
    │   ├─ evaluate_efficiency()       [统计计算]
    │   └─ evaluate_security()         [事件统计]
    │
    └─ LLMJudge.judge()  [异步,调 GLM-4-Flash]
        ↓
        合并 → 完整 metrics dict
```

### 关键实现细节

**工具调用准确率 - 参数匹配规则**(蓝图 §8.6):
- `expected_val` 是 str 且 `expected_val in str(actual_args[key])` → 命中(支持 `code_contains: "groupby"` 模糊匹配)
- `expected_val` 等值 `actual_args[key]` → 命中(精确匹配)

**安全指标 event_type 扩展**:
- 蓝图 §8.7 引用 `injection_alert` / `permission_denied` 事件类型,但 react_events 表 CHECK 约束仅允许 `thinking/tool_call/tool_result/final/error/checkpoint`
- **决策**:安全事件用 `event_type="error"` + `payload.subtype="injection_alert"|"permission_denied"|"sandbox_violation"` 表达,evaluate_security 从 payload.subtype 读取
- 此决策需在 m4-eval-runner-replay spec 中同步(ReplayExecutor 记录安全事件时用此格式)

**Judge JSON 解析容错**:
- Judge 模型可能输出非严格 JSON(如带 markdown 代码块包裹)
- 解析逻辑:提取 ```json ... ``` 块 → json.loads → 失败则尝试整段 json.loads → 仍失败返回 0 分

## Edge cases & risks

| Category | Notes |
|---|---|
| Boundary conditions | expected_output_contains 为空时 completion_rate=1.0;actual_events 为空时 tool_calls/efficiency/security 返回零值;total_tokens 缺失时按 0 处理 |
| Failure modes | Judge 模型调用失败(网络/超时)降级返回 0 分 + reason="judge_call_failed";JSON 解析失败降级返回 0 分 + reason="judge_parse_error" |
| Risks | react_events event_type CHECK 约束不含 injection_alert,安全事件需用 payload.subtype 表达,可能与蓝图 §8.7 字面描述不一致;Judge 模型输出不稳定可能影响评分一致性 |
| Mitigation | evaluate_security 明确从 payload.subtype 读取;Judge temperature=0.1 保证一致性;降级返回 0 分不阻塞流程 |

## Acceptance criteria

- AC-1: `evaluate_task_completion()` 正确计算 completion_rate = matched / total,空 expected_output_contains 返回 1.0
- AC-2: `evaluate_tool_calls()` 计算三维度(tool_selection_accuracy / order_correct / param_accuracy),参数模糊匹配(str in str)与精确匹配均支持
- AC-3: `evaluate_efficiency()` 从 events 统计 react_turns / tool_calls_count / total_tokens / total_cost / duration_seconds
- AC-4: `evaluate_security()` 从 events payload.subtype 统计 injection_alerts / permission_denied / sandbox_violations,security_score = max(0, 100 - alerts*10 - denied*5)
- AC-5: `compute_all_metrics()` 汇总四类规则指标(不含 llm_judge),返回 dict 含 task_completion/tool_calls/efficiency/security 四键
- AC-6: `build_judge_adapter(cfg)` 读 `cfg["eval"]["judge_model"]` 构造 GlmAdapter,与主模型适配器分离
- AC-7: `load_judge_prompt(cfg)` 加载 `judge_prompts/general.md` 模板,含 `{user_input}`/`{agent_response}`/`{expected_output}` 变量
- AC-8: `LLMJudge.judge()` 调用 Judge 模型,解析 JSON 返回 {response_quality, task_completion, quality_reason, completion_reason},解析失败降级返回 0 分
- AC-9: `HybridEvaluator.evaluate_sample()` 返回完整 metrics 含五类指标(四类规则 + llm_judge),Judge 失败不阻塞
- AC-10: 单测覆盖五类指标计算器(含空输入/边界/正常用例)+ Judge 降级路径(mock adapter 返回非法 JSON)

## Open questions

(无)

## Core entities (ontology)

| Entity | Type | Key fields | Relationship |
|---|---|---|---|
| LLMJudge | Class | adapter, prompt_template | 调用 Judge 模型 |
| HybridEvaluator | Class | judge | 编排规则指标 + LLM-Judge |
| MetricsResult | dict | task_completion, tool_calls, efficiency, security, llm_judge | 五类指标汇总 |

## Interview metadata

- Mode: default
- Waves: 4
- Final ambiguity: 14%
- Status: PASSED

### Clarity breakdown

| Dimension | Score | Weight | Weighted |
|---|---|---|---|
| Goal | 0.90 | 0.40 | 0.36 |
| Scope | 0.90 | 0.25 | 0.225 |
| AC | 0.90 | 0.25 | 0.225 |
| Context | 0.85 | 0.10 | 0.085 |
| Ambiguity | | | 10.5% |
