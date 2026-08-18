# 无涯升级计划：PA 全局进化核心智能体（agent-upgrader）

- 日期：2026-08-16
- 状态：设计定稿（决策已拍板），待按阶段实施
- 关联：docs/next-phase-plan-2026-08-15-agent-harness.md（Harness 层）、private-agent-blueprint.md

## §0 背景与问题

无涯（monitor）是 PA 的全局运行监控与升级者，定位为"项目进化者"（skills/monitor/system_prompt.md），
但实际行为退化为"对话聊天软件"，背离设计初衷。2026-08-16 实证根因（三层机制叠加）：

1. **注入层**：每轮仅注入 top-15 工具（selector.py:41），无涯工具池 55+（frozen 15-20 + monitor 8 + mempalace 36 + Searchpin 2），`file_write/code_execution` 因 usage=0 被挤出模型可见集 → **看不见手**。
2. **权限层**：system_prompt.md:61 一刀切"未经审批不得修改代码"→ **不敢动手**。
3. **执行层**：`apply_optim`（monitor_tools.py:204）是**模拟执行**（仅 context 配置类，不真跑 plan）→ **动不了手**。

行为证据：用户让无涯评估 D:\github\buzz-main 能否"装进 PA"，无涯理解成"装进记忆宫殿"——
其工具生态中唯一的"装入"通道是 mempalace 36 工具，代码改造类工具不可见，模型做可行映射的必然结果。

## §1 目标与定位

无涯 = **自托管、对 PA 源码树有完全读写 + 测试验证 + 分级审批边界的编码/DevOps agent**
（WorkBuddy code 模式的最小自托管版）。六个能力域：

| # | 能力域 | 说明 | 现状 |
| --- | --- | --- | --- |
| ① | 代码改造 | 读/改/写 PA 代码，跑测试验证 | ✗ 工具可见性被 top-15 屏蔽 |
| ② | 外部项目评估/接入 | 分析第三方项目，判定接入方式（MCP/skill/代码改造） | ✗ 无此工具 |
| ③ | 系统监控诊断 | 指标/日志/失败模式 | ✓ monitor_tools 6 个 |
| ④ | 版本与变更管理 | git status/diff/commit、变更摘要 | ✗ PA 无 git 工具 |
| ⑤ | 知识沉淀 | 进化经验、评估闭环、失败教训 | △ lessons_stats 只读 |
| ⑥ | 自我扩展（元能力） | 管理 PA 的 skill、MCP 配置、评估集 | ✗ 未设计 |

## §2 工具设计（三层模型）

### 2.1 核心工具（始终注入 = frozen 白名单 + always_include 锚点）

| 工具 | 作用 | 现状/动作 |
| --- | --- | --- |
| `file_read` / `file_write` / `code_execution` | 读/改/执行 | 已有；monitor 需加入 always_include 锚点 |
| `ws_read/ws_write/ws_list/ws_rm` | 工作区文件族（C-1 已建） | monitor 会话配 workspace = PA 源码根 后自动注册 |
| `optim_plan` / `apply_optim` | 进化方案提交/执行 | apply_optim 需从模拟改为真执行 |
| `git_status` / `git_diff` / `git_commit` | 版本管理（新增） | PA 空白，需新建 tools/builtins/git_tools.py |
| `pytest_run` | 跑 PA 后端测试（新增） | 替代 code_execution 内嵌 pytest，配合开发沙箱 |

### 2.2 延迟工具（按需加载 = WorkBuddy ToolSearch 模式）

| 工具 | 触发场景 |
| --- | --- |
| `project_scan` | 外部项目结构/技术栈/依赖分析（能力域②核心） |
| `impact_analysis` | 改动影响面（跨文件引用、调用链） |
| `eval_runner` | 跑评测集、对比基线（复用 eval/scenes） |
| `log_analyzer` | 日志检索、异常模式聚合 |
| `skill_manager` | 创建/修改 PA 的 skill（能力域⑥） |
| `mcp_config_manager` | 增改 mcp.json 条目（能力域⑥） |
| `mempalace_write` | 进化经验沉淀（复用 mempalace MCP） |

### 2.3 MCP：本地 vs 线上分类

| 类别 | 必配 | 选配（按需扩展） | 明确不需要 |
| --- | --- | --- | --- |
| 本地/离线 | mempalace（进化经验+评估记录）、本地 git | 本地知识库（D:\wiki-knowledge 供读项目文档） | — |
| 线上/依赖服务 | 模型 API（DeepSeek，已有）、包源/官方文档查询 | GitHub MCP（仓库同步/PR） | — |
| 数据类 | — | — | iFinD/企查查（已裁剪，与进化职责无关） |

决策原则：**核心能力（读改代码/跑测试/git/配置）必须本地自持**；知识获取走线上（模型+文档）；
协作类按需接（本地 git 够用，仅 push 需网络）。

## §3 权限模型（已拍板）

**决策 1（蒋先生 2026-08-16）**：低风险直接做 + 核心改动先出方案。

| 级别 | 动作 | 机制 |
| --- | --- | --- |
| safe（直接做+审计） | file_read、代码搜索、git diff/status、pytest 运行、ws_read/list | 自动执行，react_events 落库 |
| elevated（WS 确认一次+会话缓存） | file_write（PA 源码树内低风险）、ws_write、git commit、依赖安装 | 60s 确认窗口 |
| **方案先行**（核心改动） | 核心模块重构、架构变更、删代码、DB schema、密钥/.env | 先 optim_plan 出方案（diff/收益/风险/影响范围）→ 用户确认后执行 |
| dangerous（二次确认+备份） | ws_rm、git push | 备份 + 显式确认 |

## §4 开发沙箱（已拍板）

**决策 2（蒋先生 2026-08-16）**：允许 code_execution 访问 backend 源码树（含 .env 只读）跑测试。

设计：
- 现状：沙箱隔离（backend/.sandbox/），无法访问 backend 源码树 → 无涯无法跑 PA 自身测试。
- 目标：为 monitor 会话开放"开发沙箱"模式：cwd=backend + 只读挂载 backend 源码树 + 只读 .env（WORKSPACE/PA_DB_PASSWORD/PA_MASTER_KEY 供 pytest 加载）+ 只读 PostgreSQL 测试库连接。
- 边界：产物仍写沙箱工作区；源码树只读（改动必须经 file_write 走权限模型）；写 .env/生产库禁用。
- 落点：code_execution handler 增加 dev_mode 参数（仅 monitor 会话可开），或新建 pytest_run 工具内部走受限 subprocess。

## §5 必备基础配置 + 扩展方式

### 5.1 必备（阶段 1）

```yaml
# config.yaml
tools:
  tool_selection:
    always_include:
      # monitor 锚点：动手工具始终注入（修复"看不见手"）
      - file_read
      - file_write
      - code_execution
      - optim_plan
      - ws_read
      - ws_write
      - git_status
      - git_diff
      - git_commit
      - pytest_run
  mcp:
    skill_binding:
      monitor: ["mempalace", "Searchpin"]   # 已有，保持
```

- monitor 会话 `workspace = D:\Private agent`（源码根）→ 自动注册 ws_* 工具
- apply_optim 真执行改造

### 5.2 扩展方式（复用既有机制）

- 新增工具走 `tools/builtins/` 模块 + 白名单注册（零侵入）
- MCP 走 `skill_binding` 按任务类型绑定
- 能力打包成 PA skill（`pa-dev-tools`），工具/提示词随 skill 走（skill.yaml harness 段已支持）

## §6 开发顺序（5 阶段，每阶段可验收）

| 阶段 | 内容 | 验收标准 |
| --- | --- | --- |
| **1 打通"手"** | always_include 锚点 + apply_optim 真执行 | 无涯能对 PA 源码做真实改动（会话内肉眼验证） |
| **2 开发闭环** | monitor workspace=源码根 + 开发沙箱 + git 工具族 + pytest_run | 无涯能改代码→跑测试→提交，全链路无人工中转 |
| **3 外部项目接入** | project_scan + 接入评估报告 + skill/MCP 脚手架 | 给 buzz-main 类项目，产出"接入评估+实施方案" |
| **4 自我扩展** | skill_manager + mcp_config_manager + eval_runner | 无涯能自我安装一个工具/技能并验证 |
| **5 进化沉淀** | 经验双写 + 失败模式驱动修复 | 无涯能按评估低分自动提出并执行修复 |

依赖：1 → 2 → 3（3 依赖 2 读写闭环）→ 4（依赖 3 接入评估）→ 5。
**阶段 1 成本极低（配置 + 一个函数改造），先行验证方向。**

## §7 测试与回归计划

- 每阶段：pytest 新增用例（工具 handler / 权限边界 / 沙箱隔离 / 注入锚点）+ 相关模块回归
- 阶段 1 验收测试：test_agent_upgrader.py（apply_optim 真执行链路 + always_include 对 monitor 生效）
- 全量回归：`pytest --timeout=180 --ignore=test_eval_full_cycle.py` + 前端 tsc/vitest
- 零回归原则：所有新特性默认关闭（monitor 专属），数据库无 schema 变更

## §8 已确认决策记录

| # | 决策 | 拍板人/日期 |
| --- | --- | --- |
| 1 | 无涯权限：低风险直接做 + 核心改动先出方案 | 蒋先生 2026-08-16 |
| 2 | code_execution 允许访问 backend 源码树（.env 只读）跑测试 | 蒋先生 2026-08-16 |

## §9 主流 AI Coding 智能体能力与工作流对标（2026-08-16 调研）

无涯升级对标业界主流 agentic coding 方案，核心工作流范式收敛为
**"探索 → 规划 → 编码 → 验证 → 沉淀"五环**，各方案能力映射：

| 方案 | 核心范式 | 可借鉴到无涯的要点 |
| --- | --- | --- |
| **Claude Code** | Plan Mode（只读规划沙箱）+ CLAUDE.md 项目宪法 + 子代理委派 + 验证即完成 | ① 探索/规划/编码分模式，高成本改动前必须出书面方案（与决策 1 契合）；② 项目级指令文件（PA 对应 skills/monitor/system_prompt.md + agent-profile harness）；③ "验证即完成"——完成定义必须含证据 |
| **OpenHands** | Agent-Core-Runtime 三层 + ReAct 循环 + Docker 沙箱 + CodeAct（代码即行动） | ① 沙箱内真实执行 + 观测回填（think→act→observe→repeat）——无涯开发沙箱同源；② "写代码而非造 20 个定制工具"：bash+文件+测试足矣，工具宁少勿多 |
| **Superpowers（SDD）** | 强制技能链：头脑风暴→写计划→TDD→系统排错→代码审查；子代理三角色（实现/规格审查/质量审查） | ① 需求确认=苏格拉底式追问边界穷举，未确认完整边界禁止写码；② 任务拆解=2-5 分钟微任务，精确到文件路径；③ 强制 TDD 红绿重构；④ 双阶段评审（先规格合规，再代码质量） |
| **Spec-Driven Development** | 先写可版本化 spec（/specs 目录入库），再按编号计划逐任务实现，人工在阶段闸门审查 | ① spec 而非代码为唯一真相源——PA 对应 docs/next-phase-plan-*.md 既有机制（PA 已在用！）；② 阶段闸门审查（改 spec 10 分钟 vs 改错代码数天）；③ 实测 3-10x 首次通过率提升 |

**工程纪律提炼（全部纳入无涯工作流）**：
1. **先探索再规划再编码**：读代码取证（引用路径而非描述）→ 出方案 → 批准 → 改码
2. **小步提交**：git 在逻辑检查点提交，干净可回滚
3. **验证是完成定义的一部分**：聚焦测试 → 相关测试 → 全量回归 → lint/typecheck → diff 审查
4. **错误即规则**：每次修完 bug，把教训写回规则文件（"更新 CLAUDE.md 让它不再犯"）→ 对应 PA 经验库 + system_prompt 收敛
5. **一次改一个假设**：禁止霰弹枪式改码（shotgun debugging）
6. **审计同类**：修完 bug 后搜索同模式代码，一并修复

## §10 无涯工作流设计（七环节 × 技能 × 关卡）

用户点名的七个环节映射为无涯的标准工作流，每环节有强制技能与产出物：

| 环节 | 技能（对应 Superpowers/Claude Code） | 无涯动作 | 产出物（关卡证据） |
| --- | --- | --- | --- |
| **① 需求确认** | Brainstorming（边界穷举 + 追问） | 对模糊需求苏格拉底式追问：目标/约束/边界/异常场景；**未确认完整边界禁止写码** | 需求澄清清单（question list）→ 用户确认后进入方案 |
| **② 问题定位** | Systematic Debugging（四阶段：复现→隔离→假设→验证） | 先写复现测试（红）；git bisect 二分定位；只验证假设，不边修边测；**无根因不修复（铁律）** | 复现步骤 + 根因定位（文件:行号）+ 最小复现案例 |
| **③ 方案设计** | Writing-Plans（多方案对比 + 影响面） | 评估 ≥2 方案利弊（含回滚方案）；impact_analysis 查调用链影响面；核心改动出书面方案待批（决策 1） | 方案文档：改动方案 + diff 预览 + 预期收益 + 风险 + 影响范围 + 验证计划 |
| **④ 任务拆解** | Writing-Plans（MECE + 微任务） | 大任务拆为 2-5 分钟微任务；每任务精确到文件路径 + 验证步骤；标注依赖与可并行项 | 任务清单（编号 + 依赖 + 验证标准），对应 eval/scenes 任务模型 |
| **⑤ 分步执行** | Executing-Plans + TDD | 一次一个函数/一个任务；**先写失败测试（RED）→ 最小实现（GREEN）→ 重构**；每逻辑点 git commit（可回滚） | 每任务：测试通过 + 干净提交 |
| **⑥ 回归检测** | Verification（验证即完成） | 聚焦测试 → 相关模块测试 → 全量回归（pytest + 前端 tsc/vitest）；检查无引入新问题；diff 审查安全 | 回归报告：全量通过 + diff 审查记录 |
| **⑦ 沉淀** | Knowledge Sink（错误即规则） | 改动后反思：根因 + 修复套路沉淀 project_evolution 经验；教训写回规则文件；同类模式审计 | 经验记录（lesson_category=project_evolution）+ 规则文件更新 |

**TDD 模式（贯穿 ⑤，硬约束）**：
- 红（写失败测试）→ 绿（最小实现）→ 重构（保持全绿）三循环，测试先行是铁律
- 测试分层：原子单元测试 + 跨功能集成测试 + 系统端到端测试，三者并重（不过度偏重单元）
- 覆盖率 ≥ 85%（测试工具：PA 后端 pytest / 前端 vitest）
- 反模式禁止：测试不通过禁止进入下一环节；先写实现后补测试视为违规

## §11 无涯技能包落地（skills/ 机制）

按 §10 工作流，为无涯建立 PA skill 技能包（skills/monitor 下扩展，或独立 pa-dev-tools skill）：

| 技能 | 对应环节 | 触发条件 | 加载方式 |
| --- | --- | --- | --- |
| `requirement-clarify` | ① 需求确认 | 模糊/大需求、外部项目接入 | 按需（延迟） |
| `systematic-debugging` | ② 问题定位 | 报错、测试失败、异常行为 | 按需（延迟，但铁律注入 prompt） |
| `writing-plans` | ③④ 方案/拆解 | 改动 ≥2 文件、核心重构、新功能 | 按需（延迟） |
| `test-driven-development` | ⑤ 执行 | 任何代码改动 | 始终（核心纪律，入 frozen prompt） |
| `verification-loop` | ⑥ 回归 | 每次改动收尾 | 始终（完成定义） |
| `knowledge-sink` | ⑦ 沉淀 | 修复/进化完成后 | 始终（收尾钩子） |

加载机制：TDD/verification/knowledge-sink 入 system_prompt 常驻（铁律类）；
requirement-clarify/debugging/plans 作为延迟 skill 按场景触发（对应 §2.2 延迟工具）。

## §12 与现有机制的对齐

| 无涯新能力 | PA 现有机制 | 说明 |
| --- | --- | --- |
| 需求确认（追问/清单） | AskUserQuestion 类 WS 交互 + 会话消息 | 无涯在会话内直接追问，无需新工具 |
| 问题定位（复现/二分） | code_execution + git 工具族（新增）+ react_events 回放 | session_events 工具已有（monitor_tools.py） |
| 方案设计 | optim_plan（已具 proposal/plan/category） | apply_optim 真执行后闭环完整 |
| 任务拆解 | eval/scenes 任务模型（id/依赖/验收） | 复用 scenes_loader 校验 |
| TDD 执行 | code_execution + pytest_run（新增） | 开发沙箱已拍板（决策 2） |
| 回归检测 | 全量 pytest/vitest 命令（既有） | 验证即完成 = 完成定义 |
| 沉淀 | EvolutionRepo（lessons/经验）+ mempalace | 经验双写（进化经验 + 教训规则） |

## §13 后续调研线索（可继续延伸）

- OpenHands SDK 的 Condenser（上下文压缩器）与 stuck detection（卡死检测）机制
- Superpowers 子代理三角色（Implementer / Spec Reviewer / Quality Reviewer）在 PA 的落地形态
- Claude Code hooks（确定性生命周期动作）→ 对应 PA hooks.py 六事件

## §14 技能/MCP/工具场景归类（通道拥挤根治，2026-08-16 蒋先生需求）

### 14.1 现状盘点（已取证）

| 维度 | 现状 | 缺口 |
| --- | --- | --- |
| 技能分类 | skill.yaml 已有 `scenario` 字段（writing/documents/engineering/design/meta + 场景名），models.py:66 已解析 | **list_skills API 未透传 scenario**；前端技能选择器不过滤；add_supplementary_skills 挂载不校验场景兼容 |
| MCP 归类 | config `tools.mcp.skill_binding` 已按场景绑定（monitor 已裁 ifind） | 仅 MCP 维度，未与技能统一 |
| 工具归类 | frozen 工具全会话相同 + monitor 专属（register_monitor_tools） | 场景会话无工具子集概念 |

### 14.2 设计：统一场景归类（scene_binding 三合一）

核心：**一个场景 = 技能子集 + MCP 子集 + 工具子集**，装配与注入全程按场景收敛，从源头避免通道拥挤（与 §9 注入纪律同源）。

```
scene_binding:                      # 新增统一配置段(替代/扩展 mcp.skill_binding)
  office:                           # 子瞻
    skills: [office, documents, writing]      # 按 scenario 类目允许
    mcp: [hexin-ifind-ds-*, mempalace, Searchpin]
    tools: []                                 # 空 = frozen 全量(默认)
  data_analysis:                    # 白圭
    skills: [data_analysis, documents, engineering]
    mcp: [hexin-ifind-ds-*, mempalace, Searchpin]
    tools: []
  frontend_design:                  # 清和
    skills: [frontend_design, design, engineering]
    mcp: [mempalace, Searchpin]
    tools: []
  monitor:                          # 无涯
    skills: [monitor, engineering, meta]      # tdd/systematic-debug/git-worktree/
                                              # search-first 等全归无涯
    mcp: [mempalace, Searchpin]
    tools: [file_read, file_write, code_execution, optim_plan, ws_*, git_*]  # §2.1 锚点
```

### 14.3 落点（四层，逐层可独立验收）

| 层 | 改动 | 验收 |
| --- | --- | --- |
| **① 技能分类透传** | list_skills 返回 `scenario` 字段（一行） | API 含 scenario |
| **② 前端场景过滤** | 技能选择器按会话场景过滤可挂技能 + 按 scenario 分组展示 | 子瞻会话看不到 tdd/无涯技能，反之亦然 |
| **③ 挂载校验** | add_supplementary_skills 校验技能 scenario ∈ 场景允许类目，否则 4xx 拒绝 | 跨场景挂载被拒 |
| **④ MCP/工具收敛** | monitor 锚点集（§2.1）+ 场景工具子集（可选，后续） | 通道仅装本场景所需 |

### 14.4 与无涯升级的关系

- 无涯技能包（§11）的 tdd/systematic-debug/writing-plans 恰好是 `scenario: engineering` 类目——**scene_binding.monitor.skills 直接圈定这些技能**，无需新技能，仅需归类配置
- 通道拥挤根治 = §9 注入纪律（top-15 锚点）+ §14 场景归类（源头收敛）双管齐下
- 四场景技能归属建议（2026-08-16 蒋先生确认后修订）：

| 场景 | 主技能 | 允许附加技能类目 | 说明 |
| --- | --- | --- | --- |
| 子瞻（office） | office | documents、**writing** | 蒋先生确认：office 场景需要写作类技能（文章/报告/润色） |
| 白圭（data_analysis） | data_analysis | documents（不含 engineering） | 蒋先生确认：白圭是投资理财分析，**不需要 engineering 类目** |
| 清和（frontend_design） | frontend_design | design、engineering | 前端开发需要工程技能（git-worktree 等） |
| 无涯（monitor） | monitor（内置） | engineering、meta | tdd/debug/git-worktree/search-first |

**engineering 类目边界（2026-08-16 蒋先生澄清）**：
- engineering 类目 = 软件工程向技能（tdd 红绿重构 / systematic-debug 四阶段调试 / git-worktree 隔离开发），
  服务对象是**编码类智能体**（无涯、清和的工程侧），**不适用于数据分析类**（白圭）。
- 白圭的"排错"需求是**数据层**（财务计算错误、数据源问题、指标口径、模型参数），
  由主技能 + code_execution（财务计算/估值模型）承担，不配 engineering 附加技能。
- 若未来出现"数据分析排错"类技能（如数据校验/指标口径核查），应新增独立类目
  （如 `analysis-eng`）而非复用 engineering，保持类目语义纯净。

### 14.5 零回归保障

- 默认场景（无 scene_binding 配置）行为不变（技能全量可挂，与现状一致）
- scene_binding 为空技能类目时 = 允许全部（向后兼容）
- 前端按 scenario 过滤为渐进增强，旧配置不破坏

## §15 设计→后端→前端一致性审计（2026-08-16，附缺口清单）

### 15.1 审计范围与方法

按设计文档能力项逐项核对后端实现（backend/private_agent）与前端消费（frontend/renderer），
覆盖 Harness（A-1）/评测（A-2）/压缩（B-1/C-2）/工作区（C-1）/场景归类（§14）/监控工具（monitor_tools）。

### 15.2 已严格映射（后端实现 + 前端消费，闭环完整）

| 能力项 | 后端锚点 | 前端锚点 | 状态 |
| --- | --- | --- | --- |
| 附加技能挂载（Phase 2 多技能） | admin.py:122-201 list/add/remove | App.tsx:1488-1560 技能弹层 + 已挂载加载/勾选 | ✓ 完整闭环 |
| 技能列表/改名/测试/删除 | /admin/skills 全系 | AgentLibraryView + SettingsView SkillsSection | ✓ |
| MCP server 管理 | /admin/mcp/servers | SettingsView「工具与 MCP」分区（McpAddForm） | ✓（server 级） |
| 场景工作区（C-1） | sessions.workspace + ws_* 工具 | SettingsView:111-143 sceneWorkspaces 展示 | ✓ |
| 场景名/画像（M1） | admin 透传 scene_name/profile | App.tsx renderChatAssistantName 等 | ✓ |
| 权限确认（pendingConfirm） | elevated 工具 WS 确认 | 全局弹窗（2026-08-16 三层加固） | ✓ |
| 监控窗口 | monitor 会话 + 专属工具 | App.tsx:796-798 slot 0 监控窗口 | ✓（对话级） |

### 15.3 缺口清单（后端有、前端无）—— 排入实施顺序

| # | 缺口 | 后端锚点 | 前端现状 | 风险 | 实施阶段 |
| --- | --- | --- | --- | --- | --- |
| G1 | **优化审批列表**：optim_plan 落库 pending 后，前端无入口展示/批准；monitor_tools 输出明确提示"请在监控窗口的「优化审批」列表中确认"，但该列表不存在 | monitor_tools.py optim_plan/apply_optim + optim_log 表 | 零实现（grep optim 无结果） | **高：apply_optim 审批闭环在 UI 层断裂** | 并入阶段 1（无涯打通手） |
| G2 | **会话级 MCP/工具装配展示**：用户无法看到当前会话绑定了哪些工具/MCP（通道收敛效果不可感知） | skill_binding 装配过滤（已实施） | 仅 server 级管理，无会话级视图 | 中 | 并入阶段 1（可视化） |
| G3 | **harness 段展示**：技能面板未展示 harness（A-1 标注"可选展示"） | admin list 已透传 harness | 零展示 | 低 | 可选，随阶段 1 |

### 15.4 设计未实施（预期内，均为新设计，排入对应阶段）

| 设计项 | 状态 | 对应阶段 |
| --- | --- | --- |
| §14 场景归类：list_skills 透传 scenario | 未实施（前端弹层仅按 model_scope 分组） | 阶段 1（并入） |
| §14 场景归类：前端按场景过滤 + 类目分组 | 未实施 | 阶段 1（并入） |
| §14 挂载校验（跨场景 4xx 拒绝） | 未实施 | 阶段 1（并入） |
| §2-§13 无涯升级全部（锚点/apply_optim 真执行/git 工具/七环节工作流） | 阶段 1-5 未开始 | 阶段 1-5 |

### 15.5 结论

- 既有功能前后端闭环完整（多技能挂载/技能管理/MCP 管理/工作区/权限确认/监控窗口）；
- **最需优先处理的是 G1 优化审批列表**——非新设计缺失，而是既有功能断裂：
  无涯提交优化建议（optim_plan）后用户无入口查看/批准，apply_optim"审批后执行"从未真正到达用户，
  与"无涯没有手"（注入/权限/执行三层）为同一根因链的 UI 侧表现；
- G2/G3 + §14 场景归类可合并为"场景/工具可视化"子阶段，随阶段 1 一并实施。

