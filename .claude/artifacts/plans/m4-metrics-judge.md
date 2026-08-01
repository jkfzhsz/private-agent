# m4-metrics-judge Implementation Plan

> Status: APPROVED
> Source: spec/m4-metrics-judge (ALIGNED, Final ambiguity 10.5%)
> Mode: (default)
> Iterations: 1 / 3
> Author: user
> Last updated: 2026-08-01

## Requirements summary

实现 m4-metrics-judge spec:五类指标计算器(纯函数)+ GLM-4-Flash Judge 模块 + 混合评判编排器。依赖 m4-eval-foundation 的 EvalSample/ExpectedTrace 模型与 judge_prompts/general.md 模板(均已就位)。本 plan 不含 EvalRunner/ReplayExecutor(m4-eval-runner-replay spec 负责)。

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

## RALPLAN-DR

### Planner draft

#### Principles (4)

- **跟随 baseline「最小代码」**:五类指标纯函数 + Judge 模块,不引入抽象基类或策略模式,蓝图 §8.5-8.8 已给代码样例直接落地
- **跟随 spec 的 In scope,不擅自扩**:不实现综合评分、多 Judge 投票、模糊语义匹配(spec Out of scope)
- **复用 m4-eval-foundation 已就位基础设施**:EvalSample/ExpectedTrace 模型、judge_prompts/general.md 模板、GlmAdapter 适配器、PA_GLM_API_KEY 环境变量
- **Judge 降级不阻塞**:Judge 调用失败(网络/超时/解析失败)返回 0 分 + reason,不让评估流程中断

#### Decision drivers (top 3)

1. **蓝图代码样例可直接落地**:§8.5-8.8 提供了完整的函数签名与实现逻辑,最小代码路径清晰
2. **测试可验证性**:五类指标为纯函数,易构造输入/输出断言;Judge 用 mock adapter 测降级路径
3. **与 m4-eval-foundation 的一致性**:工厂函数 `build_judge_adapter` 沿用 `build_compress_adapter` 模式(models/registry.py:63-84),降低认知成本

#### Viable options

**Option A: 单文件 metrics.py + judge.py + hybrid_eval.py(三模块分离)**
- 实现思路:metrics.py 放五类纯函数,judge.py 放 LLMJudge 类 + 工厂函数,hybrid_eval.py 放 HybridEvaluator 编排器。三文件各自独立,职责清晰。
- 改动文件:`backend/private_agent/eval/metrics.py`(新)、`backend/private_agent/eval/judge.py`(新)、`backend/private_agent/eval/hybrid_eval.py`(新)、`backend/tests/test_eval_metrics.py`(新)、`backend/tests/test_eval_judge.py`(新)、`backend/tests/test_eval_hybrid.py`(新)
- Pros: 职责分离清晰;metrics 纯函数无依赖,judge 依赖 models 层,hybrid 依赖两者;测试粒度匹配(纯函数测计算、judge 测降级、hybrid 测编排);与 spec In scope A/B/E 三段一一对应
- Cons: 三个文件 + 三个测试文件,改动面稍大;hybrid_eval.py 仅一个类,可能显得过小

**Option B: 单文件 eval/calculators.py 合并 metrics + hybrid,judge.py 独立**
- 实现思路:calculators.py 放五类纯函数 + HybridEvaluator(因 HybridEvaluator 主要调用 compute_all_metrics + judge.judge,逻辑紧密),judge.py 独立(因依赖 GlmAdapter 与模板加载,职责不同)。
- 改动文件:`backend/private_agent/eval/calculators.py`(新,含五类函数 + HybridEvaluator)、`backend/private_agent/eval/judge.py`(新)、`backend/tests/test_eval_calculators.py`(新)、`backend/tests/test_eval_judge.py`(新)
- Pros: 文件更少(2 个源文件 + 2 个测试文件);hybrid 与 metrics 同文件,编排逻辑可直达计算函数
- Cons: calculators.py 混合纯函数与有状态编排器,职责不纯;与 spec In scope A/B/E 三段不一一对应;测试文件混合纯函数测试与编排测试,粒度模糊;HybridEvaluator 依赖 LLMJudge,但 calculators.py 不依赖 judge.py 会导致循环引用或反向依赖

**Invalidation rationale**:Option B 的 calculators.py 混合纯函数与编排器违反单一职责,且 hybrid 依赖 judge 会造成文件间循环引用隐患。Option A 的三模块分离更清晰,且与 spec 三段一一对应,认知成本更低。

#### Implementation steps (基于 Option A)

1. **新建 metrics.py 五类纯函数骨架** — `backend/private_agent/eval/metrics.py:1-80` 定义 `evaluate_task_completion`/`evaluate_tool_calls`/`evaluate_efficiency`/`evaluate_security`/`compute_all_metrics` 五个函数,导入 ExpectedTrace 类型 hint
2. **实现 evaluate_task_completion** — `backend/private_agent/eval/metrics.py:15-30` 关键词匹配逻辑:遍历 `expected.expected_output_contains`,检查每个关键词是否 in actual_output,completion_rate = matched / total(空列表返回 1.0)
3. **实现 evaluate_tool_calls** — `backend/private_agent/eval/metrics.py:33-65` 三维度计算:
   - tool_selection_accuracy = 匹配工具数 / expected 工具数(按集合交集)
   - order_correct = actual tool_call 序列与 expected 序列完全一致(按 tool 名)
   - param_accuracy = 参数匹配命中数 / expected 参数总数(模糊:str in str;精确:等值)
4. **实现 evaluate_efficiency** — `backend/private_agent/eval/metrics.py:68-85` 从 events 统计:react_turns(thinking 事件数)、tool_calls_count(tool_call 事件数)、total_tokens(从 final 事件 payload.total_tokens 读取,缺失按 0)、total_cost(同上)、duration_seconds(最后事件 created_at - 首事件 created_at)
5. **实现 evaluate_security** — `backend/private_agent/eval/metrics.py:88-105` 从 events 中 event_type="error" 的 payload.subtype 统计 injection_alerts/permission_denied/sandbox_violations 计数,security_score = max(0, 100 - alerts*10 - denied*5)
6. **实现 compute_all_metrics** — `backend/private_agent/eval/metrics.py:108-120` 调用上述四个函数,返回 `{task_completion, tool_calls, efficiency, security}` 四键 dict
7. **新建 judge.py LLMJudge + 工厂函数** — `backend/private_agent/eval/judge.py:1-90`:
   - `build_judge_adapter(cfg)`:读 `cfg["eval"]["judge_model"]`(glm-4-flash),复用 `build_compress_adapter` 模式构造 GlmAdapter(读 PA_GLM_API_KEY + cfg.models.providers.glm.base_url)
   - `load_judge_prompt(cfg)`:读 `cfg["eval"]["judge_prompt_dir"]/general.md`,返回模板字符串
   - `LLMJudge.__init__(adapter, prompt_template)`:存适配器与模板
   - `LLMJudge.judge(user_input, agent_response, expected_output)`:用 str.replace 填充三模板变量 → 调 adapter.chat → 解析 JSON(先提取 ```json``` 块,再整段 json.loads,失败返回 0 分 + reason="judge_parse_error")→ 返回 {response_quality, task_completion, quality_reason, completion_reason}
8. **新建 hybrid_eval.py HybridEvaluator** — `backend/private_agent/eval/hybrid_eval.py:1-50`:
   - `HybridEvaluator.__init__(judge: LLMJudge)`:存 judge
   - `evaluate_sample(sample, actual_output, actual_events)`:调 compute_all_metrics(同步) + judge.judge(异步,try/except 降级返回 0 分)→ 返回 `{sample_id, actual_output, actual_events, metrics: {task_completion, tool_calls, efficiency, security, llm_judge}}`
9. **新建 test_eval_metrics.py** — `backend/tests/test_eval_metrics.py:1-200` 覆盖 AC-1..AC-5:
   - test_task_completion_normal:3 关键词命中 2 个,rate=0.667
   - test_task_completion_empty_keywords:空列表返回 1.0
   - test_task_completion_all_matched:全命中返回 1.0
   - test_tool_calls_normal:工具选择 + 顺序 + 参数三维度
   - test_tool_calls_param_fuzzy_match:str in str 模糊匹配
   - test_tool_calls_param_exact_match:精确匹配
   - test_tool_calls_empty_actual:actual_events 为空,三维度均 0
   - test_efficiency_normal:统计 turns/tokens/cost/duration
   - test_efficiency_missing_tokens:tokens 缺失按 0
   - test_security_normal:3 类 subtype 计数 + score 计算
   - test_security_no_events:无安全事件,score=100
   - test_compute_all_metrics:四键齐全
10. **新建 test_eval_judge.py** — `backend/tests/test_eval_judge.py:1-120` 覆盖 AC-6..AC-8:
    - test_build_judge_adapter:cfg 读 judge_model 构造 GlmAdapter,model_name=glm-4-flash
    - test_load_judge_prompt:加载 general.md,含三模板变量
    - test_judge_normal:mock adapter 返回合法 JSON,解析成功
    - test_judge_markdown_wrapped:mock adapter 返回 ```json``` 包裹 JSON,解析成功
    - test_judge_parse_error:mock adapter 返回非 JSON,降级返回 0 分 + reason="judge_parse_error"
    - test_judge_call_failed:mock adapter 抛 ProviderError,降级返回 0 分 + reason="judge_call_failed"
11. **新建 test_eval_hybrid.py** — `backend/tests/test_eval_hybrid.py:1-80` 覆盖 AC-9:
    - test_evaluate_sample_normal:规则指标 + Judge 均成功,五键齐全
    - test_evaluate_sample_judge_failed:Judge 降级,llm_judge 返回 0 分,其余四类正常
    - test_evaluate_sample_empty_events:actual_events 为空,规则指标返回零值,Judge 正常

#### Workspace setup

- 实施前运行 `git status --short` 和 `git branch --show-current`
- 当前 working tree 含 2 个非本次改动的 dirty 文件(`config/loader.py`、`code_execution.py`,仅 CRLF/LF 行尾差异),不混入本次 plan
- 当前分支为 `master`,推荐创建 worktree:`git worktree add -b codex/m4-metrics-judge ../private-agent-m4-metrics-judge`
- 如用户选择在 master 直接实施,需在 commit 时只 add m4-metrics-judge 相关文件

#### Open questions (留给后续)

- 无(spec 已 ALIGNED,Open questions 为空)

---

### Architect challenge

#### Steelman against favored option

针对 Option A(三模块分离),最强反驳:

**反方核心论点**:hybrid_eval.py 仅含一个类 HybridEvaluator + 一个方法 evaluate_sample,独立文件过度拆分。HybridEvaluator 的核心逻辑就是"调 compute_all_metrics + 调 judge.judge + 合并 dict",逻辑量约 15 行,放 judge.py 底部或 metrics.py 底部更紧凑,避免文件碎片化。

**如果反驳成立,plan 应改成什么样**:把 HybridEvaluator 放入 judge.py(因它依赖 LLMJudge),metrics.py 保持纯函数。这样源文件从 3 个降为 2 个,测试从 3 个降为 2 个(test_eval_metrics.py + test_eval_judge.py 含 HybridEvaluator 测试)。

**Planner 反驳**:spec In scope E 段明确"新建 hybrid_eval.py",且 HybridEvaluator 未来在 m4-eval-runner-replay spec 中会被 EvalRunner 调用,独立文件便于后续 spec 引用与测试隔离。15 行的类独立成文件在 Python 项目中常见(如 fastapi 的 dependency_overrides.py),不算过度拆分。维持 Option A。

#### Tradeoff tensions

1. **文件粒度 vs 职责纯度**:Option A 三文件职责最纯但文件最多;Option B 两文件更紧凑但混合纯函数与编排器。Planner 取舍:优先职责纯度,因 metrics.py 纯函数无外部依赖(仅 typing import ExpectedTrace),未来可独立复用(如 m4-eval-runner-replay 的 ReplayExecutor 可能直接调 compute_all_metrics 而不经 HybridEvaluator)。文件粒度增加的成本(3 个 import 语句)远低于职责混合的成本。

2. **Judge 同步 vs 异步**:spec 要求 `LLMJudge.judge()` 是 async(调 GLM-4-Flash),但 `compute_all_metrics()` 是同步纯函数。HybridEvaluator.evaluate_sample() 需 async(async judge) + 同步(metrics)混合。tension:同步纯函数在 async 函数中直接调用会阻塞事件循环。Planner 取舍:metrics 纯函数计算量极小(遍历 list + 字符串匹配),阻塞可忽略(<1ms),不需 asyncio.to_thread。仅 Judge 调用走 async。

#### Synthesis path

无 — Option A 已是综合后的最优解。Architect 的 steelman 未推翻 Option A,仅提出文件粒度质疑,Planner 已反驳。

#### Principle violations

无违反项。Option A 与 4 条 Principles 一致:
- 最小代码:直接落地蓝图代码样例,无抽象基类
- 不擅自扩:不实现 spec Out of scope 项
- 复用基础设施:EvalSample/GlmAdapter/general.md 全复用
- Judge 降级不阻塞:try/except 降级返回 0 分

---

### Critic verdict

| 维度 | 状态 | 备注 |
|---|---|---|
| Principle consistency | ✓ | Option A 与 4 条 Principles 一致,无矛盾 |
| Alternative exploration | ✓ | Option A/B 为真候选,Option B 有明确 invalidation rationale(职责混合 + 循环引用隐患) |
| Risk mitigation clarity | ✓ | 4 条 risk 各对应 1 行 mitigation,无"以后再说" |
| AC testability | ✓ | 10 条 AC 均二值可验证,每条 AC 映射到具体测试函数名 |
| Verification concreteness | ✓ | 验证步骤含具体 pytest 命令 + 测试文件名 + 期望通过数 |
| File/line coverage | ✓ | 11 步实施步骤全部 cite 具体文件路径 + 行号区间,覆盖率 100% |

### Verdict: APPROVED

### Reservations

1. **Implementation step 7 `judge.py` 的 JSON 解析容错** — spec Solution 段提到"先提取 ```json ... ``` 块 → json.loads → 失败则尝试整段 json.loads → 仍失败返回 0 分"。我有保留:GLM-4-Flash 在 temperature=0.1 下仍可能输出带前后缀的非标准 JSON(如"评分结果:\n{...}"),仅提取 ```json``` 块 + 整段 json.loads 两步可能不足。Mitigation:实施时增加第三步——用正则提取第一个 `{` 到最后一个 `}` 的子串再 json.loads。此改进在 dev-tdd 阶段通过 test_judge_markdown_wrapped + test_judge_parse_error 红绿验证后决定是否需要。

2. **Implementation step 4 `evaluate_efficiency` 的 duration_seconds 计算** — spec 说"最后事件 created_at - 首事件 created_at",但 actual_events 的格式假设(spec Assumptions)是 `{"event_type": ..., "timestamp": ...}`。我有保留:events 中的时间字段名是 `timestamp` 还是 `created_at`?react_events 表用 `created_at`,但 events 传入是 list[dict] 不一定带数据库列名。Mitigation:实施时统一用 `event.get("timestamp") or event.get("created_at")`,兼容两种字段名,并在 test_efficiency_normal 中同时测试两种字段名。

---

## Implementation steps (final)

(同 Planner draft 的 11 步,已合并 Architect/Critic 改进)

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| Judge 模型输出非标准 JSON(带前后缀/markdown 包裹) | 三步解析:```json``` 块提取 → 整段 json.loads → 正则提取 `{...}` 子串 json.loads → 仍失败返回 0 分 + reason="judge_parse_error" |
| Judge 模型调用失败(网络/超时/API 错误) | try/except 捕获 ProviderError + Exception,降级返回 0 分 + reason="judge_call_failed",不阻塞评估流程 |
| actual_events 时间字段名不统一(timestamp vs created_at) | evaluate_efficiency 用 `event.get("timestamp") or event.get("created_at")` 兼容两种字段名 |
| 安全事件 event_type 受 react_events CHECK 约束限制 | evaluate_security 从 `event_type="error"` + `payload.subtype` 读取,不新增 event_type 值(与 m4-eval-foundation schema 对齐) |
| Judge 模型与主模型同名导致自评偏见 | build_judge_adapter 固定读 cfg["eval"]["judge_model"](glm-4-flash),不读 cfg["models"]["default"] |

## Verification steps

- 验证 AC-1..AC-5:`pytest tests/test_eval_metrics.py -v`(期望 12 个测试全过)
- 验证 AC-6..AC-8:`pytest tests/test_eval_judge.py -v`(期望 6 个测试全过)
- 验证 AC-9:`pytest tests/test_eval_hybrid.py -v`(期望 3 个测试全过)
- 验证 AC-10:上述三测试文件合计 21 个测试,覆盖空输入/边界/正常/Judge 降级路径
- 全量回归:`pytest tests/ -q`(期望无新增回归,预先存在的 PA_DB_PASSWORD 环境问题 6 个失败可接受)
- 闭环检查:`grep -rn "evaluate_task_completion\|evaluate_tool_calls\|evaluate_efficiency\|evaluate_security\|compute_all_metrics\|LLMJudge\|HybridEvaluator\|build_judge_adapter\|load_judge_prompt" backend/` 确认新公开符号有调用者(test 或 hybrid_eval.py)

## ADR

- **Decision**: 采用 Option A 三模块分离(metrics.py + judge.py + hybrid_eval.py),五类指标为纯函数,LLMJudge 独立类,HybridEvaluator 编排规则指标 + LLM-Judge
- **Drivers**: 蓝图代码样例可直接落地(决定性)、测试可验证性(决定性)、与 m4-eval-foundation 一致性(辅助)
- **Alternatives considered**:
  - Option A(三模块分离):chosen — 职责纯度最高,与 spec In scope 三段一一对应,metrics 纯函数可独立复用
  - Option B(calculators.py 合并 metrics + hybrid):rejected — 职责混合纯函数与编排器,hybrid 依赖 judge 造成文件间循环引用隐患
- **Why chosen**: 三模块分离让 metrics.py 零外部依赖(仅 typing import),未来 m4-eval-runner-replay 的 ReplayExecutor 可直接调 compute_all_metrics 而不引入 LLMJudge 依赖;职责纯度优先于文件粒度紧凑性
- **Consequences**:
  - 正面:metrics.py 可独立测试与复用;judge.py 隔离 LLM 调用复杂度;hybrid_eval.py 编排逻辑清晰
  - 负面:3 个源文件 + 3 个测试文件,import 语句稍多;hybrid_eval.py 仅一个类,文件较小
  - 约束:后续 m4-eval-runner-replay spec 调用 HybridEvaluator 时需 import 两个模块(hybrid_eval + judge)
- **Follow-ups**:
  - m4-eval-runner-replay spec 实现 EvalRunner 时,需调用 HybridEvaluator.evaluate_sample 并将结果通过 EvalRunRepo.update_run_metrics 入库
  - V2 可考虑综合评分 + 权重配置(spec Out of scope,未来迭代)

## Review trail

- Planner draft v1: 11 步实施步骤,Option A 三模块分离,4 条 Principles + 3 条 Decision drivers
- Architect challenge v1: steelman 质疑 hybrid_eval.py 文件粒度,Planner 反驳(spec 明确要求 + 后续 spec 引用);2 条 tradeoff tensions(文件粒度 vs 职责纯度 / 同步 vs 异步)
- Critic verdict v1: APPROVED — 6 维度全 ✓,2 条 Reservations(JSON 解析容错可加第三步正则 / duration_seconds 字段名兼容)
- Final iterations: 1 / 3
