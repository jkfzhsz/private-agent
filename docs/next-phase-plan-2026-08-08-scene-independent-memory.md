# 下一轮升级设计方案：三场景独立 + 记忆系统优化

- 日期：2026-08-08
- 状态：设计稿（待评审）
- 依据：private-agent-blueprint.md §7(技能) §4(记忆)；论文《Memory for Large Language Models》(arXiv 2607.25380) 分类学比对结果；用户 2026-08-08 需求
- 对应版本：0.5.0（目标）

---

## 1. 目标与背景

用户提出下一轮升级聚焦两点：

1. **三场景相互独立（场景已重新划分，用户 2026-08-08 17:50 确认）**：
   - **子瞻**：工作与学习（文档处理、数据分析、网页研究、学习辅导）
   - **白圭**：投资与理财（行情、基金、宏观、财务分析）
   - **清和**：生活健康与美学设计（健康管理、美学设计、前端设计）
   - 全局记忆记录用户偏好、进行中项目概况；
   - 三个场景各有**完全独立**的记忆——自己的工作流、专属 skill、专属 MCP 调用规则；
   - 三个场景分别有**自己的名字**（子瞻/白圭/清和）；
   - 历史记录按三个大类分别保存；
   - 在此基础上进一步强化各场景智能体的专业能力。
2. **记忆功能优化**：按《Memory for LLMs》调研比对结果，落地可借鉴方向。记忆是大项，涵盖用户画像、全局记忆、知识库、历史对话、各类调用规则，需持续强化。

---

## 2. 现状盘点（2026-08-08 实证）

| 维度 | 现状 | 与目标的差距 |
|---|---|---|
| 场景定义 | 3 个 skill：`office`/`data_analysis`/`frontend_design`，skill.yaml 含 `scenario` + 工具白名单 + 权限声明 | **职责语义需重构**：office→子瞻(工作学习)、data_analysis→白圭(投资理财)、frontend_design→清和(生活健康美学)；且缺中文名/品牌名 |
| 场景工具集 | `dependencies.tools` 白名单 + config `tools.mcp.skill_binding`（office→ifind 金融系+mempalace+Searchpin；data_analysis→ifind\*+mempalace+Searchpin；frontend_design→mempalace+Searchpin） | 绑定已存在；**用户确认子瞻(office)保留 ifind**（工作与金融相关），白圭(投资理财)为金融主场景；可增配场景专属 skill 挂载 |
| 知识库 | `kb_documents.scenario`（office/data_analysis/frontend_design）已按场景隔离；`auto_retrieve: false` | 场景隔离已有；自动检索未开；现有 KB 文档内容按旧职责组织，需按新职责补充（投资/健康类） |
| 记忆 | `user_memories` 表：`type`(preference/fact/todo/decision/correction) + `importance` + `is_active`，**全部全局（user_id=1），无场景维度**；提取每 8 轮一次（memory.extract_interval_turns）、注入上限 10 条×300 字符 | **缺场景独立记忆**；全局记忆未分层（偏好与项目概况混存） |
| 历史会话 | `sessions.locked_skill_name`（锁定场景）+ `folder`（分组）+ `summary` | 数据上可分场景；前端历史树未按场景分大类展示 |
| 系统提示 | 三场景各有 `system_prompt.md`（27/48/32 行）：角色定位/任务约束/工具规范/输出格式 | 专业深度不足，未含场景工作流/领域知识强化；需按新职责（投资/健康）重写 |
| 用户画像 | config `context.identity`（静态，可覆盖）；记忆 type=preference 存在但无画像聚合 | 无动态画像层（偏好+项目概况自动聚合注入） |
| 记忆宫殿 | MemPalace MCP 接入（skill_binding 全场景装配 mempalace+Searchpin） | 抽屉维度在 MCP 端管理，PA 侧未按场景路由 |

**关键结论**：知识库已按场景隔离 ✅；历史数据具备场景维度（locked_skill_name）✅；**记忆是最大缺口**（全局单一、无场景归属、无画像层）；场景职责需按新划分重构（白圭=投资理财、清和=健康美学，system_prompt/KB 需重写补充）。

---

## 3. 设计方案 A：三场景独立

### A1. 场景命名与品牌（每个场景有自己的名字）

- **场景职责与名字（用户 2026-08-08 17:50 定名 + 18:10 人格化补充）**：

| 场景名 | 对应历史名人 | 职责 | 技术标识（不变） | 人格特质（对话中体现） |
|---|---|---|---|---|
| **子瞻** | 苏轼（字子瞻） | 工作与学习：文档处理、数据分析、网页研究、学习辅导 | `office` | 博学多才、豁达乐观、文采斐然；**政治意识**：遵守社会主义核心价值观，将国家政治导向、党的理论知识、国有商业银行工作"三位统一" |
| **白圭** | 白圭（战国商祖） | 投资与理财：行情、基金、宏观、财务分析 | `data_analysis` | 商祖风范：**长远眼光、价值投资、周期判断、逆向交易、交易纪律、克制贪婪、合理的资产配置比例**（"人弃我取、人取我与""趋时若猛兽挚鸟之发"的现代演绎） |
| **清和** | 谢安（东晋名士） | 生活健康与美学设计：健康管理、美学设计、前端设计 | `frontend_design` | 从容雅量、名士风范（淝水之战镇定自若）；**关心身心健康**，提供饮食/作息/锻炼科学建议；**承担美化职责**：将子瞻、白圭的产物针对不同使用场景美化，生成不同类型的文档 |

- `skill.yaml` 扩展字段（SkillManifest 已支持 `display_name`/`avatar`，复用现有 `PUT /admin/skills/{name}/meta` 改名链路）：
  ```yaml
  display_name: "子瞻"           # 场景中文名（用户可改，初始=确认名）
  scene_name: "子瞻"             # 场景专属名字（与 display_name 同步，语义更明确）
  avatar: "office"               # 场景专属图标 key（前端映射到图标，三个场景各一个）
  scene_profile:
    role: "工作与学习伙伴：文档处理、数据分析、网页研究、学习辅导"
    ...
  ```
- **技术标识不变**（office/data_analysis/frontend_design）：`sessions.locked_skill_name`、`user_memories.scope`、`kb_documents.scenario`、skill_binding 全部沿用技术标识，0 破坏现有数据与绑定；中文名只存在于显示层与 `display_name`/`scene_name` 字段。
- 前端：场景选择面板（SkillSelectionPanel）、侧边栏历史分组、对话区助手名，一律显示 `scene_name`（回退 `display_name` → `name`）。

### A2. 场景独立记忆（核心改动）

**数据模型**：`user_memories` 增加 `scope VARCHAR(20) NOT NULL DEFAULT 'global'`：
- `global` = 全局记忆（用户偏好、进行中项目概况、协作规则——所有场景可见）
- `office` / `data_analysis` / `frontend_design` = 场景私有记忆（**技术标识**，显示层映射为 子瞻/白圭/清和）

索引：`idx_memories_scope ON user_memories(user_id, scope, importance DESC) WHERE is_active = TRUE`。

**提取（写入）**：`memory/manager.py` 的提取入口（maybe_extract/on_session_end/manual_extract）传入会话的 `locked_skill_name`：
- 会话锁定场景 → 提取的记忆 `scope = locked_skill_name`；
- 会话无锁定（普通全局对话）→ `scope = global`；
- 提取 prompt 增加一行指令："识别这条记忆属于哪个场景（工作学习→office / 投资理财→data_analysis / 生活健康美学→frontend_design / 全局通用→global），全局通用归 global"——由模型判断归属，避免全部落 global。

**注入（读取）**：`memories_repo.get_top_active` 增加 `scope` 过滤参数。**注入策略（用户 2026-08-08 18:10 定案）**：
- **全局记忆只注入"身份 + 核心偏好"**（用户画像 `user_profile` 摘要：name、协作偏好、沟通风格等，常驻 1-3 条）——目的是让智能体"知道我是谁、我的偏好"；
- **其余注入配额全部给场景记忆**（最大化当次任务的上下文，默认 10 条上限中场景记忆占大头，如全局 2 + 场景 8，比例存 `memory.inject_ratio` 可配）；
- **全局记忆的其他内容（进行中项目概况、历史偏好细节）不常驻，按需检索**：扩展 `search_knowledge`（或新增 `memory_search` 工具）支持检索 `scope='global'` 的记忆，当任务相关时由模型主动检索；
- 非场景会话：仅 `scope='global'`（全部按注入策略的全局子集 + 按需检索）。

**隔离验证**：同一用户开三个场景对话（子瞻/白圭/清和），各自写入的记忆互不可见；全局记忆按需可见。

### A3. 场景专属工作流 / skill / MCP 调用规则

- `skill.yaml` 新增 `scene_profile` 段（可选，SkillManifest 加 `scene_profile: dict`）：
  ```yaml
  # 子瞻(office) —— 苏轼人格
  scene_profile:
    persona: "苏轼(字子瞻)：博学多才、豁达乐观、文采斐然。对话中体现：善用典故与诗意表达、遇困从容、鼓励用户"
    role: "工作与学习伙伴：文档处理、数据分析、网页研究、学习辅导"
    values: "政治意识：遵守社会主义核心价值观；将国家政治导向、党的理论知识、国有商业银行工作三位统一"
    workflow: ["理解需求", "规划步骤", "工具执行", "输出文件", "总结"]
    rules:
      - "文档处理优先用 pandas/python-docx，学习类任务先拆解知识点再讲解"
      - "引用网页必须标注来源；涉政策/理论表述以权威来源为准"
  # 白圭(data_analysis) —— 商祖人格
  scene_profile:
    persona: "白圭(战国商祖)：长远眼光、重价值投资；善周期判断与逆向交易('人弃我取、人取我与')；严守交易纪律、克制贪婪"
    role: "投资理财顾问：行情、基金、宏观、财务分析"
    workflow: ["明确标的目标", "取行情/财务数据(ifind)", "宏观与基本面分析", "周期/估值判断", "风险提示与仓位建议", "给出结论"]
    rules:
      - "投资建议必须带风险提示与数据来源(ifind)"
      - "涉及持仓/盈亏时先取用户白圭场景记忆中的持仓概况"
      - "给资产配置建议时讲比例与纪律(如再平衡/止损位)，不承诺收益，不构成投资建议"
  # 清和(frontend_design) —— 谢安人格
  scene_profile:
    persona: "谢安(东晋名士)：从容雅量、镇定自若；关心身心健康，注重生活品质与美学"
    role: "生活健康与美学设计管家：健康管理(饮食/作息/锻炼)、美学设计、前端设计"
    workflow: ["倾听需求", "健康/美学知识支撑", "设计方案", "产出(HTML/清单/计划/文档)", "跟进"]
    rules:
      - "健康类建议强调咨询专业医师、不做诊疗结论"
      - "承担美化职责：把子瞻/白圭的产物按使用场景美化，生成不同类型的文档(报告/简报/演示/可视化)"
      - "设计类输出附设计说明与可复用代码"
  ```
  （现状这部分的语义已在 system_prompt.md，scene_profile 是结构化版本，供前端配置界面与注入模板复用。）
- **场景专属 skill 挂载**：SkillManifest 增加 `scene_scope: list[str]`（空=通用）。用户 2026-08-08 确认：**reasonix 15 技能三个场景均挂载**（全部 `scene_scope` 留空=通用，子瞻/白圭/清和皆可用），本轮不做按场景分流；`scene_scope` 机制保留，供后续确有需要时按场景分流。
- **MCP 绑定（用户 2026-08-08 确认：子瞻保留 ifind，因工作与金融相关）**：config `tools.mcp.skill_binding` 调整为：
  - `office`(子瞻)：**hexin-ifind-ds-\* 金融系**（股票/指数/新闻/基金/债券/宏观）+ mempalace + Searchpin
  - `data_analysis`(白圭)：**hexin-ifind-ds-\* 金融全系** + mempalace + Searchpin
  - `frontend_design`(清和)：mempalace + Searchpin
  - mempalace/Searchpin 保持全场景装配（记忆/搜索是跨场景通用能力）。

### A4. 历史记录分三大类保存

- 数据层已具备（`sessions.locked_skill_name` 即场景技术标识）；补充：新建会话时若未指定场景，`folder` 保持 NULL（全局）；指定场景后 `folder` 默认写入场景 `scene_name`（子瞻/白圭/清和）。
- 前端侧边栏历史树（TaskTree）增加**按场景分组**：三个可折叠组（**子瞻/白圭/清和**，组标题显示场景名 + 头像 + 会话数）+ 全局组（未分类会话）。
- 设置页/历史页可按场景过滤查看；场景会话的 summary/标题互不混排。

### A5. 场景专业能力强化

- 三场景 `system_prompt.md` 按**新职责 + 历史名人人格**重写强化（每份 60-100 行）：
  - **角色定位**：场景名（子瞻=苏轼 / 白圭=商祖 / 清和=谢安）人格化开场（persona）+ 性格/专业画像 + 擅长边界 + **价值观**（子瞻：政治意识/社会主义核心价值观/银行工作三位统一）；
  - **核心工作流**：该场景的标准任务处理流程（子瞻：理解→读取→处理→产出→总结；白圭：明确标的目标→取 ifind 数据→宏观/基本面分析→周期估值→风险提示与仓位→结论；清和：倾听需求→健康/美学知识支撑→设计方案→产出→跟进 + 美化职责）；
  - **领域知识要点**：各场景常见任务的领域 checklist（白圭：行情/基金/宏观/财务指标口径、估值与周期判断、资产配置比例与交易纪律；清和：健康管理常识（饮食/作息/锻炼）/设计原则/前端规范/文档美化类型；子瞻：文档/学习/研究流程、政策理论表述规范）；
  - **输出规范**：各自输出格式/文件组织/命名规范（清和额外：子瞻/白圭产物美化后的多类型文档模板）。
- **知识库自动检索**：三场景 `knowledge_base.auto_retrieve` 打开（kb 已按 scenario 隔离），场景会话自动注入该场景知识库 top-N 片段。
- **KB 语料规划（用户 2026-08-08 确认：白圭/清和 KB 本轮一并设计搭建）**：

| 场景 | scenario | 初始语料清单 | 来源 |
|---|---|---|---|
| 子瞻 | office | 现有办公/文档/学习类文档（保留） | 已入库/既有 |
| 白圭 | data_analysis | 投资理财类：宏观与行业研报、基金产品说明、财务指标口径说明、持仓分析模板 | 用户提供（本地已有《腾讯控股_巴菲特视角研究报告》可入库；或上传研报 zip） |
| 清和 | frontend_design | 美学设计类：`FlowSpace-Design-System.md`（设计系统文档，建议入库）、前端规范；健康管理类：健康指南/指标说明 | 设计系统文档可直接入库；健康类用户提供 |

  M2 实施时一并完成入库（设置页上传 / zip / manual 入库接口均已具备），并补充对应场景检索测试。
- **示例增强**：examples 从 2 条增至 3 条，注入对应场景真实案例。

---

## 4. 设计方案 B：记忆优化（论文可借鉴方向落地）

论文比对结论回顾（详见 2026-08-08 分析）：PA 属"显式+在线+长期"查找式记忆；三个主要风险 = 提取即压缩（irreversible loss）、重要性打分启发式（threshold sensitivity）、注入带宽受限（retrieval bias）。落地四项：

### B1. 全局画像层（Long-Term 分层 + 统一记忆）

- 新增画像聚合：`user_memories` 中 `type='preference'` 且 `scope='global'` 的记忆，按内容聚类/高频合并 → 生成/更新**用户画像**（存 `user_profile` 表或 config_runtime key `agent.user_profile`）：
  ```text
  画像字段: {name, 协作偏好, 常用工具, 沟通风格, 进行中项目: [{项目名, 状态, 关键路径}], 更新时间}
  ```
- 会话启动注入：身份/协作规则（现有 identity）+ 画像（偏好 + 进行中项目概况）一起注入 Frozen Zone 头部；画像变更时自动刷新（记忆 correction 类更新画像）。
- 收益：高频偏好不靠每轮 300 字符碎片注入，一次聚合常驻（对应论文"统一记忆理论"的最小实现）。

### B2. 记忆 scope 化（A2，前述）

### B3. 巩固而非硬删（缓解 irreversible loss）

- 现有驱逐：`deactivate_lowest`/`deactivate_expired`（importance<0.3 且超 30 天 / 超 200 条淘汰）→ **硬删前先巩固**：
  - 新增 `user_memories_archive` 表（id/content/scope/type/importance/archived_at/摘要）；
  - 驱逐时把原记忆内容做 1 行摘要（模型压缩）写入 archive，再 deactivate 原记录；
  - archive 不参与注入，但 `search_knowledge` 可检索（按需召回，如用户问"我之前说过什么"）。
- 对应论文 §IV-D"分层巩固（consolidation）"与表 II"admission/eviction/consolidation"的 consolidation 环节。

### B4. 注入质量（缓解 retrieval bias）

- `get_top_active` 注入排序升级：`scene 优先 + 全局常驻` → 同 scope 内 `importance × 时间衰减因子(1 / (1 + ln(1 + days_since_access)))` 排序；
- 内容去重：注入前按内容 hash 去重（避免同类记忆反复注入）；
- 动态配额：全局 4 条 + 场景 6 条（可配），超长记忆截断 300 字符（现有）。

### B5. 评估（多维度）

- 记忆提取回测：抽样 N 条已提取记忆，人工/模型判定"与原文一致性 + 场景归属正确性"，统计准确率；
- 注入命中统计：记忆 `access_count` 增长情况，低访问记忆标记可整合；
- 归档召回测试：确认"已归档记忆仍可 search_knowledge 召回"。

---

## 5. 数据模型变更汇总

| 变更 | 对象 | 说明 |
|---|---|---|
| 新增列 | `user_memories.scope` | VARCHAR(20) DEFAULT 'global' + 索引；存量数据默认 global（不迁移旧记忆归属，可从 source_session_id 反查 locked_skill_name 回填可选） |
| 新增表 | `user_memories_archive` | 巩固归档（B3） |
| 新增表 | `user_profile`（或 config_runtime key） | 用户画像聚合（B1） |
| 新增字段 | `skills` manifest JSONB：`scene_name`/`scene_profile`/`scene_scope` | SkillManifest 扩展（A1/A3） |
| 无变更 | `sessions`/`kb_documents` | 已具备场景维度（locked_skill_name/scenario） |

## 6. 分阶段实施计划

- **M1（场景独立底座）**：DB 迁移（scope 列 + 索引 + 归档表 + 画像表）；SkillManifest 扩展（scene_name/scene_profile/scene_scope）；**三场景命名写入（子瞻/白圭/清和 + scene_profile 职责 + 对应 system_prompt 角色定位行）**；记忆提取/注入 scope 化；**skill_binding 金融 MCP 收敛到 data_analysis(白圭)**；前端历史树按场景分组展示（子瞻/白圭/清和）；后端 pytest（scope 提取/注入隔离、回填）+ 前端 tsc/vitest。
- **M2（场景专业强化）**：三场景 system_prompt 按新职责**+名人人格**重写（子瞻=苏轼含价值观、白圭=商祖含投资哲学、清和=谢安含身心健康+美化职责）；KB auto_retrieve 打开 + **KB 语料规划落地（白圭投资类、清和设计系统+健康类一并入库，见 A5 表）**；**reasonix 15 技能三场景均挂载（scene_scope 留空=通用）**；示例增强；端到端验证（三场景各一轮真实任务：子瞻写文档、白圭查行情、清和出设计/美化）。
- **M3（记忆优化）**：画像聚合与注入；巩固归档（驱逐前摘要）；注入排序/去重/配额；评估工具（回测/命中统计）。

## 7. 风险与权衡

| 风险 | 对策 |
|---|---|
| DB 迁移影响存量 | scope 默认 global，迁移不阻断；可选按 source_session_id 回填 |
| 注入带宽：场景+全局可能超上限 | 动态配额（全局 4+场景 6，可配）；去重/衰减控制 |
| 提取归属误判（模型把场景记忆落 global） | 提取 prompt 显式要求归属判定 + 回测统计纠偏 |
| **场景职责重构副作用**（白圭=投资理财 与 原 data_analysis 数据分析语义差异；清和=健康美学 与 原前端设计差异） | 技术标识不变，仅职责/角色/prompt/绑定调整；数据分析能力作为白圭与子瞻的底层能力保留（子瞻工作学习仍需统计）；KB 按新职责补充而非删除旧文档 |
| **金融 MCP 双场景装配**：ifind 同时装配 office(子瞻) 与 data_analysis(白圭)，工具池变大 | 保留 `tool_selection` top-N 动态裁剪（每轮只注入相关工具 schema），装配负担可控；子瞻/白圭同一金融数据底座，职责边界靠 scene_profile 规则约束 |
| 场景改名与锁定/绑定冲突 | 沿用 display_name 原则：只改显示名，标识符不变 |
| 画像聚合过拟合/隐私 | 画像仅本地存储；correction 类记忆可覆盖画像字段 |
| 巩固归档存储膨胀 | 归档只存 1 行摘要 + 原始内容可选截断；TTL 可配 |

## 8. 验证方案

- 后端 pytest：`test_memory_scope.py`（提取打标/注入过滤/全局常驻/隔离）、`test_memory_archive.py`（巩固归档/召回）、`test_user_profile.py`（聚合/注入）、`test_scene_skill.py`（scene_scope 挂载过滤）；现有 1200+ 测试零回归。
- 前端：tsc 0 错；vitest 16 过；历史树场景分组 UI 测试。
- 用户验收：① 三场景各开对话，互相看不到对方记忆，全局偏好三处可见；② 每个场景显示自己的名字与头像；③ 历史按三大类分组；④ 会话启动可见画像（偏好+进行中项目）。

## 9. 待用户确认项（2026-08-08 18:10 全部确认完毕）

1. ✅ **三场景名字 + 历史名人人格**：子瞻=苏轼 / 白圭=商祖 / 清和=谢安（人格特质见 A1 表、A3 scene_profile）。
2. ✅ **子瞻保留 ifind**（工作与金融相关）——金融 MCP 同时装配子瞻与白圭。
3. ✅ **reasonix 15 技能三个场景均挂载**（不按场景分流，scene_scope 留空=通用）。
4. ✅ **白圭/清和 KB 本轮一并设计搭建**（语料规划见 A5 表）。
5. ✅ **注入策略**：全局只注入"身份+核心偏好"画像（1-3 条），其余配额全给场景记忆（默认全局 2 + 场景 8，`memory.inject_ratio` 可配），全局其他内容按需检索（扩展 search_knowledge/memory_search）。

> 文档自此定稿，可直接进入实施。

---

## 附录：下一对话开场提示词

```
你是 Private Agent（0.5.0）升级实施会话。本轮目标已由设计文档锁定：
docs/next-phase-plan-2026-08-08-scene-independent-memory.md（2026-08-08 定稿）。

背景：
- 0.4.4 已完成：技能恢复（expandvars 修复）、主题淡入淡出、头像统一、智能体改名。
- 三场景已重新划分并命名（用户确认）：子瞻=工作与学习(office) / 白圭=投资与理财(data_analysis) /
  清和=生活健康与美学设计(frontend_design)。技术标识不变，中文名只影响显示层与 scene_name。
- 用户确认项：①子瞻保留 ifind（工作与金融相关）；②reasonix 15 技能三场景均挂载（不按场景分流）；
  ③白圭/清和 KB 本轮一并设计搭建（语料规划见文档 A5 表）；
  ④三场景历史名人人格化：子瞻=苏轼（政治意识/社会主义核心价值观/国有商业银行工作三位统一）、
    白圭=商祖（价值投资/周期判断/逆向交易/交易纪律/克制贪婪/资产配置）、清和=谢安（身心健康关怀+美化子瞻白圭产物）；
  ⑤注入策略：全局只注入身份+偏好画像（1-3 条），其余配额全给场景记忆（默认 2:8），全局其他内容按需检索。
- 论文《Memory for Large Language Models》(arXiv 2607.25380) 已调研比对，可借鉴方向已写入文档 §4。
- 现状：kb 已按场景隔离；sessions.locked_skill_name 具备场景维度；user_memories 全局无场景字段（最大缺口）。

本轮任务（严格按文档分阶段执行）：
M1 场景独立底座：user_memories 加 scope 列 + 索引；新增 user_memories_archive/user_profile；
   SkillManifest 扩展 scene_name/scene_profile/scene_scope；三场景命名与人格写入（子瞻=苏轼/白圭=商祖/清和=谢安）；
   记忆提取按会话场景打标、注入策略=全局仅身份+偏好画像（1-3 条）+ 场景记忆占余量（默认 2:8），
   全局其他内容按需检索（扩展 memory_search / search_knowledge）；
   skill_binding 调整：office(子瞻)与 data_analysis(白圭) 均装配 ifind 金融系，frontend_design(清和) 通用；
   前端历史树按场景分三组展示（子瞻/白圭/清和）。
M2 场景专业强化：三场景 system_prompt 按新职责+名人人格重写（子瞻=苏轼含价值观、白圭=商祖含投资哲学、
   清和=谢安含身心健康与美化职责）；KB auto_retrieve 打开 + KB 语料落地（白圭投资类、清和设计系统+健康类）；
   reasonix 15 技能三场景均挂载（scene_scope 留空=通用）；示例增强。
M3 记忆优化：画像聚合注入；驱逐前先巩固归档（摘要入 archive 再 deactivate）；
   注入排序（importance×时间衰减）+ 去重 + 动态配额；评估工具（回测/命中统计）。

实施约束（必须遵守）：
1. 不执行打包操作（打包 exe 由用户在普通 CMD 手动跑 build-electron.bat）；前端构建验证用
   `vite build --emptyOutDir false`（WorkBuddy 沙箱 safe-delete 会拦截 dist 清理）。
2. 不使用子代理/子任务 Agent，全部由主代理直接执行。
3. 大改动先写代码后验证：后端 pytest（加载 backend/.env，--ignore=test_eval_full_cycle.py，
   勿两进程并发操作同一测试库）、前端 tsc --noEmit + vitest，必须 0 回归。
4. 排查问题先取证据（DB/日志/真实调用），验证路径 = 用户真实触发路径。
5. 每完成一个阶段向用户汇报：改动文件清单 + 测试结果 + 剩余风险。

第一步：读设计文档 §3-§8，确认理解后从 M1 的 DB 迁移开始实施。
```

---

*文档定稿（2026-08-08 18:10，§9 全部确认）。可直接进入实施。*
