# M2 阶段完成标记与移交事项日志

> 完成时间: 2026-08-01
> 分支: master (所有 M2 改动已合并)
> 测试: 443 tests collected (全部通过)
> 当前工作目录: d:\Private agent

---

## M2 完成范围

M2 覆盖蓝图第 4 章(记忆与知识库)+ 第 5 章(工具层)+ 第 6 章(沙箱),以下为已实现项:

### 第 4 章 — 记忆与知识库层 (全部 MVP 实现)

| 模块 | 状态 | 关键文件 |
|------|------|----------|
| 记忆策略(LLM 摘要提取,每 8 轮+会话结束) | ✅ | `memory/manager.py` |
| 记忆存储(结构化条目,四类 type, importance) | ✅ | `memory/memories_repo.py` |
| 记忆淘汰(数量上限+低重要性超期淘汰+软删除) | ✅ | `memory/manager.py` |
| 记忆注入(top 10 注入 Stable Zone + 访问记录) | ✅ | `memory/manager.py`, `core/context_manager.py` |
| 文档处理流水线(端到端+Worker 纯计算+云端降级) | ✅ | `knowledge/kb_service.py` |
| 类型识别(Markdown/PDF/Code/Plain 四类) | ✅ | `knowledge/document_processor.py` |
| Chunking(三类语义+固定长度兜底) | ✅ | `knowledge/document_processor.py` |
| Embedding(bge-m3 本地+Worker 集成+云端降级) | ✅ | `knowledge/embedding_service.py` |
| HNSW 索引(m=16/ef_construction=128/ef_search=64) | ✅ | `knowledge/kb_repo.py` |
| kb_chunks 表(统一表+四类索引+metadata) | ✅ | `knowledge/kb_repo.py` |
| 混合检索(向量+关键词+RRF 融合) | ✅ | `knowledge/kb_repo.py` |
| Reranker(bge-reranker 重排+降级) | ✅ | `knowledge/reranker_service.py` |
| Agentic RAG(search_knowledge 工具+Stable Zone 注入) | ✅ | `tools/builtins/search_knowledge.py` |
| 增量更新(文档变更增量重算+批量快照) | ✅ | `knowledge/kb_service.py` |
| 管理端点(stats + upload + extract_memory) | ✅ | `api/admin.py` |

### 第 5 章 — 工具层 (MVP 核心实现)

| 模块 | 状态 | 关键文件 |
|------|------|----------|
| 双轨架构(内置+MCP 统一调度) | ✅ | `tools/registry.py` |
| MCP Client(stdio+HTTP 双传输) | ✅ | `tools/mcp_client.py` |
| 工具发现(按会话加载+隔离) | ✅ | `tools/registry.py` |
| 9 类通用工具(含 search_knowledge/code_execution 等) | ✅ | `tools/builtins/` |
| 权限确认(三级分级+WS 确认+会话缓存) | ✅ | `tools/authorizer.py` |
| 超时重试(分类超时+指数退避) | ✅ | `tools/retry.py` |
| Artifact 机制(截断+文件存储+read_artifact) | ✅ | `tools/artifact.py` |
| 安全机制(白名单+审计+资源限额+信号量) | ✅ | `tools/security.py` |

### 第 6 章 — 沙箱代码执行 (MVP 核心实现)

| 模块 | 状态 | 关键文件 |
|------|------|----------|
| 沙箱架构(独立模块+透明接口) | ✅ | `sandbox/service.py` |
| 子进程隔离模式 | ✅ | `sandbox/executor.py` |
| 工作目录(会话隔离+7 天保留+白名单互通) | ✅ | `sandbox/workspace.py` |
| Python 语言支持 | ✅ | `sandbox/executor.py` |
| 文件工作记忆(跨轮次持久+artifact) | ✅ | `sandbox/workspace.py` |
| 资源限制(超时 300s+内存 512MB+磁盘 100MB) | ✅ | `sandbox/service.py` |
| 安全边界(三层兜底+环境变量脱敏) | ✅ | `sandbox/security.py` |
| 执行流程(端到端+错误码映射) | ✅ | `sandbox/service.py` |
| 事件记录(react_events) | ✅ | `sandbox/service.py` |

---

## 移交 M3-M4 处理的事项

### P0 — 必须完成 (阻塞 M3 场景正确运行)

| # | 事项 | 蓝图章节 | 说明 | 归属 |
|---|------|---------|------|------|
| 1 | **compress_adapter 暂缺** | §4.2 | `MemoryManager` 初始化时 `compress_adapter=None`,记忆提取依赖 LLM 压缩模型但未绑定实际适配器,导致提取功能实际不可用 | M3 |
| 2 | **JavaScript 沙箱未实现** | §6.5 | 蓝图 MVP 要求 Python + JavaScript 双语言,当前仅实现 Python,前端设计场景(Skill)依赖 JS 沙箱 | M3 |
| 3 | **沙箱流式输出未实现(实时 WS 推送)** | §6.10 | 当前采用执行完成后一次性返回 stdout/stderr,蓝图要求实时分片推送,影响前端 UX 体验 | M3 |
| 4 | **沙箱 UI 配置面板未实现** | §6.14 | 当前仅支持 config.yaml + runtime 配置覆盖,蓝图要求 UI 可视化配置面板 | M3 |

### P1 — 高优先级 (M3 场景增强,建议 M3 早期完成)

| # | 事项 | 蓝图章节 | 说明 | 归属 |
|---|------|---------|------|------|
| 5 | **Skill 定义格式与目录结构** | §7.1 | 三场景目录结构(office/data_analysis/frontend_design)未创建,skill.yaml/skill.yaml 等元数据未定义 | M3 |
| 6 | **Skill 加载器未实现** | §7.3 | Skill 从 Postgres 或文件系统加载、版本化、回滚功能未实现 | M3 |
| 7 | **Skill 选择器/调度器未实现** | §7.5 | 会话启动时 Skill 选择、UI 路由、会话锁定机制未实现 | M3 |
| 8 | **三场景专用 Prompt 未编写** | §7.6 | 办公/数据分析/前端设计各场景的 system_prompt.md 未编写 | M3 |
| 9 | **工具白名单机制未实现** | §7.7 | Skill 级别的工具白名单(tools.yaml)未实现,当前所有工具对所有会话可见 | M3 |
| 10 | **少样本注入未实现** | §7.8 | Skill 级别的 examples/ 少样本注入机制未实现 | M3 |
| 11 | **MCP 2026-07-28 协议升级** | §5.3 | 当前锁定 MCP 2025-11-25 协议,需升级兼容(含 AuthProtocol/TasksExtensionAdapter stub) | M3 |

### P2 — 中优先级 (功能增强,可在 M3 中穿插或延至 M4)

| # | 事项 | 蓝图章节 | 说明 | 归属 |
|---|------|---------|------|------|
| 12 | **Agentic Memory(Agent 主动 remember/recall)** | §4.17 V2 | Agent 主动调用工具进行记忆操作,当前仅被动提取 | M3/M4 |
| 13 | **GPU 加速(embedding/reranker)** | §4.17 V2 | 当前默认 CPU 推理,Worker 进程模型已预留 device 参数 | M3/M4 |
| 14 | **查询重写(模型扩展查询词)** | §4.17 V2 | 检索前可插入重写步骤,提升检索精度 | M3/M4 |
| 15 | **知识库版本对比与回滚** | §4.17 V2 | 快照已存储,对比逻辑和回滚逻辑未实现 | M3/M4 |
| 16 | **加权融合(可配置向量/关键词权重)** | §4.17 V2 | 当前 RRF 固定权重,可替换为可配置加权融合 | M3/M4 |
| 17 | **Tool Marketplace(动态安装 MCP server)** | §5.18 V2 | MCP 配置 UI 已支持,安装逻辑未实现 | M3/M4 |
| 18 | **数据库查询工具(database_query)** | §5.18 V2 | ToolDef schema 已定义,实现未完成 | M3/M4 |
| 19 | **Shell 执行工具(shell_execute)** | §5.18 V2 | dangerous 列表已支持,工具未注册 | M3/M4 |
| 20 | **长任务暂停/恢复(DAG 编排)** | §5.18 V2 | async_tasks 表已支持状态扩展,暂停/恢复逻辑未实现 | M3/M4 |
| 21 | **Docker 容器隔离** | §6.16 V2 | SandboxExecutor 抽象基类已预留,容器后端未实现 | M3/M4 |
| 22 | **沙箱环境快照保存/恢复** | §6.16 V2 | SnapshotManager 接口未实现 | M3/M4 |
| 23 | **沙箱依赖缓存(pip install 持久化)** | §6.16 V2 | 工作目录支持虚拟环境,缓存逻辑未实现 | M3/M4 |
| 24 | **沙箱交互式终端** | §6.16 V2 | 流式协议可扩展双向,运行时输入未实现 | M3/M4 |

### P3 — M4 专属 (评估闭环)

| # | 事项 | 蓝图章节 | 说明 | 归属 |
|---|------|---------|------|------|
| 25 | 评估环境与数据集管理 | §8.1-§8.4 | 离线/在线评估环境,数据集版本管理 | M4 |
| 26 | 五类评估指标实现 | §8.5-§8.9 | 正确性/效率/安全/质量/成本指标 | M4 |
| 27 | LLM-as-Judge 实现 | §8.10 | Judge prompt,评分标准,多 Judge 投票 | M4 |
| 28 | 三类载体迭代闭环 | §8.11-§8.13 | Prompt/Skill/知识库迭代,回滚机制 | M4 |
| 29 | 评估结果可视化 | §8.14-§8.16 | 评估看板,趋势分析,预警阈值 | M4 |

---

## 当前测试基线

```
443 tests collected (全部通过)
```

**关键测试文件**: `test_react_loop.py`, `test_context_manager.py`, `test_memory_manager.py`, `test_memories_repo.py`, `test_knowledge_services.py`, `test_kb_repo.py`, `test_document_processor.py`, `test_search_knowledge_tool.py`, `test_tools_lifecycle.py`, `test_sandbox_service.py`, `test_code_execution_tool.py`, `test_mcp_client.py`, `test_registry.py`

---

## 配置状态

- `config.yaml`: 含 memory/knowledge/sandbox/tools 完整配置段
- `config_runtime`: 运行时配置覆盖机制已就位
- 数据库: PostgreSQL 16 + pgvector 0.8.6, 所有表结构已迁移

---

## 已知风险

1. **compress_adapter 缺失**: 记忆提取依赖 LLM 压缩模型,当前未绑定实际适配器,需 M3 早期修复
2. **pgvector 索引**: HNSW 索引参数(m=16/ef_construction=128)为开发期默认值,生产环境需按数据量调优
3. **bge-m3/bge-reranker 模型下载**: 首次 embedding/rerank 需下载模型文件,建议在 M3 开始前预下载
4. **MCP 协议版本**: 当前锁定 2025-11-25,如 M3 需要 MCP 2026-07-28 新特性需升级