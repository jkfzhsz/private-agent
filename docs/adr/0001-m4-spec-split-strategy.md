# ADR 0001: M4 评估闭环 spec 切分策略

- Status: Accepted
- Date: 2026-08-01
- Decision Maker: user
- Phase: M4 评估闭环 (蓝图 §8.1-8.16)

## Context

M4 评估闭环阶段需覆盖蓝图 §8.1-8.16 共 16 个章节,涉及评估环境/数据集/五类指标/LLM-Judge/执行流程/交互式回放/Mock 模式/版本对比/回滚/退化告警/A-B 预留/持续进化等多个子系统。用户初始建议切分为 7 个独立 spec(m4-eval-datasets / m4-metrics / m4-interactive-replay / m4-version-compare / m4-iteration-loop / m4-rollback / m4-continuous-evolution),每个 spec 独立走 dev-plan → dev-tdd → dev-verify → dev-code-review → dev-finish 流程。

切分策略决定整个 M4 工作流结构和依赖顺序,错误切分会导致:
- spec 间循环依赖(无法独立 dev-plan)
- 重复劳动(同一基础设施在多个 spec 中重复实现)
- spec 粒度过细(7 个 spec × 5 步流程 = 35 次开发循环,单人开发成本过高)

## Decision

合并为 **5 个 spec**,按依赖顺序执行:

```
m4-eval-foundation  (§8.2-8.4, 8.11)
  ├─ eval_datasets.split 列 + CHECK + Pydantic 校验
  ├─ eval_datasets/eval_runs/version_snapshots Python 仓储层
  ├─ ExampleLoader.load_test_set() + 种子样本(3场景×4条=12条)
  ├─ judge_prompts/ 目录 + 通用 prompt 模板
  └─ DB migration + repo 单测
       ↓
m4-metrics-judge  (§8.5-8.8)
  ├─ 五类指标计算器(纯函数)
  ├─ LLM-as-Judge (build_judge_adapter + GLM-4-Flash)
  ├─ 混合评判流程
  └─ 指标单测(含 mock events)
       ↓
m4-eval-runner-replay  (§8.9-8.10)
  ├─ EvalRunner(离线批量 + 交互式回放)
  ├─ ReplayExecutor + Mock 模式(MockToolRegistry)
  ├─ ContextManager 扩展(reload + replace_frozen_zone)
  ├─ SkillLoader.load_version() 扩展
  ├─ 版本变更自动触发(SkillVersionListener)
  └─ 端到端评估测试
       ↓
m4-version-compare-rollback  (§8.12-8.15)
  ├─ EvalComparator(双维度筛选 + 差值)
  ├─ 回滚机制(Prompt/Skill/Harness)
  ├─ 退化告警(仅 UI + eval_runs,不阻断)
  ├─ A/B 预留(variant 字段)
  └─ eval API 端点 + 前端评估面板
       ↓
m4-continuous-evolution  (§8.16)
  ├─ WeakSampleExtractor(低分案例提取)
  ├─ 人工审核队列 + 两类筛选标准
  └─ 持续进化闭环串联测试
```

### 合并依据

1. **m4-rollback 并入 m4-version-compare-rollback**:回滚是迭代闭环(§8.13)的退化分支,且版本对比(§8.12)与回滚(§8.14)共享 version_snapshots 仓储层,合并后 spec 内聚。

2. **m4-continuous-evolution 独立保留**:低分案例提取依赖 m4-metrics 的指标结构 + m4-version-compare 的 eval_runs 记录,且人工审核队列是独立子系统,不与回滚/版本对比耦合。

3. **m4-iteration-loop 内容分散到 m4-version-compare-rollback**:迭代闭环的本质是"评估→对比→回滚/优化→再评估",其执行逻辑由 EvalRunner + EvalComparator + SkillRollbackManager 三个类协作完成,已在 m4-version-compare-rollback spec 中覆盖,无需独立 spec。

## Alternatives Considered

### Alternative A: 7 个独立 spec(用户初始建议)
- 优点:粒度细,每个 spec 范围小,dev-tdd 循环快
- 缺点:
  - m4-rollback 与 m4-iteration-loop 高度耦合,独立 dev-plan 会出现循环依赖
  - 7 spec × 5 步 = 35 次开发循环,单人开发成本过高
  - version_snapshots 仓储层在 m4-rollback 和 m4-version-compare 中重复需求
- 否决理由:耦合度高 + 开销大

### Alternative B: 3 个粗粒度 spec(foundation / execution / iteration)
- 优点:开发循环少(3 × 5 = 15 次),依赖关系清晰
- 缺点:
  - execution spec 过大(含指标+执行+回放+版本对比+回滚),dev-plan 复杂度高
  - 单 spec 代码量过大,dev-tdd 阶段测试覆盖难管理
- 否决理由:单 spec 过大,违反"spec 应独立可 plan"原则

### Alternative C: 5 个 spec(本 Decision)
- 优点:
  - 依赖链线性(foundation → metrics → runner-replay → compare-rollback → evolution)
  - 每个 spec 内聚度高,dev-plan 边界清晰
  - 开发循环适中(5 × 5 = 25 次)
  - 每个 spec 对应蓝图 2-4 个章节,范围可控
- 缺点:m4-version-compare-rollback spec 略大(4 章节),但版本对比/回滚/退化告警/A-B 预留共享 version_snapshots 与 eval_runs,内聚合理
- 采纳理由:依赖线性 + 内聚合理 + 开销适中

## Consequences

### Positive
- 依赖链线性,可按顺序串行开发,无需并行协调
- 每个 spec 独立 dev-plan,无循环依赖
- version_snapshots 仓储层在 m4-eval-foundation 一次实现,后续 spec 复用
- 5 个 spec 的代码量均衡,dev-tdd 测试管理可控

### Negative
- m4-version-compare-rollback spec 含 4 章节,dev-plan 步骤可能略多(预计 30+ 步)
- 前置 spec 未完成时后续 spec 无法启动(foundation 必须先完成)

### Neutral
- m4-eval-foundation 的仓储层接口需在 dev-plan 阶段明确定义,避免后续 spec 接口不匹配

## Compliance

- 5 个 spec 文件已创建于 `.claude/artifacts/designs/`:
  - m4-eval-foundation.md
  - m4-metrics-judge.md
  - m4-eval-runner-replay.md
  - m4-version-compare-rollback.md
  - m4-continuous-evolution.md
- 每个 spec 含 In scope / Out of scope / AC / Open questions,满足 dev-grill-docs 产物要求
- 依赖顺序在 spec Background 章节明确标注
