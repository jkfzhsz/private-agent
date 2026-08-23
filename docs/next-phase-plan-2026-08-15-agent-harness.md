# Next Phase Plan：Agent Harness 工程化（子瞻/白圭/清和/无涯）+ 上下文压缩升级 + 工作区文件工具

> 日期：2026-08-15 ｜ 状态：待评审 ｜ 提出：WorkBuddy（基于 Deep Research 调研结论映射）  
> 调研依据：`outputs/ai-agent-2026/report.md`（AI Agent 2026 框架与工具生态，12 对象调研）

## 0. 背景与决策行

行业调研核心发现（详见报告 §核心发现）：

- **Harness 层决定模型表现**：LangChain Deep Agents 实测"不换模型，仅优化系统提示/工具描述/中间件可移动 10-20 个基准点"（gpt-5.2-codex 52.8%→66.5%）
- **压缩时机是长任务质量关键**：固定阈值压缩常在坏时机触发（重构中途丢细节）；Deep Agents 改为模型自主决定压缩时机 + 保留最近 ~10% 原始消息
- **大中间产物文件化**：长任务中检索结果/中间笔记写入文件而非堆上下文，防 token 膨胀

产品理念约束（2026-08-15 蒋先生明确）：**专注子瞻/白圭/清和三场景挖深，不新增场景**。

设计决策：

- 本批次 = 三项合并（A HarnessProfile+评测集 / B 压缩触发升级 / C 工作区文件工具），全部零新增场景、零新框架
- 明确不做：A2A 协议、MCP Apps、EMA、Computer Use、自主创建 skill（详见 §7）

评审结论（2026-08-15 蒋先生）：
- A-3 评测任务由 WorkBuddy 拟定（任务清单见 §3.1-A3）
- `compress_now` 工具默认关闭，可手动打开（config.yaml / admin 开关）
- C-1 工作区写/删**沿用 file_write 的 elevated 语义**（写=WS 确认；删=危险级二次确认）
- **Harness 覆盖范围 = 子瞻/白圭/清和 + 无涯（主智能体 monitor）**——无涯走"内置角色通道"（§3.1-A4）

## 1. 现状锚点（代码事实，2026-08-15 核实）

| 项                | 现状                                                                                                                                                                                 | 代码位置                                                               |
| ---------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------ |
| 场景定义             | 3 个 skill：office=子瞻 / data_analysis=白圭 / frontend_design=清和；skill.yaml 含 `scene_profile`（persona/role/values/workflow/rules）、`dependencies.tools` 白名单、`workspace`、`model_params`   | `backend/skills/{office,data_analysis,frontend_design}/skill.yaml` |
| scene_profile 使用 | **仅前端展示**（admin API 透传），**未注入 ReactLoop 系统提示**；场景职责靠 system_prompt.md 人读版本                                                                                                         | `api/admin.py:1148,1220`；`skills/models.py:104`                    |
| 上下文压缩            | `_maybe_compress`：固定阈值触发（turns>10 或 token>80%×min(provider,cfg)）；pre_compact hook 已接；Stable Zone 合并先行；事实快照(\_is_factual)+LLM 摘要；失败熔断（3 次禁用）；按 msg_id 回写                            | `core/react_loop.py:1536-1640`；`core/compressor.py`                |
| hooks            | 六事件（user_prompt_submit/pre_tool_use/post_tool_use/stop/**pre_compact**/permission_request）× 三类型（command/http/mcp_tool），决策字段 permissionDecision/updatedInput/additionalContext/stop | `core/hooks.py`                                                    |
| 工作区              | 会话 workspace 已落地：SkillManifest.workspace → sessions.workspace，ReactLoop 路由 file_write/sandbox                                                                                      | `skills/models.py:113`（2026-08-15 实施）                              |
| MCP              | iFind(7) + 企查查(9) + mempalace(36)；白名单过滤；工具 schema 当前全量注入                                                                                                                           | `config.yaml`；react_loop 工具组装                                      |
| 权限               | SkillPermissions（allow_file_write/allow_network/sandbox_enabled/rules 路径域名细粒度）；SafetyLevel safe/elevated/dangerous；elevated 走 WS 确认                                                | `skills/models.py:48-60`                                           |
| 无涯（主智能体）         | scope=monitor：**非 skill.yaml 驱动**——内置 system_prompt（skills/monitor/system_prompt.md）+ `_monitor_system_prompt` 动态注入实时指标摘要；专属 monitor 工具（register_monitor_tools + evolution_tools 进化调度）；agent-profile.json display_name=无涯               | `main.py:433-446,1143-1154`；`skills/monitor/`；`agent-profile.json` |

## 2. 目标

1. **A-HarnessProfile**：把 scene_profile 升级为"可版本化 harness 配置单元"并注入 ReactLoop 系统提示，工具描述场景化增强；**覆盖 3 场景 + 无涯主智能体**；建立评测集（含基线），用 harness 迭代驱动质量提升——"挖深"理念制度化
2. **B-压缩触发升级**：固定阈值 → 多信号混合（token 水位 + 轮次 + 任务阶段 + 可选模型自主压缩工具），压缩保留最近原始消息比例可配
3. **C-工作区文件工具**：会话工作区文件工具集（读/写/列/删，路径沙箱），大产物文件化引导，压缩时事实型消息转存工作区文件（替代截断丢失）

## 3. 方案设计

### 3.1 A-场景 HarnessProfile

**A1. profile 结构升级**（`skills/models.py` SkillManifest 扩展，向后兼容）

由你来拟定（2026-08-15 蒋先生批注），拟定如下——skill.yaml 新增 `harness` 段，`scene_profile` 现有字段保留不动：

```yaml
# skill.yaml 新增段（场景 skill：office/data_analysis/frontend_design）
harness:
  enabled: true                       # false 时整体跳过本 harness（零行为变化）
  # 1) prompt 变量：注入 [Scene Profile] 块时渲染到模板占位符
  #    system_prompt.md 中用 {{var_name}} 引用；变量缺失时回退为空
  prompt_vars:
    audience: "国有商业银行信贷人员"
    tone: "专业、务实、避免空泛"
  # 2) 工具描述覆盖：key=tool name，value=替换默认工具描述（只影响暴露给模型的 schema 描述）
  tool_descriptions:
    web_search: "检索公开网页信息。研究类任务优先，标注来源。"
    code_execution: "沙箱内执行 Python 代码。数据分析/图表/脚本验证用。"
  # 3) 场景级压缩参数覆盖（未配置项回退全局 context.compression）
  compression:
    keep_turns: 8                     # 覆盖全局默认 6
    keep_ratio: 0.15                  # 覆盖全局默认 0.1
  # 4) 中间件（预留）：场景注入的 pre/post 处理钩子（当前恒空=零行为变化）
  middleware: []
```

**A2. 注入链路**（`react_loop.py` 系统提示组装处）

- 组装 system prompt 时：`system_prompt.md`（人读主版本）→ 追加结构化 `[Scene Profile]` 块（persona/role/values/workflow/rules 渲染自 scene_profile + harness.prompt_vars）
- 工具描述覆盖：暴露给模型的工具 schema 中，命中 `harness.tool_descriptions` 的工具用覆盖描述替换默认描述
- 零回归保障：scene_profile 为空或 harness 未配置时输出与现状完全一致

**A3. 评测集**（新目录 `backend/eval/scenes/`，复用现有 eval 设施）

- 每场景建 `scenarios.yaml`：任务清单见下（WorkBuddy 拟定，2026-08-15 蒋先生授权）；每任务含：id / 描述 / 输入附件（可选）/ 成功判据（可自动核验为主，人工 0-5 分为辅）
- 评测维度：任务完成度（人工 0-5 打分模板）/ 关键事实保留（数值/路径/结论抽查）/ 工具调用正确率 / token 成本
- 基线记录：`backend/eval/scenes/baseline-2026-08-15.md`（harness 迭代前后对比）
- 用途：harness 提示词/工具描述迭代的回归护栏（呼应调研"10-20 个基准点"洞察）

**任务清单（首批 3 场景 × 10 + 无涯 × 8，共 38 项）**

子瞻（office，工作与学习）：
1. PDF 提取表格 → Excel 汇总（含数值计算）
2. 多篇网页资料 → 带来源标注的行业调研纪要
3. CSV 数据清洗 + 分组统计 + 可视化图表（matplotlib）
4. 长文档压缩为要点摘要（关键数字零丢失）
5. 学习辅导：拆解"央行公开市场操作"为知识点讲解
6. 沙箱内批量整理目录文件（重命名/归档）
7. 生成 docx 报告（标题层级/表格/图片占位）
8. 竞品定价对比（3 家网页调研 → 对比表）
9. python-docx 修改现有文档指定段落
10. 金融术语解释 + 关联政策背景（结合 iFind）

白圭（data_analysis，商业/投资分析）：
1. 给定公司价值投资分析（基本面 + 估值框架，对照 investment_framework）
2. iFind 查询个股/指数行情 → 技术解读
3. 行业研究：竞争格局（波特五力框架）
4. 财务指标计算（ROE/毛利率/负债率）+ 趋势判断
5. 构建 DCF/PE 估值 → 结论 + 风险提示
6. 股价走势 + 均线叠加图（matplotlib，红涨绿跌配色）
7. 财报关键数据提取 + 异常项识别
8. 投资决策检查清单应用（对照 securities 知识库）
9. 宏观指标（CPI/PMI）解读与投资含义
10. 资产配置建议（家庭财富管理框架）

清和（frontend_design，生活健康与美学设计）：
1. 健康管理：作息/饮食记录 → 改善计划（含免责声明）
2. 生成响应式健康打卡 HTML（含设计说明）
3. 报告美化排版为演示 HTML
4. 前端 bug 修复：定位并修复样式问题（给定代码片段）
5. 设计系统：为品牌生成色板 + 字体规范
6. 子瞻产物美化：数据报告 → 一页可视化看板
7. 饮食建议：一周食谱（营养学 + 证据等级标注）
8. 过敏防治知识问答（引用 health-wiki）
9. 前端组件生成（卡片/表单/图表）附代码
10. 生活美学：家居配色方案 + 实施清单

无涯（monitor，项目进化）：
1. 代码诊断：阅读指定模块识别重复模式 → 提议重构
2. 运行 pytest 分析失败用例根因
3. 在线失败模式 → 评估集覆盖空白建议
4. 经验库健康度分析（lessons_stats）+ 合并/淘汰建议
5. 评估队列低分案例模式分析（review_queue_summary）
6. 系统指标异常诊断（CPU/内存/WS/token 用量）
7. 针对子瞻/白圭/清和失败模式提议 system_prompt 改进
8. 工具调用失败模式 → 提议工具实现改进

**A4. 内置角色通道（无涯/monitor）**

- 无涯无 skill.yaml（system_prompt.md 内置 + `_monitor_system_prompt` 动态注入指标），harness 载体 = **`agent-profile.json` 扩展**（影响面最小，不动 main.py monitor 特殊路径）：
```json
{
  "display_name": "无涯",
  "harness": {
    "enabled": true,
    "tool_descriptions": {
      "optim_plan": "提交项目进化方案（含改动方案/预期收益/风险/影响范围），等待用户审批。",
      "lessons_stats": "查看各场景经验统计，识别可合并/应淘汰的经验。"
    },
    "compression": { "keep_turns": 8, "keep_ratio": 0.15 }
  }
}
```
- 注入链路与 A2 相同（系统提示追加 [Scene Profile] 块渲染 persona/职责/工作流 + 工具描述覆盖）；`_monitor_system_prompt` 的指标注入逻辑不动
- 零回归：未配置 harness 段时行为不变

### 3.2 B-压缩触发升级

**B1. 多信号触发**（`compressor.py maybe_compress` + `react_loop._maybe_compress`）

- 保留现有信号：`turns > 10`、`tokens > 0.8 × context_window`（默认行为不变）
- 新增信号（config.yaml `context.compression` 段扩展，**全部默认关闭，可手动打开**——config.yaml 或 admin 运行时覆盖）：
  - `task_phase`: 任务阶段切换（回合间检测"新任务指令"关键词）时允许提前压缩
  - `model_suggested`: 暴露 `compress_now` 工具，模型在完成一个交付物、准备开始新任务时自主触发压缩（Deep Agents 自主压缩思想；**默认 disabled，用户可手动开启**）
- 触发决策优先级：token_limit > model_suggested > task_phase > turn_limit；记录 trigger 到 compress 事件（现有 `_emit_compress_event` 扩展 trigger 枚举）

**B2. 保留比例可配**（`compressor.py execute/plan_compression`）

- 新增 `keep_ratio`（默认 0.1）：压缩时保留最近 ~10% token 的原始消息（当前按 keep_turns=6 轮次），两者取 token 更优者；事实快照机制保留

**B3. 压缩转存**（与 C 联动，见 §3.3-C3）

### 3.3 C-工作区文件工具

**C1. 工具集**（`tools/` 新增 workspace 工具族，注册进白名单）

- `ws_read` / `ws_write` / `ws_list` / `ws_rm`：限定会话 workspace 目录内；路径解析后必须落于 workspace root（`os.path.commonpath` 校验 + fnmatch 白名单），穿越即拒绝
- 权限语义（2026-08-15 蒋先生确认沿用 file_write 语义）：`ws_write` = elevated（WS 60s 确认，会话缓存复用）；`ws_rm` = **dangerous**（危险级，删除操作二次确认 + 支持 `trash` 回收而非直接删除，与文件系统安全基线一致）；`ws_read`/`ws_list` 默认 safe
- 无 workspace 的会话（通用场景）：工具不注册（零回归）

**C2. 大产物文件化引导**

- 模型输出超过阈值（如单条 assistant 消息 > 8K tokens，可配）时，post_tool_use/收尾阶段注入提示"长内容建议写入工作区文件（ws_write），上下文保留路径引用"
- 为压缩服务：被压缩的事实型消息优先转存工作区 `archive/` 文件，摘要中保留文件路径引用（替代 factual_snapshot 20000 字符截断丢失）——需压缩转存后自动清理压缩存档引用一致性

**C3. 压缩转存联动**（B3）

- `compressor.execute` 压缩事实型消息时：若会话有 workspace，写入 `archive/ctx-{turn}.md` 并返回路径；摘要消息含 `[事实快照见 ws:archive/ctx-{turn}.md]`
- 无 workspace 会话：维持现有 factual_snapshot 内联截断（零回归）

## 4. 实施批次（建议顺序）

| 批次  | 内容                                            | 验收                                                                |
| --- | --------------------------------------------- | ----------------------------------------------------------------- |
| A-1 | HarnessProfile 结构扩展（SkillManifest.harness + agent-profile.json 通道）+ ReactLoop 注入链路 + 工具描述覆盖 | pytest 新增（注入存在性/覆盖替换/零回归，含 monitor 通道）+ 前端 tsc/vitest 回归；scene_profile 注入肉眼可验证 |
| A-2 | 评测集（38 任务清单见 §3.1-A3）+ 基线记录                | `eval/scenes/` 4 个 scenarios.yaml 可运行，基线文档落盘                          |
| B-1 | 压缩多信号触发 + keep_ratio + compress_now 工具（默认关、可手动开） | pytest：触发优先级/保留比例/工具注册与权限；compress 事件 trigger 新枚举                    |
| C-1 | 工作区文件工具族 + 路径沙箱 + 权限语义（写 elevated/删 dangerous） | pytest：路径穿越拒绝/无 workspace 不注册/权限分级                                  |
| C-2 | 大产物引导 + 压缩转存联动                                | pytest：转存路径/摘要引用/无 workspace 回退                                   |

依赖关系：A-1 → A-2（评测集依赖注入链路可测）→ B-1 / C-1 可并行 → C-2 依赖 B-1/C-1。

## 5. 配置与兼容

- 全部新特性默认关闭或零行为变化（config.yaml `context.compression` / skill harness 段缺省回退）
- 数据库：无 schema 变更（harness 段随 skill.yaml 文件存储；compress 事件 payload 扩展为 JSONB 自由字段）
- 前端：无强制变更；可选在技能配置页展示 harness 摘要

## 6. 测试计划

- 后端：每批次 pytest 新增用例 + 相关模块回归（compressor/context_manager/react_loop/hooks/skills_manager）；全量 `pytest --timeout=180 --timeout-method=thread`（2026-08-15 基线，--ignore=test_eval_full_cycle.py）
- 前端：tsc 0 错 + vitest 回归
- 评测：A-2 后建立基线，后续 harness 迭代跑评测集对比

## 7. 明确不做（本期）

- **A2A 协议**：单用户桌面 agent 无跨框架协作需求；等 DSH 插件生态验证后再评估
- **MCP Apps / EMA**：EMA 企业授权场景；Apps 依赖宿主 iframe 渲染，桌面端价值低
- **Computer Use**：已有 code_execution 沙箱，桌面 GUI 操作优先级低
- **自主创建 skill**（OpenClaw 模式）：风险高（2026-03 曾披露 9.9 分 CVE），远期可评估"受限创作+人工确认"
- **新增场景智能体**：3 场景固定 + 无涯主智能体（产品理念约束）
- **hooks 事件扩展**（async hooks/TaskCompleted 等）：已有六事件含 pre_compact 够用，留待后续
- **monitor 会话 skill.yaml 化**：无涯不走 skill 通道（保持内置路径，harness 经 agent-profile.json 注入），避免动 main.py monitor 特殊装配

## 8. 风险与权衡

- **压缩行为变化风险**：B-1 多信号默认关闭，逐步开启并观察 compress 事件 trigger 分布；keep_ratio 与 keep_turns 取优逻辑需单元测试覆盖边界
- **工具描述覆盖的双刃**：场景化描述可能劣化通用工具（如 iFind 在子瞻外的场景）——覆盖仅作用于配置了 harness 的场景 skill/内置角色，且以对应 skill/角色激活为条件
- **评测集人工成本**：A-2 打分为人工模板，首批 38 任务工作量可控（复用现有 eval 目录惯例）；无涯 8 任务可部分自动核验（pytest 可跑通即成功判据）
- **agent-profile.json 通道风险**：改动面小但需同步前端读取逻辑（display_name 读取处），A-1 验收须含前端回归

## 9. 待评审问题（已确认项归档）

- ✅ A-3 评测任务由 WorkBuddy 拟定（已列 38 项，见 §3.1-A3）
- ✅ `compress_now` 默认关闭、可手动打开（config.yaml / admin）
- ✅ C-1 工作区写/删沿用 file_write elevated 语义（写=确认；删=dangerous 二次确认）
- ✅ Harness 覆盖补无涯（agent-profile.json 通道，§3.1-A4）
- ⏳ 剩余 1 项：A-2 首批基线是否先跑子瞻 10 任务（工作流最短）验证评测闭环，再补齐白圭/清和/无涯？
