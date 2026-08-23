# Agent 开始"自我进化"：会出题、会反思，还会自己长出新技能

> **来源**：腾讯技术工程（微信公众号）
> **作者**：horacebao、ashexie
> **发布时间**：2026年7月27日 17:27（广东）
> **原文链接**：https://mp.weixin.qq.com/s/fsVJiorPBN4ylGjUYBcIPw

---

> **引言**：当 Agent 自己会出题、自己会答题、还能把答错的经验沉淀成下一次的"技能包"——一个永远在长大的 Agent，到底能走多远？
>
> 十几篇论文反复啃读，从"存技能"到"训技能"，再到"零数据自训"，我们终于摸清了自进化 Agent 这条赛道的脉络。本文将带你深入 Agent 的"经验大脑"——从把经验写进文件，到把经验训进权重，再到完全无人工数据的自我循环，一步步拆解研究路径。

一个完全自主、越用越强的 Agent，有可能实现吗？本文聚合了现有的前沿探索工作，向大家展现这一方向上的最新成果。文章覆盖自进化 Agent 的三大技术路线、代表工作详解、横向对比、关键洞察等话题。

---

## 01 自进化 Agent 介绍

### 1.1 什么是自进化 Agent？

**自进化 Agent**（Self-Evolving Agent）指的是一类能够在与环境/用户交互过程中**自动积累经验、提炼能力、并在后续任务中复用与提升**的智能体。简单来讲：让 Agent 自己越用越聪明，而不是每次都靠人工去喂数据、调提示词、改模型。

它的核心诉求可以拆成三件事：

- **能存**：把交互过程中沉淀下来的有价值的东西（成功模式、失败教训、可迁移技能）存下来；
- **能用**：在新任务中能把之前存下来的东西检索/调用/内化进自己的决策；
- **能进化**：经验本身是动态的，能更新、能合并、能淘汰，避免越攒越乱。

### 1.2 为什么这件事现在被重视起来了？

大模型本身的几个老大难问题：

- **静态知识**：训练完成那一刻起，模型对世界的认知就被冻结了；
- **上下文有限**：再长的上下文窗口也总有边界，多轮交互终究"断片"；
- **重复犯错**：今天教会它的事，明天还会再犯一遍；
- **训练贵**：每次想让模型变强一点，要么 SFT 要么 RL，都得重新跑一遍。

自进化 Agent 想要绕开这些限制：**让经验本身成为模型能力的延伸或更新通道**。在大模型时代被重新点燃的本质原因：

- LLM 本身具备总结归纳的能力（自己能给自己写笔记了）；
- Agent 形态的产品越来越多，长程交互场景终于有了真实需求；
- 高质量人工标注数据越来越贵，社区开始探索"少人工 / 无人工"路线。

### 1.3 三大技术路线一览

| 路线 | 是否更新模型权重 | 是否依赖人工数据 | 代表工作 |
|------|-----------------|-----------------|---------|
| **第一类：经验/Skill 存储型** | ❌ 不更新 | ✅ 依赖 | AutoSkill、EvoSkill、MemSkill、CoEvoSkills、SE-Agent、Hermes |
| **第二类：RL 训练型** | ✅ 更新 | ✅ 依赖 | EvolveR、SAGE、SkillRL、SKILL0、SkillOS、AgentEvolver |
| **第三类：0 数据自学型** | ✅ 更新 | ❌ 不依赖 | Agent0、Tool-R0、Absolute Zero |

简单理解三类工作的差异：

- **第一类**：给 Agent 配了一本"工作笔记"，模型本身不动，只在需要时翻阅；
- **第二类**：直接把"工作笔记"上的经验通过 RL 写进模型权重，让 Agent 真正"长本事"；
- **第三类**：更激进——连"老师"都不要了，让 Agent 之间互相出题互相考试，自己跟自己打。

---

## 02 第一类：经验/Skill 存储型（不更新模型权重）

**特征**：不训练、跨会话保留上下文、文件式存储；核心是把经验沉淀为可检索/可复用的"技能（Skill）"。

### 2.1 AutoSkill（arXiv: 2603.01145）

**TLDR**：基础款，动态增删改查 Skill 来防止 Skill 库爆棚。

整体设计是经典的**双环结构**：

- **左环——在线服务（用 Skill）**：查询重写 → 混合技能检索（Embedding + BM25）→ 技能注入生成
- **右环——技能进化循环（更新 Skill）**：技能提取 → 候选技能管理（Add/Merge/Discard）→ 版本化合并

评估数据集是 WildChat-1M，但**没有具体性能指标**。

### 2.2 EvoSkill（arXiv: 2603.02766）

**TLDR**：让多个 Agent 分工协作——一个执行、一个反思、一个落地——把"失败"变成"新 Skill"，并用 Pareto 前沿机制保证 Skill 库永远精而不滥。

**三 Agent 分工**：

- **Executor Agent（执行者）**：拿当前 Skill 库去跑任务，把失败案例完整记录下来；
- **Proposer Agent（反思者）**：做根因分析，决定新建还是修改 Skill；
- **SkillBuilder Agent（落地者）**：把提案变成结构化 Skill 文件夹，并做单元化校验。

**核心机制——Pareto Frontier 精英池**：新 Skill 只有在至少一个维度上严格优于现有 Skill 才会进入精英池。

**评估亮点**：

- **OfficeQA 主任务**：基线准确率 60.6% → 进化后 67.9%（+7.3pp）
- **跨任务迁移**：在 SealQA 学到的 *search persistence protocol* 迁移到 BrowseComp 任务，无需重新训练带来 +5.3pp 提升

### 2.3 MemSkill（arXiv: 2602.02474）

**TLDR**：只针对**操作 Memory 的 Skill** 做自进化。

组件拆解：

- **Retriever**：基于 Qwen0.6B Emb 的相似度计算
- **Controller**：MLP 结构，**接受 RL 训练**（这一类里少见的"有训练"环节）
- **Executor Designer Base LLM**：全部冻住

**亮点——Transfer Evaluation**：

- LLaMA 上训练的 Controller & Skill 迁移到 Qwen 上仍然有效；
- LoCoMo 上训练的迁移到 LongMemEval 上仍然有效。

### 2.4 CoEvoSkills（arXiv: 2604.01687v2）

**TLDR**：给每条新总结的 Skill 配一个"考官"（Verifier），生成的 Skill 必须先通过考试才能进库——把软件工程的"单元测试"理念搬到了 Skill 进化里。

**核心组件——Generator + Verifier 双子星**：

- **Skill Generator**：从执行轨迹提炼候选 Skill，同步生成对应单元测试；
- **Skill Surrogate Verifier**：在隔离 sandbox 环境里跑 Skill 与单元测试，返回结构化验证反馈；
- **Co-Evolution Iteration**：Skill 与 Test 同时进化。

**两阶段验证**：Surrogate 验证（廉价）→ Oracle 验证（昂贵但权威，用 Claude Code / CodeX 跑端到端任务）。

**亮点结论——Self-evo 优于 Cross-model Transfer**：

- **self-evo**：Opus 4.6 从 30.6% → 71.1%（+40.5）；GPT-5.2 从 29.6% → 69.8%（+40.2）
- **cross-model transfer**：绝对值明显低于 self-evo（如 Mistral Large 3 才到 43.1%）

> 含义：Skill 跟模型本身的"风格"是耦合的。

### 2.5 SE-Agent（arXiv: 2508.02085）

**TLDR**：一次跑出多条轨迹，让它们之间互相借鉴、互相打磨——从"单线程深度修补"切换到"多线程横向融合"。

（来自 OPPO OpenSearch-AGI 团队和复旦大学等机构，收录于 NeurIPS 2025）

**完整五阶段流程**：

1. **多策略轨迹生成**：用不同"性格"采样 N 条轨迹（P-greedy、P-tests-first、P-linter-aware、P-defensive、P-minimal）
2. **反思修订**：对每条轨迹独立做传统 self-refine
3. **质量过滤**：用评分函数 `Reward(t,T) = α·TaskCompletion(t) + β·ReasoningQuality(t) + γ·Efficiency(t)` 从 10 条砍到 5 条
4. **跨轨迹重组（⭐核心创新）**：Crossover（交叉）、Transfer（迁移）、Restructure（重构）
5. **最终方案选取**：从 10 个候选中选最高分输出，可迭代多轮（N=4 时收敛）

**评估**：SWE-Bench Verified 上最高实现 **+55% 相对改善**。

### 2.6 第一类总结

- **核心点 1**：看似不训练，其实仍要训练数据；
- **核心点 2**：核心是"存下经验/Skill"；
- **核心点 3**：总结这一步被严重低估，针对总结去优化的工作几乎没有；
- **核心点 4**：横向（多次采样）vs 纵向（历史对话）总结的区分。

---

## 03 第二类：基于 RL 的训练型自进化（重点）

**特征**：通过 RL 训练直接更新模型权重，让模型从根本上变强。这是当前学术界与工业界的主流方向。

### 3.1 EvolveR（arXiv: 2510.16079）

**TLDR**：两阶段——离线阶段提炼策略原则入原则库；在线阶段实时检索原则指导行动。

**Reward 设计**：最终结果 + 格式 reward
**评估**：Natural Questions、HotpotQA、TriviaQA、PopQA

### 3.2 SAGE（arXiv: 2512.17102）

**TLDR**：提出 **Sequential Rollout** —— RL rollout 时序列化跑一系列相似任务，后序任务训练时可使用前序生成的 skill。

除了任务完成结果奖励，还设计了 **Skill-integrated Reward** 专门激励技能的生成和调用。评估数据集：AppWorld。

### 3.3 SkillRL（arXiv: 2602.08234）⭐

**核心主张**：用强模型（o3）蒸馏 Skill，再通过 RL 训练弱模型学会使用，并递归进化技能库。

**角色配置**：

| 角色 | 配置 | 训练题目来源 |
|------|------|-------------|
| 出题者 | 无独立出题者，直接使用数据集 | 官方数据集训练集（ALFWorld 7,500 条 SFT、WebShop 2,400 条 SFT、7 个搜索 QA 数据集） |
| 解题者 | Qwen2.5-7B-Instruct ✅ 训练（Cold-start SFT → GRPO RL） | — |
| Skill 总结者 | OpenAI o3 ❌ 不训练 | — |

**主要实验结果**：

| 基准 | SkillRL | GRPO 基线 | 提升 |
|------|---------|-----------|------|
| ALFWorld | 89.9% | 77.6% | +12.3% |
| WebShop SR | 72.7% | 66.1% | +6.6% |
| Search-QA avg | 47.1% | ~38.5% | +8.6% |

Skill 库增长：55 → 100 条。

**核心设计哲学**：强模型提炼知识，弱模型通过 RL 学会使用知识。

> 评论：倾向于归类为**蒸馏**，而非真正的**进化**。

### 3.4 SKILL0（arXiv: 2604.02268）⭐

**核心主张**：将 Skill 从推理时的"外挂上下文"内化到模型参数，实现零样本执行（每步 < 0.5K tokens）。

**三阶段渐进课程**：

| 阶段 | Skill 数量 | 目标 |
|------|-----------|------|
| Stage 1 | 6 条 | 学会调用 |
| Stage 2 | 3 条 | 减少依赖 |
| Stage 3 | 0 条 | 完全内化 |

**核心设计哲学**：从"使用技能"到"内化技能"的范式转变。

### 3.5 SkillOS（arXiv: 2605.06614）⭐⭐

**核心主张**：训练一个专门的 **Curator**，通过 RL 学会如何**增/改/删 SkillRepo**，而不是直接学如何使用 Skill。

**角色配置**：

| 角色 | 配置 |
|------|------|
| 出题者 | Gemini-2.5-Pro ❌ 不训练，仅离线标注技能属性标签 |
| 解题者 | Executor ❌ 冻结不训练（训练时用 Qwen3-8B） |
| Skill 总结者 | **Qwen3-8B Curator ✅ GRPO RL 训练** |

**两个极其重要的结论**：

> **结论 1**：训练过的小模型总结者 > 冻结的大模型总结者（RL 训练的 Qwen3-8B 超过冻结的 Gemini-2.5-Pro）
>
> **结论 2**：不动解题者，性能也能涨（仅训练 Curator、解题者完全冻结，ALFWorld 上仍能长足进步）

### 3.6 AgentEvolver（arXiv: 2511.10395）⭐

**核心主张**：完全自主的三环自演化框架——**自出题、自解题、自总结**经验，全链路无需人工标注。

**Self-Questioning 四步流程**：

1. **探索**：高温 LLM 广度优先 + 深度优先探索环境
2. **合成**：从探索轨迹蒸馏 + 用户偏好约束 → 生成任务与参考解
3. **筛选**：词法去重 + 语义相似度 + 可行性验证
4. **混合（可选）**

**特点**：出题与解题使用同一个 Qwen2.5-7B/14B，RL 训练后形成**出题质量与解题能力的双重提升**。

### 3.7 第二类总结

- **核心点 1**：仍然依赖训练集反馈（除 AgentEvolver 外）；
- **核心点 2**：核心是 RL rollout 时继承/更新之前的 skill；
- **核心点 3**：不算严格意义的"RL by talking"——反馈仍来自任务结果或人工标签。

---

## 04 第三类：0 数据自学型

**特征**：完全不要人工标注数据，靠 Agent 之间互相出题/解题闭环。

### 4.1 Agent0（arXiv: 2511.16043）

- **Curriculum Agent (RL)**：出题，reward = 答题 Agent 的不确定性 + 工具使用频率
- **Executor Agent (RL)**：解题，reward = 解题成功率

**流程**：出题 Agent 先 RL 训练（答题 Agent 冻住作为 reward model）→ 出题 Agent 冻住，给答题 Agent 出题做 RL 训练。

**评估集**：GSM8K、AIME 等数学类。

### 4.2 Tool-R0（arXiv: 2602.21320）

- **Generator Agent**：格式 reward + 合法性 reward + 难度 reward
- **Solver Agent**：格式 reward + 准确性 reward

**评估集**：ToolAlpaca、SealTool、NexusRaven。

### 4.3 Absolute Zero（arXiv: 2505.03335）

**TLDR**：单个模型同时扮演出题人和解题人，用**代码执行器**作为唯一验证来源，完全不碰任何外部数据。

**出题流程**：题目为 [输入, 代码, 输出] 三元组，随机删除一个让答题 Agent 猜测，**以代码执行器为最终判断标准**。

**评估集**：

- 代码：HumanEval、MBPP、LCB
- 数学：AIME24、AIME25、AMC、MATH-500、Minerva、Olympiad

### 4.4 第三类总结

- **核心点 1**：全靠出题的 Agent 自己；
- **核心点 2**：准确率判断大多靠对照出题 Agent 自己给出的答案（sliver answer 可靠性存疑）；
- **核心点 3**：最好要有自动化的判断标准（如代码执行器）；
- **核心点 4**：出题难度很重要，reward shaping 是核心难点；
- **核心点 5**：评估很混乱，可比性差。

---

## 05 横向对比：四篇代表工作的"三角色"视角

### 5.1 核心对比表

| 论文 | 训练题目来源 | 出题者 | 解题者（训练？） | Skill/Experience 总结者（训练？） |
|------|-------------|--------|----------------|--------------------------------|
| SkillRL | 官方数据集训练集 | — | Qwen2.5-7B ✅ | OpenAI o3 ❌ |
| SKILL0 | 官方数据集训练集 | — | Qwen2.5-VL-3B/7B ✅ | OpenAI o3（继承）❌ |
| SkillOS | 官方数据集训练集 | Gemini-2.5-Pro（仅离线分组，❌） | Executor 冻结 ❌ | **Qwen3-8B Curator ✅** |
| AgentEvolver | 完全自动生成 | LLM 自身（与解题者同一模型） | Qwen2.5-7B/14B ✅ | Qwen-MAX API ❌ |

### 5.2 一行式速记

```
SkillRL：      [训练集❌] [解题者 ✅] [总结者 ❌]
SKILL0：       [训练集❌] [解题者 ✅] [总结者 ❌]
SkillOS：      [训练集❌] [解题者 ❌] [总结者 ✅]   ← 唯一训练总结者的工作
AgentEvolver： [出题者✅] [解题者 ✅] [总结者 ❌]
```

### 5.3 强模型依赖程度

| 方法 | 依赖的强模型 | 用途 |
|------|-------------|------|
| SkillRL | OpenAI o3 | Skill 总结（核心） |
| SKILL0 | OpenAI o3（继承） | Skill 总结（核心） |
| SkillOS | Gemini-2.5-Pro（辅助）+ Qwen3-32B（Judge） | 标注分组 + 质量评估 |
| AgentEvolver | Qwen-MAX | Experience 提取 + 总结 |

### 5.4 数据依赖程度

```
高依赖外部数据 ←─────────────────────────────────────────→ 完全自主
       │                                                    │
   SkillRL             SKILL0          SkillOS          AgentEvolver
（官方数据集）       （复用 SkillRL）   （官方+分组）    （完全自生成）
```

### 5.5 范式演进脉络

```
SkillRL (2602.08234)
  强模型提炼知识 → 弱模型 RL 学会使用 → 递归演化技能库
      ↓
SKILL0 (2604.02268)
  同样的技能库 → 渐进撤回 → 内化进参数 → 零样本执行（无检索开销）
      ↓
SkillOS (2605.06614)
  冻结执行者 → 训练 Curator → 学会如何管理技能（增/改/删）
      ↓
AgentEvolver (2511.10395)
  全链路自主 → 自出题 + 自解题 + 自总结 → 步骤级信用分配
```

---

## 06 关键洞察：被忽视的"总结者"

### 6.1 总结者，是被严重低估的关键模块

| 工作 | Skill 总结者是否训练 |
|------|-------------------|
| AutoSkill | ❌ |
| EvoSkill | ❌（Claude Code w/ Opus 4.5） |
| MemSkill | ❌ |
| CoEvoSkills | ❌（Claude Code） |
| SE-Agent | ❌ |
| EvolveR | ❌（Qwen2.5） |
| SAGE | ✅（序列 rollout 中带 reward 总结） |
| SkillRL | ❌（OpenAI o3） |
| SKILL0 | ❌（继承） |
| **SkillOS** | **✅（唯一专门训练）** |
| AgentEvolver | ❌（Qwen-MAX API） |

**结论**：针对总结者本身做训练的工作屈指可数——SkillOS 是唯一完整地把 Curator 当成主训练对象的工作，且实验证明**训练后的 8B Curator 优于冻结的 Gemini-2.5-Pro Curator**。

### 6.2 自主性 × 总结质量的二维空间

**右上方空白象限**（既自动生成题目、又训练总结者）目前**没有一篇工作覆盖**——这是当前最显眼的研究空白。

### 6.3 横向 vs 纵向总结的融合空间

SE-Agent 是横向（多次采样）总结，其他工作以纵向（历史对话）为主。第二类工作中两者基本没有融合。**横向 + 纵向的融合**是否会带来更稳健的 Skill 库？是开放问题。

---

## 07 写在最后

这一年多自进化 Agent 的研究节奏非常快，从"存技能"到"训技能"、从"依赖人工数据"到"零数据自训"，几乎每个月都能看到新工作。但**还有大片研究空白没人去碰**——尤其是"总结者本身的训练"和"完全自主 + 训总结者"这条交叉路径。

这条赛道的本质问题只有一个：

> **如何让 Agent 在没有人工干预的情况下，把交互的副产物转化为下一次更强的能力？**

---

## 附录 A：论文索引

| 简称 | arXiv | 类别 |
|------|-------|------|
| AutoSkill | 2603.01145 | 第一类 |
| EvoSkill | 2603.02766 | 第一类 |
| MemSkill | 2602.02474 | 第一类 |
| CoEvoSkills | 2604.01687v2 | 第一类 |
| SE-Agent | 2508.02085 | 第一类 |
| EvolveR | 2510.16079 | 第二类 |
| SAGE | 2512.17102 | 第二类 |
| SkillRL | 2602.08234 | 第二类 |
| SKILL0 | 2604.02268 | 第二类 |
| SkillOS | 2605.06614 | 第二类 |
| AgentEvolver | 2511.10395 | 第二类 |
| Agent0 | 2511.16043 | 第三类 |
| Tool-R0 | 2602.21320 | 第三类 |
| Absolute Zero | 2505.03335 | 第三类 |

## 附录 B：评估数据集索引

| 数据集 | 使用工作 | 类型 |
|--------|---------|------|
| WildChat-1M | AutoSkill | 用户对话 |
| OfficeQA | EvoSkill | 办公图表 |
| LoCoMo / LongMemEval | MemSkill | 长程对话记忆 |
| SkillBench | CoEvoSkills | 技能 |
| ALFWorld | SkillRL、SKILL0、SkillOS | 具身/Agentic |
| WebShop | SkillRL、SKILL0、SkillOS | 网页购物 |
| Search-QA | SkillRL / SKILL0 | 检索问答 |
| AppWorld | SAGE | APP 交互 |
| NQ、HotpotQA、TriviaQA、PopQA | EvolveR | 检索问答 |
| DeepMath-103k | SkillOS | 数学推理 |
| GSM8K / AIME | Agent0 | 数学 |
| ToolAlpaca、SealTool、NexusRaven | Tool-R0 | 工具使用 |
| HumanEval、MBPP、LCB | Absolute Zero | 代码 |

---

## 附：文末推广信息（原文内容）

文中最后附有**腾讯PCG大数据平台部**的推广内容，介绍了一款基于 Agentic AI 能力开发的全链路数据助手 **Dola**：用户只需引入个人数据表，即可获得专属 AI 分析师，能完成取数、跑数、异动归因、画像对比分析、股票基金回测、房价预测等任务，可自行编写/纠正 SQL、使用 Python 处理数据与可视化，并生成完整分析报告，全程无需编写代码。
