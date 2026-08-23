# PA 前端体验与完整度分阶段迭代开发方案（V1.1–V1.4）

> 状态：已批准实施 ｜ 制定日期：2026-08-05 ｜ 依据：2026-08-05 代码实勘 + 用户确认的三项设计决策
> 配套实现代码见仓库；本文件为迭代路线的唯一权威来源，各阶段开发与验收以此为准。

---

## 1. 目标与总体原则

**目标**：把 PA 从"Demo 体验版"迭代为"稳定个人主力工具"，四阶段独立可上线、不烂尾、不依赖后续功能。

**迭代原则**（用户确认）：
1. 先可用、再好用、再强大：优先补齐 MVP 缺失的核心闭环，再优化体验，最后做高阶智能与生态能力。
2. 按业务闭环拆分：会话闭环 → 智能体配置闭环 → 文件/工具闭环 → 监控调试 → 高级智能 → 生态美化。
3. 贴合个人使用场景：优先个人刚需功能，弱化多人团队、商业化冗余功能。
4. 阶段独立可上线：每一阶段结束均可独立打包发布、稳定使用。

**前置说明**：基础会话聊天、简单智能体创建、基础文件上传、基础模型调用、基础页面框架在 MVP 已完成，本方案全部为 MVP 之后的迭代内容，无重复基础功能。

---

## 2. 现状盘点（2026-08-05 代码实勘）

### 2.1 前端（renderer/，React+TS，无路由库，App.tsx 内 view 切换）

| 模块 | 现状 | 缺口 |
|---|---|---|
| App.tsx chat 主界面 | 思考链折叠、tool_call 明细、sandbox 流式输出、编辑重发（仅最后一条 user 消息）、终止生成 | 消息重生成/收藏/删除、代码块复制、图片粘贴 |
| Sidebar | 会话列表 + 仅删除 | 新建/重命名/归档/文件夹/搜索 |
| SettingsView(1866行) | Provider CRUD+测试、MCP servers 增删测、Sandbox、Security、Permission、Wallpaper、Skills、Update | 无 MCP env/密钥 UI、无工具日志、无用量统计 |
| KnowledgeView(183行) | 统计卡 + 文本粘贴上传 | 无文件上传/库管理/检索/绑定 |
| MemoryView(170行) | 列表 + 手动提取 | 无新增/删除/检索 |
| HomeView(332行) | 问候 + 壁纸/视频背景 + 3 模式按钮 | 无主题切换、无全局搜索 |

### 2.2 后端（FastAPI，业务端点全在 api/admin.py，WS 主通道 8765）

| 能力 | 现状 |
|---|---|
| 会话 | GET 列表/删除/激活/workspace/model/记忆提取；schema.sql 已支持 status IN ('active','interrupted','archived','error') + archived_at，无归档/重命名/新建/文件夹端点 |
| 消息 | messages 表（zone/turn 结构）+ messages_archive；无 starred 字段、无重生成/删除单条端点 |
| 智能体 | Skills 框架已构成"智能体"模型：skill.yaml（manifest：name/version/description/scenario/enabled/permissions/knowledge_base/dependencies.tools）+ system_prompt.md + tools.yaml + examples；skills DB 表；无头像/标签/参数 UI |
| 文件 | 仅 /admin/files/upload + /files/outputs/{filename} 下载；无树/预览/增删改 |
| 任务 | async_tasks 表存在；WS 有 turn_end/error/cancel；无任务列表端点/UI |
| MCP | servers CRUD + assemble 装配 + enabled + test 全有 |
| 记忆 | memory/manager.py + memories_repo + user_memories 表；自动提取 + 手动提取端点 |
| 知识库 | kb_repo/service + document_processor + embedding_service + reranker；仅 stats/upload 端点 |
| 调试 | core/billing.py（TokenUsage + BillingRecorder.record_usage）已存在；react_events 表全量记录工具事件；core/observability/ 目录存在 |
| 系统 | providers CRUD（OpenAI 兼容动态注册）、sandbox/permission/hooks/wallpaper/skills 全有 |

### 2.3 关键结论

1. **后端引擎能力远超前端呈现**：压缩/Stable Zone/KB 注入/记忆/权限确认/MCP 双协议已就绪，缺口主体是前端可视化闭环 + 少量后端增量端点。
2. **"智能体"无需新实体**：skill.yaml + skills 表已构成完整智能体配置模型。
3. **归档/任务/用量统计的地基已存在**（sessions.status、async_tasks、billing），多为"补端点 + 补 UI"。
4. 会话删除/归档需与 messages_archive 压缩机制保持一致性（soft-delete 优先）。

---

## 3. 设计决策（用户已确认，2026-08-05）

1. **智能体模型 = Skill 升级**：不新建 Agent 实体，复用 skill.yaml manifest + skills 表，补可视化元数据（avatar/tags/description）+ 可选 model_params 覆盖。
2. **文档粒度 = 四阶段全部细化到任务级**（后端 API / 前端组件 / 测试 / 验收）。
3. **实施顺序 = 端到端闭环优先**：每个功能前后端一起做、独立验收。

**工程约束（长期约定，必须遵守）**：
- 避免过度设计：**不做"全局默认参数"层**（V1.4 原方案条目删除，参数跟随模型/provider）。
- 外部服务统一 MCP 前端接入：**不内置网页抓取器**（V1.3 网页入库走 MCP 工具链路）。
- 工具权限 = 全局 permission（运行时）+ skill.yaml dependencies.tools（per-skill 声明），**不做 per-agent 独立权限表**。
- 后端启动 cwd=backend；pytest 必须加载 backend/.env；asyncpg JSONB 返回 str 需 json.loads；get_messages 剥离内部字段（双接口约定）。

---

## 4. 四阶段总览

| 阶段 | 主题 | 核心闭环 | 上线标准 |
|---|---|---|---|
| V1.1 | 核心闭环补全 | 会话管理→消息操作→智能体基础配置→文件管理→任务反馈 | 无功能断点，日常 100% 可用 |
| V1.2 | 能力强化与调试 | 智能体编辑器→MCP/工具→链路监控→基础 RAG | 可定制、可调试、可落地 |
| V1.3 | 高阶智能与自动化 | 长期记忆→工作流自动化→知识库升级→高级文件 | 自主干活、批量处理 |
| V1.4 | 备份与体验 | 导入导出备份→模型管理→系统设置→全局体验 | 安全稳定、个性化 |

依赖关系：V1.1（消息重生成/truncate 是 V1.2 调试的基础）→ V1.2（日志/监控）→ V1.3（记忆管理 UI 依赖 V1.2 记忆配置）→ V1.4（备份依赖全部数据面稳定）。

---

## 5. 第一阶段 V1.1 核心闭环补全（最高优先级）

> ✅ **2026-08-05 已全部实现**：后端新增 6 个测试文件 29 用例全过 + 全量回归通过；前端 13 测试 + tsc 通过。改动落盘即生效（沙箱环境 git 不可用）。

### 5.1 会话管理闭环
- **后端**：`POST /admin/sessions` 新建；`PUT /admin/sessions/{id}` 重命名/归档/取消归档；`PUT /admin/sessions/{id}/folder` 文件夹；sessions 表加 `folder VARCHAR(100)` 幂等迁移；`GET /admin/sessions?folder=` 过滤
- **前端**：Sidebar 新建按钮、双击重命名、右键菜单（重命名/归档/删除/移动文件夹）、文件夹树分组、归档折叠区（恢复）
- **测试**：pytest（sessions CRUD + 归档过滤）

### 5.2 会话数据能力：搜索 + 导出
- **后端**：`GET /admin/sessions/search?q=`（ILIKE title + 消息全文）；`GET /admin/sessions/{id}/export?format=md|json`；PDF 前端打印
- **前端**：Sidebar 搜索框 + 结果下拉；会话菜单导出
- **测试**：pytest（搜索命中/导出完整性）

### 5.3 消息精细化操作
- **后端**：messages 加 `starred BOOLEAN` 迁移；`PUT /admin/messages/{id}/starred`；`DELETE /admin/messages/{id}`（soft-delete 入 archive）；`POST /admin/sessions/{id}/messages/{mid}/regenerate`（重放 turn 走 react_loop）
- **前端**：消息 hover 操作条（重生成/收藏/删除/复制）
- **测试**：pytest（starred、soft-delete 一致性、regenerate）

### 5.4 输入区增强
- 终止生成（已有）、多行输入（已有）
- **图片粘贴上传**：paste 检测 image → /admin/files/upload → user_message 带 attachments；后端透传
- **代码块一键复制**：前端渲染加复制按钮

### 5.5 上下文可控
- **后端**：`POST /admin/sessions/{id}/truncate`；`PUT /admin/sessions/{id}/memory-enabled`；`GET /admin/sessions/{id}/system-prompt`
- **前端**：会话设置弹窗（记忆开关/截断/查看提示词）

### 5.6 智能体基础配置闭环（Skill 可视化）
- **后端**：skills 表加 avatar/tags 迁移；`PUT /admin/skills/{name}/meta`；`POST /admin/skills/{name}/clone`；可选 model_params 注入
- **前端**：新增 AgentLibraryView（卡片墙 + 编辑抽屉）；Sidebar 入口
- **测试**：pytest（meta/clone/model_params）

### 5.7 文件管理闭环
- **后端**：`GET /admin/files/tree`、`GET /admin/files/content`、`POST /admin/files/mkdir`、`PUT /admin/files/rename`、`DELETE /admin/files/delete`（全部锁 workspace）
- **前端**：chat 右侧文件面板（树 + 预览 + 右键菜单）
- **测试**：pytest（树/预览/越界 403）

### 5.8 任务状态反馈
- **后端**：`GET /admin/tasks?session_id=`（复用 async_tasks）
- **前端**：会话内任务状态条 + 任务列表抽屉（重试=regenerate、终止=cancel）

### 5.9 V1.1 验收标准
✅ 会话全生命周期管理；✅ 消息可重生成/编辑/收藏/删除/复制/图片粘贴；✅ 上下文可截断/记忆开关/提示词可查看；✅ 智能体 CRUD/克隆/启停/参数；✅ 文件树/预览/操作闭环；✅ 任务状态可见可重试可终止；✅ 无功能 BUG、数据不丢失（soft-delete 保证）。

> **实现偏差记录（已按工程约定调整）**：
> 1. 消息重生成走 **WS regenerate 协议**（按 turn 重放），非 HTTP 端点——贴合前端事件结构（无 msg_id）。
> 2. 任务状态复用 **react_events 聚合**，未强行接入 async_tasks 空表（蓝图遗留无写入点）——避免过度设计。
> 3. 图片粘贴降级为**文件引用传递**（模型 file_read 感知），vision/base64 传输列为后续增强。
> 4. skill 启停经 meta.enabled 落 skill.yaml + PG；文件删除仅允许文件/空目录（防误删）。
> 5. **2026-08-05 方向修正（用户验收反馈）**：撤销"Skill 升级为智能体"展示——**技能就是技能**。技能页只保留 **调用 + 删除**（新增 `DELETE /admin/skills/{name}`，被活跃会话锁定时 400 拒绝），移除克隆/编辑 UI；Sidebar 入口改"技能"；文件面板并入 ArtifactPanel（"产物/文件"双 Tab），对话界面不再占用。
> 6. **启动链路修正**：桌面快捷方式原指向 release2 旧打包（后端探测 `D:\PA1.0\backend` 旧拷贝导致 405）——已同步 PA1.0 后端 + 桌面改放 `Private Agent Launch.bat`（vbs 关联失效）。

---

## 6. 第二阶段 V1.2 能力强化与调试体系

> ✅ **2026-08-05 已实现**（后端新增 5 个测试文件 15 用例 + react_loop 回归 29 + 前端 tsc/13 测试过），详见下方标注。

### 6.1 技能配置编辑器 ✅（按用户方向：技能就是技能，入口放设置页不污染技能库页）
- 系统提示词编辑器：`GET/PUT /admin/skills/{name}/prompt`（写时自动快照 scope=prompt + 同步 PG + token 估算）；设置页 SkillsSection 每技能 chip 加 ✏️ 编辑器弹窗（等宽编辑 + token 显示 + 保存）
- 记忆/权限/输出规则 UI、实时完整 prompt 预览：**推迟**（V1.1 已提供会话级 memory-enabled/权限配置；prompt 预览可复用 /sessions/{id}/system-prompt）

### 6.2 MCP/工具管理 ✅
- **per-server env 配置**：McpServerRequest 加 env → _build_server_value 保留 → MCPClientConfig.env + stdio create_subprocess_exec 注入（合并 os.environ）→ mcp_tools 传递；前端 McpAddForm 加"环境变量"多行输入(KEY=VALUE)、McpRow 显示 env 数量
- assemble/enabled 开关 UI（V2 P2 已有）✓
- **工具调用日志**：`GET /admin/events?session_id=`（react_events 时间线倒序 + payload 摘要）；任务抽屉加"📜 事件日志"
- per-skill 工具绑定 UI：**推迟**（skill.yaml dependencies.tools 声明层已有，避免过度设计）

### 6.3 任务链路监控 ✅
- **工具耗时**：react_loop `_exec_plan` 用 time.monotonic 记录 duration_ms 进 ToolResult.metadata → tool_result 事件 payload 携带（无需迁移新列，payload JSONB 承载）；前端 tool_result 显示 `· Nms`
- **LLM 用量统计**：`GET /admin/usage`（聚合 react_events token_usage 事件：调用数/token/成本/按会话，billing 已落库）→ 任务抽屉"📊 用量/错误"卡片
- **错误日志**：`GET /admin/errors/summary`（聚合去重 top + samples）、`GET /admin/logs`（logs/ 最新日志尾部）
- 思考链（已有折叠）✓

### 6.4 基础知识库 RAG 轻量化 ✅
- `GET /admin/knowledge`（scenario 分组库列表）、`DELETE /admin/knowledge/{scenario}`（软删全库）、`GET /admin/knowledge/{scenario}/documents`、`POST /admin/knowledge/upload-file`（base64 → 文本 utf-8/gbk → 切片向量化，二进制 400）
- 绑定智能体：沿用 inject_kb_chunks 引擎 + skill.yaml knowledge_base.scenario（绑定选择器推迟）
- KnowledgeView 全面升级：库列表（文档数/片段/删除）+ 文件上传 + 文本上传保留

### 6.5 V1.2 验收标准
✅ 提示词可编辑可快照；✅ MCP 密钥/工具开关/日志可管理；✅ 工具耗时/用量/错误全链路可视化；✅ RAG 库管理 + 文件入库可用。
> 推迟项（避免过度设计/尊重"技能就是技能"方向）：记忆窗口 UI、权限面板可视化、输出规则、per-skill 工具绑定 UI、prompt 完整预览、KB 绑定选择器。

---

## 7. 第三阶段 V1.3 高阶智能与自动化 ✅（2026-08-05 完成）

> **实现偏差记录（已按工程约定调整）**：
> 1. 自动执行后续轮用 `[auto-execute]` 前缀提示模型继续（每轮独立 run_turn + turn_end，前端按 turn 分组天然兼容）；优先级 WS 显式传参 > 会话级配置（auto_execute/max_rounds 落 sessions 表）
> 2. reindex 依赖 kb_service.process_document 新增 `skip_dedup` 参数（重索引前清空旧 chunk 后必须强制重切，否则 hash 去重走 "unchanged" 返回空）
> 3. 网页入库**未实现抓取器**：按方案走 MCP 工具链路（用户用 MCP 抓取后文本/文件入库），仅文档说明
> 4. 切片配置通过 `_build_kb_processor(cfg)` 统一注入 upload/upload-file/search_test/reindex 四个端点（config_runtime knowledge.chunking 点分 key）

### 7.1 长期记忆系统完善
- `POST /admin/memories` 手动新增、`DELETE /admin/memories/{id}`（soft-delete）、`GET /admin/memories?q=` 检索（ILIKE）
- MemoryView 升级（新增/删除/搜索/类型过滤 + 记忆注入配置卡片）
- 记忆注入强度/开关配置：`GET/PUT /admin/settings/memory`（enabled/inject_limit/extract_interval_turns/eviction 参数，config_runtime 落盘）

### 7.2 工作流自动化
- 会话级 auto_execute + max_rounds：sessions 表幂等迁移（auto_execute BOOL DEFAULT FALSE / max_rounds INT DEFAULT 3）；WS user_message 显式传参覆盖会话配置；_handle_user_message 循环执行（每轮 turn_end）
- HooksSection UI（hooks CRUD 端点已有：GET/POST/PUT/DELETE/events；前端新增列表/表单/编辑/删除）

### 7.3 知识库专业升级
- 切片参数配置：`GET/PUT /admin/knowledge/config`（chunk_size/overlap 各文档类型）
- `POST /admin/knowledge/reindex` 批量重向量化（清空旧 chunk + skip_dedup 重切）
- `POST /admin/knowledge/search_test` 检索测试面板（复用 search_with_rerank 生产链路）
- 网页入库走 MCP 工具链路（不内置抓取器）

### 7.4 高级文件能力
- `POST /admin/files/extract` 解压（zip/tar.gz/tgz，逐条目 resolve 防路径穿越，单文件 100MB 上限，符号链接跳过）
- `GET /admin/files/download_zip?paths=` 批量打包下载（逐条校验 + 目录递归，返回 zip 流）
- 前端 FilePanel：预览区"解压"按钮（压缩包）+ 工具栏"⬇zip 全部打包"

### 7.5 V1.3 验收标准
✅ 记忆自动沉淀/手动管理/跨会话生效；✅ 自动执行多轮 + 钩子稳定 + 轮次上限生效；✅ 知识库调参/重索引/检索调试；✅ 解压与批量导出无异常。

---

## 8. 第四阶段 V1.4 系统优化、备份与体验 ✅（2026-08-05 完成）

> **实现偏差记录（已按工程约定调整）**：
> 1. 备份 zip 含 config_runtime(API Key 密文)/skills 源目录/6 张核心表(sessions/messages/messages_archive/user_memories/kb_documents/react_events)；**kb_chunks 不导出**(向量大)，还原后提示到知识库页重索引重建
> 2. 还原 DB 部分在**单事务**内执行(任一失败整体回滚返回 `restore_failed_rolled_back`)；skills 落盘在提交后做(失败仅警告不阻塞)；datetime 列(以 `_at` 结尾)备份用 ISO 字符串、还原 fromisoformat 转回；config_runtime JSONB str 需 json.loads 再序列化(工程约定)
> 3. 批量导出 = `POST /sessions/export_batch`(md 合并多会话)，单会话导出(已有)复用
> 4. 主题切换覆盖核心语义变量 + 组件硬编码白底少量处保持亮色(暗色校准回归清单部分执行)；系统通知仅**应用在后台**(visibilityState hidden)时提醒，避免前台打扰

### 8.1 导入导出 & 备份体系
- `GET /admin/backup` 全局一键备份（config_runtime + skills + 6 表 zip 下载）
- `POST /admin/backup/restore` 上传还原（单事务回滚保护 + skills 落盘 + chunks 重建提示）
- `POST /admin/sessions/export_batch` 会话批量导出（md/json 合并）
- 前端设置页"数据管理"区块（一键备份下载/上传还原/批量导出+下载合并文件）

### 8.2 模型管理完整体系
- provider 加 group/sort_order/kind 元数据（config_runtime 落盘，group 空串清除）
- 前端 ProviderSection 按 group 分组 + sort_order 排序渲染；行内"本地"徽标（kind=local）；编辑表单加分组/类型（cloud/local）
- 本地模型轻量支持：kind=local 标识 + 已有连通性测试端点，不做显存管理

### 8.3 系统设置完善
- `POST /admin/cache/clear` 清理 outputs 过期产物（按 retention_days）
- `GET/PUT /admin/settings/system`：log_level(log 级别)/log_retention_days(日志保留)/proxy_http|https(网络代理，空串清除)/workspace_root(存储路径)/master_key_configured(状态展示)
- ⚠️ 未做"全局默认参数"（工程约定）

### 8.4 全局体验优化
- 主题切换：design-tokens.css 加 `[data-theme="dark"]` 变量覆盖 + App 顶栏 🌙/☀️ 切换 + localStorage 持久化 + 根背景按主题
- 全局跨模块搜索：Sidebar 搜索框升级（会话/技能/知识库并行搜索，结果分组显示，点击跳转对应视图）
- 提示词模板库（localStorage）：输入区 📋 按钮 + 弹窗（保存当前输入/插入/删除）
- Electron 通知：turn_end/turn_cancelled/error 时 Notification（仅应用在后台时）

### 8.5 V1.4 验收标准
✅ 全套导入导出/备份还原可用且可回滚；✅ 模型分组/本地接入；✅ 存储/代理/日志/加密可配置；✅ 主题/搜索/模板/通知体验闭环。

---

## 9. 实施节奏与工程约定

```
每个闭环单元：后端 API + 幂等迁移 → pytest 后端测试 → 前端组件/视图 → 前端手动验收 → 记录
```
- 迁移约定：schema.sql 同步 + migrations.py 幂等 ALTER（沿用现有模式）
- 测试约定：pytest 必须加载 backend/.env；勿并发操作同一测试库
- 每阶段结束：全量回归（pytest tests/ --ignore=test_eval_full_cycle.py + 前端测试）→ 打包发版

## 10. 风险与注意事项

1. 消息删除与压缩一致性：soft-delete 走 messages_archive，禁止硬删。
2. 归档会话默认排除列表但搜索应覆盖。
3. 文件操作锁定 workspace 内（路径规范化校验）。
4. 图片附件限制单图 ≤5MB、会话内图数提示（token 成本）。
5. 暗色主题需全量校准硬编码色，列入 V1.4 回归清单。
6. git 不可用：改动落盘即生效，每阶段手工快照备份。
