# 蓝图非远期内容开发完整度核对报告

**核对日期**: 2026-08-02
**核对对象**: private-agent-blueprint.md(454KB, 8 章 + 137 处 [MVP] / 117 处 [V2] 标注)
**核对方法**: 蓝图 [MVP] 标注逐项对照真实代码(grep/文件核对) + M2-COMPLETION-HANDOFF 移交清单 + 历次验收报告(acceptance-report.md/v2/v3)

---

## 结论

**核心功能(8 章主体)已全部开发完毕并经测试验证; 但严格对照蓝图 [MVP] 标注, 仍存在 5 个非远期缺口**(均为"功能完整度"问题, 不影响已验收的核心链路)。

- 蓝图 §9.7 MVP 验收 30 项维度: 28/30 通过, 2 项边界(V2 前状态) → V2 后权限确认运行时已接入, 仅剩跨平台未实测
- 阶段 Done Criteria 36/36 完成(acceptance-report-v3)
- 后端测试 869 passed, 前端 13 passed

---

## 一、已开发完毕(✅)

| 领域 | 覆盖内容 | 证据 |
|---|---|---|
| 架构/通信 | Electron+Sidecar、WS 8765、三区 KV Cache 模型、hash 校验、磁盘告警、日志文件+stdout | M0 验收 5/5 |
| 模型 | 开放式接入(无预置)、FallbackChain 降级、动态注册、reasoning_content 回传 | V2 P3 + 上下文工程 |
| 上下文工程 | 分区模型、frozen hash 校验、状态栏(新增)、压缩滑动窗口+摘要、注入防护、模板变量五类命名空间({{user.name}}/{{now}}/{{session.*}}/{{skills.*}})、checkpoint、计费三类 | V2 上下文工程子集 |
| 记忆 | 提取(memory_extracted 事件)、注入 top10、淘汰逻辑(数量上限+重要性+超期+软删除) | M2 4/4 |
| 知识库 | RAG 全栈(bge-m3/small 自动切换、HNSW、混合检索 RRF、reranker)、文档流水线、增量更新 | M2 4/4 |
| 工具层 | 9 类内置 + MCP 双轨(2026-07-28/2025-11-25 自动协商)、权限确认运行时(60s 超时+会话缓存)、artifact、白名单、同轮并行 | V2 P1/P2 |
| 沙箱 | Python/JS 执行、流式输出(4KB 分片 WS 推送)、资源限制(300s/512MB/100MB/禁网络)、安全(预扫描+脱敏) | V2 P1 + B2/B5 |
| Skills | 三场景(办公/数据分析/前端设计)、加载器、选择器、白名单、少样本、版本锁定 | M3 8/8 |
| 评估(后端) | 环境/数据集/五类指标/Judge/离线+回放+Mock/版本对比/闭环/回滚 | M4 8/8 |
| 配置 | yaml + config_runtime + AES 加密 | M0 3/3 |

## 二、未开发完毕(❌ 非远期 [MVP] 缺口, 5 项)

| # | 缺口 | 蓝图章节 | 现状 | 来源 |
|---|---|---|---|---|
| 1 | **沙箱 UI 配置面板** | §6.14 `[MVP]` "完整配置段+运行时覆盖+UI 配置实现" | 无 `GET/PUT /api/sandbox/config`、`POST /api/sandbox/test` 端点, 前端无配置面板; config.yaml 段+runtime 覆盖已有 | M2 handoff P0-4(归属 M3, 未做) |
| 2 | **KB 片段注入 Stable Zone** | §4.15 `[MVP]` "工具返回的 KB 片段由 context_manager 注入 Stable Zone + 20 条计数器" | search_knowledge 结果走普通 tool message(active zone), 未注入 Stable Zone, 无计数器 | 实现偏差 |
| 3 | **Stable Zone 合并压缩** | §3.10.3 `[MVP]` "三类压缩策略全量实现(含 Stable Zone 合并存档)" | compressor.py 注释"合并(留 V2)", 未实现每 N=5 轮合并 + 快照存档(与 #2 互为因果: 无 KB 注入则合并无对象) | 实现偏差 |
| 4 | **memory_evicted 淘汰事件** | §4.4 `[MVP]` "淘汰事件记录(event_type=memory_evicted)" | 淘汰仅软删除, 未写 react_events(白名单无 memory_evicted, 只有 memory_extracted) | 实现遗漏 |
| 5 | **压缩存档** | §3.10 `[MVP]` "压缩存档(soft delete + snapshot + hash 备份)" | 压缩时仅 UPDATE compressed + INSERT 摘要, 未写 messages_archive / version_snapshots(ttl_cleanup 只清不写) | 实现遗漏 |

## 三、边界与说明(⚠️)

| 项 | 判定 |
|---|---|
| 跨平台(macOS/Linux) | 非代码缺口, Windows 全链路实测通过, 未三平台实测(测试覆盖边界) |
| 评估可视化前端(EvalPanel) | 蓝图标注 `[V2]`("UI 层新增评估可视化面板增强"/"评估回放可视化面板"), 属远期项; 后端评估 API 完整, 前端无面板符合远期除外 |
| A/B 测试 | `[MVP]` 仅 eval_runs.variant 字段预留 ✅(schema 已有); 完整框架 `[V2]` |
| shell_execute / database_query / 加权融合 / GPU / Docker / 沙箱快照等 | 蓝图明确 `[V2]` 标注, 属远期项(本次除外) |

## 四、缺口间关联

#2 与 #3 是同一机制的上下游: 蓝图设计为 search_knowledge 结果注入 Stable Zone → 长期会话 Stable Zone 膨胀 → 每 5 轮合并压缩 + 快照存档。当前实现绕过了 Stable Zone(检索结果进 active 工具消息), 该链路整体未落地。#4/#5 是独立的轻量遗漏(各约 20 行代码)。

## 五、建议

1. 若追求蓝图 [MVP] 字面达标, 优先补齐 #4(memory_evicted 事件)+ #5(压缩存档写 messages_archive), 成本低
2. #1 沙箱 UI 面板属独立功能(3 个端点 + 1 个前端面板)
3. #2/#3 涉及设计决策(KB 注入 Stable Zone vs 工具消息), 建议先确认是否沿用蓝图方案, 再实施
4. 跨平台冒烟可在后续里程碑安排
