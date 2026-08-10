# 0.5.1 阶段计划（v3）：embedding Worker 装配 —— KB 向量检索真实生效

> 版本归属：**0.5.1**（0.5.0 之后独立版本，不并入 0.5.0 打包）
> 依据：`docs/phase-closeout-0.5.0-2026-08-08.md` 未完成工作 #2
> 日期：2026-08-09
> v2 变更：吸收蒋先生风险评审（四层拆解），补充强制防护机制、回滚预案、打包兼容性、并发验收、技术债务与中长期规划
> **v3 变更（实施中定案）**：模型维度 **512**（以 ModelScope 官方 `BAAI/bge-small-zh-v1.5` 为准，hidden_size=512，蒋先生 2026-08-09 确认）；权重来源 = ModelScope 官方（D 盘 kb-model-cache/modelscope）；实测隐藏缺陷（insert_chunk 硬编码全 0 / knowledge_upload NameError）；安全删除事件误删 asyncpg/apscheduler 包文件已重装
> 方案确认：B2 padding 不动列 + bge-small-zh-v1.5（512 维）+ reranker 暂缓 + 顺手修 master key 基线缺陷

---

## 一、目标与验收标准

### 1.1 目标

让 KB 向量检索**端到端真实生效**：语料入库生成真实 `bge-small-zh-v1.5` 向量（**512 维** → padding 至 1024 存储），admin 上传、场景对话 auto_retrieve、search_knowledge 工具三条真实链路全部使用真实向量；在 **7.6GB 内存**本机上保持可用，且具备**程序强制的防脏库、防 OOM、可回滚**防护。

### 1.2 验收标准（★ = 已实测通过 2026-08-09）

| # | 验收项 | 量化标准 |
|---|---|---|
| V1★ | 入库向量真实性 | 重灌后 85 chunks 全部非全 0；前 **512** 维非零、后 512 维严格为 0；总长 1024（实测：dim=1024、front512_nonzero=512/511、back512=0） |
| V2★ | 检索真实链路 | admin upload 上传 → 纯语义查询命中该文档第 1 名（无关键词重叠，证明向量真实参与）；search_test 检索命中相关 chunk；无 zero-vector 降级日志 |
| V3 | 测试基线 | 后端全量 pytest 通过（master key 3 个基线失败已修复转绿：test_admin_database 11 passed；`test_handler_no_config` 全量顺序残留待观察） |
| V4★ | 维度校验 | 非法维度向量拒绝入库（`_validate_and_pad` 单测覆盖 512→1024 padding / 错误维拒绝） |
| V5★ | 启动自检 | 启动日志实测 "KB embedding consistency verified"（padding-512 + small 匹配）；单测覆盖混存/不匹配抛错 |
| V6★ | 并发与内存 | 3 并发检索全部 200（总 5.27s）、**embedding worker 进程恒为 1**（psutil 实测 spawn worker 仅 1 个）、无 OOM |
| V7★ | 降级链 | kill worker 进程后检索仍 200/1.2s/2 条结果（keyword-only 降级链实测生效） |
| V8 | 打包 | 不执行（蒋先生手动 `build-electron.bat`）；打包后验证 KB 检索可用 + 记录包体积增量 |

---

## 二、现状事实（2026-08-09 实测）

### 2.1 三项缺口（收尾报告确认）

| 缺口 | 实测 |
|---|---|
| ① 装配 | **7 处**生产装配点全部默认构造：`admin.py` L315/556/606/687、`context_manager.py` L313、`tools/builtins/search_knowledge.py` L47、`scripts/ingest_m2_kb.py` L47 |
| ② 依赖 | `import FlagEmbedding` → ModuleNotFoundError；**venv 仅 43 包，torch 也未装**（另注意：venv 无 psutil，但 P1 metrics_collector 引用它——实施时顺带查清其降级路径） |
| ③ 权重 | `bge-small-zh-v1.5` **已完整缓存在本地** `D:\Private agent\models--BAAI--bge-small-zh-v1.5\`（HF 缓存格式，snapshots/main 齐全，约 130MB）；bge-m3 未下载 |

### 2.2 新发现约束

1. **DB 维度硬约束**：`kb_chunks.embedding` = `vector(1024) NOT NULL` + HNSW cosine 索引（schema.sql L250-259）。实测 3 文档 / 85 chunks **全部 mock 全 0**（存储文本 `"[0,0,...]"` 恰 2049 字符）。
2. **内存硬约束**：7.6GB 总内存（实测可用 1.9GB）。bge-m3 fp16 ≈2.3GB/进程不可行；确定 bge-small（≈0.3GB/进程）。
3. **模型类 bug**：`_embed_worker_fn` 硬编码 `BGEM3FlagModel`（L219）；bge-small 为 BERT 架构必须 `FlagModel` → 按模型名分派。
4. **reranker 未装配**（kb_service.py L47，score 恒 1.0）；`select_model_by_memory()` 死代码；`_cloud_embed` mock 且 `fallback_cloud: ""`。

---

## 三、方案决策（已确认）

| 决策项 | 结论 | 理由 |
|---|---|---|
| 模型 | **bge-small-zh-v1.5（512 维）** | 权重已就绪、内存友好、7.6GB 机器唯一稳妥本地方案 |
| DB 维度 | **B2：padding 不动列** | 不动 schema、未来升 m3 平滑；等价性边界见 §4.1 |
| reranker | **暂缓** | 内存与权重成本，留待下阶段（见 §八） |
| 收尾 | **修 master key user_env 缺陷** | 消除测试基线 3 个长期带红 |

---

## 四、详细设计

### 4.1 padding 等价性边界（蒋注意点 → 设计约束）

**逐对 cosine 严格相等（数学证明）**：对任意 512 维向量 a、b，pad 至 1024 后 `pad(a)·pad(b) = a·b`（补零不贡献点积）、`|pad(a)| = |a|`，故 `cosine(pad(a), pad(b)) ≡ cosine(a, b)`。

**检索链路整体行为不完全等价（必须约束）**：
1. HNSW 在 1024 维空间构建图，近似召回路径与原生 512 维有差异——**但属近似算法固有随机性，非系统性失真**；两两距离相等 ⇒ 暴力精确召回完全一致。当前 85 chunks 远小于 `ef_search=64` 生效规模，HNSW 退化为近似全图遍历，实际损失可忽略；对照验证以**暴力余弦为 ground truth**（见 §七技术债务 D1）。
2. **混存硬约束**：混入真实 1024 维向量（换 m3 未重灌）则 cosine 完全失真 → **同一时刻单一模型，切换必须全量重灌**，并由 §4.4 启动自检程序强制（不靠人为纪律）。
3. **存储/索引冗余**：512 维补零占用 1024 维空间，当前 85 chunks 冗余 ≈340KB 无实际影响；语料 ≥10 万 chunks 时触发独立字段迁移（见 §八 M1）。

**设计约束**：
- **C1 维度校验**：`EmbeddingService` 输出校验（有效非零维==512 且 存储维==1024，不符抛 `EmbeddingError`）+ `process_document` 写入前校验（拒绝非法向量入库，不静默降级）；读取路径补长度检测防御脏数据。
- **C2 冗余接受**：文档 + 代码注释明示，迁移阈值见 §八。
- **C3 单一模型纪律**：config 显式固定 small；`select_model_by_memory()` 废弃。

### 4.2 依赖层（Step 0-1）

1. **Step 0 前置验证（先于一切代码改动）**：验证 `FlagModel` 能离线加载本地缓存（`HF_HUB_CACHE=D:/Private agent` 与实测目录结构吻合）；失败则先处理权重路径。
2. **安装**：`pip install FlagEmbedding` + **CPU 版 torch**（清华镜像，torch 约 200MB，避免 CUDA 版 2.4GB）+ psutil（小包，供内存水位检查）；安装前 `export NO_PROXY=*`（本机代理陷阱）。

### 4.3 代码层（Step 2）

**模型路径解析（评审整改：不单纯依赖环境变量）**：`_resolve_model_path()` 四级回退——
① config `kb.embedding.model_path` 显式指定 → ② `HF_HUB_CACHE` 环境变量 → ③ **代码自动探测项目根 `models--BAAI--bge-small-zh-v1.5`**（相对 backend 的 `../`，Electron 打包/脚本后台运行环境变量丢失时的兜底）→ ④ HF 默认缓存 `~/.cache/huggingface/hub`。全部失败 → mock 降级 + 明确错误日志（走 V7 降级链）。

**`embedding_service.py`**：
- `_embed_worker_fn` 模型类分派（含 "m3" → `BGEM3FlagModel`，否则 → `FlagModel`）；
- `MODEL_DIM=512` / `STORAGE_DIM=1024` 常量；`_embed_texts` 输出统一 padding + C1 校验；
- **worker 故障容错**：`FlagEmbedding` 导入失败 / 模型加载失败 / 推理异常 → 返回全 0 mock + ERROR 日志（**不抛异常阻断入库**，由 §4.4 自检与 V7 降级链兜住）；
- `__init__` 固定 config 模型；`select_model_by_memory()` 废弃（C3）；
- **监控日志**：每次 embed 记录 `model_dim/storage_dim/耗时/worker_pid/padding 标记`（评审整改：日志监控）。

**专用单 worker 池（评审整改：强制 max_workers=1）**：
- 新增模块级 `_embedding_pool: ProcessPoolExecutor(max_workers=1)` 单例，**不复用** `executor.get_pool()`（2-worker 全局池，避免蓝图约定被改、也避免与其他用途互相影响）；
- 内存上限 ≈ 主进程 + 1×0.3GB；worker 内加载模型前 psutil 检查可用内存 < 1GB → 拒绝加载并降级（水位告警日志）。

**`kb_service.py`**：process_document C1 写入前校验；`_get_embedding_model/_get_vector_dim` 适配 model_dim/storage_dim 双语义。

**装配统一**：新增 `knowledge/factory.py`：
```python
def build_embedding_service(cfg) -> EmbeddingService   # 专用单 worker 池 + config + 路径解析
def build_kb_service(conn, cfg) -> KnowledgeBaseService # 注入 embedding_service
```
替换 7 处生产装配点（admin×4 / context_manager×1 / search_knowledge×1 / ingest×1）。

**`config.yaml`**：`local_default: BAAI/bge-small-zh-v1.5`（固定）；新增 `storage_dim: 1024`、`model_path: ""`（空则自动探测）注释说明。

### 4.4 启动自检（评审整改：程序强制防混存）

`factory.verify_embedding_consistency(conn, cfg)`，FastAPI startup 时执行一次：
- 空库 → 跳过；
- 抽样 5 条 chunks：解析向量后段 512 维是否全 0 → 判定库内向量来源（padding-small / 真实-1024 / 混存）；
- **不匹配或混存 → KB 模块 fail-fast**：KB 相关端点与检索全部返回明确错误「向量库与当前模型不匹配，需重灌」（**应用其余功能正常**，避免整个桌面应用启动失败）；`PA_KB_STRICT=1` 时可升级为阻断整个进程；
- 匹配 → 正常启动，日志记录「向量库一致性校验通过（padding-512/1024）」。

> 待蒋先生确认：默认「KB 模块 fail-fast + 应用可用」还是「直接阻断进程」？我倾向前者（用户可见其余功能，错误信息明确），严格模式留配置开关。

### 4.5 测试隔离（评审整改：全局开关防测试内存溢出）

- 环境变量 **`PA_EMBEDDING_MOCK=1`** → factory 强制 `worker_pool=None`（mock 全 0 分支），任何测试误触装配也不会加载真实模型；
- `tests/conftest.py` 统一 `os.environ.setdefault("PA_EMBEDDING_MOCK", "1")`；
- 新增 `test_embedding_assembly.py`：断言 factory 注入 worker_pool/config、C1 校验拦截非法维度、路径解析四级回退（全部 mock 进程池，不加载真实模型）；
- 现有 mock 测试保持绿；pytest 全程不加载真实模型。

### 4.6 数据层（Step 3）

- 本期：85 chunks 全系 mock 全 0 无检索价值，清表重灌（`scripts/ingest_m2_kb.py`，3 篇源文档在本地 md，秒级）；
- **未来真实业务文档不做暴力清表**：走已有 `update_document`（deactivate + 重处理）逐文档增量重灌（技术债务 D2 落实方案）；
- 入库后抽样校验 V1（512 非零 + 512 补零 + 总长 1024）。

### 4.7 验证层（Step 4，真实触发路径）

1. admin upload API 上传临时文档 → 查 DB 向量非零（V1）；
2. 场景对话触发 auto_retrieve → Stable Zone 注入 KB 片段且分数合理（V2）；
3. 真实会话调用 search_knowledge 工具 → 命中相关 chunk（V2）；
4. 并发验收 V6；降级链验收 V7（kill worker 进程）；
5. 验证后清理临时文档。

### 4.8 收尾层（Step 5）

- 修 `_ensure_master_key` 不写 user_env 缺陷（admin.py，3 个基线失败转绿）；
- 定位 `test_handler_no_config` 全量顺序残留；
- 顺带查清 metrics_collector 的 psutil 引用在无 psutil venv 下的实际行为。

### 4.9 回滚预案（评审整改）

| 层 | 回滚动作 |
|---|---|
| 代码 | `git revert` 本阶段全部 commit（独立提交链，一次 revert 还原 factory/装配/校验） |
| 依赖 | `pip uninstall FlagEmbedding torch transformers safetensors psutil`（不卸载也无害：mock 分支不 import） |
| 数据 | 无需特殊操作——3 篇源文档在本地 md，重跑 ingest 即可恢复任意状态 |
| 验证 | 回滚后全量 pytest 通过 + KB 检索回到 mock 全 0 → keyword-only 降级（即 0.5.0 现状） |

### 4.10 打包兼容性与体积（评审整改）

- 新增体积预估：torch CPU ≈200MB + FlagEmbedding/transformers 依赖 + 模型权重 130MB ⇒ 安装包预计 **+350~400MB**；
- 打包后验证项（蒋先生手动打包后）：① KB 检索可用（torch dll 正常加载）② worker spawn 正常（Windows spawn 无路径编码问题）③ 记录实际包体积增量；
- 模型权重**不进安装包**（本机已有，gitignore 已忽略 models--*）；权重分发策略（随包/首次启动下载）记入中长期规划。

---

## 五、装配点清单（7 处 → factory）

| 位置 | 行号 | 用途 |
|---|---|---|
| `api/admin.py` | L315 / L556 / L606 / L687 | KB 上传/检索 admin 端点 |
| `core/context_manager.py` | L313 | 场景 auto_retrieve |
| `tools/builtins/search_knowledge.py` | L47 | 对话检索工具（Agentic RAG） |
| `scripts/ingest_m2_kb.py` | L47 | 语料入库脚本 |

---

## 六、风险与对策（整合评审四层）

| 风险 | 对策 | 落实 |
|---|---|---|
| 混存脏库致检索完全失真 | 启动自检程序强制（§4.4）+ C3 固定模型 | 本期 |
| 环境变量丢失致模型找不到 | `_resolve_model_path` 代码层四级回退（§4.3） | 本期 |
| 多 worker 内存叠加 OOM | 强制单 worker 池 + psutil 水位检查（§4.3） | 本期 |
| 测试误加载真实模型 | `PA_EMBEDDING_MOCK=1` 全局开关 + conftest（§4.5） | 本期 |
| 上线故障无法恢复 | 回滚预案（§4.9） | 本期 |
| torch/打包兼容问题 | 打包验证项 V8 + 体积预估（§4.10） | 本期（手动打包后验证） |
| Windows spawn 首调慢（数秒） | 惰性加载 + 单 worker 常驻，可接受 | 本期说明 |
| HNSW padding 召回差异 | 当前数据量可忽略；大数据集对照用例（D1） | 技术债务 |
| 本地 worker 全链路故障 | 降级链 keyword-only（V7）；云端真实 API 兜底（D3） | 本期 + 技术债务 |

---

## 七、技术债务（本期妥协，下一版本迭代）

- **D1 HNSW 对照用例**：以暴力余弦为 ground truth 校验 HNSW 召回一致性；语料量增长后建立 ef_search 调参基准。
- **D2 增量重灌方案**：真实业务文档入库后，重灌走 `update_document` 逐文档更新，禁止暴力清表；补充分区/隔离方案。
- **D3 云端 embedding 真实兜底**：本地 worker 不可用时切云端 API（需 provider 配置 + 维度对齐）；当前降级链（keyword-only）保证可用但召回质量下降。
- **D4 监控增强**：向量维度/推理耗时/worker 内存指标接入 P1 metrics_collector（system_metrics 表）。

## 八、中长期架构规划（0.6+）

- **M1 dense_vector_512 独立字段迁移**：语料 ≥10 万 chunks 触发；新增 512 维字段 + 灰度迁移 + 全量重灌，消除 padding 存储/索引冗余。
- **M2 reranker 分阶段落地**：bge-reranker-v2-m3（~1.1GB 权重），先单 worker 实验性接入，评估内存后定正式版本；当前 score 恒 1.0 是 RAG 精度天花板。
- **M3 sparse/混合检索预留**：db 层预留 sparsevec 字段与检索融合接口，降低未来切 bge-m3（dense+sparse 混合）的重构成本。
- **M4 硬件扩容后升级路径**：更换高内存机器 → 切 bge-m3 → 全量向量重灌 → 恢复 `local_default` 为 m3 + 混合检索。

---

## 九、版本与打包

- 版本号：**0.5.1**（frontend/package.json + package-lock.json）
- 打包：**不执行**，蒋先生手动 `build-electron.bat`；打包后按 V8 验证
- git：实施完成、测试绿后提交独立 commit 链（保障 §4.9 可 revert）

---

*v2 修订完成。实施按 Step 0 → 5 顺序推进，每步验证通过再进入下一步；§4.4 自检失败默认策略待蒋先生最终确认。*

---

## 十、实施记录与隐藏缺陷（2026-08-09 实测）

### 10.1 依赖安装与安全删除事件（重要教训）

- venv 原有 **asyncpg / apscheduler 包文件被 WorkBuddy safe-delete shim 误删**（目录空壳，10:50 事件，与 `pip uninstall setuptools` 的删除保护冲突同批）→ 重装恢复。**教训：安装依赖务必 `unset PYTHONPATH`**（shim 经 PYTHONPATH 注入 sitecustomize 拦截删除，清空后 pip 正常卸载/升级）。
- setuptools 升级被 shim 拦截 → 先卸载旧 setuptools（unset PYTHONPATH 下）→ 再装 FlagEmbedding（无卸载冲突）。
- torch 用官方 CPU 源 `download.pytorch.org/whl/cpu`（121.9MB）；其余依赖清华 PyPI；**PIP_CACHE_DIR 重定向 D 盘**（蒋先生要求依赖不落 C 盘）。

### 10.2 权重来源定案（512 维）

- 初查 HF 缓存 `models--BAAI--bge-small-zh-v1.5`（项目根）与 ModelScope 第三方镜像 `AI-ModelScope/bge-small-zh-v1.5` 均为 hidden_size=512/4 层（疑似 v1 错标）。
- **最终定案**：ModelScope 官方 `BAAI/bge-small-zh-v1.5`（hidden_size=512）下载至 `D:/Private agent\kb-model-cache\modelscope\models\BAAI--bge-small-zh-v1.5\snapshots\master`（魔搭 CDN 30s 完成），**蒋先生裁决 512 维正确**。
- config `model_path` 显式指向该目录（直接模型目录语义）。

### 10.3 隐藏缺陷（超出收尾报告范围，一并修复）

1. **insert_chunk 硬编码全 0**：`kb_repo.insert_chunk` 的 SQL 恒写 `array_fill(0.0, ARRAY[1024])::vector`，`chunk.embedding` 从未落库 —— 即使 worker 装配成功向量也不入库。已改为 `$6::vector` 绑定真实向量（bytes → pgvector 文本）。
2. **knowledge_upload NameError**：admin upload 端点引用未定义的 `cfg`（`_build_kb_processor(cfg)`）→ 上传恒 500。装配改造时顺带修复（补 `cfg = await _load_cfg()`）。
3. **内存水位过严**：初设 1.0GB，本机可用常 <1GB 导致拒绝加载 → 下调至 0.5GB（fp16 512 维 BERT 驻留 ~0.2GB + torch 余量）。

### 10.4 验收实测摘要

- 启动自检：`KB embedding consistency verified` ✓
- V1：85 chunks 全部真实（front512 非零、back512 全 0、总长 1024）✓
- V2：上传临时文档后纯语义查询命中第 1 名（无关键词重叠）→ 向量语义检索真实生效 ✓
- V6：3 并发 200 / 总 5.27s / worker 恒 1 / 无 OOM ✓
- V7：kill worker → 检索仍可用（keyword-only 降级）✓
- 知识模块测试：**51 passed**（含新增 test_embedding_assembly 17 项）
- test_admin_database：**11 passed**（master key 3 基线失败转绿）
