# 对话流畅度优化方案

> 项目：私人智能体（Private Agent）· 后端 `backend/`
> 日期：2026-08-03 · 状态：**方案定稿，暂不实施**
> 背景：与 WorkBuddy 同用 deepseek-v4-flash，PA 回答明显更慢——根因是架构差异（ReAct 循环 + 工具全量注入 + 上下文限制错位）。本方案针对三大根因做底层优化。

---

## 0. 问题诊断（已代码级确认）

| # | 根因 | 证据 |
|---|---|---|
| ① | **231 个工具全量注入**每轮请求 | `core/react_loop.py` L80-82 快照 `_tool_schemas`，L301-316 每迭代全量传给 adapter；MCP 工具不参与 skill 白名单过滤（`tools/mcp_tools.py`） |
| ② | **上下文限制错位/死配置** | `max_input_tokens=8192`（react_loop.py L95）**零消费**；`active_zone_token_limit=4000` 死配置；实际压缩阈值= `context.compression.context_window` 默认 8000（触发线 80%=6400，`core/compressor.py` L38）——模型 128K 能力只用了 8K |
| ③ | **每轮冗余注入** | 状态栏每**迭代**注入（`core/status_bar.py`）、KB 片段无开关（12k 字符截断）、MCP guide 全量工具名单、记忆注入不可配 |

---

## 1. 方向一：Skill 决定工具池 + 每轮动态 top-N 工具注入（收益最大）

### 背景
skill 白名单只过滤内置工具，MCP 工具（231 个）每次全量进模型请求 → 工具 schema ≈ 20k-35k token/轮，模型每轮"翻完整本工具手册"。

### 实现要点
1. **新文件 `tools/selector.py`**：动态工具选择器
   - 确定性评分：关键词 TF 重叠（工具名+描述 vs 当前消息）+ 历史使用频率衰减加权 + `always_include` 锚点（如 code_execution）
   - 每轮 turn 开始时求值一次（迭代间固定 → 对 KV Cache 友好）
2. **`core/react_loop.py`**：`_tool_schemas` 改为每轮动态求值；`_find_tool` 遍历全池作安全网（`strict_injection` 可配置，默认 false 保证执行不遗漏）
3. **`tools/mcp_tools.py`**：`get_tools(server_ids)` 按 skill 绑定只装配对应 server；`build_tools_guide` 只列工具池
4. **配置新增** `config/config.yaml`：
   ```yaml
   tools:
     mcp:
       skill_binding:        # skill → MCP server 绑定
         office: []                          # 办公不挂金融 MCP
         data_analysis: ["hexin-ifind-ds-*", "mempalace"]
         frontend_design: ["mempalace"]
     tool_selection:
       enabled: true
       strategy: "keyword_score"   # 关键词/描述匹配
       top_n: 15                    # 每轮注入上限
       min_pool_size: 8             # 池下限(池<8 全量)
       always_include: ["code_execution", "file_read", "file_write"]
   ```

### 参数说明
- **top_N 取值策略**：默认 15；池内工具 ≤ min_pool_size(8) 时全量注入（小池无需裁剪）；MCP server 级 enabled 开关仍可手动关闭
- **评分公式**：`score(t) = 0.6×关键词重叠 + 0.3×历史使用衰减 + 0.1×描述相关性`；确定性（同输入同输出，测试友好）

### 预期效果
- 工具 schema token：~20k-35k → **~2k-3k（节省 ≈90%）**
- 首 token 延迟（TTFT）显著下降，多轮迭代收益叠加

### 风险
- frozen_hash 只锁内置白名单工具，top-N 不改变 hash；但 **guide 精简会改 frozen zone** → 已有会话触发 `replace_frozen_zone` 自动重建（已有机制兜底，首条消息延迟略增）
- 测试断言需从"全量工具"改为"≤top_n 且含必需工具"（`tests/test_react_loop.py`）

---

## 2. 方向二：放开上下文上限 + 动态匹配模型能力（依赖方向一）

### 背景
工具 token 不先压下去，放大窗口的空间仍被工具定义吃掉；模型 128K 能力与 8K 实际使用严重错位。

### 实现要点
1. **`models/base.py`**：`ModelCapability` 增加 `context_window: int | None = None`
2. **`config/loader.py` `resolve_provider_limits`**：返回 `context_window`（provider 级 > 全局默认）
3. **压缩触发线公式**（`core/compressor.py` / `core/react_loop.py`）：
   `threshold = min(provider.context_window, config.context_window) × trigger_ratio(0.8)`
4. **默认值调整**：
   ```yaml
   models:
     limits:
       context_window: 32768        # 原默认 8000 → 32K
       max_output_tokens: 4096      # 2048 → 4096(可选)
   # config_runtime providers.deepseek-v4-flash.context_window: 131072
   ```
5. **死配置清理**：删除 `max_input_tokens`、`active_zone_token_limit`（或标记 deprecated，避免继续误导）
6. **token 估算**：保持 `len/3` 粗估 + 可配置系数（128K 余量下无溢出风险）；tiktoken 精确估算列为 v2 候选

### 参数说明
- 压缩触发从 6400 token 延后到 min(模型能力, 32768)×80% ≈ 26K → **压缩频率大降**（少裁剪、少丢上下文）
- provider 级覆盖：deepseek-v4-flash 配 131072 时完全放开，由模型能力兜底

### 预期效果
- 长对话不频繁丢历史（keep_turns=6 滑动窗口触发概率下降）
- 上下文利用率提升，模型理解更完整

### 风险
- 单次压缩量变大（更晚触发 → 压更多轮）——需回归 `tests/test_compressor.py`
- 128K 大上下文单请求延迟略升（token 处理时间）——与方向一的工具精简对冲

---

## 3. 方向三：精简每轮注入内容

### 背景
每轮注入的附加内容（状态栏/KB/guide/记忆）与当前任务相关性低，纯耗 token 且干扰模型。

### 实现要点与参数
| 注入项 | 现状 | 优化方案 | 预期节省 |
|---|---|---|---|
| **状态栏** `<agent_status>` | 每**迭代**注入 | `inject_per_turn` 语义收敛为"每轮 1 次"；新增 `inject_every_iterations`（默认 3，迭代内每 3 次注入 1 次） | 0.5-1.2k token/轮 |
| **KB 片段** | 无开关，单条 12k 字符 | 新增 `kb.injection.enabled` + `max_chars`（12000→6000）+ 按轮数限制 | 2-4k token/次 |
| **MCP guide** | 全量工具名单 | 只列**注入的工具池**（方向一联动） | 1-3k token/轮 |
| **用户记忆** | `memory.inject_limit=10` 全量注入 | 单条截断（如 300 字符/条）+ 相关性排序取 top | 0.5-1k token/轮 |
| **Doom Loop 提示** | 触发时注入 | 保持现状（触发少，必要） | — |

### 配置新增
```yaml
context:
  status_bar:
    inject_every_iterations: 3
  kb:
    injection:
      enabled: true
      max_chars: 6000
  memory:
    max_item_chars: 300
```

### 预期效果
- 每轮非必要 token 节省 ~4-8k（合计），模型注意力更聚焦当前任务

### 风险
- 状态栏/KB 关闭后模型对"正在进行的工作"感知变弱 → 需真机验证阈值
- 所有开关默认**保留原行为**（渐进式，用户可回退）

---

## 4. 实施顺序与依赖

```
方向一(Skill 工具池 + top-N) → 方向二(放开上下文 + 模型能力) → 方向三(精简注入)
```
- **方向二依赖方向一**：工具 token 不先压下去，放大窗口的空间仍被工具吃掉
- **方向三的 MCP guide 项依赖方向一**；状态栏/KB/记忆项可独立并行
- 每方向完成即回归（pytest）并可单独上线（配置开关隔离，低风险）

## 5. 关键文件清单（改动范围）

| 文件 | 改动 |
|---|---|
| `backend/private_agent/tools/selector.py` | **新增**：动态工具选择器 |
| `backend/private_agent/core/react_loop.py` | 工具动态求值（L80-82/301-316）、压缩触发线（L921）、状态栏注入频率 |
| `backend/private_agent/tools/mcp_tools.py` | `get_tools(server_ids)` 按 skill 装配 + guide 精简 |
| `backend/private_agent/models/base.py` | ModelCapability 加 context_window |
| `backend/private_agent/config/loader.py` | resolve_provider_limits 返回 context_window |
| `backend/private_agent/core/compressor.py` | 触发线公式化 + 阈值来源 |
| `backend/private_agent/core/context_manager.py` | KB 注入开关/截断、记忆单条截断 |
| `backend/private_agent/main.py` | skill_binding 装配、provider limits 传递 |
| `backend/config/config.yaml` | 新增 tools.tool_selection / kb.injection / memory.max_item_chars；调整 context_window |
| 测试 | `tests/test_react_loop.py`、`tests/test_compressor.py`、新增 `tests/test_tool_selector.py` |

## 6. 测试方案要点
- **test_tool_selector**：确定性（同输入同输出）、top_n 裁剪、min_pool 全量、always_include 必含
- **test_react_loop**：工具参数断言改"≤top_n 且含必需工具"；迭代间 schema 不变（KV 友好）
- **test_compressor**：新触发线（32768×0.8）行为、provider context_window 覆盖
- **回归**：后端 pytest 906 全量 + 前端 vitest 13

## 7. 预期总效果（上线后）
- 首 token 延迟：预计下降 40-60%（工具 90% 削减为主因）
- 长对话保真度：上下文从 6.4K 触发压缩 → 26K+，历史保留大幅增强
- 每轮 token 消耗：下降 30-50%

## 8. 实施节奏建议
- 分方向实施，每个方向一个 git 提交，配置开关隔离（默认保留原行为），可独立回退
- 每方向完成先小范围真机验证（一个会话对比 TTFT/token），再全量
