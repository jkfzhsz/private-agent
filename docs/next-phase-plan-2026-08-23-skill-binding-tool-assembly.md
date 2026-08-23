# 0.5.2 阶段计划：skill_binding 语义重构 —— "起始清单 + 按需全局"双层工具模型 + 设置页配置化

> 版本：v1（2026-08-23 初稿，待蒋先生评审）
> 依据：蒋先生 2026-08-23 决策 —— 无涯作为全局智能体应拥有全局工具浏览能力，但不必每轮注入全局；skill_binding 只应限制**对话起始工具清单**，不应成为硬边界；设置页须提供 skill_binding 配置 UI，禁止工具绑定改动走代码侧。
> 状态：**设计阶段，未实施**（蒋先生确认后执行）

---

## 一、问题与目标

### 1.1 现状（证据链，2026-08-23 实测）

| 项 | 现状 | 证据 |
|---|---|---|
| MCP 装配 | `get_tools(cfg, server_ids)`：server_ids 非 None 时按 `_match_server_ids` **fnmatch 硬过滤**，未绑定 server 的工具**完全不进工具池** | `mcp_tools.py` L159-196 / L269-282 |
| 无涯装配 | `config.yaml` L240 `monitor: ["mempalace", "Searchpin"]`——只装配 2 个 server | `config/config.yaml` L232-241 |
| 执行安全网 | `_find_tool` 遍历 `self._tools` 全池（模型明确请求时仍可执行）——**但池内无未装配工具** | `react_loop.py` L2348 |
| 配置入口 | skill_binding 仅存在于 `config.yaml` 静态配置，**无 UI 配置入口** | `config/config.yaml` L232 |
| 已有工具 | 内置 `mcp_server_list`（列 server 配置摘要）/ `mcp_server_add`（新增 server），**不列工具、不装配** | `tools/builtins/mcp_config_manager.py` |

**核心问题**：
1. skill_binding 是**硬边界**——未绑定即不可达，无涯无法按需访问 codegraph/iFind 等全局能力；
2. 上下文工程是**每轮 top-N 注入**（已实现，`ToolSelector`）——**注入受控已解决，可达性不应再受限**；
3. 配置**代码硬编码**——新增/调整绑定须改 config.yaml（或将来改代码），无 UI。

### 1.2 设计目标（三条）

1. **全局可达**：所有智能体（含无涯）可通过**按需机制**浏览/调用**全部已启用 MCP server 的工具**（不受 skill_binding 硬过滤）；
2. **上下文受控**：每轮注入仍为"绑定子集 + top-N"（起始清单 = 轻量，全局 = 按需拉取），不因全局可达而膨胀 schema；
3. **配置 UI 化**：设置页提供 skill_binding 配置（场景 × MCP server 矩阵勾选），后端 admin API 读写，config_runtime 覆盖 yaml 默认。

---

## 二、方案设计：双层工具模型

### 2.1 语义定义（重构后）

| 概念 | 语义 | 实现 |
|---|---|---|
| **起始清单（skill_binding）** | 对话启动时装配到工具池的 MCP server 子集（种子）——决定**默认可见**工具 | `get_tools(cfg, server_ids)`（现状保留） |
| **按需全局（on-demand）** | 智能体可随时浏览/装配**全部 enabled server** 的工具——决定**可达**范围 | 新增内置工具 `mcp_browse` + 执行安全网扩展 |
| **注入受控** | 每轮仅 top-N 工具 schema 注入模型（含按需装配的池） | `ToolSelector`（现状保留） |

**原则**：`skill_binding` 从"能力边界"降级为"**初始视图偏好**"；能力边界只由 MCP server 自身的 `enabled`/`assemble` 开关决定。

### 2.2 后端改动

#### A. `mcp_tools.py`：新增全量发现能力
```python
async def get_all_server_summary(self, cfg) -> list[dict]:
    """返回全部 enabled 且 assemble 的 server 摘要(含未绑定): id + 工具数 + 状态。"""
    # 遍历 tools.mcp.servers, 过滤 enabled/assemble, 不连接(仅配置摘要)
    # 返回 [{id, tool_count_hint, bound: bool}]

async def load_tools_by_server(self, cfg, server_ids: list[str]) -> list[ToolDef]:
    """按需装配指定 server 的工具(复用 _load_server_tools 缓存), 供会话池扩展。"""
```

#### B. 新增内置工具 `mcp_browse`（`tools/builtins/mcp_config_manager.py` 扩展）
- 分类：`list`/`exec` = `safe`（只读/中转执行）；`assemble`/`remove` = `elevated`（确认后装配，防工具池被随意扩张）
- 参数：`action: "list" | "exec" | "assemble" | "remove"`；`server_ids?: list[str]`；`tool?: str`；`args?: dict`
  - **`list`**：返回**全部 enabled 未绑定 server 的工具"索引"**（server id + 工具名 + 一句话描述，仿 Anthropic Skills 渐进披露第一级——~100 tokens 级，不加载全 schema），含是否已装配标记
  - **`exec`**（借鉴 Docker mcp-exec，v1.1 新增）：按工具名 + 参数**直接中转调用**——**工具定义不进工具池、不进上下文**（后端查全量 server 工具 schema 执行，结果返回），上下文零占用；适用于低频/临时工具（如 codegraph 偶发查询）
  - **`assemble`**：按 server_ids 装配工具到会话工具池（复用 `load_tools_by_server`），装配后每轮自动进 ToolSelector.select（对齐 opencode 每 turn resolve 语义）
  - **`remove`**（v1.1 新增，借鉴 Docker mcp-remove）：从会话池移除已装配 server，防池膨胀
- 注入策略：`mcp_browse` 自身加入 `always_include` 锚点（monitor 与场景通用）——**入口工具全局可见，实际工具按需 exec/装配**
- 会话级：exec 中转结果与装配结果均记录入 react_events（可回放）；装配上限 config `tools.mcp.max_attached_servers` 默认 6

#### C. `react_loop.py`：`_find_tool` 安全网扩展（可选增强）
- 未命中时：若工具名匹配 `mcp__{server_id}__*` 且该 server enabled → **自动按需装配该 server 再查找**（防 Agent 基于摘要误调用）
- 默认关闭（由 config `tools.mcp.auto_attach_on_miss` 控制，默认 false——先走显式 `mcp_browse`，避免隐式装配复杂度）

#### D. `config.yaml`：语义注释更新
```yaml
skill_binding:
  # 语义(2026-08-23 重构): 对话起始工具清单(种子), 非硬边界。
  # 智能体可通过 mcp_browse 按需浏览/装配全部 enabled server 的工具。
  # UI: 设置页 → 智能体工具装配(config_runtime 覆盖此默认)。
  office: ["hexin-ifind-ds-*", "mempalace", "Searchpin"]
  data_analysis: ["hexin-ifind-ds-*", "mempalace", "Searchpin"]
  frontend_design: ["mempalace", "Searchpin"]
  monitor: ["mempalace", "Searchpin"]   # 保持轻量; codegraph 等按需 mcp_browse
```

#### E. admin API：skill-binding 读写
```
GET  /admin/config/skill-binding        → 合并后绑定 {office:[...], ..., monitor:[...], sources:{yaml|runtime}}
PUT  /admin/config/skill-binding        → 写 config_runtime tools.mcp.skill_binding(整体覆盖, UI 全量提交)
DELETE /admin/config/skill-binding      → 删 runtime 键, 回退 yaml 默认
```
- 校验：server id 须存在于 `tools.mcp.servers`（或合法通配如 `hexin-ifind-ds-*`），非法项拒绝并提示
- 生效：**新会话生效**（会话启动时读取；运行中会话维持锁定装配——与 Skill 版本锁定一致）

### 2.3 前端改动：设置页"智能体工具装配"

**位置**：设置页 → MCP/工具分区新增「智能体工具装配」卡片（与"MCP 服务"管理并列）

**UI 结构**：场景 × server 勾选矩阵
```
        mempalace  Searchpin  codegraph  hexin-ifind-ds-*  iFind基金  ...
子瞻(office)   [x]        [x]        [ ]         [x]            [x]
白圭(data)     [x]        [x]        [ ]         [x]            [x]
清和(fd)       [x]        [x]        [ ]         [ ]            [ ]
无涯(monitor)  [x]        [x]        [ ]         [ ]            [ ]
```
- server 列来源：`GET /admin/mcp/servers`（现有接口扩展返回全部 enabled server）
- 通配展示：iFind 系按 server id 通配前缀分组（`hexin-ifind-ds-*` 展开为多列或折叠组）
- 保存：`PUT /admin/config/skill-binding` → 成功提示"新会话生效"
- 附注文案：说明"绑定=对话起始工具清单，智能体可经 mcp_browse 按需访问全部工具"

### 2.4 配置分层

| 层 | 内容 | 优先级 |
|---|---|---|
| `config.yaml` | 默认绑定（现状） | 低（无 runtime 时） |
| `config_runtime` `tools.mcp.skill_binding` | UI 写入的整体覆盖 | 高 |
| 读取 | `config_runtime` 存在则用之，否则 yaml | 与现有配置分层一致（`_load_cfg` 合并） |

---

## 三、改动清单

| 文件 | 改动 | 风险 |
|---|---|---|
| `tools/mcp_tools.py` | + `get_all_server_summary` / `load_tools_by_server` | 低（新增方法） |
| `tools/builtins/mcp_config_manager.py` | + `mcp_browse` 工具（list/assemble） | 中（新工具，elevated 装配） |
| `core/react_loop.py` | 按需装配结果并入 `self._tools`；`_find_tool` 未命中扩展（默认关） | 中（核心循环，谨慎） |
| `api/admin.py` | + skill-binding GET/PUT/DELETE | 低（新增路由） |
| `main.py` | monitor/场景分支不变（起始清单语义不变）；锚点加 `mcp_browse` | 低 |
| `config/config.yaml` | 注释更新 | 零 |
| 前端设置页 | 工具装配矩阵 UI | 中（前端） |

**不变量**（保持现状，避免回归）：
- 每轮注入仍 top-N（ToolSelector）；frozen_tools 不含 MCP（frozen hash 不受影响）；
- 场景智能体绑定保持现状（office 含 ifind 等）；无涯起始清单保持 mempalace/Searchpin 轻量；
- codegraph 等**不默认加入任何绑定**——按需 `mcp_browse` 装配（符合"起始轻量、全局按需"语义）。

---

## 四、验收标准

| # | 验收项 | 量化标准 |
|---|---|---|
| V1 | 无涯按需访问 codegraph | 无涯会话调 `mcp_browse list` → 见 codegraph 未绑定；`assemble [codegraph]` → 工具池出现 `mcp__codegraph__codegraph_explore`，可正常调用 |
| V2 | 起始清单不变 | 无涯新会话默认工具池仍 = mempalace/Searchpin（+ 内置），无 codegraph（未显式装配前） |
| V3 | 注入受控 | 每轮注入 schema 数量不因按需装配显著膨胀（top-N 生效） |
| V4 | UI 配置 | 设置页勾选/保存绑定 → config_runtime 写入 → 新会话按新绑定装配；DELETE 回退 yaml |
| V5 | 安全 | `mcp_browse assemble` 走 elevated 确认（WS 60s）；非 enabled/assemble=false 的 server 不可见不可装配 |
| V6 | 回归 | 后端 pytest（加载 .env）全过；前端 tsc 0 错 + vitest 全过 |

---

## 五、回滚与风险

| 风险 | 对策 |
|---|---|
| 按需装配延迟（首次连接 server 慢） | `load_tools_by_server` 复用现有超时/缓存；失败返回明确错误不阻塞 |
| 工具池膨胀（多次 assemble） | 会话级装配结果有上限（config `tools.mcp.max_attached_servers` 默认 6）；超限提示 |
| mcp_browse 误装配 | elevated 确认 + 装配日志入 react_events（可回放） |
| 配置回退 | DELETE runtime 键 → 回 yaml 默认；代码改动独立 commit 可 revert |
| 场景智能体意外扩张 | 按需装配遵循各 server enabled/assemble；场景绑定默认不变 |

---

## 六、实施顺序（确认后）

1. 后端：`mcp_tools.py` 新增方法 → `mcp_browse` 工具 → admin skill-binding API（含测试）
2. 后端：`react_loop.py` 按需装配并入 + 锚点（`mcp_browse` 进 always_include）
3. 前端：设置页工具装配矩阵（tsc + vitest）
4. 验证（V1-V6）+ 测试基线 → git 独立提交链（可 revert）
5. 记忆宫殿双写 + 版本收尾

---

## 七、对标分析：opencode（sst/opencode）工具调用机制（2026-08-23 调研）

> 依据：opencode 官方文档（opencode.ai/docs/mcp-servers）、DeepWiki（sst/opencode 源码解析）、dev.to《OpenCode Tool Calling Internals》。opencode 是知名开源终端编码 agent（Apache-2.0），其工具运行时设计对本方案的验证与校准价值高。

### 7.1 opencode 机制（源码实证）

- **工具目录（Tool Catalog）**：`ToolRegistry` + `SessionTools.resolve()` **每个 turn 重算**——工具可见性取决于 agent/model/provider/权限/MCP 客户端，不维护静态列表（dev.to："Build a tool catalog, not a static tool list"）；
- **注入方式**：**全量注入**（无 top-N 动态选择）——MCP 工具与内置工具一起提供给 LLM；
- **上下文控制**：靠**用户手动选择启用哪些 MCP server**（`opencode.json` 的 `mcp.enabled` 开关）。官方文档明确警告："MCP 服务器会占用上下文空间，启用大量工具时上下文消耗迅速增加，**请谨慎选择**"；
- **权限边界**：工具执行点 permission gate（y/n 提示，`Permission.ask`）——**边界在执行层而非注入层**；
- **无按需装配机制**：MCP server 要么 enabled（全量进注入），要么 disabled（完全不可见）。

### 7.2 对比结论

| 维度 | opencode | PA 双层模型（本设计） | 判定 |
|---|---|---|---|
| 注入 | 全量（所有 enabled 工具每轮注入） | 起始清单 + ToolSelector **top-N 动态** | **PA 更优**（opencode 官方文档自认 MCP 全量注入占上下文是问题；我们已解决） |
| 边界 | `enabled` 开关 + Permission.Ruleset | enabled/assemble 开关 + skill_binding（起始视图） | **语义对齐**（边界=开关；可见性=过滤） |
| 按需装配 | 无（手动切开关） | `mcp_browse`（agent 自主 list/assemble） | **PA 超集**（更灵活且不依赖用户手工） |
| 目录重算 | 每 turn resolve | 每轮 ToolSelector.select + 按需装配并入池 | 对齐（注入都是每轮动态） |
| 权限 | 执行点 gate | elevated WS 确认（执行点） | 对齐 |

### 7.3 借鉴点（采纳，强化设计表述）

1. **"每 turn 目录重算"语义显式化**：按需装配（`mcp_browse assemble`）的 server 工具**立即并入会话工具池，后续每轮自动进入 ToolSelector.select 池**——与 opencode 每轮 resolve 对齐（设计 §2.2-C 已含，此处显式列为验收项）；
2. **"enabled 是唯一硬边界"显式化**：除 `enabled=false`/`assemble=false` 外，**任何 server 工具都可达**（skill_binding 仅为初始视图）——与 opencode 边界语义一致，且多了 top-N 与按需两层能力；
3. **不采纳** opencode 的"全量注入 + 手动开关"模式——官方文档自认其上下文问题，PA 的 top-N 注入是明确优势，转向全量是退步。

**结论**：本方案（起始清单 + 按需全局 + top-N 注入 + UI 配置）**不劣于 opencode 且在其基础上更优**；对齐其"边界=开关、权限=执行点、目录每轮动态"三个正确原则即可，无需转向其模式。

### 7.4 自主装配先例：Docker Dynamic MCP + Anthropic Agent Skills（2026-08-23 调研）

**Docker MCP Gateway —— Dynamic MCP（官方生态，mcp-find/mcp-add 先例）**：
- 网关暴露 **primordial 管理工具**：`mcp-find`（目录搜索）/ `mcp-add`（会话级装配）/ `mcp-remove` / `mcp-exec`（**工具不进上下文直接中转调用**）/ `mcp-config-set` / `code-mode`（组合工具）；
- Agent 自主工作流：任务分析 → mcp-find 搜索 → mcp-add 装配 → 执行；**会话级作用域**（不持久化，新会话干净）；
- **mcp-exec 是上下文最省方案**：server 工具定义不注入，经 mcp-exec 中转调用（官方："avoiding the need to load all available tools into every LLM request"）；
- 与 Anthropic《building more efficient agents》的 `search_tools` 建议呼应。

**Anthropic Agent Skills —— Progressive Disclosure（2025-12 开放标准，skills 自主加载先例）**：
- **三级渐进披露**：启动只加载每个 skill 的 **name + description（~100 tokens）**进 system prompt（索引级）→ 模型判断相关时读 SKILL.md 全文（正文级）→ 引用的参考文件按需再读（深层级）；
- **"reference book on the shelf"**：几十个 skills 只付索引成本，使用时才加载——上下文几乎零占用；
- **模型自主调用**：Claude 根据 description 自动选择何时使用 skill；`skills` 选项控制会话可用集；
- Skills 与 MCP **互补**：server 提供能力，skill 教 agent 怎么用好。

**哲学共识（late binding）**：工具列表/加载的 skills/连接的 server/委派决策——**全部尽可能晚解析**（模型信息最多时决策），而非预烘焙全量。

### 7.5 借鉴升级（v1.1，采纳 3 点）

| # | 借鉴 | 来源 | 落到设计 |
|---|---|---|---|
| 1 | **`mcp-exec` 中转执行**（工具不进上下文） | Docker Dynamic MCP | `mcp_browse exec` 动作（§2.2-B）：按工具名+参数直接中转，上下文零占用，低频/临时工具首选 |
| 2 | **工具"索引"模式**（name+description 索引，不全 schema） | Anthropic progressive disclosure | `mcp_browse list` 返回工具索引（~100 tokens 级），模型看索引决定 exec/assemble |
| 3 | **`mcp-remove` 会话内移除** | Docker Dynamic MCP | `mcp_browse remove` 动作（§2.2-B）：防装配池膨胀 |

**明确不采纳**：Docker 的容器隔离（PA 是本地单机、无 docker 依赖）；Anthropic "全部 skill 索引常驻 system prompt"（PA 场景锁定 skill + top-N 已更省；工具索引仅经 mcp_browse 按需拉取）。

**验证结论**：本设计 `mcp_browse list/exec/assemble/remove` 与 Docker Dynamic MCP（find/exec/add/remove）**四动作一一对应**，方向被官方生态验证；工具索引模式采纳 Anthropic 渐进披露思想——**设计 v1.1 相比 v1 的增强：exec 中转 + 工具索引 + remove 治理**。

---

*设计待蒋先生评审。确认后按 §六 顺序实施。*
