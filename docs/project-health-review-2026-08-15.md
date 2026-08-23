# PA 项目开发计划全面审视报告（2026-08-15）

> 审视范围：蓝图（private-agent-blueprint.md，137 [MVP] / 117 [V2] 标注）、docs/ 全部计划与收尾文档、M2 handoff 27 项遗留、architecture-revision 批次1/2/3、2026-08-05~08-15 全部 next-phase-plan。
> 方法：文档验收标记 + 代码证据双重核对（不凭文档自述）。
> 结论先行：**原定与新增计划均已闭环或明确挂起，无"遗漏未做"的既定计划；真正遗留是 13 项 V2/蓝图级长期项（多为明确暂缓）与 1 项测试基线质量问题。**

---

## 一、已闭环清单（文档 + 代码双确认）

| 计划 | 状态 | 证据 |
|---|---|---|
| 阶段一/二/三（08-04 安全硬边界/最小内核） | ✅ | phase-closeout 确认，已推送 |
| V1.1~V1.4 迭代 | ✅ | iteration-plan 无未勾项 |
| 0.5.0 四窗口并发（08-08） | ✅ | phase-closeout-0.5.0；打包按约定用户手动 |
| 0.5.1 embedding-worker（08-09） | ✅ V1~V7 实测通过 | 计划 §1.2 ★标记；7 装配点代码在位 |
| 0.5.3 LLM 韧性/双链（08-10） | ✅ 已实施 | text/vision_chain（registry）+ 注入预算 + 错误零静默（_emit_error_msg 分类） |
| 技能增强（08-12）三需求 | ✅ 已实施 | / 召唤（supplementary_skills 'slash'）、zip 一键安装（test_admin_skill_upload_zip）、多技能 |
| 自我认知 v2（08-13） | ✅ 已实施 | system_capabilities 工具 + memory_search 修复 |
| 子代理类型并发（08-13） | ✅ 已实施 | subagents.task_type 列 + type 限流（文档标"待评审"滞后） |
| DesignStylist（08-14） | ✅ 已实施 | backend/skills/design-stylist/ + test_design_stylist.py（文档标"待评审"滞后） |
| 插件机制 A-1 事件流（08-15） | ✅ 今天完成 | C-4 去重 + context_injected 五注入点；B 方案明确挂起（触发条件=DSH 生态市场验证） |
| 架构批次1（C-1/C-2/C-3） | ✅ | 提交 1523892 |
| 架构批次2（T-1/T-2/T-3） | ✅ | 路径强制/超时统一/任务集 |
| 架构批次3（C-5/T-4/C-4） | ✅ C-4 今日收口 | 回放 Zone 过滤/MCP 加固/事件级去重 |
| M2 handoff P0（4 项） | ✅ 全部解决 | compress_adapter（phase3 修）/ JS 沙箱（executor 支持 node）/ 流式输出 / 沙箱 UI 面板 |
| M2 handoff P1（7 项） | ✅ 全部解决 | Skill 体系全套 + MCP 2026-07-28 协议升级 |
| M2 handoff P3 评估体系（25-27） | ✅ 已实现 | eval/（hybrid_eval/judge/metrics/models） |

## 二、遗留与未闭环

### A. 文档滞后（实际已完成，仅状态标记未更新）——建议立即同步，10 分钟

1. `architecture-revision.md` C-4 未勾（今天已实施完成）
2. `next-phase-plan-2026-08-13-subagent-type-concurrency.md` 标"待评审"（已实施）
3. `next-phase-plan-2026-08-14-design-stylist.md` 标"待确认后动工"（已实施）
4. `next-phase-plan-2026-08-12-skill-enhancement.md` 标"待确认后执行"（已实施）
5. `next-phase-plan-2026-08-15-plugin-mechanisms.md` "全量测试基线通过"未勾（被既有测试缺陷阻断，见 B.1）

### B. 真实未实现（V2/蓝图级，多为明确暂缓或长期项）

| # | 项 | 来源 | 性质 | 备注 |
|---|---|---|---|---|
| 1 | **全量 pytest 基线转绿** | 8-15 实测 | ⚠️ 质量债 | 既有测试缺陷：test_admin_database:94 断言默认值；schema 污染累积；WS 测试未跟上双链（auto_execute 已修，剩余 admin 系列待清）。**阻碍后续所有版本验证，建议优先** |
| 2 | reranker | 8-09 计划 §八 | 明确暂缓 | 内存/权重成本，留待下阶段 |
| 3 | 查询重写（扩展查询词） | M2 P2-14 | 未做 | 检索精度增强 |
| 4 | GPU 加速（embedding/reranker） | M2 P2-13 | 未做 | device 参数已预留 |
| 5 | 加权融合（可配置向量/关键词权重） | M2 P2-16 | 未做 | 当前 RRF 固定权重 |
| 6 | KB 版本对比与回滚 | M2 P2-15 | 未做 | version_snapshots 表已就绪 |
| 7 | database_query 工具 | M2 P2-18 | 未做 | ToolDef schema 已定义 |
| 8 | shell_execute 工具 | M2 P2-19 | 未注册 | code_execution 已覆盖多数执行需求，需评估是否仍需 |
| 9 | 长任务暂停/恢复（DAG 编排） | M2 P2-20 | 未做 | async_tasks 表就绪；会话级暂停已有（V1.5 项-5），任务级未做 |
| 10 | Docker 容器隔离 | M2 P2-21 | 未做 | SandboxExecutor 抽象已预留 |
| 11 | 沙箱环境快照保存/恢复 | M2 P2-22 | 未做 | SnapshotManager 未实现 |
| 12 | 沙箱依赖缓存（pip 持久化） | M2 P2-23 | 未做 | 工作目录 venv 支持 |
| 13 | 沙箱交互式终端 | M2 P2-24 | 未做 | 流式协议可扩展双向 |

### C. 挂起项（有明确触发条件，非待执行）

- **B 方案插件总线**：DSH 生态市场验证成熟后触发（8-15 文档 §8）
- **打包**：蒋先生手动 build-electron.bat（铁律，AI 不执行）
- **kb_mounts 字段逻辑**：原计划"待 v0.5.1 embedding 完成后联动"——embedding 已完成，**可评估是否仍需要**

## 三、下一阶段建议（按价值/成本排序）

1. ~~**全量测试基线修复**（约 1-2 天）~~ → **✅ 2026-08-15 已完成**（提交 a3ffc73）：conftest 连接池隔离 + 双链适配 + 断言修正；修复后全量 1468 过 / 10 失败全修（8 文件 93 过验证）。
2. ~~**KB 版本对比/回滚**（约 1 天）~~ → **✅ 2026-08-15 已完成**（提交 dc7b429）：save/compare/rollback + 3 处自动快照（4 过 + 现有 65 过）。
3. ~~**查询重写**（约 0.5-1 天）~~ → **✅ 2026-08-15 已完成**（提交 57dca60）：启发式多查询扩展，默认关闭零行为变化（7 过 + 现有 49 过）。
4. ~~**任务级暂停/恢复**（约 1 天）~~ → **✅ 2026-08-15 已完成**（提交 e31b5b4）：subagents.status=paused + 协作式挂起 + 心跳兼容（16 过）。
5. **长期项维持暂缓**：GPU/Docker/交互终端/依赖缓存/reranker/加权融合/database_query/shell_execute——内存 7.6GB 与单机约束下收益低。

## 四、结论

- 原定计划：**全部闭环**（含今天收口的 C-4 事件级去重与 context_injected 审计）。
- 新增计划：8-05~8-15 共 11 份，**已实施 10 份、挂起 1 份（B 方案，触发条件明确）**。
- 无"被遗漏的既定计划"；**本报告提出的 4 项可执行建议已于 2026-08-15 全部完成**（测试基线/KB 版本/查询重写/任务级暂停，提交 a3ffc73/dc7b429/57dca60/e31b5b4）。
- 剩余 V2 长期项（GPU/Docker/交互终端等）为单机约束下明确暂缓，无阻塞项。
