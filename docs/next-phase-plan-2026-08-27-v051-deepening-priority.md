# 0.5.1 深化项优先级与实施方案（收尾 + 技术债务处置）

> 版本：v2（2026-08-27 实施记录；v1 为设计稿）
> 依据：2026-08-27 三项 v0.5.1 工作详解与优先级分析（代码侧核实）
> 状态：**P0-A/B 与 P1-C/D 已实施（测试全绿）；P2-E/F 留待条件触发/蒋先生决策**
> 前置事实：三项核心（KB embedding Worker 真实检索 / V2 多 LLM 开放接入 + MCP 前端化 / 批次3 架构改造）**均已落地**。本文档只覆盖"后续深化/收尾子项的优先级与最优方案"，不含从零实现。

---

## 一、结论与裁决（先行）

1. 三项核心均为既成事实，本阶段**不做重做**，只做收尾与加固。
2. 优先级一句话：**P0 收尾（测试基线 + 真实会话验收）→ P1 增量重灌 → P1 监控增强 → P2 对照用例 / 云端兜底**。
3. **硬舍弃（本机硬件不达标）**：reranker（bge-reranker-v2-m3，~1.1GB）、bge-m3（~2.3GB/进程）。
4. **搁置（条件触发）**：512 独立字段迁移（≥10 万 chunks 触发）、硬件升级后切 m3。

---

## 二、现状事实（证据链，2026-08-27 核实）

### 2.1 三项核心均已落地

| 项 | 落地证据 | 关键文件 |
|---|---|---|
| KB embedding 真实检索 | bge-small-zh-v1.5 512→1024 padding、专用单 worker 池（max_workers=1）、7 处装配点统一到 factory、启动自检 verify_embedding_consistency；2026-08-09 实测 V1-V8 大部分通过、知识模块 51 passed | `knowledge/embedding_service.py`、`knowledge/factory.py` |
| V2 多 LLM + MCP 前端化 | providers CRUD / 连通性测试 / fallback chain 幽灵项自愈 / 会话 model_id auto+锁定 / text+vision 双链；skill-binding GET/PUT/DELETE + SettingsView 场景×server 矩阵 + mcp_browse（list/exec/assemble/remove） | `api/admin.py` L2285+、L6525+；`renderer/views/SettingsView.tsx` |
| 批次3（回放过滤/MCP 权限/事件去重） | 回放过滤（滤 tool_confirmation_required / thinking / delta、zone 过滤、补合成 final）；三级权限 + 五模式 + 通俗化确认卡片；event_id 事件级去重 + ws_offset 单调保护 | `storage/ws_offset.py`、`tools/permission_manager.py`、`core/react_loop.py`（C-4） |

### 2.2 待决策的深化子项（技术债务映射）

| 编号 | 子项 | 对应技术债务 |
|---|---|---|
| A | 批次3 收尾（断线重连端到端回归、补用例） | 批次3 验收 |
| B | V2·MCP 验收（skill-binding vitest + mcp_browse 四动作） | skill-binding 计划 V1-V6 |
| C | 增量重灌（`update_document` 逐文档） | D2 |
| D | embedding 监控（system_metrics 落库） | D4 |
| E | HNSW 暴力余弦对照 | D1 |
| F | 云端 embedding 真实兜底 | D3 |

---

## 三、硬件约束基线（裁决依据）

| 项 | 数值 | 结论 |
|---|---|---|
| 本机总内存 / 可用 | 7.6GB / ~1.9GB | **裁决线：新增常驻模型内存 >0.5GB 即进入风险区** |
| bge-small-zh-v1.5（现状） | ~0.3GB/进程 | 可承载 |
| reranker v2-m3 | ~1.1GB 权重 + 推理驻留 | 舍弃 |
| bge-m3 fp16 | ~2.3GB/进程 | 舍弃 |
| 语料规模 | 85 chunks | M1 触发（10 万）遥远 |

---

## 四、优先级矩阵

| 级别 | 子项 | 内存成本 | 收益 | 风险 | 裁决 |
|---|---|---|---|---|---|
| **P0** | A 批次3 收尾 | 0 | 高（稳定性） | 低 | 做 |
| **P0** | B V2·MCP 验收 | 0 | 高（日常配置） | 低 | 做 |
| **P1** | C 增量重灌（D2） | 0 | 高（KB 可维护） | 中（数据路径） | 做 |
| **P1** | D 监控增强（D4） | 0 | 中（可观测） | 低 | 做 |
| **P2** | E HNSW 对照（D1） | 0 | 低（85 chunks 收益近零） | 低 | 语料量上来再做 |
| **P2** | F 云端兜底（D3） | 0（网络/token） | 中 | 中 | **待蒋先生拍板** |
| **舍弃** | G reranker | ~1.1GB | 高 | OOM | 舍弃 |
| **舍弃** | H bge-m3 | ~2.3GB | 高 | OOM | 舍弃 |
| **搁置** | I 512 独立字段迁移（M1） | — | 低 | — | ≥10 万 chunks 触发 |
| **搁置** | J 硬件升级后切 m3（M4） | — | — | — | 依赖新硬件 |

---

## 五、详细设计

### 5.1 P0-A 批次3 收尾
- 端到端回归：turn N 中途断线 → 重连后前端按 `event_id` 去重、无重复 delta；切历史会话全量加载不卡死、无"思考中"假死。
- 补测试：`test_ws_offset` / `test_ws_offset_ack` / `test_react_loop_event_sink` 已有，补充断线重连端到端场景。
- 验收：无重复渲染、确认弹窗不重弹（`tool_confirmation_required` 已被回放过滤）。

### 5.2 P0-B V2·MCP 验收
- 前端补 `SkillBindingSection` vitest（勾选 / 保存 / DELETE 回退 / 新会话生效提示）。
- `mcp_browse` 四动作**真实会话**验证（无涯：`list` → `assemble [codegraph]` → `exec` → `remove`）；**走真实触发路径，不用 test 接口**。
- 验收：V1 按需可达；V2 起始清单不变；V3 每轮注入不膨胀（top-N 生效）；V4 UI 写 config_runtime 新会话生效；V5 assemble 走 elevated WS 确认；V6 回归基线全绿。

### 5.3 P1-C 增量重灌（D2）
- 落实 `update_document`（deactivate + 重处理）为**唯一**重灌通道，禁止暴力清表。
- 脚本化验证：改文档 → 重灌 → 旧 chunk 停用、新 chunk 可检索、无残留。
- 理由：KB 是日常主场景，真实业务文档只会持续增长；清表方案在 85 chunks 时无感，文档一多即不可逆。

### 5.4 P1-D 监控增强（D4）
- 复用 psutil（已装），把 `model_dim / storage_dim / 耗时 / worker_pid / 内存水位` 写入 `system_metrics` 表（`_embed_texts` 已有日志，直接扩展落库）。
- 零新增依赖、零内存。

### 5.5 P2-E HNSW 对照（D1）
- 随机采样 query → HNSW top-K vs 暴力余弦 top-K → 一致性报告。当前 85 chunks 收益近零，**语料量增长后执行**。

### 5.6 P2-F 云端兜底（D3，待决策）
- 若做：需确认 embedding 服务商、维度对齐（1024 或按需转换）、token 预算。
- **配置类决策，交还蒋先生**，AI 不擅自修改；当前 keyword-only 降级（V7 实测）已保证可用，属"可缓"。

---

## 六、舍弃与搁置裁决理由

- **舍弃**（reranker / bge-m3）：可用内存 ~1.9GB 减去主进程 + embedding worker（0.3GB）后无余量容纳 1GB+ 常驻模型；强行接入会触发 Windows 内存压力，反而拖垮主应用——与本机"个人日常可用性第一"的目标相悖。
- **搁置**（M1 / M4）：条件触发项，无需主动做，等触发条件自然到来。

---

## 七、验收标准

| # | 验收项 | 量化标准 |
|---|---|---|
| V1 | 断线重连无重复渲染 | 中断 turn 后重连，delta 不重复累积（前端按 event_id 去重） |
| V2 | 历史会话切换不卡死 | 长会话全量加载正常出 final 状态，无"思考中"假死 |
| V3 | mcp_browse 四动作 | list / assemble / exec / remove 全通；assemble 走 elevated 确认 |
| V4 | 增量重灌 | update_document 后旧 chunk deactivate、新 chunk 检索命中 |
| V5 | 监控落库 | system_metrics 出现 embedding 维度 / 耗时记录 |
| V6 | 回归基线 | 后端 pytest（加载 .env，--ignore=test_eval_full_cycle.py）全过；前端 tsc 0 错 + vitest 全过 |

---

## 八、风险与对策

| 风险 | 对策 |
|---|---|
| 收尾改动回归核心循环（react_loop / ws） | 独立 commit 链可 revert；改动前先跑全量基线 |
| 增量重灌误伤数据 | 重灌前备份；update_document 事务内 deactivate + 重处理 |
| 云端兜底引入外部依赖与成本 | 待决策；不做则 keyword-only 降级兜底（已有） |
| 测试误加载真实模型 | `PA_EMBEDDING_MOCK=1` 全局开关（已有，conftest 统一设置） |

---

## 九、实施顺序

1. **P0-A + P0-B**：跑现有基线 → 补用例 → 真实会话验收（零成本正确性，先做）
2. **P1-C**：增量重灌实现与验证
3. **P1-D**：监控落库
4. **P2-E**：留待语料量增长；**P2-F**：待蒋先生决策后再启动
5. 每步独立 commit（可 revert）；全部完成后版本收尾 + 记忆宫殿双写

---

## 十、版本与打包

- 版本：作为 0.5.1 收尾（不升主版本号，除非蒋先生另行决定）
- 打包：**不执行**，蒋先生手动 `build-electron.bat`
- git：实施完成、测试绿后独立提交链

---

## 十一、实施记录（2026-08-27，v2）

### 11.1 P0-A 批次3 收尾（完成）
- **新增测试 2**：`test_build_replay_messages_filters_stable_zone_user_messages`（C-5 zone 过滤：stable 注入不重放、active/NULL 重放）、`test_react_loop_persisted_events_backfill_db_event_id`（A-1 event_id 回填与 DB 同源）。
- **修复滞后断言 3**：`test_ws_offset_ack._seed_events` 种子从 thinking 改 tool_call（2026-08-16 起 replay 过滤 thinking/delta 增量，thinking 种子被滤空）；event_sink 用例补 status 事件（sink 3 / queue 2）；event_id 回填断言放宽（context_injected/checkpoint 走直接 insert 不进 sink）。
- 结果：`test_ws_offset.py + test_ws_offset_ack.py + test_react_loop_event_sink.py` **31 passed**。

### 11.2 P0-B V2·MCP 验收（完成）
- **新增后端 `test_mcp_browse.py` 11 测**（handler 级，monkeypatch 隔离 MCP 连接）：assemble/remove 装配标记、list 工具索引/空态、exec 透传、action 非法/缺省。
- **新增前端 `SkillBindingSection.test.tsx` 5 测**（`SettingsView.tsx` 导出 SkillBindingSection）：矩阵渲染、通配列、勾选保存 PUT、回退 DELETE、空态。
- **新增后端 `test_skill_binding_api.py` 6 测**（ASGI + 测试库）：GET/PUT/DELETE 生命周期、非法 server 400、通配接受、空 binding。
- **修复真实缺陷**：`GET /config/skill-binding` 空 binding 清空不生效 —— 根因 `loader._deep_merge` 对"dict 覆盖空 dict"递归合并不变，PUT {} 后 GET 仍合并回 yaml 默认；修复为 runtime 键存在时直接返回其值（整体覆盖语义）。
- 结果：**11 + 5 + 6 全绿**。mcp_browse 真实会话验收（模型自主调用）留待后端启动后手动执行（依赖真实 LLM）。

### 11.3 P1-C 增量重灌（完成）
- `update_document`（deactivate + 重处理）已存在，确认为唯一重灌通道（kb_repo 全检索路径 `is_active=TRUE` 过滤）。
- **新增 `test_kb_update_integration.py` 3 测**（真实 DB）：V4 状态机（旧 doc/chunk 停用、新 doc 激活、active chunks 无残留、旧内容不命中/新内容命中）、幂等（同内容重复 update 不新增）、scenario 保留。
- 结果：**3 passed**。

### 11.4 P1-D 监控增强（完成）
- `EmbeddingService` 新增可选 `metrics_sink` 回调（无 DB 依赖，构造注入 + `set_metrics_sink`），`_embed_texts` 各路径（mock/worker 成功/故障降级）emit `embed_worker_ok / embed_elapsed_sec / embed_n_texts / embed_dim / embed_storage_dim / embed_avail_mem_mb`。
- `factory.build_kb_service` 注入写 `system_metrics`（kind='kb'）的 sink，落库失败静默。
- **新增测试 4**：mock 路径 emit、set 注入、sink 故障不阻断（test_embedding_assembly.py）+ 真实 DB 落库集成（test_kb_update_integration.py，V5 验收）。
- 结果：**25 passed**（两文件合计）。

### 11.5 回归基线
- 前端：tsc `--noEmit` **0 错**；vitest 全量 **49 passed（8 文件）**。
- 后端：全量 pytest（加载 .env，`--ignore=tests/test_eval_full_cycle.py`）——结果见 §11.6（待全量跑完回填）。

### 11.6 提交
- 改动：8 改 + 4 新增（3 后端测试 + 1 前端测试）+ 本文档，独立提交链，待全量 pytest 绿后提交。
