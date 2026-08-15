# PA 记忆 / 知识库 / 数据存储系统诊断报告

> 日期：2026-08-13 | 排查人：WorkBuddy（主代理直接执行，无子代理）
> 证据来源：DB 实查（private_agent 库）+ 后端代码阅读 + 前端代码阅读 + 真实调用记录（messages 表）+ 子串匹配复现验证
> 结论均标注【已验证】（DB/代码/复现证据）或【推测】（无直接证据）

---

## 一、结论先行（TL;DR）

1. **三套系统是三个不同的东西，只有两套在 PA 自己的 PG 里**：
   - **A. 原生记忆**（PG `user_memories` 表）＝ 对话中自动提取/主动写入的"轻记忆"（身份、偏好、事实、纠正）。
   - **B. 场景知识库**（PG `kb_documents`/`kb_chunks` 表 + vector(1024) 列 + HNSW）＝ 长文档 RAG（分块 + bge 向量 + reranker）。
   - **C. 记忆宫殿 mempalace**（**本地 ChromaDB，D:\mempalace\palace\chroma.sqlite3**，不是 PG）＝ 外部 MCP 独立服务，与 PA 后端零代码耦合。
   - **清和在诊断报告里把 C 误判为"PG 通道"——这是错的**。C 的后端是 ChromaDB（SQLite 向量库），只是恰好配了 `palace_path=D:/mempalace/palace` 在 D 盘。

2. **"记忆能写不能读"的根因【已验证，非推测】：`memory_search` 的匹配逻辑是"整个 query 当连续子串"，对带空格的中文多词查询必然失败**。
   - DB 铁证：id=5 已落库、`is_active=TRUE`、content 完整、scope=frontend_design。
   - 真实调用记录（messages 表，session 91713）：清和 3 次查询 query 分别为
     `'健康 生活 饮食 作息 锻炼'`、`'健康生活要点 餐盘法 睡眠 运动 PG写入测试'`、`'健康生活四大支柱 餐盘法 蔬果全谷 运动睡眠'`（均带空格、多词组合、且带 scope=frontend_design）。
   - 代码事实：`memory_search.py` 第 59 行 `hits = [m for m in memories if query.lower() in (m.content or "").lower()]` —— 把含空格的整串 query 作为连续子串匹配，content 里不可能出现"健康生活要点 餐盘法 睡眠 运动 PG写入测试"这种连续串 → 必然 0 命中。
   - 复现验证：对 id=5 的 content，清和使用的 4 个查询词子串命中全部 False；而 content 中的原词（"1/2蔬果"、"每周≥150分钟"）子串命中全部 True。**数据没问题，检索逻辑有问题**。
   - 清和推测的三个原因（写入不同步/索引未刷新/会话隔离）**全部不成立**。

3. **"知识库能读不能写"是设计使然，不是 bug【已验证】**：
   - LLM 侧只有 `search_knowledge`（只读），无写工具。
   - 写入通道存在但不在 LLM 工具里：Admin API `POST /admin/knowledge/upload`、`POST /admin/knowledge/upload-file` + 前端「知识库管理」页（KnowledgeView.tsx，文件上传 + 文本上传 + 删除 + 切片配置）。
   - 清和报告里"场景知识库设计上不支持实时 API 写入"的判断**正确**；但"数据靠 D:\health-wiki 文件经 mempalace_mine 批量导入"这句**表述有误**——`mempalace_mine` 导入的是**记忆宫殿（ChromaDB）**，不是 PA 场景知识库。两者写入通道完全不同。

4. **"记忆宫殿能写能读"正常的原因【已验证】**：C 是独立系统，`mempalace_add_drawer → mempalace_get_drawer` 是它自己完整的写读闭环，且是 ChromaDB 语义向量检索，不存在"写读链路分叉"问题。清和拿它和 A 对比得出"PG 没问题"的结论是错的——因为 C 根本不走 PG。

---

## 二、三套系统边界表

| 维度 | A. 原生记忆 | B. 场景知识库 | C. 记忆宫殿 mempalace |
|---|---|---|---|
| 物理存储 | PG `private_agent` 库 `user_memories`（+`user_memories_archive`/`user_profile`） | PG `private_agent` 库 `kb_documents`/`kb_chunks`，`embedding vector(1024)` + HNSW | **ChromaDB**（D:\mempalace\palace\chroma.sqlite3）+ 知识图谱 SQLite；非 PG |
| 本质 | 用户轻记忆：preference/fact/todo/decision/correction | 长文档 RAG：文档→chunk→向量 | 外部 MCP 记忆宫殿：wing/room/drawer 层级 + 语义检索 + 隧道 + 知识图谱 |
| LLM 可见工具 | `memory_save`（写，0.5.1 新增）、`memory_search`（读） | `search_knowledge`（只读） | `mempalace_add_drawer`/`get_drawer`/`search`/`kg_*` 等 36 个（读写齐全） |
| 写入通道 | LLM 工具 + 后台自动提取（每 8 轮）+ 用户纠正沉淀 | 仅 Admin API/前端上传（upload/upload-file）；**LLM 无写工具** | mempalace 自带 MCP 写工具 |
| 检索机制 | **纯子串匹配（query in content），无分词、无语义【缺陷】** | 向量 + 关键词**分词 OR** + RRF 融合 + reranker | ChromaDB 语义向量检索 |
| 生命周期 | Stable Zone 注入 + 淘汰 + 归档 + 画像聚合 | 文档级增删/重索引/快照 | 抽屉级写/读/隧道连接 |

**混淆根源**：
1. A、B 共用一个 PG 库（不同表），C 完全独立（ChromaDB）——但 LLM 感知不到底层，清和甚至把 C 误判为 PG。
2. 三套系统工具描述边界不清：`memory_save` 自称"PostgreSQL user_memories"，`search_knowledge` 叫"knowledge base"，mempalace 叫"memory palace"，场景智能体无法从名字区分。
3. 用户视角入口分散：前端有「知识库管理」页（B 的写入），场景智能体侧同时挂着 A/B/C 三套工具，容易混。

---

## 三、现象一：记忆"能写不能读"——证据链与根因

### 3.1 DB 证据（id=5 确实存在且有效）

```sql
SELECT id, type, left(content,50), importance, scope, is_active, access_count
FROM user_memories ORDER BY id;
-- id=5 | fact | 【健康生活要点·清和整理】膳食:每餐1/2蔬果+1/4全谷... | 0.6 | frontend_design | t | 0
-- 全表仅 4 行：id1/2 已 inactive，id4/5 活跃 → 不存在"top15 截断"问题
```

### 3.2 真实调用记录（messages 表，session 91713，清和 frontend_design 场景）

| 时间 | 动作 | 参数/结果 |
|---|---|---|
| 11:36:40 | memory_search | `query='健康 生活 饮食 作息 锻炼' (scope=frontend_design)` → No memories found |
| 11:38:53 | memory_save | 写入 id=5（scope=frontend_design，importance=0.6）→ 成功 |
| 11:38:53 | mempalace_add_drawer | `drawer_health_lifestyle_lifestyle-summary_8f7d2f...` → 成功 |
| 11:40:16 | memory_search | `query='健康生活要点 餐盘法 睡眠 运动 PG写入测试' (scope=frontend_design)` → No memories found |
| 11:40:42 | memory_search | `query='健康生活四大支柱 餐盘法 蔬果全谷 运动睡眠' (scope=frontend_design)` → No memories found |

### 3.3 根因（【已验证】）

`backend/private_agent/tools/builtins/memory_search.py` 第 54-59 行：

```python
memories = await repo.get_top_active(limit=top_k * 3, scope=scope)  # importance 前 15
hits = [m for m in memories if query.lower() in (m.content or "").lower()]  # ← 整串子串匹配
```

- 检索不是 SQL ILIKE、不是分词、不是向量，而是 **Python 里把含空格的整个 query 当连续子串** 与 content 比对。
- 中文多词查询（"健康生活要点 餐盘法 睡眠 运动 PG写入测试"）在 content 中**不可能**以连续子串出现 → 必然 0 命中。
- 复现验证（对 id=5 content）：

```
query="健康生活四大支柱" -> False      query="健康生活要点" -> True
query="餐盘法"            -> False      query="1/2蔬果"       -> True
query="蔬果全谷"          -> False      query="每周≥150分钟"   -> True
query="运动睡眠"          -> False
```

**对比**：知识库的 keyword_search（kb_repo.py 第 498-500 行）早已实现"按空白分词 + OR 匹配"：

```python
tokens = [t for t in query.split() if t.strip()]
conditions.append(f"chunk_text ILIKE ${len(params)+1}")   # 逐词 OR
```

**记忆侧缺了这个分词逻辑**，是明显的实现不对称缺陷。

### 3.4 附加隐患（【推测】，结构性）

`get_top_active(limit=top_k*3)` 只取 importance 最高的 15 条再过滤。当前活跃记忆仅 2 条所以没触发；**一旦活跃记忆超过 15 条，新写入的低 importance 记忆即使内容完全匹配也会被截断漏检**。这是比本次 bug 更深的隐患。

---

## 四、现象二：知识库"能读不能写"——设计使然

- `search_knowledge.py`：只读检索（混合检索 + reranker），无任何写入工具。
- 写入通道（admin.py 387-426 行 + 前端 KnowledgeView.tsx）：
  - `POST /admin/knowledge/upload`（文本上传）
  - `POST /admin/knowledge/upload-file`（文件上传，≤10MB）
  - 前端「知识库管理」页提供 UI。
- **结论**：对 LLM 而言"不能写"是**当前设计定位**（知识库=文档级批量导入，非对话级单条写入）；对用户而言**能写**（前端上传）。若要让场景智能体直接写入，需新增 LLM 写工具（如 `knowledge_save`），属功能演进而非 bug 修复。
- 现状快照：frontend_design 仅 2 文档/69 chunks（FlowSpace 设计系统 + 08-09 内置健康语料）；**D:\health-wiki（08-12 建立）尚未导入 PA 场景知识库**，清和测试用的健康知识来自记忆宫殿。

---

## 五、现象三：记忆宫殿为何"写→读完美"

- 清和报告称"mempalace_add_drawer (PG)"——**误判**。
- mempalace 默认后端 `chroma`（config.py `DEFAULT_BACKEND="chroma"`），本机 config.json 仅配置 `palace_path: D:/mempalace/palace`，目录内为 `chroma.sqlite3` + `knowledge_graph.sqlite3`（5.8MB）。
- 它是外部 MCP 独立进程（venv D:\skills\mempalace-develop），与 PA 后端无共享代码/无共享表，add_drawer → get_drawer 是它自己的完整闭环 + ChromaDB 语义检索 → 写入立即可查。
- 这也解释了为什么拿它对比原生记忆会得出"PG 没问题"的错误结论——两条链路完全独立。

---

## 六、修复建议（按优先级）

| 优先级 | 动作 | 说明 | 工作量 |
|---|---|---|---|
| P0 | `memory_search` 增加空格分词 OR 匹配（对齐 kb keyword_search） | 一行核心改动：`tokens=[t for t in query.split() if t.strip()]; hits=[m for m in memories if any(t in m.content for t in tokens)]`。立刻消除"写进读不出" | 极小 |
| P0 | 可选：query 无空格时保留整串匹配（兼容单词查询） | 与 kb 行为一致 | 极小 |
| P1 | `get_top_active` 候选集扩大或全表扫 | 消除">15 条后新记忆永远查不到"隐患 | 小 |
| P2 | 记忆加向量列（复用 bge-small-zh-v1.5 + pgvector）或复用 kb 检索 | 语义检索兜底中文措辞差异；与 0.5.1 KB embedding Worker 同栈 | 中 |
| P2 | 工具描述/系统提示明确三系统边界 | 让场景智能体不再误判存储后端（如清和把 ChromaDB 当 PG） | 小 |
| P3 | 可选：新增 LLM 可用的知识库写入工具 | 让场景智能体可实时写 KB（当前走前端上传） | 中 |

---

## 七、取证明细（可复核）

- DB：`D:\PostgreSQL\16\bin\psql.exe`，库 `private_agent`，表 `user_memories`/`kb_documents`/`kb_chunks`/`messages`
- 代码：`backend/private_agent/tools/builtins/memory_save.py`、`memory_search.py`、`search_knowledge.py`；`backend/private_agent/memory/memories_repo.py`、`manager.py`；`backend/private_agent/knowledge/kb_repo.py`、`kb_service.py`；`backend/private_agent/api/admin.py`（360-426 行）
- 前端：`frontend/renderer/views/KnowledgeView.tsx`（上传/文件/删除/切片配置）
- mempalace：`D:\skills\mempalace-develop\mempalace-develop\mempalace\config.py`（默认后端 chroma）；本机 `~/.mempalace/config.json`（palace_path=D:/mempalace/palace）
