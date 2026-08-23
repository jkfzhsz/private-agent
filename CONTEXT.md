# Context

## Glossary

| Term | Meaning | Notes |
|---|---|---|
| Skill | 场景化能力包,含 manifest + system_prompt + 工具白名单 + 少样本;会话创建时锁定 | 蓝图 §7.1;`skills` 表存储运行时副本 |
| SkillManifest | Skill 元数据值对象,对应 `skill.yaml` 解析结果 | 蓝图 §7.2;含 dependencies/permissions/prompt_vars/knowledge_base/examples |
| ToolDependency | Skill 声明的工具依赖,含 name + safety_level_override + enabled | 蓝图 §7.2/§7.5;白名单矩阵的最小单元 |
| SkillLoader | Skill 加载服务,PG db_first + 文件系统回退 | 蓝图 §7.4;config `skills.storage.runtime_source` |
| SkillManager | Skill 激活服务,负责模板替换+少样本+白名单+Frozen Zone+会话锁定 | 蓝图 §7.3/§7.4 |
| ExampleLoader | 少样本加载服务,读 `examples/*.md` 并按 token 预算截断 | 蓝图 §7.7;注入 Frozen Zone |
| 会话锁定 (Session Lock) | 会话创建时锁定 Skill 版本,运行中不允许切换 | 蓝图 §7.3;sessions 表 locked_skill_name/version/frozen_hash |
| Frozen Zone | 上下文工程中 hash 固化的前缀区(system_prompt + tools + examples) | 蓝图 §3.2/§7.3;KV Cache prefix,变更导致全 miss |
| Frozen Hash | Frozen Zone 内容的 hash,用于会话锁定与完整性校验 | 蓝图 §3.4/§7.3;存 sessions.frozen_hash |
| compress_adapter | 记忆提取使用的 LLM 压缩模型适配器,复用 `models.compress_model` | 蓝图 §4.2/§3.11;P0.1 修复项 |
| EvalSample | 评估样本值对象,含 sample_id/scenario/skill_name/skill_version/case_type/difficulty/split/input/expected_react_trace/expected_output | 蓝图 §8.3;Pydantic 模型,入库前校验 |
| ExpectedTrace | 期望 ReAct 行为轨迹,含 tool_calls[] + expected_output_contains[] | 蓝图 §8.3;EvalSample 的字段,JSONB CHECK 约束 |
| ExpectedToolCall | 期望工具调用,含 tool/args/expected_result_type | 蓝图 §8.3;ExpectedTrace 的字段 |
| EvalRun | 评估运行记录,含 skill_version/model_id/eval_mode/mock_enabled/metrics/sample_results | 蓝图 §8.11;eval_runs 表 |
| 离线批量评估 (Offline Eval) | 仅调模型不执行工具,速度快,适合 CI 回归 | 蓝图 §8.2;actual_events=[] |
| 交互式回放 (Replay Eval) | 完整 ReAct 循环 + Mock 模式,精度高 | 蓝图 §8.2/§8.10;actual_events 含 tool_call/tool_result |
| Mock 模式 (Mock Mode) | 工具调用返回预设结果,加速批量评测 | 蓝图 §8.10;MockToolRegistry 替换 handler,mock_data 按 sample_id+tool_name 索引 |
| LLM-as-Judge | GLM-4-Flash 评判响应质量(1-5)+任务完成度(1-5),与主模型分离 | 蓝图 §8.8;规避同模型自评偏见 |
| 五类指标 (Five Metric Categories) | 任务完成率/工具调用准确率/LLM-Judge/效率/安全,独立展示不做综合评分 | 蓝图 §8.5;MVP 不做权重综合 |
| 版本对比 (Version Compare) | 同模型+同 Skill 最新成功基线双维度筛选,差值标记 improved/degraded/stable | 蓝图 §8.12;EvalComparator |
| 回滚 (Rollback) | Prompt 独立回滚 + Skill 完整回滚 + Harness 手动 git revert,仅对新会话生效 | 蓝图 §8.14;运行中会话维持锁定版本 |
| 退化告警 (Degradation Alert) | 评估指标退化时 UI 告警 + eval_runs 记录,不自动阻断发布 | 蓝图 §8.13;降低单人开发流程门槛 |
| 持续进化闭环 (Continuous Evolution) | 评估→低分提取→人工审核→入库→再评估的闭环 | 蓝图 §8.16;WeakSampleExtractor + ReviewQueueRepo |
| 两类筛选标准 | 模型能力限制丢弃(model_limitation_drop)/Prompt 缺陷编辑后入库(prompt_defect_edit) | 蓝图 §8.16;人工审核决策 |
