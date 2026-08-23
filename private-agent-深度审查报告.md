# Private Agent 深度审查报告

> **审查对象**：`https://github.com/jkfzhsz/private-agent`（main 分支）
> **审查方式**：克隆仓库 → 按核心子系统并行深读 → 关键结论对照源码逐项验证（标注"已验证"的均为直接读过代码确认）
> **审查日期**：2026-08-04
> **技术栈**：Electron + React + FastAPI + PostgreSQL（本地优先个人 AI Agent）

---

## 一、总体判断

该仓库功能面非常完整（技能系统 / MCP / RAG / 记忆 / 沙箱 / 计费 / 评估），文档体系（blueprint、CONTEXT、ADR）也相当扎实。但深入源码后发现一个系统性特征：

> **大量子系统是"结构性空壳"：要么是永不触达的死代码，要么安全边界是"建议"而非"强制"。**

具体表现为三类：

1. **死代码**：计费系统（`usage` 字段不存在）、RAG 向量检索（`vector_search` 无条件返回 `[]`）、记忆提取（LLM 读到的是占位符）、eval 指标（事件契约不匹配）。
2. **安全边界形同虚设**：沙箱限制在 Windows 上完全不生效；文件读写工具的把关路径由 LLM 自己决定；注入防护事后执行且不阻断；管理 API 无鉴权 + CORS 全开。
3. **并发与一致性缺陷**：断线不取消任务、cancel 打错目标、replay 永久丢事件、迁移系统不可恢复。

以下按严重度分级，全部标注 `文件:行号` 与验证状态。

---

## 二、P0 严重问题（结构性失效 / 安全硬伤）

### P0-1 沙箱是空壳：code_execution 以完整权限 + 全网络运行（Critical）【已验证】

| 项 | 内容 |
|---|---|
| 位置 | `sandbox/executor.py:55-61`、`sandbox/service.py:12`、`sandbox/resource_limiter.py:11-47` |
| 现象 | `ResourceLimiter.get_preexec_fn()`（RLIMIT_AS/RLIMIT_CPU）从未被传入 `create_subprocess_exec`；`disable_network()` 只被 import、从未调用。`config.yaml` 的 `memory_limit_mb: 512`、`network_enabled: false`、`cpu_timeout_sec` 全部静默无效。 |
| 后果 | 恶意/注入脚本可 `socket.connect(攻击者)` 任意外联、耗尽内存与磁盘；Windows 无 RLIMIT 机制，必须依赖 Job Object / WINAPI，当前完全没有。 |
| 建议 | 沙箱真正接入隔离层：Windows 用 Job Object（终止进程树）+ 网络拦截，Linux 用 `preexec_fn` + RLIMIT；`code_execution` 默认需用户确认。 |

### P0-2 file_read / file_write 路径限制由 LLM 决定、零用户确认（Critical）【已验证】

| 项 | 内容 |
|---|---|
| 位置 | `tools/builtins/file_read.py:44,58-64`、`tools/builtins/file_write.py:27,33-39`、`tools/defs.py:46` |
| 现象 | `data_dir = args.get("data_dir", "")`，仅 `if data_dir:` 才做 containment 检查；生产调用方从不注入 `data_dir`。两工具默认 `safety_level="none"`，不触发权限确认。 |
| 后果 | 一条 prompt injection 即可：`file_write(path=".../Startup/evil.py")` 实现启动持久化 RCE；`file_read(path="C:\Users\<user>\.ssh\id_rsa")` 窃取密钥；或覆盖应用自身 `config.yaml`/`.env`。全程零用户交互。 |
| 建议 | 服务端强制注入安全目录（不信任 LLM 参数），并把 file_write/敏感路径工具升级为 `elevated`，默认要求用户确认。 |

### P0-3 客户端断线不取消运行中的 turn，工具副作用继续执行（Critical）

| 项 | 内容 |
|---|---|
| 位置 | `main.py:373-386`（`WebSocketDisconnect` 只调 `mark_session_interrupted`，不 cancel `_session_tasks`）；`react_loop.py:197-200`（WS 发送异常被吞） |
| 后果 | 用户关闭客户端后，带副作用的工具（code_execution、file_write）继续在后台执行；session 锁被"幽灵 turn"持有，重连后新消息排队卡死；新连接无法观察到该 turn 的事件。 |
| 建议 | 断线时真正 `cancel` 运行中 task，并在取消路径中终止子进程、释放锁、写中断标记事件。 |

### P0-4 `cancel` 打错目标 / 排队 turn 变得不可取消（High）

| 项 | 内容 |
|---|---|
| 位置 | `main.py:329-334`（单槽 `_session_tasks[session_id] = task`）、`main.py:341-343`（cancel） |
| 现象 | 第二个 `user_message` 会覆盖 `_session_tasks[session_id]`；随后第一个 task 的 done_callback `pop(sid)` 弹掉的是第二个（仍在排队）条目，使其之后运行却再也无法被 cancel。用户点"停止"取消的往往是排队中的空转 task。 |
| 建议 | `_session_tasks` 改为 `dict[sid, set[Task]]`，或对同 session 并发 user_message 直接拒绝；cancel 遍历取消全部 task。 |

### P0-5 Replay 永久丢事件（数据一致性，Critical）

| 项 | 内容 |
|---|---|
| 位置 | `frontend/renderer/App.tsx:442-451`（对每个事件发 `ack`）、`backend/private_agent/storage/ws_offset.py:166-170`（`effective_offset = max(config_offset, last_turn)`）、`react_loop.py:183-200`（先 INSERT 后发 WS） |
| 现象 | 事件按"每条 ack、turn 粒度 offset"追踪：某 turn 的 `E3` 已写库但 WS 推送丢失 → 重连后 `turn > offset` 查不到 E3，**该 turn 尾部永久不可达**；`update_ws_offset`（`ws_offset.py:92-115`）无 `GREATEST` 单调保护，stale ack 可让 offset 回退造成重复。 |
| 建议 | 改为**事件级 id + 客户端去重**；`update_ws_offset` 加单调约束；`_emit_event` 的持久化与推送之间补兜底。 |

### P0-6 计费系统是死代码（Critical）【已验证】

| 项 | 内容 |
|---|---|
| 位置 | `core/react_loop.py:331`（`hasattr(result, "usage")`）、`models/base.py:26-37`（`ChatResult` 无 `usage` 字段）、`core/billing.py:16-21`（硬编码 USD 定价） |
| 现象 | `ChatResult` 没有任何 adapter 设置 `usage` → `record_usage` 永不执行，`token_usage` 事件永不写入。即便触发，`BillingRecorder` 也是硬编码 USD 价格，`config.yaml` 的 `currency: CNY`、`price_snapshot_enabled` 全仓库无读取者。 |
| 后果 | 成本追踪系统性缺失；eval 的 `efficiency.total_tokens/total_cost` 恒为 0。 |
| 建议 | `ChatResult` 增加 `usage` 字段，adapter 解析流式 `include_usage` 尾块并回填；计费价格改为按 provider 配置查找。 |

### P0-7 eval 指标基于错误事件契约，结构性恒为 0/100（Critical）

| 项 | 内容 |
|---|---|
| 位置 | `eval/metrics.py:100-104,155-163,175-199,224-233` vs `core/react_loop.py:174-180,448-454` |
| 现象 | `metrics.py` 读 `e["tool"]`/`e["args"]`/`timestamp`/`final.total_tokens`/`error.subtype`；实际事件形状是 `payload.tool_name`/`payload.arguments` 且无时间戳。 |
| 后果 | 工具选择准确率恒 0、时长恒 0、安全分恒 100——**所有非文本评估指标都是噪声**，版本对比结论无意义。单测 `tests/test_eval_metrics.py:65-68` 固化的是错误契约，CI 无法发现。 |
| 建议 | 统一事件契约（或让 metrics 适配真实事件形状），并加契约校验测试。 |

---

## 三、P1 高优先级

### P1-1 注入防护事后执行且不阻断；记忆 / KB 块绕过防护（High）

| 项 | 内容 |
|---|---|
| 位置 | `core/injection_guard.py:14-30`（仅 6+2 条正则）、`core/react_loop.py:657-680`（扫描在工具执行后，只截断不剔除）、`context_manager._inject_memories:116-141`、`inject_kb_chunks:143-187` |
| 现象 | 短注入 payload 原样进入上下文；`web_search`/`file_read`/MCP 结果携带"ignore previous instructions …"可直接引导模型调工具；记忆与 KB 块注入时完全不经过 guard。 |
| 建议 | 对工具输出做**移除+告警**而非仅告警；记忆/KB 注入同样走 guard；注入内容不得提升为用户角色指令。 |

### P1-2 `http_request` 无 URL 校验 → SSRF（High）

| 项 | 内容 |
|---|---|
| 位置 | `tools/builtins/http_request.py:35-44` |
| 现象 | URL 直传 `httpx`，无 scheme/host 白名单、无防环、无重定向限制。可打云元数据 `169.254.169.254`、`127.0.0.1:8765`（无鉴权管理 API）、LAN 设备。 |
| 建议 | 禁 loopback/内网/云元数据段；禁跨主机重定向；默认需用户确认。 |

### P1-3 管理 API 无鉴权 + CORS 全开（High）

| 项 | 内容 |
|---|---|
| 位置 | `main.py:26-32`（`allow_origins=["*"]`）、`api/admin.py`（端点无 `Depends` 鉴权） |
| 现象 | 任意本地网页可读记忆（`GET /admin/memories`）、改 provider `base_url` 劫持路由（`PUT /admin/settings/providers/{name}`）、触发 `POST /admin/settings/sandbox/test` 任意代码执行、`POST /admin/skills/upload` 写文件。 |
| 建议 | CORS 收窄到 localhost 来源；管理 API 加本地随机 token 鉴权（启动时生成，preload 注入）。 |

### P1-4 `kb_chunks` 迁移 SQL 是非法字面量，升级路径损坏（High）【已验证】

| 项 | 内容 |
|---|---|
| 位置 | `storage/migrations.py:78-81` |
| 现象 | `ALTER TABLE kb_chunks ALTER COLUMN embedding TYPE vector(1024) USING '\\x'::bytea::text::vector` —— USING 是常量且非法 vector 字面量，**非空表执行必抛错**；被 `main.py:580-590` catch 后仅告警 → 每次启动重试失败，HNSW 索引永不建立。 |
| 建议 | 修正 USING 表达式（按列转换或分步迁移）；加失败即停、可回滚的迁移框架。 |

### P1-5 手写迁移系统无版本、非事务、不可恢复（High）

| 项 | 内容 |
|---|---|
| 位置 | `storage/migrations.py:89-143`、`main.py:580-590` |
| 现象 | `schema.sql` 在事务外执行；中途失败后下次启动 `sessions_exists` 守卫**跳过整段 schema**，失败点之后的表永不创建。无版本表；并发启动（双 worker）会竞态 DROP/ADD constraint。 |
| 建议 | 引入带版本号的迁移表（如 Alembic 或轻量自研），每步在事务内执行并记录版本。 |

### P1-6 删除会话竞态与孤儿数据（High）

| 项 | 内容 |
|---|---|
| 位置 | `api/admin.py:1071-1096`（`delete_session` 不检查活跃状态）、`storage/ws_offset.py:92-115` |
| 现象 | 删除进行中会话 → CASCADE 删父行 → 运行中的 `ReactLoop` 下一条 INSERT 违反 FK 崩溃；`config_runtime ws_offset:{id}`、`messages_archive` 无 FK 成孤儿；客户端生成随机 session_id 可能与残留 offset 冲突。 |
| 建议 | 删除前检查 status；孤儿表补清理/关联；offset 写入加单调保护。 |

### P1-7 压缩滑动窗口摧毁 Frozen / Stable Zone（High）

| 项 | 内容 |
|---|---|
| 位置 | `core/compressor.py:157-188`、`core/react_loop.py:933-941,1033` |
| 现象 | `_sliding_window` 按 `turn < keep_from` 统一标记，不区分 zone：system prompt（无 turn 键默认 0）与 stable zone 中的记忆/KB（携带注入时旧 turn）在靠后轮次被标 `compressed=True` 并过滤出 API 上下文；system prompt 还会被截断塞进 assistant 摘要，污染后续每一轮。 |
| 建议 | 压缩仅作用于 active zone；system prompt / stable zone / 记忆 / KB 永不压缩。 |

### P1-8 RAG 向量管线整条是空壳（High）【已验证】

| 项 | 内容 |
|---|---|
| 位置 | `knowledge/kb_repo.py:396-400`（`vector_search` 无条件 `return []`）、`kb_repo.py:240-242`（`insert_chunk` 硬编码零向量）、`knowledge/embedding_service.py`（离线失败静默返回零向量）、`reranker_service.py:62-70` |
| 后果 | 用户上传文档后搜索必空，UI 却显示"知识可用"；reranker 被跳过，`min_similarity` 过滤失效。 |
| 建议 | 打通 `insert_chunk` 真实向量写入、实现 `vector_search`（pgvector）、本地 embedding 失败时显式报错而非静默零向量。 |

### P1-9 记忆提取读的是占位符（High）【已验证】

| 项 | 内容 |
|---|---|
| 位置 | `memory/manager.py:231-233` |
| 现象 | `session_messages=f"[session_id={session_id}, turn={current_turn}]"` —— LLM 每 8 轮提取时看到的是空壳字符串，**记忆功能按设计就不工作**。 |
| 建议 | 加载真实会话消息（含工具结果，但需先去注入）后拼接给提取 prompt。 |

---

## 四、P2 中优先级

| # | 位置 | 问题 | 影响 |
|---|---|---|---|
| P2-1 | `eval/replay.py:108-130` + `mock_tool_registry.py:76-99` | "replay"实为**活执行**（重新调 LLM），mock 忽略模型参数返回罐头输出 | 版本对比是"苹果比橙子"，结论不可信 |
| P2-2 | `api/eval.py:117`、`eval/judge.py:37-40,97-98`、`admin.py:1993` | eval 用静态 `load_config()`（看不到设置页配置）→ 无 provider 则 500；judge_model 空则静默记 0；`version=` 传给 `new_version=` 形参 → 自动回归永不触发 | 真实部署下 eval 不可用 |
| P2-3 | `models/adapters/__init__.py:148-194`、`models/base.py:146-155` | `chat_stream` 不把 httpx 错误包成 `ProviderError` → 流式中断**不走 fallback**；部分流已发客户端后降级重发 → 用户看到 A 的部分 + B 的完整，且 DB 与显示分叉 | 最常见降级路径不可靠 |
| P2-4 | `tools/mcp_tools.py:204-238`、`tools/mcp_client.py:550-578` | MCP 工具 `safety_level=none` 绕过权限门；stdio 读循环无行大小上限；`_handle_response` 不校验 jsonrpc 协议；协议版本 `2026-07-28` 为臆造 | 恶意/失陷 MCP server 输出直入上下文并自动执行 |
| P2-5 | `memory/memories_repo.py:199` vs `:30-35`、`:86-100` | 淘汰要求 `importance < 0.3` 但最低类型也有 0.5 → 永不过期；无去重，重复事实无限累积并每轮重注入 | 记忆质量持续恶化 |
| P2-6 | `storage/ws_offset.py:182-201` | 重放取 `role='user'` 无 zone 过滤 → KB/记忆注入被重放成"用户气泡" | 界面污染、上下文误导 |
| P2-7 | `frontend/renderer/App.tsx:65` vs `main/preload.ts:9` | 渲染器硬编码 `ws://localhost:8765`，preload 读配置端口 | 改端口即整个 UI 失联 |
| P2-8 | `frontend/renderer/App.tsx:308-313,464-476,602-629,1331-1335` | WS 非 OPEN 时消息静默丢弃；`isGenerating` 只能由不持久化的 `turn_end` 复位 | 断线重连后 UI 卡死、消息丢失 |
| P2-9 | `frontend/main/sidecar.ts:86-111` | 崩溃重启后新进程绑定失败但 `waitForHealth` 命中僵尸旧进程 | 静默连到陈旧实例，确认卡片报错 |
| P2-10 | `frontend/main/window.ts:24`、`index.html` | `sandbox:false` + 全局 `--no-sandbox` + 无 CSP + 运行时拉取 Google Fonts | Electron 纵深防御弱化 |
| P2-11 | `sandbox/executor.py:62-81` | 仅 `TimeoutError` 杀子进程；取消（`CancelledError`）后子进程成孤儿继续运行 | 取消泄漏后台进程 |
| P2-12 | `storage/disk_alert.py:54-58,81-84` | "已自动清理"文案但从未触发清理；`get_pg_data_dir_size` 统计实例全部数据库导致误报 | 磁盘告警失真 |
| P2-13 | `storage/ttl_cleanup.py:90-114` | `cleanup_old_eval_runs` 是死代码，`eval_runs` 无限增长 | 违反"保留近 100 条"契约 |
| P2-14 | `core/react_loop.py:856-894,983-1033`、`memory/manager.py:194-219` | 压缩/合并/记忆提取多语句非事务：mark-compressed 与 INSERT summary 间崩溃 → 消息被永久过滤出上下文 | 静默上下文丢失 |
| P2-15 | `main.py:409,603` | 热路径用 `db.connect()`（每事件新建连接）而非启动时的连接池；`db.get_pool()` 无初始化锁 | 连接数随并发膨胀、可能泄漏池 |
| P2-16 | `api/admin.py:535-559` | 自动生成的 `PA_MASTER_KEY` 明文追加写入 `backend/.env` | 主密钥落盘明文 |
| P2-17 | `frontend/renderer/views/KnowledgeView.tsx:9,97,172` | 使用中文键 `total_片段s`/`s.片段s`，后端返回 `total_chunks`/`chunks` | 渲染为"—"、"undefined 片段" |

---

## 五、修复路线图

### 阶段一：保底正确性（改动集中、风险低，建议最先做）

1. **打通计费**：`ChatResult` 增加 `usage` 字段；adapter（含流式 `include_usage` 尾块）回填真实用量；计费价格按 provider 配置查找，`record_usage` 写入 `token_usage` 事件。（对应 P0-6）
2. **修复 Replay 丢事件**：改为事件级 id 去重；`update_ws_offset` 加 `GREATEST` 单调保护；客户端去重逻辑。（P0-5）
3. **断线/取消正确性**：WS 断线真正 cancel 运行中 task 并终止子进程；`_session_tasks` 改为集合结构或拒绝并发 user_message。（P0-3、P0-4、P2-11）
4. **文件工具强制约束**：服务端强制注入 `data_dir`，不信任 LLM 参数；file_write 与敏感路径升级为需确认。（P0-2）
5. **迁移修复**：修正 `kb_chunks` USING 表达式；引入版本化迁移表。（P1-4、P1-5）

### 阶段二：安全硬边界

6. **沙箱真正隔离**：Windows Job Object + 网络拦截；Linux RLIMIT/preexec_fn；沙箱参数全量接入。（P0-1）
7. **SSRF 防环**：http_request 禁 loopback/内网/云元数据 + 禁跨主机重定向。（P1-2）
8. **API 鉴权 + CORS 收窄**：随机本地 token + localhost 来源白名单。（P1-3）
9. **注入防护强化**：工具输出做"移除+告警"；记忆/KB 注入纳入 guard。（P1-1）

### 阶段三：功能复活

10. **RAG 打通**：`vector_search` 实现、`insert_chunk` 写真实向量、embedding 失败显式报错、reranker 接入。（P1-8）
11. **记忆复活**：提取真实会话消息、加去重、修正淘汰阈值。（P1-9、P2-5）
12. **eval 修正**：统一事件契约、注入 config overrides、修复 `version=` 参数、限制版本对比样本集。（P0-7、P2-1、P2-2）
13. **流式降级**：`chat_stream` 包装 ProviderError，fallback 生效且去重。（P2-3）

---

## 六、附录：发现明细表（按严重度）

| 严重度 | 编号 | 子系统 | 一句话结论 |
|---|---|---|---|
| Critical | P0-1 | 沙箱 | 资源/网络限制全部未接入，code_execution 全权限运行 |
| Critical | P0-2 | 文件工具 | 路径限制由 LLM 决定，可零确认任意读写 |
| Critical | P0-3 | 会话 | 断线后带副作用任务继续执行 |
| Critical | P0-5 | 数据层 | 重放永久丢事件、offset 可回退 |
| Critical | P0-6 | 计费 | `usage` 字段不存在，计费永不执行 |
| Critical | P0-7 | 评估 | 指标事件契约不匹配，恒为 0/100 |
| High | P0-4 | 会话 | cancel 打错目标、排队 turn 不可取消 |
| High | P1-1 | 安全 | 注入防护事后不阻断，记忆/KB 绕过 |
| High | P1-2 | 工具 | http_request SSRF |
| High | P1-3 | API | 管理 API 无鉴权 + CORS 全开 |
| High | P1-4 | 迁移 | kb_chunks 迁移 SQL 非法，升级路径损坏 |
| High | P1-5 | 迁移 | 迁移系统无版本、非事务、不可恢复 |
| High | P1-6 | 数据层 | 删除会话竞态、孤儿数据、offset 冲突 |
| High | P1-7 | 上下文 | 压缩摧毁 Frozen/Stable Zone |
| High | P1-8 | RAG | 向量检索恒返回空，整条管线空壳 |
| High | P1-9 | 记忆 | 提取读到占位符，功能按设计失效 |
| Med-High | P2-1 | 评估 | replay 是活执行 + mock 桩 |
| Med-High | P2-2 | 评估 | eval 无 provider 即 500，judge 静默记 0 |
| Med-High | P2-3 | 模型 | 流式中断不走 fallback，内容重复 |
| Med-High | P2-4 | MCP | 绕过权限门、输出全信、无行上限 |
| Med-High | P2-14 | 数据层 | 压缩/记忆多语句非事务，可能静默丢上下文 |
| Medium | P2-5 | 记忆 | 淘汰死代码 + 无去重 |
| Medium | P2-6 | 数据层 | 重放把 KB/记忆当用户气泡 |
| Medium | P2-7 | 前端 | 端口硬编码，改配置即失联 |
| Medium | P2-8 | 前端 | WS 断线丢消息、UI 卡死 |
| Medium | P2-9 | 前端 | 僵尸 sidecar 竞态 |
| Medium | P2-11 | 沙箱 | 取消泄漏子进程 |
| Medium | P2-12 | 运维 | 磁盘告警失真 |
| Medium | P2-13 | 运维 | eval_runs 无限增长 |
| Medium | P2-15 | 数据层 | 连接池未用于热路径 |
| Medium | P2-16 | 安全 | 主密钥明文落盘 |
| Low-Med | P2-10 | 前端 | Electron 沙箱/ CSP 关闭 |
| Low-Med | P2-17 | 前端 | 字段键不匹配渲染错误 |

---

*本报告基于 main 分支快照（clone 于 2026-08-04）编写；标注"已验证"的条目均由审查者直接阅读对应源码确认，其余来自交叉源码核验。*
