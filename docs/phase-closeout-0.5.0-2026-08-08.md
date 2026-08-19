# 0.5.0 版本收尾报告：三场景独立 + 记忆优化 + 四窗口并发架构

> 版本号：**0.5.0**（frontend/package.json + package-lock.json）
> 完成时间：2026-08-08 23:30
> 依据计划：`docs/next-phase-plan-2026-08-08-scene-independent-memory.md`（0.5.0 三阶段）+ `docs/next-phase-plan-2026-08-08-four-windows.md`（四窗口架构 P1-P4）
> 测试基线：**后端 1277 passed / 4 failed**（4 个失败均为基线既有问题，非本轮引入）+ **前端 vitest 20 passed + tsc 0 error + vite build 成功**
> 打包：**未执行**（按约定由蒋先生手动运行 `build-electron.bat`）

---

## 一、本次版本范围

0.5.0 覆盖 **0.4.3 发布之后至今的全部改动**，共四大块：基础质量修复、0.5.0 三阶段（M1/M2/M3）、四窗口并发架构（P1-P4）、前端交互打磨。

## 二、改动总结

### 2.1 基础质量修复（0.4.3 之后）

| 项 | 内容 | 状态 |
|---|---|---|
| MCP"返回空"三连根治 | ① stdio 并发 rid 竞态（入口快照局部 rid）② T-1 参数注入误伤所有工具（白名单收窄 file_read/file_write/read_artifact）③ 前端 deAIfy 删表格行 | ✅ |
| 壁纸系列重构 | 极简化 → 缩放/移动/旋转 → 亮暗主题独立背景 → 切换入口移到侧边栏滑块 → 主题切换交叉淡化（CSS animation） | ✅ |
| 暗色主题清理 | 全量语义变量 tokenize（design-tokens.css），修复残留黑字（ArtifactPanel tab/对话页 toolbar/输入卡片） | ✅ |
| 技能丢失根因 | skills loader 缺 `expandvars`（PA_USER_DATA 引入后技能丢失真正根因） | ✅ |
| Reasonix 技能库移植 | 14+ 技能转换入库（novelist/tdd/search-first/systematic-debug/writing-humanizer 等），deepseek 默认调用 | ✅ |
| 头像与改名 | 头像改原图（RobotAvatar.tsx）；改名入口合并到侧边栏 → 最终迁至设置页 | ✅ |
| 输入区重构 | + 号弹层 IDE 风格、输入卡片统一/无框化/按钮改小 | ✅ |

### 2.2 0.5.0 三阶段（M1 场景独立底座 / M2 场景专业强化 / M3 记忆优化）

| 模块 | 关键实现 | 状态 |
|---|---|---|
| **M1 场景独立底座** | `user_memories.scope` 隔离 + `sessions.kind` 扩容（main/sub/monitor）+ 三场景人格化 system_prompt（子瞻=苏轼/白圭=商祖/清和=谢安）+ 前端三场景按钮（首页） | ✅ |
| **M2 场景专业强化** | KB auto_retrieve + vector 检索修复（全 0 向量降级纯 keyword、hybrid 串行化避 asyncpg 单连接冲突）+ 语料入库（`ingest_m2_kb.py`） | ✅ |
| **M3 记忆优化** | 画像聚合（`aggregate_profile` 24h 刷新 + `upsert_profile`）+ 归档巩固（`archive_memories`/`search_archived`）+ 评估工具（`memory_stats`）+ 注入配额 2:8（global:scene）+ `_rank_memories`（importance×时间衰减+hash 去重） | ✅ |
| 会话级模型隔离 | 每轮从 `sessions.model_id` 独立构建 FallbackChain 实例，适配器零共享 | ✅ |

### 2.3 四窗口并发架构（P1-P4）

| 阶段 | 内容 | 状态 |
|---|---|---|
| **P1 监控数据链路** | `system_metrics`/`optim_log` 两表（幂等迁移）+ `core/metrics_collector.py`（psutil+WS+react_events 聚合，60s 采集，72h 保留）+ 4 监控工具（system_metrics_query/system_status/optim_plan/apply_optim，apply_optim 仅 approved+context 白名单+elevated）+ admin optim-log 审批流 API | ✅ |
| **P2 多窗口前端** | `activeSlot` + `windowCacheRef` 4 slot 快照（sessionId/skill/events/input/model/lastTurn）+ `switchWindow`/`closeWindow`/`saveWindowSnapshot` + 单 WS 复用（消息带 session_id 路由，切换零网络请求） | ✅ |
| **P3 主智能体装配** | `skills/monitor/system_prompt.md` 专属提示词 + `_monitor_system_prompt`（注入 latest_summary）+ monitor 工具白名单 + `sessions.kind='monitor'` + MonitorPanel（指标摘要 30s 轮询 + 优化审批卡 15s 轮询） | ✅ |
| **P4 协调打磨** | SettingsView `AgentNameSection` 名称配置卡（主智能体 + 三场景集中改名）；`max_concurrent_turns` 记为 V2 增强 | ✅ |

### 2.4 前端交互打磨（P5 系列，蒋先生多轮反馈）

| 轮次 | 内容 | 最终状态 |
|---|---|---|
| tab 条探索 | 顶部浮窗 → sticky 条 → 问候卡内 → 问候卡同行，多轮位置调整 | ❌ **最终删除 tab 条**（2026-08-08 22:44 蒋先生决定） |
| **状态圆点方案** | ① 智能体图标旁状态圆点（绿=对话中/红=无对话）② 右上角"🗑 关闭对话"按钮 ③ 点击图标恢复未结束对话，切出页面不打断 | ✅ |
| 圆点样式统一 | 统一为左下角"本地用户"状态点样式（7px 纯色，无光效） | ✅ |
| **圆点排列调整（最后一项）** | 按蒋先生截图统一为"本地用户"卡片式：头像在左 → 名称加粗在上 → 7px 圆点 + 状态文字（"对话中"/"无对话"）在名称下方 | ✅ |
| 相关 bug 修复 | ① handlePickMode 恢复分支加 `sessionId>0 && skill 匹配` 双校验 ② closeWindow 删除后不再 saveWindowSnapshot ③ 场景切换清空 input ④ 删除"场景变化→agentName 覆盖"effect，对话区用 `renderChatAssistantName` 动态区分 | ✅ |

## 三、测试统计

| 维度 | 结果 |
|---|---|
| 后端 pytest（ignore test_eval_full_cycle） | **1277 passed / 4 failed**（8 warnings，1318.58s） |
| 后端新增测试 | test_memory_scope(22) + test_scene_skill(15) + test_kb_retrieval(5) + test_memory_optimization(7) + test_system_metrics(11) + test_admin_provider_lifecycle(5) + test_wallpaper_theme + 8 个既有测试更新 |
| 前端 vitest | **20 passed**（Windows.test.tsx 4 用例：PA 图标入口/状态隔离/切出不打断/关闭归档） |
| 前端 tsc | 0 error |
| 前端 vite build | 成功（--emptyOutDir false） |

### 3.1 未通过测试（4 个，均为基线既有问题，非本轮引入）

| 测试 | 根因 | 归属 |
|---|---|---|
| `test_admin_database.py::test_get_database_settings_defaults` | `_ensure_master_key` 从 backend/.env 继承密钥后**不写 user_env** 的既有逻辑缺陷（git diff 证明 admin.py master key 区域本轮 0 改动，2026-08-06 引入） | 基线 |
| `test_admin_database.py::test_put_database_settings_persists` | 同上 | 基线 |
| `test_admin_database.py::test_master_key_stable_and_inherited` | 同上 | 基线 |
| `test_code_execution_tool.py::TestCodeExecutionHandler::test_handler_no_config` | 全量顺序残留（单独运行 12 个用例全过，仅在串行全量末尾出现） | 环境 |

## 四、未完成工作（留待下一版本）

1. **腾讯控股研报语料缺失**：白圭 KB 暂用内置投资方法论框架语料；用户后续提供研报文件后可用 `ingest_m2_kb.py` 入库（入口与脚本已就绪）。
2. **embedding Worker 未配置（2026-08-09 已定位三层缺口，待新对话窗口讨论解决）**：向量检索自动降级为纯 keyword（不影响可用性）。缺口如下：
   - **① 代码装配缺口**：`EmbeddingService` 构造时 `worker_pool` 恒为 `None`（`embedding_service.py:123` 走 mock 全 0 分支）；`kb_service.py:46` 默认构造未传 `worker_pool`；全部 6 处装配点（admin.py 4 处 + context_manager.py 2 处）均未注入 `embedding_service`；`core/executor.py get_pool()`（ProcessPoolExecutor 2 worker）已存在但从未被 embedding 使用。→ 需装配 `EmbeddingService(worker_pool=get_pool(), config=...)` 并注入各 KBService。
   - **② Python 依赖缺口**：`FlagEmbedding` 未安装（venv 内 `import FlagEmbedding` 抛 ModuleNotFoundError），Worker 进程内 `_embed_worker_fn` 的 `from FlagEmbedding import BGEM3FlagModel` 失败 → 即使传了 worker_pool 仍返回全 0。→ 需 `pip install FlagEmbedding`（清华镜像）。
   - **③ 模型权重缺口**：`BAAI/bge-m3`（1024 维）或低内存自动切换 `BAAI/bge-small-zh-v1.5`（384 维）权重未下载。→ 需 `HF_ENDPOINT=https://hf-mirror.com HF_HUB_DISABLE_XET=1` 拉取（本机直连 HF 超时）。
   - **附加**：云端兜底 `_cloud_embed` 同为 mock（全 0），且 `config.yaml` `fallback_cloud: ""` 已按去预置化置空——云端路径实际禁用。
3. **V2 增强项（设计文档已记录，V1 不阻塞）**：
   - `max_concurrent_turns` 全局并发限制（WS 天然按会话隔离，V1 已满足）
   - 会话级 provider 参数覆盖（`sessions.model_params` 列：max_tokens/temperature）
   - 会话级 fallback_chain 覆盖列
4. **子代理委派（项-1）**：ADR 待评估（V1.5 遗留，用户明确不使用子代理，优先级低）。
5. ~~**git 提交**：当前工作区 120+ 文件改动/新增未提交，打包前建议先提交~~ ✅ **2026-08-09 已提交**（含 P6 全部改动）。
6. **测试基线 4 个失败**：`_ensure_master_key` 不写 user_env 缺陷建议下一版本修复；`test_handler_no_config` 全量顺序残留建议定位（疑似全局状态污染）。

## 五、2026-08-09 补充：P6 单 WS 复用 + 主智能体统一渲染（已并入 0.5.0）

### 5.1 切换会话输入框短时不可用（已修复）
- 根因：原实现每次切换会话/窗口都 close WS + 重建（挂载 effect 依赖 `[connect, sessionId]`），重连期间 `status !== "connected"` → 发送按钮禁用。
- 修复：**单 WS 复用** —— `sessionIdRef/realSessionIdRef/activeSlotRef` 解耦依赖，WS 只挂载时建一次；切换仅更新内存 activeSessionId + 发 replay；`handleSwitchSession` 移除手动 close。

### 5.2 事件按 sessionId 分发到独立状态切片（蒋先生验收 6 条全过）
- **不 abort 后台推理**（允许后台多任务并行）：后台窗口生成继续跑，事件不丢弃。
- `appendToSlotEvents(slot, msg)`：非当前会话 react_event 按 session_id 找到所属窗口快照写入（delta/thinking 同款累积合并、更新 lastTurn），不渲染当前视图；切回时 `switchWindow` 恢复快照即展示后台进展。error 同样写入对应快照。
- 后端 main.py 补齐 16 处缺 session_id 的下行消息（replay_failed/ack_failed/regenerate_failed/approval_deferred/resume_failed/user_message_failed + 13 处 invalid 协议错误）；兜底扫描仅 pong（心跳）不带。

### 5.3 主智能体对话视图统一渲染
- 删除 monitor 独立简化渲染块（约 150 行），`view === "chat"` 统一走场景完整渲染：delta 流式/thinking/工具/沙箱/权限确认/消息操作条/完整输入卡片全部复用；仅 chip 名、切换技能按钮（隐藏）、空态/placeholder 按角色适配。

### 5.4 测试统计（08-09 终值）
| 维度 | 结果 |
|---|---|
| 前端 vitest | **25 passed**（新增 P6-1 切换不新建连接 / P6-2 主智能体功能区+流式 / P6-3 后台快照写入切回展示 / P6-4 并发来回切换不串台 / P6-5 重连恢复不串台） |
| 前端 tsc / vite build | 0 error / 成功 |
| 后端 WS 相关 pytest | 43 passed 无回归 |

## 五、发布与打包说明

- 版本号已更新：`frontend/package.json` 与 `frontend/package-lock.json` → **0.5.0**；代码注释中 0.6.0 引用已统一替换为 0.5.0（纯注释，无逻辑依赖）。
- 打包由蒋先生手动执行（双击 `build-electron.bat`，产物 `release2/Private Agent Setup 0.5.0.exe`）。
- 正式版通过应用内更新升级（docs/release-and-update.md）：升级后 `%APPDATA%` 配置/DB/mempalace 均不丢。
- 如发布 GitHub Release：`node scripts/publish-release.mjs --dry-run` 先检查，再正式发布（自动 tag v0.5.0）。

---

*文档结束。下一版本从「未完成工作」章节继续。*
