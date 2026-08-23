# 私有化 Agent 开发方案

> 基于《深入理解 AI Agent：设计原理与工程实践》理论体系
> 定位：单人 + Trae Code 打造的本地通用 Agent 平台实施蓝图

---

## 第 1 章 概述与设计原则

本章是全书的入口,核心职责是回答"为什么做、做什么、怎么做",为读者建立全局认知。本章不引入任何新设计,仅概述第 2-9 章已锁定的内容,所有结论精确引用对应章节号便于跳转。

**本章的读者**:仅作者本人(单人 + Trae Code 开发场景)。后续章节展开工程实现细节,本章只建立全局心智模型。

**本章结构**:1.1 项目背景与目标 → 1.2 设计原则(三大约束) → 1.3 技术选型概要 → 1.4 架构总览 → 1.5 实施路线概要 → 1.6 边界与约束。

---

### 1.1 项目背景与目标

**项目定位**:基于《深入理解 AI Agent:设计原理与工程实践》理论体系,打造一套可落地的私有化 Agent 开发方案,作为单人 + Trae Code 开发的本地通用 Agent 平台实施蓝图。

**私有化边界**:仅"本地 Agent 运行窗口"私有化,模型、数据、工具链均可云端。具体边界见第 2 章四层架构(2.1)。

**目标场景**:通用 Agent 平台,首批落地三大场景:

| 场景 | 核心能力 | 对应章节 |
|---|---|---|
| 日常办公 | Excel/Word 文档处理 + 网页调研 + 来源标注 | 7.8-7.11 |
| 数据分析 | pandas + matplotlib + scipy 全栈 + 图表输出 | 7.12 |
| 前端设计 | HTML/React/Vue 代码生成 + 设计系统 RAG | 7.13-7.15 |

**开发模式**:架构设计、前后端开发完全依靠 Trae Code,无团队协作,不对外商业化,不考虑合规与监管。该模式决定全书不引入多租户、集群、CI/CD 等服务端实践。

**落地策略**:"可落地"为先,用书的理论做底层支撑,但慎重考虑书里强调而工程上常被省略的部分,不为快速落地牺牲未来扩展空间。MVP/V2 边界清晰划分,见 9.2/9.3 整合表。

`[MVP]` 项目目标由 9.2 MVP 完整模块清单落地,共 60+ 项必须实现项,覆盖四层架构全栈。

---

### 1.2 设计原则

全书以三大第一性约束为顶层设计底线,所有架构决策若违反任一约束即视为错误。三大约束的完整落地检查表见 9.8(共 59 项)。

**约束一:上下文质量优先**

任何架构决策若牺牲上下文质量(污染、丢失、截断、错位)即视为错误。

- 落地实现:19 项,覆盖 KV Cache 分区模型、ReAct 循环纯净、hash 校验、状态栏机制、模板变量体系、三类压缩策略、注入防护、记忆注入限制、混合检索 + reranker、artifact 机制、沙箱 stdout 严格阈值、文件系统工作记忆、场景专用 Prompt 四段式、少样本注入、设计系统 RAG 注入、LLM-as-Judge、低分案例驱动样本扩充。
- 对应章节:2.4、2.8、3.2、3.4-3.10、3.12、4.5、4.13-4.15、5.7、5.15、6.6、6.10、7.6、7.7、7.15、8.8、8.16。
- 验证方式见 9.8 检查表。

**约束二:缓存友好**

KV Cache 命中率是模型调用成本与延迟的隐形决定因素,架构层必须保证 prefix 稳定性。

- 落地实现:18 项,覆盖 KV Cache 分区模型、各适配器 cache 行为映射、Skills 版本与会话绑定、压缩逻辑外置、hash 校验、状态栏注入时机、模板变量解析时机、KB 片段注入 Stable Zone、增量更新不影响已有会话、会话启动锁定工具集、权限确认缓存、会话工作目录跨轮次持久、串行调用复用目录、会话锁定 Skill 版本、会话中途切换拒绝、示例注入 Frozen Zone、版本变更触发快速回归子集、Mock 模式加速批量评测。
- 对应章节:2.4、2.7、2.8、2.11、3.4、3.6、3.7、3.10、4.15、4.16、5.5、5.12、6.4、6.11、7.3、7.4、7.7、8.10、8.13。
- 验证方式见 9.8 检查表。

**约束三:评估驱动迭代**

所有运行轨迹必须可回放、可评判、可回滚,否则迭代退化为直觉赌博。

- 落地实现:22 项,覆盖事件流持久化、评估数据集与版本快照表、异常轨迹保存、V2 接口预留、压缩存档、token 三类成本分类、消息时序规则、持久化路径完整、记忆提取/淘汰事件、知识库快照、软删除保留历史数据、工具调用入 react_events、资源限额与审计日志、异步任务状态持久化、沙箱执行入 react_events、预扫描告警、崩溃恢复记录、Skill 示例作为黄金样本、版本快照支持历史回放、场景化评估指标差异化、评估闭环完整。
- 对应章节:2.10、2.13、2.14、2.16、3.10、3.13-3.15、4.2、4.4、4.16、5.13、5.14、5.16、6.8、6.12、6.13、7.3、7.16、8.1-8.16。
- 验证方式见 9.8 检查表。

**三大约束的关系**:

- 上下文质量优先与缓存友好常存在张力(如压缩会破坏 Frozen Zone),架构层通过 hash 校验 + 压缩逻辑外置 + Stable Zone 合并规则协调,见 3.4、3.10。
- 评估驱动迭代是前两条约束的验证机制,通过 react_events 完整事件流 + 版本快照实现可回放,见 2.13、8.2。

`[MVP]` 三大约束在前 8 章均有明确落地实现,9.8 检查表确认 59 项无遗漏。

---

### 1.3 技术选型概要

技术选型严格服从"上下文质量优先"与"缓存友好"两大第一性约束。完整配置骨架见 9.13 全局 config.yaml(11 段配置全量)。

**整体技术栈**:

| 层 | 选型 | 决策依据 | 对应章节 |
|---|---|---|---|
| 前端 | Electron + React + TypeScript | 桌面端 GUI 形态,跨平台,ReAct 步骤流式渲染 | 2.1、2.15 |
| 后端 | Python asyncio | 协程模型适合 ReAct 循环 + 流式输出 + 工具调度 | 2.2、2.6 |
| 数据库 | PostgreSQL | 会话历史、用户记忆、知识库向量(pgvector)、评估数据集统一存储 | 2.10 |
| 向量库 | pgvector(HNSW 索引) | 复用 Postgres,基于 HNSW 的向量存储方案 | 4.11 |
| 模型 | GLM / DeepSeek / Agnes / KIMI | 四家适配器 + capability 降级 + ManualRouter | 2.7、2.9 |
| 工具层 | MCP 统一接口 | 双轨架构(内置 + MCP),先建通用工具集再扩展 | 5.1-5.4 |
| 沙箱 | 复用 Trae Code 执行能力 | 子进程隔离 + 资源限制 + 安全边界 | 6.1-6.8 |

**模型接入策略**:

- 四家模型(GLM/DeepSeek/Agnes/KIMI)统一适配器基类 + capability 元数据 + 各厂商特性兼容(2.7)。
- API Key 加密:AES-256-GCM + 机器 ID 派生密钥 + UI 录入 + config_runtime 密文存储(2.7、2.12)。
- 模型路由:MVP 采用 ManualRouter(UI 手动选择),V2 预留 TagBasedRouter Protocol(2.9)。
- 降级链:fallback_chain 配置,某家不可用时自动切换备选(2.7、2.14)。

**存储策略**(2.10、2.11):

- Postgres:会话完整历史(持久可回放)、用户长期记忆、知识库向量(pgvector)、评估数据集、版本快照、ReAct 完整执行轨迹。
- 内存:当前活跃会话临时上下文窗口、运行中 ReAct 临时状态(进程销毁自动丢弃)。
- Skills/Prompt 混合存储:开发期文件系统(yaml/json,Git 管理)+ 运行时数据库(PG,支持 UI 编辑);数据库优先,文件回退。

**配置分层**(2.12):

- 静态 yaml 默认值 + config_runtime 运行时覆盖。
- 标注 `[runtime]` 的项支持 UI 配置面板修改,无需重启;未标注的需重启 Sidecar。
- 完整骨架见 9.13,共 11 段:系统基础、通信协议、模型、上下文工程、记忆、知识库、工具层、沙箱、Skills、评估、可观测性。

`[MVP]` 技术选型已全部锁定,9.13 config.yaml 骨架为开发期配置参考,实际运行时以 config_runtime 优先。

---

### 1.4 架构总览

平台采用严格四层分层架构(2.1),层间单向依赖,禁止反向调用。模块依赖关系 DAG 见 9.5。

**四层架构**:

```
┌─────────────────────────────────────────────────────────────┐
│  UI 层 (Electron Renderer)                                  │
│  会话视图 · ReAct 步骤渲染 · Skills/配置管理 · 评估面板      │
└──────────────────────────┬──────────────────────────────────┘
                           │ HTTP (控制面) + WebSocket (数据面)
┌──────────────────────────┴──────────────────────────────────┐
│  编排层 (Python asyncio)                                    │
│  ReAct 核心循环 · 上下文管理 · 模型适配 · 模型路由           │
│  流式聚合 · 工具调度 · 异常降级 · 可观测性                  │
└──────────────────────────┬──────────────────────────────────┘
                           │ 内部进程调用 (asyncio / ProcessPool)
┌──────────────────────────┴──────────────────────────────────┐
│  能力层                                                      │
│  MCP 工具集 (通用+场景) · 沙箱代码执行 (复用 Trae 机制)      │
│  Skills 加载器 · 事件订阅器                                  │
└──────────────────────────┬──────────────────────────────────┘
                           │ DB 协议 / 文件 IO
┌──────────────────────────┴──────────────────────────────────┐
│  持久层 (Postgres + 内存)                                   │
│  会话/消息/事件 · 用户记忆 · 知识库向量 · 版本快照 · 评估   │
│  config_runtime · async_tasks · skills                      │
└─────────────────────────────────────────────────────────────┘
```

**进程模型**(2.2):

- 单 Python Sidecar(主进程)+ 固定 2 Worker 进程池(embedding/reranker/batch eval 纯计算)。
- 跨平台:Windows spawn(Worker 入口最小化,避免重复加载 backend);macOS/Linux fork(启动更快)。
- Worker 不持有 DB 连接,通过 Sidecar 内部 HTTP 接口读写 DB(127.0.0.1 私有接口,单 Worker 并发上限 3)。

**通信协议**(2.3):

- HTTP 控制面:会话管理、Skills 管理、配置管理、评估触发等控制类操作。
- WebSocket 数据面:ReAct 事件流(thinking/tool_call/tool_result/final/error)实时推送。
- 断线重连:基于 ws_offset 会话消费位点补发,仅补发 turn > offset 的事件,避免全量重复渲染。

**关键路径**(9.5 DAG):

依赖链路最长的关键路径,决定 MVP 最短实施周期:

```
四层骨架(2.1) → 进程模型(2.2) → 通信协议(2.3) → ReAct 循环(2.4)
  → 上下文管理器(3.1-3.15) → 模型适配(2.7) → 工具层(5.1-5.17)
  → 沙箱执行(6.1-6.15) → 场景 Skills(7.1-7.16) → 评估闭环(8.1-8.16)
```

**核心数据流**:

1. 用户消息 → UI 层 WS 客户端 → 编排层 HTTP 控制面 → ReAct 循环启动。
2. ReAct 循环每轮:上下文管理器构建分区(Frozen/Stable/Active) → 模型适配器调用 → 流式输出 WS 推送 → 工具调度(能力层) → 持久层记录事件。
3. 长任务:工具调用走 Worker 进程池(asyncio + ProcessPoolExecutor),结果异步回传。
4. 异常:四类异常(模型/工具/进程/用户)分类降级 + checkpoint 存储(2.14)。

`[MVP]` 四层架构与进程模型在 M0 阶段落地(9.4),关键路径贯穿 M0-M4 五阶段。

---

### 1.5 实施路线概要

MVP 拆分为 M0-M4 五个阶段,严格按四层架构依赖顺序划分。完整 Done Criteria 见 9.4,本章仅概述阶段目标与前置依赖。

**阶段划分总览**:

| 阶段 | 名称 | 核心目标 | 前置依赖章节 |
|---|---|---|---|
| M0 | 基础骨架 | 四层架构 + 进程模型 + 通信协议 + 持久层表结构 | 第 2 章(2.1、2.2、2.3、2.10、2.12、2.15) |
| M1 | 编排核心 | ReAct 循环 + 上下文工程 + 模型适配 | 第 2 章(2.4-2.9、2.13、2.14)+ 第 3 章(3.1-3.15) |
| M2 | 能力层 | 知识库 RAG + 工具层 + 沙箱代码执行 | 第 4 章(4.1-4.16)+ 第 5 章(5.1-5.17)+ 第 6 章(6.1-6.15) |
| M3 | 场景化 | 三场景 Skills(办公/数据分析/前端设计) | 第 7 章(7.1-7.16) |
| M4 | 评估闭环 | 评估环境 + 数据集 + 指标 + 迭代流程 | 第 8 章(8.1-8.16) |

**各阶段目标**:

- **M0 基础骨架**:搭建可运行的最小骨架,前后端能通信、Postgres 能读写、配置能加载、磁盘分级告警生效。
- **M1 编排核心**:ReAct 循环跑通,四家模型可调用,上下文工程完整就位(分区模型 + hash 校验 + 压缩 + 注入防护 + 计费感知)。
- **M2 能力层**:Agent 具备知识检索(search_knowledge + 混合检索 + reranker)、工具调用(9 类通用工具 + MCP 双探活)、代码执行(Python/JavaScript 沙箱)三大核心能力。
- **M3 场景化**:三场景 Skills 可独立运行,覆盖首批落地需求(办公文档处理 + 网页调研 + 数据分析 + 前端代码生成)。
- **M4 评估闭环**:评估环境 + 数据集 + 五类指标 + LLM-as-Judge + 三类载体迭代闭环 + 回滚机制完整就位,支持持续进化。

**关键路径与并行机会**(9.5、9.6):

- 关键路径:M0 → M1 → M2 → M3 → M4,贯穿四层架构依赖。
- 并行机会:M1 模型适配与上下文管理器可并行;M2 知识库 RAG 与工具层可并行,沙箱与 RAG 可并行;M3 三场景 Skills 可并行;M4 评估环境与数据集可并行。
- 单人开发推荐顺序:9.6 给出 27 步实操步骤,基于 DAG 输出,可根据实际进度调整穿插顺序,但不可跳过关键路径上的步骤。

**MVP 验收标准**:9.7 定义 30 项验收维度,全部通过即为 MVP 完成,对应第 8 章评估指标的工程化落地。

**V2 演化路线**:9.10 按"用户价值×实施成本"二维排序给出 P1-P4 推荐顺序,不承诺时间点,仅在 MVP 完成后启动。V2 完整清单见 9.3。

`[MVP]` M0-M4 五阶段为 MVP 完整实施范围,每阶段 Done Criteria 为验收标准。
`[V2规划]` 9.3 V2 清单中的所有项不纳入 M0-M4,仅在 MVP 完成后按 9.10 优先级启动。

---

### 1.6 边界与约束

本节复用 9.11 三条架构边界守护原则,不新增规则。完整变更检查清单(10 项)见 9.11。

**三条边界守护原则**(沿用 2.16):

1. **V2 预留接口必须在 MVP 阶段以"空实现"或"Protocol 定义"形式存在,不允许"以后再加"**。
   - 空实现:返回默认值或 NotImplementedError 的方法。
   - Protocol 定义:Python `typing.Protocol` 或 ABC,定义接口契约但不实现。
   - 目录占位:如 `core/multi_agent/` 空目录 + README 说明。

2. **MVP 实现不得依赖 V2 接口的具体实现(仅依赖抽象)**。
   - MVP 代码调用 V2 接口时,仅依赖 Protocol/ABC,不依赖具体类。
   - V2 接口变更不影响 MVP 代码(通过抽象层隔离)。

3. **每次架构变更必须更新边界表,确保边界清晰**。
   - 新增 MVP 模块:更新 9.2 整合表 + 对应章节的 MVP/V2 边界小节。
   - 新增 V2 预留:更新 9.3 整合表 + 对应章节的 MVP/V2 边界小节。
   - V2 转为 MVP:从 9.3 移除,加入 9.2,更新对应章节。

**场景边界**:

- 仅面向单人本地 Electron 桌面 Agent,剔除多租户、集群、团队 CI/CD、负载均衡等无关服务端实践。
- 不对外商业化,不考虑合规与监管问题。
- 模型、数据、工具链可云端,仅 Agent 运行窗口本地化。

**文档边界**:

- 第 1 章为概述,不展开实现细节,精确引用对应章节号。
- 第 2-8 章为各层详细设计,每章末尾标注 `[MVP]` / `[V2]` 边界。
- 第 9 章为 MVP 路线与 V2 扩展汇总,整合 2-8 章边界,不新增设计。

**变更检查流程**(9.11):

```
架构变更提议
  → 检查 10 项变更检查清单(9.11)
    → 全部通过 → 实施变更 + 更新文档
    → 任一不通过 → 补充缺失项后重新检查
```

10 项检查清单覆盖:MVP/V2 边界更新、三大约束检查、DAG 更新、开发顺序更新、验收标准更新、风险评估、配置更新、持久化更新、回滚降级更新、V2 接口验证。完整内容见 9.11。

`[MVP]` 三条原则 + 10 项检查清单为架构变更的强制流程,单人开发也需遵守,避免架构漂移。

---

第 1 章起草完成。本章概述了项目背景与目标(1.1)、三大设计原则(1.2)、技术选型概要(1.3)、架构总览(1.4)、实施路线概要(1.5)、边界与约束(1.6),共 6 节,所有内容严格复用第 2-9 章已锁定决策,未引入新方案。

全书结构总览:

| 章节 | 主题 | 核心职责 |
|---|---|---|
| 第 1 章 | 概述与设计原则 | 全局认知,概述 2-9 章锁定决策 |
| 第 2 章 | 总体架构 | 四层骨架 + 进程模型 + 通信协议 + 模型适配 + 持久层 + 可观测性 + 异常分类 |
| 第 3 章 | 上下文工程层 | 分区模型 + hash 校验 + 状态栏 + 模板变量 + 压缩策略 + 注入防护 + 计费感知 |
| 第 4 章 | 记忆与知识库层 | 用户记忆 + 知识库 RAG 全栈(embedding/HNSW/混合检索/reranker/Agentic RAG) |
| 第 5 章 | 工具层与 MCP 集成 | 双轨工具架构 + MCP Client + 9 类通用工具 + 权限/超时/异步/artifact/安全 |
| 第 6 章 | 沙箱代码执行 | 子进程隔离 + 资源限制 + 安全边界 + 流式输出 + 跨平台 |
| 第 7 章 | 场景 Skills 设计 | 三场景(办公/数据分析/前端)+ 底层基础设施 + Prompt 框架 + 评估支持 |
| 第 8 章 | 评估与持续进化闭环 | 两类评估环境 + 数据集 + 五类指标 + LLM-as-Judge + 三类载体迭代 + 回滚 |
| 第 9 章 | MVP 路线与 V2 扩展 | M0-M4 五阶段 + DAG + 验收标准 + 三大约束检查表 + 风险 + 回滚降级 + 配置 + ER |

全书起草完成。

---

## 第 2 章 总体架构

本章是全篇的工程地基。后续各章（上下文、知识库、工具、沙箱、Skills、评估）都在本章定义的分层、契约与边界内展开。本章只回答"骨架怎么搭",不展开任何具体能力实现。

三条第一性约束贯穿本章与全书:

- **上下文质量优先**:任何架构决策若牺牲上下文质量(污染、丢失、截断、错位)即视为错误。
- **缓存友好**:KV Cache 命中率是模型调用成本与延迟的隐形决定因素,架构层必须保证 prefix 稳定性。
- **评估驱动迭代**:所有运行轨迹必须可回放、可评判、可回滚,否则迭代退化为直觉赌博。

每节末尾以 `[MVP]` 或 `[V2]` 标注实现边界。

---

### 2.1 四层架构总览与职责边界

平台采用严格四层分层架构,层间单向依赖,禁止反向调用。

```
┌─────────────────────────────────────────────────────────────┐
│  UI 层 (Electron Renderer)                                  │
│  会话视图 · ReAct 步骤渲染 · Skills/配置管理 · 评估面板      │
└──────────────────────────┬──────────────────────────────────┘
                           │ HTTP (控制面) + WebSocket (数据面)
┌──────────────────────────┴──────────────────────────────────┐
│  编排层 (Python asyncio)                                    │
│  ReAct 核心循环 · 上下文管理 · 模型适配 · 模型路由           │
│  流式聚合 · 工具调度 · 异常降级 · 可观测性                  │
└──────────────────────────┬──────────────────────────────────┘
                           │ 内部进程调用 (asyncio / ProcessPool)
┌──────────────────────────┴──────────────────────────────────┐
│  能力层                                                      │
│  MCP 工具集 (通用+场景) · 沙箱代码执行 (复用 Trae 机制)      │
│  Skills 加载器 · 事件订阅器                                  │
└──────────────────────────┬──────────────────────────────────┘
                           │ DB 协议 / 文件 IO
┌──────────────────────────┴──────────────────────────────────┐
│  持久层                                                      │
│  PostgreSQL (会话/记忆/知识库 pgvector/评估/版本/轨迹)       │
│  文件系统 (开发期 Skills/Prompt 模板 · 日志 · artifacts)     │
└─────────────────────────────────────────────────────────────┘
```

**层间契约**:

| 边界 | 契约形式 | 禁止行为 |
|---|---|---|
| UI ↔ 编排 | HTTP + WS 消息 schema | UI 直连能力层或持久层 |
| 编排 ↔ 能力 | Python 内部接口 + MCP 协议 | 能力层反向调用编排层状态 |
| 能力 ↔ 持久 | SQL + 文件路径 | 能力层跨进程持有 DB 连接 |
| 编排 ↔ 持久 | Repository 模式 | 编排层散写 SQL |

**单一持久化入口**:所有 DB 访问收敛到 `storage/` 包的 Repository 类,能力层与编排层均通过 Repository 读写,避免 SQL 散落。这是后续评估回放、版本快照、清理策略统一生效的前提。

`[MVP]` 四层骨架全部实现,但每层只做最小可用子集。
`[V2]` 能力层新增多 Agent 协作接口,UI 层新增评估可视化面板增强。

---

### 2.2 进程模型与生命周期

桌面端运行 4 类进程,职责严格隔离:

```
┌──────────────────────────────────────────────────────────┐
│ Electron 主进程 (Node.js)                                │
│ · 窗口/菜单/托盘生命周期                                  │
│ · 启动并守护 Python Sidecar 子进程                       │
│ · 全局快捷键、系统通知、文件对话框                        │
└──────────────┬───────────────────────────────────────────┘
               │ spawn + stdio 监控
┌──────────────┴───────────────────────────────────────────┐
│ Python Sidecar (uvicorn + asyncio)                       │
│ · HTTP 控制面 (会话管理/配置 CRUD/评估接口)               │
│ · WebSocket 数据面 (流式 token / ReAct 步骤推送)          │
│ · ReAct 编排主循环                                        │
│ · 持有 Postgres 连接池                                    │
└──────────────┬───────────────────────────────────────────┘
               │ ProcessPoolExecutor (CPU offload)
┌──────────────┴───────────────────────────────────────────┐
│ Worker 进程池 (按需启动,默认 2 worker)                   │
│ · 本地 embedding 计算                                    │
│ · 知识库 chunking / 索引重建                             │
│ · 评估批量推理                                            │
└──────────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────────┐
│ Electron 渲染进程 (Chromium)                              │
│ · React UI                                               │
│ · 通过 preload 桥接 HTTP/WS 客户端                        │
└──────────────────────────────────────────────────────────┘
```

**生命周期规则**:

- Electron 主进程启动时拉起 Python Sidecar,健康检查通过后加载 UI;sidecar 崩溃则主进程重启并尝试恢复最后一次活跃会话状态(从 Postgres 读取)。
- Worker 进程池懒启动,空闲 10 分钟自动回收,避免常驻内存占用。
- 应用退出时:sidecar 拒绝新请求 → 等待进行中 ReAct 循环完成或超时(30s)→ 强制关闭 → Postgres 优雅关闭。

**Worker 与 DB 的关系**(关键澄清):

Worker 进程是**纯计算节点**,不持有 DB 连接,不访问 Postgres。职责严格收敛为 CPU 密集任务:

- sidecar 将"待计算数据"序列化发送给 Worker(如待 embedding 的文本列表)
- Worker 返回计算结果(如向量列表)给 sidecar
- sidecar 负责将结果写入 DB

这样消除了"Worker 通过 HTTP 访问 DB"的链路空白,也避免了多进程持有 DB 连接导致连接数膨胀。典型流程:知识库索引时,sidecar 从 `kb_documents` 读取文本 → 发给 Worker chunking → Worker 返回 chunks → sidecar 再把 chunks 发给 Worker embedding → Worker 返回向量 → sidecar 写入 `kb_chunks`。

**资源约束**:Python Sidecar 内存上限 1.5GB(可在配置中调整),超限触发主动 GC + 告警;Worker 进程单实例上限 512MB。桌面端不能因 Agent 运行而吃满系统资源。

**跨平台备注**:Windows 下 `ProcessPoolExecutor` 默认 `spawn` 启动方式会重新导入父进程模块,需保证 Worker 入口模块最小化(仅依赖 numpy/torch 等计算库,不导入完整 backend),避免重复加载模型适配器与 DB 连接;macOS/Linux 可用 `fork` 提升启动速度。Worker 启动方式在 `executor.py` 中按平台自动选择。

`[MVP]` 实现单 Python Sidecar + 固定 2 worker 进程池,Worker 纯计算无 DB 访问。
`[V2]` 按负载动态调整 worker 数;Python Sidecar 多实例隔离不同会话(为多 Agent 协作铺路)。

---

### 2.3 前后端通信协议

采用混合协议:HTTP 做控制面(请求/响应、CRUD、配置),WebSocket 做数据面(流式、事件推送、长任务进度)。

**HTTP 控制面**(RESTful,JSON):

| 路径 | 方法 | 用途 |
|---|---|---|
| `/api/sessions` | POST/GET | 创建/列出会话 |
| `/api/sessions/{id}` | GET/DELETE | 会话详情/删除 |
| `/api/sessions/{id}/messages` | POST | 发送用户消息(触发 ReAct) |
| `/api/skills` | GET/PUT | Skills 列表/启用状态 |
| `/api/skills/{id}/template` | GET/PUT | Prompt 模板读写 |
| `/api/config` | GET/PUT | 运行时配置 |
| `/api/eval/datasets` | GET/POST | 评估数据集管理 |
| `/api/eval/runs` | POST | 触发评估运行 |

**WebSocket 数据面**(单连接,JSON 帧):

连接建立后客户端订阅 `session.{id}` 频道,服务端推送以下事件类型:

```typescript
type WSEvent =
  | { type: "thinking"; session_id: string; content: string }
  | { type: "token"; session_id: string; content: string }
  | { type: "tool_call"; session_id: string; tool: string; args: object; call_id: string }
  | { type: "tool_result"; session_id: string; call_id: string; result: object; ok: boolean }
  | { type: "error"; session_id: string; category: "model"|"tool"|"process"|"user"; code: string; message: string }
  | { type: "done"; session_id: string; final_text: string };
```

**错误码体系**(统一三位数):

- `1xx` 模型层(100 超时 / 101 限流 / 102 响应格式错误 / 103 上下文超限)
- `2xx` 工具层(200 MCP 崩溃 / 201 超时 / 202 返回非法 / 203 权限拒绝)
- `3xx` 进程层(300 Sidecar 崩溃 / 301 worker 死亡 / 302 OOM)
- `4xx` 用户层(400 主动取消 / 401 窗口关闭)

**tool_call 批量兼容**:一轮内多个 tool_calls 通过连续推送多个 `tool_call` 事件实现(每个事件携带独立 `call_id`),前端按 `call_id` 区分渲染。前端可选择批量缓冲(如 50ms 内的事件合并渲染)以优化性能,但协议层保持单事件推送的简单性。

**连接管理**:WS 单连接复用所有会话事件,通过 `session_id` 路由。

**断线重连与消费位点**(关键机制):

为避免重连后全量补发导致前端重复渲染,引入会话消费位点:

- `config_runtime` 表存储 `ws_offset:{session_id}` = 客户端最大已接收 `turn` 值。
- 服务端推送事件时携带 `turn` 字段;客户端每接收一批事件后,通过 `/api/sessions/{id}/ack` 上报当前 `turn`。
- 重连时客户端发送上次 `offset`,服务端仅从 `react_events` 表查询 `turn > offset` 的事件补发。
- 这是"评估驱动迭代"在通信层的体现:`react_events` 表既是评估回放数据源,也是 WS 补发数据源,单表双用。

`[MVP]` HTTP + WS 全量实现,含消费位点机制与断线重连补发。
`[V2]` 多会话并行时 WS 帧背压控制;`session_id` 路由扩展为多 Agent 协作场景下的多路复用。

---

### 2.4 ReAct 核心循环与状态机

平台核心运行单元是 ReAct 循环,严格对齐书中第一章"思考→行动→观察"模型。

**内部消息规范**:统一对齐 OpenAI messages 格式作为内部表示(降低适配成本),各模型适配器在出口处转换。

```python
@dataclass
class Message:
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None
    tool_calls: list[ToolCall] | None = None       # assistant 角色
    tool_call_id: str | None = None                # tool 角色
    name: str | None = None                        # tool 角色的工具名

@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict
```

**Agent 状态机**:

```
                  ┌─────────────┐
            ┌─────│   IDLE      │←────────────────────┐
            │     └─────┬───────┘                     │
            │  user msg │                             │
            │           ▼                             │
            │     ┌─────────────┐    tool_call        │
            │     │  THINKING   │────────────────┐    │
            │     └─────┬───────┘                │    │
            │  no tool  │                        ▼    │
            │           │                 ┌──────────┐│
            │           ▼                 │  ACTING  ││
            │     ┌─────────────┐         └────┬─────┘│
            │     │   DONE      │              │ result
            │     └─────────────┘              ▼      │
            │                          ┌──────────┐   │
            │                          │ OBSERVING│───┘
            │                          └──────────┘
            │                                  │
            │  error (model/tool/process)      │
            └──────────────────────────────────┘
                      ↓ (降级或重试后仍失败)
                 ┌──────────┐
                 │  ERROR   │
                 └──────────┘
```

**循环主体**(伪代码):

```python
async def react_loop(session: Session, user_msg: Message) -> None:
    ctx = await context_manager.build(session, user_msg)
    while True:
        msg = await model_adapter.stream(ctx)           # THINKING
        await event_bus.emit("thinking", msg)
        ctx.append(msg)
        if not msg.tool_calls:                          # 无工具调用 → 终态
            await event_bus.emit("done", msg.content)
            await storage.persist_trace(session.id, ctx)
            return
        for call in msg.tool_calls:                     # ACTING
            await event_bus.emit("tool_call", call)
            try:
                result = await tool_dispatcher.dispatch(call)
                await event_bus.emit("tool_result", call.id, result, ok=True)
            except ToolError as e:
                result = ToolResult(error=e.code, message=str(e))
                await event_bus.emit("tool_result", call.id, result, ok=False)
            ctx.append(Message(role="tool", tool_call_id=call.id, content=result.to_json()))
        # 进入下一轮 THINKING,context_manager 决定是否压缩
        ctx = await context_manager.maybe_compress(ctx)
```

**关键约束**:

- 每一轮的 `thinking` / `tool_call` / `tool_result` 都通过 `event_bus` 推送 WS 并同步落 Postgres `react_events` 表,这是评估回放的数据源。
- `maybe_compress` 钩子集中在上下文管理器(第 3 章),不在循环内散写压缩逻辑,保证 ReAct 主循环纯净。
- 最大循环深度可配置(默认 20),超限触发 `ERROR` 态并保存当前轨迹供评估分析。

`[MVP]` 单 Agent ReAct 全量实现,含错误降级与轨迹持久化。
`[V2]` 状态机扩展 `DELEGATING` 态(委托子 Agent),为多 Agent 协作预留。

---

### 2.5 轻量自研框架边界

采用"轻量自研 + 借用工具抽象"折中路线,明确边界防止架构漂移。

**自研部分**(完全掌控):

- ReAct 主循环与状态机(2.4 节)
- 上下文管理器(压缩、状态栏、KV Cache 友好构造)
- 模型适配层与路由
- 工具调度器与 MCP 客户端
- 流式聚合与事件总线
- 评估闭环与版本管理

**借用部分**(仅类型与装饰器,不引入运行时框架):

- 借鉴 Pydantic AI 的 `@tool` 装饰器模式定义 MCP 工具的 Python 端签名,但运行时自研调度。
- 借用 `pydantic` 做消息与配置的 schema 校验(已是事实标准,不算框架依赖)。
- 借用 `httpx` / `websockets` / `asyncpg` / `pgvector` 等基础库。

**明确不借用**(避免抽象层过厚):

- 不引入 LangChain 的 `Chain` / `Agent` / `Runnable` 抽象,避免黑盒。
- 不引入 LlamaIndex 的索引抽象,pgvector 直接操作。
- 不引入 OpenAI Agents SDK 的完整运行时(只参考其 tool calling schema)。

**判定原则**:任何外部依赖引入前必须回答——"它解决的是否是本平台核心问题?能否用 < 200 行自研替代?"。若能,自研;若不能,借用最小子集。

`[MVP]` 自研 ReAct + 借用 Pydantic schema 校验。
`[V2]` 评估是否引入 `outlines` / `instructor` 做结构化输出强化(若模型原生 tool calling 不稳定)。

---

### 2.6 协程编排:流式输出与任务调度

Python asyncio 单事件循环 + ProcessPoolExecutor 双轨模型。

**IO 轨**(asyncio 主循环):

- 模型流式 token 接收(SSE / WebSocket 长连接,各家适配器统一为 `async generator`)
- MCP 工具调用(MCP Python SDK 原生 async)
- Postgres 读写(asyncpg 连接池)
- WS 事件推送

**CPU 轨**(进程池):

- 本地 embedding 计算(Sentence-Transformers,阻塞型)
- 知识库文档 chunking(长文本分词、PDF 解析)
- 评估批量推理(数据集回放)

**双轨桥接**:

```python
class Executor:
    def __init__(self, pool_size: int = 2):
        self.pool = ProcessPoolExecutor(max_workers=pool_size)

    async def run_cpu(self, func, *args) -> Any:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self.pool, func, *args)
```

**流式聚合规则**:模型流式 token 在 IO 轨逐 token 推送 WS(保证 UI 实时性),同时聚合成完整 assistant 消息落 Postgres(避免高频 DB 写入)。聚合时机:

- `thinking` 块结束 → 整块入库
- `tool_call` 完整解析 → 入库
- 终态 `done` → 全消息入库 + 会话历史更新

**工具调度策略**:

- 同一轮多个 tool_call:默认并行(`asyncio.gather`),工具可声明 `sequential=True` 强制串行(如数据库写操作)。
- 工具超时:默认 30s,可在工具定义中覆盖;超时触发 `ToolError(201)` 并允许 Agent 在下一轮决定重试或换工具。
- 工具并发上限:全局信号量(默认 5),防止 Agent 在一轮内并行调用过多工具压垮 MCP server。

**缓存友好落地**:模型流式请求的 messages 数组前缀必须稳定(见 2.8),流式 token 接收过程中绝不修改已发送前缀,新信息只追加到尾部。

`[MVP]` 双轨模型 + 流式聚合 + 并行工具调度全量实现。
`[V2]` 进程池动态扩缩;工具调度支持 DAG 依赖声明(复杂任务编排)。

---

### 2.7 模型适配层与四家厂商差异化策略

统一适配器基类 + capability 元数据 + 各厂商兼容策略,集中在本节展开。

**适配器基类**:

```python
class ModelAdapter(ABC):
    @property
    @abstractmethod
    def capability(self) -> ModelCapability: ...

    @abstractmethod
    async def stream(self, ctx: list[Message], tools: list[ToolDef]) -> AsyncIterator[Message]: ...

    @abstractmethod
    def to_api_messages(self, ctx: list[Message]) -> list[dict]: ...

    @abstractmethod
    def to_api_tools(self, tools: list[ToolDef]) -> list[dict]: ...
```

**能力元数据**:

```python
@dataclass
class ModelCapability:
    context_window: int
    supports_thinking: bool        # DeepSeek-R1 / GLM-Z1 等思考型模型为 True
    supports_tool_calling: bool
    supports_streaming: bool
    kv_cache_prefix_stable: bool   # 前缀是否真正稳定(部分厂商 system prompt 不进 cache)
    max_tool_calls_per_turn: int
    # thinking_channel: 思考内容的传输通道
    #   "separate" = 独立字段(如 GLM-Z1 的 reasoning_content)
    #   "inline"   = 内嵌标签(如 DeepSeek-R1 的 <think>...</think>)
    #   "none"     = 不支持思考模式
    thinking_channel: Literal["separate", "inline", "none"] = "none"
```

**四家适配策略**:

| 厂商 | 协议 | 思考模式 | tool calling | KV Cache 行为 | 适配重点 |
|---|---|---|---|---|---|
| GLM | OpenAI 兼容 | Z1 系列独立 `reasoning_content` 字段 | 稳定,原生 | system prompt 进 cache | 直接走 OpenAI 客户端,解析思考字段 |
| DeepSeek | OpenAI 兼容 | R1 系列 inline `<think>` 标签或独立字段 | 稳定 | prefix cache 需显式启用 | 思考内容剥离到 `thinking` 事件,不混入 content |
| Agnes | 待确认(首版按 OpenAI 兼容处理) | 待确认 | 待确认 | 待确认 | 【落地阶段补充:对接 Agnes 官方文档后完善 capability、消息格式、思考字段、KV Cache 行为;开发期第一阶段优先完成 Agnes 适配器补齐】 |
| KIMI | OpenAI 兼容 | 无 | 稳定 | 长上下文(128k+)需注意 prefix 截断 | 长上下文场景的路由优先级;超长输入的压缩触发 |

**降级规则**(调用方按 capability 自动适配):

- 模型不支持 `tool_calling` → 适配器自动将工具定义注入 system prompt,解析模型回复中的 JSON 工具调用(回退模式,标注 `tool_calling_mode: "prompt_injected"`)。
- 模型不支持 `streaming` → 降级为非流式,UI 显示 loading 直到完整响应。
- 模型 `thinking_channel: "separate"` → 适配器将思考内容映射到 `thinking` 事件,主 `content` 仅保留最终答案。

**上下文超限保护**:适配器在发送前检查 `len(ctx) > capability.context_window * 0.8`,超限触发上下文管理器压缩(第 3 章),避免请求被厂商拒绝。

`[MVP]` 四家适配器全量实现;降级规则实现 tool_calling 回退与思考字段剥离。
`[V2]` 自动路由(见 2.9);Agnes 适配器按真实协议补全。

---

### 2.8 KV Cache 友好性架构约束

"缓存友好"是三大第一性约束之一,必须在架构层强制贯彻,而非依赖开发期自觉。

**消息列表分区模型**(对应 KV Cache 的物理分区):

```
┌──────────────────────────────────────────────────────────────┐
│ Frozen Zone (进 cache, 永不变更)                              │
│  · system prompt (含角色定义、全局规则)                       │
│  · tools 定义 (JSON schema)                                   │
└──────────────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────────────┐
│ Stable Zone (进 cache, 单会话内不变)                          │
│  · 会话级长期记忆摘要 (压缩后稳定)                            │
│  · 会话级知识库检索结果 (按需追加, 不重写)                    │
└──────────────────────────────────────────────────────────────┘
┌──────────────────────────────────────────────────────────────┐
│ Active Zone (每轮追加, 不重写历史)                            │
│  · ReAct 历史 (thinking + tool_call + tool_result)            │
│  · 当前用户消息                                               │
└──────────────────────────────────────────────────────────────┘
```

**架构层强制规则**:

1. **system prompt 冻结**:会话开始后 system prompt 不可修改;需要动态信息(如当前时间、用户偏好)走"状态栏"机制(注入到 Active Zone 尾部,不污染 Frozen Zone)。详见第 3 章。
2. **tools 定义冻结**:会话进行中不可增删工具;Skills 的动态加载发生在会话启动时,启动后工具列表冻结。需要新 Skills → 新会话。
3. **历史不可重写**:已发送的 messages 永不修改;上下文压缩产生新摘要时,摘要作为新 message 追加,旧消息标记为 `compressed` 但保留原文(评估回放需要)。
4. **检索结果只追加**:RAG 检索结果作为新的 user/assistant 消息追加,不替换历史检索结果。

**Stable Zone 合并压缩规则**(防止 Agentic RAG 多轮检索导致膨胀):

由于 Agentic RAG(第 4 章)会在多轮中多次检索知识库,每次追加新片段,Stable Zone 会无限膨胀。配套规则:

- 每 N 轮(默认 N=5)触发一次 Stable Zone 合并压缩:将历史检索片段摘要合并为单一"知识摘要"消息,替换旧 Stable Zone 内容。
- 合并后生成历史快照存入 `version_snapshots`(scope=`stable_zone`),供评估回放。
- 合并是**整体替换**而非局部修改:旧 Stable Zone 整体标记 `compressed`,新 Stable Zone 作为新消息块追加,`prefix_hash_check` 以新 Stable Zone 为准。
- 此规则是 2.4 节 `context_manager.maybe_compress` 的具体职责之一,不在 ReAct 主循环中实现。

**违规检测**:上下文管理器在每次构建 ctx 时执行 `prefix_hash_check` —— 计算 Frozen + Stable Zone 的 hash,与上一轮比对,不一致直接抛 `ContextIntegrityError`,中断循环。这是架构层的硬约束,防止任何下游模块无意中破坏 prefix。

**与各厂商的对接**:见 2.7 表格"KV Cache 行为"列;适配器负责将本平台的分区模型映射到厂商 API 的实际 cache 行为(如 DeepSeek 需显式启用 prefix cache)。

`[MVP]` 分区模型 + hash 检测全量实现;四家适配器的 cache 行为按表格落地。
`[V2]` 跨会话的 cache 复用(同一 Skills 配置的会话共享 Frozen Zone cache key)。

---

### 2.9 模型路由抽象层

可插拔路由接口,任务标签外部传入,路由策略不硬编码。

**路由接口**:

```python
class Router(Protocol):
    async def select(self, task_tag: str | None, context: list[Message]) -> str:
        """返回 model_id;task_tag 由调用方传入,不内嵌业务知识"""
        ...

class ManualRouter(Router):
    """MVP 默认实现:UI 选择的模型直接使用"""
    def __init__(self, selected_model_id: str):
        self.model_id = selected_model_id
    async def select(self, task_tag, context):
        return self.model_id
```

**调用链**:

```
UI (用户选择模型 OR 任务标签) → Router.select() → model_id → ModelAdapterRegistry.get(model_id)
```

**任务标签来源**:MVP 阶段仅来自 UI 用户手动选择;V2 阶段可来自 Skills 元数据声明(`skill.preferred_model_tag = "reasoning"`)或 Agent 自主判断。

**预留扩展点**:

- `TagBasedRouter`:按 `task_tag` 查表路由(如 `reasoning → deepseek-r1`,`chat → glm-air`)
- `CostAwareRouter`:按成本/速度/剩余配额选模型
- `AgentMetaRouter`:Agent 先调用轻量模型判断任务类型,再路由到主模型

**注册机制**:路由策略通过配置注入,运行时可切换,不需要改代码:

```yaml
# config.yaml
router:
  type: manual           # MVP
  # type: tag_based      # V2
  # rules:
  #   reasoning: deepseek-r1
  #   chat: glm-air
```

`[MVP]` 仅实现 `ManualRouter`,但 Router 接口与注册机制必须就位。
`[V2]` 实现 `TagBasedRouter` 与 `CostAwareRouter`;预留 `AgentMetaRouter` 接口。

---

### 2.10 持久化层:Postgres Schema 与桌面端运维约束

所有结构化数据收敛到内嵌 Postgres,文件系统仅承担开发期模板与日志。

**Schema 总览**:

```
sessions            会话元信息 (id, title, created_at, status, model_id, skill_set)
messages            会话消息历史 (id, session_id, role, content, tool_calls, tool_call_id, created_at)
                                    — 完整历史,评估回放源
user_memories       用户长期记忆 (id, scope, key, value, embedding, updated_at)
kb_documents        知识库文档 (id, source, content, metadata)
kb_chunks           知识库分块 (id, doc_id, content, embedding vector(N), hnsw_idx)
                                    — embedding 维度 N 由模型决定, 见第 4 章
eval_datasets       评估数据集 (id, name, scenario, samples jsonb, created_at)
eval_runs           评估运行 (id, dataset_id, model_id, started_at, finished_at, metrics jsonb)
version_snapshots   版本快照 (id, scope, version, payload jsonb, created_at)
                                    — scope: prompt | skill | harness | config
react_events        ReAct 事件流 (id, session_id, turn, event_type, payload jsonb, created_at)
                                    — thinking/tool_call/tool_result/final, 评估回放核心
config_runtime      运行时配置 (key, value jsonb, updated_at)
```

**索引策略**:

- `messages`: `(session_id, created_at)` 复合索引,会话回放主路径。
- `react_events`: `(session_id, turn)` 复合索引 + `created_at` 单列索引(清理用)。
- `kb_chunks.embedding`: HNSW 索引,参数调优见第 4 章。
- `user_memories.embedding`: HNSW 索引(用户记忆语义检索)。
- `version_snapshots`: `(scope, version)` 唯一索引。

**桌面端运维约束**(关键):

桌面端 Postgres 不能像服务端那样无节制写入,必须主动管控磁盘:

1. **流式 token 不直接入库**:WS 推送的 token 仅在内存聚合,`thinking` 块完整后再一次性写入 `react_events`(避免每 token 一次 INSERT)。
2. **`react_events` TTL 清理**:默认保留 30 天,超期自动删除;清理任务在 sidecar 启动时与每日定时执行。
3. **`messages` 归档策略**:会话关闭 90 天后,消息压缩为摘要存入 `sessions.summary`,原始消息转储到 `messages_archive` 表(冷数据,可手动清理)。
4. **`kb_chunks` 去重**:同一文档重新索引时,先标记旧 chunks 为 `deleted`,新 chunks 插入后再物理删除旧记录(避免索引膨胀)。
5. **VACUUM 调度**:每周日凌晨自动 `VACUUM ANALYZE`,避开用户活跃时段;HNSW 索引重建仅在知识库大批量更新后手动触发。
6. **磁盘占用分级告警**:Python Sidecar 每 5 分钟检查 Postgres 数据目录大小,三级阈值响应:
   - 1.5GB:预警,UI 黄色提示"存储空间即将不足,建议清理"。
   - 2GB:禁止新会话,UI 橙色提示"存储空间不足,无法新建会话,请清理后继续"。
   - 3GB:强制清理,自动触发 `react_events` TTL 收紧(保留 7 天)+ `messages_archive` 清理,UI 红色提示"已自动清理过期数据"。

**连接池**:asyncpg 连接池大小 10(桌面端足够),仅 Python Sidecar 持有;Worker 进程为纯计算节点,不访问 DB(见 2.2)。

`[MVP]` 全部表结构 + 基础索引 + TTL 清理 + 磁盘监控实现。
`[V2]` 归档策略自动化;HNSW 索引增量更新优化。

---

### 2.11 Skills/Prompt 混合存储三层流转

Skills 定义与 Prompt 模板采用"文件 → Postgres → UI 编辑"三层流转,兼顾开发期版本控制与运行时动态编辑。

**三层职责**:

```
┌──────────────────────────────────────────────────────────┐
│ 源码目录 (开发期基准, Git 管理)                           │
│  skills/                                                 │
│    office/                                               │
│      manifest.yaml    (Skill 元信息 + Prompt 模板)        │
│      tools.yaml       (MCP 工具声明)                      │
│    data-analysis/                                        │
│    frontend-design/                                      │
└──────────────────────┬───────────────────────────────────┘
                       │ 启动时同步(若 PG 无对应版本)
┌──────────────────────┴───────────────────────────────────┐
│ Postgres (运行时主源)                                     │
│  version_snapshots (scope=skill, payload=完整定义)        │
│  version_snapshots (scope=prompt, payload=模板文本)       │
└──────────────────────┬───────────────────────────────────┘
                       │ 运行时读取
┌──────────────────────┴───────────────────────────────────┐
│ UI 编辑器 (运行时修改)                                    │
│  编辑 Prompt → 写入 PG 新版本 → 立即生效(新会话)          │
└──────────────────────────────────────────────────────────┘
```

**加载优先级**:

1. 运行时请求 Skill → 查 Postgres `version_snapshots` 最新版本。
2. Postgres 无记录 → 回退读取源码目录文件,并写入 Postgres 作为初始版本。
3. 源码目录文件修改 → 启动时检测 hash 变化,自动同步为新版本写入 Postgres(开发期迭代友好)。

**版本化规则**:

- 每次修改生成新 `version`(语义化: `1.0.0` → `1.0.1` 小改 / `1.1.0` 加功能 / `2.0.0` 破坏性)。
- 旧版本不删除,支持回滚(对应第 8 章持续进化机制)。
- 会话启动时锁定 Skill 版本,会话进行中不切换(与 2.8 tools 冻结一致)。

**源码目录结构**:

```
skills/
  office/
    manifest.yaml       # name, version, description, preferred_model_tag
    system_prompt.md    # Skill 的 system prompt 模板
    tools.yaml          # 依赖的 MCP 工具列表
    vars.yaml           # 模板变量默认值
  data-analysis/
    ...
  frontend-design/
    ...
```

**缓存友好关联**(关键约束):

Skill 的 `system_prompt.md` 内容与 `tools.yaml` 声明的工具定义均属于 Frozen Zone(2.8),会话期间不可变。原因:这两部分直接决定 KV Cache 的 prefix hash,运行时变更会导致:

1. prefix hash 失效,KV Cache 全部 miss,后续请求重新计算全量注意力,成本与延迟剧增。
2. `prefix_hash_check` 检测到不一致,抛 `ContextIntegrityError`,ReAct 循环中断。

因此:

- MVP 严格禁止会话进行中切换 Skill 版本;UI 编辑产生的新版本只对后续新会话生效。
- V2 Skills 热加载必须显式处理 cache 失效:切换 Skill → 清空当前会话 KV Cache → 以新 Skill 重建上下文 → 重启 ReAct 循环(等价于新会话,但保留会话 ID 与历史轨迹)。这是"热加载"的真实代价,文档中明确标注,避免开发期误解为无缝切换。

`[MVP]` 三层流转 + 版本化 + 回滚全量实现;会话内禁止切换 Skill。
`[V2]` UI 版本对比工具(diff 可视化);Skills 热加载(需主动清空 cache 并重启会话循环)。

---

### 2.12 配置分层管理

静态 YAML(启动配置) + Postgres(运行时配置),加载时序明确。

**静态配置**(`config.yaml`,源码目录,Git 管理):

```yaml
# 启动期只读
server:
  http_port: 8765
  ws_port: 8766
  sidecar_memory_limit_mb: 1500
  worker_pool_size: 2

models:
  adapters:
    glm:
      api_base: "https://open.bigmodel.cn/api/paas/v4"
      api_key_env: GLM_API_KEY
      default_model: "glm-4-plus"
    deepseek:
      api_base: "https://api.deepseek.com"
      api_key_env: DEEPSEEK_API_KEY
      default_model: "deepseek-chat"
    agnes:
      api_base: "..."
      api_key_env: AGNES_API_KEY
    kimi:
      api_base: "https://api.moonshot.cn/v1"
      api_key_env: KIMI_API_KEY
      default_model: "moonshot-v1-128k"

database:
  host: "127.0.0.1"
  port: 5432
  name: "private_agent"
  pool_size: 10

router:
  type: manual

logging:
  level: "INFO"
  file: "logs/agent.log"
  max_size_mb: 100
  retain_days: 30

cleanup:
  react_events_ttl_days: 30
  vacuum_cron: "0 3 * * 0"   # 每周日凌晨 3 点
```

**运行时配置**(Postgres `config_runtime` 表):

| key | value | 用途 |
|---|---|---|
| `active_skills` | `["office", "data-analysis"]` | 启用的 Skills 列表 |
| `default_model` | `"glm-4-plus"` | UI 默认选中模型 |
| `user_preferences` | `{"theme": "dark", "language": "zh-CN"}` | UI 偏好 |
| `router_rules` | `{}` | V2 路由规则(MVP 为空) |
| `eval_policy` | `{"auto_run": false}` | 评估策略 |

**加载时序**:

1. sidecar 启动 → 读取 `config.yaml` → 校验完整性 → 初始化模型适配器、DB 连接池、Worker 池。
2. 启动完成后 → 读取 Postgres `config_runtime` → 覆盖默认值(如 `default_model`)。
3. 运行时修改 → 通过 `/api/config` 写入 Postgres → 内存缓存同步更新 → 下次请求生效。

**敏感信息管理**(桌面端特殊处理):

API Key 同时支持两种来源,优先级:环境变量 > Postgres 加密配置。

- **环境变量**(开发模式):`api_key_env` 字段指定环境变量名,适合开发期 `.env` 文件管理。
- **Postgres 加密存储**(UI 录入模式):用户在 UI 设置面板录入密钥 → 用本机机器 ID 派生密钥加密(AES-256-GCM)→ 密文存入 `config_runtime.api_keys` → Python Sidecar 启动时解密载入内存。密钥不出内存、不落明文。

这是桌面端必需的兼容逻辑:单人桌面客户端用户无法方便地管理系统环境变量,UI 录入是主路径;环境变量作为开发期与 CI 的备选路径。

| 字段 | 存储 | 加密 | 用途 |
|---|---|---|---|
| `config.yaml` 的 `api_key_env` | 文件 | 无(仅变量名) | 指定从哪个环境变量读取 |
| `config_runtime.api_keys.{provider}` | Postgres | AES-256-GCM(机器 ID 派生) | UI 录入的密钥密文 |

机器 ID 派生密钥的方案保证:密文仅在当前机器可解密,数据库文件被复制到其他机器后无法解密,满足单人桌面端的基础安全需求。

`[MVP]` 静态配置 + 运行时配置全量实现。
`[V2]` UI 配置编辑器;配置变更审计日志。

---

### 2.13 可观测性架构

三层观测:本地文件日志 + Postgres 事件流 + otel 预留接口。

**第一层:结构化日志**(文件落盘 + stdout):

```json
{
  "ts": "2026-07-29T10:23:45.123Z",
  "level": "INFO",
  "module": "react_loop",
  "session_id": "abc123",
  "trace_id": null,
  "span_id": null,
  "event": "tool_call",
  "tool": "search_web",
  "duration_ms": 1200
}
```

`trace_id` / `span_id` 字段 MVP 阶段留空(`null`),V2 接入 otel 后填充,与第三层预留接口对齐。

- 文件路径:`logs/agent.log`(按日轮转,单文件 100MB,保留 30 天)。
- stdout 同步输出(开发期调试用)。
- 级别:`DEBUG`(开发)/ `INFO`(默认)/ `WARN`(降级)/ `ERROR`(中断)。

**第二层:ReAct 事件流**(Postgres `react_events` 表):

完整记录每个会话每轮的 `thinking` / `tool_call` / `tool_result` / `final` 事件,作为评估回放数据源(第 8 章核心依赖)。

```sql
CREATE TABLE react_events (
  id          BIGSERIAL PRIMARY KEY,
  session_id  UUID NOT NULL,
  turn        INT NOT NULL,
  event_type  TEXT NOT NULL CHECK (event_type IN ('thinking','tool_call','tool_result','final','error')),
  payload     JSONB NOT NULL,
  created_at  TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_react_session_turn ON react_events(session_id, turn);
CREATE INDEX idx_react_created ON react_events(created_at);
```

**第三层:otel 预留接口**(不实现,仅预留):

- 所有日志与事件携带 `trace_id` / `span_id` 字段(MVP 阶段留空)。
- V2 阶段接入 OpenTelemetry SDK 时,只需填充这两个字段并导出 span,不破坏现有结构。

**自动清理策略**(对应 2.10 运维约束):

- `react_events`:TTL 30 天,sidecar 启动时执行一次 + 每日凌晨 3 点定时执行。
- `agent.log`:文件轮转 + 30 天保留。
- 清理任务状态写入 `config_runtime.cleanup_last_run`,UI 可查看。

**评估回放路径**:

```
eval_datasets (黄金样本) → 回放 react_events → LLM-as-Judge 评分 → eval_runs 记录指标
```

第 8 章详细展开,本节仅明确数据源就位。

`[MVP]` 前两层全量实现;第三层仅字段预留。
`[V2]` otel 接入;评估回放可视化面板。

---

### 2.14 异常分类与降级策略

四类异常统一分类,每类有明确降级路径,UI 区分展示。

**异常分类体系**:

| 类别 | code 范围 | 典型场景 | 降级策略 |
|---|---|---|---|
| 模型层 | 100-199 | API 超时、限流、响应格式错误、上下文超限 | 1) 重试(指数退避,最多 3 次);2) 切换备用模型;3) 中断并保存轨迹 |
| 工具层 | 200-299 | MCP server 崩溃、超时、返回非法、权限拒绝 | 1) 重试(同工具 1 次);2) 跳过工具,结果回灌错误信息让 Agent 决策;3) 中断 |
| 进程层 | 300-399 | Sidecar 崩溃、worker 死亡、OOM | 1) 主进程重启 Sidecar;2) 恢复最后会话状态;3) 无法恢复则 UI 报错 |
| 用户层 | 400-499 | 主动取消、窗口关闭 | 1) 保存当前轨迹与 checkpoint;2) 标记会话为 `interrupted`;3) 支持后续恢复 |

**降级实现**:

```python
class ErrorHandler:
    async def handle_model_error(self, err: ModelError, ctx, session) -> None:
        if err.code == 101 and err.retry_count < 3:        # 限流
            await asyncio.sleep(2 ** err.retry_count)
            return await self.retry(ctx, session)
        if err.code == 102:                                # 响应格式错误
            await self.switch_to_fallback_model(ctx, session)
            return
        await self.fail(session, err)                      # 中断, 保存轨迹

    async def handle_tool_error(self, err: ToolError, call, ctx, session) -> None:
        if err.code == 201 and not call.retried:           # 超时, 重试 1 次
            call.retried = True
            return await self.retry_tool(call, ctx, session)
        # 跳过工具, 错误信息回灌, 让 Agent 决策
        ctx.append(Message(role="tool", tool_call_id=call.id,
                          content=f'{{"error":"{err.code}","message":"{err}"}}'))
```

**UI 展示规则**:

- 模型层错误:橙色提示,显示"模型 X 不可用,已切换至 Y"或"重试中(第 N 次)"。
- 工具层错误:黄色提示,显示具体工具名与错误,不影响会话继续。
- 进程层错误:红色全屏告警,引导用户重启应用。
- 用户中断:灰色提示,显示"已保存进度,可继续"。

**轨迹保存与 checkpoint**:任何异常导致的中断都必须保存当前 `react_events` 到 Postgres,这是评估"失败案例"的数据源,不能丢失。

**checkpoint 机制**(打通 V2 断点续传链路):

- ReAct 主循环每轮结束自动写入 checkpoint 到 `react_events` 表,`event_type = "checkpoint"`,`payload` 包含当前 `turn`、`ctx` 的序列化摘要(不含完整 messages,仅含结构与长度,用于恢复时重建)。
- MVP 仅存储 checkpoint,不实现恢复逻辑;会话标记 `interrupted` 后,用户可手动发起"继续",但实际是开新会话。
- V2 断点续传:读取最新 `checkpoint` 事件 → 从 `messages` 表恢复完整 ctx → 从中断 turn 继续 ReAct 循环。架构链路在 MVP 阶段已打通,仅缺恢复执行器。

这样消除"用户取消/进程崩溃"与"断点续传"之间的信息割裂,checkpoint 存储位置明确,无需额外表。

`[MVP]` 四类异常处理 + 降级策略 + UI 区分展示 + checkpoint 存储全量实现。
`[V2]` 断点续传执行器(从 checkpoint 恢复 ReAct 循环)。

---

### 2.15 模块代码组织与目录结构

单人开发维持代码整洁的关键是清晰的目录契约,模块边界与 2.1 分层严格对应。

**Python 后端目录**(`backend/`):

```
backend/
  core/                       # 编排层核心
    react_loop.py             # ReAct 主循环 (2.4)
    state_machine.py          # Agent 状态机
    context_manager.py        # 上下文管理 (第 3 章)
    event_bus.py              # 事件总线 (WS 推送 + PG 持久化)
    executor.py               # asyncio + ProcessPool 双轨 (2.6)
  models/                     # 模型适配层
    base.py                   # ModelAdapter 基类 + ModelCapability
    glm_adapter.py
    deepseek_adapter.py
    agnes_adapter.py
    kimi_adapter.py
    registry.py               # 适配器注册表
    router.py                 # 路由抽象层 (2.9)
  tools/                      # 能力层 - 工具
    mcp_client.py             # MCP 统一客户端
    dispatcher.py             # 工具调度器
    sandbox.py                # 沙箱代码执行 (第 6 章)
  skills/                     # 能力层 - Skills 加载
    loader.py                 # 三层流转 (2.11)
    definitions/              # 源码目录基准模板 (yaml/md)
      office/
      data-analysis/
      frontend-design/
  storage/                    # 持久层
    db.py                     # asyncpg 连接池
    repositories/
      sessions.py
      messages.py
      memories.py
      kb.py                   # 知识库 (第 4 章)
      eval.py
      versions.py             # 版本快照
      react_events.py
    cleanup.py                # TTL 与 VACUUM 调度 (2.10)
  eval/                       # 评估闭环 (第 8 章)
    datasets.py
    runner.py                 # 回放执行器
    judge.py                  # LLM-as-Judge
    metrics.py
  api/                        # HTTP + WS 接口
    http_routes.py            # 控制面
    ws_handler.py             # 数据面
    schemas.py                # Pydantic schema
  config/
    loader.py                 # 静态配置加载
    runtime.py                # 运行时配置管理 (2.12)
  observability/
    logger.py                 # 结构化日志
    tracing.py                # otel 预留 (2.13)
  errors.py                   # 异常分类体系 (2.14)
  main.py                     # Python Sidecar 启动入口
  tests/                      # 测试目录(单人迭代必备)
    core/                     # ReAct 循环、状态机、上下文管理单元测试
    models/                   # 适配器 mock 测试
    tools/                    # MCP 工具集成测试
    storage/                  # Repository 单元测试
    eval/                     # 评估回放测试
    replay/                   # 基于 react_events 的回放回归测试
```

**Electron 前端目录**(`frontend/`):

```
frontend/
  main/                       # Electron 主进程
    index.ts                  # 入口
    sidecar.ts                # Python Sidecar 生命周期管理 (2.2)
    window.ts                 # 窗口管理
  preload/
    bridge.ts                 # contextBridge 暴露 HTTP/WS 客户端
  renderer/                   # React UI
    components/
      chat/                   # 会话视图
      react-steps/            # ReAct 步骤渲染
      skills/                 # Skills 管理面板
      eval/                   # 评估面板
      config/                 # 配置面板
    hooks/
      useWebSocket.ts
      useSession.ts
    stores/                   # Zustand 状态管理
    api/
      client.ts               # HTTP 客户端
      ws.ts                   # WS 客户端
    App.tsx
  package.json
```

**依赖规则**:

- `core/` 不依赖 `api/` / `eval/`(核心逻辑不应感知传输层与评估层)。
- `models/` / `tools/` / `skills/` 依赖 `core/` 的抽象,不反向依赖。
- `storage/` 不依赖任何上层,仅暴露 Repository 接口。
- `api/` 依赖 `core/` / `storage/` / `eval/`,是组装层。
- 前端 `renderer/` 通过 `preload/bridge.ts` 访问后端,不直接 import Node 模块。

**包管理**:后端 `uv`(速度优于 pip);前端 `pnpm`(monorepo 友好)。

**MCP SDK 版本锁定**(MCP 2026-07-28 兼容):

`backend/pyproject.toml` 锁定 MCP Python SDK v1.x stable,避免引入 v2.0.0rc1 候选版:

```toml
[project]
dependencies = [
    # MCP SDK:MVP 锁定 v1.x stable(2025-11-25 协议)
    # V2 待 v2.0.0 stable 正式发布后升级(见 5.18 V2 清单 / 9.9 风险十二)
    "mcp>=1.0,<2.0",
    # ... 其他依赖
]
```

> 注:`pip install mcp` 当前仍解析到 v1.x stable,v2.0.0rc1 需显式 `--pre` 安装。MVP 不使用 `--pre`,确保协议路径稳定。config loader(2.12)对 `mcp.protocol_version="2026-07-28"` 抛 `ConfigNotSupportedInMVP`,防止 UI 误改导致静默失败。

`[MVP]` 目录结构全量建立,各模块最小实现就位。
`[V2]` 多 Agent 协作模块新增 `core/multi_agent/`;otel 接入 `observability/otel.py`。

---

### 2.16 MVP 与 V2 架构边界

本章所有设计按 MVP / V2 边界落地,确保"落地优先、不牺牲扩展性"。

**MVP 必须实现**(第 9 章 MVP 路线详述):

| 模块 | MVP 范围 |
|---|---|
| 四层骨架 | 全量(2.1) |
| 进程模型 | 单 Sidecar + 固定 2 worker(2.2) |
| 通信协议 | HTTP + WS 全量(2.3) |
| ReAct 循环 | 单 Agent 完整循环 + 错误降级(2.4) |
| 模型适配 | 四家适配器 + capability 降级(2.7) |
| KV Cache 约束 | 分区模型 + hash 检测(2.8) |
| 模型路由 | `ManualRouter` + 接口就位(2.9) |
| 持久化 | 全部表 + TTL 清理 + 磁盘监控(2.10) |
| Skills 存储 | 三层流转 + 版本化 + 回滚(2.11) |
| 配置管理 | 静态 + 运行时(2.12) |
| 可观测性 | 日志 + 事件流(2.13) |
| 异常处理 | 四类异常 + 降级(2.14) |
| 代码组织 | 目录全量(2.15) |

**V2 预留接口**(不实现,但接口契约就位):

| 模块 | V2 范围 | 预留接口 |
|---|---|---|
| 多 Agent 协作 | `DELEGATING` 状态、子 Agent 委托 | `Agent.state_machine` 扩展点;`core/multi_agent/` 目录占位 |
| 模型路由 | `TagBasedRouter` / `CostAwareRouter` | `Router` Protocol + 配置注入机制(2.9) |
| 断点续传 | 进程崩溃后恢复 ReAct | `react_events` 表已支持;`react_loop` 预留 checkpoint hook |
| otel 链路追踪 | 分布式 trace | 日志与事件流 `trace_id` / `span_id` 字段预留(2.13) |
| Skills 热加载 | 会话进行中切换 Skill | `skills/loader.py` 预留热加载接口(需配合 cache 失效) |
| 多会话并行 | WS 背压控制、多路复用 | `ws_handler` 设计已支持 `session_id` 路由(2.3) |
| 打包内嵌 Python | Electron + pyinstaller | Sidecar 启动协议不变,仅打包方式调整(2.2) |

**架构边界守护原则**:

1. V2 预留接口必须在 MVP 阶段以"空实现"或"Protocol 定义"形式存在,不允许"以后再加"。
2. MVP 实现不得依赖 V2 接口的具体实现(仅依赖抽象)。
3. 每次架构变更必须更新本表,确保边界清晰。

**与三大约束的对应**:

- 上下文质量优先 → 2.8 KV Cache 分区 + hash 检测;2.4 ReAct 循环纯净(压缩逻辑外置)。
- 缓存友好 → 2.8 分区模型;2.7 各适配器 cache 行为映射;2.11 Skills 版本与会话绑定。
- 评估驱动迭代 → 2.13 事件流持久化;2.10 评估数据集与版本快照表;2.14 异常轨迹保存;2.16 V2 接口预留。

---

第 2 章起草完成。本章确立了四层架构、进程模型、通信契约、ReAct 状态机、模型适配、KV Cache 约束、路由抽象、持久化边界、配置分层、可观测性、异常降级、代码组织与 MVP/V2 边界,共 16 节,所有决策严格复用前序锁定结论,未引入新方案。

后续各章(3-8)在本章定义的分层与契约内展开具体实现:

- 第 3 章:展开 2.4 `context_manager` 与 2.8 KV Cache 分区的实现细节。
- 第 4 章:展开 2.10 `kb_chunks` 表与 HNSW 索引的工程落地。
- 第 5 章:展开 2.6 工具调度与 MCP 客户端。
- 第 6 章:展开 2.15 `tools/sandbox.py` 与 Trae Code 执行机制复用。
- 第 7 章:展开 2.11 Skills 三层流转的场景化落地。
- 第 8 章:展开 2.13 事件流回放与 2.10 版本快照的评估闭环。

---

## 第 3 章 上下文工程层

本章是"上下文质量优先"与"缓存友好"两大第一性约束的工程落地。第 2 章定义了 KV Cache 分区模型(2.8)与 ReAct 循环中的 `maybe_compress` 钩子(2.4),本章展开 `context_manager` 的完整实现:分区元数据、构建流水线、hash 校验、状态栏、模板变量、工具规范、压缩策略、注入防护、计费感知。

核心原则:所有内部 metadata 字段(`zone` / `compressed` / `compressed_from` 等)**仅本地存储,不进入模型 API 请求**;透传给模型的消息严格遵循 OpenAI messages 格式(2.4 决策)。

每节末尾以 `[MVP]` 或 `[V2]` 标注实现边界。

---

### 3.1 上下文管理器的职责定位

`context_manager` 是 ReAct 主循环与模型适配层之间的**唯一中间件**,承担第 2 章 2.4 节 `maybe_compress` 钩子的全部职责。

**职责清单**:

| 职责 | 输入 | 输出 | 对应约束 |
|---|---|---|---|
| 构建 ctx | 会话 ID + 用户消息 | 完整 messages 列表 | 上下文质量 |
| 分区管理 | messages 列表 | 带 zone 标记的 messages | 缓存友好 |
| hash 校验 | Frozen + Stable Zone | 通过 / `ContextIntegrityError` | 缓存友好 |
| 压缩触发 | ctx 状态 + 触发条件 | 压缩后的 ctx | 上下文质量 |
| 状态栏注入 | 当前状态 | 状态栏消息 | 上下文质量 |
| 模板变量解析 | Skill system_prompt 模板 | 渲染后的 system prompt | 缓存友好 |

**边界**(不做的事):

- 不承担模型调用(由 `model_adapter` 负责)
- 不承担工具调度(由 `tool_dispatcher` 负责)
- 不承担消息持久化(由 `messages` Repository 负责,context_manager 仅读写)
- 不承担评估回放(由 `eval/runner` 负责,context_manager 仅保证轨迹完整)

**调用关系**:

```
react_loop → context_manager.build() → ctx
react_loop → model_adapter.stream(ctx) → msg
react_loop → context_manager.maybe_compress(ctx) → ctx'
```

context_manager 是无状态的(所有状态在 Postgres 与会话内存中),便于未来多 Agent 协作时共享。

`[MVP]` 全部职责实现;无状态设计。
`[V2]` 多 Agent 场景下的上下文隔离与共享机制。

---

### 3.2 消息列表的分区元数据模型

2.8 节定义了 Frozen / Stable / Active 三区分区模型,本节给出工程落地。

**Message 结构扩展**(在 2.4 基础上):

```python
@dataclass
class Message:
    # === OpenAI 兼容字段(透传给模型 API)===
    role: Literal["system", "user", "assistant", "tool"]
    content: str | None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    name: str | None = None
    # === 内部 metadata(不进入 API 请求)===
    zone: Literal["frozen", "stable", "active"] | None = None
    compressed: bool = False                  # 是否已被压缩(原文保留但不进 API)
    compressed_from: list[int] | None = None  # 压缩产生时,源 message id 列表
    msg_id: int | None = None                 # Postgres messages 表主键
    turn: int | None = None                   # 所属 ReAct 轮次
```

**关键约定**:序列化给模型 API 时,`to_api_messages()`(2.7 适配器基类)仅输出 OpenAI 兼容字段,丢弃所有 metadata。metadata 仅用于内部管理与持久化。

**分区判定规则**:

| zone | 包含内容 | 生命周期 | 可变性 |
|---|---|---|---|
| `frozen` | Skill system prompt + tools 定义 | 会话级,启动时锁定 | 不可变(违反则抛 `ContextIntegrityError`) |
| `stable` | 长期记忆摘要 + 知识库检索片段 | 会话级,可合并压缩 | 仅整体替换(合并压缩时),不局部修改 |
| `active` | ReAct 历史(thinking/tool_call/tool_result)+ 当前用户消息 + 状态栏 | 每轮追加 | 可追加、可压缩(标记 `compressed` 但保留原文) |

**分区边界标记**:messages 列表中通过 `zone` 字段标记,不依赖位置。构建 ctx 时按 `frozen → stable → active` 顺序排列,保证 prefix 稳定。

`[MVP]` 元数据模型 + 分区判定全量实现。
`[V2]` 跨会话的 Frozen Zone 共享(同 Skill 配置的会话复用 cache key)。

---

### 3.3 上下文构建流水线

从会话启动到每轮 ReAct 的 ctx 构建流程,严格遵循分区顺序。

**会话启动时构建**(一次性):

```
1. 加载 Skill 定义(2.11 三层流转)
   → system_prompt 模板 + tools.yaml
2. 解析模板变量(3.7)
   → 渲染后的 system prompt
3. 加载长期记忆摘要
   → 从 user_memories 读取当前用户摘要
4. 初始化空 Active Zone
5. 计算 Frozen + Stable Zone 的 hash(基准)
   → 存入会话内存:base_frozen_hash / base_stable_hash
6. 输出初始 ctx
```

**每轮 ReAct 构建**(循环内):

```
1. 读取会话内存中的 messages 列表(含 metadata)
2. 过滤掉 compressed=True 的消息(不进 API)
3. 按 zone 排序:frozen → stable → active
4. 在 Active Zone 尾部追加当前用户消息
5. 若为用户消息轮:注入状态栏(3.5)
6. hash 校验(3.4)
7. 输出完整 ctx 给 model_adapter
```

**伪代码**:

```python
class ContextManager:
    async def build(self, session: Session, user_msg: Message) -> list[Message]:
        messages = await self.messages_repo.get(session.id)
        # 过滤已压缩消息
        active = [m for m in messages if not m.compressed]
        # 分区排序
        active.sort(key=lambda m: (zone_order(m.zone), m.turn or 0))
        # 追加用户消息
        user_msg.zone = "active"
        user_msg.turn = session.current_turn
        active.append(user_msg)
        # 状态栏注入(仅用户消息轮)
        if user_msg.role == "user":
            status = await self.build_status_bar(session)
            active.append(status)
        # hash 校验
        await self.verify_hash(active)
        return active

    async def maybe_compress(self, ctx: list[Message]) -> list[Message]:
        # 见 3.10-3.12
        ...
```

**缓存友好保证**:每轮构建只追加 Active Zone,不动 Frozen + Stable Zone,保证模型 API 请求的 messages 前缀稳定。

`[MVP]` 启动构建 + 每轮构建全量实现。
`[V2]` 增量构建(仅重建变化部分,提升大上下文场景性能)。

---

### 3.4 KV Cache prefix 稳定性的 hash 校验

2.8 节架构约束的工程实现。每次构建 ctx 时强制校验前缀完整性。

**hash 计算规则**:

```python
def compute_zone_hash(messages: list[Message], zone: str) -> str:
    """计算指定 zone 的 hash,仅含 OpenAI 兼容字段"""
    zone_msgs = [m for m in messages if m.zone == zone and not m.compressed]
    # 序列化为 canonical JSON(键排序,保证可重现)
    canonical = json.dumps(
        [{"role": m.role, "content": m.content,
          "tool_calls": m.tool_calls, "tool_call_id": m.tool_call_id}
         for m in zone_msgs],
        sort_keys=True, ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode()).hexdigest()
```

**校验流程**(会话维度标志,避免多会话并发竞态):

```python
async def verify_hash(self, session: Session, messages: list[Message]) -> None:
    frozen_hash = compute_zone_hash(messages, "frozen")
    if frozen_hash != session.base_frozen_hash:
        raise ContextIntegrityError(
            f"Frozen Zone hash mismatch: expected {session.base_frozen_hash}, "
            f"got {frozen_hash}"
        )
    # Stable Zone:允许合并压缩后更新基准,其他变更视为违规
    # 标志存于会话对象,避免全局标志在多会话场景下的并发污染
    stable_hash = compute_zone_hash(messages, "stable")
    if stable_hash != session.base_stable_hash and not session.is_stable_merging:
        raise ContextIntegrityError(
            f"Stable Zone hash mismatch: expected {session.base_stable_hash}, "
            f"got {stable_hash}"
        )
```

**Stable Zone 合并时的 hash 更新**:

- 合并压缩开始:设置 `session.is_stable_merging = True`(仅当前会话放行 hash 变更)
- 合并完成:新 Stable Zone 入库,`session.base_stable_hash` 更新为新 hash
- 快照 payload 同步存入当时的 `stable_hash`,供评估回放校验历史前缀完整性
- `session.is_stable_merging = False`

`is_stable_merging` 与 `base_stable_hash` 均为会话级字段,存于 Session 内存对象,不共享给其他会话。这是唯一允许 Stable Zone hash 变更的路径,其他任何变更都是违规。

**hash 不覆盖的范围**:Active Zone 不校验(每轮追加是正常的);tool_call 与 tool_result 的配对完整性由 ReAct 循环保证(2.4)。

`[MVP]` SHA-256 + canonical JSON + 校验流程全量实现。
`[V2]` 增量 hash(仅校验变更的 zone,降低大上下文场景的计算开销)。

---

### 3.5 状态栏机制的设计与内容

决策 2(a/b/c):状态栏注入当前时间戳、用户偏好、会话元信息。

**状态栏内容 schema**:

```yaml
status_bar:
  timestamp: "2026-07-29T10:23:45+08:00"      # (a) 当前时间戳
  user_preferences:                            # (b) 用户偏好
    language: "zh-CN"
    timezone: "Asia/Shanghai"
    theme: "dark"
  session:                                     # (c) 会话元信息
    id: "abc123"
    turn: 5
    context_window_used: 4567
    context_window_limit: 128000
    remaining_budget: 123433
```

**状态栏消息格式**:作为独立 `user` 消息注入,`zone = "active"`,格式为结构化文本(非 system role,避免污染 Frozen Zone):

```python
STATUS_BAR_TEMPLATE = """[Current Status]
Time: {timestamp}
User Preferences: language={language}, timezone={timezone}
Session: turn={turn}, context_used={used}/{limit} tokens"""
```

**注入示例**(消息列表尾部):

```python
Message(
    role="user",
    content="[Current Status]\nTime: 2026-07-29T10:23:45+08:00\n"
            "User Preferences: language=zh-CN, timezone=Asia/Shanghai\n"
            "Session: turn=5, context_used=4567/128000 tokens",
    zone="active",
    turn=5,
)
```

**关键约定**:

- 状态栏是 `user` role 而非 `system` role,因为 system role 属于 Frozen Zone,动态信息不能污染。
- 状态栏内容是结构化文本而非 JSON,便于模型理解且不破坏 OpenAI messages 格式。
- 状态栏 `msg_id` 与 `turn` 正常记录,评估回放时可见。

`[MVP]` 三类状态字段 + 注入机制全量实现。
`[V2]` 状态字段可配置(UI 编辑);按需注入(模型主动查询)。

---

### 3.6 状态栏注入时机与缓存友好

决策 3(B):仅在用户消息时注入,工具结果轮次不注入。

**注入时序**:

```
轮次 1: user_msg → status_bar → (model thinking) → tool_call → tool_result
轮次 2: (model thinking) → tool_call → tool_result    ← 无状态栏
轮次 3: (model thinking) → final                       ← 无状态栏
轮次 4: user_msg → status_bar → ...                    ← 新用户消息,重新注入
```

**缓存友好的体现**:

- Frozen + Stable Zone 不变 → prefix hash 不变 → KV Cache 命中。
- Active Zone 每轮追加是正常的,模型 API 的 prefix cache 仍能命中前缀(Frozen + Stable + 早期 Active)。
- 状态栏仅在用户消息轮注入,避免每轮都重建 Active Zone 尾部(若每轮注入,工具结果轮也会被打断)。

**注入位置**:状态栏在用户消息**之后**追加,作为 Active Zone 的最后一条消息(在模型 thinking 之前)。这样模型先看到用户意图,再看到当前状态上下文。

**与 hash 校验的关系**:状态栏在 Active Zone,不影响 Frozen/Stable Zone 的 hash。每轮注入新状态栏是 Active Zone 正常追加,不触发 `ContextIntegrityError`。

`[MVP]` 用户消息轮注入机制全量实现。
`[V2]` 按需注入(模型调用 `get_status` 工具查询,减少自动注入的开销)。

---

### 3.7 提示工程模板变量体系与解析器

决策 7(a/b/c/e/f + `{{var}}` 语法)。本节合并原要点 7 与 8(模板变量体系 + 解析器实现)。

**变量命名空间**:

| 命名空间 | 示例 | 来源 | 解析时机 |
|---|---|---|---|
| `user.*` | `{{user.name}}` `{{user.preferences}}` | `user_memories` 表 | 会话启动时 |
| `session.*` | `{{session.id}}` `{{session.created_at}}` | 会话元信息 | 会话启动时 |
| `env.*` | `{{env.os}}` `{{env.version}}` | 系统环境 | 会话启动时 |
| `skills.*` | `{{skills.active}}` `{{skills.tools}}` | Skill 定义 | 会话启动时 |
| `kb.*` | `{{kb.context}}` | RAG 检索结果(第 4 章) | 运行时(每次检索) |

**关键区分**:`user.*` / `session.*` / `env.*` / `skills.*` 在会话启动时解析一次,渲染后的 system prompt 进入 Frozen Zone;`kb.*` 在运行时动态解析,放入 Stable Zone(不进 Frozen Zone)。

**变量语法**:`{{var}}` 简单字符串替换,非 Jinja2。

```python
class TemplateResolver:
    NAMESPACE_LOADERS = {
        "user": load_user_vars,
        "session": load_session_vars,
        "env": load_env_vars,
        "skills": load_skills_vars,
    }

    def resolve(self, template: str, session: Session) -> str:
        """会话启动时解析,渲染 Frozen Zone 模板"""
        vars_map = {}
        for ns, loader in self.NAMESPACE_LOADERS.items():
            vars_map.update(loader(session))
        return self._replace(template, vars_map)

    def _replace(self, template: str, vars_map: dict) -> str:
        # 简单字符串替换,不支持表达式/循环/条件
        result = template
        for key, value in vars_map.items():
            result = result.replace(f"{{{{{key}}}}}", str(value))
        return result
```

**缺失变量处理**:未匹配的 `{{var}}` 保留原样(不报错,不置空),便于调试时发现拼写错误。日志记录未解析的变量名。

**`{{kb.context}}` 的特殊处理**:

- 不在会话启动时解析(此时无检索结果)。
- 运行时 RAG 检索后(第 4 章),检索结果作为新 `user` 消息追加到 Stable Zone,content 包含检索片段。
- `{{kb.context}}` 在 system prompt 中仅作为占位符提示模型"知识库内容会在后续消息中提供",实际内容由 RAG 注入。

**安全考量**:`{{var}}` 替换不支持任意表达式,避免 Jinja2 的模板注入风险。变量值来自受信任来源(数据库/系统环境),不接受用户输入直接作为变量名。

**示例**(Skill system_prompt.md 片段):

```markdown
你是 {{user.name}} 的个人助手,运行于 {{env.os}} 环境。
当前会话 {{session.id}} 创建于 {{session.created_at}}。
已启用技能:{{skills.active}}。
知识库内容将在后续消息中以 [KB Context] 标记提供:{{kb.context}}
```

**解析后**(Frozen Zone):

```markdown
你是 张三 的个人助手,运行于 Windows 11 环境。
当前会话 abc123 创建于 2026-07-29T10:00:00+08:00。
已启用技能:office, data-analysis。
知识库内容将在后续消息中以 [KB Context] 标记提供:{{kb.context}}
```

`{{kb.context}}` 保留原样,运行时由 RAG 注入实际内容到 Stable Zone。

**`{{kb.context}}` 双模式配置**(应对模型混淆占位符与真实 KB 内容的问题):

```yaml
# config.yaml
template:
  kb_replace_mode: false  # 默认 false;true 为运行时替换模式
```

| 模式 | 行为 | 代价 | 适用场景 |
|---|---|---|---|
| `false`(MVP 默认) | system prompt 保留 `{{kb.context}}` 占位符;KB 内容以独立 `[KB Context]` user 消息放入 Stable Zone | 模型可能混淆占位符与真实内容,降低检索利用率 | 通用场景,KV Cache 友好 |
| `true`(V2 可选) | 每次 RAG 后重新渲染 system prompt,直接替换 `{{kb.context}}` 为检索摘要 | 触发 Frozen Zone hash 变更,KV Cache 全部 miss,token 消耗增加 | 低上下文成本场景,或模型对占位符不敏感时 |

MVP 固定使用 `false` 模式(缓存友好优先);`true` 模式作为 V2 配置项预留,用户可按需切换。

`[MVP]` 五类命名空间 + `{{var}}` 解析器 + 缺失变量保留 + `kb_replace_mode=false` 默认实现。
`[V2]` `kb_replace_mode=true` 运行时替换;条件变量(`{{#if user.is_admin}}...{{/if}}`);变量值缓存。

---

### 3.8 工具描述规范与扩展字段

决策 8(A+B):严格遵循 OpenAI function calling schema + 扩展字段。

**内部工具定义**(扩展字段):

```python
@dataclass
class ToolDef:
    # === OpenAI function calling schema(透传给 API)===
    name: str
    description: str
    parameters: dict          # JSON Schema 2020-12(超集,兼容旧 draft;MCP 2026-07-28 升级)
    output_schema: dict | None = None  # JSON Schema 2020-12,V2 启用(MCP 2026-07-28 resultType 强制)
    # === 扩展字段(仅内部调度,不进 API)===
    category: Literal["perception", "execution", "collaboration",
                      "event", "communication"]
    safety_level: Literal["safe", "elevated", "dangerous"]
    timeout_seconds: int = 30
    sequential: bool = False  # 是否强制串行(如数据库写)
```

**透传给模型 API 的格式**(适配器 `to_api_tools`):

```python
def to_api_tools(self, tools: list[ToolDef]) -> list[dict]:
    return [{
        "type": "function",
        "function": {
            "name": t.name,
            "description": t.description,
            "parameters": t.parameters,
        }
    } for t in tools]
```

> **MCP 2026-07-28 兼容注记**:`parameters` 已升级为 JSON Schema 2020-12 超集,`output_schema` 字段为 V2 预留。透传前需做 provider 兼容性校验(见 9.9 风险十二 R-2):GLM/DeepSeek/Kimi/Agnes 的 function calling 是否接受 `oneOf`/`anyOf`/`$ref` 未经验证,若某 provider 拒绝,需在透传前做 schema 降级转换(剥离 2020-12 独有关键字)。MVP 锁定 MCP `2025-11-25` 协议,`2026-07-28` 的 `resultType` 强制要求降级至 V2。

扩展字段(`category` / `safety_level` / `timeout_seconds` / `sequential`)由 `tool_dispatcher`(2.6)消费,用于调度决策:

- `category`:五类工具设计原则(感知/执行/协作/事件/沟通),第 5 章展开。
- `safety_level`:`safe` 直接执行;`elevated` 需记录审计;`dangerous` 需用户确认(第 5 章工具安全)。
- `timeout_seconds`:覆盖默认 30s 超时(2.6)。
- `sequential`:强制串行执行,避免并发冲突。

**工具描述编写规范**:

- `name`:小写下划线,动词开头(如 `search_web` / `read_file`)。
- `description`:一句话说明用途 + 一句话说明参数 + 一句话说明返回。避免歧义。
- `parameters`:严格 JSON Schema,必填字段标注 `required`。

**示例**:

```python
ToolDef(
    name="search_web",
    description="Search the web for current information. "
                "Args: query (str, required), max_results (int, default 5). "
                "Returns: list of {title, url, snippet}.",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "max_results": {"type": "integer", "default": 5},
        },
        "required": ["query"],
    },
    category="perception",
    safety_level="safe",
    timeout_seconds=15,
    sequential=False,
)
```

`[MVP]` OpenAI schema + 扩展字段全量实现;编写规范文档化。
`[V2]` 工具组(一个工具声明多个相关操作,如 `db.query` / `db.insert` 合并)。

---

### 3.9 上下文压缩触发条件矩阵

决策 4(a/b/e):三种触发条件,优先级与响应策略明确。

**触发条件**:

| 触发条件 | 判定逻辑 | 触发动作 | 优先级 |
|---|---|---|---|
| (a) token 超限 | `len(ctx) > capability.context_window * 0.8` | 调用滑动窗口 + 摘要压缩 | 1(最高) |
| (b) Active Zone 轮次超限 | `active_turns > 10` | 调用滑动窗口 + 摘要压缩 | 2 |
| (e) API 返回超限错误 | 模型适配器捕获 103 错误 | 立即强制压缩(更激进阈值) | 0(紧急) |

**触发检查时机**:每轮 ReAct 结束后,`maybe_compress` 钩子统一检查:

```python
async def maybe_compress(self, ctx: list[Message]) -> list[Message]:
    # 检查触发条件
    triggers = []
    if self._token_exceeds(ctx, threshold=0.8):
        triggers.append("token_limit")
    if self._active_turns_exceed(ctx, limit=10):
        triggers.append("turn_limit")

    if triggers:
        ctx = await self._apply_compression(ctx, triggers, aggressive=False)
    return ctx

async def handle_context_overflow(self, ctx: list[Message]) -> list[Message]:
    """API 返回 103 错误时的紧急压缩"""
    return await self._apply_compression(ctx, ["api_overflow"], aggressive=True)
```

**激进模式**(`aggressive=True`):阈值从 80% 降到 50%,滑动窗口从保留 6 轮降到 3 轮,确保下次请求不再超限。

**Stable Zone 合并压缩的独立触发**:不参与上述矩阵,由轮次计数器独立触发(3.12),避免与 Active Zone 压缩耦合。

`[MVP]` 三种触发条件 + 优先级 + 激进模式全量实现。
`[V2]` 用户主动触发(UI 按钮);基于模型反馈的自适应阈值。

---

### 3.10 三类压缩策略统一实现

合并原要点 11(滑动窗口)+ 12(摘要)+ 13(Stable Zone 合并)。三种策略同属压缩实现,集中讲解。

#### 3.10.1 滑动窗口压缩

保留最近 N 轮(N 可配,默认 6),更早的 Active Zone 消息标记 `compressed` 但保留原文。

**窗口边界对齐**(关键):不切断 tool_call 与 tool_result 配对,支持多轮连环工具调用。

**边界场景**:多轮连环工具调用(turn5 tool_call → turn6 tool_result → turn7 tool_call → turn8 tool_result),若截断点落在 turn6,仅扩展到 turn6 会拆分 turn7/8 配对。需全局匹配所有工具配对。

```python
def _sliding_window(self, ctx: list[Message], keep_turns: int = 6) -> list[Message]:
    active = [m for m in ctx if m.zone == "active" and not m.compressed]
    if len(active) <= keep_turns * 3:
        return ctx

    # 初始保留边界:最近 keep_turns 轮的最早 turn
    keep_from_turn = max(m.turn or 0 for m in active) - keep_turns + 1

    # 全局建立 call_id → (assistant_turn, tool_turn) 映射
    call_id_map: dict[str, tuple[int, int | None]] = {}
    for m in active:
        if m.role == "assistant" and m.tool_calls:
            for tc in (m.tool_calls or []):
                call_id_map[tc.id] = (m.turn or 0, None)
        elif m.role == "tool" and m.tool_call_id in call_id_map:
            asst_turn, _ = call_id_map[m.tool_call_id]
            call_id_map[m.tool_call_id] = (asst_turn, m.turn or 0)

    # 扩展边界:任何跨越 keep_from_turn 的工具配对,取两者的最小 turn
    for asst_turn, tool_turn in call_id_map.values():
        if tool_turn is None:
            continue
        # 若 assistant 在保留区但 tool_result 在裁剪区(或反之),扩展边界
        if (asst_turn >= keep_from_turn) != (tool_turn >= keep_from_turn):
            keep_from_turn = min(keep_from_turn, asst_turn, tool_turn)

    # 兜底:若仍存在无法配对的 tool_call(如 tool_result 丢失),多保留 2 轮
    unpaired = [asst for asst, tool in call_id_map.values() if tool is None]
    if unpaired and min(unpaired) >= keep_from_turn:
        keep_from_turn = max(0, keep_from_turn - 2)
        logger.warning(
            f"Sliding window: unpaired tool_call in session, "
            f"extended keep_from_turn to {keep_from_turn}"
        )

    # 标记被裁剪的消息
    for m in active:
        if (m.turn or 0) < keep_from_turn:
            m.compressed = True
    return ctx
```

**与 hash 的关系**:窗口滑动不改 Frozen/Stable Zone,hash 不变。被标记 `compressed` 的消息不进入下次 API 请求,但保留在 `messages` 表供评估回放。

#### 3.10.2 摘要压缩

对被滑动窗口裁剪的消息生成摘要,作为新 `assistant` 消息追加到 Active Zone。

**触发时机**:滑动窗口执行后,对被裁剪的消息批量摘要。

```python
async def _summarize(self, ctx: list[Message], compressed_msgs: list[Message]) -> Message:
    # 构造摘要请求
    summary_prompt = self._build_summary_prompt(compressed_msgs)
    # 调用压缩专用模型(3.11)
    summary = await self.compress_model_adapter.stream(
        ctx=[Message(role="user", content=summary_prompt)],
        tools=[]  # 压缩模型不调用工具
    )
    return Message(
        role="assistant",
        content=f"[Previous Context Summary]\n{summary.content}",
        zone="active",
        compressed_from=[m.msg_id for m in compressed_msgs],
    )
```

**摘要 prompt 模板**:

```
请将以下对话历史压缩为简洁摘要,保留:
1. 用户的核心需求与目标
2. 已做出的关键决策
3. 已获取的重要信息(工具结果要点)
4. 待完成的任务

对话历史:
{compressed_messages}

输出格式:结构化文本,不超过 500 字。
```

**摘要质量保证**:关键信息保留要求在 prompt 中明确;摘要后旧消息标记 `compressed` 保留原文(评估可回溯)。

#### 3.10.3 Stable Zone 合并压缩

决策 5(e) + 2.8 规则:每 N 轮(N=5)合并 Stable Zone 检索片段。

**触发条件**:

```python
def _should_merge_stable(self, ctx: list[Message]) -> bool:
    stable = [m for m in ctx if m.zone == "stable" and not m.compressed]
    # 条件 1:轮次达阈值(每 5 轮)
    if self.current_turn % 5 == 0 and self.current_turn > 0:
        return True
    # 条件 2:检索片段数超阈值(超过 20 条)
    kb_chunks = [m for m in stable if "[KB Context]" in (m.content or "")]
    if len(kb_chunks) > 20:
        return True
    return False
```

**合并执行**:

```python
async def _merge_stable_zone(self, ctx: list[Message]) -> list[Message]:
    # 设置标志,允许 hash 变更(3.4)
    self.stable_zone_merging = True

    stable = [m for m in ctx if m.zone == "stable" and not m.compressed]
    # 调用模型合并摘要
    merged = await self.compress_model_adapter.stream(
        ctx=[Message(role="user", content=self._build_merge_prompt(stable))],
        tools=[]
    )
    # 旧 stable 标记 compressed,存档到 version_snapshots
    await self.versions_repo.save_snapshot(
        scope="stable_zone",
        version=f"turn-{self.current_turn}",
        payload={"messages": [m.to_dict() for m in stable]}
    )
    for m in stable:
        m.compressed = True
    # 新 stable 消息追加
    new_msg = Message(
        role="user",
        content=f"[Merged KB Context]\n{merged.content}",
        zone="stable",
    )
    ctx.append(new_msg)
    # 更新 base_stable_hash
    self.base_stable_hash = compute_zone_hash(ctx, "stable")
    self.stable_zone_merging = False
    return ctx
```

**关键约束**:

- 合并是**整体替换**:所有旧 Stable Zone 消息标记 `compressed`,新合并消息作为唯一 Stable Zone。
- 历史快照存 `version_snapshots`(scope=`stable_zone`),供评估回放。
- hash 在合并期间允许变更(通过 `stable_zone_merging` 标志),合并完成后更新基准。

`[MVP]` 三类压缩策略全量实现;窗口边界对齐;摘要 prompt 模板;Stable Zone 合并存档。
`[V2]` 重要性裁剪(基于模型/规则判断消息重要性);结构化提取(从对话提取决策/事实/待办存入结构化字段)。

---

### 3.11 压缩执行模型选型

决策 6(D):用户可配置压缩用模型,默认便宜快速模型。

**配置位置**:

```yaml
# config.yaml
compression:
  default_model: "glm-4-flash"    # 默认压缩模型
  allow_user_override: true        # 允许 UI 覆盖
```

```python
# config_runtime 表
{
  "compress_model": "glm-4-flash"  # 用户可改
}
```

**适配器复用**:压缩模型也走 2.7 适配层(统一接口),但标记 `compress_mode: true`,与主会话模型区分:

```python
class ContextManager:
    def __init__(self, ...):
        self.compress_model_id = config.compress_model
        self.compress_adapter = model_registry.get(self.compress_model_id)

    async def _summarize(self, ...):
        # 压缩模型不调用工具,不流式,不走状态栏
        result = await self.compress_adapter.stream(
            ctx=[Message(role="user", content=summary_prompt)],
            tools=[]
        )
        return result
```

**计费隔离**:压缩调用的 token 消耗单独记录到 `react_events`,`event_type = "compress"`,不混入主会话的 token 统计(3.13)。

**模型不可用降级**:压缩模型不可用时,降级使用当前会话主模型(日志告警,成本上升但不中断功能)。

`[MVP]` 可配置压缩模型 + 适配器复用 + 计费隔离全量实现。
`[V2]` 压缩模型路由(按摘要长度/复杂度选择模型);本地小模型支持。

---

### 3.12 提示注入防护机制

决策 9(B):`tool` role 天然隔离 + 长度限制 + 关键词过滤。

**第一层:role 隔离**(OpenAI 格式天然支持)

工具返回结果走 `tool` role,模型在解析时知道这是工具输出而非系统指令。这是最基础的隔离,无需额外实现。

**第二层:长度限制**(按工具来源差异化,见下方沙箱规则)

```python
def _truncate_tool_result(self, result: str,
                          source: Literal["mcp", "sandbox"] = "mcp") -> str:
    limit = self._get_truncation_limit(source)
    tokens = self.token_estimator.estimate(result)
    if tokens <= limit:
        return result
    truncated = result[:limit * 3]  # 粗略按 3 字符/token
    return f"{truncated}\n\n[Result truncated: original {tokens} tokens]"
```

**第三层:关键词过滤(中英文 + 高低风险分级)**

```python
# 高危模式:角色劫持、清空前置指令 → 推送 UI 告警 + 入库记录
HIGH_RISK_PATTERNS = [
    # 英文
    r"ignore\s+(previous|above|prior)\s+(instructions?|prompt)",
    r"disregard\s+(above|prior|previous)",
    r"you\s+are\s+now\s+(a|an)\s+\w+",
    r"<\s*system\s*>",
    # 中文
    r"忽略(前面|以上|上文|全部)指令",
    r"无视前文所有设定",
    r"你现在切换成(管理员|开发者|系统)",
]

# 低风险模式:单纯关键词 → 仅日志记录,不推送前端
LOW_RISK_PATTERNS = [
    r"system\s*:\s*",       # 伪 system 指令(可能是合法文本)
    r"系统指令[:：]",
]

def _scan_injection(self, tool_result: str, call_id: str,
                    source: Literal["mcp", "sandbox"] = "mcp") -> InjectionScanResult:
    high_alerts = []
    low_alerts = []
    for pattern in HIGH_RISK_PATTERNS:
        if re.search(pattern, tool_result, re.IGNORECASE):
            high_alerts.append(InjectionAlert(
                pattern=pattern, call_id=call_id, risk="high",
                source=source, snippet=tool_result[:200]
            ))
    for pattern in LOW_RISK_PATTERNS:
        if re.search(pattern, tool_result, re.IGNORECASE):
            low_alerts.append(InjectionAlert(
                pattern=pattern, call_id=call_id, risk="low",
                source=source, snippet=tool_result[:200]
            ))
    return InjectionScanResult(high_alerts=high_alerts, low_alerts=low_alerts)
```

**处理策略分级**:

- **高危模式**:推送 UI 告警 + 写入 `react_events`(供评估分析),用户可见。
- **低风险模式**:仅写入本地日志,不推送前端,避免误报干扰日常调试。

**沙箱与 MCP 工具的差异化规则**:

沙箱代码执行输出(第 6 章)注入风险更高,因模型可执行任意代码,输出可能包含构造性注入内容。差异化处理:

```python
MAX_TOOL_RESULT_TOKENS_MCP = 4000       # MCP 工具默认 4k token
MAX_TOOL_RESULT_TOKENS_SANDBOX = 2000   # 沙箱输出更严格 2k token

def _get_truncation_limit(self, source: str) -> int:
    return MAX_TOOL_RESULT_TOKENS_SANDBOX if source == "sandbox" \
           else MAX_TOOL_RESULT_TOKENS_MCP
```

沙箱输出触发更严格截断,降低构造性注入的风险。

`[MVP]` 三层防护 + 告警不阻断 + 配置可调全量实现。
`[V2]` 输出层校验(检测模型输出是否被操纵,如是否泄漏 system prompt);基于 LLM-as-Judge 的注入检测。

---

### 3.13 上下文预算与计费感知

决策 10(B):每轮记录 token 消耗与成本,UI 展示,不主动干预。

**token 记录**:模型 API 响应通常包含 usage 信息,适配器统一提取:

```python
@dataclass
class ModelResponse:
    content: str
    tool_calls: list[ToolCall] | None
    usage: TokenUsage | None

@dataclass
class TokenUsage:
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cached_tokens: int = 0   # KV Cache 命中的 token 数
```

**成本估算**:基于模型价格表(config.yaml),支持多币种:

```yaml
models:
  adapters:
    glm:
      pricing:
        currency: "CNY"                  # 币种,支持 CNY/USD
        input_per_1k: 0.05               # 输入 token 单价(每千 token)
        output_per_1k: 0.05              # 输出 token 单价
        cached_input_per_1k: 0.01        # 仅输入缓存折扣价;输出 token 无缓存优惠
    deepseek:
      pricing:
        currency: "CNY"
        input_per_1k: 0.001
        output_per_1k: 0.002
        cached_input_per_1k: 0.0001
```

**缓存折扣说明**:`cached_input_per_1k` 仅作用于输入 token;输出 token 无缓存优惠(模型生成阶段无法利用 KV Cache 折扣)。

**价格版本快照**:每次修改 `config.yaml` 模型价格时,自动生成版本快照存入 `version_snapshots`(scope=`model_pricing`),评估回放时按会话创建时的价格版本核算历史成本,避免调价后历史成本失真。

**记录到 react_events**(三类成本独立标记):

```python
async def _record_usage(self, session_id, turn, model_id, usage,
                        cost_type: Literal["dialogue", "compress", "eval"] = "dialogue"):
    cost = self._calculate_cost(model_id, usage)
    await self.react_events_repo.insert(
        session_id=session_id,
        turn=turn,
        event_type="token_usage",
        payload={
            "model_id": model_id,
            "cost_type": cost_type,       # dialogue: 用户对话;compress: 压缩;eval: 评估回放
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cached_tokens": usage.cached_tokens,
            "currency": pricing.currency,
            "cost": cost,
        }
    )
```

**UI 展示**(分类汇总):

- **对话成本**(`cost_type="dialogue"`):用户交互产生的 token 与成本
- **压缩成本**(`cost_type="compress"`):上下文压缩调用产生的 token 与成本
- **评估成本**(`cost_type="eval"`):评估回放产生的 token 与成本(第 8 章)
- 每类按模型分项展示,合计总成本(按币种分组)
- cache 命中率(cached_tokens / input_tokens)

**不主动干预**:MVP 阶段仅展示,不设预算上限、不自动停止。用户根据展示自行决策。

`[MVP]` token 记录 + 成本估算 + UI 展示 + 三类成本分类统计全量实现。
`[V2]` 用户可设预算上限,超限自动压缩或停止;成本预警。

---

### 3.14 TokenEstimator 公共工具与压缩后消息时序

**TokenEstimator**(全章公共工具,解决 `_estimate_tokens` 实现缺失问题):

四家模型分词器不同,token 计数需统一封装:

```python
class TokenEstimator:
    """统一 token 估算,适配四家模型分词差异"""

    def __init__(self):
        self._tokenizers: dict[str, callable] = {}  # 按模型 ID 缓存分词器
        self._fallback_ratio = 3.0  # 无分词器时按 3 字符/token 估算

    def estimate(self, text: str, model_id: str | None = None) -> int:
        if model_id and model_id in self._tokenizers:
            return len(self._tokenizers[model_id].encode(text))
        # 兜底:粗略估算(中英文混合约 2-3 字符/token)
        return int(len(text) / self._fallback_ratio)

    def estimate_messages(self, messages: list[Message],
                          model_id: str | None = None) -> int:
        total = 0
        for m in messages:
            if m.compressed:  # 已压缩消息不计入
                continue
            total += self.estimate(m.content or "", model_id)
            if m.tool_calls:
                for tc in m.tool_calls:
                    total += self.estimate(json.dumps(tc.to_dict()), model_id)
        return total

    def register_tokenizer(self, model_id: str, tokenizer):
        """注册模型专用分词器(如 tiktoken for OpenAI 兼容模型)"""
        self._tokenizers[model_id] = tokenizer
```

**使用场景**:

- 3.9 压缩触发条件 (a):`token_estimator.estimate_messages(ctx, model_id) > capability.context_window * 0.8`
- 3.12 长度限制:`token_estimator.estimate(tool_result, model_id) > limit`
- 3.13 状态栏展示:`context_window_used` 字段

**分词器加载策略**:

- GLM / DeepSeek / KIMI / Agnes 基本兼容 OpenAI tiktoken,首版用 `cl100k_base` 兜底。
- 精确分词器按需注册(模型适配器初始化时),未注册则用字符比例估算。
- 分词器实例缓存在 `TokenEstimator._tokenizers`,避免重复加载。

**压缩后消息时序排序规则**(关键约束,防止上下文顺序错乱):

摘要压缩与 Stable Zone 合并产生的新消息,追加位置必须遵循以下规则:

| 消息类型 | 追加位置 | 理由 |
|---|---|---|
| Active Zone 摘要消息 | 被裁剪消息**之后**、新对话**之前** | 摘要概括旧上下文,新对话在摘要之后 |
| Stable Zone 合并消息 | 旧 Stable Zone **全部标记 compressed 后**追加为唯一 Stable | 保证 Stable Zone 整体性(2.8) |
| 状态栏消息 | 当前用户消息**之后**、模型 thinking **之前** | 3.6 注入时机规则 |

**Active Zone 摘要追加示例**:

```
原顺序: [user_1, asst_1, tool_1, user_2, asst_2, tool_2, user_3]
                       ^^^^^^^^^^^^^^^^^^^^^^^^^^^^
                       被裁剪(标记 compressed)

压缩后: [summary_1, user_3]
         ^^^^^^^^^^
         新追加,turn = max(compressed_msgs.turn) + 1
```

```python
async def _append_summary(self, ctx: list[Message], summary: Message,
                          compressed_msgs: list[Message]) -> list[Message]:
    summary.turn = max(m.turn or 0 for m in compressed_msgs) + 1
    summary.zone = "active"
    summary.compressed_from = [m.msg_id for m in compressed_msgs]
    # 插入位置:被裁剪消息之后、未压缩消息之前
    insert_idx = self._find_insert_position(ctx, compressed_msgs)
    ctx.insert(insert_idx, summary)
    return ctx
```

这是保证评估回放时上下文顺序可还原的关键约束。

---

### 3.15 与持久化层的交互

`context_manager` 通过 Repository 读写,与 2.10 持久化边界一致。

**读取路径**:

| 数据 | Repository | 用途 |
|---|---|---|
| 会话消息历史 | `messages_repo.get(session_id)` | 构建 ctx |
| 长期记忆 | `memories_repo.get(user_id)` | Stable Zone 初始化 |
| Skill 定义 | `versions_repo.get(scope="skill", name=...)` | Frozen Zone 构建 |
| Stable Zone 快照 | `versions_repo.get(scope="stable_zone", ...)` | 评估回放 |

**写入路径**:

| 数据 | Repository | 触发时机 |
|---|---|---|
| 新消息(用户/assistant/tool/状态栏) | `messages_repo.insert()` | 每轮 ReAct |
| 压缩标记 | `messages_repo.update(msg_id, compressed=True)` | 压缩执行后 |
| 压缩产生的新消息 | `messages_repo.insert()`(含 `compressed_from`) | 摘要/Stable 合并后 |
| Stable Zone 快照 | `versions_repo.save_snapshot(scope="stable_zone")` | 合并压缩时 |
| token 使用记录 | `react_events_repo.insert(event_type="token_usage")` | 每轮模型调用后 |
| 注入告警 | `react_events_repo.insert(event_type="injection_alert")` | 检测到注入时 |

**关键约定**:

- 压缩标记 `compressed=True` 是软删除(保留原文),不物理删除消息。这是评估回放的前提(第 8 章需要完整轨迹)。
- `compressed_from` 字段记录摘要消息的来源 message id 列表,支持评估时还原压缩前的上下文。
- Stable Zone 快照存 `version_snapshots`,与 Skills/Prompt 版本化管理统一(2.11)。

**与 Worker 进程的边界**(对应 2.2):context_manager 仅运行在 Python Sidecar 主进程,不 offload 到 Worker(无 CPU 密集任务)。压缩调用模型走 IO 轨(2.6)。

`[MVP]` 全部读写路径实现;软删除 + 快照存档。
`[V2]` 增量持久化(仅写变更消息,降低 DB 压力);压缩前的原文归档到 `messages_archive`。

---

### 3.16 MVP 与 V2 边界

本章设计按 MVP / V2 边界落地,与第 2 章 2.16 边界表对齐。

**MVP 必须实现**:

| 模块 | MVP 范围 |
|---|---|
| 上下文管理器 | 全部职责,无状态设计(3.1) |
| 分区元数据 | zone 字段 + compressed/compressed_from(3.2) |
| 构建流水线 | 启动构建 + 每轮构建(3.3) |
| hash 校验 | SHA-256 + canonical JSON + 会话维度合并标志(3.4) |
| 状态栏 | 时间戳/偏好/会话元信息(3.5) |
| 状态栏注入 | 用户消息轮注入(3.6) |
| 模板变量 | 五类命名空间 + `{{var}}` 解析 + `kb_replace_mode=false`(3.7) |
| 工具规范 | OpenAI schema + 扩展字段(3.8) |
| 压缩触发 | 三种条件 + 激进模式(3.9) |
| 三类压缩 | 滑动窗口(全局配对映射)+ 摘要 + Stable 合并(3.10) |
| 压缩模型 | 可配置 + 适配器复用 + 计费隔离(3.11) |
| 注入防护 | 三层防护 + 中英文 + 高低风险分级 + 沙箱差异化(3.12) |
| 计费感知 | token 记录 + 多币种 + 价格快照 + 三类成本分类(3.13) |
| TokenEstimator | 公共工具 + 分词器注册 + 消息时序规则(3.14) |
| 持久层交互 | 全部读写路径 + 软删除 + 快照(3.15) |

**V2 预留接口**:

| 模块 | V2 范围 | 预留接口 |
|---|---|---|
| 增量构建 | 仅重建变化部分 | `ContextManager.build()` 支持 `incremental=True` 参数 |
| 跨会话 cache 复用 | 同 Skill 会话共享 Frozen Zone hash | hash 计算逻辑已隔离,可扩展为跨会话 key |
| 重要性裁剪 | 基于模型/规则判断消息重要性 | `_apply_compression` 支持新策略枚举 |
| 结构化提取 | 从对话提取决策/事实/待办 | `compressed_from` 字段已支持溯源 |
| 按需状态栏 | 模型主动调用 `get_status` | 状态栏内容 schema 已定义,可作为工具暴露 |
| `kb_replace_mode=true` | 运行时替换 KB 占位符 | 配置开关已定义,替换逻辑在 `TemplateResolver` 扩展 |
| 输出层注入校验 | 检测模型输出被操纵 | `react_events` 已记录注入告警,可扩展输出检测 |
| 预算上限 | 超限自动压缩/停止 | token_usage 事件已分类记录,可扩展阈值检查 |
| 本地压缩模型 | 本地小模型做压缩 | 适配器层统一,本地模型作为新适配器接入 |
| 精确分词器 | 每家模型专用分词器 | `TokenEstimator.register_tokenizer` 接口已定义 |

**与三大约束的对应**:

- 上下文质量优先 → 3.2 分区模型;3.3 构建流水线;3.10 三类压缩;3.12 注入防护(含沙箱差异化)。
- 缓存友好 → 3.4 hash 校验(会话维度标志);3.6 状态栏注入时机;3.7 模板变量解析时机(Frozen vs Stable)+ `kb_replace_mode` 双模式。
- 评估驱动迭代 → 3.10 压缩存档(soft delete + snapshot + hash 备份);3.13 token 三类成本分类 + 价格快照;3.14 消息时序规则;3.15 持久化路径完整。

---

第 3 章起草完成。本章展开了 `context_manager` 的完整实现:分区元数据模型、构建流水线、hash 校验(会话维度标志)、状态栏机制、模板变量体系(含 KB 双模式)、工具描述规范、压缩触发矩阵、三类压缩策略(含全局配对映射)、压缩模型选型、注入防护(中英文+高低风险+沙箱差异化)、计费感知(多币种+价格快照+三类成本分类)、TokenEstimator 公共工具与消息时序规则、持久层交互,共 16 节,所有决策严格复用前序锁定结论。

后续章节衔接:

- 第 4 章:展开 Stable Zone 的知识库检索与 `{{kb.context}}` 注入的完整 RAG 实现。
- 第 5 章:展开工具描述规范(3.8)的 MCP 工具集与调度细节。
- 第 8 章:基于 3.13 token 记录与 3.14 压缩存档构建评估闭环。

---

## 第 4 章 记忆与知识库层

本章为 Stable Zone(2.8)提供两大数据来源:**用户长期记忆**(跨会话)与**知识库检索**(单会话内 Agentic RAG)。第 3 章定义了 Stable Zone 的注入时机与合并压缩规则,本章展开数据生产侧:记忆提取/淘汰/注入、文档处理/chunking/embedding、混合检索 + reranker、Agentic RAG 工具、增量更新与版本快照。

核心原则:本章只生产数据,不决定注入时机(注入由 `context_manager` 在构建 Stable Zone 时调用);所有表结构复用 2.10 已定义的 Postgres schema,不新增无关数据表。

每节末尾以 `[MVP]` 或 `[V2]` 标注实现边界。

---

### 4.1 记忆与知识库层的职责定位

**在架构中的位置**:

```
┌─────────────────────────────────────────────────────────┐
│  ReAct 主循环(2.4)                                      │
│    ↓ 调用 context_manager.build()                        │
│  Context Manager(第 3 章)                               │
│    ↓ 构建 Stable Zone 时调用本章接口                     │
│  ┌──────────────────┐  ┌──────────────────────────┐     │
│  │ 用户记忆管理(4.2-4.5)│ │ 知识库 RAG(4.6-4.16)    │     │
│  │ → user_memories   │ │ → kb_chunks + 检索        │     │
│  └──────────────────┘  └──────────────────────────┘     │
│    ↓ 写入                  ↓ Agent 调用 search_knowledge │
│  Postgres(2.10)         Worker 进程(2.2,纯计算)         │
└─────────────────────────────────────────────────────────┘
```

**两大职责**:

| 职责 | 数据流 | 触发时机 | 数据去向 |
|---|---|---|---|
| 用户长期记忆 | 会话 → LLM 提取 → `user_memories` | 会话结束/每 8 轮 | Stable Zone 初始内容 |
| 知识库检索 | Agent 调用工具 → 混合检索 → reranker | Agent 自主决定 | Stable Zone 追加(3.7) |

**与 context_manager 的边界**:

- 本章提供 `load_user_memories(user_id)` 与 `search_knowledge(query, ...)` 接口。
- `context_manager` 在会话启动时调用前者(3.3 步骤 3),在 Agent 调用工具时由 `tool_dispatcher` 调用后者。
- 本章不感知 Stable Zone 的存在,只返回结构化数据;注入与压缩由第 3 章处理。

`[MVP]` 两大职责接口实现;与 context_manager 边界清晰。
`[V2]` 多用户记忆隔离(当前单人场景默认单用户);记忆跨设备同步。

---

### 4.2 用户记忆策略:LLM 摘要提取

决策 1(b):会话结束或每 8 轮自动触发摘要提取,用 LLM 从对话中抽取关键信息写入 `user_memories`。

**触发时机**:

```python
class MemoryManager:
    EXTRACT_INTERVAL_TURNS = 8  # 每 8 轮触发一次

    async def maybe_extract(self, session: Session) -> None:
        # 条件 1:每 8 轮触发
        if session.current_turn > 0 and session.current_turn % self.EXTRACT_INTERVAL_TURNS == 0:
            await self._extract_memories(session)
        # 条件 2:会话结束触发(由 API 层调用 session_end 钩子)
    
    async def on_session_end(self, session: Session) -> None:
        await self._extract_memories(session)
```

**提取 prompt 模板**:

```
请从以下对话中提取用户的关键信息,分类为:
1. preference: 用户偏好(语言、风格、工作习惯等)
2. fact: 事实信息(用户身份、项目背景、技术栈等)
3. todo: 待办事项(用户提到需要完成的任务)
4. decision: 已做出的决策(用户明确选择的方向)

仅提取明确出现的信息,不要推测。每条记忆格式:
[type] content

对话历史:
{session_messages}

输出:每行一条,空行分隔不同类型。
```

**提取执行**:复用第 3 章压缩模型配置(3.11),`compress_model` 兼任记忆提取,计费记入 `cost_type="compress"`。

```python
async def _extract_memories(self, session: Session) -> list[Memory]:
    messages = await self.messages_repo.get_session_messages(session.id)
    prompt = self._build_extract_prompt(messages)
    result = await self.compress_adapter.stream(
        ctx=[Message(role="user", content=prompt)], tools=[]
    )
    memories = self._parse_extracted(result.content, session.id)
    await self.memories_repo.batch_insert(memories)
    # 写入 react_events 供评估
    await self.react_events_repo.insert(
        session_id=session.id, turn=session.current_turn,
        event_type="memory_extracted",
        payload={"count": len(memories), "types": [m.type for m in memories]}
    )
    return memories
```

**解析规则**:按行解析 `[type] content` 格式,未匹配格式的行丢弃;type 不在枚举内的丢弃并日志告警。

**UI 手动触发入口**(缺口补充):除自动触发外,UI 提供"立即提取记忆"按钮,用户可在评估调试或希望立即沉淀对话关键信息时主动触发,不受 8 轮轮次限制。

```python
class MemoryManager:
    async def manual_extract(self, session_id: str) -> list[Memory]:
        """UI 手动触发记忆提取,立即执行,不受 8 轮轮次限制"""
        session = await self.session_repo.get(session_id)
        return await self._extract_memories(session)
```

**HTTP 接口**:归入 2.3 已有会话接口分组,无需新增路由大类:

```
POST /api/sessions/{session_id}/extract_memory
Response: {"count": int, "types": [str, ...]}
```

调用后立即返回本次提取的记忆条数与类型分布,前端可在 UI Toast 提示"已提取 N 条记忆"。

`[MVP]` 每 8 轮 + 会话结束触发提取 + UI 手动触发双模式;复用压缩模型;解析规则实现。
`[V2]` Agentic Memory(Agent 主动调用 `remember`/`recall` 工具);基于重要性的自适应提取频率。

---

### 4.3 用户记忆的存储结构

决策 2(B):结构化条目,复用 2.10 `user_memories` 表。

**表 schema**(扩展 2.10 定义):

```sql
-- 复用 2.10 已定义的 user_memories 表,补充字段说明
CREATE TABLE user_memories (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL,                    -- 单人场景固定为 1
    type VARCHAR(20) NOT NULL,                  -- preference/fact/todo/decision
    content TEXT NOT NULL,                      -- 记忆内容
    importance FLOAT DEFAULT 0.5,               -- 0.0-1.0,用于淘汰
    source_session_id BIGINT,                   -- 来源会话(评估溯源)
    created_at TIMESTAMPTZ DEFAULT NOW(),
    last_accessed_at TIMESTAMPTZ DEFAULT NOW(), -- 最近被注入会话的时间
    access_count INT DEFAULT 0,                 -- 被注入次数
    is_active BOOLEAN DEFAULT TRUE              -- 软删除标记
);

CREATE INDEX idx_memories_user_type ON user_memories(user_id, type) 
    WHERE is_active = TRUE;
CREATE INDEX idx_memories_importance ON user_memories(user_id, importance DESC)
    WHERE is_active = TRUE;
```

**记忆类型枚举**:

| type | 含义 | 示例 |
|---|---|---|
| `preference` | 用户偏好 | "偏好浅色主题" / "使用 PostgreSQL" |
| `fact` | 事实信息 | "项目使用 Electron + Python" |
| `todo` | 待办事项 | "完成第 4 章起草" |
| `decision` | 已做决策 | "MVP 不做模型后训练" |

**importance 初始值规则**:

- `decision` 默认 0.9(决策类高价值)
- `fact` 默认 0.7
- `preference` 默认 0.6
- `todo` 默认 0.5(完成后应降级或标记 inactive)

`[MVP]` 表结构 + 类型枚举 + importance 初始规则全量实现。
`[V2]` importance 由 LLM 在提取时动态评分;todo 完成后自动标记 inactive。

---

### 4.4 记忆淘汰与合并策略

长期积累的记忆需要管理,避免 `user_memories` 无限膨胀影响注入质量与查询性能。

**淘汰阈值配置化**(缺口补充):单人桌面用户的知识库/对话量差异较大,固定阈值不灵活,改为 `config.yaml` 静态默认 + `config_runtime` 运行时可改,UI 配置面板支持调整,无需重启服务。

```yaml
# config.yaml
memory:
  eviction:
    max_active_count: 200              # 活跃记忆上限
    min_importance_threshold: 0.3      # 低于此值且超期则淘汰
    expire_days: 30                    # 超期天数(last_accessed_at)
```

```python
class MemoryManager:
    def __init__(self, config_loader: ConfigLoader):
        # 启动时从 config_runtime 加载,缺失则回退 config.yaml 默认值
        cfg = config_loader.get("memory.eviction", source="runtime_first")
        self.eviction_rules = {
            "max_active_count": cfg["max_active_count"],
            "min_importance": cfg["min_importance_threshold"],
            "expire_days": cfg["expire_days"],
        }

    async def evict_memories(self, user_id: int) -> int:
        # 条件 1:超过上限,按 importance 升序淘汰最低的
        active = await self.memories_repo.count_active(user_id)
        if active > self.eviction_rules["max_active_count"]:
            excess = active - self.eviction_rules["max_active_count"]
            await self.memories_repo.deactivate_lowest(user_id, excess)
        
        # 条件 2:低重要性 + 长期未访问,标记 inactive
        cutoff = datetime.now() - timedelta(days=self.eviction_rules["expire_days"])
        evicted = await self.memories_repo.deactivate_expired(
            user_id,
            min_importance=self.eviction_rules["min_importance"],
            cutoff=cutoff
        )
        return evicted
```

**淘汰触发时机**:每次记忆提取后(`maybe_extract` 末尾调用 `evict_memories`),避免独立定时任务。

**软删除约定**:`is_active = FALSE` 表示淘汰,不物理删除(评估回放需要历史记忆)。淘汰记录写入 `react_events`(`event_type="memory_evicted"`)。

**合并策略**(V2 预留):

MVP 不做智能合并;V2 引入:相同 type + 语义相似度 > 0.9 的记忆,用 LLM 合并为单条,保留更高 importance。

`[MVP]` 数量上限 + 低重要性超期淘汰;软删除;淘汰事件记录。
`[V2]` 基于语义相似度的智能合并;importance 动态衰减(时间 + 访问频率)。

---

### 4.5 记忆注入到 Stable Zone 的机制

会话启动时,`context_manager` 调用 `load_user_memories` 读取高 importance 记忆,作为 Stable Zone 初始内容。

**注入接口**:

```python
class MemoryManager:
    INJECT_LIMIT = 10  # 默认注入 top 10

    async def load_user_memories(self, user_id: int, 
                                  limit: int = INJECT_LIMIT) -> list[Memory]:
        """会话启动时调用,返回高重要性记忆"""
        memories = await self.memories_repo.get_top_active(
            user_id, order_by="importance DESC, last_accessed_at DESC",
            limit=limit
        )
        # 更新访问记录(评估溯源)
        await self.memories_repo.batch_update_access(memories)
        return memories
```

**与 3.3 构建流水线的衔接**:

```
会话启动构建(3.3 步骤 3):
  → context_manager 调用 memory_manager.load_user_memories(user_id)
  → 返回 top 10 记忆
  → 格式化为 Stable Zone 初始消息:
    Message(role="user", content="[User Memories]\n{memories_text}",
            zone="stable")
  → 计入 base_stable_hash(3.4)
```

**格式化示例**:

```python
def _format_memories_for_stable(self, memories: list[Memory]) -> str:
    lines = ["[User Memories]"]
    for m in memories:
        lines.append(f"[{m.type}] {m.content}")
    return "\n".join(lines)
```

**注入数量限制**:默认 top 10,避免 Stable Zone 过大影响 Active Zone 预算。可在 `config_runtime.memory_inject_limit` 调整。

**与 Stable Zone 合并压缩的关系**:记忆注入的 Stable Zone 初始内容,在 3.10.3 合并压缩时会被纳入合并范围(与 KB 检索片段一起摘要)。这是预期行为——长期会话中,早期记忆可能已被新记忆覆盖,合并压缩避免冗余。

`[MVP]` top 10 注入 + 访问记录更新 + 格式化实现。
`[V2]` 按会话场景选择性注入(办公场景只注入办公相关记忆);记忆重要性按会话主题动态排序。

---

### 4.6 知识库文档处理流水线总览

端到端流程:文档上传 → 类型识别 → chunking → embedding → 写入 `kb_chunks`。

**流水线架构**:

```
用户上传文档(UI)
  ↓
Python Sidecar 主进程:文档接收 + 类型识别
  ↓ 序列化文本发送给 Worker
Worker 进程(2.2,纯计算):
  → chunking(按文档类型分块)
  → embedding(bge-m3 批量推理)
  ↓ 返回 chunks + vectors
Python Sidecar:写入 kb_chunks(2.10)
  ↓
生成知识库快照(4.16)
```

**与 Worker 进程的交互**(遵循 2.2):

- Worker 是纯计算节点,不访问 DB。
- Python Sidecar 将"待处理文本"序列化发送给 Worker。
- Worker 返回 chunks + vectors,Python Sidecar 负责持久化。
- 批量处理:单文档多 chunk 一次 Worker 调用,减少进程间通信开销。

**错误处理**:Worker 不可用时,降级到云端 embedding(4.10);chunking 失败时记录错误文档,不阻塞其他文档处理。

`[MVP]` 端到端流水线实现;Worker 纯计算;云端降级。
`[V2]` 流水线并行化(多文档并发处理);增量索引重建。

---

### 4.7 文档类型识别与分发

决策 4:Markdown/PDF/Code 三类,按类型分发不同 chunking 参数。

**类型识别规则**:

```python
class DocumentProcessor:
    TYPE_RULES = [
        # (扩展名, 内容嗅探正则, 文档类型)
        ([".md", ".markdown"], None, "markdown"),
        ([".pdf"], None, "pdf"),
        ([".py", ".js", ".ts", ".java", ".go", ".rs", ".cpp", ".c", ".h"],
         None, "code"),
        # 兜底:内容嗅探
        ([], r"^#{1,6}\s+", "markdown"),  # 开头是 Markdown 标题
        ([], None, "plain"),               # 默认纯文本
    ]

    def detect_type(self, filename: str, content: str) -> str:
        ext = Path(filename).suffix.lower()
        for extensions, pattern, doc_type in self.TYPE_RULES:
            if ext in extensions:
                return doc_type
            if pattern and re.search(pattern, content[:200], re.MULTILINE):
                return doc_type
        return "plain"  # 兜底纯文本,使用默认参数
```

**分发表**:

| 文档类型 | chunking 策略 | chunk_size | overlap | 特殊处理 |
|---|---|---|---|---|
| `markdown` | 按标题层级 + 段落 | 800 | 100 | 保留标题层级 metadata |
| `pdf` | 按段落 + 页码 | 800 | 100 | 保留页码 metadata |
| `code` | 按函数/类边界 | 500 | 50 | 保留符号名 metadata |
| `plain` | 固定长度兜底 | 800 | 100 | 无特殊处理 |

**类型识别失败兜底**:无法识别时归为 `plain`,使用默认参数,日志告警供用户确认。

`[MVP]` 三类 + 兜底识别;分发表实现。
`[V2]` 内容嗅探增强(基于模型分类);用户手动指定类型(UI 覆盖)。

---

### 4.8 chunking 策略实现

决策 3(a+b):段落语义优先 + 固定长度兜底。

**Markdown chunking**(按标题层级):

```python
def chunk_markdown(content: str, chunk_size: int = 800, 
                   overlap: int = 100) -> list[Chunk]:
    # 按 ## / ### 标题切割
    sections = re.split(r'(?=^#{1,6}\s+)', content, flags=re.MULTILINE)
    chunks = []
    for section in sections:
        if not section.strip():
            continue
        # 提取标题作为 metadata
        title_match = re.match(r'^(#{1,6})\s+(.+)', section)
        title = title_match.group(2) if title_match else ""
        # 单节超过 chunk_size,进一步按段落切割
        if len(section) > chunk_size * 3:  # 粗略字符估算
            sub_chunks = _split_by_paragraph(section, chunk_size, overlap)
            for sc in sub_chunks:
                chunks.append(Chunk(text=sc, metadata={"title": title, "type": "markdown"}))
        else:
            chunks.append(Chunk(text=section, metadata={"title": title, "type": "markdown"}))
    return chunks
```

**PDF chunking**(按段落 + 页码):

```python
def chunk_pdf(pages: list[PageContent], chunk_size: int = 800,
              overlap: int = 100) -> list[Chunk]:
    chunks = []
    for page in pages:
        paragraphs = re.split(r'\n\s*\n', page.text)
        buffer = ""
        for para in paragraphs:
            if len(buffer) + len(para) > chunk_size * 3 and buffer:
                chunks.append(Chunk(
                    text=buffer, metadata={"page": page.number, "type": "pdf"}
                ))
                buffer = para
            else:
                buffer = buffer + "\n\n" + para if buffer else para
        if buffer:
            chunks.append(Chunk(
                text=buffer, metadata={"page": page.number, "type": "pdf"}
            ))
    return chunks
```

**Code chunking**(按函数/类边界):

```python
def chunk_code(content: str, filename: str, chunk_size: int = 500,
               overlap: int = 50) -> list[Chunk]:
    # 按函数/类定义切割(简化:匹配 def/class/function/func 等关键字)
    pattern = r'(?=^(?:async\s+)?(?:def|class|function|func|public|private|protected)\s+)'
    blocks = re.split(pattern, content, flags=re.MULTILINE)
    chunks = []
    for block in blocks:
        if not block.strip():
            continue
        # 超长函数进一步按行切割
        if len(block) > chunk_size * 3:
            sub_blocks = _split_by_lines(block, chunk_size, overlap)
            for sb in sub_blocks:
                chunks.append(Chunk(
                    text=sb, metadata={"file": filename, "type": "code"}
                ))
        else:
            chunks.append(Chunk(
                text=block, metadata={"file": filename, "type": "code"}
            ))
    return chunks
```

**固定长度兜底**:

```python
def _split_by_paragraph(text: str, chunk_size: int, overlap: int) -> list[str]:
    """按段落切割,超过 chunk_size 时在段落边界截断,保留 overlap"""
    paragraphs = text.split("\n\n")
    chunks = []
    buffer = ""
    for para in paragraphs:
        if len(buffer) + len(para) > chunk_size * 3 and buffer:
            chunks.append(buffer)
            # overlap:保留末尾部分
            buffer = buffer[-overlap * 3:] + "\n\n" + para if overlap else para
        else:
            buffer = buffer + "\n\n" + para if buffer else para
    if buffer:
        chunks.append(buffer)
    return chunks
```

**overlap 实现**:按字符数估算(token 估算在 embedding 阶段做),保留上一块末尾 `overlap * 3` 字符作为下一块开头,保证跨块语义连续。

`[MVP]` 三类 chunking + 固定长度兜底全量实现。
`[V2]` 递归分块(先按大结构,再细分);模型分块(LLM 判断语义边界)。

---

### 4.9 chunk 参数配置

决策 4:config 区分 markdown/pdf/code 三类模板,运行时可覆盖。

**config.yaml 结构**:

```yaml
kb:
  chunking:
    templates:
      markdown:
        chunk_size: 800
        overlap: 100
        strategy: "heading"  # 按标题层级
      pdf:
        chunk_size: 800
        overlap: 100
        strategy: "paragraph"  # 按段落
      code:
        chunk_size: 500
        overlap: 50
        strategy: "symbol"  # 按函数/类符号
      plain:
        chunk_size: 800
        overlap: 100
        strategy: "fixed"  # 固定长度兜底
```

**运行时覆盖**(`config_runtime` 表):

```json
{
  "kb.chunking.templates.code.chunk_size": 600
}
```

加载优先级:`config_runtime` > `config.yaml` > 代码默认值(2.14 配置分层)。

**参数调优建议**:

- chunk_size 过大:单块信息密度高但检索精度下降(向量稀释关键信息)。
- chunk_size 过小:上下文不完整,需多次检索拼接。
- overlap 过小:跨块语义断裂;overlap 过大:冗余存储。
- 代码文档下调至 500/50:函数通常较短,小 chunk 提高符号级检索精度。

`[MVP]` 三类模板 + 运行时覆盖 + 加载优先级实现。
`[V2]` 基于检索反馈的自适应调参(评估命中率反向调整 chunk_size)。

---

### 4.10 Embedding 模型与 Worker 集成

决策 5(B):本地 bge-m3,Worker 进程 CPU 计算;云端 embedding 离线兜底降级。

**bge-m3 模型加载**:

```python
# Worker 进程入口(最小化依赖,2.2 跨平台备注)
class EmbeddingWorker:
    MODEL_NAME = "BAAI/bge-m3"
    
    def __init__(self):
        from FlagEmbedding import BGEM3FlagModel
        self.model = BGEM3FlagModel(
            self.MODEL_NAME,
            use_fp16=True  # CPU 下用 fp16 加速
        )
    
    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """批量 embedding,返回向量列表"""
        embeddings = self.model.encode(
            texts, batch_size=32, max_length=8192
        )["dense_vecs"]
        return embeddings.tolist()
```

**Worker 预热**:Worker 进程启动时立即加载 bge-m3 模型(约 2GB 内存),避免首次检索延迟。模型实例缓存在 Worker 进程,后续调用零加载开销。

**Python Sidecar 调用 Worker**:

```python
class EmbeddingService:
    def __init__(self, worker_pool: ProcessPoolExecutor):
        self.worker_pool = worker_pool
    
    async def embed_chunks(self, chunks: list[Chunk]) -> list[list[float]]:
        texts = [c.text for c in chunks]
        # offload 到 Worker 进程(2.2 + 2.6)
        loop = asyncio.get_event_loop()
        vectors = await loop.run_in_executor(
            self.worker_pool, embed_worker_fn, texts
        )
        return vectors
```

**云端 embedding 降级**(Worker 不可用时):

```python
class EmbeddingService:
    async def embed_with_fallback(self, chunks: list[Chunk]) -> list[list[float]]:
        try:
            return await self.embed_chunks(chunks)
        except (WorkerCrashError, WorkerTimeoutError) as e:
            logger.warning(f"Worker unavailable, falling back to cloud embedding: {e}")
            return await self._cloud_embed(chunks)
    
    async def _cloud_embed(self, chunks: list[Chunk]) -> list[list[float]]:
        # 使用主模型同厂商的 embedding API(如 GLM embedding)
        # 注意:云端 embedding 维度需与 bge-m3 一致(1024),否则需重新索引
        adapter = self.model_registry.get_embedding_adapter(self.config.fallback_embedding_model)
        return await adapter.embed([c.text for c in chunks])
```

**降级约束**:云端 embedding 维度必须与 bge-m3(1024 维)一致,否则向量索引失效。若维度不一致,需触发全量重建索引(仅 Worker 长期不可用时才走此路径)。

**轻量模型降级方案**(缺口补充):bge-m3 加载约 2GB,低配置笔记本(8GB 内存)Worker + Sidecar 易 OOM。新增轻量模型备选,运行时按内存自动切换:

```yaml
# config.yaml
kb:
  embedding:
    local_default: "BAAI/bge-m3"              # 标准:1024 维,约 2GB,16GB+ 内存推荐
    local_light: "BAAI/bge-small-zh-v1.5"     # 轻量:384 维,约 300MB,8GB 低配适配
    fallback_cloud: "glm-embedding"
```

```python
class EmbeddingWorker:
    @staticmethod
    def select_model_by_memory() -> str:
        """启动时检测可用内存,自动选择标准/轻量模型"""
        import psutil
        avail_gb = psutil.virtual_memory().available / (1024 ** 3)
        if avail_gb < 6.0:
            logger.warning(f"Available memory {avail_gb:.1f}GB < 6GB, using light model")
            return "BAAI/bge-small-zh-v1.5"
        return "BAAI/bge-m3"
```

**维度兼容约束**:bge-small 为 384 维,与 bge-m3(1024 维)不兼容;切换模型时自动触发对应 HNSW 索引重建(`kb_chunks.embedding` 全量重算),避免检索报错。UI 配置面板支持用户手动切换"标准/轻量",切换前需提示重建索引耗时。

**query 向量 LRU 缓存**(缺口补充):高频相同 query 重复计算 embedding 浪费 CPU,在 Worker 内存中维护本地 LRU 缓存:

```python
from functools import lru_cache

class EmbeddingWorker:
    @lru_cache(maxsize=512)
    def embed_query_cached(self, query: str) -> tuple[float, ...]:
        """query 向量 LRU 缓存,key=query 文本,有效期 10 分钟。
        maxsize=512,单条 query 向量约 4KB,总缓存 < 2MB,内存开销可忽略。
        lru_cache 基于参数哈希,相同 query 命中缓存零推理开销。
        """
        vec = self.model.encode([query], batch_size=1)["dense_vecs"][0]
        return tuple(vec.tolist())  # 转 tuple 才能哈希
```

> 缓存命中策略:仅缓存 `search_knowledge` 工具触发的单条 query(批量 chunk embedding 不缓存,因 chunk 文本唯一性高);TTL 通过定期 `cache_clear()` 实现(每 10 分钟由 Sidecar 触发一次清理)。

**异常入库告警**(缺口补充):Worker 崩溃、模型加载失败、reranker 不可用等异常不仅降级,还推送 `tool_error` 事件至 WS 并入 `react_events`,供评估分析 Agent 在该会话的检索质量:

```python
async def embed_with_fallback(self, chunks: list[Chunk], session_id: str, turn: int):
    try:
        return await self.embed_chunks(chunks)
    except (WorkerCrashError, WorkerTimeoutError) as e:
        logger.warning(f"Worker unavailable, falling back to cloud embedding: {e}")
        # 异常入库,供评估分析检索质量退化原因
        await self.react_events_repo.insert(
            session_id=session_id, turn=turn,
            event_type="tool_error",
            payload={"tool": "embedding", "error": str(e), "fallback": "cloud"}
        )
        return await self._cloud_embed(chunks)
```

**计费**:本地 embedding 零 API 成本,仅记 Worker CPU 时间;云端降级时按 embedding API 价格计费,记入 `cost_type="compress"`(归类为非对话成本)。

`[MVP]` bge-m3 本地加载 + Worker 集成 + 云端降级 + 轻量模型内存自动切换 + query LRU 缓存 + 异常入库告警全量实现。
`[V2]` GPU 加速(若有 GPU);多 embedding 模型路由(按文档语言选择);向量维度自适应(降级时重建索引)。

---

### 4.11 pgvector HNSW 索引配置

决策 6:m=16, ef_construction=128, ef_search=64;ef_search 运行时可调。

**索引创建 SQL**:

```sql
-- 假设 kb_chunks 表已定义(2.10),embedding 字段为 vector(1024)
-- bge-m3 输出 1024 维向量

CREATE INDEX idx_kb_chunks_embedding_hnsw ON kb_chunks 
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 128);
```

**参数说明**:

| 参数 | 值 | 作用 | 调整时机 |
|---|---|---|---|
| `m` | 16 | 每层最大连接数,增大提升召回但内存增加 | 重建索引 |
| `ef_construction` | 128 | 构建时搜索宽度,增大提升质量但构建慢 | 重建索引 |
| `ef_search` | 64 | 查询时搜索宽度,增大提升召回但查询慢 | 运行时(检索接口传参) |

**ef_search 运行时调参**:

```python
class KnowledgeBaseRepo:
    async def vector_search(
        self, query_vector: list[float], limit: int = 20,
        ef_search: int = 64,  # 运行时覆盖默认
        filters: dict = None
    ) -> list[Chunk]:
        async with self.db.transaction():
            # 设置本会话的 ef_search
            await self.db.execute(f"SET LOCAL hnsw.ef_search = {ef_search}")
            # 向量检索 + metadata 过滤
            sql = """
                SELECT id, doc_id, scenario, source, chunk_text, 
                       1 - (embedding <=> $1) AS similarity
                FROM kb_chunks
                WHERE 1=1
            """
            params = [str(query_vector)]
            if filters:
                if "scenario" in filters:
                    sql += " AND scenario = $" + str(len(params) + 1)
                    params.append(filters["scenario"])
                if "source" in filters:
                    sql += " AND source = $" + str(len(params) + 1)
                    params.append(filters["source"])
            sql += f" ORDER BY embedding <=> $1 LIMIT {limit}"
            return await self.db.fetch(sql, *params)
```

**内存占用估算**:bge-m3 1024 维 × float32(4 字节)= 4KB/向量;10 万 chunk 约 400MB 向量数据 + HNSW 图结构(约 1.5 倍)≈ 1GB。桌面端需监控内存,超 1.5GB 触发知识库清理提示。

**索引重建场景**:

- 调整 `m` 或 `ef_construction`:需 `DROP INDEX` + `CREATE INDEX`,耗时较长(10 万 chunk 约 5-10 分钟)。
- 批量新增文档:pgvector HNSW 支持增量插入,无需重建;但大量插入后建议 `REINDEX` 优化图结构。

`[MVP]` HNSW 索引创建 + ef_search 运行时调参全量实现。
`[V2]` 索引重建调度(UI 触发 + 进度展示);基于召回率的自动调参。

---

### 4.12 kb_chunks 表 schema 与 metadata 设计

决策 9(C):统一向量表 + metadata 过滤,复用 2.10 `kb_chunks` 表。

**表 schema**(扩展 2.10 定义):

```sql
CREATE TABLE kb_chunks (
    id BIGSERIAL PRIMARY KEY,
    doc_id BIGINT NOT NULL,                  -- 所属文档 ID
    scenario VARCHAR(50),                    -- 场景:office/data_analysis/frontend_design
    source VARCHAR(200),                     -- 来源:文件名/URL/手动输入
    chunk_text TEXT NOT NULL,                -- 原始文本
    embedding vector(1024) NOT NULL,         -- bge-m3 向量
    metadata JSONB DEFAULT '{}',             -- 扩展 metadata(标题/页码/符号名等)
    created_at TIMESTAMPTZ DEFAULT NOW(),
    is_active BOOLEAN DEFAULT TRUE           -- 软删除标记
);

-- HNSW 向量索引(4.11)
CREATE INDEX idx_kb_chunks_embedding_hnsw ON kb_chunks
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 128)
    WHERE is_active = TRUE;

-- metadata 过滤索引
CREATE INDEX idx_kb_chunks_scenario ON kb_chunks(scenario) WHERE is_active = TRUE;
CREATE INDEX idx_kb_chunks_doc_id ON kb_chunks(doc_id) WHERE is_active = TRUE;
CREATE INDEX idx_kb_chunks_source ON kb_chunks(source) WHERE is_active = TRUE;

-- 全文检索索引(4.13 BM25 混合检索)
CREATE INDEX idx_kb_chunks_text_tsv ON kb_chunks 
    USING gin (to_tsvector('simple', chunk_text)) WHERE is_active = TRUE;
```

**metadata 字段用途**:

| 来源 | metadata 示例 | 用途 |
|---|---|---|
| Markdown | `{"title": "架构设计", "level": 2}` | 检索结果展示标题 |
| PDF | `{"page": 5}` | 检索结果展示页码 |
| Code | `{"file": "context_manager.py", "symbol": "build"}` | 检索结果展示文件+符号 |

**软删除约定**:`is_active = FALSE` 表示文档已删除,不物理删除(评估回放需要历史 chunk)。删除文档时批量更新 `is_active`,向量索引自动排除(WHERE 条件)。

`[MVP]` 表结构 + 四类索引 + metadata 设计全量实现。
`[V2]` metadata 索引优化(GIN 索引支持复杂查询);多语言全文检索(分语言 tsvector)。

---

### 4.13 混合检索策略

决策 7(c):BM25 全文检索 + 向量相似度融合打分。

**混合检索流程**:

```
用户查询(query)
  ├── 向量检索:query → bge-m3 embedding → pgvector cosine 相似度 top-20
  └── 关键词检索:query → tsvector → BM25 打分 top-20
       ↓
  融合打分(RRF / 加权融合)
       ↓
  top-20 候选送入 reranker(4.14)
```

**融合策略:RRF(Reciprocal Rank Fusion)**:

```python
def rrf_fusion(vector_results: list[Chunk], keyword_results: list[Chunk],
               k: int = 60, limit: int = 20) -> list[Chunk]:
    """RRF 融合:rank-based,无需分数归一化"""
    scores: dict[int, float] = {}  # chunk_id → score
    chunks: dict[int, Chunk] = {}
    
    for rank, chunk in enumerate(vector_results):
        scores[chunk.id] = scores.get(chunk.id, 0) + 1.0 / (k + rank + 1)
        chunks[chunk.id] = chunk
    for rank, chunk in enumerate(keyword_results):
        scores[chunk.id] = scores.get(chunk.id, 0) + 1.0 / (k + rank + 1)
        chunks[chunk.id] = chunk
    
    # 按 RRF 分数降序,取 top-20
    sorted_ids = sorted(scores.keys(), key=lambda cid: scores[cid], reverse=True)
    return [chunks[cid] for cid in sorted_ids[:limit]]
```

**为什么选 RRF**:向量相似度(cosine 0-1)与 BM25 分数(无上界)尺度不同,加权融合需归一化;RRF 基于排名融合,对分数尺度不敏感,实现简单且效果稳定。

**检索接口**:

```python
class KnowledgeBaseRepo:
    async def hybrid_search(
        self, query: str, query_vector: list[float],
        scenario: str = None, source: str = None,
        limit: int = 20, ef_search: int = 64
    ) -> list[Chunk]:
        filters = {"scenario": scenario, "source": source}
        # 并行执行向量检索与关键词检索
        vector_results, keyword_results = await asyncio.gather(
            self.vector_search(query_vector, limit=limit, 
                             ef_search=ef_search, filters=filters),
            self.keyword_search(query, limit=limit, filters=filters)
        )
        # RRF 融合
        return rrf_fusion(vector_results, keyword_results, limit=limit)
    
    async def keyword_search(self, query: str, limit: int = 20,
                             filters: dict = None) -> list[Chunk]:
        sql = """
            SELECT id, doc_id, scenario, source, chunk_text,
                   ts_rank(to_tsvector('simple', chunk_text), 
                           plainto_tsquery('simple', $1)) AS rank
            FROM kb_chunks
            WHERE to_tsvector('simple', chunk_text) @@ plainto_tsquery('simple', $1)
        """
        params = [query]
        if filters and filters.get("scenario"):
            sql += f" AND scenario = ${len(params)+1}"
            params.append(filters["scenario"])
        sql += f" ORDER BY rank DESC LIMIT {limit}"
        return await self.db.fetch(sql, *params)
```

`[MVP]` 向量 + 关键词并行检索 + RRF 融合全量实现。
`[V2]` 加权融合(可配置向量/关键词权重);查询重写(模型扩展查询词)。

---

### 4.14 Reranker 重排

决策 7(d):bge-reranker 二次精排。

**reranker 模型加载**(Worker 进程):

```python
class RerankerWorker:
    MODEL_NAME = "BAAI/bge-reranker-v2-m3"
    
    def __init__(self):
        from FlagEmbedding import FlagReranker
        self.model = FlagReranker(self.MODEL_NAME, use_fp16=True)
    
    def rerank(self, query: str, candidates: list[Chunk],
               top_k: int = 5) -> list[Chunk]:
        """对候选 chunk 重排,返回 top-k"""
        pairs = [[query, c.text] for c in candidates]
        scores = self.model.compute_score(pairs, normalize=True)
        # 按分数降序排序
        ranked = sorted(zip(candidates, scores), key=lambda x: x[1], reverse=True)
        return [c for c, _ in ranked[:top_k]]
```

**与混合检索的衔接**:

```python
class KnowledgeBaseService:
    async def search_with_rerank(
        self, query: str, scenario: str = None, top_k: int = 5
    ) -> list[Chunk]:
        # 1. 查询向量化(Worker)
        query_vector = await self.embedding_service.embed_single(query)
        # 2. 混合检索 top-20(4.13)
        candidates = await self.kb_repo.hybrid_search(
            query, query_vector, scenario=scenario, limit=20
        )
        # 3. reranker 精排 top-5(Worker)
        reranked = await self.reranker_service.rerank(query, candidates, top_k=top_k)
        return reranked
```

**延迟考量**:本地 CPU 推理,bge-reranker-v2-m3 对 20 个候选的重排约 200-500ms(取决于 CPU 性能)。与 embedding 共享 Worker 进程,避免额外进程开销。

**reranker 不可用降级**:Worker 崩溃时,跳过重排,直接返回混合检索 top-5,日志告警。检索质量略降但不中断。

`[MVP]` bge-reranker 加载 + 重排 + 降级全量实现。
`[V2]` GPU 加速;多 reranker 模型路由(按语言/场景选择)。

---

### 4.15 Agentic RAG 工具设计

决策 8(B):`search_knowledge` 工具,Agent 自主调用。

**工具定义**(遵循 3.8 规范):

```python
ToolDef(
    name="search_knowledge",
    description="Search the knowledge base for relevant information. "
                "Args: query (str, required), scenario (str, optional, "
                "one of office/data_analysis/frontend_design), "
                "top_k (int, default 5), min_similarity (float, default 0.2), "
                "page (int, default 1), page_size (int, default 5). "
                "Returns: list of {content, source, metadata, score}.",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "scenario": {
                "type": "string",
                "enum": ["office", "data_analysis", "frontend_design"],
                "description": "Scenario filter, optional"
            },
            "top_k": {"type": "integer", "default": 5, "minimum": 1, "maximum": 20},
            "min_similarity": {
                "type": "number", "default": 0.2, "minimum": 0.0, "maximum": 1.0,
                "description": "Minimum similarity score after rerank, filter low-score chunks"
            },
            "page": {"type": "integer", "default": 1, "minimum": 1},
            "page_size": {"type": "integer", "default": 5, "minimum": 1, "maximum": 20}
        },
        "required": ["query"]
    },
    category="perception",
    safety_level="safe",
    timeout_seconds=10,  # 检索 + rerank 应在 10s 内完成
    sequential=False,
)
```

**工具执行**:

```python
async def execute_search_knowledge(
    args: dict, session: Session
) -> list[dict]:
    query = args["query"]
    scenario = args.get("scenario")
    top_k = args.get("top_k", 5)
    min_similarity = args.get("min_similarity", 0.2)
    page = args.get("page", 1)
    page_size = args.get("page_size", 5)
    
    # 场景强制过滤(缺口补充):防止跨场景文档泄露
    if scenario is None:
        logger.warning("search_knowledge called without scenario, searching all KB")
    
    chunks = await kb_service.search_with_rerank(
        query, scenario=scenario, top_k=top_k * page  # 多取用于分页
    )
    
    # 1. min_similarity 过滤:重排后剔除低分 chunk,减少 Stable Zone 污染
    filtered = [c for c in chunks if c.score >= min_similarity]
    if not filtered:
        return [{"content": "未找到匹配知识库内容", "source": None, "metadata": {}}]
    
    # 2. 分页切片:支持大结果集流式返回,缓解单条 tool_result token 压力
    start = (page - 1) * page_size
    end = start + page_size
    page_chunks = filtered[start:end]
    
    return [{
        "content": c.text,
        "source": c.source,
        "metadata": c.metadata,
        "score": c.score,
        "has_more": end < len(filtered)  # 提示 Agent 是否还有下一页
    } for c in page_chunks]
```

**Stable Zone 片段计数器**(缺口补充):每次 `search_knowledge` 返回结果注入 Stable Zone 前,自动累加计数;达到 20 条提前触发 3.10.3 Stable 合并压缩,避免长期会话 Stable Zone 无限膨胀:

```python
async def _handle_kb_search_result(
    self, session, tool_result: list[dict]
):
    kb_text = self._format_kb_chunks(tool_result)
    if self.config.kb_replace_mode:
        # V2:重新渲染 system prompt(触发 hash 变更)
        await self._rerender_frozen_zone(session, kb_context=kb_text)
    else:
        # MVP:追加到 Stable Zone
        msg = Message(
            role="user",
            content=f"[KB Context]\n{kb_text}",
            zone="stable"
        )
        await self.messages_repo.insert(session.id, msg)
        
        # 累加 KB 片段计数,超 20 条触发 Stable 合并压缩
        session.kb_chunks_count += len(tool_result)
        if session.kb_chunks_count >= 20:
            await self.context_manager.merge_stable_zone(session)
            session.kb_chunks_count = 0  # 合并后重置
```

> 计数绑定会话级 `session.kb_chunks_count`,会话销毁自动重置;与 3.10.3 每 5 轮合并规则正交,任一条件满足即触发。

**Skill system prompt 内置说明**(Agent 自主判断):

```markdown
## 可用工具

### search_knowledge
当你需要查询项目文档、技术规范、历史决策等知识库内容时,调用此工具。
不要在每轮都调用——仅当你判断当前问题需要外部知识支持时才调用。
可按场景过滤(office/data_analysis/frontend_design)。
```

**返回结果注入 Stable Zone**(跨章节关联,3.7):

工具返回的 KB 片段由 `context_manager` 注入 Stable Zone,严格遵循 3.7 的 `kb_replace_mode` 配置:

| `kb_replace_mode` | 注入方式 | 缓存影响 |
|---|---|---|
| `false`(MVP 默认) | 作为独立 `[KB Context]` user 消息追加到 Stable Zone | 不破坏 Frozen Zone hash,KV Cache 友好 |
| `true`(V2 可选) | 重新渲染 system prompt,替换 `{{kb.context}}` 占位符 | Frozen Zone hash 变更,KV Cache 全部 miss |

注入实现见上方"Stable Zone 片段计数器"代码块,其中 `_handle_kb_search_result` 已含 `kb_replace_mode` 分支与片段计数触发合并逻辑。

**多次检索的累积**:Agent 可能在一轮内多次调用 `search_knowledge`(如先查背景,再查细节)。每次检索结果独立追加到 Stable Zone,直到触发 3.10.3 合并压缩(每 5 轮或超过 20 条片段,由 `session.kb_chunks_count` 计数器自动触发)。

`[MVP]` 工具定义(min_similarity + 分页)+ 执行(过滤 + 分页)+ Stable Zone 注入(`kb_replace_mode=false`)+ 片段计数触发合并全量实现。
`[V2]` `kb_replace_mode=true` 运行时替换;Agent 自主决定检索深度(基于首次结果判断是否需要二次检索);完整异步流式大结果集返回。

---

### 4.16 知识库增量更新与版本快照

决策 10(a + 快照):文档变更仅重计算对应 chunk 向量;批量变更生成快照。

**增量更新流程**:

```python
class KnowledgeBaseService:
    async def update_document(self, doc_id: int, content: str, 
                               filename: str, scenario: str):
        # 1. 标记旧 chunk 为 inactive(软删除)
        await self.kb_repo.deactivate_by_doc(doc_id)
        # 2. 重新 chunking + embedding
        chunks = self.processor.process(content, filename, scenario)
        vectors = await self.embedding_service.embed_with_fallback(chunks)
        # 3. 写入新 chunk
        await self.kb_repo.batch_insert_chunks(doc_id, scenario, chunks, vectors)
        # 4. 记录更新事件
        await self.react_events_repo.insert(
            session_id=None,  # 知识库更新不绑定会话
            turn=0,
            event_type="kb_document_updated",
            payload={"doc_id": doc_id, "chunks_count": len(chunks)}
        )
```

**批量变更与快照**:

```python
async def batch_update_with_snapshot(self, updates: list[DocumentUpdate]):
    # 执行所有更新
    for update in updates:
        await self.update_document(
            update.doc_id, update.content, update.filename, update.scenario
        )
    # 生成知识库快照
    await self._save_kb_snapshot()

async def _save_kb_snapshot(self):
    stats = await self.kb_repo.get_stats()  # 文档数、chunk 数、场景分布
    await self.versions_repo.save_snapshot(
        scope="kb_snapshot",
        version=f"kb-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
        payload=stats
    )
```

**快照内容**(存入 `version_snapshots`,2.10):

```json
{
  "total_documents": 42,
  "total_chunks": 1580,
  "scenarios": {
    "office": {"docs": 15, "chunks": 580},
    "data_analysis": {"docs": 12, "chunks": 420},
    "frontend_design": {"docs": 15, "chunks": 580}
  },
  "embedding_model": "bge-m3",
  "vector_dim": 1024,
  "timestamp": "2026-07-29T10:00:00+08:00"
}
```

**评估回放关联**:评估时(第 8 章)可读取历史 `kb_snapshot`,还原评估时的知识库状态,确保评估结果可复现。

**软删除与清理**:`is_active = FALSE` 的 chunk 保留供评估回放;定期清理任务(2.10 磁盘管理)可清理超过 90 天的 inactive chunk,释放存储。

`[MVP]` 增量更新 + 批量快照 + 评估回放支持全量实现。
`[V2]` 知识库多版本对比(diff 可视化);基于快照的回滚(恢复到历史版本)。

---

### 4.17 MVP 与 V2 边界

本章设计按 MVP / V2 边界落地,与 2.16/3.16 边界表对齐。

**MVP 必须实现**:

| 模块 | MVP 范围 |
|---|---|
| 记忆策略 | LLM 摘要提取(每 8 轮 + 会话结束)(4.2) |
| 记忆存储 | 结构化条目 + 四类 type + importance(4.3) |
| 记忆淘汰 | 数量上限 + 低重要性超期淘汰 + 软删除(4.4) |
| 记忆注入 | top 10 注入 Stable Zone + 访问记录(4.5) |
| 文档处理流水线 | 端到端 + Worker 纯计算 + 云端降级(4.6) |
| 类型识别 | Markdown/PDF/Code/Plain 四类(4.7) |
| chunking | 三类语义 + 固定长度兜底(4.8) |
| chunk 参数 | 三类模板 + 运行时覆盖(4.9) |
| Embedding | bge-m3 本地 + Worker 集成 + 云端降级(4.10) |
| HNSW 索引 | m=16/ef_construction=128/ef_search=64 可调(4.11) |
| kb_chunks 表 | 统一表 + 四类索引 + metadata(4.12) |
| 混合检索 | 向量 + 关键词 + RRF 融合(4.13) |
| Reranker | bge-reranker 重排 + 降级(4.14) |
| Agentic RAG | search_knowledge 工具 + Stable Zone 注入(4.15) |
| 增量更新 | 文档变更增量重算 + 批量快照(4.16) |

**V2 预留接口**:

| 模块 | V2 范围 | 预留接口 |
|---|---|---|
| Agentic Memory | Agent 主动 remember/recall | 工具定义预留,ToolDef schema 可扩展 |
| 智能合并 | 语义相似度合并记忆 | `memories_repo` 已支持批量查询,合并逻辑可扩展 |
| 递归/模型分块 | 更精细的语义分块 | `DocumentProcessor` 支持新策略枚举 |
| 自适应调参 | 基于检索反馈调整 chunk_size | 评估数据可反向输入参数调优 |
| GPU 加速 | bge-m3/bge-reranker GPU 推理 | Worker 进程模型加载逻辑可扩展 device 参数 |
| 多 embedding 路由 | 按文档语言选择模型 | `EmbeddingService` 支持多模型注册 |
| 向量维度自适应 | 降级时重建索引 | 索引重建接口已定义(4.11) |
| 加权融合 | 可配置向量/关键词权重 | RRF 可替换为加权融合策略 |
| 查询重写 | 模型扩展查询词 | 检索前可插入重写步骤 |
| kb_replace_mode=true | 运行时替换 KB 占位符 | 配置开关已定义(3.7),替换逻辑预留 |
| 知识库版本对比 | diff 可视化 | 快照已存储,对比逻辑可扩展 |
| 知识库回滚 | 恢复到历史版本 | 快照 + 软删除支持回滚 |

**与三大约束的对应**:

- 上下文质量优先 → 4.5 记忆注入 top 10 限制;4.13-4.14 混合检索 + reranker 精排;4.15 Agent 自主决定检索(避免无意义检索污染上下文)。
- 缓存友好 → 4.15 KB 片段注入 Stable Zone(不破坏 Frozen Zone hash,`kb_replace_mode=false`);4.16 增量更新不影响已有会话的 Stable Zone。
- 评估驱动迭代 → 4.2 记忆提取事件入 `react_events`;4.4 淘汰事件入 `react_events`;4.16 知识库快照入 `version_snapshots`;软删除保留历史数据供回放。

---

第 4 章起草完成。本章展开了记忆与知识库层的完整实现:用户记忆(LLM 摘要提取/结构化存储/淘汰/注入)、知识库 RAG 全栈(文档处理/chunking/bge-m3 embedding/HNSW 索引/混合检索/reranker/Agentic RAG 工具/增量更新与快照),共 17 节,所有决策严格复用前序锁定结论。

后续章节衔接:

- 第 5 章:展开工具层(3.8 工具规范)的 MCP 集成与通用工具集设计。
- 第 6 章:展开沙箱代码执行,与本章 `search_knowledge` 工具同属工具层但独立成章。
- 第 8 章:基于 4.16 知识库快照与 4.2 记忆提取事件构建评估闭环。

---

## 第 5 章 工具层与 MCP 集成

本章展开 3.8 工具规范的工程落地,核心是**内置工具 + MCP 工具双轨架构**:内置工具(search_knowledge/沙箱/文件/HTTP/搜索/计算器/时间)进程内直接调用,MCP 工具(GitHub/Slack 等外部服务)走 MCP 协议统一适配,两者共享 3.8 ToolDef schema 对 Agent 透明。

本章与第 6 章的边界:第 5 章定义沙箱代码执行工具的**接口契约**,第 6 章展开沙箱**实现机制**。

每节末尾以 `[MVP]` 或 `[V2]` 标注实现边界。

---

### 5.1 工具层在架构中的位置与职责边界

**在架构中的位置**:

```
┌──────────────────────────────────────────────────────────┐
│  ReAct 主循环(2.4)                                       │
│    ↓ 模型输出 tool_calls                                  │
│  Tool Dispatcher(本章)                                   │
│    ├── 内置工具(进程内 Python 函数)                      │
│    │   ├── search_knowledge(4.15)                        │
│    │   ├── code_execution(→ 第 6 章沙箱)                 │
│    │   ├── web_search / file_read_write / http_request   │
│    │   └── get_time / calculator                         │
│    └── MCP 工具(外部进程 / 远程服务)                     │
│        ├── stdio MCP server(本地子进程)                  │
│        └── HTTP MCP server(本地 / 远程)                  │
│    ↓ 工具结果                                             │
│  Context Manager(第 3 章,结果回灌 Stable/Active Zone)   │
└──────────────────────────────────────────────────────────┘
```

**职责边界**:

| 职责 | 归属 | 说明 |
|---|---|---|
| 工具发现/注册 | 本章 | 启动时加载,会话隔离 |
| 工具调度/执行 | 本章 | 并发控制、超时、重试 |
| 权限确认 | 本章 | safety_level 分级 |
| 结果大小处理 | 本章 | 截断 + artifact |
| 结果回灌上下文 | 第 3 章 | context_manager 处理 |
| 沙箱实现 | 第 6 章 | 本章只调接口 |
| 工具 schema 规范 | 3.8 | 本章遵循 |

**核心原则**:工具层对 Agent 完全透明——Agent 不感知工具是内置还是 MCP,只看到统一的 ToolDef 列表。

`[MVP]` 双轨架构 + 透明调度实现。
`[V2]` 工具市场(动态安装 MCP server);基于使用模式的工具推荐。

---

### 5.2 内置工具与 MCP 工具的双轨架构

决策 4(C):内置工具优先直接调用(性能优),MCP 作为外部工具统一适配。

**双轨抽象**:

```python
class ToolDispatcher:
    """统一工具调度入口,对 Agent 透明"""
    
    def __init__(self, builtin_tools: dict, mcp_client: MCPClient):
        self.builtin = builtin_tools    # name → BuiltinTool
        self.mcp = mcp_client          # MCPClient 管理 MCP 工具
    
    async def list_tools(self, session: Session) -> list[ToolDef]:
        """返回当前会话可用工具列表(内置 + MCP 合并)"""
        builtin_defs = [t.to_def() for t in self.builtin.values()]
        mcp_defs = await self.mcp.list_tools(session.skill_id)
        return builtin_defs + mcp_defs
    
    async def execute(self, tool_name: str, args: dict, 
                      session: Session) -> ToolResult:
        """统一执行入口,自动路由到内置或 MCP"""
        if tool_name in self.builtin:
            return await self._execute_builtin(tool_name, args, session)
        elif await self.mcp.has_tool(tool_name, session.skill_id):
            return await self._execute_mcp(tool_name, args, session)
        else:
            raise ToolNotFoundError(f"Tool not found: {tool_name}")
```

**内置工具与 MCP 工具的差异**:

| 维度 | 内置工具 | MCP 工具 |
|---|---|---|
| 调用方式 | 进程内 Python 函数 | stdio/HTTP 跨进程 |
| 性能开销 | 无 IO,最快 | 有进程通信开销 |
| 配置 | 代码内置,config 调参 | config.yaml + config_runtime |
| 生命周期 | 随主进程 | 独立子进程或远程服务 |
| 适用场景 | 高频/核心能力 | 外部服务集成 |
| 典型示例 | search_knowledge/file_read | GitHub/Slack/Figma |

**透明性保证**:两者共享 3.8 ToolDef schema,Agent 看到的工具列表无来源标记差异。`to_def()` 方法在内置工具上返回标准 ToolDef,与 MCP `tools/list` 返回的格式一致。

`[MVP]` 双轨调度 + 透明路由实现。
`[V2]` 混合工具(一个工具部分逻辑内置、部分走 MCP);工具来源标记(评估分析用,Agent 不可见)。

---

### 5.3 MCP Client 实现与协议适配

决策 2(B):基于 official MCP Python SDK;决策 1(D):stdio + HTTP 双传输。

> **MCP 2026-07-28 基线注记**:MVP 锁定 MCP `2025-11-25` 协议(initialize 握手 + `Mcp-Session-Id` + 粘性路由);`2026-07-28` 无状态协议的双协议分发(server/discover + `_meta` + `Mcp-Method` 头 + MRTR)降级至 V2。依据:MCP Python SDK v2.0.0rc1 非稳定版(`pip install mcp` 仍解析到 v1.x stable),旧协议至少 12 个月弃用宽限,蓝图所列 github/figma/local-files server 当前均支持 2025-11-25。V2 切换 gate on v2.0.0 stable 正式发布(见 5.18、9.9 风险十二)。

**MCP Client 架构**:

```python
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamablehttp_client

class MCPClient:
    """管理多个 MCP server 连接,提供统一工具调用接口"""
    
    def __init__(self):
        self.sessions: dict[str, ClientSession] = {}  # server_name → session
        self.transports: dict[str, Any] = {}           # server_name → transport
    
    async def connect_stdio(self, config: MCPServerConfig) -> None:
        """连接 stdio MCP server(本地子进程)"""
        params = StdioServerParameters(
            command=config.command,
            args=config.args,
            env={**os.environ, **config.env}
        )
        transport = await stdio_client(params).__aenter__()
        session = ClientSession(*transport)
        await session.initialize()
        self.sessions[config.name] = session
        self.transports[config.name] = transport
    
    async def connect_http(self, config: MCPServerConfig) -> None:
        """连接 HTTP/SSE MCP server(本地或远程)"""
        if config.transport == "sse":
            transport = await sse_client(config.url).__aenter__()
        else:  # streamable_http
            transport = await streamablehttp_client(config.url).__aenter__()
        session = ClientSession(*transport)
        await session.initialize()
        self.sessions[config.name] = session
        self.transports[config.name] = transport
    
    async def list_tools(self, skill_id: str) -> list[ToolDef]:
        """列出当前 Skill 依赖的所有 MCP server 的工具"""
        tools = []
        for server_name in self._get_skill_servers(skill_id):
            session = self.sessions.get(server_name)
            if session:
                result = await session.list_tools()
                for t in result.tools:
                    tools.append(self._convert_mcp_tool(t, server_name))
        return tools
    
    async def call_tool(self, tool_name: str, args: dict,
                        skill_id: str) -> ToolResult:
        """调用 MCP 工具(工具名格式:server_name.tool_name)"""
        server_name, actual_name = tool_name.split(".", 1)
        session = self.sessions.get(server_name)
        if not session:
            raise MCPConnectionError(f"Server not connected: {server_name}")
        result = await session.call_tool(actual_name, args)
        return self._convert_mcp_result(result)
```

**协议版本协商**:MCP SDK 在 `initialize` 阶段自动协商协议版本,无需手动处理。若 server 版本不兼容,SDK 抛出异常,被 5.13 重试逻辑捕获。

> **MCP 2026-07-28 协商注记**:MVP 依赖 v1.x stable SDK 的 `initialize` 协商(`pyproject.toml` 锁 `mcp>=1.0,<2.0`);V2 切换 v2.0.0+ 后改为 `server/discover` 探测 + `_meta` 携带协议版本/能力,无会话、无 `Mcp-Session-Id`,请求可路由到任意实例。双协议版本分发逻辑(negotiate_version + 按版本分发)降级至 V2。

**连接管理**:

- **连接池**:`sessions` 字典维护所有活跃连接,会话隔离(不同 Skill 加载不同 server 子集)。
- **断线重连**:调用工具时检测连接状态,断开则自动重连一次,仍失败则走 5.13 重试逻辑。
- **健康检查**:定期(每 60s)对 idle 连接发 `ping`,失败则标记为不可用。

**MCP tool 转换为 ToolDef**:

```python
def _convert_mcp_tool(self, mcp_tool: Any, server_name: str) -> ToolDef:
    """将 MCP tool 转换为 3.8 统一 ToolDef"""
    return ToolDef(
        name=f"{server_name}.{mcp_tool.name}",  # 命名空间隔离
        description=mcp_tool.description,
        parameters=mcp_tool.inputSchema,
        category="collaboration",  # MCP 工具默认协作类
        safety_level="elevated",   # MCP 外部工具默认需确认
        timeout_seconds=30,
        sequential=False,
        source="mcp"  # 内部标记,Agent 不可见
    )
```

`[MVP]` stdio + HTTP 双传输 + 连接管理 + 工具转换全量实现。
`[V2]` 连接池预热;多 server 并行工具发现;MCP 资源(resources)与提示(prompts)支持。

---

### 5.4 MCP Server 配置管理与探活

决策 10:混合配置(config.yaml 静态 + config_runtime 运行时) + stdio/ping + HTTP/health 双探活。

**配置 schema**(config.yaml):

```yaml
mcp:
  servers:
    - name: "github"
      transport: "stdio"           # stdio | sse | streamable_http
      command: "npx"
      args: ["-y", "@modelcontextprotocol/server-github"]
      env:
        GITHUB_TOKEN: "${GITHUB_TOKEN}"  # 环境变量引用
      scenario: "frontend_design"  # 场景归属(5.5 按 Skill 加载)
      enabled: true
    
    - name: "figma"
      transport: "streamable_http"
      url: "http://localhost:3845/mcp"
      scenario: "frontend_design"
      enabled: true
    
    - name: "local-files"
      transport: "stdio"
      command: "python"
      args: ["-m", "mcp_server_files", "--root", "${WORKSPACE}"]
      scenario: "office"
      enabled: true
```

**运行时配置**(config_runtime 表):

```json
{
  "mcp.servers.github.enabled": false,
  "mcp.servers.figma.url": "http://localhost:4000/mcp"
}
```

加载优先级(2.14):`config_runtime` > `config.yaml` > 默认。

**UI 管理**:HTTP API 提供 MCP server CRUD:

- `POST /api/mcp/servers` 新增
- `PUT /api/mcp/servers/{name}` 修改(启停/参数)
- `DELETE /api/mcp/servers/{name}` 删除
- `GET /api/mcp/servers` 列表(含状态)
- `POST /api/mcp/servers/{name}/test` 手动探活

**双探活机制**:

```python
class MCPHealthChecker:
    STDIO_CHECK_INTERVAL = 60  # 秒
    HTTP_CHECK_INTERVAL = 30
    
    async def check_stdio(self, server_name: str) -> bool:
        """stdio server:检查子进程是否存活"""
        session = self.client.sessions.get(server_name)
        if not session:
            return False
        try:
            await asyncio.wait_for(session.send_ping(), timeout=5)
            return True
        except (asyncio.TimeoutError, Exception):
            return False
    
    async def check_http(self, server_name: str, url: str) -> bool:
        """HTTP server:GET /health endpoint"""
        try:
            health_url = url.rstrip("/") + "/health"
            async with httpx.AsyncClient() as client:
                resp = await client.get(health_url, timeout=5)
                return resp.status_code == 200
        except Exception:
            return False
```

**探活失败处理**:标记 server 为 `unavailable`,UI 显示状态;Agent 调用该 server 工具时返回 `ToolUnavailableError`,走 5.13 降级逻辑;定期重试探活(指数退避,最长间隔 5 分钟)。

> **MCP 2026-07-28 探活注记**:`session.send_ping()`(stdio)与 GET `/health`(HTTP)在新协议下语义不变,探活逻辑无需改动。MVP 保持现有双探活;V2 无状态协议下探活可改为轻量 `server/discover` 或 `tools/list` 探测,不再依赖会话状态。

`[MVP]` 混合配置 + UI CRUD + 双探活全量实现。
`[V2]` MCP server 自动发现(局域网 mDNS);配置导入导出;探活历史趋势。

---

### 5.5 工具发现与动态加载

决策 5(B):按 Skill 需求加载,避免全量加载浪费。

**Skill 声明工具依赖**(联动 2.11 Skills 三层流转):

```yaml
# skills/data_analysis/skill.yaml
name: "data_analysis"
version: "1.0.0"
system_prompt: "system_prompt.md"
tools:
  builtin:
    - "search_knowledge"
    - "code_execution"
    - "file_read"
    - "file_write"
    - "http_request"
    - "get_time"
    - "calculator"
  mcp:
    - "postgres-mcp"        # 数据库 MCP server
    - "excel-mcp"           # Excel 处理 MCP server
```

**会话启动加载流程**:

```python
class ToolDispatcher:
    async def load_session_tools(self, session: Session) -> None:
        """会话启动时,按 Skill 依赖加载工具"""
        skill = await self.skills_repo.get(session.skill_id)
        
        # 1. 注册内置工具(直接引用,无加载开销)
        for tool_name in skill.tools.builtin:
            if tool_name in self.builtin:
                session.registered_tools[tool_name] = self.builtin[tool_name]
        
        # 2. 连接并加载 MCP server
        for server_name in skill.tools.mcp:
            config = await self.mcp_config_repo.get(server_name)
            if config and config.enabled:
                try:
                    if config.transport == "stdio":
                        await self.mcp.connect_stdio(config)
                    else:
                        await self.mcp.connect_http(config)
                    session.connected_mcp_servers.append(server_name)
                except Exception as e:
                    logger.warning(f"MCP server {server_name} connect failed: {e}")
                    # 不阻断会话启动,该 server 工具不可用
```

**工具注册表(内存中,会话隔离)**:

```python
@dataclass
class SessionToolRegistry:
    session_id: str
    registered_tools: dict[str, ToolDef]  # name → ToolDef
    
    def get_available_tools(self) -> list[ToolDef]:
        return list(self.registered_tools.values())
```

**工具版本管理**:同一工具不同版本共存(MVP 暂不实现,V2 支持)。MVP 阶段每个工具名全局唯一,版本变更需重启会话(与 2.11 Skills 版本锁定一致)。

**运行时工具变更**:会话运行中不支持动态增减工具(会破坏 Frozen Zone hash,2.11 已约束)。用户需在 UI 切换 Skill 或重启会话。

`[MVP]` 按 Skill 加载 + 会话隔离 + 连接失败降级实现。
`[V2]` 多版本工具共存;运行时工具热加载(需 cache 重建,2.11 V2)。

---

### 5.6 通用工具集 MVP 清单与设计

决策 3:MVP 内置工具 6 类 + 沙箱接口。

**MVP 工具总清单**:

| 工具名 | 类别 | safety_level | 超时 | 说明 |
|---|---|---|---|---|
| `web_search` | 感知 | safe | 10s | 调用搜索 API 获取实时信息 |
| `file_read` | 感知 | safe | 10s | 读取本地文件(白名单内) |
| `file_write` | 执行 | elevated | 10s | 写入本地文件(白名单内) |
| `file_list` | 感知 | safe | 5s | 列出目录内容 |
| `http_request` | 执行 | elevated | 30s | 通用 HTTP client |
| `get_time` | 感知 | safe | 2s | 获取当前时间/时区转换 |
| `calculator` | 执行 | safe | 5s | 精确数值计算 |
| `code_execution` | 执行 | elevated | 60s | 调用第 6 章沙箱 |
| `search_knowledge` | 感知 | safe | 10s | 第 4 章知识库检索(内置) |

**V2 预留工具**:

| 工具名 | 类别 | 说明 |
|---|---|---|
| `database_query` | 执行 | 直接连 PostgreSQL 查询 |
| `shell_execute` | 执行 | 系统命令执行(高风险) |

**设计原则**:

- 内置工具遵循 3.8 ToolDef schema(OpenAI function calling + 扩展字段)。
- 每类工具的 `parameters` 严格定义 JSON Schema,支持模型自动补全参数。
- `description` 写明用途、参数含义、返回格式,供模型理解。
- 与书中五类工具(感知/执行/协作/事件/沟通)的映射见 5.17。

后续 5.7-5.11 逐个展开 MVP 工具的设计。

`[MVP]` 9 类工具全量实现。
`[V2]` 数据库查询、Shell 执行;工具自动补全(基于历史调用模式推荐参数)。

---

### 5.7 Web 搜索工具

**工具定义**:

```python
ToolDef(
    name="web_search",
    description="Search the web for real-time information. "
                "Use for current events, API docs, or facts not in knowledge base. "
                "Args: query (str), max_results (int, default 5). "
                "Returns: list of {title, url, snippet}.",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query"},
            "max_results": {"type": "integer", "default": 5, "minimum": 1, "maximum": 20}
        },
        "required": ["query"]
    },
    category="perception",
    safety_level="safe",
    timeout_seconds=10,
    sequential=False,
)
```

**搜索 API 选型**(config 可配):

```yaml
tools:
  web_search:
    provider: "tavily"    # tavily | serper | custom
    api_key_env: "TAVILY_API_KEY"
    max_results_default: 5
```

- **Tavily**:AI 友好,返回结构化 snippet,推荐默认。
- **Serper**:Google 搜索结果,覆盖面广。
- **custom**:自建搜索代理,适配企业内网搜索。

**返回格式**:

```python
async def execute_web_search(args: dict, session: Session) -> list[dict]:
    query = args["query"]
    max_results = args.get("max_results", 5)
    
    provider = config.tools.web_search.provider
    api_key = get_env(config.tools.web_search.api_key_env)
    
    if provider == "tavily":
        results = await tavily_search(query, api_key, max_results)
    else:
        results = await serper_search(query, api_key, max_results)
    
    return [{
        "title": r.title,
        "url": r.url,
        "snippet": r.snippet
    } for r in results[:max_results]]
```

**与知识库检索的边界**(联动 4.15):

| 场景 | 用 web_search | 用 search_knowledge |
|---|---|---|
| 实时信息(新闻、股价) | ✓ | ✗ |
| API 文档(最新版本) | ✓ | △(可缓存到 KB) |
| 项目内部文档 | ✗ | ✓ |
| 历史决策记录 | ✗ | ✓ |
| 通用知识(模型已知) | ✗ | ✗(直接回答) |

Skill system prompt 应明确告知 Agent 何时用哪个工具,避免重复检索浪费。

`[MVP]` Tavily/Serper 双 provider + 配置化实现。
`[V2]` 自建搜索代理;搜索结果缓存(短 TTL);多引擎聚合。

---

### 5.8 文件读写工具

**工具定义**(三件套):

```python
FILE_READ = ToolDef(
    name="file_read",
    description="Read content of a file within the workspace whitelist. "
                "Args: path (str, relative to workspace), "
                "max_lines (int, default 1000). "
                "Returns: file content (truncated if exceeds limit).",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "Relative path to file"},
            "max_lines": {"type": "integer", "default": 1000, "minimum": 1, "maximum": 10000}
        },
        "required": ["path"]
    },
    category="perception",
    safety_level="safe",
    timeout_seconds=10,
)

FILE_WRITE = ToolDef(
    name="file_write",
    description="Write content to a file within the workspace whitelist. "
                "Args: path (str), content (str), append (bool, default false). "
                "Returns: {bytes_written, path}.",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "content": {"type": "string"},
            "append": {"type": "boolean", "default": False}
        },
        "required": ["path", "content"]
    },
    category="execution",
    safety_level="elevated",  # 写操作需确认
    timeout_seconds=10,
)

FILE_LIST = ToolDef(
    name="file_list",
    description="List files in a directory within the workspace. "
                "Args: path (str, default '.'), recursive (bool, default false).",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "default": "."},
            "recursive": {"type": "boolean", "default": False}
        }
    },
    category="perception",
    safety_level="safe",
    timeout_seconds=5,
)
```

**工作目录白名单**:

```yaml
tools:
  file:
    workspace_root: "${WORKSPACE}"    # 默认项目根目录
    allowed_paths:
      - "${WORKSPACE}/data"
      - "${WORKSPACE}/artifacts"
      - "${WORKSPACE}/output"
    denied_paths:
      - "${WORKSPACE}/.git"
      - "${WORKSPACE}/.env"
    max_file_size_mb: 10
```

**路径校验(防目录穿越)**:

```python
class FileTool:
    def __init__(self, workspace_root: str, allowed_paths: list[str]):
        self.workspace_root = Path(workspace_root).resolve()
        self.allowed_paths = [Path(p).resolve() for p in allowed_paths]
    
    def _validate_path(self, relative_path: str) -> Path:
        """校验路径在白名单内,防止目录穿越"""
        full = (self.workspace_root / relative_path).resolve()
        # 检查是否在允许的路径下
        if not any(self._is_subpath(full, allowed) for allowed in self.allowed_paths):
            raise PermissionError(f"Path outside whitelist: {relative_path}")
        # 检查是否逃逸 workspace
        if not self._is_subpath(full, self.workspace_root):
            raise PermissionError(f"Path escapes workspace: {relative_path}")
        return full
    
    def _is_subpath(self, child: Path, parent: Path) -> bool:
        try:
            child.relative_to(parent)
            return True
        except ValueError:
            return False
```

**大文件处理**(联动 5.15 artifact 机制):

- 文件 > `max_file_size_mb`:拒绝读取,提示 Agent 用 `code_execution` 流式处理。
- 读取结果 > 4k token:截断 + 写入 artifact,返回截断内容 + artifact 路径。

`[MVP]` 三件套 + 路径校验 + 白名单实现。
`[V2]` 文件变更监听(watchdog);网络文件系统支持;文件差异读取(只读变更部分)。

---

### 5.9 HTTP 请求工具

**工具定义**:

```python
ToolDef(
    name="http_request",
    description="Send HTTP request to external API. "
                "Args: method (str), url (str), headers (object, optional), "
                "body (str, optional), timeout (int, default 30). "
                "Returns: {status_code, headers, body (truncated)}.",
    parameters={
        "type": "object",
        "properties": {
            "method": {"type": "string", "enum": ["GET", "POST", "PUT", "DELETE", "PATCH"]},
            "url": {"type": "string", "format": "uri"},
            "headers": {"type": "object"},
            "body": {"type": "string"},
            "timeout": {"type": "integer", "default": 30, "minimum": 1, "maximum": 120}
        },
        "required": ["method", "url"]
    },
    category="execution",
    safety_level="elevated",  # 外部网络访问需确认
    timeout_seconds=30,
)
```

**域名白名单**(可选,config 配置):

```yaml
tools:
  http:
    whitelist_enabled: true
    allowed_domains:
      - "api.github.com"
      - "api.openai.com"
      - "localhost"
    denied_domains:
      - "*.internal.company.com"
    max_response_size_kb: 100
```

**执行逻辑**:

```python
async def execute_http_request(args: dict, session: Session) -> dict:
    method = args["method"]
    url = args["url"]
    headers = args.get("headers", {})
    body = args.get("body")
    timeout = args.get("timeout", 30)
    
    # 域名白名单校验
    if config.tools.http.whitelist_enabled:
        _check_domain_whitelist(url)
    
    async with httpx.AsyncClient() as client:
        resp = await client.request(
            method, url, headers=headers, content=body, timeout=timeout
        )
    
    # 响应大小处理(联动 5.15 artifact)
    body = resp.text
    if len(body) > config.tools.http.max_response_size_kb * 1024:
        artifact_path = await _save_artifact(body, session.id)
        body = body[:4000] + f"\n... [truncated, full content at {artifact_path}]"
    
    return {
        "status_code": resp.status_code,
        "headers": dict(resp.headers),
        "body": body
    }
```

**安全约束**:

- 默认 `safety_level=elevated`,每次调用需用户确认(5.12)。
- 敏感 header 过滤(如 `Authorization` 不记录到 react_events)。
- 响应体扫描注入风险(联动 3.12 注入防护,对 HTTP 响应做关键词检测)。

`[MVP]` 通用 HTTP + 域名白名单 + 响应 artifact 实现。
`[V2]` 请求模板库(常用 API 预设);OAuth 流程支持;响应缓存(相同请求短 TTL)。

---

### 5.10 时间日期与计算器工具

**时间工具**:

```python
ToolDef(
    name="get_time",
    description="Get current time or convert timezone. "
                "Args: timezone (str, default 'Asia/Shanghai'), "
                "format (str, default ISO). "
                "Returns: {datetime, timezone, unix_timestamp}.",
    parameters={
        "type": "object",
        "properties": {
            "timezone": {"type": "string", "default": "Asia/Shanghai"},
            "format": {"type": "string", "default": "iso"}
        }
    },
    category="perception",
    safety_level="safe",
    timeout_seconds=2,
)
```

**计算器工具**:

```python
ToolDef(
    name="calculator",
    description="Evaluate mathematical expression with precision. "
                "Supports +,-,*,/,**,(),sqrt,log,sin,cos,tan. "
                "Args: expression (str). Returns: {result}.",
    parameters={
        "type": "object",
        "properties": {
            "expression": {"type": "string", "description": "e.g. '2**10 + sqrt(144)'"}
        },
        "required": ["expression"]
    },
    category="execution",
    safety_level="safe",
    timeout_seconds=5,
)
```

**计算器实现(安全 eval)**:

```python
import sympy

async def execute_calculator(args: dict, session: Session) -> dict:
    expr = args["expression"]
    try:
        # 使用 sympy 安全解析,避免 eval 注入
        result = sympy.sympify(expr)
        # 数值化
        if result.is_number:
            result = float(result)
        return {"result": str(result)}
    except Exception as e:
        raise ToolExecutionError(f"Calculator error: {e}")
```

**设计考量**:

- 这两类工具避免 LLM 的时间幻觉与计算错误。
- `get_time` 返回结构化时间,Agent 可用于日志、调度、时区转换。
- `calculator` 用 sympy 而非 `eval()`,防止代码注入。

`[MVP]` 两个工具全量实现。
`[V2]` 时间工具支持自然语言解析("下周三");计算器支持单位换算、日期运算。

---

### 5.11 沙箱代码执行工具的接口契约

本节定义 `code_execution` 工具的接口,实现在第 6 章。

**工具定义**:

```python
ToolDef(
    name="code_execution",
    description="Execute code in sandbox environment. "
                "Supports Python/JavaScript. "
                "Args: code (str), language (str, default 'python'), "
                "timeout (int, default 60, max 300). "
                "Returns: {stdout, stderr, exit_code, files: [path]}.",
    parameters={
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "Code to execute"},
            "language": {"type": "string", "enum": ["python", "javascript"], "default": "python"},
            "timeout": {"type": "integer", "default": 60, "minimum": 1, "maximum": 300}
        },
        "required": ["code"]
    },
    category="execution",
    safety_level="elevated",
    timeout_seconds=60,
)
```

**执行接口(调用第 6 章沙箱)**:

```python
async def execute_code_execution(args: dict, session: Session) -> dict:
    code = args["code"]
    language = args.get("language", "python")
    timeout = args.get("timeout", 60)
    
    # 调用第 6 章沙箱服务
    result = await sandbox_service.execute(
        code=code,
        language=language,
        timeout=timeout,
        session_id=session.id,
        workspace=session.sandbox_workspace  # 沙箱工作目录
    )
    
    # 结果大小处理(联动 5.15 artifact)
    if len(result.stdout) > 4000:
        artifact_path = await _save_artifact(result.stdout, session.id)
        result.stdout = result.stdout[:4000] + f"\n... [truncated, full at {artifact_path}]"
    
    return {
        "stdout": result.stdout,
        "stderr": result.stderr,
        "exit_code": result.exit_code,
        "files": result.generated_files
    }
```

**与第 6 章的边界**:

- 本章只定义 ToolDef 与调用入口。
- 第 6 章实现沙箱机制(Docker/E2B/复用 Trae 执行能力)、文件系统工作记忆、安全边界。
- 沙箱工作目录由第 6 章管理,会话隔离。

**错误处理**:沙箱超时/崩溃/资源超限的错误由第 6 章抛出,本章按 5.13 重试或降级。

`[MVP]` 接口契约 + 调用入口实现(依赖第 6 章沙箱)。
`[V2]` 多语言支持(Rust/Go/Shell);流式输出(代码执行进度实时推送);沙箱快照恢复。

---

### 5.12 工具调用的权限确认机制

决策 6(B):safe 自动 / elevated 弹窗 / dangerous 拦截。

**权限分级**:

| safety_level | 行为 | 典型工具 |
|---|---|---|
| `safe` | 自动执行,不打断 Agent | web_search, file_read, get_time, calculator, search_knowledge |
| `elevated` | WS 推送确认请求,用户点击后执行 | file_write, http_request, code_execution, MCP 工具 |
| `dangerous` | 直接拦截,不入队 | (配置定义,如 shell_execute V2) |

**权限确认流程**:

```python
class PermissionManager:
    def __init__(self, ws_manager: WebSocketManager):
        self.ws = ws_manager
        self.confirmation_cache: dict[str, bool] = {}  # 会话级缓存
    
    async def check_and_confirm(
        self, tool_name: str, args: dict, session: Session
    ) -> bool:
        tool_def = session.registered_tools[tool_name]
        
        if tool_def.safety_level == "safe":
            return True
        elif tool_def.safety_level == "dangerous":
            logger.warning(f"Dangerous tool blocked: {tool_name}")
            return False
        elif tool_def.safety_level == "elevated":
            # 检查会话级缓存(同工具同参数组合)
            cache_key = self._cache_key(tool_name, args)
            if cache_key in self.confirmation_cache:
                return self.confirmation_cache[cache_key]
            
            # WS 推送确认请求
            confirmation_id = str(uuid.uuid4())
            await self.ws.send(session.user_id, WSEvent(
                type="tool_confirmation_required",
                data={
                    "confirmation_id": confirmation_id,
                    "tool_name": tool_name,
                    "args": args,
                    "message": f"Allow tool '{tool_name}' to execute?"
                }
            ))
            
            # 等待用户响应(超时 60s 自动拒绝)
            try:
                approved = await asyncio.wait_for(
                    self._wait_confirmation(confirmation_id),
                    timeout=60
                )
            except asyncio.TimeoutError:
                approved = False
            
            # 缓存结果(同会话同参数)
            self.confirmation_cache[cache_key] = approved
            return approved
```

**确认缓存规则**:

- 缓存 key:`f"{tool_name}:{hash(json.dumps(args, sort_keys=True))}"`
- 同会话内,首次确认后,相同工具 + 相同参数组合自动放行。
- 不同参数组合需重新确认(如 `file_write` 不同路径)。
- 会话结束清空缓存。

**dangerous 工具列表**(config 配置):

```yaml
tools:
  dangerous:
    - "shell_execute"
    - "database_drop"
    - "file_delete"
```

`[MVP]` 三级权限 + WS 确认流程 + 会话级缓存实现。
`[V2]` 基于用户行为的权限学习(频繁批准的工具降级为 safe);危险操作二次确认(输入特定关键词才放行)。

**MCP 2026-07-28 授权扩展预留**(V2 stub,对应蓝图目录 `backend/core/auth/`):

```python
from abc import ABC, abstractmethod

class AuthProtocol(ABC):
    """V2 预留:对接 MCP 2026-07-28 授权强化(OAuth 2.0 / OIDC / EMA 企业托管授权)。
    
    MCP 新规范强化 iss 验证(防 OAuth mix-up)、弃用 DCR 改用 CIMD(Client ID
    Metadata Documents)、新增 EMA 扩展(企业 IdP 集中管理员工权限)。
    MVP 保持 2.7/2.12 现有 config_runtime API Key + AES-256-GCM 加密方案,
    不接入 OAuth;V2 待 MCP v2.0.0 stable 后以本 ABC 为锚点实现。
    
    追踪项:5.18 V2 清单 / 9.9 风险十二 stub 腐化防护 / SEP-2567 EMA。
    """
    
    @abstractmethod
    async def authenticate(self, credentials: dict) -> dict:
        """认证并返回 token + issuer 元信息(iss 校验防 mix-up)。"""
        ...
    
    @abstractmethod
    async def get_token(self, session_handle: str) -> str:
        """按句柄获取有效 token(显式句柄模式,SEP-2567)。"""
        ...
```

> 注:MVP 运行路径不经过此 ABC,`PermissionManager` 保持现有 API Key 方案。stub 仅作 V2 重构锚点。

---

### 5.13 工具调用超时与重试策略

决策 7:全局默认 30s + 分类差异化;指数退避 3 次;支持中断。

**分类超时配置**:

```yaml
tools:
  timeout:
    perception: 10    # 感知类(搜索/读取)
    execution: 30     # 执行类(写入/HTTP)
    collaboration: 120 # 协作类(MCP 外部服务)
    default: 30
  retry:
    max_attempts: 3
    backoff_base: 1   # 指数退避基数(秒)
    backoff_max: 10   # 单次等待上限
    retryable_errors:
      - "TimeoutError"
      - "ConnectionError"
      - "MCPConnectionError"
```

**调度器实现**:

```python
class ToolDispatcher:
    async def _execute_with_timeout_retry(
        self, tool_name: str, args: dict, session: Session
    ) -> ToolResult:
        tool_def = session.registered_tools[tool_name]
        timeout = tool_def.timeout_seconds or config.tools.timeout.default
        max_attempts = config.tools.retry.max_attempts
        
        last_error = None
        for attempt in range(max_attempts):
            try:
                # 支持用户中断
                result = await asyncio.wait_for(
                    self._execute_with_cancel_check(
                        tool_name, args, session
                    ),
                    timeout=timeout
                )
                # 记录调用事件(联动评估)
                await self._log_tool_call(session, tool_name, args, result, attempt)
                return result
            except asyncio.TimeoutError as e:
                last_error = e
                logger.warning(f"Tool {tool_name} timeout, attempt {attempt+1}")
            except (ConnectionError, MCPConnectionError) as e:
                last_error = e
                logger.warning(f"Tool {tool_name} connection error: {e}")
                # MCP 2026-07-28 注记:MVP 旧协议下重试同实例(依赖 Mcp-Session-Id 粘性路由);
                # V2 无状态协议下重试可路由到不同实例,无需会话亲和性。
            except UserCancelledError:
                raise  # 用户中断不重试
            
            # 指数退避
            if attempt < max_attempts - 1:
                wait = min(
                    config.tools.retry.backoff_base * (2 ** attempt),
                    config.tools.retry.backoff_max
                )
                await asyncio.sleep(wait)
        
        # 重试耗尽,降级处理
        await self._log_tool_failure(session, tool_name, args, last_error)
        raise ToolExecutionError(
            f"Tool {tool_name} failed after {max_attempts} attempts: {last_error}"
        )
    
    async def _execute_with_cancel_check(
        self, tool_name: str, args: dict, session: Session
    ):
        """支持用户中断的执行包装"""
        cancel_event = session.get_cancel_event(tool_name)
        task = asyncio.create_task(self._actual_execute(tool_name, args, session))
        done, pending = await asyncio.wait(
            [task, asyncio.create_task(cancel_event.wait())],
            return_when=asyncio.FIRST_COMPLETED
        )
        if cancel_event.is_set():
            task.cancel()
            raise UserCancelledError(f"Tool {tool_name} cancelled by user")
        return task.result()
```

**用户中断机制**:

- UI 提供"取消"按钮,WS 推送 `cancel_tool` 事件。
- Sidecar 设置会话级 `cancel_event`,工具执行循环检测并中止。
- 中断后工具结果标记为 `cancelled`,不入 react_events 成功记录。

**事件记录**(联动评估,第 8 章):

```python
async def _log_tool_call(self, session, tool_name, args, result, attempt):
    await self.react_events_repo.insert(
        session_id=session.id,
        turn=session.current_turn,
        event_type="tool_call",
        payload={
            "tool": tool_name,
            "args": self._sanitize_args(args),  # 过滤敏感参数
            "success": True,
            "attempt": attempt + 1,
            "duration_ms": result.duration_ms
        }
    )

async def _log_tool_failure(self, session, tool_name, args, error):
    await self.react_events_repo.insert(
        session_id=session.id,
        turn=session.current_turn,
        event_type="tool_error",
        payload={
            "tool": tool_name,
            "args": self._sanitize_args(args),
            "error": str(error),
            "error_type": type(error).__name__
        }
    )
```

`[MVP]` 分类超时 + 指数退避 + 用户中断 + 事件记录全量实现。
`[V2]` 自适应超时(基于历史执行时间动态调整);工具降级策略(主工具失败自动切备用)。

---

### 5.14 异步事件驱动架构

决策 8(a/b/d):长任务进度推送 / 外部回调等待 / 流式工具结果。

**异步任务表 schema**(复用 2.10,扩展):

```sql
CREATE TABLE async_tasks (
    id BIGSERIAL PRIMARY KEY,
    session_id BIGINT NOT NULL,
    tool_name VARCHAR(100) NOT NULL,
    status VARCHAR(20) DEFAULT 'pending',  -- pending/running/completed/failed/cancelled
    progress FLOAT DEFAULT 0,              -- 0.0-1.0
    result JSONB,                          -- 完成后的结果
    error TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    completed_at TIMESTAMPTZ
);

CREATE INDEX idx_async_tasks_session ON async_tasks(session_id, status);
```

**长任务执行流程**:

```python
class AsyncTaskManager:
    async def execute_long_task(
        self, tool_name: str, args: dict, session: Session,
        progress_callback: callable = None
    ) -> str:
        """启动长任务,返回 task_id,立即返回不阻塞"""
        task_id = await self.tasks_repo.create(
            session_id=session.id, tool_name=tool_name
        )
        
        # 后台执行
        asyncio.create_task(self._run_task(
            task_id, tool_name, args, session, progress_callback
        ))
        
        # 立即返回 task_id,Agent 可继续其他工作
        return f"Task started, id={task_id}. Use check_task_status to query."
    
    async def _run_task(self, task_id, tool_name, args, session, progress_cb):
        try:
            await self.tasks_repo.update_status(task_id, "running")
            
            # 执行工具,带进度回调
            result = await self.dispatcher.execute(
                tool_name, args, session,
                progress_callback=lambda p: self._on_progress(task_id, p, session)
            )
            
            await self.tasks_repo.update_status(
                task_id, "completed", result=result
            )
            # WS 推送完成
            await self.ws.send(session.user_id, WSEvent(
                type="async_task_completed",
                data={"task_id": task_id, "result": result}
            ))
        except Exception as e:
            await self.tasks_repo.update_status(task_id, "failed", error=str(e))
            await self.ws.send(session.user_id, WSEvent(
                type="async_task_failed",
                data={"task_id": task_id, "error": str(e)}
            ))
    
    async def _on_progress(self, task_id, progress, session):
        await self.tasks_repo.update_progress(task_id, progress)
        await self.ws.send(session.user_id, WSEvent(
            type="async_task_progress",
            data={"task_id": task_id, "progress": progress}
        ))
```

**Agent 查询任务状态**:

```python
ToolDef(
    name="check_task_status",
    description="Check status of an async task. "
                "Args: task_id (str). Returns: {status, progress, result?}.",
    parameters={
        "type": "object",
        "properties": {"task_id": {"type": "string"}},
        "required": ["task_id"]
    },
    category="perception",
    safety_level="safe",
)
```

**MCP 2026-07-28 Tasks 扩展兼容层**(V2 预留 stub):

```python
from abc import ABC, abstractmethod

class TasksExtensionAdapter(ABC):
    """V2 预留:对接 MCP 2026-07-28 Tasks 扩展(io.modelcontextprotocol/tasks)。
    
    MCP 新规范 Tasks 扩展提供 tasks/get(轮询状态)、tasks/cancel(取消),
    不再有旧版 tasks/result / tasks/list(无会话环境无法确定安全列表范围)。
    MVP 保持上方 AsyncTaskManager 自定义实现;V2 待 MCP v2.0.0 stable 后,
    以本 ABC 为锚点替换为官方 Tasks 扩展实现。
    
    追踪项:5.18 V2 清单 / 9.9 风险十二 stub 腐化防护 / SEP Tasks 扩展。
    """
    
    @abstractmethod
    async def get_task(self, task_handle: str) -> dict:
        """对应 MCP tasks/get,轮询长任务状态。"""
        ...
    
    @abstractmethod
    async def cancel_task(self, task_handle: str) -> None:
        """对应 MCP tasks/cancel,取消长任务。"""
        ...
```

> 注:MVP 运行路径不经过此 ABC,`AsyncTaskManager` 保持现状。stub 仅作 V2 重构锚点,避免切换时改动集中。

**外部回调等待**:

```python
class WebhookManager:
    """接收外部 webhook,触发异步任务完成"""
    
    async def register_webhook(self, task_id: str, expected_url: str) -> str:
        """注册 webhook 等待,返回实际回调 URL"""
        callback_path = f"/webhook/{task_id}"
        await self.tasks_repo.update(task_id, callback_url=callback_path)
        return f"http://localhost:{config.port}{callback_path}"
    
    async def handle_webhook(self, task_id: str, payload: dict):
        """webhook 回调到达,标记任务完成"""
        await self.tasks_repo.update_status(
            task_id, "completed", result=payload
        )
        # WS 推送
        task = await self.tasks_repo.get(task_id)
        await self.ws.send(task.session_id, WSEvent(
            type="async_task_completed",
            data={"task_id": task_id, "result": payload, "source": "webhook"}
        ))
```

**流式工具结果**(联动 5.15 artifact):

- 大型搜索结果(>20 条)分批返回,每批作为独立 tool_result 推送 WS。
- 模型可中途判断已获足够信息,主动取消后续批次。

`[MVP]` 长任务 + 进度推送 + 外部 webhook + 流式结果实现。
`[V2]` 长任务暂停/恢复;任务 DAG(多任务依赖编排);任务优先级队列。

---

### 5.15 工具结果大小限制与 Artifact 机制

决策 9(B):截断 + 写入文件,Agent 按需读取。

**Artifact 机制**:

```python
class ArtifactManager:
    def __init__(self, workspace_root: str):
        self.artifacts_dir = Path(workspace_root) / ".artifacts"
        self.artifacts_dir.mkdir(exist_ok=True)
    
    async def maybe_create_artifact(
        self, content: str, session_id: str, 
        tool_name: str, threshold_tokens: int = 4000
    ) -> dict:
        """结果超阈值时,写入 artifact,返回截断内容 + 路径"""
        token_count = await self.token_estimator.estimate(content)
        
        if token_count <= threshold_tokens:
            return {"content": content, "artifact_path": None}
        
        # 写入 artifact 文件
        artifact_filename = f"{session_id}_{tool_name}_{int(time.time())}.txt"
        artifact_path = self.artifacts_dir / artifact_filename
        artifact_path.write_text(content, encoding="utf-8")
        
        # 截断内容 + 引用
        truncated = content[:threshold_tokens * 3]  # 粗略字符估算
        truncated += f"\n... [truncated, full content ({token_count} tokens) at {artifact_path}]"
        
        return {
            "content": truncated,
            "artifact_path": str(artifact_path),
            "full_token_count": token_count
        }
```

**Agent 读取 Artifact**:

```python
ToolDef(
    name="read_artifact",
    description="Read full content of an artifact file. "
                "Args: path (str), offset (int, default 0), "
                "limit (int, default 1000 lines).",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "offset": {"type": "integer", "default": 0},
            "limit": {"type": "integer", "default": 1000, "maximum": 10000}
        },
        "required": ["path"]
    },
    category="perception",
    safety_level="safe",
)
```

**Artifact 生命周期**:

- 存储位置:`${WORKSPACE}/.artifacts/`
- 命名规则:`{session_id}_{tool_name}_{timestamp}.txt`
- 清理策略:会话结束后 7 天自动清理(2.10 磁盘管理)。
- 不入 Postgres(文件系统存储,避免数据库膨胀)。

**与沙箱的联动**(第 6 章):

- 沙箱代码执行可生成大量输出(stdout/文件),自动走 artifact 机制。
- Agent 可用 `code_execution` 直接处理 artifact 文件(读取/分析/转换)。

`[MVP]` 截断 + artifact 写入 + read_artifact 工具实现。
`[V2]` Artifact 索引(可搜索历史 artifact);自动摘要(超长 artifact 生成摘要供 Agent 快速理解)。

---

### 5.16 工具安全机制全景

2.5 白名单 / 审计 / 资源限额的落地。

**白名单机制**:

```yaml
tools:
  whitelist:
    enabled: true
    allowed_tools:
      - "web_search"
      - "file_read"
      - "search_knowledge"
      - "code_execution"
    blocked_tools:
      - "shell_execute"  # V2 工具,提前封禁
```

- 启动时加载白名单,工具注册时校验。
- 白名单外的工具即使 MCP server 提供,也不注册到会话。

**审计日志**(联动 5.13):

- 所有工具调用入 `react_events`(`event_type="tool_call"`),含工具名、参数(脱敏)、结果摘要、耗时、重试次数。
- 失败调用入 `event_type="tool_error"`,含错误类型与堆栈摘要。
- 评估回放(第 8 章)可完整还原工具调用轨迹。

**资源限额**:

```python
class ResourceLimitManager:
    LIMITS = {
        "max_calls_per_session": 100,      # 单会话工具调用上限
        "max_total_duration_sec": 600,     # 单会话累计执行时间
        "max_concurrent_calls": 3,         # 并发调用上限(全局信号量,2.6)
        "max_artifact_size_mb": 50,        # 单 artifact 大小
    }
    
    def __init__(self):
        self.session_counters: dict[str, dict] = {}  # session_id → counters
        self.global_semaphore = asyncio.Semaphore(
            self.LIMITS["max_concurrent_calls"]  # 全局并发信号量(2.6 约定)
        )
    
    async def acquire(self, session: Session, tool_name: str) -> bool:
        """工具调用前获取资源"""
        counters = self.session_counters.setdefault(session.id, {
            "calls": 0, "duration_sec": 0
        })
        
        # 检查会话级限额
        if counters["calls"] >= self.LIMITS["max_calls_per_session"]:
            raise ResourceLimitExceeded("Session tool call limit exceeded")
        if counters["duration_sec"] >= self.LIMITS["max_total_duration_sec"]:
            raise ResourceLimitExceeded("Session duration limit exceeded")
        
        # 获取全局并发信号量(联动 2.6 全局信号量)
        await self.global_semaphore.acquire()
        counters["calls"] += 1
        return True
    
    def release(self, session: Session, duration_sec: float):
        """工具调用后释放资源"""
        self.global_semaphore.release()
        counters = self.session_counters.get(session.id)
        if counters:
            counters["duration_sec"] += duration_sec
```

**超限处理**:

- 会话级超限:WS 推送告警,Agent 收到 `ResourceLimitExceeded` 错误,应主动结束会话或换策略。
- 全局并发超限:工具调用排队等待(信号量阻塞),超时后失败。
- Artifact 大小超限:拒绝写入,Agent 收到错误,应改用分块处理。

**单会话工具并发上限**(联动 2.6 全局信号量):

- 全局并发上限 `max_concurrent_calls=3`,通过 `asyncio.Semaphore` 实现。
- 单会话内多个工具调用共享全局信号量,避免桌面端资源耗尽。
- 信号量获取顺序:先到先得(FIFO),保证公平。

`[MVP]` 白名单 + 审计日志 + 资源限额 + 全局信号量全量实现。
`[V2]` 基于用户行为的限额调整(频繁使用的工具放宽限额);工具调用配额(按工具分类独立限额)。

---

### 5.17 五类工具设计原则映射

书中第四章五类工具(感知/执行/协作/事件/沟通)在本平台的工程落地。

| 书中分类 | 本平台映射 | 设计考量 |
|---|---|---|
| **感知类** | web_search, file_read, file_list, get_time, search_knowledge | 只读不副作用,safety_level=safe,超时短(10s),结果可直接注入上下文 |
| **执行类** | file_write, http_request, code_execution, calculator | 有副作用,safety_level=elevated,需确认,超时中等(30-60s),结果可能需 artifact |
| **协作类** | MCP 工具(GitHub/Slack/Figma) | 跨进程/远程,safety_level=elevated,超时长(120s),连接管理与探活 |
| **事件类** | async_task, webhook, 流式结果 | 长时间运行,异步执行,进度推送,不阻塞 ReAct 主循环 |
| **沟通类** | WS 推送用户(tool_confirmation/progress/notification) | 非工具调用,而是工具执行过程中的用户交互 |

**每类的共性设计规则**:

- **感知类**:优先并行调用(多个只读操作可并发),提升 Agent 效率。
- **执行类**:严格顺序执行(避免副作用冲突),每次调用独立审计。
- **协作类**:连接复用(避免频繁建连),失败降级(外部服务不可用不阻断主流程)。
- **事件类**:任务状态持久化(崩溃可恢复),进度粒度可配(避免过于频繁的 WS 推送)。
- **沟通类**:非阻塞(用户响应慢不卡 Agent),超时默认行为(60s 未确认视为拒绝)。

> **MCP 2026-07-28 五类映射注记**:ToolDef 的 `parameters` 已升级为 JSON Schema 2020-12 超集(3.8),五类工具的 schema 定义均向后兼容。协作类(MCP 工具)的连接管理与探活在 MVP 保持 2025-11-25 基线,V2 切换无状态协议后连接复用语义变化(无会话、无粘性路由),但五类分类与 safety_level 映射不变。

`[MVP]` 五类工具的设计规则在工具实现中贯彻。
`[V2]` 工具分类自动识别(基于 ToolDef 的 category 字段统计);按分类的调用配额管理。

---

### 5.18 MVP 与 V2 边界

本章设计按 MVP / V2 边界落地,与 2.16/3.16/4.17 边界表对齐。

**MVP 必须实现**:

| 模块 | MVP 范围 |
|---|---|
| 双轨架构 | 内置 + MCP 统一调度(5.1-5.2) |
| MCP Client | stdio + HTTP 双传输 + 连接管理(5.3),锁定 `2025-11-25` 协议 |
| MCP 配置 | 混合配置 + UI CRUD + 双探活(5.4) + `protocol_version` 配置项 |
| 工具发现 | 按 Skill 加载 + 会话隔离(5.5) |
| 通用工具集 | 9 类工具(5.6-5.11) |
| 工具描述规范 | ToolDef JSON Schema 2020-12 超集 + `output_schema` 字段(3.8) |
| 权限确认 | 三级分级 + WS 确认 + 会话缓存(5.12) |
| 超时重试 | 分类超时 + 指数退避 + 用户中断(5.13) |
| 异步事件 | 长任务 + webhook + 流式结果(5.14) |
| Artifact | 截断 + 文件存储 + read_artifact(5.15) |
| 安全机制 | 白名单 + 审计 + 资源限额 + 信号量(5.16) |
| MCP 2026-07-28 兼容 | 超集级改动吸收(3.8/9.13)+ AuthProtocol/TasksExtensionAdapter stub 预留 |

**V2 预留接口**:

| 模块 | V2 范围 | 预留接口 |
|---|---|---|
| 工具市场 | 动态安装 MCP server | MCP 配置 UI 已支持,安装逻辑可扩展 |
| 动态权限学习 | 频繁批准的工具降级为 safe | 权限缓存可扩展为持久化学习 |
| 数据库查询 | 直接连 PostgreSQL | ToolDef schema 已定义,实现可扩展 |
| Shell 执行 | 系统命令(高风险) | dangerous 列表已支持 |
| 长任务暂停/恢复 | 任务 DAG 编排 | async_tasks 表已支持状态扩展 |
| 多版本工具 | 同工具不同版本共存 | ToolDef 已有 version 字段(3.8) |
| 工具来源标记 | 评估分析用 | ToolDef 已有 source 字段(内部标记) |
| 自适应超时 | 基于历史执行时间 | 事件日志已记录 duration_ms |
| Artifact 索引 | 可搜索历史 artifact | 文件命名规则已支持,索引可扩展 |
| MCP 双协议分发(调整一) | `2026-07-28` 无状态协议 + `server/discover` + `_meta` + 版本协商 | 5.3 注记 + config `protocol_version` 字段 |
| MCP 新 HTTP 头传输层(调整二) | `MCP-Protocol-Version`/`Mcp-Method`/`Mcp-Name` 头 + ttlMs/cacheScope + W3C Trace Context | 5.3 注记 + config `cache_ttl_ms`/`enable_server_discover` 字段 |
| MCP MRTR 多轮交互 | `InputRequiredResult` + `requestState` 重发 | 5.3 注记(替代蓝图原中间输入机制) |
| MCP Tasks 扩展迁移(调整五) | `tasks/get`/`tasks/cancel` 替代自定义异步 | 5.14 `TasksExtensionAdapter` ABC stub |
| MCP EMA/OAuth 授权(调整四) | OAuth 2.0/OIDC/EMA 替代 API Key | 5.12 `AuthProtocol` ABC stub |
| MCP SDK 升级 | v2.0.0 stable 替代 v1.x | `pyproject.toml` 锁 `mcp>=1.0,<2.0` + 监控触发 |

**与三大约束的对应**:

- 上下文质量优先 → 5.15 artifact 机制避免大结果污染上下文;5.7 Web 搜索与 4.15 KB 检索的边界明确,避免重复检索。
- 缓存友好 → 5.5 会话启动锁定工具集(不运行时变更,保护 Frozen Zone hash);5.12 权限确认缓存减少重复打扰。
- 评估驱动迭代 → 5.13 所有工具调用入 `react_events`;5.16 资源限额与审计日志支持评估分析;5.14 异步任务状态持久化可回放。

---

第 5 章起草完成。本章展开了工具层与 MCP 集成的完整设计:双轨架构(内置 + MCP)、MCP Client 实现、配置与探活、动态加载、9 类通用工具、权限分级、超时重试、异步事件、artifact 机制、安全全景、五类工具映射,共 18 节。

后续章节衔接:

- 第 6 章:展开沙箱代码执行(5.11 接口契约的实现)与文件系统工作记忆。
- 第 7 章:基于第 5 章通用工具集,设计三个场景 Skills(办公/数据分析/前端设计)。
- 第 8 章:基于 5.13 工具调用事件构建评估闭环。

---

## 第 6 章 沙箱代码执行

本章展开 5.11 `code_execution` 工具契约的实现机制。沙箱是 Agent 的"手脚",核心决策:**参考 Trae Code 的沙箱设计,但运行时不依赖 Trae 程序**,作为平台独立模块实现。MVP 采用子进程隔离模式,V2 升级为 Docker 容器。

书中第五章核心论点:文件系统是 Agent 的工作记忆。本章将此落地为沙箱工作目录的跨轮次持久化机制。

每节末尾以 `[MVP]` 或 `[V2]` 标注实现边界。

---

### 6.1 沙箱在架构中的位置与职责边界

**在架构中的位置**:

```
┌──────────────────────────────────────────────────────────┐
│  ReAct 主循环(2.4)                                       │
│    ↓ 模型输出 code_execution 工具调用                     │
│  Tool Dispatcher(5.2)                                    │
│    ↓ 权限确认(5.12,elevated)                              │
│    ↓ 超时/重试包装(5.13)                                  │
│  Sandbox Service(本章)                                   │
│    ├── 预扫描(6.8)                                        │
│    ├── 环境变量脱敏(6.8)                                  │
│    ├── 子进程执行(6.3)                                    │
│    ├── 资源限制(6.7)                                      │
│    └── 流式输出(6.10)                                     │
│    ↓ 结果                                                 │
│  Artifact Manager(5.15,大输出截断)                       │
│  Context Manager(第 3 章,结果回灌)                       │
└──────────────────────────────────────────────────────────┘
```

**职责边界**:

| 职责 | 归属 | 说明 |
|---|---|---|
| 工具契约定义 | 5.11 | ToolDef schema |
| 权限确认 | 5.12 | elevated 级别 |
| 超时/重试 | 5.13 | 分类超时 60s |
| 代码执行与隔离 | 本章 | 子进程 + 资源限制 |
| 工作目录管理 | 本章 | 会话隔离 + 生命周期 |
| 安全边界 | 本章 | 预扫描 + 路径过滤 + 环境脱敏 |
| 结果大小处理 | 5.15 | artifact 机制 |
| 结果回灌上下文 | 第 3 章 | context_manager |

**核心原则**:沙箱对工具层透明——工具层只调 `sandbox_service.execute()`,不感知沙箱内部隔离机制。

`[MVP]` 沙箱服务 + 透明接口实现。
`[V2]` 多沙箱后端(子进程/容器/远程);沙箱池化复用。

---

### 6.2 复用 Trae Code 执行能力的方式

决策 1(B):抽离 Trae 的沙箱执行逻辑,作为平台独立模块,运行时不依赖 Trae 程序。

**复用维度**:

| 维度 | Trae 的做法 | 本平台借鉴方式 |
|---|---|---|
| 进程管理 | asyncio 子进程池 | 参考:`asyncio.create_subprocess_exec` + 进程池复用 |
| 输出流式 | stdout/stderr 实时分片推送 | 参考:WS 事件流式推送协议 |
| 超时控制 | subprocess timeout + 软杀 | 参考:`asyncio.wait_for` + `process.terminate()` |
| 工作目录 | 每会话独立工作区 | 参考:`${WORKSPACE}/.sandbox/{session_id}/` |
| 安全模型 | 路径白名单 + 代码扫描 | 参考:三层兜底(6.8) |

**不依赖 Trae 运行时**:

- 平台运行时不调用 `trae` CLI 命令。
- 不要求用户安装 Trae 才能使用平台。
- 沙箱模块代码完全独立实现,仅在设计层面参考 Trae 的模式。

**与第 2 章决策的对应**(2.3 决策 A:Trae 仅开发期):开发期可用 Trae Code 辅助编写沙箱模块代码;运行期平台独立运行,沙箱是平台内置模块。

`[MVP]` 独立沙箱模块实现,设计参考 Trae 模式。
`[V2]` 可选集成 Trae 运行时(若用户已安装 Trae,优先复用其沙箱以获得更强隔离)。

---

### 6.3 沙箱隔离模型:子进程模式

决策 2(B):`subprocess` 起独立进程执行代码;V2 升级为 Docker 容器。

**子进程隔离架构**:

```python
import asyncio

class SandboxExecutor:
    """子进程隔离的代码执行器"""
    
    async def execute(
        self, code: str, language: str, timeout: int,
        workspace: str, session_id: str
    ) -> SandboxResult:
        # 1. 写入临时脚本文件
        script_path = await self._write_script(code, language, workspace)
        # 2. 构建执行命令
        cmd = self._build_command(language, script_path, workspace)
        # 3. 启动子进程(带资源限制)
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workspace,
            env=self._build_sandbox_env(session_id),  # 脱敏环境变量(6.8)
            preexec_fn=self._set_resource_limits if os.name != "nt" else None
        )
        # 4. 流式读取输出 + 超时控制
        try:
            stdout_chunks, stderr_chunks = await asyncio.wait_for(
                self._stream_output(process, session_id),
                timeout=timeout
            )
            await process.wait()
        except asyncio.TimeoutError:
            process.terminate()
            await process.wait()
            raise SandboxTimeoutError(f"Execution exceeded {timeout}s")
        
        # 5. 扫描生成的文件
        generated_files = await self._scan_generated_files(workspace)
        
        return SandboxResult(
            stdout="".join(stdout_chunks),
            stderr="".join(stderr_chunks),
            exit_code=process.returncode,
            generated_files=generated_files
        )
```

**子进程模式的隔离边界**:

| 维度 | 隔离能力 | 局限 |
|---|---|---|
| 进程 | 独立进程,崩溃不影响 Sidecar | 共享操作系统用户权限 |
| 文件系统 | 工作目录隔离(6.4) | 子进程理论上可访问全盘(靠路径过滤兜底) |
| 网络 | 环境变量禁用代理(6.7) | 应用层防护,非内核级 |
| 资源 | `resource.setrlimit`(Linux) | Windows 无原生限制,靠超时兜底 |

**V2 容器升级路径**:

```python
# V2: Docker 容器后端(预留接口)
class DockerSandboxExecutor(SandboxExecutor):
    async def execute(self, code, language, timeout, workspace, session_id):
        # docker run --rm -v workspace:/sandbox --network=none \
        #   --memory=512m --cpus=1 python:3.11 python /sandbox/script.py
        cmd = ["docker", "run", "--rm", "-v", f"{workspace}:/sandbox",
               "--network=none", "--memory=512m", "--cpus=1",
               f"{language}:latest", ...]
        # 其余流程与子进程模式一致
```

通过抽象 `SandboxExecutor` 基类,V2 可无缝切换到 Docker 后端,不影响工具层调用。

`[MVP]` 子进程隔离 + 资源限制(Linux)+ 超时兜底(Windows)实现。
`[V2]` Docker 容器后端;远程沙箱(E2B 云服务);沙箱池化复用。

---

### 6.4 沙箱工作目录与会话隔离

决策 3:每会话独立目录 + 完全隔离 + 保留 7 天。

**目录结构**:

```
${WORKSPACE}/.sandbox/
├── {session_id_1}/
│   ├── scripts/          # Agent 生成的脚本文件
│   ├── outputs/          # 执行输出文件
│   ├── artifacts/        # 自动写入的 artifact(5.15)
│   └── .env              # 沙箱专用环境变量(脱敏后)
├── {session_id_2}/
│   └── ...
└── _archive/             # 已归档的过期会话目录
```

**会话隔离规则**:

- 每个会话启动时创建独立目录 `mkdir -p ${WORKSPACE}/.sandbox/{session_id}/{scripts,outputs,artifacts}`。
- 沙箱子进程的 `cwd` 设置为该会话目录,代码默认只能读写当前目录。
- 跨会话文件访问:禁止(沙箱内代码不能 `../` 访问其他会话目录)。

**白名单只读路径**(决策 5c,6.6):

```yaml
sandbox:
  readonly_paths:
    - "${WORKSPACE}/src"           # 项目源码(只读)
    - "${WORKSPACE}/docs"          # 项目文档(只读)
    - "${WORKSPACE}/data"          # 数据文件(只读)
  writable_paths:
    - "${WORKSPACE}/.sandbox/{session_id}"  # 会话工作目录(读写)
    - "${WORKSPACE}/output"                 # 全局输出目录(读写)
```

**生命周期管理**:

```python
class WorkspaceManager:
    RETENTION_DAYS = 7
    
    async def create_session_workspace(self, session_id: str) -> str:
        workspace = Path(config.workspace_root) / ".sandbox" / session_id
        for subdir in ["scripts", "outputs", "artifacts"]:
            (workspace / subdir).mkdir(parents=True, exist_ok=True)
        return str(workspace)
    
    async def cleanup_expired(self):
        """定期清理过期会话目录(2.10 磁盘管理调度)"""
        sandbox_root = Path(config.workspace_root) / ".sandbox"
        cutoff = datetime.now() - timedelta(days=self.RETENTION_DAYS)
        for session_dir in sandbox_root.iterdir():
            if session_dir.name.startswith("_"):
                continue  # 跳过 _archive 等特殊目录
            mtime = datetime.fromtimestamp(session_dir.stat().st_mtime)
            if mtime < cutoff:
                # 移动到 _archive 而非直接删除(支持恢复)
                archive_dir = sandbox_root / "_archive" / session_dir.name
                session_dir.rename(archive_dir)
```

**与 5.8 文件工具的互通**(决策 9):

- 沙箱工作目录纳入 5.8 `file_read`/`file_write` 的 `allowed_paths` 白名单。
- Agent 可用 `file_read` 读取沙箱生成的文件,或用 `file_write` 预置脚本到沙箱目录。
- 路径校验复用 5.8 的 `_validate_path` 逻辑。

`[MVP]` 会话独立目录 + 隔离规则 + 7 天保留 + 白名单互通实现。
`[V2]` 工作目录加密(防止磁盘取证泄露);跨设备工作区同步;沙箱快照恢复。

---

### 6.5 支持的语言与执行后端

决策 4:Python + JavaScript(MVP);Shell/Rust/Go(V2)。

**语言配置**:

```yaml
sandbox:
  languages:
    python:
      command: "python"
      script_extension: ".py"
      version_check: "python --version"
      min_version: "3.10"
    javascript:
      command: "node"
      script_extension: ".js"
      version_check: "node --version"
      min_version: "18.0"
    # V2 预留
    shell:
      command: "bash"
      script_extension: ".sh"
    rust:
      command: "rustc"
      script_extension: ".rs"
```

**环境检测与版本管理**:

```python
class LanguageManager:
    async def detect_runtime(self, language: str) -> bool:
        """启动时检测语言运行时是否可用"""
        lang_config = config.sandbox.languages[language]
        try:
            result = await asyncio.create_subprocess_exec(
                *lang_config.version_check.split(),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await asyncio.wait_for(result.wait(), timeout=5)
            version_output = (await result.stdout.read()).decode().strip()
            return self._check_version(version_output, lang_config.min_version)
        except (FileNotFoundError, asyncio.TimeoutError):
            return False
    
    def _build_command(self, language: str, script_path: str, 
                       workspace: str) -> list[str]:
        lang_config = config.sandbox.languages[language]
        return [lang_config.command, script_path]
```

**启动时探活**:Sidecar 启动时检测所有配置的语言运行时,不可用的语言标记为 `unavailable`。Agent 调用 `code_execution` 时指定不可用语言,返回明确错误提示。

`[MVP]` Python + JavaScript 双语言 + 环境检测实现。
`[V2]` Shell/Rust/Go 支持;多版本 Python 共存(pyenv 集成);语言插件机制。

---

### 6.6 文件系统作为工作记忆

决策 5(全选):对应书中第五章"Harvey/Memo"模式。

**工作记忆模型**:

```
Agent 的"桌面" = 沙箱工作目录
├── 跨轮次持久:同一会话内,Agent 写的文件下一轮可读
├── artifact 自动写入:工具大输出自动存入 artifacts/(5.15)
├── 白名单只读:可读取项目源码/数据(6.4)
└── 输出目录可写:可写入全局 output/ 供用户查看
```

**跨轮次持久化**:

```python
class SandboxService:
    async def execute(self, code, language, timeout, session_id):
        # 复用会话工作目录(非每次新建)
        workspace = await self.workspace_manager.get_or_create(session_id)
        # 执行代码,工作目录内的文件跨轮次保留
        result = await self.executor.execute(
            code, language, timeout, workspace, session_id
        )
        return result
```

**Agent 典型工作流**(多轮代码执行):

```
轮次 1: Agent 调用 code_execution 写入数据处理脚本
  → scripts/process_data.py 生成
  → 工作目录保留

轮次 2: Agent 调用 code_execution 运行脚本
  → python scripts/process_data.py
  → outputs/result.json 生成
  → 工作目录保留

轮次 3: Agent 调用 code_execution 分析结果
  → python -c "import json; data=json.load(open('outputs/result.json')); ..."
  → 读取轮次 2 生成的文件

轮次 4: Agent 调用 file_read 读取分析结果
  → 5.8 文件工具,读取沙箱工作目录内的文件
  → 结果回灌上下文
```

**与书中理论的对应**:

- **Harvey 模式**:文件系统作为长期记忆,Agent 在文件中积累上下文(如 `.sandbox/{session_id}/notes.md`)。
- **Memo 模式**:Agent 主动用文件记录中间结果,避免上下文窗口溢出。
- 本平台落地:沙箱工作目录天然支持这两种模式,Agent 可自由读写文件作为外部记忆。

`[MVP]` 跨轮次持久 + artifact 自动写入 + 白名单只读 + 输出目录可写全量实现。
`[V2]` 工作记忆结构化(自动索引沙箱文件);跨会话工作记忆继承(可选)。

---

### 6.7 沙箱资源限制

决策 6:CPU 300s 超时 / 内存 512MB / 磁盘 100MB / 网络禁止。

**资源限制配置**:

```yaml
sandbox:
  limits:
    cpu_timeout_sec: 300          # 最大执行时间
    memory_limit_mb: 512          # 内存上限
    disk_limit_mb: 100            # 工作目录磁盘上限
    network_enabled: false        # 禁止网络访问
```

**跨平台实现**:

```python
import resource
import os

class ResourceLimiter:
    def __init__(self, config):
        self.memory_limit = config.sandbox.limits.memory_limit_mb * 1024 * 1024
        self.cpu_timeout = config.sandbox.limits.cpu_timeout_sec
    
    def apply_to_process(self):
        """在子进程启动前应用资源限制(preexec_fn)"""
        if os.name == "nt":
            # Windows 无 setrlimit,仅依赖超时兜底
            return
        # Linux/macOS:设置内存限制
        resource.setrlimit(
            resource.RLIMIT_AS,
            (self.memory_limit, self.memory_limit)
        )
        # CPU 时间限制(秒)
        resource.setrlimit(
            resource.RLIMIT_CPU,
            (self.cpu_timeout, self.cpu_timeout)
        )
    
    async def check_disk_usage(self, workspace: str) -> bool:
        """检查工作目录磁盘使用量"""
        total_size = 0
        for dirpath, _, filenames in os.walk(workspace):
            for f in filenames:
                fp = os.path.join(dirpath, f)
                total_size += os.path.getsize(fp)
        limit = config.sandbox.limits.disk_limit_mb * 1024 * 1024
        return total_size < limit
```

**网络访问禁止**:

```python
def _build_sandbox_env(self, session_id: str) -> dict:
    """构建沙箱环境变量(脱敏 + 禁网络)"""
    # 1. 环境变量脱敏(6.8)
    env = self._sanitize_env(os.environ)
    # 2. 禁用网络代理(应用层防护)
    env["HTTP_PROXY"] = "invalid"
    env["HTTPS_PROXY"] = "invalid"
    env["NO_PROXY"] = "*"
    env["http_proxy"] = "invalid"
    env["https_proxy"] = "invalid"
    # 3. 限制 Python 网络访问(通过环境变量提示,非强制)
    env["PYTHONHTTPSVERIFY"] = "0"  # 不影响,仅提示
    return env
```

**网络隔离的局限**:子进程模式下,网络禁止是应用层防护(代理环境变量),Agent 代码理论上仍可发起原始 socket 连接。V2 的 Docker 容器模式可通过 `--network=none` 实现内核级网络隔离。

**资源超限处理**:

- CPU 超时:`asyncio.wait_for` 捕获 `TimeoutError`,`process.terminate()` 终止子进程。
- 内存超限(Linux):`RLIMIT_AS` 触发 `MemoryError`,子进程崩溃,返回 stderr。
- 磁盘超限:执行前检查工作目录大小,超限拒绝执行并提示 Agent 清理。
- 网络访问:代码尝试联网时超时或失败(代理无效),Agent 收到错误。

`[MVP]` 超时 + 内存限制(Linux)+ 磁盘检查 + 网络代理禁用实现。
`[V2]` Docker `--network=none` 内核级网络隔离;Windows 内存限制(Job Object API);CPU 使用率监控(非仅时间限制)。

---

### 6.8 沙箱安全边界

决策 7:路径过滤 + 危险代码预扫描 + 无内核 seccomp + 环境变量脱敏(缺口补充)。

**三层兜底架构**:

```
代码执行请求
  ↓
第 1 层:危险代码预扫描(静态正则匹配)
  ↓ 通过
第 2 层:路径白名单过滤(运行时路径校验)
  ↓ 通过
第 3 层:进程资源限制(6.7,内存/超时/网络)
  ↓
子进程执行
```

**第 1 层:危险代码预扫描**:

```python
class CodeScanner:
    DANGEROUS_PATTERNS = [
        # 系统命令执行
        r"os\.system\s*\(",
        r"subprocess\.(call|run|Popen|check_output)\s*\(",
        r"pty\.spawn\s*\(",
        r"commands\.(getstatusoutput|getoutput)\s*\(",
        # 文件系统危险操作
        r"shutil\.rmtree\s*\(",
        r"os\.remove\s*\(.*/\*",
        r"os\.unlink\s*\(",
        # 网络监听
        r"socket\.socket\s*\(\s*socket\.AF_INET.*SOCK_STREAM",
        r"socket\.listen\s*\(",
        # 进程操作
        r"os\.kill\s*\(",
        r"os\.fork\s*\(",
        # eval/exec(代码注入风险)
        r"\beval\s*\(",
        r"\bexec\s*\(",
    ]
    
    def scan(self, code: str) -> list[CodeWarning]:
        """预扫描代码,返回告警列表(不阻断)"""
        warnings = []
        for pattern in self.DANGEROUS_PATTERNS:
            matches = re.finditer(pattern, code)
            for match in matches:
                warnings.append(CodeWarning(
                    pattern=pattern,
                    line=code[:match.start()].count("\n") + 1,
                    snippet=match.group()
                ))
        return warnings
```

**预扫描行为**:告警不阻断——记录告警入 `react_events`,Agent 仍可执行代码。理由:MVP 不依赖内核级隔离,预扫描作为"软提示",帮助评估分析 Agent 的代码质量;阻断会导致合法数据分析代码(如 `subprocess.run(["python", "helper.py"])`)被误杀。

**第 2 层:路径白名单过滤**:

```python
class PathFilter:
    def __init__(self, workspace: str, readonly_paths: list[str], 
                 writable_paths: list[str]):
        self.workspace = Path(workspace).resolve()
        self.readonly = [Path(p).resolve() for p in readonly_paths]
        self.writable = [Path(p).resolve() for p in writable_paths]
    
    def validate_file_access(self, requested_path: str, write: bool) -> bool:
        """校验代码访问的路径是否在白名单内"""
        target = Path(requested_path).resolve()
        if write:
            return any(self._is_subpath(target, p) for p in self.writable)
        else:
            return (any(self._is_subpath(target, p) for p in self.readonly)
                    or any(self._is_subpath(target, p) for p in self.writable))
```

**路径过滤的局限**:子进程模式下,路径过滤是应用层校验(在代码执行前检查代码中硬编码的路径),无法拦截运行时动态生成的路径(如 `os.path.join("..", "..", "etc", "passwd")`)。真正的路径隔离需 V2 的容器挂载。

**环境变量脱敏**(缺口补充):

```python
class EnvSanitizer:
    SENSITIVE_PATTERNS = [
        "KEY", "SECRET", "TOKEN", "PASSWORD", "PASSWD",
        "CREDENTIAL", "AUTH", "API_KEY", "PRIVATE_KEY",
        "DATABASE_URL", "DB_PASSWORD", "CONNECTION_STRING"
    ]
    
    def sanitize(self, env: dict) -> dict:
        """过滤敏感环境变量,防止 Agent 代码读取本地凭证"""
        sanitized = {}
        for key, value in env.items():
            if self._is_sensitive(key):
                logger.debug(f"Sanitized env var: {key}")
                continue  # 不传递到沙箱环境
            sanitized[key] = value
        # 保留必要的基础变量
        sanitized["PATH"] = env.get("PATH", "")
        sanitized["HOME"] = env.get("HOME", "")
        sanitized["USER"] = env.get("USER", "")
        sanitized["LANG"] = env.get("LANG", "en_US.UTF-8")
        return sanitized
    
    def _is_sensitive(self, key: str) -> bool:
        key_upper = key.upper()
        return any(pattern in key_upper for pattern in self.SENSITIVE_PATTERNS)
```

**脱敏执行时机**:子进程启动前,`_build_sandbox_env` 调用 `EnvSanitizer.sanitize()`,确保 Agent 代码无法通过 `os.environ` 读取 API Key、数据库连接串等敏感凭证。

**第 3 层:进程资源限制**:见 6.7,内存/超时/网络限制作为最终兜底。

**跨平台约束**:

| 平台 | 第 1 层(预扫描) | 第 2 层(路径过滤) | 第 3 层(资源限制) |
|---|---|---|---|
| Linux | 应用层正则 | 应用层校验 | 内核级(`setrlimit`) |
| macOS | 应用层正则 | 应用层校验 | 内核级(`setrlimit`) |
| Windows | 应用层正则 | 应用层校验 | 仅超时(无 `setrlimit`) |

不依赖 Linux 独有的 seccomp/apparmor,所有防护均应用层实现,保证跨平台一致。

`[MVP]` 三层兜底 + 环境变量脱敏全量实现。
`[V2]` Docker 容器内核级隔离(seccomp/apparmor);运行时路径拦截(LD_PRELOAD);代码沙箱化(RestrictedPython)。

---

### 6.9 沙箱执行接口与调用流程

`code_execution` 工具的完整执行流程。

**端到端流程**:

```python
class SandboxService:
    """沙箱服务,对接 5.11 code_execution 工具"""
    
    async def execute(self, code: str, language: str, 
                      timeout: int, session_id: str) -> dict:
        # 1. 获取/创建会话工作目录
        workspace = await self.workspace_manager.get_or_create(session_id)
        
        # 2. 预扫描代码(6.8 第 1 层)
        warnings = self.scanner.scan(code)
        if warnings:
            await self._log_warnings(session_id, warnings)
        
        # 3. 环境变量脱敏(6.8)
        sandbox_env = self.env_sanitizer.sanitize(os.environ)
        sandbox_env = self._disable_network(sandbox_env)
        
        # 4. 检查工作目录磁盘配额(6.7)
        if not await self.resource_limiter.check_disk_usage(workspace):
            raise SandboxResourceError("Workspace disk quota exceeded")
        
        # 5. 写入脚本文件
        script_path = await self._write_script(code, language, workspace)
        
        # 6. 构建命令并启动子进程
        cmd = self.language_manager._build_command(language, script_path, workspace)
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workspace,
            env=sandbox_env,
            preexec_fn=self.resource_limiter.apply_to_process if os.name != "nt" else None
        )
        
        # 7. 流式读取输出 + 超时控制(6.10)
        try:
            stdout_data, stderr_data = await asyncio.wait_for(
                self._stream_output(process, session_id),
                timeout=timeout
            )
            await process.wait()
        except asyncio.TimeoutError:
            process.terminate()
            await process.wait()
            raise SandboxTimeoutError(f"Execution exceeded {timeout}s")
        
        # 8. 扫描生成的文件(6.10)
        generated_files = await self._scan_generated_files(workspace)
        
        # 9. 记录执行事件(6.13)
        await self._log_execution(session_id, code, language, 
                                   stdout_data, stderr_data, 
                                   process.returncode, generated_files)
        
        # 10. 返回结果(5.11 契约)
        return {
            "stdout": stdout_data,
            "stderr": stderr_data,
            "exit_code": process.returncode,
            "files": generated_files
        }
```

**与 5.11 接口契约的对齐**:

| 5.11 契约字段 | 本章实现 |
|---|---|
| `code` | 写入临时脚本文件 |
| `language` | 路由到对应语言执行后端(6.5) |
| `timeout` | `asyncio.wait_for` 控制 |
| 返回 `stdout/stderr/exit_code/files` | 流式收集 + 文件扫描 |

**错误处理映射**(对齐 2.3 错误码体系):

| 沙箱错误 | 错误码 | 处理 |
|---|---|---|
| 超时 | 501 | 终止进程,返回部分输出 |
| 内存超限(Linux) | 502 | 子进程崩溃,返回 stderr |
| 磁盘超限 | 503 | 执行前拒绝 |
| 语言不可用 | 504 | 启动时检测,调用时返回错误 |
| 代码执行错误 | 200 | 正常返回 stderr + exit_code |

`[MVP]` 端到端流程实现。
`[V2]` 执行计划优化(代码 AST 分析预分配资源);并行代码执行;沙箱预热。

---

### 6.10 流式输出与文件列表

决策 8:stdout/stderr 实时流式推送 + 文件列表扫描 + artifact 衔接。

**流式输出实现**:

```python
class SandboxService:
    async def _stream_output(self, process: asyncio.subprocess.Process,
                              session_id: str) -> tuple[str, str]:
        """流式读取 stdout/stderr,实时推送 WS"""
        stdout_chunks = []
        stderr_chunks = []
        
        async def read_stream(stream, chunks, stream_type: str):
            while True:
                chunk = await stream.read(4096)  # 4KB 分片
                if not chunk:
                    break
                text = chunk.decode("utf-8", errors="replace")
                chunks.append(text)
                # WS 推送流式输出(2.3 数据面)
                await self.ws_manager.send(session_id, WSEvent(
                    type="sandbox_output",
                    data={
                        "stream": stream_type,  # stdout / stderr
                        "chunk": text,
                        "timestamp": datetime.now().isoformat()
                    }
                ))
        
        # 并行读取 stdout 和 stderr
        await asyncio.gather(
            read_stream(process.stdout, stdout_chunks, "stdout"),
            read_stream(process.stderr, stderr_chunks, "stderr")
        )
        
        return "".join(stdout_chunks), "".join(stderr_chunks)
```

**文件列表扫描**:

```python
async def _scan_generated_files(self, workspace: str) -> list[str]:
    """扫描工作目录,返回本次执行新增/修改的文件"""
    workspace_path = Path(workspace)
    files = []
    for subdir in ["outputs", "artifacts", "scripts"]:
        dir_path = workspace_path / subdir
        if dir_path.exists():
            for f in dir_path.iterdir():
                if f.is_file():
                    files.append(str(f.relative_to(workspace_path)))
    return files
```

**大输出 artifact 衔接**(联动 5.15):

```python
async def execute(self, code, language, timeout, session_id):
    result = await self._execute_internal(...)
    
    # stdout 超过 2k token(沙箱专用阈值,3.12),走 artifact
    token_count = await self.token_estimator.estimate(result["stdout"])
    if token_count > 2000:
        artifact = await self.artifact_manager.maybe_create_artifact(
            content=result["stdout"],
            session_id=session_id,
            tool_name="code_execution",
            threshold_tokens=2000  # 沙箱更严格
        )
        result["stdout"] = artifact["content"]
        if artifact["artifact_path"]:
            result["artifact_path"] = artifact["artifact_path"]
    
    return result
```

**流式输出的 UI 体验**:

- WS 事件 `sandbox_output` 实时推送,前端可展示"代码运行中"的终端效果。
- 执行完成后,完整 stdout/stderr 通过 tool_result 返回(经 artifact 截断)。
- 前端可同时展示流式输出(实时)和最终结果(截断后)。

`[MVP]` 流式输出 + 文件列表 + 2k artifact 截断实现。
`[V2]` 输出过滤(高亮错误行);交互式终端(支持运行时输入);多文件 diff 展示。

---

### 6.11 沙箱与工具层的协作

决策 9:串行调用复用工作目录 + 文件工具互通。

**串行调用复用目录**:

同一会话内,Agent 多次调用 `code_execution` 共享同一工作目录。文件跨轮次持久(6.6),Agent 可:

```
轮次 1: code_execution → 写入 scripts/process.py
轮次 2: code_execution → 运行 python scripts/process.py,生成 outputs/result.json
轮次 3: code_execution → 读取 outputs/result.json 并分析
轮次 4: file_read → 读取沙箱内的 outputs/result.json(互通)
```

**文件工具互通**:

```python
# 5.8 FileTool 的白名单包含沙箱工作目录
class FileTool:
    def __init__(self, workspace_root, sandbox_root):
        self.allowed_paths = [
            workspace_root / "data",
            workspace_root / "output",
            # 沙箱工作目录(动态添加,按会话)
            sandbox_root / "{session_id}"  # 模式匹配
        ]
    
    def _validate_path(self, path, session_id):
        # 沙箱路径校验:只允许访问当前会话的沙箱目录
        sandbox_path = self.sandbox_root / session_id
        if self._is_subpath(path, sandbox_path):
            return True
        # 其他白名单路径...
```

**Agent 工作流示例**(数据分析场景):

```
用户: "分析这份销售数据,生成图表"

Agent 行为:
1. file_read("data/sales.csv") → 读取数据文件(白名单只读)
2. code_execution(写脚本) → scripts/analyze.py 生成
3. code_execution(运行脚本) → python scripts/analyze.py
   → outputs/chart.png 生成
   → stdout 输出分析摘要
4. file_read("outputs/chart.png") → 读取生成的图表
5. 最终回复用户,附带图表路径和分析摘要
```

**协作约束**:

- 沙箱工作目录的文件,Agent 可通过 `file_read` 读取,但写入必须通过 `code_execution` 或 `file_write`(后者受 elevated 权限确认)。
- 跨会话文件访问:禁止。会话 A 的沙箱文件,会话 B 的 `file_read` 不可访问(路径校验按 session_id 隔离)。

`[MVP]` 串行复用 + 文件工具互通 + 跨会话隔离实现。
`[V2]` 沙箱文件版本管理(类似 git);文件变更监听通知 Agent。

---

### 6.12 沙箱失败恢复

决策 10:子进程崩溃保留工作目录文件;快照 V2 预留。

**崩溃场景与恢复**:

| 崩溃场景 | 文件保留 | Agent 恢复策略 |
|---|---|---|
| 代码执行错误(exit_code != 0) | ✓ 已落盘文件保留 | Agent 读取 stderr,修正代码重试 |
| 超时终止 | ✓ 已落盘文件保留 | Agent 读取部分输出,优化代码或换策略 |
| 内存超限(Linux OOM) | ✓ 已落盘文件保留 | Agent 读取已生成文件,减少内存使用 |
| 子进程异常退出(信号杀) | ✓ 已落盘文件保留 | Agent 收到错误,检查工作目录文件 |
| Sidecar 崩溃 | ✓ 文件在磁盘 | 重启后 Agent 可继续读取沙箱文件 |

**恢复机制**:

```python
class SandboxService:
    async def execute(self, code, language, timeout, session_id):
        workspace = await self.workspace_manager.get_or_create(session_id)
        try:
            result = await self._execute_internal(...)
            return result
        except (SandboxTimeoutError, SandboxResourceError) as e:
            # 崩溃后扫描已生成的文件
            existing_files = await self._scan_generated_files(workspace)
            return {
                "stdout": "",
                "stderr": f"Execution failed: {str(e)}",
                "exit_code": -1,
                "files": existing_files,  # 返回已生成的文件
                "error": str(e)
            }
```

**Agent 自愈工作流**:

```
Agent 调用 code_execution → 超时崩溃
  ↓ 收到错误 + 已生成文件列表
Agent: "执行超时,但发现已生成 outputs/partial_result.json"
  ↓ file_read("outputs/partial_result.json")
Agent: "部分结果可用,基于此继续分析"
  ↓ 调整代码,减少计算量
Agent: code_execution(优化后的代码) → 成功
```

**V2 快照预留**:

```python
# V2: 沙箱环境快照(预留接口)
class SnapshotManager:
    async def create_snapshot(self, session_id: str) -> str:
        """保存沙箱环境状态(文件 + 已安装依赖)"""
        # 打包工作目录 + Python 虚拟环境
        snapshot_path = f"{workspace}/_snapshots/{timestamp}.tar.gz"
        # tar -czf snapshot_path -C workspace .
        return snapshot_path
    
    async def restore_snapshot(self, snapshot_path: str, session_id: str):
        """从快照恢复沙箱环境"""
        # tar -xzf snapshot_path -C workspace
```

`[MVP]` 崩溃保留文件 + 返回已生成文件列表实现。
`[V2]` 环境快照保存与恢复;依赖缓存(pip install 结果持久化)。

---

### 6.13 沙箱事件记录与评估支持

所有沙箱执行入 `react_events`,支持评估回放(第 8 章)。

**事件记录**:

```python
class SandboxService:
    async def _log_execution(self, session_id, code, language,
                              stdout, stderr, exit_code, generated_files,
                              warnings=None):
        await self.react_events_repo.insert(
            session_id=session_id,
            turn=session.current_turn,
            event_type="sandbox_execution",
            payload={
                "language": language,
                "code": code[:4000],  # 代码截断,完整代码存 artifact
                "code_artifact_path": await self._maybe_save_code_artifact(code, session_id),
                "stdout_length": len(stdout),
                "stderr_length": len(stderr),
                "exit_code": exit_code,
                "generated_files": generated_files,
                "warnings": [w.to_dict() for w in (warnings or [])],
                "duration_ms": ...,
                "timestamp": datetime.now().isoformat()
            }
        )
```

**评估回放支持**:

- 完整代码:超长代码存 artifact,`react_events` 记录 artifact 路径。
- 执行结果:stdout/stderr 摘要(长度 + 前 500 字符),完整输出存 artifact。
- 文件变更:记录生成文件列表,评估可还原文件系统状态。
- 告警信息:预扫描告警记录,评估分析 Agent 代码质量。

**与第 8 章评估闭环的衔接**:

- 评估数据集可包含"代码执行任务",对比预期输出与实际输出。
- 评估可统计 Agent 的代码执行成功率、平均耗时、常见错误类型。
- 告警统计:Agent 是否频繁使用危险 API(如 `os.system`)。

`[MVP]` 事件记录 + artifact 溢出存储实现。
`[V2]` 执行轨迹可视化(代码 → 输出 → 文件变更时间线);自动回归测试(历史执行重放)。

---

### 6.14 沙箱配置管理

决策 14:静态 yaml + 运行时 runtime 可改。

**config.yaml 沙箱配置段**:

```yaml
sandbox:
  enabled: true
  workspace_root: "${WORKSPACE}/.sandbox"
  retention_days: 7
  
  languages:
    python:
      command: "python"
      script_extension: ".py"
      min_version: "3.10"
    javascript:
      command: "node"
      script_extension: ".js"
      min_version: "18.0"
  
  limits:
    cpu_timeout_sec: 300
    memory_limit_mb: 512
    disk_limit_mb: 100
    network_enabled: false
  
  security:
    code_scan_enabled: true
    env_sanitization_enabled: true
    dangerous_patterns:
      - "os.system"
      - "subprocess"
      - "shutil.rmtree"
    sensitive_env_patterns:
      - "KEY"
      - "SECRET"
      - "TOKEN"
      - "PASSWORD"
  
  paths:
    readonly:
      - "${WORKSPACE}/src"
      - "${WORKSPACE}/docs"
      - "${WORKSPACE}/data"
    writable:
      - "${WORKSPACE}/.sandbox/{session_id}"
      - "${WORKSPACE}/output"
  
  output:
    stream_chunk_size: 4096       # 流式输出分片大小
    stdout_artifact_threshold: 2000  # stdout artifact 截断阈值(token)
    code_artifact_threshold: 4000    # 代码 artifact 截断阈值
```

**运行时配置**(config_runtime 表):

```json
{
  "sandbox.limits.memory_limit_mb": 1024,
  "sandbox.limits.cpu_timeout_sec": 600,
  "sandbox.security.code_scan_enabled": false
}
```

加载优先级(2.14):`config_runtime` > `config.yaml` > 默认。

**UI 配置面板**:HTTP API 提供沙箱配置 CRUD:

- `GET /api/sandbox/config` 读取配置
- `PUT /api/sandbox/config` 修改运行时配置
- `POST /api/sandbox/test` 测试沙箱执行(运行示例代码验证配置)

`[MVP]` 完整配置段 + 运行时覆盖 + UI 配置实现。
`[V2]` 配置预设(开发/生产/安全模式);配置变更审计日志。

---

### 6.15 跨平台兼容性

Windows/macOS/Linux 差异化处理。

**进程启动方式**:

```python
class ProcessManager:
    def get_start_method(self) -> str:
        """获取子进程启动方式(2.2 跨平台备注)"""
        if os.name == "nt":
            return "spawn"  # Windows 必须用 spawn
        else:
            return "fork"   # Linux/macOS 默认 fork,启动快
    
    async def create_process(self, cmd, cwd, env):
        # asyncio.create_subprocess_exec 已自动处理跨平台
        return await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env,
            preexec_fn=self._linux_resource_limits if os.name != "nt" else None
        )
```

**资源限制能力差异**:

| 能力 | Linux | macOS | Windows |
|---|---|---|---|
| 内存限制(`RLIMIT_AS`) | ✓ | ✓ | ✗(仅超时兜底) |
| CPU 时间限制(`RLIMIT_CPU`) | ✓ | ✓ | ✗ |
| 磁盘配额 | 应用层检查 | 应用层检查 | 应用层检查 |
| 网络隔离 | 应用层(代理禁用) | 应用层 | 应用层 |
| 进程树杀 | `kill -9 -pid` | `kill -9 -pid` | `taskkill /T /PID` |

**Windows 资源限制兜底**:

```python
def _windows_resource_management(self, process, timeout, memory_mb):
    """Windows 下的资源管理(仅超时 + 内存监控)"""
    # 启动监控任务,超时杀进程
    async def monitor():
        start = time.time()
        while True:
            if time.time() - start > timeout:
                process.terminate()
                break
            # 内存监控(仅告警,不强制)
            # Windows 无原生 RLIMIT,仅通过 psutil 监控
            try:
                import psutil
                p = psutil.Process(process.pid)
                mem = p.memory_info().rss / 1024 / 1024
                if mem > memory_mb:
                    logger.warning(f"Process memory {mem}MB > limit {memory_mb}MB")
            except:
                pass
            await asyncio.sleep(1)
    
    asyncio.create_task(monitor())
```

**路径分隔符处理**:

```python
class PathHandler:
    @staticmethod
    def normalize(path: str) -> str:
        """统一路径分隔符"""
        return str(Path(path))  # pathlib 自动处理跨平台
    
    @staticmethod
    def join(*parts) -> str:
        return str(Path(*parts))
```

**Python/Node 安装检测**:

```python
class RuntimeDetector:
    async def detect_all(self) -> dict:
        """启动时检测所有语言运行时"""
        results = {}
        for lang in config.sandbox.languages:
            available = await self._check_runtime(lang)
            results[lang] = {
                "available": available,
                "version": await self._get_version(lang) if available else None,
                "path": await self._get_path(lang) if available else None
            }
        return results
    
    async def _check_runtime(self, language: str) -> bool:
        cmd = config.sandbox.languages[language].command
        try:
            result = await asyncio.create_subprocess_exec(
                cmd, "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await asyncio.wait_for(result.wait(), timeout=5)
            return result.returncode == 0
        except (FileNotFoundError, asyncio.TimeoutError):
            return False
```

**Worker 进程与沙箱子进程的关系**(联动 2.2):

- Worker 进程(2.2)用于 CPU 密集计算(embedding/reranker),不执行用户代码。
- 沙箱子进程独立于 Worker,由 SandboxService 直接管理。
- 两者不共享进程池,避免用户代码影响 AI 推理任务的稳定性。

`[MVP]` 跨平台进程管理 + Windows 兜底 + 运行时检测实现。
`[V2]` Windows Job Object API 内存限制;macOS sandbox-exec 强化隔离;语言运行时自动安装。

---

### 6.16 MVP 与 V2 边界

本章设计按 MVP / V2 边界落地,与 2.16/3.16/4.17/5.18 边界表对齐。

**MVP 必须实现**:

| 模块 | MVP 范围 |
|---|---|
| 沙箱架构 | 独立模块 + 透明接口(6.1) |
| Trae 复用 | 设计参考,运行时独立(6.2) |
| 隔离模型 | 子进程模式(6.3) |
| 工作目录 | 会话隔离 + 7 天保留 + 白名单互通(6.4) |
| 语言支持 | Python + JavaScript(6.5) |
| 文件工作记忆 | 跨轮次持久 + artifact + 只读/可写(6.6) |
| 资源限制 | 超时 300s + 内存 512MB + 磁盘 100MB + 禁网络(6.7) |
| 安全边界 | 三层兜底 + 环境变量脱敏(6.8) |
| 执行流程 | 端到端 + 错误码映射(6.9) |
| 流式输出 | stdout/stderr 流式 + 文件列表 + 2k artifact(6.10) |
| 工具协作 | 串行复用 + 文件互通(6.11) |
| 失败恢复 | 崩溃保留文件 + 返回已生成文件(6.12) |
| 事件记录 | react_events + artifact 溢出(6.13) |
| 配置管理 | yaml + runtime + UI(6.14) |
| 跨平台 | Windows/macOS/Linux 差异化处理(6.15) |

**V2 预留接口**:

| 模块 | V2 范围 | 预留接口 |
|---|---|---|
| 容器隔离 | Docker 后端 | `SandboxExecutor` 抽象基类,可切换实现 |
| 远程沙箱 | E2B 云服务 | 接口已抽象,可扩展远程后端 |
| 多语言 | Shell/Rust/Go | `languages` 配置可扩展 |
| 沙箱池化 | 进程/容器复用 | 执行器接口支持池化 |
| 内核级隔离 | seccomp/apparmor | Linux 增强安全模块 |
| 运行时路径拦截 | LD_PRELOAD | 动态链接库注入 |
| 代码沙箱化 | RestrictedPython | Python AST 改写 |
| 环境快照 | 保存/恢复 | `SnapshotManager` 接口预留 |
| 依赖缓存 | pip install 持久化 | 工作目录支持虚拟环境 |
| 交互式终端 | 运行时输入 | 流式协议可扩展双向 |
| Windows 内存限制 | Job Object API | 资源限制接口可扩展 |
| 文件版本管理 | git-like | 工作目录可扩展版本控制 |
| 执行轨迹可视化 | 时间线 | 事件已记录,可视化可扩展 |

**与三大约束的对应**:

- 上下文质量优先 → 6.10 stdout 超 2k token 自动走 artifact(沙箱专用阈值,比 3.12 的 4k 更严格);6.6 文件系统工作记忆避免大输出污染上下文窗口。
- 缓存友好 → 6.4 会话工作目录跨轮次持久(不重建,文件作为外部记忆);6.11 串行调用复用目录(不破坏会话状态)。
- 评估驱动迭代 → 6.13 所有沙箱执行入 `react_events`,含代码/输出/文件/告警;6.8 预扫描告警支持代码质量评估;6.12 崩溃恢复记录支持失败分析。

---

第 6 章起草完成。本章展开了沙箱代码执行的完整设计:Trae 设计复用、子进程隔离、会话工作目录、多语言支持、文件工作记忆、资源限制、三层安全边界、流式输出、工具协作、失败恢复、事件记录、配置管理、跨平台兼容,共 16 节。

后续章节衔接:

- 第 7 章:基于第 5 章通用工具集 + 第 6 章沙箱,设计三个场景 Skills(办公/数据分析/前端设计)。
- 第 8 章:基于 6.13 沙箱执行事件 + 5.13 工具调用事件构建评估闭环。

---

## 第 7 章 场景 Skills 设计

本章基于第 5 章通用工具集 + 第 6 章沙箱,设计三个场景 Skills 的具体实现:办公、数据分析、前端设计。开发优先级:办公 → 数据分析 → 前端设计(决策 3,贴合首批落地需求)。

核心设计原则:
- Skills 共享 5.6 通用工具集,场景仅定制 Prompt + 工具白名单(决策 7)
- 每个 Skill 独立专用 Prompt,参考书中第三章四段式框架(决策 8)
- 会话创建时锁定 Skill,运行中不支持切换(避免 Frozen Zone hash 失效)

每节末尾以 `[MVP]` 或 `[V2]` 标注实现边界。

---

### 7.1 Skill 的定义格式与目录结构

决策 1(C):独立目录结构,每个 Skill 一个完整目录。

**目录结构**:

```
${WORKSPACE}/skills/
├── office/                          # 办公场景 Skill
│   ├── skill.yaml                   # 元数据
│   ├── system_prompt.md             # 专用提示词
│   ├── tools.yaml                   # 工具白名单
│   ├── examples/                    # 少样本示例
│   │   ├── excel_summary.md
│   │   └── web_research.md
│   └── assets/                      # 静态资源(模板文件等)
│       └── report_template.docx
├── data_analysis/                   # 数据分析场景 Skill
│   ├── skill.yaml
│   ├── system_prompt.md
│   ├── tools.yaml
│   └── examples/
│       ├── sales_analysis.md
│       └── csv_cleanup.md
└── frontend_design/                 # 前端设计场景 Skill
    ├── skill.yaml
    ├── system_prompt.md
    ├── tools.yaml
    └── examples/
        ├── landing_page.md
        └── react_component.md
```

**与 2.11 三层流转的对应**:

- 开发期:上述目录结构由 Git 管理,支持版本控制与团队协作(单人亦利于回溯)。
- 运行时:Skill 加载器优先从 Postgres 读取(支持 UI 编辑),回退到文件系统。
- UI 编辑:修改后写入 Postgres 并生成 `version_snapshots` 快照。

`[MVP]` 三场景目录结构 + Git 管理 + PG 回退实现。
`[V2]` Skill 市场(导入/导出/分享);Skill 签名验证(安全来源校验)。

---

### 7.2 Skill 元数据 schema

`skill.yaml` 定义 Skill 的元数据,加载时解析。

**schema 定义**:

```yaml
# skills/office/skill.yaml
name: office
version: "1.0.0"
description: "日常办公场景:文档处理、网页浏览、信息检索"
scenario: office                          # 对应 4.15 知识库 scenario 标签
author: "zongxin"
created_at: "2026-07-29"
enabled: true

# 工具依赖白名单(5.6 通用工具集子集)
dependencies:
  tools:
    - name: code_execution
      safety_level_override: elevated     # 覆盖 5.12 默认 safety_level
    - name: file_read
      safety_level_override: safe
    - name: file_write
      safety_level_override: elevated
    - name: web_search
      safety_level_override: safe
    - name: search_knowledge              # 第 4 章 RAG
      safety_level_override: safe
    - name: http_request
      safety_level_override: elevated
      enabled: false                      # MVP 禁用,V2 启用

# 权限配置
permissions:
  allow_file_write: true
  allow_network: true                     # web_search 需要
  sandbox_enabled: true
  max_file_size_mb: 50                    # 文档大小限制

# 模板变量(3.7)
prompt_vars:
  - user.name
  - user.preferences
  - session.id
  - session.created_at
  - now
  - skills.active

# 知识库配置(第 4 章)
knowledge_base:
  enabled: true
  scenario: office                        # 过滤条件
  auto_retrieve: false                    # Agentic RAG,Agent 自主决定

# 少样本示例配置
examples:
  enabled: true
  max_examples: 3                         # token 预算控制
  inject_to: frozen_zone                  # 注入 Frozen Zone

# Frozen Zone token 预算(关联第 3 章)
max_frozen_token: 4000                    # Prompt+示例总 token 上限,防止 Frozen Zone 过大触发上下文压缩
```

**校验规则**:

- `name` 全局唯一,加载时检测重名。
- `version` 遵循 semver 规范。
- `dependencies.tools` 中的工具必须在 5.6 通用工具集或已加载 MCP server 中存在。
- `safety_level_override` 必须是 `safe`/`elevated`/`dangerous` 之一(对齐 3.8)。

`[MVP]` 完整 schema + 校验规则实现。
`[V2]` Skill 依赖声明(跨 Skill 依赖);动态 schema 扩展(插件化字段)。

---

### 7.3 Skill 版本管理机制

决策 2(a/b/c/d 全选):Git + PG 快照 + 会话锁定 + UI 回滚。

**版本管理流程**:

```
开发期(Git)                    运行时(PG)                     会话层
┌─────────────────┐          ┌─────────────────┐          ┌─────────────────┐
│ skills/office/  │          │ version_snapshots│          │ sessions        │
│ skill.yaml      │ 修改 →   │ scope=skill      │ 加载 →   │ locked_skill_   │
│ system_prompt.md│ ──────→  │ version=1.0.0    │ ──────→  │ version=1.0.0   │
│ tools.yaml      │          │ payload={...}    │          │ frozen_hash=xxx │
└─────────────────┘          └─────────────────┘          └─────────────────┘
      │                            │                            │
      │ git commit                 │ 每次 UI 编辑生成新快照      │ 会话启动锁定
      │ git tag v1.0.0             │ 保留历史所有版本            │ 运行中不切换
      ↓                            ↓                            ↓
   源码版本可追溯              UI 可回滚任意历史版本         Frozen Zone 稳定
```

**会话锁定规则**:

```python
class SkillManager:
    async def load_skill_for_session(self, skill_name: str, 
                                      session_id: str) -> Skill:
        # 1. 从 PG 读取最新版本(或指定版本)
        skill = await self.skill_repo.get_latest(skill_name)
        
        # 2. 锁定版本到会话
        await self.session_repo.update(session_id, 
            locked_skill_name=skill_name,
            locked_skill_version=skill.version,
            frozen_hash=None  # 待 Frozen Zone 构建后填充
        )
        
        # 3. 加载工具白名单(5.5)
        await self.tool_dispatcher.load_tools(skill.dependencies.tools, session_id)
        
        # 4. 构建 Frozen Zone(第 3 章)
        frozen_zone = await self.context_manager.build_frozen_zone(skill, session_id)
        
        # 5. 计算 hash 并锁定
        frozen_hash = self._compute_hash(frozen_zone)
        await self.session_repo.update(session_id, frozen_hash=frozen_hash)
        
        return skill
```

**回滚约束**:

- UI 回滚操作仅修改 PG 中的 `latest_version` 指针。
- **当前运行中的会话不受影响**(继续使用锁定版本,避免 Frozen Zone hash 失效、KV Cache 全部 miss)。
- 回滚对**新创建的会话**生效。
- 会话结束后,下次启动自动加载 `latest_version` 指向的版本。

**会话重启版本加载规则**:已锁定旧版本的会话正常结束销毁后,下次新建同 Skill 会话会自动读取 `latest_version`(回滚后的新版本),不会沿用历史锁定版本;仅**持续在线未重启会话**维持原有锁定 Skill 版本。

**与 3.4 hash 校验的衔接**:Skill 版本锁定后,Frozen Zone(system_prompt + tools 定义)的 hash 固化。任何运行时修改 Skill 的尝试(如热加载)会触发 `ContextIntegrityError`(3.4)。

`[MVP]` Git + PG 快照 + 会话锁定 + UI 回滚全量实现。
`[V2]` 多版本并存(A/B 测试);Skill 灰度发布;版本兼容性自动校验。

---

### 7.4 Skill 加载与激活流程

决策 10(A):UI 手动选择 → 会话启动锁定 → Frozen Zone 构建。

**完整加载流程**:

```python
class SkillLoader:
    async def activate_skill(self, skill_name: str, session_id: str):
        """会话创建时激活 Skill"""
        
        # 1. 读取 skill.yaml(PG 优先,文件系统回退)
        skill_meta = await self.skill_repo.get_latest(skill_name)
        if not skill_meta:
            skill_meta = await self._load_from_filesystem(skill_name)
        
        # 兜底:Skill 不存在时返回 UI 友好错误,跳转选择页
        if not skill_meta:
            raise SkillNotFoundError(
                f"Skill '{skill_name}' 不存在,请检查 Skill 是否已安装"
            )
        
        # 2. 读取 system_prompt.md
        prompt_content = await self.skill_repo.get_prompt(
            skill_name, skill_meta.version
        )
        
        # 3. 模板变量替换(3.7)
        prompt_content = await self.template_renderer.render(
            prompt_content,
            variables={
                "user": await self.user_repo.get_profile(),
                "session": await self.session_repo.get(session_id),
                "now": datetime.now().isoformat(),
                "skills": {"active": skill_name}
            }
        )
        
        # 4. 加载少样本示例(7.7)
        examples = await self._load_examples(skill_name, max_examples=3)
        prompt_content += "\n\n## 示例\n\n" + "\n\n".join(examples)
        
        # 5. 加载工具白名单(5.5 动态加载)
        await self.tool_dispatcher.load_tools(
            skill_meta.dependencies.tools, 
            session_id
        )
        
        # 6. 构建 Frozen Zone(第 3 章)
        frozen_zone = FrozenZone(
            system_prompt=prompt_content,
            tools=await self.tool_dispatcher.list_tools(session_id)
        )
        
        # 7. 锁定版本 + 计算 hash(7.3)
        await self.skill_manager.load_skill_for_session(skill_name, session_id)
```

**会话中途切换的约束**:

```python
async def switch_skill(self, new_skill: str, session_id: str):
    """V2 预留:会话中途切换 Skill"""
    # 警告:切换会导致 Frozen Zone 重建,KV Cache 全部失效
    # MVP 直接拒绝
    raise SkillSwitchNotAllowedError(
        "Skill 切换会破坏 Frozen Zone hash,KV Cache 将全部失效。"
        "MVP 不支持会话中途切换,请新建会话。"
    )
```

**缓存代价说明**:

- Frozen Zone 包含 system_prompt + tools 定义,是 KV Cache 的 prefix。
- 切换 Skill → system_prompt 变化 + tools 变化 → prefix hash 变化 → KV Cache 全部 miss。
- 后续所有请求需重新计算 prefix 的 KV,增加首次响应延迟与 token 成本(3.4)。

`[MVP]` UI 手动选择 + 会话锁定 + 切换拒绝实现。
`[V2]` 语义自动路由(根据用户输入匹配 Skill);多 Skill 并行(多 Frozen Zone 共存);会话中途切换(带缓存重建提示)。

---

### 7.5 Skill 间的共享与依赖

决策 7(B):共享 5.6 通用工具集,Skill 仅声明工具白名单。

**工具白名单矩阵**:

| 工具 | 办公 | 数据分析 | 前端设计 | 说明 |
|---|---|---|---|---|
| `code_execution` | ✓ (elevated) | ✓ (elevated) | ✓ (elevated) | 沙箱代码执行(第 6 章) |
| `file_read` | ✓ (safe) | ✓ (safe) | ✓ (safe) | 文件读取(5.8) |
| `file_write` | ✓ (elevated) | ✓ (elevated) | ✓ (elevated) | 文件写入(5.8) |
| `web_search` | ✓ (safe) | ✗ | ✓ (safe) | Web 搜索(5.7) |
| `search_knowledge` | ✓ (safe) | ✓ (safe) | ✓ (safe) | 知识库检索(4.15) |
| `http_request` | ✗ (V2) | ✗ | ✗ | HTTP 请求(5.9,V2) |
| `get_current_time` | ✓ (safe) | ✓ (safe) | ✓ (safe) | 时间工具(5.10) |
| `calculator` | ✓ (safe) | ✓ (safe) | ✗ | 计算器(5.10) |
| `db_query` | ✗ | ✗ (V2) | ✗ | 数据库查询(V2) |
| `shell_exec` | ✗ (V2) | ✗ (V2) | ✗ (V2) | Shell 执行(V2) |

**跨 Skill 工具权限隔离**(缺口补充):

同一工具在不同 Skill 中可配置差异化 `safety_level`:

```python
# 办公场景:文档读写
- name: file_read
  safety_level_override: safe        # 办公场景文件读取低风险

# 数据分析场景:大批量数据文件
- name: file_read
  safety_level_override: safe        # 读取仍 safe
- name: file_write
  safety_level_override: elevated    # 写入结果文件需确认
```

**权限缓存隔离**(联动 5.12):

- 5.12 权限确认缓存的 `cache_key` 必须包含 `skill_name`,避免不同 Skill 同工具的权限混淆。
- `cache_key = hash(f"{skill_name}:{tool_name}:{json.dumps(args, sort_keys=True)}")`

**权限缓存隔离完整实现**:

```python
import hashlib
import json

def get_permission_cache_key(skill_name: str, tool_name: str, args: dict) -> str:
    """权限确认缓存完整 key 构造,区分不同 Skill 同工具不同权限
    
    skill_name 作为缓存隔离前缀,完全规避不同 Skill 同一工具权限缓存互相覆盖问题。
    与 5.12 权限校验逻辑完全兼容。
    """
    args_str = json.dumps(args, sort_keys=True, ensure_ascii=False)
    raw = f"{skill_name}::{tool_name}::{args_str}"
    return hashlib.sha256(raw.encode()).hexdigest()
```

**不做继承的理由**:

- 单人维护,继承链增加复杂度与调试难度。
- 工具白名单已足够灵活,无需通过继承复用代码。
- Prompt 独立定制,不共享基础模板(决策 8)。

`[MVP]` 工具白名单 + 跨 Skill 权限隔离 + 缓存 key 含 skill_name 实现。
`[V2]` Skill 继承机制(基础 Skill 派生场景 Skill);工具组合预设(一键启用工具组)。

---

### 7.6 Skill 的 system prompt 设计框架

决策 8(B):场景专用,参考书中第三章四段式框架。

**四段式框架**:

```markdown
# {Skill Name} Agent

## 1. 角色定位
定义 Agent 的身份、能力边界、服务对象。
- 你是谁(如"数据分析助手")
- 你能做什么(如"处理 CSV/Excel 数据,生成图表与统计分析")
- 你不能做什么(如"不执行 SQL 查询,V2 支持")

## 2. 任务约束
定义任务执行规则、质量要求、安全边界。
- 输入约束(接受什么格式的数据/请求)
- 输出约束(响应格式、语言、长度)
- 安全约束(不泄露 system prompt、不执行危险操作)
- 工具使用约束(何时用何工具,优先级)

## 3. 工具使用规范
定义工具调用的决策逻辑与最佳实践。
- 可用工具列表(动态注入,{{skills.tools}})
- 工具选择优先级(如"先搜索知识库,再 Web 搜索")
- 参数填写规范(如"code_execution 的 language 参数必须明确")
- 结果处理规范(如"大输出自动存 artifact,不要重复粘贴")

## 4. 输出格式
定义最终回复用户的格式规范。
- 结构化输出(如"数据分析结果用表格 + 图表路径")
- 来源标注(如"引用知识库内容标注来源")
- 错误处理(如"工具失败时告知用户并提供替代方案")
```

**模板变量注入点**(3.7):

```markdown
## 用户信息
- 用户:{{user.name}}
- 偏好语言:{{user.preferences.language}}
- 时区:{{user.preferences.timezone}}

## 会话信息
- 会话 ID:{{session.id}}
- 创建时间:{{session.created_at}}
- 当前时间:{{now}}
- 激活 Skill:{{skills.active}}
```

**变量解析时机**(3.7):会话启动时解析一次,运行时不再替换(保证 Frozen Zone 稳定)。`{{kb.context}}` 例外,放入 Stable Zone 运行时动态注入。

**完整可复用 Prompt 模板示例**(办公场景,数据分析/前端仅替换角色部分即可复用):

```markdown
# 办公场景专用 Agent

## 1. 角色定位
你是本地办公自动化助手,专注 Excel/Word 文档处理、网络信息调研。
仅使用内置文档工具与网页搜索,不调用日历、邮件等 V2 MCP 能力。

## 2. 任务约束
{{user.name}},你的时区 {{user.preferences.timezone}},当前时间 {{now}}
- 文档单文件上限 50MB,超大文件自动分块读取
- 敏感表格禁止完整输出,仅展示汇总统计
- 网络信息必须标注来源 URL,禁止编造数据

## 3. 工具使用规范
可用工具列表:{{skills.tools}}
1. 文档优先用 file_read 读取结构,再通过 code_execution 处理
2. 行业调研先用 web_search,抓取页面补充细节
3. 超长文本输出存入 artifact,不要粘贴全部内容到对话

## 4. 输出格式
1. 文档结果:表格汇总 + 文件路径
2. 调研内容:分点列表,每条附带 [来源](url)
3. 执行失败清晰说明文件/网络错误,给出简化操作建议
```

> 数据分析、前端 Skill 仅替换「角色定位 + 工具约束」段落即可复用该四段结构。

`[MVP]` 四段式框架 + 模板变量注入 + 三场景独立 prompt 实现。
`[V2]` Prompt 版本对比(A/B 测试);Prompt 自动优化(基于评估结果)。

---

### 7.7 少样本示例机制

决策 9(B):每 Skill 2-3 个 markdown 示例,注入 Frozen Zone。

**示例格式**:

```markdown
<!-- skills/data_analysis/examples/sales_analysis.md -->
## 示例:销售数据分析

### 用户输入
分析 data/sales.csv 文件,按地区统计销售额,生成柱状图。

### Agent 行为
1. 调用 file_read("data/sales.csv") 读取文件前 100 行,了解数据结构
2. 调用 code_execution(language="python", code="""
   import pandas as pd
   df = pd.read_csv('data/sales.csv')
   region_sales = df.groupby('region')['amount'].sum()
   region_sales.plot(kind='bar')
   import matplotlib.pyplot as plt
   plt.savefig('outputs/sales_by_region.png')
   print(region_sales)
   """)
3. 调用 file_read("outputs/sales_by_region.png") 确认图表生成

### 期望输出
各地区销售额统计如下:

| 地区 | 销售额 |
|---|---|
| 华东 | ¥1,234,567 |
| 华北 | ¥987,654 |
| ...

图表已生成:outputs/sales_by_region.png
```

**注入机制**:

```python
class ExampleLoader:
    async def load_examples(self, skill_name: str, 
                            max_examples: int = 3) -> list[str]:
        examples_dir = Path(f"skills/{skill_name}/examples")
        examples = []
        for md_file in sorted(examples_dir.glob("*.md"))[:max_examples]:
            examples.append(md_file.read_text(encoding="utf-8"))
        return examples
```

**token 预算控制**:

- 每个示例限制在 500 token 以内(避免 Frozen Zone 过大)。
- 总示例 token 不超过 1500(3 个示例)。
- 超限时截断或减少示例数量,优先保留最具代表性的。

**与 Frozen Zone 的关系**:

- 示例注入到 system_prompt 之后、tools 定义之前。
- 作为 Frozen Zone 的一部分,hash 校验包含示例内容。
- 示例变更 → hash 变化 → KV Cache 失效(需新会话生效)。

`[MVP]` markdown 示例 + 注入 Frozen Zone + token 预算控制实现。
`[V2]` RAG 检索式少样本(从示例库动态检索相关示例);示例质量自动评估。

---

### 7.8 办公场景 Skill 总览

决策 4:MVP 文档处理 + 网页浏览;V2 日历/邮件/IM/任务。

**场景定位**:日常办公自动化,聚焦文档处理与信息检索。

**能力清单**:

| 能力 | MVP/V2 | 实现方式 | 工具依赖 |
|---|---|---|---|
| 文档处理(Excel/Word) | MVP | 沙箱 openpyxl/python-docx | code_execution + file_read/file_write |
| 网页浏览(搜索/摘要) | MVP | 5.7 web_search + 沙箱抓取 | web_search + code_execution |
| 日历管理 | V2 | MCP server(飞书/Google) | V2 MCP |
| 邮件管理 | V2 | MCP server(飞书/Gmail) | V2 MCP |
| IM 消息 | V2 | MCP server(飞书/钉钉) | V2 MCP |
| 任务看板 | V2 | MCP server | V2 MCP |

**MVP 边界理由**:

- 文档处理与网页浏览不依赖第三方 MCP,纯沙箱 + 内置工具即可实现。
- 日历/邮件/IM 依赖第三方 API + MCP server,需配套 MCP 管理面板(5.4),V2 迭代。

`[MVP]` 文档处理 + 网页浏览 Skill 实现。
`[V2]` 日历/邮件/IM/任务看板 Skill(配套 MCP 管理面板)。

---

### 7.9 办公场景:文档处理 Skill

MVP 核心:沙箱 openpyxl/python-docx 处理 Excel/Word。

**system_prompt.md 核心片段**:

```markdown
## 角色定位
你是办公文档处理助手,擅长 Excel/Word 文档的读写、格式化、数据提取与合并。

## 任务约束
- 支持格式:.xlsx(Excel)、.docx(Word)
- 文件大小限制:单文件不超过 50MB
- 数据安全:处理敏感文档时不输出数据内容到对话,仅输出摘要
- 格式保留:修改文档时保留原有格式(字体、样式、公式)

## 工具使用规范
1. 先用 file_read 确认文件存在与大小
2. 用 code_execution 执行 openpyxl/python-docx 代码
3. 大输出(如完整 Excel 数据)存 artifact,不直接粘贴到对话
4. 生成的新文件存入 outputs/ 目录

## 输出格式
- 文档操作结果用表格展示关键数据(前 10 行)
- 生成的文件标注路径(如"已生成:outputs/report.xlsx")
- 错误时说明原因并提供替代方案
```

**典型能力**:

- Excel:数据汇总、透视表、格式化、图表、合并多表
- Word:报告生成、模板填充、格式转换、批量替换

**示例任务**(少样本):

```
用户:把 data/sales.xlsx 按地区汇总,生成汇总表

Agent:
1. file_read("data/sales.xlsx") → 确认文件
2. code_execution(python) → pandas + openpyxl 汇总
3. file_write("outputs/sales_summary.xlsx") → 保存结果
4. 回复:"已生成汇总表 outputs/sales_summary.xlsx,各地区数据如下:..."
```

**文件大小限制处理**:大文件(>50MB)沙箱可能 OOM。Agent 应分块处理(如 pandas chunksize),或提示用户拆分文件。

**超大文件分块读取代码模板**(触发阈值:行数 > 10000 或文件 > 50MB):

```python
# 超大 Excel 分片读取模板
import pandas as pd

# 超过 10 万行自动分块,避免沙箱内存超限
chunk_iter = pd.read_excel("data/sales.xlsx", chunksize=10000)
total_summary = []
for chunk in chunk_iter:
    agg = chunk.groupby("region")["amount"].sum()
    total_summary.append(agg)

# 合并分片结果
final_result = pd.concat(total_summary).groupby(level=0).sum()
print(final_result)
```

`[MVP]` Excel/Word 处理 + 大文件分块策略实现。
`[V2]` PPT 处理(python-pptx);PDF 处理(pypdf);文档版本对比。

---

### 7.10 办公场景:网页浏览 Skill

MVP 核心:5.7 web_search + 沙箱代码抓取。

**system_prompt.md 核心片段**:

```markdown
## 角色定位
你是信息检索助手,擅长网络搜索、网页内容抓取与信息摘要。

## 任务约束
- 搜索结果需标注来源 URL
- 信息摘要保留关键事实,去除冗余
- 多来源交叉验证重要信息
- 不输出版权内容全文,仅摘要

## 工具使用规范
1. 先用 web_search 搜索关键词
2. 用 code_execution 抓取具体网页内容(requests + BeautifulSoup)
3. 信息综合后用表格或列表呈现
4. 来源标注格式:[来源:网站名](URL)

## 输出格式
- 搜索结果用列表展示(标题 + URL + 摘要)
- 深度分析用表格对比多来源信息
- 引用原文时用引用块,标注来源
```

**典型能力**:

- 行业调研:搜索行业报告,汇总关键数据
- 产品对比:多产品参数对比
- 新闻摘要:最新资讯汇总

**示例任务**:

```
用户:调研国内主流大模型 API 的价格对比

Agent:
1. web_search("国内大模型 API 价格 GLM DeepSeek Qwen") → 获取搜索结果
2. code_execution(python) → requests 抓取各厂商定价页
3. code_execution(python) → 解析价格数据,生成对比表
4. 回复:"主流大模型 API 价格对比如下:..."(含表格 + 来源)
```

**与知识库的边界**:

- web_search:获取实时网络信息(新闻、最新数据)
- search_knowledge:检索本地知识库(文档、历史资料)
- Agent 根据问题性质自主选择(如"最新"用 web_search,"内部文档"用 search_knowledge)

`[MVP]` web_search + 沙箱抓取 + 来源标注实现。
`[V2]` 浏览器自动化(Playwright);网页内容变更监控;信息可信度评分。

---

### 7.11 数据分析场景 Skill 总览

决策 5:MVP pandas/matplotlib/openpyxl/scipy + CSV/Excel;V2 SQL。

**场景定位**:数据清洗、分析、可视化,不依赖外部数据库。

**能力清单**:

| 能力 | MVP/V2 | 实现方式 | 工具依赖 |
|---|---|---|---|
| CSV 处理 | MVP | 沙箱 pandas | code_execution + file_read/file_write |
| Excel 处理 | MVP | 沙箱 openpyxl/pandas | code_execution + file_read/file_write |
| 数据可视化 | MVP | 沙箱 matplotlib/plotly | code_execution |
| 统计分析 | MVP | 沙箱 scipy/statsmodels | code_execution |
| SQL 查询 | V2 | db_query 工具(5.6 V2) | V2 db_query |

**MVP 边界理由**:

- pandas/matplotlib/scipy 沙箱内即可运行,无需外部依赖。
- SQL 直连数据库复杂度高(权限管理、连接池、查询限制),延后 V2。

`[MVP]` CSV/Excel/可视化/统计 全量实现。
`[V2]` SQL 查询(配套权限管控);实时数据流处理;大数据引擎集成(Dask/Polars)。

---

### 7.12 数据分析场景:数据处理与可视化 Skill

MVP 核心:沙箱 pandas + matplotlib + scipy。

**system_prompt.md 核心片段**:

```markdown
## 角色定位
你是数据分析助手,擅长数据清洗、统计分析与可视化。

## 任务约束
- 支持格式:.csv、.xlsx、.json
- 数据安全:处理敏感数据时不输出原始数据,仅输出统计结果
- 图表规范:标题、轴标签、图例必须完整,中文用 SimHei 字体
- 统计方法:根据数据特征选择合适方法(描述性统计/假设检验/回归)

## 工具使用规范
1. 先用 file_read 或 code_execution 读取数据,了解结构
2. 数据清洗:code_execution(pandas) 处理缺失值、异常值、类型转换
3. 分析:code_execution(scipy/statsmodels) 执行统计方法
4. 可视化:code_execution(matplotlib) 生成图表,存入 outputs/
5. 结果汇总:表格 + 图表路径 + 关键发现

## 输出格式
- 数据概览:行数、列数、数据类型、缺失率
- 统计结果:表格展示关键指标
- 图表:标注文件路径(如"图表:outputs/sales_trend.png")
- 关键发现:列表总结 3-5 条核心洞察
```

**图表展示机制**(联动 6.11):

- Agent 生成的图表存入沙箱 `outputs/` 目录(路径:`{session_workspace}/outputs/*.png`)。
- Agent 通过 `file_read` 确认图表存在,在回复中标注路径。
- 前端 UI 行为:ReAct 工具结果中解析文件路径,渲染图片预览卡片。
- 交互支持:点击卡片在 Electron 内置图片弹窗全屏查看。
- V2 预留:实时流式图表内嵌对话,MVP 仅文件预览模式。

**示例任务**:

```
用户:分析 data/sales.csv,找出销售额最高的产品和月份

Agent:
1. code_execution(python) → pandas 读取 + 数据概览
2. code_execution(python) → 按产品分组汇总,排序
3. code_execution(python) → 按月份分组,matplotlib 生成趋势图
4. code_execution(python) → 保存图表到 outputs/product_sales.png, outputs/monthly_trend.png
5. 回复:"销售额最高的产品是 XX(¥123,456),峰值月份是 7 月。图表:..."
```

**统计方法选择指南**(注入 prompt):

- 描述性统计:均值、中位数、标准差、分布
- 假设检验:t 检验、卡方检验、ANOVA
- 相关性分析:Pearson、Spearman
- 回归分析:线性回归、逻辑回归

`[MVP]` pandas + matplotlib + scipy 全栈 + 图表存储实现。
`[V2]` 交互式图表(plotly);机器学习(scikit-learn);大数据处理(Polars)。

---

### 7.13 前端设计场景 Skill 总览

决策 6:MVP HTML/React/Vue 代码生成 + 设计系统 RAG;V2 浏览器预览/Figma/截图。

**场景定位**:UI 代码生成,遵循设计系统规范。

**能力清单**:

| 能力 | MVP/V2 | 实现方式 | 工具依赖 |
|---|---|---|---|
| HTML/CSS/JS 生成 | MVP | 沙箱代码执行 | code_execution + file_write |
| React 组件生成 | MVP | 沙箱代码执行 | code_execution + file_write |
| Vue 组件生成 | MVP | 沙箱代码执行 | code_execution + file_write |
| 设计系统 RAG | MVP | 第 4 章知识库 | search_knowledge |
| 浏览器预览 | V2 | Electron Webview / Playwright | V2 浏览器工具 |
| Figma 设计稿读取 | V2 | Figma MCP | V2 MCP |
| 截图工具 | V2 | Playwright | V2 浏览器工具 |

**MVP 边界理由**:

- 代码生成纯沙箱实现,无需外部依赖。
- 浏览器预览/Figma/截图依赖 Electron Webview 或外部工具,V2 迭代。

`[MVP]` HTML/React/Vue 生成 + 设计系统 RAG 实现。
`[V2]` 浏览器预览(Electron Webview);Figma MCP;截图工具(Playwright)。

---

### 7.14 前端设计场景:代码生成 Skill

MVP 核心:沙箱 JS/Python 生成 HTML/CSS/JS/React/Vue。

**system_prompt.md 核心片段**:

```markdown
## 角色定位
你是前端设计助手,擅长生成符合设计规范的 UI 代码。

## 任务约束
- 支持框架:原生 HTML/CSS/JS、React、Vue
- 设计规范:遵循知识库中的设计系统(颜色、字体、间距、组件)
- 响应式:必须适配移动端与桌面端
- 代码质量:语义化 HTML、可维护 CSS、组件化 JS

## 工具使用规范
1. 先用 search_knowledge 检索设计系统文档(如"按钮规范"、"颜色变量")
2. 用 code_execution 生成代码,存入 outputs/ 目录
3. 代码文件结构:HTML/CSS/JS 分离,或组件文件
4. 生成后用 file_read 确认文件内容

## 输出格式
- 代码文件:标注路径(如"已生成:outputs/landing.html")
- 代码说明:简要说明结构(如"包含 header、hero、features、footer 四部分")
- 预览方式:MVP 需用户手动打开文件;V2 内嵌预览
```

**设计系统 RAG 联动**(7.15):

- Agent 生成代码前,先用 `search_knowledge(query="按钮组件规范", scenario="frontend_design")` 检索设计系统。
- 检索结果注入 Stable Zone(3.7),Agent 参考规范生成代码。
- 知识库内容:设计 token(颜色/字体/间距)、组件规范、样式指南。

**示例任务**:

```
用户:生成一个产品落地页,浅色主题

Agent:
1. search_knowledge("浅色主题设计规范 颜色变量") → 检索设计系统
2. code_execution(python) → 生成 HTML 结构
3. code_execution(python) → 生成 CSS(浅色主题,参考检索到的颜色变量)
4. code_execution(javascript) → 生成交互 JS
5. file_write("outputs/landing.html", ...) → 保存
6. 回复:"已生成落地页 outputs/landing.html,浅色主题,包含..."
```

`[MVP]` HTML/React/Vue 代码生成 + 设计系统 RAG 检索实现。
`[V2]` 代码实时预览(Electron Webview);组件库集成(Ant Design/Element);设计稿转代码(Figma MCP)。

---

### 7.15 前端设计场景:设计系统知识库

MVP 核心:第 4 章 RAG 检索设计系统文档。

**知识库内容**:

```
${WORKSPACE}/data/kb/frontend_design/
├── design_tokens.md          # 设计 token(颜色、字体、间距)
├── components/
│   ├── button.md             # 按钮组件规范
│   ├── card.md               # 卡片组件规范
│   ├── form.md               # 表单组件规范
│   └── navigation.md         # 导航组件规范
├── styles/
│   ├── dark_theme.md         # 深色主题规范
│   ├── light_theme.md        # 浅色主题规范
│   └── responsive.md         # 响应式断点规范
└── guidelines/
    ├── accessibility.md      # 无障碍设计指南
    └── typography.md         # 排版指南
```

**scenario 标签过滤**(联动 4.15):

- 知识库文档上传时标记 `scenario: frontend_design`。
- `search_knowledge` 工具调用时,Agent 传入 `scenario="frontend_design"` 过滤。
- 避免跨场景文档泄露(4.15 场景强制过滤)。

**RAG 流程**:

```python
# Agent 调用 search_knowledge 的典型参数
{
    "query": "按钮组件规范 颜色 尺寸",
    "scenario": "frontend_design",   # 强制场景过滤
    "source": null,                  # 不限来源
    "limit": 5,
    "min_similarity": 0.2
}
```

**知识库维护**:

- 设计系统文档变更时,增量更新向量(4.16)。
- 支持版本快照(设计系统迭代可回溯)。
- V2 可支持多套设计系统(如 Material Design / Ant Design 切换)。

`[MVP]` 设计系统知识库 + scenario 过滤 + 增量更新实现。
`[V2]` 多设计系统切换;设计系统自动同步(Figma → 知识库);设计 token 代码生成(从文档提取变量)。

---

### 7.16 Skill 的评估支持

每个 Skill 的示例与执行轨迹支持评估闭环(第 8 章)。

**评估数据来源**:

| 数据源 | 用途 | 存储位置 |
|---|---|---|
| Skill 少样本示例 | 黄金样本(预期行为) | `skills/{name}/examples/` |
| Skill 版本快照 | 还原历史 Skill 行为 | `version_snapshots` |
| ReAct 执行轨迹 | 实际行为 | `react_events` |
| 沙箱执行记录 | 代码执行质量 | `react_events`(6.13) |
| 工具调用记录 | 工具使用效率 | `react_events`(5.13) |

**评估指标按场景差异化**:

| 场景 | 核心指标 | 评估方法 |
|---|---|---|
| 办公(文档处理) | 文档操作准确率、格式保留率 | 对比生成文档与预期 |
| 办公(网页浏览) | 信息召回率、来源准确性 | 对比搜索结果与人工标注 |
| 数据分析 | 分析结果正确性、图表完整性 | 对比统计结果与预期值 |
| 前端设计 | 代码可运行率、设计规范遵循率 | 沙箱执行代码 + 设计规范检查 |

**与第 8 章的衔接**:

- 第 8 章评估环境从 `skills/{name}/examples/test/` 加载黄金样本(测试集,不注入 prompt)。
- `skills/{name}/examples/train/` 的示例注入 Frozen Zone(7.7),Agent 能看到,不用于评估。
- 评估回放时,从 `version_snapshots` 还原历史 Skill 版本。
- LLM-as-Judge 按场景差异化指标评判。

`[MVP]` 评估数据源接入 + 场景化指标定义实现。
`[V2]` 自动化评估流水线;Skill 质量评分;评估结果驱动 Skill 优化。

---

### 7.17 MVP 与 V2 边界

本章设计按 MVP / V2 边界落地,与 2.16/3.16/4.17/5.18/6.16 边界表对齐。

**MVP 必须实现**:

| 模块 | MVP 范围 |
|---|---|
| Skill 目录结构 | 三场景独立目录 + Git 管理(7.1) |
| 元数据 schema | skill.yaml 完整字段 + 校验(7.2) |
| 版本管理 | Git + PG 快照 + 会话锁定 + UI 回滚(7.3) |
| 加载激活 | UI 手动选择 + Frozen Zone 构建(7.4) |
| 工具共享 | 白名单 + 跨 Skill 权限隔离(7.5) |
| Prompt 框架 | 四段式 + 模板变量(7.6) |
| 少样本 | 2-3 个 markdown 示例 + Frozen Zone 注入(7.7) |
| 办公-文档 | Excel/Word 沙箱处理(7.9) |
| 办公-网页 | web_search + 沙箱抓取(7.10) |
| 数据分析 | pandas/matplotlib/scipy 全栈(7.12) |
| 前端-代码 | HTML/React/Vue 生成(7.14) |
| 前端-知识库 | 设计系统 RAG + scenario 过滤(7.15) |
| 评估支持 | 数据源接入 + 场景化指标(7.16) |

**V2 预留接口**:

| 模块 | V2 范围 | 预留接口 |
|---|---|---|
| Skill 市场 | 导入/导出/分享 | 目录结构可扩展 |
| 版本管理 | A/B 测试、灰度发布 | `version_snapshots` 支持多版本并存 |
| 加载激活 | 语义自动路由、多 Skill 并行 | 加载器接口可扩展 |
| Skill 继承 | 基础 Skill 派生 | 元数据 schema 可扩展 `extends` 字段 |
| 办公扩展 | 日历/邮件/IM/任务 | MCP 管理面板(5.4) |
| 数据分析扩展 | SQL 查询、大数据引擎 | db_query 工具(5.6 V2) |
| 前端扩展 | 浏览器预览、Figma、截图 | Electron Webview / MCP |
| 少样本 | RAG 检索式少样本 | 示例库 + search_knowledge |
| 评估 | 自动化流水线、质量评分 | 评估接口可扩展(第 8 章) |

**与三大约束的对应**:

- 上下文质量优先 → 7.6 场景专用 Prompt 四段式框架;7.7 少样本示例注入 Frozen Zone;7.15 设计系统 RAG 检索结果注入 Stable Zone。
- 缓存友好 → 7.3 会话锁定 Skill 版本,Frozen Zone hash 稳定;7.4 会话中途切换拒绝,避免 KV Cache 失效;7.7 示例注入 Frozen Zone,运行时不变。
- 评估驱动迭代 → 7.16 Skill 示例作为黄金样本;版本快照支持历史回放;场景化评估指标差异化。

---

第 7 章起草完成。本章展开了场景 Skills 的完整设计:目录结构、元数据 schema、版本管理、加载激活、工具共享、Prompt 框架、少样本机制,以及三个场景(办公/数据分析/前端设计)的具体 Skill 实现,共 17 节。

后续章节衔接:

- 第 8 章:基于 7.16 Skill 评估支持 + 5.13 工具调用事件 + 6.13 沙箱执行事件,构建评估与持续进化闭环。
- 第 9 章:MVP 路线与 V2 扩展总结。

---

## 第 8 章 评估与持续进化闭环

本章是"评估驱动迭代"约束的核心落地。基于 7.16 Skill 评估支持 + 5.13 工具调用事件 + 6.13 沙箱执行事件,构建完整的评估方法论与持续进化机制。

核心设计原则:
- 离线批量评估为主,交互式回放补足多轮场景
- 规则校验先行(零模型开销),LLM-as-Judge 评判主观质量
- 评估结果绑定 `react_events`,复用现有可观测体系(2.15)
- 三类更新载体(Prompt/Skills/Harness)统一纳入迭代闭环

每节末尾以 `[MVP]` 或 `[V2]` 标注实现边界。

---

### 8.1 评估闭环在架构中的位置与职责

本章是全书"评估驱动迭代"约束的核心落地,串联前序所有章节的可观测数据。

**职责边界**:

- 评估环境搭建(离线批量 + 交互式回放)
- 数据集管理(黄金样本 + 自动扩充)
- 指标计算(五类指标 + LLM-as-Judge)
- 结果分析(版本对比 + 可视化)
- 迭代流程串联(三类更新载体的评估驱动闭环)

**与 7.16 的衔接**:7.16 定义数据源(示例 + 轨迹 + 快照)与场景化指标,本章实现评估执行与迭代闭环。

**数据来源全景**:

| 数据源 | 用途 | 存储位置 | 关联章节 |
|---|---|---|---|
| Skill 少样本示例 | 黄金样本 | `skills/{name}/examples/` | 7.7 |
| Skill 版本快照 | 还原历史 Skill | `version_snapshots` | 7.3 |
| ReAct 执行轨迹 | 实际行为 | `react_events` | 2.15 |
| 沙箱执行记录 | 代码执行质量 | `react_events`(event_type="sandbox_execution") | 6.13 |
| 工具调用记录 | 工具使用效率 | `react_events`(event_type="tool_call") | 5.13 |
| 上下文压缩记录 | 压缩行为 | `react_events`(event_type="compress") | 3.10 |
| 注入防护记录 | 安全事件 | `react_events`(event_type="injection_alert") | 3.12 |

**安全联动约束**:所有评估会话的沙箱磁盘/内存限制复用全局 sandbox 配置(6.7),不会因批量评测放开资源阈值,避免评估会话影响桌面端稳定性。

`[MVP]` 评估闭环核心链路实现(数据源接入 + 指标计算 + 迭代流程)。
`[V2]` 在线评估 + 自动化流水线 + 评估结果驱动自动优化。

---

### 8.2 两类评估环境:离线批量 + 交互式回放

决策 1(a+c):MVP 落地离线批量 + 交互式回放;在线评估 V2。

**离线批量评估**:

- 用途:版本回归、版本对比、快速发现退化
- 执行方式:批量跑测试集,不与真实用户交互
- 特点:速度快、可并行、适合 CI 式回归
- 数据需求:输入 + 期望输出(轻量)

**交互式回放评估**:

- 用途:完整复现 ReAct 工具调用链路,贴合办公/数据分析/前端多轮交互场景
- 执行方式:从样本输入启动完整会话,执行 Agent 循环,记录所有 tool_call/tool_result
- 特点:精度高、可评判工具调用序列、但速度慢(需真实或 mock 执行)
- 数据需求:输入 + 完整期望 ReAct 行为轨迹(重量级)

**适用场景对比**:

| 场景 | 离线批量 | 交互式回放 |
|---|---|---|
| Prompt 小修改回归 | ✓(快速验证输出质量) | △(可选,验证工具调用) |
| Skill 版本升级 | ✓ | ✓(必选,验证完整链路) |
| Harness 代码变更 | ✓ | ✓(必选,验证底层行为) |
| 模型切换 | ✓ | ✓(必选,验证工具调用兼容性) |

**对 KV Cache 的影响**:

- 离线批量:仅模拟模型调用,不创建会话、不重建 Frozen/Stable Zone,无缓存变更,适合快速回归。
- 交互式回放:新建独立评估会话,重建 Frozen/Stable Hash(与真实会话隔离),不影响线上会话缓存。

`[MVP]` 离线批量 + 交互式回放双环境实现。
`[V2]` 在线评估(真实会话中收集用户反馈)。

---

### 8.3 黄金样本数据集设计

决策 2:每场景 20 条,手动编写 + 真实会话提取,格式为输入 + 完整期望 ReAct 行为轨迹。

**样本规模与来源**:

- 规模:每场景 20 条(单人维护成本可控)
- 来源:
  - 手动编写基准(10 条):覆盖核心能力,作为回归基线
  - 真实会话提取(10 条):从实际使用中筛选优质轨迹,贴近真实分布
- 覆盖三类用例:
  - 正常用例(12 条):典型业务场景
  - 边界用例(5 条):极端输入、空数据、大文件
  - 工具错误用例(3 条):工具调用失败、超时、权限拒绝

**样本格式**:

```json
{
  "sample_id": "office_001",
  "scenario": "office",
  "skill_name": "office",
  "case_type": "normal",
  "difficulty": "easy",
  "input": "把 data/sales.xlsx 按地区汇总,生成汇总表",
  "expected_react_trace": {
    "tool_calls": [
      {
        "tool": "file_read",
        "args": {"path": "data/sales.xlsx"},
        "expected_result_type": "file_content"
      },
      {
        "tool": "code_execution",
        "args": {
          "language": "python",
          "code_contains": "groupby"
        },
        "expected_result_type": "execution_output"
      },
      {
        "tool": "file_write",
        "args": {"path_contains": "outputs/sales_summary"}
      }
    ],
    "expected_output_contains": ["地区", "销售额", "outputs/sales_summary"]
  },
  "expected_output": "已生成汇总表 outputs/sales_summary.xlsx..."
}
```

**轨迹评判粒度**:

- 工具调用序列(是否按预期顺序调用)
- 工具参数(关键参数是否正确,如 `language: python`、`code_contains: groupby`)
- 文件操作(是否读写预期路径)
- RAG 检索行为(是否调用 search_knowledge,查询词是否合理)

`[MVP]` 每场景 20 条样本 + 三类用例覆盖 + 完整轨迹格式实现。
`[V2]` 样本自动生成(模型生成 + 人工筛选);样本难度自动分级。

---

### 8.4 样本数据集的存储与组织

7.16 已定 `examples/train/` + `examples/test/` 拆分。本章补充 Postgres 存储与 schema。

**目录结构**(文件系统,开发期 Git 管理):

```
skills/office/examples/
├── train/              # 训练集(注入 Frozen Zone 的少样本,7.7)
│   ├── excel_summary.md
│   └── web_research.md
└── test/               # 测试集(评估用,不注入 prompt)
    ├── normal/
    │   ├── 001_excel_summary.json
    │   └── 002_web_research.json
    ├── boundary/
    │   ├── 003_empty_csv.json
    │   └── 004_large_file.json
    └── error/
        ├── 005_tool_timeout.json
        └── 006_permission_denied.json
```

**eval_datasets 表 schema**(含 JSONB CHECK 约束):

```sql
CREATE TABLE eval_datasets (
    id SERIAL PRIMARY KEY,
    sample_id VARCHAR(100) NOT NULL UNIQUE,
    scenario VARCHAR(50) NOT NULL,
    skill_name VARCHAR(50) NOT NULL,
    skill_version VARCHAR(20) NOT NULL,
    case_type VARCHAR(20) NOT NULL CHECK (case_type IN ('normal', 'boundary', 'error')),
    difficulty VARCHAR(20) NOT NULL CHECK (difficulty IN ('easy', 'medium', 'hard')),
    input TEXT NOT NULL,
    expected_react_trace JSONB NOT NULL,
    expected_output TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    -- JSONB 结构强约束:tool_calls 必须是数组,expected_output_contains 必须是数组
    CHECK (
        jsonb_typeof(expected_react_trace->'tool_calls') = 'array'
        AND jsonb_typeof(expected_react_trace->'expected_output_contains') = 'array'
    ),
    INDEX idx_eval_datasets_scenario (scenario, skill_name, skill_version)
);
```

**Pydantic 强校验模型**(入库前拦截非法轨迹):

```python
from pydantic import BaseModel, ValidationError

class ExpectedToolCall(BaseModel):
    tool: str
    args: dict = {}
    expected_result_type: str | None = None

class ExpectedTrace(BaseModel):
    tool_calls: list[ExpectedToolCall]
    expected_output_contains: list[str]

# 入库前校验:解析失败直接抛出样本格式错误,避免脏数据入库
def validate_expected_trace(trace: dict) -> ExpectedTrace:
    try:
        return ExpectedTrace(**trace)
    except ValidationError as e:
        raise InvalidSampleFormatError(f"样本格式错误: {e}")
```

**train/test 分离规则**:

- `train/` 目录的示例注入 Frozen Zone(7.7 少样本机制),Agent 能看到
- `test/` 目录的样本仅用于评估,Agent 看不到,确保评估客观性
- 评估时从 `eval_datasets` 表读取 `test/` 对应样本

**与 7.7 的衔接**:7.7 的 `examples/` 目录默认指 `train/`(注入 prompt)。`test/` 独立管理,不注入。

`[MVP]` 目录结构 + eval_datasets 表 + train/test 分离实现。
`[V2]` 样本版本管理(数据集快照);跨场景样本共享;样本质量自动评分。

---

### 8.5 评估指标体系全景

决策 3(全选):五类指标完整支撑迭代判断。

| 指标类别 | 指标名 | 定义 | 数据来源 | 判定方式 |
|---|---|---|---|---|
| 业务 | 任务完成率 | 是否达成用户目标 | 期望输出 vs 实际输出 | 规则 + LLM |
| 工具 | 工具调用准确率 | 工具选择/参数/顺序正确性 | `react_events`(tool_call) | 规则 |
| 质量 | LLM-as-Judge 评分 | 主观响应质量 | 实际输出 | LLM |
| 敦率 | 轮次数/token/耗时 | 执行效率 | `react_events` | 规则(统计) |
| 安全 | 注入拦截/越权检测 | 安全事件 | `react_events`(injection_alert/permission) | 规则 |

**指标计算流程**:

```
样本执行 → react_events 记录轨迹 → 指标计算器读取事件 → 
硬性规则判定(工具/效率/安全) + LLM 评判(任务完成/质量) → 
汇总到 eval_runs
```

**权重与综合评分**:

- MVP 不做综合评分(各指标独立展示,人工判断)
- V2 支持自定义权重,计算综合得分

`[MVP]` 五类指标独立计算与展示实现。
`[V2]` 综合评分 + 权重配置 + 指标趋势预警。

---

### 8.6 任务完成率与工具调用准确率

硬性指标,规则判定,零模型开销。

**任务完成率**:

```python
def evaluate_task_completion(expected: dict, actual: dict) -> dict:
    """
    判定任务是否完成
    - expected_output_contains: 期望输出包含的关键词列表
    - actual_output: Agent 实际输出
    """
    expected_keywords = expected.get("expected_output_contains", [])
    actual_output = actual.get("final_output", "")
    
    matched = [kw for kw in expected_keywords if kw in actual_output]
    completion_rate = len(matched) / len(expected_keywords) if expected_keywords else 1.0
    
    return {
        "completion_rate": completion_rate,
        "matched_keywords": matched,
        "missing_keywords": [kw for kw in expected_keywords if kw not in actual_output]
    }
```

**工具调用准确率**:

```python
def evaluate_tool_calls(expected_trace: dict, actual_events: list) -> dict:
    """
    判定工具调用准确性
    - expected_trace.tool_calls: 期望的工具调用序列
    - actual_events: react_events 中的 tool_call 事件
    """
    expected_calls = expected_trace.get("tool_calls", [])
    actual_calls = [e for e in actual_events if e["event_type"] == "tool_call"]
    
    # 1. 工具选择正确性(是否调用了期望的工具)
    expected_tools = [c["tool"] for c in expected_calls]
    actual_tools = [c["tool"] for c in actual_calls]
    tool_selection_accuracy = len(set(expected_tools) & set(actual_tools)) / len(expected_tools)
    
    # 2. 调用顺序正确性(是否按期望顺序)
    order_correct = True
    for i, expected_call in enumerate(expected_calls):
        if i >= len(actual_calls):
            order_correct = False
            break
        if actual_calls[i]["tool"] != expected_call["tool"]:
            order_correct = False
            break
    
    # 3. 参数正确性(关键参数是否匹配)
    param_accuracy = 0
    for i, expected_call in enumerate(expected_calls):
        if i >= len(actual_calls):
            break
        expected_args = expected_call.get("args", {})
        actual_args = actual_calls[i].get("args", {})
        for key, expected_val in expected_args.items():
            if key in actual_args:
                if isinstance(expected_val, str) and expected_val in str(actual_args[key]):
                    param_accuracy += 1
                elif actual_args[key] == expected_val:
                    param_accuracy += 1
    
    return {
        "tool_selection_accuracy": tool_selection_accuracy,
        "order_correct": order_correct,
        "param_accuracy": param_accuracy / sum(len(c.get("args", {})) for c in expected_calls),
        "expected_calls_count": len(expected_calls),
        "actual_calls_count": len(actual_calls)
    }
```

**与 react_events 的关联**:所有实际工具调用记录在 `react_events`(5.13),含 tool_name/args/result/timestamp,评估时按 session_id + turn 查询。

`[MVP]` 任务完成率 + 工具调用准确率规则判定实现。
`[V2]` 模糊匹配(语义相似度而非关键词);工具调用路径优化建议。

---

### 8.7 效率指标与安全指标

**效率指标**(数据来源:`react_events` 统计):

```python
def evaluate_efficiency(events: list) -> dict:
    """从 react_events 计算效率指标"""
    react_turns = len(set(e["turn"] for e in events if "turn" in e))
    tool_calls = [e for e in events if e["event_type"] == "tool_call"]
    total_tokens = sum(e.get("tokens", 0) for e in events)
    total_cost = sum(e.get("cost", 0) for e in events)
    duration = (events[-1]["timestamp"] - events[0]["timestamp"]).total_seconds() if events else 0
    
    return {
        "react_turns": react_turns,
        "tool_calls_count": len(tool_calls),
        "total_tokens": total_tokens,
        "total_cost": total_cost,
        "duration_seconds": duration
    }
```

**安全指标**(数据来源:`react_events` 安全事件):

```python
def evaluate_security(events: list) -> dict:
    """从 react_events 计算安全指标"""
    injection_alerts = [e for e in events if e["event_type"] == "injection_alert"]
    permission_denied = [e for e in events if e["event_type"] == "permission_denied"]
    sandbox_violations = [e for e in events if e.get("event_subtype") == "sandbox_violation"]
    
    return {
        "injection_alerts_count": len(injection_alerts),
        "permission_denied_count": len(permission_denied),
        "sandbox_violations_count": len(sandbox_violations),
        "security_score": max(0, 100 - len(injection_alerts) * 10 - len(permission_denied) * 5)
    }
```

**与 3.12 注入防护的衔接**:`react_events` 中 `event_type="injection_alert"` 记录所有触发的注入防护(3.12),含高危/低风险分级。

**与 5.12 权限确认的衔接**:`react_events` 中 `event_type="permission_denied"` 记录用户拒绝的权限请求。

`[MVP]` 效率指标 + 安全指标统计实现。
`[V2]` 效率趋势分析(版本间对比);安全风险预测。

---

### 8.8 LLM-as-Judge 混合评判机制

决策 4(D):规则校验先行,主观质量交 GLM-4-Flash 评判。

**混合评判流程**:

```
样本执行结果 → 
├── 硬性规则判定(零模型开销)
│   ├── 任务完成率(关键词匹配,8.6)
│   ├── 工具调用准确率(序列对比,8.6)
│   ├── 效率指标(统计计算,8.7)
│   └── 安全指标(事件统计,8.7)
└── LLM 评判(主观质量)
    ├── 响应质量(清晰度、准确性、完整性)
    └── 任务完成度(语义层面判断是否达成目标)
```

**Judge 模型配置**(复用 3.11 压缩模型配置机制):

```yaml
# config.yaml
eval:
  judge_model: "glm-4-flash"      # 固定轻量低成本模型
  judge_temperature: 0.1           # 低温度保证一致性
  judge_max_tokens: 500            # 限制评判输出长度
```

**Judge prompt 模板**:

```python
JUDGE_PROMPT = """你是一个专业的 AI 助手质量评估者。

## 评估任务
根据用户输入和 Agent 的实际响应,评判以下维度:

1. **响应质量**(1-5 分)
   - 清晰度:是否易于理解
   - 准确性:信息是否正确
   - 完整性:是否完整回答了用户问题

2. **任务完成度**(1-5 分)
   - 是否达成了用户的实际目标
   - 输出是否可直接使用

## 用户输入
{user_input}

## Agent 响应
{agent_response}

## 期望输出(参考)
{expected_output}

## 评估输出格式(严格 JSON)
```json
{
  "response_quality": <1-5>,
  "task_completion": <1-5>,
  "quality_reason": "<评分理由>",
  "completion_reason": "<评分理由>"
}
```
"""
```

**规避同模型自评偏见**:

- 主模型(GLM/DeepSeek/Agnes/KIMI)与 Judge 模型(GLM-4-Flash)分离
- 配置中可指定 Judge 模型,确保与被评估模型不同
- V2 支持多 Judge 模型投票(避免单一 Judge 偏见)

**场景差异化 Judge**(V2 预留):

- 办公场景:侧重文档操作准确性与来源标注
- 数据分析场景:侧重统计结果正确性与图表完整性
- 前端设计场景:侧重代码可运行率与设计规范遵循

MVP 使用通用 Judge prompt(上述模板);V2 支持按 `scenario` 加载独立场景 Prompt。预留配置:

```yaml
# config.yaml eval 块新增 V2 预留字段
eval:
  judge_prompt_dir: "./config/judge_prompts"  # V2 场景化 Prompt 存放目录
```

`[MVP]` 混合评判流程 + GLM-4-Flash Judge + 通用 prompt 模板实现。
`[V2]` 场景差异化 Judge prompt;多 Judge 投票;Judge 质量自评估。

---

### 8.9 评估执行流程

决策 5(a+b):手动触发 + 版本变更自动触发。

**完整执行流程**:

```python
class EvalRunner:
    async def run_evaluation(
        self,
        skill_name: str,
        skill_version: str,
        model_id: str,
        eval_mode: str = "offline",  # offline(离线批量) / replay(交互式回放)
        sample_subset: str = None    # None=全量, "quick"=快速回归子集
    ) -> str:
        """执行评估,返回 run_id"""
        
        # 1. 加载数据集
        dataset = await self.dataset_repo.load(
            scenario=skill_name,
            skill_version=skill_version,
            split="test"
        )
        if sample_subset == "quick":
            dataset = dataset[:5]  # 快速回归取前 5 条
        
        # 2. 初始化评估环境
        eval_env = await self._init_eval_env(skill_name, skill_version, model_id, eval_mode)
        
        # 3. 创建评估运行记录
        run_id = await self.eval_repo.create_run(
            skill_name=skill_name,
            skill_version=skill_version,
            model_id=model_id,
            eval_mode=eval_mode,
            dataset_version=dataset.version,
            status="running"
        )
        
        # 4. 逐条执行样本
        results = []
        for sample in dataset:
            result = await self._eval_sample(sample, eval_env)
            results.append(result)
        
        # 5. 计算指标
        metrics = self._compute_metrics(results)
        
        # 6. 保存评估结果
        await self.eval_repo.update_run(run_id, status="completed", metrics=metrics)
        
        return run_id
    
    async def _eval_sample(self, sample: dict, eval_env: EvalEnv) -> dict:
        """评估单条样本"""
        # 离线批量:仅调用模型,不执行工具
        if eval_env.mode == "offline":
            actual_output = await self._call_model(sample["input"], eval_env)
            actual_events = []  # 离线模式无工具调用轨迹
        
        # 交互式回放:完整执行 ReAct 循环
        elif eval_env.mode == "replay":
            session_id = await self._start_replay_session(eval_env)
            actual_output, actual_events = await self._run_react_loop(
                sample["input"], session_id, eval_env
            )
        
        # 计算指标
        return {
            "sample_id": sample["sample_id"],
            "actual_output": actual_output,
            "actual_events": actual_events,
            "metrics": {
                "task_completion": evaluate_task_completion(
                    sample["expected_react_trace"], {"final_output": actual_output}
                ),
                "tool_calls": evaluate_tool_calls(
                    sample["expected_react_trace"], actual_events
                ) if actual_events else None,
                "efficiency": evaluate_efficiency(actual_events) if actual_events else None,
                "security": evaluate_security(actual_events) if actual_events else None
            }
        }
```

**版本变更自动触发**:

```python
class SkillVersionListener:
    async def on_skill_version_saved(self, skill_name: str, new_version: str):
        """Skill 保存新版本后自动触发快速回归"""
        await self.eval_runner.run_evaluation(
            skill_name=skill_name,
            skill_version=new_version,
            model_id="default",  # 用默认模型
            eval_mode="offline",  # 快速回归用离线模式
            sample_subset="quick"  # 仅跑前 5 条
        )
```

**手动触发**:UI 按钮"运行评估",选择场景/版本/模型/模式。

`[MVP]` 手动触发 + 版本变更自动触发(快速回归子集)实现。
`[V2]` 定时执行;CI/CD 集成(Git push 触发);并行评估(多模型同时跑)。

---

### 8.10 交互式回放的实现机制

完整复现 ReAct 工具调用链路。

**实现方式**:

```python
class ReplayExecutor:
    async def run_replay(
        self,
        sample: dict,
        skill_config: dict,
        model_id: str,
        mock_mode: bool = False
    ) -> tuple[str, list]:
        """
        交互式回放
        mock_mode=True: 工具调用返回预设结果(加速批量评测)
        mock_mode=False: 真实执行工具调用
        """
        
        # 1. 启动临时会话(隔离,不影响真实会话)
        session_id = await self.session_repo.create(
            skill_name=skill_config["name"],
            skill_version=skill_config["version"],
            model_id=model_id,
            is_eval_session=True  # 标记为评估会话
        )
        
        # 2. 构建 Frozen Zone(第 3 章)
        await self.context_manager.build_frozen_zone(skill_config, session_id)
        
        # 3. 注入用户输入
        await self.context_manager.add_user_message(session_id, sample["input"])
        
        # 4. 执行 ReAct 循环(第 2 章)
        events = []
        while True:
            # 调用模型
            response = await self.model_adapter.chat(session_id)
            events.append({"event_type": "thinking", "content": response.thinking})
            
            if response.tool_calls:
                for tool_call in response.tool_calls:
                    if mock_mode:
                        # mock 模式:返回预设结果
                        result = await self._get_mock_result(tool_call, sample)
                    else:
                        # 真实执行
                        result = await self.tool_dispatcher.execute(tool_call, session_id)
                    
                    events.append({"event_type": "tool_call", "tool": tool_call.tool, "args": tool_call.args})
                    events.append({"event_type": "tool_result", "result": result})
                    
                    # 回灌上下文
                    await self.context_manager.add_tool_result(session_id, tool_call, result)
            else:
                # 无工具调用,Agent 完成
                break
        
        # 5. 清理临时会话
        await self.session_repo.delete(session_id)
        
        return response.content, events
```

**Mock 模式**(缺口补充):

- 评估回放可开关沙箱/知识库真实调用
- mock 直接返回预设结果,加速批量评测
- 大幅降低云端模型、本地向量计算开销
- Mock 数据管理:`skills/{name}/test/mock_data/` 目录,按工具名组织

```
skills/office/test/mock_data/
├── file_read/
│   └── sales_xlsx.json       # 预设 file_read 返回内容
├── code_execution/
│   └── excel_summary.json    # 预设沙箱执行结果
└── web_search/
    └── industry_report.json  # 预设搜索结果
```

**mock 数据版本同步规则**:mock 数据文件命名与工具名一一对应,版本跟随 Skill 快照同步更新;Skill 回滚时自动加载对应版本 mock 数据,避免新旧 mock 不匹配。

**Mock vs 真实执行**:

| 模式 | 速度 | 成本 | 精度 | 适用场景 |
|---|---|---|---|---|
| Mock | 快 | 低(仅模型调用) | 中(工具结果预设) | 快速回归、Prompt 迭代 |
| 真实执行 | 慢 | 高(模型+工具) | 高(完整链路) | Skill 版本升级、模型切换验证 |

`[MVP]` 交互式回放 + Mock 模式 + 真实执行双模式实现。
`[V2]` 分布式并行回放;回放过程可视化;Mock 数据自动生成。

---

### 8.11 eval_runs 表 schema 与评估结果存储

决策 6:独立 Postgres 表,不复用 version_snapshots。

**eval_runs 表**:

```sql
CREATE TABLE eval_runs (
    id SERIAL PRIMARY KEY,
    run_id UUID NOT NULL UNIQUE DEFAULT gen_random_uuid(),
    
    -- 评估维度
    skill_name VARCHAR(50) NOT NULL,
    skill_version VARCHAR(20) NOT NULL,
    model_id VARCHAR(50) NOT NULL,
    dataset_version VARCHAR(20) NOT NULL,
    eval_mode VARCHAR(20) NOT NULL CHECK (eval_mode IN ('offline', 'replay')),
    mock_mode BOOLEAN DEFAULT FALSE,
    
    -- A/B 测试预留(V2)
    variant VARCHAR(20),  -- "A" / "B" / null
    mock_enabled BOOLEAN DEFAULT FALSE,  -- 是否使用 mock 模式(统计区分)
    
    -- 执行状态
    status VARCHAR(20) NOT NULL CHECK (status IN ('running', 'completed', 'failed')),
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    error_message TEXT,
    
    -- 指标汇总
    metrics JSONB NOT NULL,  -- 完整指标 JSON
    
    -- 样本级别结果
    sample_results JSONB,    -- 每条样本的详细结果
    
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    INDEX idx_eval_runs_skill_version (skill_name, skill_version),
    INDEX idx_eval_runs_model (model_id),
    INDEX idx_eval_runs_created (created_at)
);
```

**metrics JSON 结构**:

```json
{
  "task_completion": {
    "avg_rate": 0.85,
    "samples": [{"sample_id": "office_001", "rate": 0.9}, ...]
  },
  "tool_calls": {
    "avg_selection_accuracy": 0.92,
    "avg_param_accuracy": 0.88,
    "order_correct_rate": 0.80
  },
  "efficiency": {
    "avg_turns": 3.5,
    "avg_tokens": 2400,
    "avg_cost": 0.012,
    "avg_duration": 8.5
  },
  "security": {
    "avg_score": 95,
    "total_alerts": 2
  },
  "llm_judge": {
    "avg_response_quality": 4.2,
    "avg_task_completion": 4.5
  }
}
```

**与 version_snapshots 的边界**:

- `version_snapshots`:存配置快照(Skill/Prompt/知识库/模型定价)
- `eval_runs`:存评估结果(指标/轨迹/样本级别结果)
- 两者通过 `skill_version` 关联,但数据维度完全不同,不复用

`[MVP]` eval_runs 表 + 完整 metrics JSON + 样本级别结果存储实现。
`[V2]` 评估结果时序数据库(支持大规模时序查询);指标实时流式计算。

---

### 8.12 版本对比与可视化

决策 6:按 skill_version + model_id 双维度筛选对比,计算指标差值。

**版本对比逻辑**:

```python
class EvalComparator:
    async def compare_versions(
        self,
        base_version: str,
        target_version: str,
        model_id: str = None
    ) -> dict:
        """对比两个版本的评估结果"""
        
        base_runs = await self.eval_repo.list_runs(
            skill_version=base_version, model_id=model_id, status="completed"
        )
        target_runs = await self.eval_repo.list_runs(
            skill_version=target_version, model_id=model_id, status="completed"
        )
        
        # 基线筛选:默认取同模型、同 Skill 最新成功评估记录
        # 避免跨模型对比无意义指标
        base_metrics = base_runs[-1].metrics if base_runs else None
        target_metrics = target_runs[-1].metrics if target_runs else None
        
        if not base_metrics or not target_metrics:
            raise InsufficientDataError("缺少对比数据")
        
        # 计算差值
        diff = self._compute_diff(base_metrics, target_metrics)
        
        return {
            "base_version": base_version,
            "target_version": target_version,
            "model_id": model_id,
            "base_metrics": base_metrics,
            "target_metrics": target_metrics,
            "diff": diff  # 正数=提升,负数=退化
        }
    
    def _compute_diff(self, base: dict, target: dict) -> dict:
        """计算指标差值,标记退化/提升"""
        diff = {}
        for category in base:
            diff[category] = {}
            for metric in base[category]:
                base_val = base[category].get(metric, 0)
                target_val = target.get(category, {}).get(metric, 0)
                delta = target_val - base_val
                diff[category][metric] = {
                    "delta": delta,
                    "status": "improved" if delta > 0 else ("degraded" if delta < 0 else "stable")
                }
        return diff
```

**UI 可视化(MVP 基础)**:

- **版本趋势折线图**:X 轴为版本号,Y 轴为指标值,展示指标随版本变化趋势
- **版本对比表格**:两版本指标并排展示,差值列高亮(退化标红,提升标绿)
- **样本级别详情**:点击某条样本查看完整 ReAct 轨迹对比(期望 vs 实际)

**MVP 可视化范围**:

- 折线图(版本趋势)
- 表格(版本对比)
- 不做复杂看板(V2)

`[MVP]` 版本对比逻辑 + 基础 UI 图表(折线 + 表格)实现。
`[V2]` 丰富细分看板;指标预警阈值;自定义图表配置。

---

### 8.13 三类更新载体的评估驱动迭代流程

决策 7:前序已实现版本化存储,本章聚焦评估串联迭代链路。

**三类载体迭代流程**:

```
┌─────────────────────────────────────────────────────────────┐
│                    评估驱动迭代闭环                           │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐  │
│  │ Prompt 迭代  │    │ Skills 迭代  │    │ Harness 迭代 │  │
│  │ (7.3 快照)   │    │ (7.3 快照)   │    │ (Git Tag)    │  │
│  └──────┬───────┘    └──────┬───────┘    └──────┬───────┘  │
│         │                   │                   │           │
│         └───────────┬───────┴───────────┬───────┘           │
│                     ↓                   ↓                   │
│              ┌──────────────┐   ┌──────────────┐           │
│              │ 新版本快照    │   │ Git Tag      │           │
│              │ (version_    │   │ (v1.1.0)     │           │
│              │  snapshots)  │   │              │           │
│              └──────┬───────┘   └──────┬───────┘           │
│                     │                  │                   │
│                     ↓                  ↓                   │
│              ┌──────────────────────────────────┐          │
│              │   自动触发回归评估(快速子集)      │          │
│              │   (8.9 版本变更触发)              │          │
│              └──────────────┬───────────────────┘          │
│                             │                              │
│                             ↓                              │
│              ┌──────────────────────────────────┐          │
│              │   评估结果分析(8.12 版本对比)     │          │
│              │   - 与上一版本对比指标             │          │
│              │   - 退化检测                       │          │
│              └──────────────┬───────────────────┘          │
│                             │                              │
│                     ┌───────┴───────┐                      │
│                     ↓               ↓                      │
│              ┌────────────┐  ┌────────────┐               │
│              │ 达标:发布   │  │ 不达标:回滚│               │
│              │ (更新       │  │ (8.14      │               │
│              │  latest_    │  │  回滚机制) │               │
│              │  version)   │  │            │               │
│              └────────────┘  └────────────┘               │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**Prompt 迭代流程**:

1. 修改 `system_prompt.md`(开发期 Git 管理)
2. UI 保存 → 生成 `version_snapshots`(scope="skill_prompt")
3. 自动触发离线快速回归(前 5 条样本)
4. 评估通过 → 更新 `latest_version` 指针
5. 评估不达标 → 告警 + 人工确认是否回滚

> **发布控制说明**:评估指标退化仅生成 UI 告警、写入 `eval_runs` 记录,**不会自动阻断新版本保存/发布**;是否继续上线完全由人工确认,无强制拦截逻辑,降低单人开发流程门槛。

**Skills 迭代流程**:

1. 修改 `skill.yaml` + `system_prompt.md` + `tools.yaml`
2. UI 保存 → 生成完整 Skill 快照
3. 自动触发交互式回放回归(完整 20 条样本,因为涉及工具白名单变更)
4. 评估通过 → 发布
5. 评估不达标 → 回滚到上一版本

**Harness 代码迭代流程**:

1. 修改代码 → Git commit
2. Git Tag 标记版本(如 `v1.1.0`)
3. 手动触发全量回归评估(离线 + 交互式)
4. 评估通过 → 部署(单人开发即重启 Sidecar)
5. 评估不达标 → Git revert(手动)

**自动触发 vs 手动触发**:

| 载体 | 自动触发 | 手动触发 |
|---|---|---|
| Prompt | ✓(快速回归,离线) | ✓(全量评估) |
| Skills | ✓(完整回归,交互式回放) | ✓(全量评估) |
| Harness | ✗(需手动触发全量) | ✓(必选) |

`[MVP]` 三类载体迭代流程 + 自动/手动触发实现。
`[V2]` 全自动化流水线(Git push → 评估 → 自动部署);灰度发布。

---

### 8.14 回滚机制

决策 8:Prompt 独立回滚 + Harness 代码 Git revert;自动回滚 V2。

**Prompt 独立回滚**:

```python
class SkillRollbackManager:
    async def rollback_prompt(
        self,
        skill_name: str,
        target_version: str
    ):
        """仅回滚 Prompt,不影响 Skill 元数据与工具白名单"""
        
        # 1. 从 version_snapshots 读取历史 Prompt
        prompt_snapshot = await self.snapshot_repo.get(
            scope="skill_prompt",
            skill_name=skill_name,
            version=target_version
        )
        
        # 2. 更新 latest_version 指针(仅 prompt)
        await self.skill_repo.update_prompt_version(
            skill_name=skill_name,
            latest_prompt_version=target_version
        )
        
        # 3. 回滚约束:仅对新会话生效(7.3)
        # 当前运行中的会话继续使用锁定版本
```

**Skill 完整回滚**(7.3 已定):

```python
async def rollback_skill(self, skill_name: str, target_version: str):
    """回滚整个 Skill(元数据 + Prompt + 工具白名单)"""
    await self.skill_repo.update_latest_version(skill_name, target_version)
    # 当前会话不受影响,新会话加载回滚后版本
```

**Harness 代码回滚**:

- 单人开发,手动 `git revert <commit>` + 重新部署
- MVP 不实现自动回滚(复杂度高,单人场景手动足够)

**自动回滚(V2 预留)**:

```python
# V2:评估不达标自动回滚
async def auto_rollback_if_degraded(
    self,
    new_version: str,
    baseline_version: str
):
    """新版本评估不达标自动回滚"""
    comparison = await self.comparator.compare_versions(
        baseline_version, new_version
    )
    
    if comparison.has_significant_degradation():
        await self.rollback_skill(skill_name, baseline_version)
        await self.notify_user(f"新版本 {new_version} 评估退化,已自动回滚到 {baseline_version}")
```

**回滚约束**(统一):

- 所有回滚仅对新会话生效(7.3)
- 当前运行中的会话不受影响(避免 Frozen Zone hash 失效)
- 回滚操作记录入 `react_events`(event_type="rollback")供审计

`[MVP]` Prompt 独立回滚 + Skill 完整回滚 + Harness 手动 Git revert 实现。
`[V2]` 自动回滚(评估不达标触发);回滚前自动备份当前版本;回滚影响范围分析。

---

### 8.15 A/B 测试预留

决策 9(A):MVP 仅预留数据表字段,不实现分配逻辑。

**预留设计**:

- `eval_runs.variant` 字段:标记 A/B 变体("A" / "B" / null)
- 数据结构支持未来 A/B 测试:

```sql
-- V2: A/B 测试配置表(预留,MVP 不创建)
CREATE TABLE ab_tests (
    id SERIAL PRIMARY KEY,
    test_name VARCHAR(100) NOT NULL,
    skill_name VARCHAR(50) NOT NULL,
    variant_a_version VARCHAR(20) NOT NULL,
    variant_b_version VARCHAR(20) NOT NULL,
    traffic_split INT DEFAULT 50,  -- B 变体流量百分比
    status VARCHAR(20) DEFAULT 'pending',
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    winner_variant CHAR(1)  -- 'A' / 'B' / null
);
```

**MVP 行为**:

- 评估时 `variant` 字段默认为 null
- 版本对比仅对比两个指定版本,不涉及流量分配
- V2 实现完整 A/B 测试:流量分配 → 并行评估 → 统计显著性检验 → 自动胜出版本

`[MVP]` eval_runs.variant 字段预留。
`[V2]` 完整 A/B 测试框架(配置表 + 流量分配 + 统计检验 + 自动胜出)。

---

### 8.16 持续进化闭环与自动样本扩充

决策 10:基础闭环流程 + MVP 内置低分案例自动提取薄弱用例扩充样本库。

**完整闭环流程**:

```
┌──────────────────────────────────────────────────────────────┐
│                   持续进化闭环                                 │
├──────────────────────────────────────────────────────────────┤
│                                                               │
│   ┌─────────────┐                                            │
│   │ 数据集更新   │ ← 人工编写 + 真实会话提取 + 自动扩充(V2)    │
│   └──────┬──────┘                                            │
│          ↓                                                   │
│   ┌─────────────┐                                            │
│   │ 评估运行     │ ← 手动触发 / 版本变更自动触发              │
│   └──────┬──────┘                                            │
│          ↓                                                   │
│   ┌─────────────┐                                            │
│   │ 结果分析     │ ← 版本对比 + 退化检测                      │
│   └──────┬──────┘                                            │
│          ↓                                                   │
│   ┌─────────────┐                                            │
│   │ 问题定位     │ ← 低分样本分析 + 指标归因                  │
│   └──────┬──────┘                                            │
│          ↓                                                   │
│   ┌─────────────┐                                            │
│   │ 修改优化     │ ← Prompt/Skill/Harness 修改               │
│   └──────┬──────┘                                            │
│          ↓                                                   │
│   ┌─────────────┐                                            │
│   │ 新版本快照   │ ← version_snapshots / Git Tag             │
│   └──────┬──────┘                                            │
│          ↓                                                   │
│   ┌─────────────┐                                            │
│   │ 回归评估     │ ← 自动触发快速回归                         │
│   └──────┬──────┘                                            │
│          ↓                                                   │
│   ┌──────┴───────┐                                           │
│   ↓              ↓                                           │
│ ┌──────┐    ┌──────────┐                                    │
│ │ 发布  │    │ 回滚     │                                    │
│ └──┬───┘    └──────────┘                                    │
│    ↓                                                         │
│ ┌──────────────┐                                            │
│ │ 自动样本扩充  │ ← MVP:从低分案例提取薄弱用例                │
│ │ (MVP 内置)   │ ← V2:用户在线反馈采集                      │
│ └──────┬───────┘                                            │
│        │                                                     │
│        └─────────────────────────────┐                      │
│                                      ↓                      │
│                               回到「数据集更新」              │
│                                                               │
└──────────────────────────────────────────────────────────────┘
```

**自动样本扩充(MVP 内置)**:

```python
class WeakSampleExtractor:
    async def extract_from_low_score_runs(self, threshold: float = 0.6):
        """从低分评估案例中提取薄弱用例,扩充样本库
        
        人工审核筛选标准:
        - 低分原因为模型能力限制 → 丢弃,不加入测试集
        - 低分原因为 Prompt/Skill 逻辑缺陷 → 编辑期望轨迹后入库作为边界样本
        """
        
        # 1. 查找低分样本
        low_score_samples = await self.eval_repo.get_low_score_samples(
            threshold=threshold  # 任务完成率 < 0.6
        )
        
        # 2. 人工审核队列(MVP 不自动入库,需人工确认)
        for sample in low_score_samples:
            await self.review_queue.add({
                "source_run_id": sample.run_id,
                "sample_input": sample.input,
                "actual_output": sample.actual_output,
                "actual_events": sample.events,
                "failure_reason": sample.failure_reason,
                "suggested_as": "boundary"  # 低分案例多为边界用例
            })
        
        # 3. 人工审核通过后,转为正式测试样本
        # (UI 提供审核界面,人工编辑期望轨迹后入库)
```

**人工审核必要性**:

- 低分案例可能因模型能力不足(而非样本问题),直接入库会引入低质量样本
- 人工审核确认:是否为真实薄弱场景 → 编辑期望轨迹 → 入库

**V2 用户在线反馈采集**:

- 会话结束后,UI 展示"这次回答有帮助吗?"反馈按钮
- 负反馈会话自动进入审核队列,作为样本扩充候选
- 正反馈会话作为"真实会话提取"的来源(8.3)

`[MVP]` 闭环流程 + 低分案例自动提取 + 人工审核队列实现。
`[V2]` 用户在线反馈采集;样本自动生成(模型生成 + 人工筛选);闭环全自动化。

---

### 8.17 MVP 与 V2 边界

本章设计按 MVP / V2 边界落地,与 2.16/3.16/4.17/5.18/6.16/7.17 边界表对齐。

**MVP 必须实现**:

| 模块 | MVP 范围 |
|---|---|
| 评估环境 | 离线批量 + 交互式回放(含 Mock 模式)(8.2, 8.10) |
| 数据集 | 每场景 20 条 + train/test 分离 + eval_datasets 表(8.3, 8.4) |
| 指标体系 | 五类指标全量(任务完成/工具准确/LLM-Judge/效率/安全)(8.5-8.8) |
| 评判机制 | 规则 + LLM 混合,GLM-4-Flash Judge(8.8) |
| 评估执行 | 手动触发 + 版本变更自动触发(8.9) |
| 结果存储 | eval_runs 表 + 完整 metrics(8.11) |
| 版本对比 | 双维度筛选 + 差值计算 + UI 基础图表(8.12) |
| 迭代流程 | 三类载体评估驱动闭环(8.13) |
| 回滚机制 | Prompt 独立回滚 + Skill 完整回滚 + Harness 手动(8.14) |
| A/B 预留 | eval_runs.variant 字段(8.15) |
| 持续进化 | 闭环流程 + 低分案例自动提取 + 人工审核(8.16) |

**V2 预留接口**:

| 模块 | V2 范围 | 预留接口 |
|---|---|---|
| 评估环境 | 在线评估(用户反馈) | 反馈采集通道 |
| 数据集 | 样本自动生成、版本管理 | 数据集快照机制 |
| 指标 | 综合评分、权重配置 | metrics JSON 可扩展 |
| 评判 | 场景差异化 Judge、多 Judge 投票 | Judge prompt 可配置 |
| 执行 | 定时执行、CI/CD 集成、并行评估 | 调度器接口可扩展 |
| 可视化 | 丰富看板、预警阈值 | UI 组件可扩展 |
| 迭代 | 全自动化流水线、灰度发布 | 流水线接口可扩展 |
| 回滚 | 自动回滚(评估不达标触发) | auto_rollback 接口预留 |
| A/B 测试 | 完整框架(流量分配 + 统计检验) | ab_tests 表 + variant 字段 |
| 进化 | 用户在线反馈、样本自动生成、全自动化 | 反馈通道 + 生成器接口 |

**与三大约束的对应**:

- 上下文质量优先 → 8.8 LLM-as-Judge 评判主观质量;8.16 低分案例驱动样本扩充,持续提升上下文质量。
- 缓存友好 → 8.13 版本变更触发快速回归子集(仅 5 条),避免全量评估开销;8.10 Mock 模式加速批量评测。
- 评估驱动迭代 → 8.13 三类载体迭代闭环;8.16 持续进化闭环;8.14 回滚机制保障迭代安全。

---

第 8 章起草完成。本章展开了评估与持续进化闭环的完整设计:两类评估环境、黄金样本数据集、五类指标体系、LLM-as-Judge 混合评判、评估执行流程、交互式回放、结果存储与可视化、三类载体迭代流程、回滚机制、A/B 预留、持续进化闭环,共 17 节。

后续章节衔接:

- 第 9 章:MVP 路线与 V2 扩展总结,整合全 8 章的边界表。

---

## 第 9 章 MVP 路线与 V2 扩展

### 9.1 章节定位与整合策略

本章是全书的实施收尾章,核心职责是**整合前 8 章已锁定的 MVP/V2 边界,形成可执行的分阶段实施路线与长期演化规划**。本章不引入任何新的功能、技术方案或接口设计,所有内容均严格复用前 8 章决策。

**本章的三个职责**:

1. **汇总**:横向整合 2-8 章分散的 MVP 必须实现项与 V2 预留接口,形成单一总览视图,便于开发进度追踪与交叉查阅。
2. **排序**:基于模块依赖关系,将 MVP 拆分为 M0-M4 五个阶段,给出单人开发的推荐顺序与关键路径。
3. **守护**:汇总三大约束落地检查表、风险缓解、回滚降级、配置与持久化总览,形成实施过程中的统一查阅入口。

**与前序章节的引用关系**:

- 所有 MVP/V2 边界精确引用到章节小节编号(如 2.16、4.17),不重写底层实现描述。
- 三大约束检查表沿用每章末尾"与三大约束的对应"内容,仅做横向汇总。
- 配置与表结构引用前序章节定义,本章仅整合为完整骨架。

**本章边界**(不做的事):

- 不展开任何模块的实现细节(已在 2-8 章完成)。
- 不引入团队协作、CI/CD 流水线、多租户、集群等服务端实践(单人本地 Electron 桌面 Agent 场景)。
- 不承诺 V2 时间点,仅按"用户价值×实施成本"给出推荐演化顺序。

每节末尾以 `[MVP]` 或 `[V2规划]` 标注实现边界,本章无 V2 代码接口,仅路线规划标记。

`[MVP]` 本章 9.1-9.16 共 16 节均为整合汇总,不新增设计;V2 路线规划仅给出推荐顺序与依赖,不承诺实施时间。

---

### 9.2 MVP 完整模块清单(整合表)

横向汇总 2-8 章所有 MVP 必须实现项,按四层架构(UI 层、编排层、能力层、持久层)分组,每项标注对应章节号便于交叉查阅。

**UI 层(Electron 前端)**:

| 模块 | MVP 范围 | 对应章节 |
|---|---|---|
| 桌面 GUI 框架 | Electron + React + TypeScript 全量搭建 | 2.1、2.15 |
| 通信客户端 | HTTP 控制面 + WS 数据面客户端 | 2.3 |
| 会话视图 | chat 组件 + WS 事件消费 + ws_offset 上报 | 2.3、2.16 |
| ReAct 步骤渲染 | thinking/tool_call/tool_result/final 流式展示 | 2.4、6.10 |
| Skills 管理面板 | 选择/切换/版本回滚 UI | 7.3、7.4 |
| 评估面板 | 评估运行 + 结果图表 + 版本对比 | 8.11、8.12 |
| 配置面板 | 模型/记忆/知识库/沙箱/工具配置 UI | 2.12、4.4、4.10、5.4、6.14 |
| 异常分类展示 | 四类异常 UI 区分 + 降级提示 | 2.14 |
| 图片预览卡片 | 沙箱生成的图表文件预览 | 7.12 |

**编排层(Python Sidecar 核心)**:

| 模块 | MVP 范围 | 对应章节 |
|---|---|---|
| 四层骨架 | UI/编排/能力/持久全量分层 | 2.1 |
| 进程模型 | 单 Sidecar + 固定 2 Worker + 跨平台 spawn/fork | 2.2 |
| 通信协议 | HTTP 控制面 + WS 数据面 + 事件补发 + ws_offset | 2.3 |
| ReAct 核心循环 | 单 Agent 完整循环 + 状态机 + 错误降级 | 2.4 |
| 轻量自研框架边界 | 自研循环 + 借用工具抽象,明确分工 | 2.5 |
| 流式输出/工具调度/CPU offload | asyncio 协程模型 + Worker 进程池 | 2.6 |
| 模型统一接口 | 四家适配器(GLM/DeepSeek/Agnes/KIMI) + capability 降级 | 2.7 |
| KV Cache 约束 | 分区模型(Frozen/Stable/Active) + SHA-256 hash 校验 | 2.8 |
| 模型路由 | ManualRouter(UI 手动选择) + Protocol 就位 | 2.9 |
| 异常处理 | 四类异常(模型/工具/进程/用户) + 降级 + checkpoint 存储 | 2.14 |
| API Key 加密 | AES-256-GCM 机器 ID 派生 + UI 录入 + config_runtime 存储 | 2.7、2.12 |
| 上下文管理器 | 全部职责无状态设计 + 分区元数据 + 构建流水线 | 3.1-3.3 |
| hash 校验 | SHA-256 + canonical JSON + 会话维度合并标志 | 3.4 |
| 状态栏机制 | 时间戳/偏好/会话元信息 + 用户消息轮注入 | 3.5、3.6 |
| 模板变量体系 | 五类命名空间 + `{{var}}` 解析 + `kb_replace_mode=false` | 3.7 |
| 工具描述规范 | OpenAI schema + 扩展字段(category/safety_level/timeout) | 3.8 |
| 压缩触发矩阵 | 三种条件 + 激进模式 | 3.9 |
| 三类压缩策略 | 滑动窗口(全局配对映射) + 摘要 + Stable 合并 | 3.10 |
| 压缩模型选型 | 可配置 + 适配器复用 + 计费隔离 | 3.11 |
| 注入防护 | 三层防护 + 中英文 + 高低风险分级 + 沙箱差异化 | 3.12 |
| 计费感知 | token 记录 + 多币种 + 价格快照 + 三类成本分类 | 3.13 |
| TokenEstimator | 公共工具 + 分词器注册 + 消息时序规则 | 3.14 |
| 持久层交互 | 全部读写路径 + 软删除 + 快照 | 3.15 |

**能力层(知识库/工具/沙箱/场景 Skills)**:

| 模块 | MVP 范围 | 对应章节 |
|---|---|---|
| 用户记忆策略 | LLM 摘要提取(每 8 轮 + 会话结束 + UI 手动触发) | 4.2 |
| 用户记忆存储 | 结构化条目 + 四类 type + importance | 4.3 |
| 记忆淘汰与合并 | 数量上限 + 低重要性超期淘汰 + 软删除 + 阈值可配置 | 4.4 |
| 记忆注入 | top 10 注入 Stable Zone + 访问记录 | 4.5 |
| 文档处理流水线 | 端到端 + Worker 纯计算 + 云端降级 | 4.6 |
| 文档类型识别 | Markdown/PDF/Code/Plain 四类 | 4.7 |
| chunking 策略 | 三类语义 + 固定长度兜底 | 4.8 |
| chunk 参数配置 | 三类模板 + 运行时覆盖 | 4.9 |
| Embedding 模型 | bge-m3 本地 + Worker 集成 + 云端降级 + bge-small 轻量备选 + 内存自动切换 + LRU query 缓存 | 4.10 |
| HNSW 索引 | m=16/ef_construction=128/ef_search=64 可调 | 4.11 |
| kb_chunks 表 | 统一表 + 四类索引 + metadata | 4.12 |
| 混合检索 | 向量 + 关键词 + RRF 融合(k=60) | 4.13 |
| Reranker | bge-reranker 重排 + 降级 + 异常入库告警 | 4.14 |
| Agentic RAG 工具 | search_knowledge + min_similarity + 分页 + Stable Zone 注入 + 片段计数触发合并 + 场景强制过滤 | 4.15 |
| 知识库增量更新 | 文档变更增量重算 + 批量快照 | 4.16 |
| 双轨工具架构 | 内置 + MCP 统一调度 | 5.1、5.2 |
| MCP Client | stdio + HTTP 双传输 + 连接管理 | 5.3 |
| MCP 配置 | 混合配置 + UI CRUD + stdio/ping + HTTP/health 双探活 | 5.4 |
| 工具发现与加载 | 按 Skill 加载 + 会话隔离 + 跨 Skill 权限隔离 | 5.5、7.5 |
| 通用工具集 | 9 类工具(search_knowledge/file_read/file_write/http_request/web_search/calculator/datetime/code_execution/read_artifact) | 5.6-5.11 |
| 权限确认 | 三级分级(safe/elevated/dangerous) + WS 确认 + 会话缓存 + cache_key 含 skill_name | 5.12、7.5 |
| 超时重试 | 分类超时(基础 30s + 分类差异化) + 指数退避 3 次 + 用户中断 | 5.13 |
| 异步事件 | 长任务 + webhook + 流式结果 | 5.14 |
| Artifact 机制 | 截断 + 文件存储 + read_artifact | 5.15 |
| 安全机制 | 白名单 + 审计 + 资源限额 + 信号量 | 5.16 |
| 沙箱架构 | 独立模块 + 透明接口(参考 Trae Code 设计,运行时独立) | 6.1、6.2 |
| 隔离模型 | 子进程模式 + 会话工作目录隔离 | 6.3、6.4 |
| 语言支持 | Python + JavaScript | 6.5 |
| 文件工作记忆 | 跨轮次持久 + artifact + 只读/可写 + outputs/ 图表 | 6.6、7.12 |
| 资源限制 | 超时 300s + 内存 512MB + 磁盘 100MB + 禁网络 | 6.7 |
| 安全边界 | 三层兜底(预扫描 + 路径过滤 + 资源限制) + 环境变量脱敏 | 6.8 |
| 执行流程 | 端到端 + 错误码映射 | 6.9 |
| 流式输出 | stdout/stderr 流式 + 文件列表 + 2k artifact(沙箱专用阈值) | 6.10 |
| 工具协作 | 串行复用 + 文件互通 | 6.11 |
| 失败恢复 | 崩溃保留文件 + 返回已生成文件 | 6.12 |
| 事件记录 | react_events + artifact 溢出 | 6.13 |
| 跨平台 | Windows/macOS/Linux 差异化处理(spawn/fork + 资源限制能力差异兜底) | 6.15 |
| Skill 目录结构 | 三场景独立目录 + Git 管理 | 7.1 |
| 元数据 schema | skill.yaml 完整字段 + max_frozen_token + 校验 | 7.2 |
| 版本管理 | Git + PG 快照 + 会话锁定 + UI 回滚 + 重启版本加载规则 | 7.3 |
| 加载激活 | UI 手动选择 + Frozen Zone 构建 + SkillNotFoundError 兜底 | 7.4 |
| Prompt 框架 | 四段式(角色/约束/工具/输出) + 模板变量 + 完整落地模板 | 7.6 |
| 少样本机制 | 2-3 个 markdown 示例 + Frozen Zone 注入 + train/test 拆分 | 7.7、7.16 |
| 办公-文档处理 | Excel/Word 沙箱处理 + 超大文件分块读取 | 7.8、7.9 |
| 办公-网页调研 | web_search + 沙箱抓取 + 来源标注 | 7.10、7.11 |
| 数据分析 | pandas/matplotlib/scipy 全栈 + 图表输出 | 7.12 |
| 前端-代码生成 | HTML/React/Vue 生成 | 7.13、7.14 |
| 前端-知识库 | 设计系统 RAG + scenario 过滤 + 增量更新 | 7.15 |
| 评估支持 | Skill 示例作为黄金样本 + 场景化指标 | 7.16 |

**持久层(Postgres + 内存)**:

| 模块 | MVP 范围 | 对应章节 |
|---|---|---|
| Postgres Schema | 全部表(sessions/messages/react_events/user_memories/kb_chunks/kb_documents/version_snapshots/eval_datasets/eval_runs/async_tasks/config_runtime/skills) | 2.10 |
| 桌面端内嵌运维 | 磁盘分级告警(1.5GB/2GB/3GB) + TTL 清理 + VACUUM 调度 | 2.10 |
| Skills/Prompt 混合存储 | 文件系统(开发) + PG(运行时) 三层流转 | 2.11 |
| 配置分层管理 | 静态 yaml + config_runtime 运行时 | 2.12 |
| 可观测性 | 结构化 JSON 日志 + ReAct 事件入库 + trace_id 预留 + 自动清理 | 2.13 |
| 评估环境 | 离线批量 + 交互式回放(含 Mock 模式) | 8.2、8.10 |
| 数据集 | 每场景 20 条 + train/test 分离 + eval_datasets 表 + CHECK 约束 + Pydantic 校验 | 8.3、8.4 |
| 指标体系 | 五类指标(任务完成/工具准确/LLM-Judge/效率/安全)全量 | 8.5-8.8 |
| 评判机制 | 规则 + LLM 混合 + GLM-4-Flash Judge + 通用 Prompt | 8.8 |
| 评估执行 | 手动触发 + 版本变更自动触发 + mock_enabled 标记 | 8.9、8.11 |
| 结果存储 | eval_runs 表 + 完整 metrics | 8.11 |
| 版本对比 | 双维度筛选 + 差值计算 + UI 基础图表 + 基线自动筛选规则 | 8.12 |
| 迭代流程 | 三类载体(Prompt/Skills/Harness)评估驱动闭环 + 退化仅告警不自动阻断 | 8.13 |
| 回滚机制 | Prompt 独立回滚 + Skill 完整回滚 + Harness 手动 | 8.14 |
| A/B 预留 | eval_runs.variant 字段 | 8.15 |
| 持续进化 | 闭环流程 + 低分案例自动提取 + 人工审核 + 两类筛选标准 | 8.16 |

`[MVP]` 全表为 MVP 必须实现范围,开发进度追踪以本表为单一事实源。

---

### 9.3 V2 扩展完整清单(整合表)

横向汇总 2-8 章所有 V2 预留接口,按扩展类型分类,标注预留接口位置与依赖关系,便于长期演化规划。

**性能增强类**:

| 模块 | V2 范围 | 预留接口 | 依赖关系 | 对应章节 |
|---|---|---|---|---|
| GPU 加速 | bge-m3/bge-reranker GPU 推理 | Worker 进程模型加载逻辑可扩展 device 参数 | 依赖 4.10 Worker 集成 | 4.17 |
| 沙箱池化 | 进程/容器复用 | 执行器接口支持池化 | 依赖 6.3 子进程模式 | 6.16 |
| 多会话并行 | WS 背压控制、多路复用 | ws_handler 设计已支持 session_id 路由 | 依赖 2.3 通信协议 | 2.16 |
| 增量构建 | 仅重建变化部分 | ContextManager.build() 支持 incremental=True 参数 | 依赖 3.3 构建流水线 | 3.16 |
| 跨会话 cache 复用 | 同 Skill 会话共享 Frozen Zone hash | hash 计算逻辑已隔离,可扩展为跨会话 key | 依赖 3.4 hash 校验 | 3.16 |
| 精确分词器 | 每家模型专用分词器 | TokenEstimator.register_tokenizer 接口已定义 | 依赖 3.14 TokenEstimator | 3.16 |
| 依赖缓存 | pip install 持久化 | 工作目录支持虚拟环境 | 依赖 6.4 工作目录 | 6.16 |

**能力新增类**:

| 模块 | V2 范围 | 预留接口 | 依赖关系 | 对应章节 |
|---|---|---|---|---|
| 多 Agent 协作 | DELEGATING 状态、子 Agent 委托 | Agent.state_machine 扩展点 + core/multi_agent/ 目录占位 | 依赖 2.4 状态机 + 2.16 目录 | 2.16 |
| 模型路由 | TagBasedRouter / CostAwareRouter | Router Protocol + 配置注入机制 | 依赖 2.9 路由抽象 | 2.16 |
| Agentic Memory | Agent 主动 remember/recall | 工具定义预留,ToolDef schema 可扩展 | 依赖 4.2 记忆策略 + 3.8 工具规范 | 4.17 |
| 递归/模型分块 | 更精细的语义分块 | DocumentProcessor 支持新策略枚举 | 依赖 4.8 chunking | 4.17 |
| 自适应调参 | 基于检索反馈调整 chunk_size | 评估数据可反向输入参数调优 | 依赖 4.9 chunk 参数 + 8.11 评估数据 | 4.17 |
| 多 embedding 路由 | 按文档语言选择模型 | EmbeddingService 支持多模型注册 | 依赖 4.10 Embedding 模型 | 4.17 |
| 加权融合 | 可配置向量/关键词权重 | RRF 可替换为加权融合策略 | 依赖 4.13 混合检索 | 4.17 |
| 查询重写 | 模型扩展查询词 | 检索前可插入重写步骤 | 依赖 4.13 混合检索 | 4.17 |
| kb_replace_mode=true | 运行时替换 KB 占位符 | 配置开关已定义(3.7),替换逻辑在 TemplateResolver 扩展 | 依赖 3.7 模板变量 + 4.15 注入 | 3.16、4.17 |
| 工具市场 | 动态安装 MCP server | MCP 配置 UI 已支持,安装逻辑可扩展 | 依赖 5.4 MCP 配置 | 5.18 |
| 数据库查询 | 直接连 PostgreSQL | ToolDef schema 已定义,实现可扩展 | 依赖 5.6 通用工具集 | 5.18 |
| Shell 执行 | 系统命令(高风险) | dangerous 列表已支持 | 依赖 5.12 权限分级 | 5.18 |
| 多版本工具 | 同工具不同版本共存 | ToolDef 已有 version 字段(3.8) | 依赖 3.8 工具规范 | 5.18 |
| 长任务暂停/恢复 | 任务 DAG 编排 | async_tasks 表已支持状态扩展 | 依赖 5.14 异步事件 | 5.18 |
| 容器隔离 | Docker 后端 | SandboxExecutor 抽象基类,可切换实现 | 依赖 6.3 隔离模型 | 6.16 |
| 远程沙箱 | E2B 云服务 | 接口已抽象,可扩展远程后端 | 依赖 6.3 隔离模型 | 6.16 |
| 多语言沙箱 | Shell/Rust/Go | languages 配置可扩展 | 依赖 6.5 语言支持 | 6.16 |
| Skill 市场 | 导入/导出/分享 | 目录结构可扩展 | 依赖 7.1 Skill 目录 | 7.17 |
| Skill 继承 | 基础 Skill 派生 | 元数据 schema 可扩展 extends 字段 | 依赖 7.2 元数据 schema | 7.17 |
| 语义自动路由 | 自动选择 Skill | 加载器接口可扩展 | 依赖 7.4 加载激活 | 7.17 |
| 办公扩展 | 日历/邮件/IM/任务 | MCP 管理面板(5.4) | 依赖 5.4 MCP 配置 | 7.17 |
| 数据分析扩展 | SQL 查询、大数据引擎 | db_query 工具(5.6 V2) | 依赖 5.6 通用工具集 | 7.17 |
| 前端扩展 | 浏览器预览、Figma、截图 | Electron Webview / MCP | 依赖 5.3 MCP Client | 7.17 |

**自动化提升类**:

| 模块 | V2 范围 | 预留接口 | 依赖关系 | 对应章节 |
|---|---|---|---|---|
| 断点续传 | 进程崩溃后恢复 ReAct | react_events 表已支持 + react_loop 预留 checkpoint hook | 依赖 2.14 checkpoint 机制 | 2.16 |
| otel 链路追踪 | 分布式 trace | 日志与事件流 trace_id / span_id 字段预留 | 依赖 2.13 可观测性 | 2.16 |
| Skills 热加载 | 会话进行中切换 Skill | skills/loader.py 预留热加载接口(需配合 cache 失效) | 依赖 2.11 Skills 存储 + 2.8 KV Cache | 2.16 |
| 智能合并 | 语义相似度合并记忆 | memories_repo 已支持批量查询,合并逻辑可扩展 | 依赖 4.4 记忆淘汰 | 4.17 |
| 知识库版本对比 | diff 可视化 | 快照已存储,对比逻辑可扩展 | 依赖 4.16 增量更新 | 4.17 |
| 知识库回滚 | 恢复到历史版本 | 快照 + 软删除支持回滚 | 依赖 4.16 增量更新 | 4.17 |
| 动态权限学习 | 频繁批准的工具降级为 safe | 权限缓存可扩展为持久化学习 | 依赖 5.12 权限确认 | 5.18 |
| Artifact 索引 | 可搜索历史 artifact | 文件命名规则已支持,索引可扩展 | 依赖 5.15 Artifact 机制 | 5.18 |
| 自适应超时 | 基于历史执行时间 | 事件日志已记录 duration_ms | 依赖 5.13 超时重试 | 5.18 |
| 代码沙箱化 | RestrictedPython | Python AST 改写 | 依赖 6.8 安全边界 | 6.16 |
| 环境快照 | 保存/恢复 | SnapshotManager 接口预留 | 依赖 6.4 工作目录 | 6.16 |
| 交互式终端 | 运行时输入 | 流式协议可扩展双向 | 依赖 6.10 流式输出 | 6.16 |
| 文件版本管理 | git-like | 工作目录可扩展版本控制 | 依赖 6.4 工作目录 | 6.16 |
| 执行轨迹可视化 | 时间线 | 事件已记录,可视化可扩展 | 依赖 6.13 事件记录 | 6.16 |
| 版本管理 A/B | 测试、灰度发布 | version_snapshots 支持多版本并存 | 依赖 7.3 版本管理 | 7.17 |
| 少样本 RAG | 检索式少样本 | 示例库 + search_knowledge | 依赖 7.7 少样本 + 4.15 Agentic RAG | 7.17 |
| 评估自动化 | 流水线、质量评分 | 评估接口可扩展 | 依赖第 8 章评估闭环 | 7.17 |
| 在线评估 | 用户反馈采集 | 反馈采集通道 | 依赖 8.2 评估环境 | 8.17 |
| 样本自动生成 | 模型生成 + 人工筛选 | 数据集快照机制 | 依赖 8.3 数据集 | 8.17 |
| 综合评分 | 权重配置 | metrics JSON 可扩展 | 依赖 8.5 指标体系 | 8.17 |
| 场景差异化 Judge | 按 scenario 加载独立 Prompt | judge_prompt_dir 配置预留 | 依赖 8.8 LLM-as-Judge | 8.17 |
| 多 Judge 投票 | 多模型评判 | Judge prompt 可配置 | 依赖 8.8 LLM-as-Judge | 8.17 |
| 定时执行 | CI/CD 集成、并行评估 | 调度器接口可扩展 | 依赖 8.9 评估执行 | 8.17 |
| 全自动化流水线 | 灰度发布 | 流水线接口可扩展 | 依赖 8.13 迭代流程 | 8.17 |
| 自动回滚 | 评估不达标触发 | auto_rollback 接口预留 | 依赖 8.14 回滚机制 | 8.17 |
| A/B 测试完整框架 | 流量分配 + 统计检验 | ab_tests 表 + variant 字段 | 依赖 8.15 A/B 预留 | 8.17 |

**隔离升级类**:

| 模块 | V2 范围 | 预留接口 | 依赖关系 | 对应章节 |
|---|---|---|---|---|
| 内核级隔离 | seccomp/apparmor | Linux 增强安全模块 | 依赖 6.8 安全边界 | 6.16 |
| 运行时路径拦截 | LD_PRELOAD | 动态链接库注入 | 依赖 6.8 安全边界 | 6.16 |
| Windows 内存限制 | Job Object API | 资源限制接口可扩展 | 依赖 6.7 资源限制 + 6.15 跨平台 | 6.16 |
| 打包内嵌 Python | Electron + pyinstaller | Sidecar 启动协议不变,仅打包方式调整 | 依赖 2.2 进程模型 | 2.16 |

**可视化增强类**:

| 模块 | V2 范围 | 预留接口 | 依赖关系 | 对应章节 |
|---|---|---|---|---|
| 重要性裁剪 | 基于模型/规则判断消息重要性 | _apply_compression 支持新策略枚举 | 依赖 3.10 三类压缩 | 3.16 |
| 结构化提取 | 从对话提取决策/事实/待办 | compressed_from 字段已支持溯源 | 依赖 3.10 三类压缩 | 3.16 |
| 按需状态栏 | 模型主动调用 get_status | 状态栏内容 schema 已定义,可作为工具暴露 | 依赖 3.5 状态栏 | 3.16 |
| 输出层注入校验 | 检测模型输出被操纵 | react_events 已记录注入告警,可扩展输出检测 | 依赖 3.12 注入防护 | 3.16 |
| 预算上限 | 超限自动压缩/停止 | token_usage 事件已分类记录,可扩展阈值检查 | 依赖 3.13 计费感知 | 3.16 |
| 本地压缩模型 | 本地小模型做压缩 | 适配器层统一,本地模型作为新适配器接入 | 依赖 3.11 压缩模型 | 3.16 |
| 评估看板 | 丰富看板、预警阈值 | UI 组件可扩展 | 依赖 8.12 版本对比 | 8.17 |
| 工具来源标记 | 评估分析用 | ToolDef 已有 source 字段(内部标记) | 依赖 3.8 工具规范 | 5.18 |

`[V2规划]` 全表为 V2 预留接口清单,本章仅规划不实施;每个 V2 项必须在前序章节已有"空实现"或"Protocol 定义"形式存在(见 9.11 边界守护原则)。

---

### 9.4 分阶段实施里程碑(M0-M4)

基于四层架构依赖关系,将 MVP 拆分为 5 个阶段,每阶段定义完成标志(Done Criteria)与前置依赖章节索引。

**阶段划分总览**:

| 阶段 | 名称 | 核心目标 | 前置依赖章节 |
|---|---|---|---|
| M0 | 基础骨架 | 四层架构 + 进程模型 + 通信协议 + 持久层表结构 | 第 2 章(2.1、2.2、2.3、2.10、2.12、2.15) |
| M1 | 编排核心 | ReAct 循环 + 上下文工程 + 模型适配 | 第 2 章(2.4-2.9、2.13、2.14)+ 第 3 章(3.1-3.15) |
| M2 | 能力层 | 知识库 RAG + 工具层 + 沙箱代码执行 | 第 4 章(4.1-4.16)+ 第 5 章(5.1-5.17)+ 第 6 章(6.1-6.15) |
| M3 | 场景化 | 三场景 Skills(办公/数据分析/前端设计) | 第 7 章(7.1-7.16) |
| M4 | 评估闭环 | 评估环境 + 数据集 + 指标 + 迭代流程 | 第 8 章(8.1-8.16) |

**M0 基础骨架**:

**目标**:搭建可运行的最小骨架,前后端能通信、Postgres 能读写、配置能加载。

**实施范围**:
- Electron + React 前端框架搭建(2.1、2.15)
- Python Sidecar 进程模型 + Worker 进程池(2.2,含跨平台 spawn/fork 备注)
- HTTP 控制面 + WS 数据面通信协议(2.3,含 ws_offset 补发)
- Postgres 全部表结构创建(2.10,sessions/messages/react_events/user_memories/kb_chunks/kb_documents/version_snapshots/eval_datasets/eval_runs/async_tasks/config_runtime/skills)
- 磁盘分级告警 + TTL 清理 + VACUUM 调度(2.10)
- config.yaml 静态配置 + config_runtime 运行时配置(2.12),含 mcp.protocol_version/cache_ttl_ms/enable_server_discover 三字段(9.13,MVP 锁定 2025-11-25)
- API Key 加密存储 AES-256-GCM(2.7、2.12)
- 结构化 JSON 日志 + trace_id 预留(2.13)
- 代码目录结构全量建立(2.15,含 tests/ 测试目录)

**Done Criteria**:
1. Electron 启动后能拉起 Python Sidecar,WS 连接建立成功
2. Postgres 全部表创建成功,可插入/查询基础数据
3. config.yaml 加载成功,API Key 加密存储可读写
4. 磁盘告警在 1.5GB/2GB/3GB 三级阈值触发 UI 提示
5. 日志写入本地文件 + stdout,含 trace_id 字段(留空)

**前置依赖章节索引**:2.1、2.2、2.3、2.7、2.10、2.12、2.13、2.15

---

**M1 编排核心**:

**目标**:ReAct 循环跑通,四家模型可调用,上下文工程完整就位。

**实施范围**:
- ReAct 核心循环 + 状态机(IDLE/THINKING/ACTING/OBSERVING/ERROR)(2.4)
- 轻量自研框架边界界定(2.5)
- asyncio 协程模型 + 流式输出 + 工具调度 + CPU offload(2.6)
- 四家模型适配器(GLM/DeepSeek/Agnes/KIMI)+ capability 降级(2.7)
- KV Cache 分区模型(Frozen/Stable/Active)+ SHA-256 hash 校验(2.8)
- ManualRouter + Router Protocol(2.9)
- 异常分类体系(模型/工具/进程/用户)+ 降级策略 + checkpoint 存储(2.14)
- 上下文管理器全部职责(3.1)
- 分区元数据模型(3.2)
- 构建流水线(启动构建 + 每轮构建)(3.3)
- hash 校验(会话维度合并标志)(3.4)
- 状态栏机制(3.5、3.6)
- 模板变量体系(五类命名空间 + `{{var}}` 解析 + `kb_replace_mode=false`)(3.7)
- 工具描述规范(OpenAI schema + 扩展字段)(3.8)
- 压缩触发矩阵(3.9)
- 三类压缩策略(滑动窗口 + 摘要 + Stable 合并)(3.10)
- 压缩模型选型(3.11)
- 注入防护(三层 + 中英文 + 高低风险分级 + 沙箱差异化)(3.12)
- 计费感知(token 记录 + 多币种 + 价格快照 + 三类成本)(3.13)
- TokenEstimator(3.14)
- 持久层交互(3.15)

**Done Criteria**:
1. 用户发送消息,ReAct 循环完整执行(thinking→tool_call→tool_result→final),前端 WS 流式渲染
2. 四家模型(GLM/DeepSeek/Agnes/KIMI)均可成功调用,某家不可用时降级到备选
3. 会话启动构建 Frozen/Stable/Active 三区,hash 校验通过
4. 长会话触发压缩(任一条件),压缩后 hash 重新计算通过
5. 注入防护拦截中英文高危输入,告警入 react_events
6. 用户主动取消触发 checkpoint 存储,会话标记 interrupted
7. token 计费按对话/压缩/embedding 三类分别记录

**前置依赖章节索引**:2.4-2.9、2.13、2.14、3.1-3.15

---

**M2 能力层**:

**目标**:Agent 具备知识检索、工具调用、代码执行三大核心能力。

**实施范围**:
- 第 4 章全部(4.1-4.16):用户记忆 + 知识库 RAG 全栈
  - 含 4.10 bge-small 轻量备选 + 内存自动切换 + LRU query 缓存
  - 含 4.15 search_knowledge + min_similarity + 分页 + 片段计数触发合并
- 第 5 章全部(5.1-5.17):双轨工具架构 + MCP 集成 + 9 类通用工具
  - 含 5.3 MCP 锁定 2025-11-25(不实现双协议分发,V2)+ 5.12 AuthProtocol stub + 5.14 TasksExtensionAdapter stub(MCP 2026-07-28 兼容)
  - 含 5.4 stdio/ping + HTTP/health 双探活
  - 含 5.12 权限缓存 cache_key 含 skill_name
- 第 6 章全部(6.1-6.15):沙箱代码执行
  - 含 6.8 三层安全边界 + 环境变量脱敏
  - 含 6.15 跨平台 spawn/fork + Windows 资源限制兜底

**Done Criteria**:
1. 知识库文档(Markdown/PDF/Code/Plain)端到端处理入库,bge-m3 embedding + HNSW 索引可用
2. search_knowledge 工具调用返回结果,RRF 融合 + reranker 重排生效,min_similarity 过滤低分 chunk
3. 低配置环境(<6GB 可用内存)自动切换 bge-small,切换时 HNSW 索引重建成功
4. 9 类通用工具均可调用,MCP 工具(stdio + HTTP)双探活通过
5. 沙箱执行 Python/JavaScript 代码,stdout/stderr 流式输出,超 2k token 走 artifact
6. 沙箱资源限制生效(300s 超时 + 512MB 内存 + 100MB 磁盘 + 禁网络)
7. 危险代码预扫描告警入 react_events,环境变量脱敏后 Agent 代码无法读取 API Key
8. 用户记忆每 8 轮 + 会话结束 + UI 手动触发三种方式均可提取,注入 Stable Zone

**前置依赖章节索引**:4.1-4.16、5.1-5.17、6.1-6.15

---

**M3 场景化**:

**目标**:三场景 Skills(办公/数据分析/前端设计)可独立运行,覆盖首批落地需求。

**实施范围**:
- 第 7 章全部(7.1-7.16):场景 Skills 设计
  - 7.1-7.7 底层通用基础设施(目录/元数据/版本/加载/工具共享/Prompt/少样本)
  - 7.8-7.9 办公-文档处理(含超大文件分块读取)
  - 7.10-7.11 办公-网页调研
  - 7.12 数据分析(含图表输出 + 前端预览链路)
  - 7.13-7.14 前端-代码生成
  - 7.15 前端-知识库(设计系统 RAG + scenario 过滤)
  - 7.16 评估支持(含 train/test 拆分)
  - 含 7.2 max_frozen_token 配置
  - 含 7.3 重启版本加载规则
  - 含 7.4 SkillNotFoundError 兜底
  - 含 7.5 权限 cache_key 完整伪代码
  - 含 7.6 办公 Skill 完整 system_prompt.md 模板

**Done Criteria**:
1. 三场景 Skills 目录结构 + skill.yaml 元数据 + Git 版本管理就位
2. UI 选择 Skill 后会话锁定,运行中切换被拒绝并提示
3. 办公场景:Excel/Word 文档处理 + 网页调研 + 来源标注,超大文件(>10000 行或 >50MB)自动分块读取
4. 数据分析场景:pandas + matplotlib + scipy 全栈可用,图表存入 outputs/ 目录,前端渲染预览卡片
5. 前端设计场景:HTML/React/Vue 代码生成 + 设计系统 RAG 检索 + scenario 过滤生效
6. Skill 不存在时返回 UI 友好错误,跳转选择页
7. 权限缓存 cache_key 含 skill_name,不同 Skill 同工具权限不互相覆盖
8. 少样本示例注入 Frozen Zone,train/test 拆分规则生效

**前置依赖章节索引**:7.1-7.16

---

**M4 评估闭环**:

**目标**:评估环境 + 数据集 + 指标 + 迭代流程完整就位,支持持续进化。

**实施范围**:
- 第 8 章全部(8.1-8.16):评估与持续进化闭环
  - 8.2 两类评估环境(离线批量 + 交互式回放)+ 缓存影响说明
  - 8.3-8.4 数据集 + eval_datasets 表 + CHECK 约束 + Pydantic 校验
  - 8.5-8.8 五类指标 + LLM-as-Judge(GLM-4-Flash)+ 通用 Prompt
  - 8.9 评估执行(手动 + 版本变更自动触发)
  - 8.10 交互式回放 + Mock 数据 + 跟随 Skill 版本同步
  - 8.11 eval_runs 表 + mock_enabled 字段
  - 8.12 版本对比 + 基线自动筛选规则
  - 8.13 三类载体迭代闭环 + 退化仅告警不自动阻断
  - 8.14 回滚机制(Prompt/Skill/Harness)
  - 8.15 A/B 预留
  - 8.16 持续进化 + 低分案例自动提取 + 人工审核 + 两类筛选标准

**Done Criteria**:
1. 离线批量评估可执行,每场景 20 条样本(train/test 分离),规则校验 + LLM-as-Judge 双评判
2. 交互式回放可重建会话,Mock 数据按工具名一一对应,Skill 回滚时 mock 数据同步加载
3. eval_runs 表记录完整 metrics,含 mock_enabled 标记
4. 版本对比双维度筛选(同模型 + 同 Skill 最新成功基线),差值计算 + UI 图表展示
5. Prompt/Skill/Harness 三类载体迭代闭环跑通,退化时 UI 告警 + eval_runs 记录,不自动阻断发布
6. Skill 回滚后新会话加载 latest_version,持续在线会话维持锁定版本
7. 低分案例自动提取,人工审核队列支持两类筛选标准(模型能力限制丢弃 / Prompt 缺陷编辑后入库)
8. expected_react_trace 入库前通过 Pydantic 校验,非法结构抛出样本格式错误

**前置依赖章节索引**:8.1-8.16

`[MVP]` M0-M4 五阶段为 MVP 完整实施范围,每阶段 Done Criteria 为验收标准。
`[V2规划]` 9.3 V2 清单中的所有项不纳入 M0-M4,仅在 MVP 完成后按 9.10 优先级启动。

---

### 9.5 模块依赖关系图(DAG)

绘制 MVP 模块的依赖有向无环图(DAG),标注关键路径,辅助开发顺序决策。节点为模块,边为"被依赖→依赖"(上游→下游)。

**层级依赖 DAG(顶层)**:

```
┌─────────────────────────────────────────────────────────────────┐
│  M0 基础骨架层                                                   │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐         │
│  │ 四层骨架 │→ │ 进程模型 │→ │ 通信协议 │→ │ Postgres │         │
│  │  (2.1)   │  │  (2.2)   │  │  (2.3)   │  │  (2.10)  │         │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘         │
│       │             │             │             │                │
│       │             │             │             ↓                │
│       │             │             │      ┌──────────────┐       │
│       │             │             │      │ 配置分层     │       │
│       │             │             │      │   (2.12)     │       │
│       │             │             │      └──────────────┘       │
│       │             │             │             │                │
│       │             │             │             ↓                │
│       │             │             │      ┌──────────────┐       │
│       │             │             │      │ 可观测性     │       │
│       │             │             │      │   (2.13)     │       │
│       │             │             │      └──────────────┘       │
└───────┼─────────────┼─────────────┼─────────────┼────────────────┘
        │             │             │             │
        ↓             ↓             ↓             ↓
┌─────────────────────────────────────────────────────────────────┐
│  M1 编排核心层                                                   │
│  ┌──────────┐                                                   │
│  │  ReAct   │←── 依赖:进程模型(2.2)+ 通信协议(2.3)         │
│  │  (2.4)   │                                                   │
│  └────┬─────┘                                                   │
│       │                                                         │
│       ↓                                                         │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │
│  │ 模型适配     │  │ 上下文管理器 │  │  异常处理    │          │
│  │   (2.7)      │  │   (3.1-3.15) │  │   (2.14)     │          │
│  └──────┬───────┘  └──────┬───────┘  └──────────────┘          │
│         │                 │                                     │
│         ↓                 ↓                                     │
│  ┌──────────────┐  ┌──────────────┐                            │
│  │ KV Cache 约束│  │ 压缩策略     │                            │
│  │   (2.8)      │←─│   (3.10)     │                            │
│  └──────────────┘  └──────────────┘                            │
└─────────────────────────────────────────────────────────────────┘
        │
        ↓
┌─────────────────────────────────────────────────────────────────┐
│  M2 能力层                                                       │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │
│  │ 知识库 RAG   │  │  工具层      │  │  沙箱执行    │          │
│  │ (4.1-4.16)   │  │ (5.1-5.17)   │  │ (6.1-6.15)   │          │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘          │
│         │                 │                 │                  │
│         │    ┌────────────┘                 │                  │
│         ↓    ↓                              ↓                  │
│  ┌──────────────────┐         ┌──────────────────────┐         │
│  │ search_knowledge │←────────│  code_execution 工具 │         │
│  │     (4.15)       │         │       (5.11)         │         │
│  └──────────────────┘         └──────────────────────┘         │
└─────────────────────────────────────────────────────────────────┘
        │
        ↓
┌─────────────────────────────────────────────────────────────────┐
│  M3 场景化层                                                     │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │
│  │  办公 Skill  │  │ 数据分析Skill│  │ 前端设计Skill│          │
│  │  (7.8-7.11)  │  │   (7.12)     │  │  (7.13-7.15) │          │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘          │
│         │                 │                 │                  │
│         └─────────────────┼─────────────────┘                  │
│                           ↓                                    │
│              ┌──────────────────────┐                          │
│              │  Skill 底层基础设施  │                          │
│              │     (7.1-7.7)        │                          │
│              └──────────────────────┘                          │
└─────────────────────────────────────────────────────────────────┘
        │
        ↓
┌─────────────────────────────────────────────────────────────────┐
│  M4 评估闭环层                                                   │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          │
│  │  评估环境    │→ │  数据集      │→ │  指标体系    │          │
│  │   (8.2)      │  │  (8.3-8.4)   │  │  (8.5-8.8)   │          │
│  └──────────────┘  └──────────────┘  └──────┬───────┘          │
│                                            │                   │
│                            ┌───────────────┘                   │
│                            ↓                                   │
│              ┌──────────────────────┐                          │
│              │  迭代流程 + 回滚     │                          │
│              │  (8.13-8.16)         │                          │
│              └──────────────────────┘                          │
└─────────────────────────────────────────────────────────────────┘
```

**关键路径(M0 → M1 → M2 → M3 → M4)**:

依赖链路最长的关键路径,决定 MVP 最短实施周期:

```
四层骨架(2.1)
  → 进程模型(2.2)
    → 通信协议(2.3)
      → ReAct 循环(2.4)
        → 上下文管理器(3.1-3.15)
          → 模型适配(2.7)
            → 工具层(5.1-5.17)
              → 沙箱执行(6.1-6.15)
                → 场景 Skills(7.1-7.16)
                  → 评估闭环(8.1-8.16)
```

**跨层关键依赖(纵向)**:

| 上游模块 | 下游模块 | 依赖说明 | 对应章节 |
|---|---|---|---|
| Postgres Schema | 上下文管理器 | ctx 读写依赖 messages/react_events 表 | 2.10 → 3.1 |
| Postgres Schema | 知识库 RAG | kb_chunks/kb_documents 表 | 2.10 → 4.6 |
| Postgres Schema | 评估闭环 | eval_datasets/eval_runs 表 | 2.10 → 8.3 |
| ReAct 循环 | 上下文管理器 | maybe_compress 钩子调用 | 2.4 → 3.1 |
| 上下文管理器 | 知识库 RAG | Stable Zone 注入 KB 片段 | 3.7 → 4.15 |
| 上下文管理器 | 用户记忆 | Stable Zone 注入记忆 | 3.7 → 4.5 |
| 模型适配 | 上下文管理器 | 压缩模型复用适配器 | 2.7 → 3.11 |
| 模型适配 | 知识库 RAG | Embedding 云端降级 | 2.7 → 4.10 |
| 工具描述规范 | 工具层 | ToolDef schema 统一 | 3.8 → 5.1 |
| 工具描述规范 | 场景 Skills | Skill 工具白名单 | 3.8 → 7.5 |
| 工具层 | 沙箱执行 | code_execution 工具契约 | 5.11 → 6.1 |
| 工具层 | 场景 Skills | 场景工具白名单 | 5.6 → 7.5 |
| 沙箱执行 | 场景 Skills | 文档处理 + 数据分析 + 代码生成 | 6.1 → 7.8/7.12/7.14 |
| 可观测性 | 评估闭环 | react_events 回放 | 2.13 → 8.2 |
| 场景 Skills | 评估闭环 | Skill 示例作为黄金样本 | 7.16 → 8.3 |
| 版本管理 | 评估闭环 | 版本对比 + 回滚 | 7.3 → 8.12/8.14 |

**并行开发机会**:

依赖图中可并行的模块(单人开发可在前置依赖完成后交替推进):

- M1 阶段:模型适配(2.7)与上下文管理器(3.1-3.15)在 ReAct 循环就位后可并行
- M2 阶段:知识库 RAG(第 4 章)与工具层(第 5 章)在编排核心就位后可并行;沙箱执行(第 6 章)依赖工具层,但可与 RAG 并行
- M3 阶段:三场景 Skills(7.8-7.11、7.12、7.13-7.15)在底层基础设施(7.1-7.7)就位后可并行
- M4 阶段:评估环境(8.2)与数据集(8.3-8.4)可并行;指标体系(8.5-8.8)依赖前两者

`[MVP]` DAG 与关键路径为单人开发顺序的核心依据,9.6 节基于此输出实操步骤。

---

### 9.6 关键路径与单人开发推荐顺序

基于 9.5 DAG 与关键路径,给出单人使用 Trae Code 开发的推荐顺序。每步标注前置依赖与产出物,便于实操追踪。

**开发顺序总览(按关键路径串行 + 并行机会穿插)**:

| 步骤 | 模块 | 前置依赖 | 产出物 | 对应阶段 |
|---|---|---|---|---|
| 1 | 四层骨架 + 目录结构 | 无 | backend/frontend 目录 + 依赖规则 | M0 |
| 2 | 进程模型 + Worker 池 | 步骤 1 | Python Sidecar 可启动 + Worker 可 spawn | M0 |
| 3 | 通信协议(HTTP + WS) | 步骤 2 | 前后端 WS 连接 + ws_offset 补发 | M0 |
| 4 | Postgres Schema + 运维 | 步骤 2 | 全部表 + 磁盘告警 + TTL 清理 | M0 |
| 5 | 配置分层 + API Key 加密 | 步骤 4 | config.yaml + config_runtime + AES-256-GCM + mcp.protocol_version='2025-11-25'(loader 对 2026-07-28 抛 ConfigNotSupportedInMVP) | M0 |
| 6 | 可观测性(日志 + 事件流) | 步骤 3、4 | 结构化 JSON 日志 + react_events 入库 | M0 |
| 7 | ReAct 核心循环 + 状态机 | 步骤 3、5、6 | ReAct 循环可跑通(无工具) | M1 |
| 8 | 模型适配(四家) | 步骤 5、7 | 四家适配器 + capability 降级 + 验证 ToolDef 2020-12 schema 透传(R-2 provider spike,见 9.9 风险十二) | M1 |
| 9 | 上下文管理器(分区 + 构建) | 步骤 4、7 | Frozen/Stable/Active 三区 + 构建流水线 | M1 |
| 10 | hash 校验 + 状态栏 + 模板变量 | 步骤 9 | SHA-256 hash + 状态栏注入 + `{{var}}` 解析 | M1 |
| 11 | 压缩策略(三类) + 注入防护 + 计费 | 步骤 8、9、10 | 滑动窗口 + 摘要 + Stable 合并 + 三层防护 | M1 |
| 12 | 异常处理 + checkpoint | 步骤 6、7 | 四类异常降级 + checkpoint 存储 | M1 |
| 13 | 知识库 RAG 全栈 | 步骤 4、8、9 | 文档处理 + chunking + embedding + HNSW + 混合检索 + reranker | M2 |
| 14 | search_knowledge 工具 | 步骤 9、13 | min_similarity + 分页 + Stable 注入 + 片段计数 | M2 |
| 15 | 用户记忆(提取/存储/淘汰/注入) | 步骤 4、8、9 | manual_extract + 阈值可配置 + top 10 注入 | M2 |
| 16 | 双轨工具架构 + MCP Client | 步骤 7、9 | 内置 + MCP 统一调度 + stdio/HTTP 双探活 + MCPClient 锁定 2025-11-25(不实现双协议分发,V2;实现量参考 5.3 全文,仅协议层锁定旧版,其余逻辑不减) | M2 |
| 17 | 9 类通用工具 | 步骤 16 | search_knowledge/file_read/file_write/http_request/web_search/calculator/datetime/code_execution/read_artifact | M2 |
| 18 | 权限确认 + 超时重试 + 异步事件 + Artifact + 安全 | 步骤 16、17 | 三级分级 + 指数退避 + 长任务 + 截断 + 白名单 + AuthProtocol/TasksExtensionAdapter 两个 ABC stub(MCP 2026-07-28 V2 预留) | M2 |
| 19 | 沙箱代码执行 | 步骤 17 | 子进程隔离 + 资源限制 + 三层安全边界 + 跨平台 | M2 |
| 20 | Skill 底层基础设施 | 步骤 16、17 | 目录/元数据/版本/加载/工具共享/Prompt/少样本 | M3 |
| 21 | 办公 Skill(文档 + 网页) | 步骤 19、20 | Excel/Word 处理 + web_search + 分块读取 | M3 |
| 22 | 数据分析 Skill | 步骤 19、20 | pandas/matplotlib/scipy + 图表输出 | M3 |
| 23 | 前端设计 Skill(代码 + 知识库) | 步骤 13、19、20 | HTML/React/Vue 生成 + 设计系统 RAG | M3 |
| 24 | 评估环境 + 数据集 | 步骤 6、20 | 离线批量 + 交互式回放 + Mock + eval_datasets | M4 |
| 25 | 指标体系 + LLM-as-Judge | 步骤 24 | 五类指标 + GLM-4-Flash Judge | M4 |
| 26 | 评估执行 + 结果存储 + 版本对比 | 步骤 24、25 | 手动/自动触发 + eval_runs + 基线筛选 | M4 |
| 27 | 迭代流程 + 回滚 + 持续进化 | 步骤 26 | 三类载体闭环 + Prompt/Skill/Harness 回滚 + 低分案例提取 | M4 |

**MCP 2026-07-28 兼容性说明**:本次 MCP 协议升级对 27 步开发顺序的影响为"零运行时变更 + 4 处注记"(步骤 5/8/16/18),不改变步骤编号、依赖关系与产出物。MVP 锁定 `2025-11-25` 协议,双协议分发/MRTR/Tasks/EMA 整体降级 V2(见 5.18 V2 清单与 9.9 风险十二)。步骤 8 需在模型适配阶段验证四家 provider 对 JSON Schema 2020-12 的透传支持(R-2 provider spike);步骤 16 的 MCPClient 实现量不因协议锁定而减少(仍需 stdio/HTTP/连接池/探活/重试完整逻辑);步骤 18 的两个 ABC stub 为零成本 V2 预留,MVP 运行路径不经过。重排版 docx 的 Step 1-7 通用开发流程与协议无关,不受影响。

**单人开发实操建议**:

1. **严格串行 M0**:M0 是全部依赖根基,不可并行;完成后做一次集成验证(Done Criteria 全通过)再进入 M1。
2. **M1 内部可穿插**:步骤 8(模型适配)与步骤 9-11(上下文管理器)在步骤 7(ReAct 循环)就位后可交替推进;建议先完成 8(能调通模型)再深入 9-11,避免上下文工程无模型可测。
3. **M2 内部三线并行**:知识库 RAG(13-15)、工具层(16-18)、沙箱(19)在编排核心就位后相互独立,可按兴趣穿插;建议先完成工具层(16-18),因为沙箱(19)依赖 code_execution 工具契约。
4. **M3 三场景并行**:步骤 21-23 在步骤 20 就位后可并行;建议先做办公场景(用户最高频需求),验证 Prompt 框架与工具协作,再复制到数据分析与前端设计。
5. **M4 最后做**:评估闭环依赖所有前序模块的事件流与版本快照,必须放在最后;但数据集(步骤 24)可在 M3 进行时同步积累黄金样本。
6. **每阶段完成后回归测试**:每个阶段 Done Criteria 全通过后,运行一次回归测试(复用前序阶段的 Done Criteria),确保不引入回归。

`[MVP]` 27 步开发顺序为单人 Trae Code 开发的推荐路径,可根据实际进度调整穿插顺序,但不可跳过关键路径上的步骤。

---

### 9.7 MVP 验收标准

对接第 8 章评估指标,定义全系统可落地验证的 MVP 验收标准。每项标准标注对应章节与验证方式。

**验收标准总表**:

| 验收维度 | 验收标准 | 验证方式 | 对应章节 |
|---|---|---|---|
| 架构完整性 | 四层骨架 + 进程模型 + 通信协议全量就位,Electron 拉起 Sidecar,WS 连接建立 | 手动启动应用,观察 WS 连接日志 | 2.1-2.3 |
| 持久层完整性 | 12 张 Postgres 表全部创建,可读写基础数据,磁盘分级告警生效 | SQL 查询表结构 + 模拟磁盘超限 | 2.10 |
| 配置完整性 | config.yaml + config_runtime 加载成功,API Key AES-256-GCM 加密存储 | UI 录入 Key + 查询 config_runtime 密文 | 2.7、2.12 |
| ReAct 循环 | 单 Agent 完整循环(thinking→tool_call→tool_result→final),前端 WS 流式渲染 | 发送测试消息,观察 ReAct 步骤渲染 | 2.4 |
| 模型适配 | 四家模型(GLM/DeepSeek/Agnes/KIMI)均可调用,capability 降级生效 | 逐一切换模型发送消息 + 模拟某家不可用 | 2.7 |
| KV Cache 约束 | Frozen/Stable/Active 三区构建,SHA-256 hash 校验通过 | 查询 messages 表 zone 字段 + hash 比对 | 2.8、3.4 |
| 上下文压缩 | 长会话触发压缩(任一条件),压缩后 hash 重新计算通过 | 构造超阈值会话,观察压缩事件 + hash 变更 | 3.9、3.10 |
| 注入防护 | 中英文高危输入被拦截,告警入 react_events | 发送注入测试用例,查询 injection_alert 事件 | 3.12 |
| 计费感知 | token 按对话/压缩/embedding 三类分别记录 | 查询 token_usage 事件分类统计 | 3.13 |
| 异常处理 | 四类异常(模型/工具/进程/用户)均降级,checkpoint 存储 | 模拟各类异常 + 用户取消,查询 checkpoint 事件 | 2.14 |
| 用户记忆 | 每 8 轮 + 会话结束 + UI 手动触发三种方式提取,注入 Stable Zone | 构造 8 轮对话 + 手动触发 + 查询 user_memories | 4.2、4.5 |
| 记忆淘汰 | 数量上限 + 低重要性超期淘汰 + 软删除,阈值可配置 | 插入超限记忆 + 修改 config_runtime 阈值 | 4.4 |
| 知识库 RAG | 文档(Markdown/PDF/Code/Plain)端到端入库,混合检索 + reranker 生效 | 上传测试文档 + 调用 search_knowledge | 4.6-4.15 |
| Embedding 降级 | bge-m3 + bge-small 轻量备选 + 内存自动切换 + LRU 缓存 | 模拟低内存环境 + 重复 query 命中缓存 | 4.10 |
| 检索质量 | min_similarity 过滤低分 chunk,分页返回,片段计数触发合并 | 调用 search_knowledge + 查询 Stable Zone 片段数 | 4.15 |
| 工具层 | 9 类通用工具均可调用,MCP(stdio + HTTP)双探活通过 | 逐一调用工具 + 配置 MCP server 探活 | 5.6、5.4 |
| 权限确认 | 三级分级 + WS 确认 + 会话缓存 + cache_key 含 skill_name | 调用 elevated 工具 + 切换 Skill 验证缓存隔离 | 5.12、7.5 |
| 沙箱执行 | Python/JavaScript 代码执行,stdout/stderr 流式,超 2k 走 artifact | 执行测试代码 + 查询 artifact 文件 | 6.10 |
| 沙箱安全 | 资源限制(300s/512MB/100MB/禁网络)+ 预扫描 + 环境脱敏 | 执行死循环 + 大内存 + 读取 env | 6.7、6.8 |
| 跨平台 | Windows/macOS/Linux 均可运行,spawn/fork 自动选择 | 三平台分别启动应用 | 6.15 |
| 场景 Skills | 三场景(办公/数据分析/前端)可独立运行,覆盖首批需求 | 逐场景执行典型任务 | 7.8-7.15 |
| Skill 版本管理 | Git + PG 快照 + 会话锁定 + UI 回滚 + 重启版本加载 | 回滚 Skill 版本 + 重启会话验证加载规则 | 7.3 |
| Skill 加载兜底 | Skill 不存在时返回 UI 友好错误,跳转选择页 | 输入不存在的 Skill 名 | 7.4 |
| 评估环境 | 离线批量 + 交互式回放可执行,Mock 数据按工具名对应 | 运行评估 + Mock 模式回放 | 8.2、8.10 |
| 数据集 | 每场景 20 条样本 + train/test 分离 + CHECK 约束 + Pydantic 校验 | 插入非法结构样本验证校验 | 8.3、8.4 |
| 评估指标 | 五类指标全量 + LLM-as-Judge(GLM-4-Flash) | 运行评估查看 metrics | 8.5-8.8 |
| 版本对比 | 双维度筛选 + 基线自动筛选 + 差值计算 + UI 图表 | 运行两次评估 + 版本对比 | 8.12 |
| 迭代闭环 | 三类载体(Prompt/Skills/Harness)闭环,退化仅告警不阻断 | 修改 Prompt + 运行评估 + 验证告警 | 8.13 |
| 回滚机制 | Prompt 独立回滚 + Skill 完整回滚 + Harness 手动 | 分别触发三类回滚 | 8.14 |
| 持续进化 | 低分案例自动提取 + 人工审核 + 两类筛选标准 | 运行评估 + 查看审核队列 | 8.16 |

**验收方式分类**:

- **手动验证**:架构完整性、ReAct 循环、场景 Skills 等需用户交互的项,通过 UI 操作观察行为。
- **SQL 查询**:持久层、计费、事件流等可通过 Postgres 查询验证。
- **自动化测试**:压缩、注入防护、沙箱安全等可通过单元测试 + 集成测试验证(对应 tests/ 目录)。
- **评估回放**:评估环境、数据集、指标等可通过第 8 章评估流程验证。

`[MVP]` 全部 30 项验收标准通过即为 MVP 完成,对应第 8 章评估指标的工程化落地。

---

### 9.8 三大约束落地检查表

汇总每章对"上下文质量优先 / 缓存友好 / 评估驱动迭代"三大第一性约束的落地实现,形成单一检查表,确认 MVP 阶段三大约束无遗漏。

**约束一:上下文质量优先**:

| 落地实现 | 对应章节 | 验证方式 |
|---|---|---|
| KV Cache 分区模型(Frozen/Stable/Active) | 2.8、3.2 | 查询 messages 表 zone 字段 |
| ReAct 循环纯净(压缩逻辑外置到 context_manager) | 2.4 | 代码审查 react_loop 不含压缩逻辑 |
| hash 校验(会话维度合并标志) | 3.4 | hash 比对 + canonical JSON |
| 状态栏机制(时间戳/偏好/会话元信息) | 3.5、3.6 | 查询状态栏消息注入 |
| 模板变量体系(五类命名空间 + `{{var}}` 解析) | 3.7 | 渲染 system prompt 验证变量替换 |
| 三类压缩策略(滑动窗口 + 摘要 + Stable 合并) | 3.10 | 触发压缩 + 查询 compressed_from |
| 注入防护(三层 + 中英文 + 高低风险分级 + 沙箱差异化) | 3.12 | 发送注入用例 + 查询 injection_alert |
| 记忆注入 top 10 限制 | 4.5 | 查询 Stable Zone 记忆条数 |
| 混合检索 + reranker 精排 | 4.13、4.14 | search_knowledge 返回结果评分 |
| Agent 自主决定检索(避免无意义检索污染上下文) | 4.15 | Skill prompt 内置检索判断说明 |
| artifact 机制避免大结果污染上下文 | 5.15 | 工具返回超长结果走 artifact |
| Web 搜索与 KB 检索边界明确 | 5.7 | Skill 工具白名单区分 |
| 沙箱 stdout 超 2k 走 artifact(比 3.12 的 4k 更严格) | 6.10 | 执行长输出代码 + 查询 artifact |
| 文件系统工作记忆避免大输出污染上下文 | 6.6 | 沙箱跨轮次复用文件 |
| 场景专用 Prompt 四段式框架 | 7.6 | 查看 Skill system_prompt.md |
| 少样本示例注入 Frozen Zone | 7.7 | 查询 Frozen Zone 示例消息 |
| 设计系统 RAG 检索结果注入 Stable Zone | 7.15 | 前端 Skill 检索测试 |
| LLM-as-Judge 评判主观质量 | 8.8 | 运行评估查看 Judge 评分 |
| 低分案例驱动样本扩充 | 8.16 | 查询审核队列 |

**约束二:缓存友好**:

| 落地实现 | 对应章节 | 验证方式 |
|---|---|---|
| KV Cache 分区模型 | 2.8 | 分区构建 + hash 校验 |
| 各适配器 cache 行为映射 | 2.7 | 查询 ModelCapability.cache_field |
| Skills 版本与会话绑定 | 2.11 | 会话锁定 + 切换拒绝 |
| 压缩逻辑外置(不破坏 Frozen Zone) | 2.4、3.10 | 压缩后 hash 重新计算 |
| hash 校验(会话维度合并标志) | 3.4 | SHA-256 + canonical JSON |
| 状态栏注入时机(用户消息轮) | 3.6 | 查询状态栏消息位置 |
| 模板变量解析时机(Frozen vs Stable) | 3.7 | `kb_replace_mode=false` 默认 |
| KB 片段注入 Stable Zone(不破坏 Frozen Zone hash) | 4.15 | `kb_replace_mode=false` |
| 增量更新不影响已有会话 Stable Zone | 4.16 | 文档更新 + 已有会话 Stable 不变 |
| 会话启动锁定工具集(不运行时变更) | 5.5 | 会话中工具集不变 |
| 权限确认缓存减少重复打扰 | 5.12 | 同工具同参数二次调用免确认 |
| 会话工作目录跨轮次持久(不重建) | 6.4 | 跨轮次文件复用 |
| 串行调用复用目录(不破坏会话状态) | 6.11 | 串行 code_execution 文件互通 |
| 会话锁定 Skill 版本,Frozen Zone hash 稳定 | 7.3 | 会话中 Skill 版本不变 |
| 会话中途切换拒绝(避免 KV Cache 失效) | 7.4 | 切换 Skill 被拒绝 |
| 示例注入 Frozen Zone,运行时不变 | 7.7 | Frozen Zone 含示例消息 |
| 版本变更触发快速回归子集(仅 5 条) | 8.13 | 版本变更评估仅跑子集 |
| Mock 模式加速批量评测 | 8.10 | Mock 评估执行时间 |

**约束三:评估驱动迭代**:

| 落地实现 | 对应章节 | 验证方式 |
|---|---|---|
| 事件流持久化(react_events) | 2.13 | 查询 react_events 表 |
| 评估数据集与版本快照表 | 2.10 | 查询 eval_datasets/version_snapshots |
| 异常轨迹保存 | 2.14 | 查询 ERROR 态 react_events |
| V2 接口预留 | 2.16 | 代码审查 Protocol 定义 |
| 压缩存档(soft delete + snapshot + hash 备份) | 3.10 | 查询 messages_archive |
| token 三类成本分类 + 价格快照 | 3.13 | 查询 token_usage 分类 |
| 消息时序规则 | 3.14 | TokenEstimator 输出 |
| 持久化路径完整 | 3.15 | 读写路径覆盖测试 |
| 记忆提取事件入 react_events | 4.2 | 查询 memory_extracted 事件 |
| 淘汰事件入 react_events | 4.4 | 查询 memory_evicted 事件 |
| 知识库快照入 version_snapshots | 4.16 | 查询 kb 版本快照 |
| 软删除保留历史数据供回放 | 4.4、4.16 | 查询 is_active=FALSE 记录 |
| 所有工具调用入 react_events | 5.13 | 查询 tool_call/tool_result 事件 |
| 资源限额与审计日志支持评估分析 | 5.16 | 查询审计日志 |
| 异步任务状态持久化可回放 | 5.14 | 查询 async_tasks 状态 |
| 所有沙箱执行入 react_events | 6.13 | 查询 sandbox_execution 事件 |
| 预扫描告警支持代码质量评估 | 6.8 | 查询 warnings 字段 |
| 崩溃恢复记录支持失败分析 | 6.12 | 查询崩溃事件 |
| Skill 示例作为黄金样本 | 7.16 | 查询 eval_datasets 示例 |
| 版本快照支持历史回放 | 7.3 | 查询 version_snapshots |
| 场景化评估指标差异化 | 7.16 | 评估指标按场景区分 |
| 评估闭环完整(环境 + 数据集 + 指标 + 迭代) | 8.1-8.16 | 运行完整评估流程 |

**检查结论**:

三大约束在前 8 章均有明确落地实现,无遗漏:
- **上下文质量优先**:19 项落地实现,覆盖分区、压缩、防护、检索、artifact、Prompt 框架等。
- **缓存友好**:18 项落地实现,覆盖分区、hash、会话锁定、工具集锁定、工作目录持久等。
- **评估驱动迭代**:22 项落地实现,覆盖事件流、快照、软删除、审计、评估闭环等。

`[MVP]` 三大约束检查表为 MVP 验收的顶层核查清单,每项均可在前序章节找到实现细节。

---

### 9.9 风险识别与缓解

汇总 MVP 实施过程中的主要风险,标注对应章节原有的降级实现引用。每条风险给出触发场景、影响、缓解措施与验证方式。

**风险一:Worker 进程内存 OOM**:

| 维度 | 说明 |
|---|---|
| 触发场景 | 低配置笔记本(8GB 内存)运行 bge-m3(约 2GB)+ Sidecar(1.5GB)+ Electron,可用内存不足 |
| 影响 | Worker 崩溃,embedding/reranker 不可用,知识库检索中断 |
| 缓解措施 | 4.10 bge-small 轻量备选(约 300MB)+ 内存自动切换(可用内存 <6GB 切换)+ 云端 embedding 降级 + 异常入库告警(4.10、4.14) |
| 对应章节 | 4.10、4.14 |
| 验证方式 | 模拟低内存环境,观察自动切换 + 降级日志 |

**风险二:跨平台 spawn/fork 兼容性**:

| 维度 | 说明 |
|---|---|
| 触发场景 | Windows 下 ProcessPoolExecutor 默认 spawn 重新导入父进程模块,加载完整 backend 导致内存冗余 |
| 影响 | Worker 启动慢、内存占用高,极端情况启动失败 |
| 缓解措施 | 2.2 跨平台备注(Worker 入口最小化,仅依赖 numpy/torch)+ 6.15 ProcessManager 按平台自动选择 spawn/fork + Windows 资源限制兜底(仅超时 + psutil 监控) |
| 对应章节 | 2.2、6.15 |
| 验证方式 | Windows/macOS/Linux 三平台分别启动,观察 Worker 启动时间与内存 |

**风险三:KV Cache 失效**:

| 维度 | 说明 |
|---|---|
| 触发场景 | Frozen Zone 内容变更(system prompt/工具定义/少样本示例),导致 prefix hash 变化,KV Cache 全部 miss |
| 影响 | 每轮推理需重新计算 Frozen Zone 的 KV,token 成本与延迟增加 |
| 缓解措施 | 2.8 分区模型 + 3.4 hash 校验(变更检测)+ 7.3 会话锁定 Skill 版本(运行中不切换)+ 7.4 切换拒绝(避免运行时变更)+ 3.7 `kb_replace_mode=false` 默认(KB 注入 Stable 不破坏 Frozen) |
| 对应章节 | 2.8、3.4、3.7、7.3、7.4 |
| 验证方式 | 会话中尝试切换 Skill 被拒绝 + 查询 hash 稳定性 |

**风险四:知识库 Stable Zone 膨胀**:

| 维度 | 说明 |
|---|---|
| 触发场景 | Agentic RAG 多轮多次检索,每次追加 KB 片段到 Stable Zone,长期会话 Stable Zone 无限膨胀 |
| 影响 | Active Zone 预算被挤压,上下文质量下降,压缩频繁触发 |
| 缓解措施 | 4.15 片段计数器(`session.kb_chunks_count >= 20` 触发合并)+ 3.10.3 Stable 合并压缩(每 5 轮或超 20 条)+ 4.15 min_similarity 过滤低分 chunk + 分页返回减少单次注入量 |
| 对应章节 | 3.10、4.15 |
| 验证方式 | 构造多轮检索会话,观察 Stable Zone 片段数 + 合并事件 |

**风险五:Postgres 磁盘膨胀**:

| 维度 | 说明 |
|---|---|
| 触发场景 | 长期使用,react_events/messages/kb_chunks 等表无限增长,桌面端磁盘空间不足 |
| 影响 | 数据库查询性能下降,极端情况磁盘写满导致应用崩溃 |
| 缓解措施 | 2.10 三级磁盘告警(1.5GB 预警 / 2GB 禁止新会话 / 3GB 强制清理)+ TTL 清理(react_events 保留 7 天,3GB 时收紧)+ messages_archive 归档 + VACUUM 调度(每周日凌晨)+ 软删除不物理删除(评估回放需要) |
| 对应章节 | 2.10 |
| 验证方式 | 模拟磁盘超限,观察三级告警 + 自动清理 |

**风险六:沙箱代码逃逸**:

| 维度 | 说明 |
|---|---|
| 触发场景 | Agent 生成恶意代码(或被注入攻击诱导),读取敏感文件、执行危险操作、耗尽资源 |
| 影响 | 本地凭证泄露、文件系统破坏、系统资源耗尽 |
| 缓解措施 | 6.8 三层安全边界(预扫描告警 + 路径过滤白名单 + 资源限制)+ 环境变量脱敏(API Key 不传入沙箱)+ 6.7 资源限制(300s/512MB/100MB/禁网络)+ 5.12 权限分级(code_execution 为 elevated,需 WS 确认)+ 3.12 注入防护(中英文 + 高低风险分级) |
| 对应章节 | 3.12、5.12、6.7、6.8 |
| 验证方式 | 执行危险代码(os.system/读取 .env/死循环/大内存)+ 查询告警 |

**风险七:模型 API 不可用**:

| 维度 | 说明 |
|---|---|
| 触发场景 | 某家模型 API(GLM/DeepSeek/Agnes/KIMI)服务故障、限流、网络中断 |
| 影响 | ReAct 循环中断,用户无法继续对话 |
| 缓解措施 | 2.7 capability 降级(某家不可用自动切换备选)+ 2.14 异常分类(模型层异常 500-599 降级)+ 2.7 四家适配器均独立,单家故障不影响其他 |
| 对应章节 | 2.7、2.14 |
| 验证方式 | 模拟某家 API 不可用,观察降级切换 |

**风险八:会话状态丢失**:

| 维度 | 说明 |
|---|---|
| 触发场景 | Sidecar 进程崩溃、Electron 异常退出、用户主动取消 |
| 影响 | 当前 ReAct 循环中断,未保存的上下文丢失 |
| 缓解措施 | 2.14 checkpoint 机制(每轮结束自动写入 react_events)+ 会话标记 interrupted + react_events/messages 持久化到 Postgres(进程崩溃不丢数据)+ 6.12 沙箱崩溃保留已生成文件 |
| 对应章节 | 2.14、6.12 |
| 验证方式 | 模拟进程崩溃 + 重启后查询 checkpoint + react_events 完整性 |

**风险九:评估数据污染**:

| 维度 | 说明 |
|---|---|
| 触发场景 | eval_datasets 样本结构非法、expected_react_trace 字段缺失、Mock 数据与 Skill 版本不匹配 |
| 影响 | 评估结果失真,迭代决策错误 |
| 缓解措施 | 8.4 Postgres CHECK 约束 + Pydantic 强校验模型(入库前拦截)+ 8.10 Mock 数据跟随 Skill 版本同步(回滚时自动加载对应版本)+ 8.12 基线自动筛选规则(同模型 + 同 Skill 最新成功记录) |
| 对应章节 | 8.4、8.10、8.12 |
| 验证方式 | 插入非法结构样本 + Skill 回滚后验证 Mock 加载 |

**风险十:Skills 版本冲突**:

| 维度 | 说明 |
|---|---|
| 触发场景 | 用户回滚 Skill 版本后,已有会话仍锁定旧版本,新会话加载回滚后版本,行为不一致 |
| 影响 | 用户困惑,评估对比基线混乱 |
| 缓解措施 | 7.3 会话重启版本加载规则(会话正常结束销毁后,新建同 Skill 会话自动读取 latest_version;仅持续在线会话维持锁定版本)+ 8.12 基线筛选规则(同 Skill 最新成功记录) |
| 对应章节 | 7.3、8.12 |
| 验证方式 | 回滚 Skill + 重启会话验证加载 + 持续在线会话维持锁定 |

**风险十一:权限缓存跨 Skill 污染**:

| 维度 | 说明 |
|---|---|
| 触发场景 | 不同 Skill 对同一工具配置不同 safety_level,权限缓存互相覆盖 |
| 影响 | 用户已在 A Skill 批准的操作,在 B Skill 被误放行或误拦截 |
| 缓解措施 | 7.5 权限 cache_key 含 skill_name(`hash(f"{skill_name}:{tool_name}:{json.dumps(args)}")`)+ 5.12 权限缓存会话级隔离 |
| 对应章节 | 5.12、7.5 |
| 验证方式 | 切换 Skill 调用同工具,验证缓存不互相覆盖 |

**风险十二:MCP 2026-07-28 协议升级**(新增):

| 维度 | 说明 |
|---|---|
| 触发场景 | MCP 2026-07-28 无状态协议发布,SDK v2.0.0rc1 非稳定;旧协议 12 个月弃用宽限期;模型侧 function calling 对 JSON Schema 2020-12 兼容性未知 |
| 影响 | ① 过早引入 rc1 SDK 导致生产不稳定;② 双协议并行增加代码面与测试矩阵;③ 2020-12 的 oneOf/anyIf 透传某 provider 失败;④ stub(AuthProtocol/TasksExtensionAdapter)腐化;⑤ config `protocol_version` 误改静默失败;⑥ 旧协议弃用提前 |
| 缓解措施 | MVP 锁定 `2025-11-25` + `pyproject.toml` 锁 `mcp>=1.0,<2.0`(5.3);渐进吸收超集级改动(3.8 ToolDef 2020-12 + output_schema);ABC stub 预留 V2 扩展点(5.12 AuthProtocol / 5.14 TasksExtensionAdapter)并建立追踪;config loader 对 MVP 不支持的 `protocol_version` 值抛 `ConfigNotSupportedInMVP`(9.13);V2 gate on v2.0.0 stable 发布;监控旧协议弃用时间表;V2 降级方案:本地 protocol-bridge proxy 应对新协议独占 server |
| 对应章节 | 3.8、5.3、5.4、5.12、5.13、5.14、5.17、5.18、9.13 |
| 验证方式 | ① `pip show mcp` 确认 v1.x;② provider spike 测试 2020-12 schema 透传;③ config 误改 `2026-07-28` 触发显式错误;④ grep 确认 stub 存在且追踪项就位;⑤ 监控 MCP 官方公告 |

`[MVP]` 12 项风险均有前序章节已设计的降级策略,实施过程中按本表对照验证。

---

### 9.10 V2 演化路线优先级

对 9.3 V2 清单按"用户价值 × 实施成本"二维排序,给出推荐演化顺序。本章仅规划不实施,不承诺时间点。

**评估维度**:

- **用户价值**:对单人桌面 Agent 使用体验的提升程度(高/中/低)。
- **实施成本**:基于已有预留接口的扩展难度(低/中/高,低表示接口已就位仅需填充实现)。

**V2 演化优先级排序(推荐顺序)**:

| 优先级 | 模块 | 用户价值 | 实施成本 | 推荐理由 | 对应章节 |
|---|---|---|---|---|---|
| P1 | 断点续传 | 高 | 低 | react_events + checkpoint hook 已就位,仅需恢复执行器;进程崩溃后可恢复 ReAct,大幅提升可靠性 | 2.16 |
| P1 | 模型路由(TagBasedRouter) | 高 | 低 | Router Protocol 已就位,仅需实现标签路由策略;支持按任务类型自动选择模型,降低成本 | 2.16 |
| P1 | 知识库回滚 | 高 | 低 | 快照 + 软删除已支持,仅需回滚 UI;知识库误更新可快速恢复 | 4.17 |
| P2 | 多 Agent 协作 | 高 | 中 | state_machine 扩展点 + 目录占位就位,需实现子 Agent 委托逻辑;复杂任务可拆分,提升能力上限 | 2.16 |
| P2 | 容器隔离(Docker) | 中 | 中 | SandboxExecutor 抽象基类就位,需实现 Docker 后端;安全隔离从子进程升级到容器,防护更强 | 6.16 |
| P2 | Skills 热加载 | 中 | 中 | loader.py 预留接口,需配合 cache 失效;会话进行中可切换 Skill,提升灵活性 | 2.16 |
| P2 | otel 链路追踪 | 中 | 低 | trace_id/span_id 字段已预留,仅需接入 otel SDK;分布式追踪提升可观测性 | 2.16 |
| P2 | 智能合并(记忆) | 中 | 低 | memories_repo 已支持批量查询,需实现语义相似度合并;长期记忆质量提升 | 4.17 |
| P2 | 加权融合(检索) | 中 | 低 | RRF 可替换,需实现加权融合策略;检索质量可调优 | 4.17 |
| P2 | 自适应超时 | 中 | 低 | 事件日志已记录 duration_ms,需实现历史数据分析;超时阈值更精准 | 5.18 |
| P3 | GPU 加速 | 中 | 高 | Worker 模型加载逻辑可扩展,需 GPU 环境与驱动;embedding/reranker 性能大幅提升 | 4.17 |
| P3 | 工具市场 | 中 | 中 | MCP 配置 UI 已支持,需实现安装逻辑;动态扩展工具能力 | 5.18 |
| P3 | 数据库查询工具 | 中 | 中 | ToolDef schema 已定义,需实现 PostgreSQL 连接;数据分析场景能力扩展 | 5.18 |
| P3 | kb_replace_mode=true | 中 | 中 | 配置开关已定义,需实现 TemplateResolver 替换逻辑;KB 注入更灵活 | 3.16、4.17 |
| P3 | 多会话并行 | 中 | 中 | ws_handler 已支持 session_id 路由,需实现背压控制;多任务并行提升效率 | 2.16 |
| P3 | 场景差异化 Judge | 中 | 低 | judge_prompt_dir 配置预留,需实现场景 Prompt;评估更精准 | 8.17 |
| P3 | A/B 测试完整框架 | 中 | 中 | ab_tests 表 + variant 字段预留,需实现流量分配 + 统计检验;科学化迭代 | 8.17 |
| P4 | 远程沙箱(E2B) | 低 | 中 | 接口已抽象,需实现远程后端;本地资源不足时可用云端沙箱 | 6.16 |
| P4 | 自动回滚 | 低 | 中 | auto_rollback 接口预留,需实现评估不达标触发;迭代安全性提升 | 8.17 |
| P4 | 全自动化流水线 | 低 | 高 | 流水线接口可扩展,需实现完整 CI/CD;单人开发场景价值有限 | 8.17 |
| P4 | 打包内嵌 Python | 低 | 中 | Sidecar 启动协议不变,需 pyinstaller 打包;分发便利性提升 | 2.16 |

**演化路线建议**:

1. **P1 优先做**:三项均为高价值低成本,基于已有预留接口快速实现,立即提升可靠性、成本优化、数据安全。
2. **P2 按需做**:五项为中价值中低成本,根据实际使用痛点选择;多 Agent 协作与容器隔离成本较高但能力提升显著。
3. **P3 谨慎做**:六项为中价值中高成本,需评估投入产出;GPU 加速需硬件支持,工具市场需维护生态。
4. **P4 最后做**:四项为低价值,单人开发场景价值有限;远程沙箱与全自动化流水线更适合团队场景。

`[V2规划]` 优先级排序为长期演化参考,不承诺时间点;每次启动 V2 项前需更新 9.3 清单的"预留接口"状态,确认接口仍可用。

---

### 9.11 架构边界守护原则

复用 2.16 的三条边界守护原则,补充本章的变更检查清单,保障扩展不破坏现有架构。

**三条边界守护原则**(沿用 2.16):

1. **V2 预留接口必须在 MVP 阶段以"空实现"或"Protocol 定义"形式存在,不允许"以后再加"**。
   - 空实现:返回默认值或 NotImplementedError 的方法。
   - Protocol 定义:Python `typing.Protocol` 或 ABC,定义接口契约但不实现。
   - 目录占位:如 `core/multi_agent/` 空目录 + README 说明。

2. **MVP 实现不得依赖 V2 接口的具体实现(仅依赖抽象)**。
   - MVP 代码调用 V2 接口时,仅依赖 Protocol/ABC,不依赖具体类。
   - V2 接口变更不影响 MVP 代码(通过抽象层隔离)。

3. **每次架构变更必须更新边界表,确保边界清晰**。
   - 新增 MVP 模块:更新 9.2 整合表 + 对应章节的 MVP/V2 边界小节。
   - 新增 V2 预留:更新 9.3 整合表 + 对应章节的 MVP/V2 边界小节。
   - V2 转为 MVP:从 9.3 移除,加入 9.2,更新对应章节。

**变更检查清单**(本章补充):

每次架构变更(新增模块/调整边界/修改接口)时,按以下清单检查:

| 检查项 | 检查内容 | 通过标准 |
|---|---|---|
| 1. MVP/V2 边界更新 | 是否更新 9.2/9.3 整合表 + 对应章节边界小节 | 表格与章节一致 |
| 2. 三大约束检查 | 新增模块是否在 9.8 检查表中有对应落地实现 | 三大约束无遗漏 |
| 3. DAG 更新 | 新增模块是否在 9.5 DAG 中标注依赖关系 | 依赖关系清晰 |
| 4. 开发顺序更新 | 新增模块是否在 9.6 开发顺序中标注步骤 | 步骤与依赖匹配 |
| 5. 验收标准更新 | 新增模块是否在 9.7 验收标准中有对应项 | 可验证 |
| 6. 风险评估 | 新增模块是否在 9.9 风险表中有对应风险与缓解 | 风险可控 |
| 7. 配置更新 | 新增模块是否在 9.13 config.yaml 骨架中有对应配置段 | 配置完整 |
| 8. 持久化更新 | 新增模块是否在 9.14 Postgres 表汇总中有对应表 | 表结构完整 |
| 9. 回滚降级更新 | 新增模块是否在 9.12 回滚降级总览中有对应机制 | 故障可恢复 |
| 10. V2 接口验证 | 新增 V2 预留是否以空实现/Protocol 形式存在 | 接口就位 |

**边界守护流程**:

```
架构变更提议
  → 检查 10 项变更检查清单
    → 全部通过 → 实施变更 + 更新文档
    → 任一不通过 → 补充缺失项后重新检查
```

`[MVP]` 三条原则 + 10 项检查清单为架构变更的强制流程,单人开发也需遵守,避免架构漂移。

---

### 9.12 回滚与降级机制总览

整合前序章节的回滚机制与降级策略,形成单一总览,便于故障处理时一站式查阅。

**回滚机制总览**:

| 回滚类型 | 触发场景 | 回滚范围 | 实现机制 | 对应章节 |
|---|---|---|---|---|
| Skill 版本回滚 | Skill 迭代后效果退化 | Skill 定义 + 工具白名单 + Prompt + 少样本 | Git revert + version_snapshots 快照 + UI 回滚按钮;会话重启版本加载规则(新会话读 latest_version,在线会话维持锁定) | 7.3、8.14 |
| Prompt 独立回滚 | Prompt 修改后效果退化 | 仅 system_prompt.md | Git 版本管理 + PG 快照;独立于 Skill 版本回滚 | 8.14 |
| 知识库快照回滚 | 知识库误更新或数据污染 | kb_chunks + kb_documents | version_snapshots 快照 + 软删除(is_active=FALSE);回滚到历史快照版本 | 4.16(V2) |
| Harness 手动回滚 | 代码层变更引入 bug | 整个 Python Sidecar 代码 | Git 版本管理 + 手动 revert;无自动回滚机制 | 8.14 |
| Mock 数据跟随回滚 | Skill 回滚后 Mock 不匹配 | mock_data 目录 | Mock 数据文件命名与工具名一一对应,版本跟随 Skill 快照同步更新 | 8.10 |

**降级策略总览**:

| 降级类型 | 触发场景 | 降级行为 | 影响范围 | 对应章节 |
|---|---|---|---|---|
| 模型 API 降级 | 某家模型不可用(故障/限流/网络) | 自动切换备选模型;capability 降级(不支持的能力跳过) | 推理质量可能下降,不中断 | 2.7 |
| Worker 降级 | Worker 进程崩溃/超时 | embedding/reranker 切换云端 API;异常入库告警 | 检索质量略降,成本增加 | 4.10、4.14 |
| Embedding 模型降级 | 低内存环境(<6GB 可用) | bge-m3 自动切换 bge-small;HNSW 索引重建 | 维度变化需重建索引,检索质量略降 | 4.10 |
| Reranker 降级 | reranker 不可用 | 跳过重排,直接返回混合检索 top-5 | 检索精度下降,不中断 | 4.14 |
| 沙箱执行降级 | 沙箱超时/内存超限/磁盘满 | 终止进程 + 返回已生成文件 + 告警入 react_events | 当前代码执行中断,文件保留 | 6.7、6.12 |
| 工具调用降级 | 工具超时/失败 | 指数退避重试 3 次 + 返回错误信息给 Agent | Agent 需自行处理失败,可能改用其他工具 | 5.13 |
| 上下文压缩降级 | Active Zone 超 token 预算 | 触发三类压缩(滑动窗口/摘要/Stable 合并) | 历史信息被压缩,可能丢失细节 | 3.9、3.10 |
| 异常分类降级 | 模型/工具/进程/用户四类异常 | 模型层降级切换/工具层重试/进程层保存轨迹/用户层 checkpoint | 对应类型的中断处理,不全局崩溃 | 2.14 |
| 磁盘空间降级 | Postgres 数据目录超阈值 | 1.5GB 预警 / 2GB 禁止新会话 / 3GB 强制清理 | 新会话受限或自动清理过期数据 | 2.10 |
| 评估退化降级 | 评估指标退化 | 仅 UI 告警 + eval_runs 记录,不自动阻断发布 | 用户人工确认是否继续上线 | 8.13 |

**故障处理流程**:

```
故障发生
  → 查询 9.12 回滚机制总览(确定回滚类型)
    → 执行对应回滚(Skill/Prompt/知识库/Harness/Mock)
  → 查询 9.12 降级策略总览(确定降级类型)
    → 确认降级已自动触发(模型/Worker/Embedding/Reranker/沙箱/工具/压缩/异常/磁盘/评估)
  → 验证故障恢复
    → 查询 react_events 确认降级事件 + 告警
    → 查询 9.7 验收标准对应项确认功能恢复
```

`[MVP]` 回滚与降级机制总览为故障处理的一站式查阅入口,所有机制均在前序章节已实现。

---

### 9.13 全局 config.yaml 完整骨架

整合全 8 章涉及的 config.yaml 配置段,形成完整配置文件骨架。每段标注对应章节与是否运行时可改(config_runtime 覆盖)。

```yaml
# ==============================================================================
# 私有化 Agent 开发方案 - 全局配置文件
# 对应章节:2.12 配置分层管理
# 规则:静态 yaml 默认值 + config_runtime 运行时覆盖(标注 [runtime] 的项支持)
# ==============================================================================

# ------------------------------------------------------------------------------
# 1. 系统基础配置(2.1、2.2)
# ------------------------------------------------------------------------------
system:
  app_name: "Private Agent"
  version: "0.1.0"
  workspace_root: "${WORKSPACE}"           # 工作区根目录
  sidecar:
    memory_limit_mb: 1536                  # Python Sidecar 内存上限 [runtime]
    log_level: "INFO"                      # 日志级别 [runtime]
  worker:
    pool_size: 2                           # Worker 进程池大小
    memory_limit_mb: 512                   # 单 Worker 内存上限

# ------------------------------------------------------------------------------
# 2. 通信协议配置(2.3)
# ------------------------------------------------------------------------------
server:
  http:
    host: "127.0.0.1"
    port: 8765                             # HTTP 控制面端口
  websocket:
    port: 8766                             # WS 数据面端口
    reconnect_interval_sec: 3              # 重连间隔
    event_buffer_size: 100                 # 事件缓冲区大小

# ------------------------------------------------------------------------------
# 3. 模型配置(2.7、2.9、2.12)
# ------------------------------------------------------------------------------
models:
  # API Key 加密存储(2.7):UI 录入 → AES-256-GCM 加密 → config_runtime.api_keys
  # 此处仅声明 providers,实际密钥在 config_runtime 中密文存储
  providers:
    glm:
      base_url: "https://open.bigmodel.cn/api/paas/v4"
      model_name: "glm-4"
      enabled: true [runtime]
    deepseek:
      base_url: "https://api.deepseek.com/v1"
      model_name: "deepseek-chat"
      enabled: true [runtime]
    agnes:
      base_url: "待确认"                    # 落地阶段补充官方文档
      model_name: "待确认"
      enabled: false [runtime]
    kimi:
      base_url: "https://api.moonshot.cn/v1"
      model_name: "moonshot-v1-8k"
      enabled: true [runtime]
  
  # 模型路由(2.9):MVP 手动选择,V2 预留 TagBasedRouter
  router:
    type: "manual"                         # MVP: manual / V2: tag_based [runtime]
    fallback_chain: ["glm", "deepseek", "kimi"]  # 降级链 [runtime]
  
  # 压缩模型(3.11)
  compress_model: "glm-4-flash"            # 压缩用模型 [runtime]

# ------------------------------------------------------------------------------
# 4. 上下文工程配置(3.5、3.7、3.9、3.10、3.13)
# ------------------------------------------------------------------------------
context:
  # 状态栏(3.5)
  status_bar:
    enabled: true [runtime]
    inject_per_turn: true                  # 每轮注入 [runtime]
  
  # 模板变量(3.7)
  template:
    kb_replace_mode: false                 # MVP: false(注入 Stable) / V2: true [runtime]
  
  # 压缩触发(3.9)
  compression:
    active_zone_token_limit: 4000          # Active Zone token 上限 [runtime]
    stable_zone_size_limit: 20             # Stable Zone 消息条数上限 [runtime]
    aggressive_mode: false                 # 激进模式 [runtime]
    kb_chunks_merge_threshold: 20          # KB 片段触发合并阈值(4.15) [runtime]
  
  # 计费(3.13)
  billing:
    currency: "CNY"                        # 默认币种 [runtime]
    price_snapshot_enabled: true           # 价格快照 [runtime]

# ------------------------------------------------------------------------------
# 5. 记忆配置(4.2、4.4、4.5)
# ------------------------------------------------------------------------------
memory:
  extract_interval_turns: 8                # 自动提取间隔轮次 [runtime]
  inject_limit: 10                         # Stable Zone 注入 top N [runtime]
  eviction:                                # 淘汰阈值(4.4,可配置) [runtime]
    max_active_count: 200
    min_importance_threshold: 0.3
    expire_days: 30

# ------------------------------------------------------------------------------
# 6. 知识库配置(4.9、4.10、4.11、4.13)
# ------------------------------------------------------------------------------
kb:
  # chunk 参数(4.9)
  chunk:
    markdown:
      chunk_size: 512
      chunk_overlap: 64
    code:
      chunk_size: 256
      chunk_overlap: 32
    pdf:
      chunk_size: 512
      chunk_overlap: 64
    plain:
      chunk_size: 400
      chunk_overlap: 50
  
  # Embedding 模型(4.10)
  embedding:
    local_default: "BAAI/bge-m3"           # 标准:1024 维,约 2GB
    local_light: "BAAI/bge-small-zh-v1.5"  # 轻量:384 维,约 300MB [runtime]
    fallback_cloud: "glm-embedding"
    auto_switch_memory_gb: 6               # 可用内存低于此值自动切换轻量 [runtime]
    lru_cache_size: 512                    # query 向量 LRU 缓存大小
  
  # HNSW 索引(4.11)
  hnsw:
    m: 16
    ef_construction: 128
    ef_search: 64                          # 运行时可调 [runtime]
  
  # 混合检索(4.13)
  retrieval:
    rrf_k: 60                              # RRF 融合参数(MVP 固定)
    vector_top_k: 20                       # 向量检索 top-k
    keyword_top_k: 20                      # 关键词检索 top-k
    final_top_k: 5                         # 融合后 top-k

# ------------------------------------------------------------------------------
# 7. 工具层配置(5.4、5.12、5.13、5.15)
# ------------------------------------------------------------------------------
tools:
  # MCP 配置(5.4):混合配置,实际 MCP server 列表在 config_runtime
  mcp:
    config_source: "hybrid"                # 静态 + 运行时 [runtime]
    probe_interval_sec: 60                 # 探活间隔 [runtime]
    # MCP 2026-07-28 协议版本协商(MVP 锁定旧协议,V2 启用新协议)
    protocol_version: "2025-11-25"         # auto | 2026-07-28 | 2025-11-25 [runtime]
                                           # MVP 仅支持 2025-11-25;loader 对 2026-07-28 抛 ConfigNotSupportedInMVP
    cache_ttl_ms: 30000                    # 工具列表缓存 TTL(新协议 ttlMs)[runtime] V2 启用
    enable_server_discover: false          # 是否启用 server/discover 探测 [runtime] V2 启用
  
  # 权限确认(5.12)
  permission:
    cache_ttl_sec: 3600                    # 权限缓存 TTL [runtime]
    elevated_require_ws_confirm: true      # elevated 工具需 WS 确认 [runtime]
  
  # 超时重试(5.13)
  timeout:
    default_sec: 30                        # 基础超时 [runtime]
    categories:                            # 分类超时 [runtime]
      file_ops: 30
      http_request: 60
      web_search: 30
      code_execution: 300
      search_knowledge: 10
    max_retries: 3                         # 最大重试次数
    backoff_base_sec: 1                    # 指数退避基数
  
  # Artifact(5.15)
  artifact:
    truncate_threshold_tokens: 4000        # 截断阈值(通用)
    sandbox_truncate_threshold_tokens: 2000  # 沙箱专用阈值(更严格,6.10)

# ------------------------------------------------------------------------------
# 8. 沙箱配置(6.7、6.14)
# ------------------------------------------------------------------------------
sandbox:
  enabled: true [runtime]
  workspace_root: "${WORKSPACE}/.sandbox"
  retention_days: 7                        # 工作目录保留天数 [runtime]
  
  languages:
    python:
      command: "python"
      script_extension: ".py"
      min_version: "3.10"
    javascript:
      command: "node"
      script_extension: ".js"
      min_version: "18.0"
  
  limits:                                  # 资源限制(6.7) [runtime]
    cpu_timeout_sec: 300
    memory_limit_mb: 512
    disk_limit_mb: 100
    network_enabled: false
  
  security:
    code_scan_enabled: true [runtime]
    env_sanitization_enabled: true [runtime]
    dangerous_patterns:                    # 预扫描危险模式(6.8)
      - "os\\.system"
      - "subprocess\\.Popen"
      - "pty\\.spawn"
      - "os\\.fork"
      # ... 完整列表见 6.8

# ------------------------------------------------------------------------------
# 9. Skills 配置(7.2、7.6)
# ------------------------------------------------------------------------------
skills:
  # 存储三层流转(2.11)
  storage:
    dev_dir: "./skills"                    # 开发期文件系统目录
    runtime_source: "db_first"             # 运行时优先加载数据库 [runtime]
  
  # Skill 元数据默认值(7.2)
  defaults:
    max_frozen_token: 4000                 # Prompt+示例总 token 上限 [runtime]
    examples_count: 3                      # 少样本示例数量

# ------------------------------------------------------------------------------
# 10. 评估配置(8.8、8.9)
# ------------------------------------------------------------------------------
eval:
  judge_model: "glm-4-flash"               # LLM-as-Judge 模型 [runtime]
  judge_prompt_dir: "./config/judge_prompts"  # V2 场景 Prompt 目录(预留)
  datasets_per_scenario: 20                # 每场景样本数 [runtime]
  regression_subset: 5                     # 版本变更快速回归子集数 [runtime]
  auto_trigger_on_version_change: true     # 版本变更自动触发 [runtime]

# ------------------------------------------------------------------------------
# 11. 可观测性配置(2.10、2.13)
# ------------------------------------------------------------------------------
observability:
  # 日志(2.13)
  logging:
    level: "INFO"                          # 日志级别 [runtime]
    file_path: "${WORKSPACE}/logs/agent.log"
    stdout_enabled: true                   # 同时输出 stdout [runtime]
    trace_id_enabled: true                 # trace_id 字段预留(MVP 留空)
  
  # 磁盘管理(2.10)
  disk:
    warning_gb: 1.5                        # 预警阈值 [runtime]
    block_new_session_gb: 2.0              # 禁止新会话阈值 [runtime]
    force_cleanup_gb: 3.0                  # 强制清理阈值 [runtime]
    react_events_retention_days: 7         # 事件保留天数 [runtime]
    vacuum_schedule: "0 3 * * 0"           # VACUUM 调度(周日凌晨)
```

**运行时可改项说明**:

标注 `[runtime]` 的配置项支持通过 UI 配置面板修改,写入 `config_runtime` 表,无需重启服务;未标注的为静态配置,修改后需重启 Sidecar 生效。

`[MVP]` 全局 config.yaml 骨架为开发期配置参考,实际运行时以 config_runtime 优先。

---

### 9.14 Postgres 全表 ER 与 TTL 汇总

汇总全 8 章涉及的 Postgres 表,给出 ER 关系简图与生命周期清理策略。

**全表清单**:

| 表名 | 用途 | 对应章节 | TTL 策略 | 软删除 |
|---|---|---|---|---|
| sessions | 会话元数据 | 2.10 | 会话结束后保留 30 天,之后归档 | 是(archived_at) |
| messages | 会话消息(含分区元数据) | 2.10、3.2 | 归档后移至 messages_archive,保留 90 天 | 是(compressed 标记) |
| messages_archive | 压缩归档消息 | 3.10 | 保留 90 天后物理删除 | 否 |
| react_events | ReAct 事件流(thinking/tool_call/tool_result/final/checkpoint/error) | 2.10、2.13 | 保留 7 天;3GB 强制清理时收紧 | 否 |
| user_memories | 用户长期记忆 | 2.10、4.3 | 无 TTL(淘汰机制管理) | 是(is_active) |
| kb_chunks | 知识库 chunk + 向量 | 2.10、4.12 | 无 TTL(增量更新管理) | 是(is_active) |
| kb_documents | 知识库文档元数据 | 4.6 | 无 TTL(增量更新管理) | 是(is_active) |
| version_snapshots | 版本快照(Skill/知识库/Prompt) | 2.10、7.3、4.16 | 保留最近 20 个版本 | 否 |
| eval_datasets | 评估数据集 | 2.10、8.3 | 无 TTL(手动管理) | 否 |
| eval_runs | 评估运行记录 | 2.10、8.11 | 保留最近 100 次 | 否 |
| async_tasks | 异步任务状态 | 2.10、5.14 | 任务完成后保留 7 天 | 否 |
| config_runtime | 运行时配置 + API Key 密文 + ws_offset | 2.10、2.12 | 无 TTL | 否 |
| skills | Skills 元数据(PG 运行时副本) | 2.10、2.11 | 无 TTL(版本管理) | 否 |

**ER 关系简图**:

```
┌─────────────┐     ┌─────────────────┐     ┌──────────────────┐
│  sessions   │────→│    messages     │     │ messages_archive │
│  (会话)     │     │  (含分区元数据) │────→│  (压缩归档)      │
└──────┬──────┘     └─────────────────┘     └──────────────────┘
       │
       │
       ↓
┌─────────────────┐     ┌─────────────────┐
│  react_events   │     │  async_tasks    │
│  (事件流)       │     │  (异步任务)     │
└─────────────────┘     └─────────────────┘

┌─────────────────┐     ┌─────────────────┐     ┌──────────────────┐
│  kb_documents   │────→│    kb_chunks    │     │ version_snapshots│
│  (文档元数据)   │     │  (chunk+向量)   │     │ (版本快照)       │
└─────────────────┘     └─────────────────┘     └──────────────────┘
                                                        ↑
                                                        │
┌─────────────────┐     ┌─────────────────┐            │
│     skills      │────→│ version_snapshots│────────────┘
│  (Skills 元数据)│     │  (Skill 快照)   │
└─────────────────┘     └─────────────────┘

┌─────────────────┐     ┌─────────────────┐
│  user_memories  │     │  config_runtime │
│  (用户记忆)     │     │  (配置+密钥)    │
└─────────────────┘     └─────────────────┘

┌─────────────────┐     ┌─────────────────┐
│  eval_datasets  │────→│    eval_runs    │
│  (评估数据集)   │     │  (评估运行)     │
└─────────────────┘     └─────────────────┘
```

**关键关系说明**:

| 关系 | 说明 | 对应章节 |
|---|---|---|
| sessions → messages | 一个会话有多条消息(1:N) | 2.10 |
| sessions → react_events | 一个会话有多条事件(1:N) | 2.10、2.13 |
| messages → messages_archive | 压缩后消息归档(1:N,一条消息可能被多次压缩) | 3.10 |
| kb_documents → kb_chunks | 一个文档拆分为多个 chunk(1:N) | 4.6、4.12 |
| skills → version_snapshots | 一个 Skill 有多个版本快照(1:N) | 7.3 |
| kb_documents → version_snapshots | 知识库批量更新生成快照(1:N) | 4.16 |
| eval_datasets → eval_runs | 一个数据集可运行多次评估(1:N) | 8.3、8.11 |
| sessions → config_runtime | ws_offset 按会话存储(1:1,键值对形式) | 2.3 |

**TTL 清理调度**:

| 清理任务 | 触发时机 | 操作 | 对应章节 |
|---|---|---|---|
| react_events 清理 | 每日凌晨 3 点 + 3GB 强制触发 | 删除 7 天前事件;3GB 时收紧至 3 天 | 2.10、2.13 |
| messages 归档 | 每周日凌晨 4 点 | 30 天前会话的 messages 移至 messages_archive | 2.10、3.10 |
| messages_archive 清理 | 每周日凌晨 5 点 | 删除 90 天前归档 | 3.10 |
| async_tasks 清理 | 每日凌晨 3 点 | 删除 7 天前已完成任务 | 5.14 |
| VACUUM ANALYZE | 每周日凌晨 3 点 | 全表 VACUUM(避开用户活跃时段) | 2.10 |
| 磁盘告警检查 | 每 5 分钟 | 检查数据目录大小,触发三级告警 | 2.10 |

**软删除约定**:

- `is_active = FALSE`:kb_chunks、kb_documents、user_memories 使用,不物理删除,评估回放需要历史数据。
- `archived_at != NULL`:sessions 使用,标记已归档。
- `compressed = TRUE`:messages 使用,标记已被压缩(原消息保留,压缩后生成新消息)。

`[MVP]` 全表 ER 与 TTL 汇总为持久层的完整视图,所有表结构与清理策略均在前序章节已定义。

---

### 9.15 第 1 章补写指引

第 1 章留到最后写,本章仅提示补写时需复用的汇总数据,不展开第 1 章正文。

**第 1 章定位**:

第 1 章为全书概述与设计原则,核心职责是回答"为什么做、做什么、怎么做",为读者建立全局认知。应在第 2-9 章全部完成后撰写,确保概述与实际设计一致。

**补写时需复用的本章数据**:

| 第 1 章小节 | 需复用的本章数据 | 来源 |
|---|---|---|
| 项目背景与目标 | MVP 完整模块清单(概要) | 9.2 |
| 设计原则 | 三大约束落地检查表(概要) | 9.8 |
| 技术选型 | config.yaml 全局骨架(概要) | 9.13 |
| 架构总览 | 模块依赖 DAG(概要) | 9.5 |
| 实施路线 | M0-M4 五阶段里程碑(概要) | 9.4 |
| 边界与约束 | 架构边界守护原则 | 9.11 |

**补写约束**:

1. 第 1 章不引入新设计,仅概述第 2-9 章已锁定的内容。
2. 概述需精确引用对应章节号,便于读者跳转。
3. 设计原则部分直接复用三大约束(上下文质量优先 / 缓存友好 / 评估驱动迭代),不新增原则。
4. 实施路线部分概述 M0-M4 五阶段,不展开 Done Criteria(已在 9.4 详述)。
5. 边界与约束部分复用 9.11 三条原则,不新增规则。

`[MVP]` 第 1 章补写为文档收尾工作,复用本章汇总数据即可,无需额外设计。

---

### 9.16 本章 MVP/V2 边界

本章属于 MVP 文档的一部分,无 V2 代码接口;但"路线执行过程"本身是 MVP 范围,V2 路线仅规划不实施。

**MVP 范围**(本章已实现):

| 内容 | 范围 | 对应小节 |
|---|---|---|
| 章节定位与整合策略 | 完成 | 9.1 |
| MVP 完整模块清单 | 整合 2-8 章,全量 | 9.2 |
| V2 扩展完整清单 | 整合 2-8 章,全量 | 9.3 |
| M0-M4 五阶段里程碑 | 全量 + Done Criteria | 9.4 |
| 模块依赖 DAG | 全量 + 关键路径 + 跨层依赖 | 9.5 |
| 单人开发推荐顺序 | 27 步全量 | 9.6 |
| MVP 验收标准 | 30 项全量 | 9.7 |
| 三大约束落地检查表 | 上下文质量 19 项 + 缓存友好 18 项 + 评估驱动 22 项 | 9.8 |
| 风险识别与缓解 | 11 项全量 | 9.9 |
| V2 演化路线优先级 | P1-P4 全量 | 9.10 |
| 架构边界守护原则 | 三条原则 + 10 项检查清单 | 9.11 |
| 回滚与降级机制总览 | 回滚 5 类 + 降级 10 类 | 9.12 |
| 全局 config.yaml 骨架 | 11 段全量 | 9.13 |
| Postgres 全表 ER + TTL | 13 张表 + ER 图 + 6 项清理调度 | 9.14 |
| 第 1 章补写指引 | 复用数据映射表 | 9.15 |

**V2 规划范围**(本章仅规划,不实施):

| 内容 | 范围 | 对应小节 |
|---|---|---|
| V2 演化优先级 | P1-P4 推荐顺序,不承诺时间点 | 9.10 |
| V2 接口验证 | 每次启动 V2 项前更新 9.3 清单状态 | 9.11 |

**与三大约束的对应**:

- 上下文质量优先 → 9.8 检查表确认 19 项落地实现无遗漏;9.13 config.yaml 骨架含上下文工程配置段。
- 缓存友好 → 9.8 检查表确认 18 项落地实现无遗漏;9.5 DAG 标注 KV Cache 约束在关键路径上。
- 评估驱动迭代 → 9.8 检查表确认 22 项落地实现无遗漏;9.7 验收标准对接第 8 章评估指标;9.12 回滚降级机制支持故障分析。

---

第 9 章起草完成。本章整合了前 8 章的 MVP/V2 边界,形成分阶段实施里程碑(M0-M4)、模块依赖 DAG、单人开发推荐顺序(27 步)、MVP 验收标准(30 项)、三大约束落地检查表(59 项)、风险识别与缓解(11 项)、V2 演化优先级(P1-P4)、架构边界守护原则(3 条 + 10 项检查清单)、回滚与降级机制总览(5 类回滚 + 10 类降级)、全局 config.yaml 骨架(11 段)、Postgres 全表 ER 与 TTL 汇总(13 张表)、第 1 章补写指引,共 16 节,所有内容严格复用前 8 章已锁定决策,未引入新方案。

后续衔接:

- 第 1 章概述与设计原则:复用本章 9.2/9.4/9.5/9.8/9.11/9.13 的汇总数据,概述全书设计与实施路线。
