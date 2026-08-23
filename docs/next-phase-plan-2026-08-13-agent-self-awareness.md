# 设计文档：PA 智能体自身认知系统（Agent Self-Awareness）

> 日期：2026-08-13 | 状态：**已实施（v2，按需自省为主）** | 作者：WorkBuddy（主代理）
> 关联：memory-kb-storage-诊断报告-2026-08-13.md
> 前置：P0 修复已实施（memory_search 分词 OR 匹配 + 候选集扩大）

---

## 一、背景：本次故障暴露的三类认知缺失

清和（frontend_design 场景）在诊断"记忆能写不能读"时暴露 PA 智能体对自身的三类认知盲区：

| 缺失类型 | 实例（清和实际行为） | 后果 |
|---|---|---|
| 1. 系统结构认知缺失 | 把记忆宫殿 mempalace 误判为"PG 通道"（实际 ChromaDB）；分不清三存储系统边界 | 得出错误根因，误导排障 |
| 2. 工具能力边界认知缺失 | 用 memory_search 做语义检索，3 次失败后推测"索引未刷新/会话隔离"（均不成立） | 无法正确归因，反复低效 |
| 3. 运行时状态认知缺失 | 不知道知识库写入只有前端通道；不知道当前装配了什么 | 给不出正确替代路径 |

**根本原因**：系统提示词只有人格与浅层"工具使用规范"，**没有一份关于 PA 系统自身的权威说明书**。

## 二、方案 v2：按需自省为主（采纳蒋先生决策）

> 蒋先生观点（2026-08-13）：能力地图/功能快照不需要会话启动一次性灌入、不需每几轮强调；让 LLM 按任务需要时查"说明书"，并在会话内缓存，更省 token。

**结论：观点成立，且量化后优势更明显。** 最终方案 = **一个精简的 `system_capabilities` 只读工具**（替代原 L1 注入地图 + L2 注入快照），模型按需调用，查到后在会话上下文内自然缓存复用。

### 2.1 为什么按需优于注入（token 量化）

中文 token 粗算（1 汉字 ≈ 1 token）：

| 方案 | 每轮常驻成本 | 按需成本 | 纯对话轮次净省 |
|---|---|---|---|
| 注入式（L1 地图全文 + L2 快照） | ~800-1100 token/轮（Stable 前缀每轮都带） | 0 | — |
| **按需式（本方案）** | **~250 token/轮**（工具 schema） | 首次查 ~800 token，缓存后 0 | **~550-850 token/轮** |
| 动态工具（非内核，连 schema 都裁剪） | ~0（最省） | 同上 | 最多，但触发不可靠 |

- 注入式的代价是**每轮**都把地图全文带进 input（Stable Zone 在每次 LLM 调用都传入），而 PA 大量轮次是纯对话/不碰存储系统，全文纯浪费。
- 按需式只付一份精简工具 schema，全文只在真正需要时查一次，查到后落在上下文里（对话缓存），后续轮次零成本。

### 2.2 关键风险：触发可靠性（"自信地不查"）

按需式有一个必须解决的软肋：**模型可能不知道有说明书可查，或过于自信而不查**——清和这次正是"自信地把 ChromaDB 当 PG"，从没想过查证。

**缓解设计（三层）**：
1. **工具描述含锚点**（成本≈0，描述本就要注入）：`system_capabilities` 的描述开头一句话点明"PA 有三个独立数据存储系统（原生记忆 PG / 场景知识库 PG / 记忆宫殿 ChromaDB），职责不同勿混用；不确定自身数据系统、工具能力边界或操作渠道时，调用本工具查询"。这句话让模型在 tool selection 阶段就获得"存在分歧"的意识。
2. **内核工具（始终可见）**：`system_capabilities` 设为 `is_kernel=True`（或加入 `_ALWAYS_AVAILABLE_TOOLS` 白名单豁免），不被 ToolSelector top-N 裁剪——否则纯对话轮次模型根本看不到这个工具，触发无从谈起。
3. **system_prompt 尾句（可选保险，~30 token）**：加一句"涉及数据存储/工具能力/系统渠道时，可调用 system_capabilities 查询自身说明书"。

> 权衡说明：这本质是"保证可见（内核，schema 常驻）vs 极致省 token（非内核，schema 动态裁剪）"的取舍。取内核，是因为**可靠性优先于再省几十 token**——自我认知入口若被裁剪，整个方案失效。

## 三、`system_capabilities` 工具设计

- `name="system_capabilities"`，`safety_level="none"`，`is_kernel=True`，readonly。
- 参数 `aspect`（enum，默认 `all`）：
  - `storage`：三系统边界表（见下）
  - `tools`：关键工具能力边界（memory_search 关键词匹配/search_knowledge 语义/memory_save/mempalace_*）
  - `state`：运行时快照（场景、MCP 装配、知识库统计、记忆分布、embedding 状态）
  - `all`：以上全部
- 返回内容由代码实时生成（单一事实源，不硬编码散落）：

```
### 存储系统（勿混用）
1. 原生记忆(PostgreSQL user_memories): 用户画像/偏好/事实/纠正, 轻量文本。
   读 memory_search(关键词匹配, 须用原文措辞); 写 memory_save。
2. 场景知识库(PostgreSQL kb_documents/kb_chunks, 向量语义检索): 长文档 RAG。
   读 search_knowledge(措辞不同也能命中); 写仅用户前端上传, 你无写工具。
3. 记忆宫殿(mempalace, 独立 ChromaDB 向量库, 外部 MCP): 知识抽屉/知识图谱。
   读 mempalace_search/get_drawer; 写 mempalace_add_drawer 等。非 PostgreSQL, 与前两者无共享数据。

### 工具能力边界
- memory_search: 关键词子串匹配, 非语义。多词按空格分词, 任一命中; 查不到≠没写入, 先换原文措辞。
- search_knowledge: 向量+关键词+reranker 语义检索, 仅读。
- memory_save: 写原生记忆(scope: global/office/data_analysis/frontend_design)。

### 操作渠道
- 能做: 原生记忆读写 / 知识库检索 / 记忆宫殿读写 / 文件 / 代码 / 网页搜索。
- 不能做: 知识库写入(仅用户前端) / 模型与工具装配(仅用户设置页) / 打包部署(仅用户手动)。

### 运行时状态
场景: frontend_design | MCP: mempalace(36) Searchpin(2) ... | 知识库: frontend_design 2文档/69片段
原生记忆: 活跃 2 | embedding: bge-small-zh-v1.5 可用(512维)
```

数据源全部现成：`KnowledgeBaseRepo.get_stats()`、`MemoriesRepo.memory_stats()`、config MCP 装配、`EmbeddingService` 状态——与 L2 快照同源，只是从"启动注入"改为"按需查询返回"。

## 四、会话缓存机制

- **天然缓存**：模型查到一次，结果即写入 messages 上下文，后续轮次自动可见复用，无需额外机制。
- **压缩后丢失**：PA 上下文压缩（滑动窗口+摘要）可能把早期查询结果摘要掉。处理：允许模型再次调用（工具幂等、便宜），不为此增加复杂保留逻辑。

## 五、实施计划（已完成）

| 步 | 内容 | 交付 | 状态 |
|---|---|---|---|
| 1 | memory_search 分词 OR + 候选集扩大 | memory_search.py | ✅ 7 新测试 |
| 2 | 能力说明书生成器 `core/capability_map.py` | 1 新文件 | ✅ |
| 3 | `system_capabilities` ToolDef + 注册内核 + 白名单豁免 | system_capabilities.py + __init__.py + main.py | ✅ |
| 4 | 工具描述锚点（已含） + system_prompt 尾句（**跳过**，避免改 Frozen 触发 hash mismatch，且省 token） | __init__.py | ✅ |
| 5 | 文档收尾 + 回归 | 本计划文档 | ✅ 6 新测试 + 全量回归 |

**落地文件**：
- `backend/private_agent/core/capability_map.py`（新增，单一事实源）
- `backend/private_agent/tools/builtins/system_capabilities.py`（新增）
- `backend/private_agent/tools/builtins/__init__.py`（注册 + 内核标记）
- `backend/private_agent/main.py`（`_ALWAYS_AVAILABLE_TOOLS` 加 `system_capabilities`）
- `backend/tests/test_system_capabilities.py`（新增 6 用例）
- `backend/tests/test_tools_lifecycle.py` / `test_kernel_downgrade.py`（计数断言 12→13、8 内核→9 内核）

## 六、验收标准

1. `system_capabilities` 在场景会话工具清单中始终可见（内核，不被裁剪）。
2. 调用 `aspect="all"` 返回三系统边界、工具边界、操作渠道、运行时快照；快照数据与 DB 实查一致。
3. 调用 `aspect="storage"` / `"tools"` / `"state"` 分别返回对应子集。
4. 纯对话轮次无能力地图注入（回归验证 Stable Zone 启动不含地图全文）。
5. 现有测试基线不破坏（不修改 Frozen Zone 内容，frozen hash 不受影响）。

## 七、风险与边界

- **触发不完整**：模型仍可能"自信不查"。已用"描述锚点 + 内核可见 + prompt 尾句"三重缓解；彻底解决需模型侧配合，属可接受 trade-off。
- **工具 schema 成本**：`system_capabilities` schema ~250 token/轮，是"按需查全文"的固定代价；较注入全文（~800-1100/轮）仍净省。
- **内容漂移**：能力说明书随版本维护，建议附"最后更新版本"，随 skill version 发布。
- **Frozen hash**：本方案不修改 Frozen Zone，旧会话零影响。

## 八、落地结论（原"待确认"已定）

| 决策点 | 结论 |
|---|---|
| 落地方式 | **按需自省工具 `system_capabilities`（内核）**，放弃启动注入与周期强调 |
| 触发保险 | 工具描述锚点（已做）；system_prompt 尾句跳过（避免 Frozen hash + 省 token） |
| 运行时快照 | 并入 `aspect="state"` 按需查，不做启动注入 |
