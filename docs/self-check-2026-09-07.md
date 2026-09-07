# Private Agent 全面自检报告（四场景设计梳理 + 缺陷排查 + 下一阶段方向）

> 日期：2026-09-07 | 执行：WorkBuddy 主代理（无子代理）
> 证据基线：config.yaml / 四场景 skill 定义 / system_prompt / docs 46 份 /
> 生产库只读查询（kb_documents、user_memories、sessions、missions、react_events、
> system_metrics、skill_lessons）/ git log（HEAD=a67e27c）
> 当前版本：0.6.0（Mission 层已收尾）+ 0.5.1 深化项（P0/P1 已实施）+ 沙箱方案 A/B 已落地

---

## 一、结论（先行）

1. **四场景架构本身是健康且自洽的**：人格、职责边界、工具种子、工作区、Harness 四层配置均已落地且互相对应；设计文档→代码→配置三条线一致。
2. **核心问题不在"缺功能"，而在"已建机制空转"**：本次自检最重要的三个发现全是"实施了但生产未生效/未验证"——
   - **Mission 层（0.6.0 最大投资）生产零使用**：`missions` 表 **0 行**，真实链路验收从未执行；
   - **子瞻场景记忆为零**：106 个会话（全场景最多）但 `scope=office` 记忆 **0 条**；
   - **embedding 监控落库（P1-D）生产无数据**：`system_metrics` 无 `kind='kb'` 记录。
3. **场景间资源分配严重不对称**：清和 PG KB 仅 3 篇（health-wiki 十页知识库闲置未挂载）；经验沉淀 office=2 条 vs 清和 32 / 白圭 29。
4. 下一阶段主线建议：**A 阶段"激活空转机制"（验证 > 新建）→ B 阶段"场景均衡补强" → C 阶段"质量增强"**。不建议在 A 阶段完成前启动任何新能力域。

---

## 二、四场景设计完整梳理

### 2.1 设计对照总表（代码级核实）

| 维度 | 无涯（monitor） | 子瞻（office） | 白圭（data_analysis） | 清和（frontend_design） |
|---|---|---|---|---|
| 人格 | 《庄子》"知也无涯"·项目进化者 | 苏轼·博学豁达·工作学习伙伴 | 战国商祖·价值投资/周期/纪律 | 谢安·从容雅量·生活健康美学 |
| 定义方式 | **agent-profile.json + system_prompt.md（无 skill.yaml，特殊通道）** | skill.yaml 全件 | skill.yaml 全件 | skill.yaml 全件 |
| 职责 | 监控诊断/代码进化/经验调度/Mission 编排 | 文档处理/数据分析/网页研究/学习辅导 | 行情/基金/宏观/财务分析 + 组合引擎 | 健康管理/美学设计/前端设计/产物美化 |
| MCP 工具种子 | mempalace + Searchpin（刻意剔除 ifind） | ifind 全系 + mempalace + Searchpin | ifind 全系 + mempalace + Searchpin | mempalace + Searchpin |
| scene_skills 类目 | engineering, meta | documents, writing | documents | design, engineering |
| 专属工作区 | D:\PA\wuya | D:\PA\zizhan | D:\PA\baigui | D:\PA\qinghe |
| Harness | ✅（profile 通道） | ✅（audience=国有商业银行信贷人员） | ✅（含 risk_profile_summary 运行时回填位） | ✅（含健康免责 tone） |
| PG KB 文档 | —（不需要） | **37**（商业银行/金融五篇/工行三库） | **29**（家庭财富/证券投资两库 + 风险画像/投资框架） | **3**（仅 kb_assets 两件+1） |
| 评测集 | 8 任务 | 10 任务 | 10 任务 | 10 任务 |
| 边界约束 | 禁改场景人格/密钥/用户数据 | 不给投资建议、不做医疗诊断 | 建议必带风险提示+数据来源；风险画像缺失先采集 | 不做诊疗结论；反 AI 默认设计纪律 |

### 2.2 生产使用分布（2026-09-07 只读查询）

| 指标 | 无涯 | 子瞻 | 白圭 | 清和 | 全局/其他 |
|---|---|---|---|---|---|
| 会话数 | 58（locked=None） | **106** | 47 | 27 | 测试残留 4（apple/airbnb/bugatti/article-writer） |
| 活跃场景记忆 | — | **0** ⚠️ | 9 | 5 | global 8 |
| skill_lessons 经验 | 4 | **2** ⚠️ | 29 | 32 | global 1 |
| 最近活跃 | 09-05 | 09-04 | **08-24（两周未用）** | 09-05 | — |

其他关键生产数据：
- `react_events` 总量 521K（近 7 天 3.6K，系统在用）；
- `token_usage` 297 条 / 累计 **1627 万 tokens**（08-18 起）——**记忆中"token_usage 未落库"已过时，实际已落库且 metrics_collector 已消费**；
- KB：69 篇文档 / **708 活跃 chunks**（8-27 时仅 85 chunks，已增长 8 倍——query_rewrite、HNSW 对照等"语料量触发项"应重估）；
- `missions` 表 **0 行**；`system_metrics` 仅 system/session 两类，**无 kind='kb'**。

### 2.3 设计层面评价（逐场景）

**无涯**：职责体系最完整（监控→诊断→进化→经验→评估闭环→Mission 编排五层），权限分级（低风险直接做/核心改动审批）设计清晰。**弱点**：无 skill.yaml 标准件，靠 agent-profile.json 特殊通道且注释要求"与 system_prompt.md 手工保持同步"——双源漂移风险；Mission 层建成后未经真实使用验证。

**子瞻**：人格与"三位统一"价值观明确，工作流完整。**弱点**：会话量第一但记忆/经验双零（机制性问题，见缺陷 #2/#5）；与白圭工具种子完全相同，职责边界仅靠 prompt 软约束。

**白圭**：四场景中设计深度最高——风险画像驱动（五维度硬约束）、组合体检引擎（HHI/相关性/再平衡偏离全程 code_execution 计算）、"投资建议书六段"输出规范，是"挖深"战略的样板。**弱点**：两周未使用；批次4（前端风险问卷 UI）暂缓；画像缺失时首轮流程偏长。

**清和**：双职责（健康+设计）独特，Taste 设计品味准则（三旋钮/反 AI 默认/Pre-Flight 自检）是四场景中最精细的输出纪律。**弱点**：PG KB 仅 3 篇，D:\health-wiki 十页知识库（08-12 建）从未导入——健康场景的检索能力与设计投入不匹配。

---

## 三、缺陷与不足清单（按严重度分级，均附证据）

### P0 —— 机制空转（已建投资未产生价值）

| # | 缺陷 | 证据 | 影响面 |
|---|---|---|---|
| 1 | **Mission 层生产零使用** | `missions` 表 0 行；phase-closeout-mission §六-2 自认"真实验收建议打包后执行"，至今未做 | 0.6.0 全部投资（~2070 行 + 124 测试）的真实可靠性未知；定时监督（W6）亦未启用 |
| 2 | **子瞻场景记忆为零** | 106 会话 / `user_memories scope=office` 0 条（白圭 9、清和 5、global 8） | "场景独立记忆"对最高频场景空转；extract_interval_turns=8 触发或 scope 归因存疑 |
| 3 | **embedding 监控落库无生产数据** | `system_metrics` 无 kind='kb'（P1-D commit 3db7887 已实现 metrics_sink） | 实施了但从未被触发验证；embedding 故障时无监控兜底（设计目的落空） |

### P1 —— 设计缺口 / 场景不对称

| # | 缺陷 | 证据 | 影响面 |
|---|---|---|---|
| 4 | **清和健康知识库未挂载** | import_kb_dirs.py 五源无 health-wiki；frontend_design PG KB 仅 3 篇 | 清和健康建议只能靠 1 个 kb_asset + 模型通识，D:\health-wiki 十页投资闲置 |
| 5 | **经验沉淀不对称** | skill_lessons：office=2 vs 清和 32 / 白圭 29 | 与 #2 同源可能：反思/提取管线在 office 场景欠触发；高频场景反而无积累 |
| 6 | **无涯双源定义漂移风险** | harness.py L28-32 注释"内容与 system_prompt.md 保持同步"靠人工 | 改一处忘另一处 → 注入画像与实际 prompt 不一致 |
| 7 | **版本号三处漂移** | frontend package.json=0.5.0；config.yaml system.version=0.1.0；实际 0.6.0+ | 无单一版本源，排障/打包时易误判 |
| 8 | **子瞻/白圭工具种子全同** | config.yaml L238-239 两行相同；边界仅靠 scene_profile 软约束 | 子瞻技术上可达 ifind 全系并给"类投资建议"，靠 prompt 自觉 |

### P2 —— 遗留技术债（不阻断日常使用）

| # | 缺陷 | 证据/出处 |
|---|---|---|
| 9 | EnvSanitizer 不过滤 PYTHONPATH → 透传进沙箱 | 09-03 取证遗留，未修 |
| 10 | compress_model / judge_model 均空 | config.yaml L85/L378；压缩靠主模型兼任，评测 LLM-Judge 降级 |
| 11 | query_rewrite 默认关 | chunks 已 708（+8 倍），"语料量小收益低"的原始裁决前提变化，应重估 |
| 12 | 白圭批次4 前端风险问卷 UI 暂缓 | baigui-investment-advisory §决策记录，待蒋先生裁定是否启动 |
| 13 | 5 个 .bak + archive/ctx-*.md 临时产物未清理 | 09-04 日志已列，待蒋先生决定；建议 .gitignore 加 `*.bak-*` |
| 14 | 会话表测试残留（apple/airbnb/bugatti） | sessions 查询实见；无害但污染统计 |
| 15 | web_search backend 注释与 yaml 不一致 | yaml=duckduckgo，注释称"当前默认 .env=bing" |

### 已证伪/过时项（自检中顺带纠正）

- ~~token_usage 未落库~~ → 实际 08-18 起已落库（297 条/1627 万 tokens），MEMORY.md 已同步修正。
- ~~全量回归 40 分钟~~ → e823f38 后 18m51s。

---

## 四、下一阶段优化方向（A/B/C 分阶段，依赖关系已标注）

### A 阶段：激活空转机制（验证 > 新建，零新功能）

> 主题：0.6.0 与 0.5.1 已建机制"转正"。**A 未完成前不启动新能力域**。

| # | 事项 | 内容 | 依赖 |
|---|---|---|---|
| A1 | **Mission 层真实链路验收** | 蒋先生打包后走真实路径：创建 1 个真实长任务 mission → 观察里程碑执行/失败纠偏/汇报隔离/重启恢复；发现的问题按原子 commit 修复 | 需蒋先生打包+操作 |
| A2 | **子瞻记忆/经验管线归因诊断** | 代码级取证：记忆提取触发条件（8 轮间隔）在 office 会话的命中情况、scope 归因逻辑、反思引擎触发点；定位"106 会话 0 记忆 2 经验"根因后再定修复 | 无，AI 可直接做 |
| A3 | **embedding 监控触发验证** | 导入 1 篇测试文档 → 确认 system_metrics 出现 kind='kb'；不落库则查 metrics_sink 装配路径 | 无 |

### B 阶段：场景均衡补强（A2 根因明确后并行）

| # | 事项 | 内容 | 依赖 |
|---|---|---|---|
| B1 | **清和健康知识库导入** | import_kb_dirs.py 增加 D:\health-wiki（scenario=frontend_design），增量导入 + 检索命中验证 | 无（可与 A 并行） |
| B2 | **无涯定义单源化** | 二选一：① 补 monitor skill.yaml 标准件，废弃 agent-profile 特殊通道；② 保留特例但加构建期一致性校验（profile vs system_prompt diff 测试） | 无 |
| B3 | **版本号统一** | 单一版本源（建议 config.yaml system.version 为准，前端打包时注入）；三处对齐 0.6.x | 无 |
| B4 | **office 记忆/经验修复** | 按 A2 诊断结论实施（预计为触发条件或 scope 归因修复 + 补测） | 依赖 A2 |

### C 阶段：质量增强（A/B 完成后，含决策项）

| # | 事项 | 内容 | 依赖 |
|---|---|---|---|
| C1 | compress_model / judge_model 配置 | **配置类决策，交蒋先生**（选哪个模型兼任、是否增加调用成本） | 蒋先生拍板 |
| C2 | query_rewrite 启用评估 | 708 chunks 规模下 A/B 对比召回质量，有效则 enabled=true | A 阶段后 |
| C3 | 白圭批次4 风险问卷 UI | 前端画像采集卡片，缩短首轮画像流程 | 蒋先生裁定是否启动 |
| C4 | EnvSanitizer PYTHONPATH 过滤 | 沙箱环境隔离补漏 | 无，小改动 |
| C5 | 卫生清理 | .gitignore 加 `*.bak-*`；会话表测试残留归档；web_search 注释对齐 | 蒋先生确认后 |

### 优先级理由（量化）

- A 阶段三项合计预计 **≤2 个工作日**，但把 0.6.0（~2070 行）+ P1-D 的沉没投资转化为已验证资产，**投入产出比最高**；
- B1 是四场景中唯一"知识库投资闲置"项，一次导入即补齐清和短板；
- C 阶段全部为增强项，延迟无风险。

---

## 五、风险与注意事项

1. A1 真实验收可能暴露 Mission 层生产级问题（此前 registry 泄漏、审批消息类型两个 bug 都是"测试全绿但生产失效"的前科）——验收时发现的问题应逐个原子修复，不批量改。
2. A2 诊断若定位到记忆提取管线本身（而非 office 特有），影响面扩至全场景，修复需全量回归护航（`--basetemp=D:/Private agent/.pytest-tmp`）。
3. B1 导入 health-wiki 会增加 chunks（预计 +100 上下），embedding worker 内存水位已在监控设计内（A3 验证后可观测）。
4. 本报告全部结论基于只读取证，未修改任何代码/配置/数据。

---

## 附：自检取证方法（可复现）

- 配置层：config.yaml 464 行全读；四场景 skill.yaml + system_prompt.md 全读。
- 代码层：harness.py（monitor 特例）、billing.py（token_usage）、import_kb_dirs.py（KB 源映射）。
- 数据层：生产库 private_agent 只读查询 8 组（KB/记忆/会话/missions/事件/指标/经验/token）。
- 文档层：docs/ 46 份中 8 份关键文档（四窗口/白圭强化/0.5.1 深化/Mission 设计+收尾/09-03~04 诊断）。
