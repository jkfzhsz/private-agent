# Reasonix 技能库移植 + LLM 前缀缓存命中优化

日期：2026-08-08
状态：设计评审稿（待用户确认后动工）

## 1. 背景与目标

用户提供 DeepSeek-Reasonix-main-v2（DeepSeek 原生终端 AI coding agent，TypeScript+Go 内核）
及社区技能库 reasonix-skills，希望**改造成 PA 可调用的技能库**，并**提升 LLM 缓存命中率**
（DeepSeek 前缀缓存，命中可显著降本提速）。

目标：
1. PA 具备 Reasonix 风格的**通用技能库**（Markdown playbook，`run_skill` 按需加载）
2. 技能库设计为**缓存友好**：技能索引（仅 name+description）进稳定前缀，body 永不进前缀
3. 全链路贯彻 Reasonix 的**前缀缓存优先**纪律，提升命中率

## 2. Reasonix 核心机制分析（可移植点）

### 2.1 缓存优先架构（核心价值）

- **System prompt 前缀 byte-stable**：base prompt + 工具 schema + 技能索引 + 记忆索引
  跨轮次完全不变（REASONIX.md 明确 "Cache-first: 前缀必须保持 byte-stable"）。
- **一切可变信息 ride the turn tail**（注入用户消息而非 system）：
  - `<active-goal>` 目标状态、plan-mode marker
  - `<memory-update>` 本会话新增记忆（下一会话才并入稳定前缀）
  - `<background-jobs>` 后台任务完成通知、hook context、记忆召回块
- **压缩纪律**：旧工具输出先 snip/prune 再摘要 compaction，绝不动稳定前缀。
- **缓存诊断**（`internal/agent/cache_shape.go`）：CaptureShape 对 system/tools 做
  sha256 hash，CompareShape 跨轮比较，产出 CacheDiagnostics（PrefixChanged + reasons +
  hit/miss tokens），解释每次缓存 miss 的根因（system 变了 / tools 变了 / 内容被重写）。
- **工具 schema 合约**：内置工具 schema 有文档 + 回归测试保护，防 schema 抖动。

### 2.2 技能库设计（internal/skill/）

- 技能 = Markdown 文件 `<name>/SKILL.md`，frontmatter：
  ```yaml
  ---
  name: reasonix-guide
  description: "一句话用途说明（决定模型何时调用）"
  runAs: inline        # inline=折叠进当前轮 | subagent=独立子循环
  ---
  ```
- **发现约定**：`.reasonix/skills`、`.agents/skills`、`.agent/skills`、`.claude/skills`
  （项目级）+ home 全局；`[skills].paths` 追加自定义根；disabled/excluded 可屏蔽。
- **缓存友好索引**（index.go）：只把 **name + description** 渲染成系统提示中的索引块
  （`IndexMaxChars = 4000` 上限），**body 按需加载**——调用时经 `run_skill` 工具注入本轮，
  body 永远不进稳定前缀，技能再多也不撑爆缓存前缀。
- 调用：`run_skill({name, arguments})`，inline 模式 body 作为 tool result 进入上下文。

### 2.3 其他特性

- **R1 思考采集**（openai/think.go）：`<think>...</think>` 块剥离为 reasoning，与
  `reasoning_content` 双通道处理。
- **工具调用修复**：工具调用失败自动修复重试（repair 语义）。
- **多模型组合**：执行器 + 规划器双模型，各自独立缓存稳定 session。

## 2.4 Skills 作用机理（源码确认，2026-08-08 补充）

完整链路（internal/skill/skill.go）：

1. **发现 Discovery**（scanDir 递归，maxDepth 限制）：
   - 扫描根：项目 `.reasonix/.agents/.agent/.claude/skills` + home 全局 + `[skills].paths` 追加根
   - 目录技能 `<name>/SKILL.md`；**扁平 `<name>.md` 仅当带 skill frontmatter 才加载**
   - 递归子目录（depth 1 直接收，更深层要求 description 非空）；跳过 `.`开头目录、
     assets/node_modules/references/scripts；跟随 symlink；scope 优先级
     project > custom > global > builtin
2. **解析 Parse**：`---` YAML frontmatter + body：
   - 核心：`name`（覆盖文件名 stem）、`description`（缺失=警告，**不进索引**）
   - 可选：`runas/context/agent`（inline|subagent）、`allowed-tools/tools`、`model`、
     `effort`、`read-only`、`triggers`、`negative-triggers`、`auto-use`、
     `needs-fresh-data`、`cost`、`color`、`invocation`(auto|manual)、`requires`、`profiles`
   - body 增强：`@path` 引用展开（loadBodyWithReferences）、`scripts/` 目录脚本
     （loadBodyWithScripts）
3. **索引 Index（缓存友好核心）**：只渲染 **name + description** 进 system prompt
   Skills 块（`IndexMaxChars = 4000` 硬上限），**body 永不进前缀**；
   `invocation: manual` / 无 description 不进索引
4. **调用 Invoke**：模型自主 `run_skill({name, arguments})`：
   - inline：body（含展开的引用/脚本）作为 tool result 折叠进本轮 —— 轻量首选
   - subagent：隔离子循环，只回最终答案（重型、上下文隔离）
   - 触发增强：`triggers`/`auto-use`/`needs-fresh-data` 让技能按条件自动触发
5. **治理**：`disabled_skills`/`excluded_paths` 屏蔽；`profiles`/`requires` 门控

### 2.5 reasonix-skills 社区仓库实测（backend/skills/reasonix-skills-main.zip）

- 17 个技能、5 分类：meta(3)/writing(6)/documents(4)/engineering(4)
- 结构为**扁平 `<category>/<name>.md`**（非 `<name>/SKILL.md` 目录式）—— Reasonix
  递归扫描可发现（depth 2 + description 非空 ✓）
- frontmatter 仅 `name` + `description` 两字段（无 runas → 默认 inline）
- 内容为纯方法论文案（TDD/写作/PDF 生成/调试等），**不携带工具依赖/权限声明**
- 亮点：use-skills（智能组合推荐，含 Token 成本估算）、duan-nian-jian（小说工作流）、
  novel-workflow（长篇写作工程化）

## 2.6 与 PA 现有模式技能的范式差异（关键）

| | PA 模式技能（office 等） | Reasonix playbook 技能 |
|---|---|---|
| 形态 | 独立 skill.yaml + system_prompt.md + tools.yaml + examples/ | 单个 SKILL.md（frontmatter+body） |
| 性质 | **声明式会话模式**（工具集/权限/知识库/示例注入 frozen） | **按需 playbook**（纯提示词方法论） |
| 加载 | 会话启动三选一，决定会话骨架 | 轮次内 run_skill 按需折叠 |
| 缓存影响 | examples 注入 frozen_zone（启动时固定） | 仅索引进 frozen，body 按需加载 |

**结论：两者互补并存** —— 模式技能保留（会话骨架），playbook 技能库作为任务打法。

## 2.7 YAML 头部适配（用户指出，2026-08-08）

reasonix-skills 的 `---name/description---` 是 **markdown 内嵌 frontmatter**，
PA 现有解析器读的是**独立 skill.yaml**（name/version/dependencies.tools/permissions/
knowledge_base/examples/max_frozen_token）—— 格式完全不同，PA 不认识 SKILL.md frontmatter。

适配方向（**不改 reasonix-skills 内容**，PA 新增解析能力）：
- PA 新增 SKILL.md frontmatter 解析器（核心字段子集：name/description/runas/triggers）
- 双格式并存：目录级 skill.yaml（模式技能，现状）+ SKILL.md/扁平 md（playbook 技能，新）
- playbook 索引注入 frozen；模式技能维持现状

### 字段认识能力矩阵（源码确认，2026-08-08）

PA 技能模型 = `SkillManifest`（pydantic，private_agent/skills/models.py），
loader 读 `skills/{name}/skill.yaml + system_prompt.md + tools.yaml` 目录结构。

| Reasonix frontmatter 字段 | PA 认识？ | PA 对应消费点 |
|---|---|---|
| `name` | ✅ | SkillManifest.name |
| `description` | ✅ | SkillManifest.description |
| `runAs`(inline/subagent) | ❌ | PA 有 SubagentRunner（独立子 session+ReactLoop）但**无技能级 runAs 绑定**；inline 模式无对应机制（需新增 run_skill） |
| `allowed-tools` | ⚠️ 字段名不认，**语义有对应** | dependencies.tools（ToolDependency 工具白名单 + safety_level_override + enabled，工具装配时消费） |
| `model`(subagent 模型覆盖) | ❌ | PA 仅**会话级**模型选择（sessionModel/fallback 链）+ model_params（temperature 等**参数**，非模型选择）；无技能级 model 覆盖 |
| `triggers/auto-use/cost/requires/profiles` 等 | ❌ | 无 |

**pydantic 默认 extra='ignore'**：未知字段**静默忽略**（不报错、不消费）——但
reasonix-skills 是扁平 md + frontmatter，PA loader 连目录结构都不匹配，
name/description 也拿不到（格式适配优先于字段适配）。

**决策：宽容解析 + 最小映射**
1. 第一期只消费 `name` + `description`（缓存友好索引的前提），其余 Reasonix 私有字段
   **宽容忽略**，reasonix-skills 原样可用、零风险
2. 后续可选语义映射（不阻塞第一期）：
   - `allowed-tools` → 映射为 PA 工具白名单（dependencies.tools 语义，技能执行时过滤）
   - `runAs=subagent` → 挂到现有 SubagentRunner（独立子 session）
   - `model` → 需新增"技能级模型覆盖"（激活技能时锁 model_id），扩展面较大，暂缓


## 3. PA 现状对照

| 维度 | PA 现状 | Reasonix | 差距 |
|---|---|---|---|
| 稳定前缀 | Frozen Zone（system+[TOOLS] JSON，turn=0，hash 锁定） | system 前缀 byte-stable | ✅ 同构，已具备 |
| 可变信息 ride tail | status_bar 每轮注入末尾；memory_evicted 入库 | goal/memory-update/jobs 全 ride tail | ⚠️ 记忆更新/KB 注入是否进稳定区需审查 |
| 压缩 | Compressor 滑动窗口+摘要+按 msg_id 回写，frozen/stable 不参与 | snip/prune → compaction | ✅ 已具备 |
| 技能库 | 无（仅前端模式选择 office/data_analysis/frontend_design） | run_skill + 索引 ≤4000 字符 | ❌ **核心缺口** |
| 缓存诊断 | 无 | CaptureShape/CompareShape hash 对比 | ❌ 缺口 |
| 思考采集 | reasoning_content 回传（get_messages 剥离内部字段） | `<think>` + reasoning_content | ✅ 已具备 |
| 工具 schema 稳定 | frozen 含 [TOOLS]，MCP assemble 开关会改 | schema 合约+回归测试 | ⚠️ 需保护 |

结论：PA 的 Frozen Zone 已天然是缓存稳定前缀，**主要缺口 = 通用技能库 + 缓存诊断**。

## 4. 改造方案

### M1：缓存友好技能库（核心）

1. **技能存储与发现**（后端新模块 `private_agent/core/skills.py`）：
   - 扫描根：`backend/skills/`（项目级）+ `~/.workbuddy/skills/`（全局，兼容 WorkBuddy）
     + `~/.claude/skills/`（兼容 reasonix/claude 约定）
   - 格式：`<name>/SKILL.md`，frontmatter `name/description/runAs(inline)`；扁平
     `<name>.md` 仅当带 skill frontmatter 时加载
   - `[skills]` 配置：`paths`（追加根）、`disabled_skills`、`excluded_paths`

2. **索引注入 Frozen Zone（缓存友好）**：
   - 启动构建 frozen 时，若存在技能：在 system_prompt 末尾追加
     `# Skills\n索引(仅 name+description, ≤4000 字符, body 按需加载)` 块
   - **技能增删 → 索引变化 → 触发 frozen rebuild**（compute_frozen_hash 重算，等价
     Reasonix "下个会话生效"）；正常会话中索引 byte-stable，不破坏缓存

3. **`run_skill` 工具**：
   - 注册进工具列表（注入 frozen 的 [TOOLS]，schema 稳定）
   - 执行：读 SKILL.md body（markdown 原文）→ 以 tool_result 返回本轮 → 模型按 body 执行
   - inline 语义（subagent 模式 M2 再说）

### M2：缓存诊断（可观测）

- 新增 `CacheDiagnostics`：
  - 每轮请求后记录 `usage.prompt_cache_hit_tokens / prompt_cache_miss_tokens`
    （DeepSeek/OpenAI 兼容返回，PA adapter 需透传 usage）
  - 对 frozen 内容做 sha256（compute_frozen_hash 已有）+ 每轮比较，miss 时记录原因
    （system/tools 变化、压缩重写等）
  - 落库 react_events 或独立表，前端"任务诊断"面板可看命中率

### M3：前缀稳定性加固

1. **审查可变信息注入点**：记忆更新、KB 注入、状态栏——确保全部 ride turn tail
   （注入 active_zone 尾部消息），绝不改 frozen/stable 内容
2. **工具 schema 稳定性**：工具列表按 name 排序（避免 dict 序抖动）；MCP assemble 变更
   才触发 rebuild
3. **思考采集对齐**：确认 reasoning_content 全链路（已有），补 `<think>` 剥离兜底

### 技能内容（reasonix-skills）

zip 中**不包含** reasonix-skills 仓库（独立仓库）。实施时：
- 优先：用户提供 reasonix-skills 仓库副本，或从 GitHub 拉取
- 格式已兼容（SKILL.md + frontmatter），拉取后直接放入 `backend/skills/` 即可被发现
- 内置技能示例：`backend/skills/reasonix-guide/SKILL.md`（自诊断 playbook，演示格式）

## 5. 实施步骤

1. 后端 `skills.py`：目录扫描 + frontmatter 解析 + 索引渲染（含单测）
2. context_manager 集成：索引注入 frozen + rebuild 逻辑
3. `run_skill` 工具注册 + 执行（含权限：只读本地文件）
4. adapter usage 透传 + cache 诊断模块 + 事件落库
5. 前端任务诊断面板展示缓存命中
6. reasonix-skills 仓库接入验证

## 6. 验证方式

- 单测：skills 扫描/索引上限/disabled；run_skill 注入；frozen hash 稳定性
- **缓存实测**：同一会话连续多轮，检查 API 返回 usage 中
  `prompt_cache_hit_tokens` 占比（目标 >80%）；对比改造前后
- 诊断日志：每轮 PrefixHash + 变化原因

## 7. 风险与注意

- DeepSeek 前缀缓存是 provider 侧自动行为，PA 侧只能保证"前缀稳定"来配合；
  命中率受对话自然演进（工具调用结果追加）影响，不可能 100%
- 技能索引膨胀风险：IndexMaxChars 4000 硬上限，超出提示用户精简
- run_skill 只读本地文件，权限模型走现有 elevated 通道
- 后续可选：subagent 模式技能（隔离子循环）、技能热加载（文件监听）

## 8. 待确认

1. reasonix-skills 仓库如何获取（用户提供 / GitHub 拉取 / 仅内置示例先行）
2. M1（技能库+缓存友好）优先实施，M2/M3 是否同批
