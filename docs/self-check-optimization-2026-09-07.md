# 自检优化方案执行报告（2026-09-07）

> 依据：docs/self-check-2026-09-07.md（四场景全面自检）
> 执行：WorkBuddy 主代理（无子代理）| 批准项：B2 方案②、C2 评估后更优才启用、生产写入授权两项
> 结果：S1-S8 全部完成，全量回归 1872 passed（2 失败均已归因修复）

---

## 一、总体目标达成情况

**把"已建未验/已建未用"的沉没投资转化为已验证资产** —— 三个 P0 空转机制中两个（记忆管线、embedding 监控）本轮直接闭环；Mission 层验收（A1）需蒋先生打包后操作，按计划挂起。

## 二、逐步执行记录

| 步骤 | 缺陷 | 改动内容 | 涉及文件 | 验证 | 结果 |
|---|---|---|---|---|---|
| S1（A2） | P0-#2/#5 | 归因诊断（只读） | manager.py / main.py / 生产库取证 | DB：自动提取全库仅触发 3 次、仅产 1 条泛化记忆；会话均值 1.57~3.7 轮 | **根因三层**：①`on_session_end` 全仓零调用；②提取 prompt 用占位符冒充对话历史；③非 office 特有，全场景管线空转（scope 归因无辜） |
| S2（A3） | P0-#3 | 探针文档首触发验证 | 临时探针脚本（已删） | system_metrics 出现 6 行 kind='kb'（worker_ok=1、512→1024 padding、冷启 40.2s） | **PASS**，此前无数据纯因 08-27 后无导入，非装配缺陷 |
| S3（B1） | P1-#4 | import_kb_dirs.py 增 health-wiki 源 | backend/scripts/import_kb_dirs.py | 导入 12 篇/114 chunks；检索命中语义正确（过敏→allergy-management、心脑血管运动→cardio 三篇） | 清和 PG KB 3→15 篇，全局 chunks 708→822 |
| S4（B4） | P0-#2/#5 | ①接真实对话历史 ②WS 断连接线会话结束提取 | memory/manager.py、memory/memories_repo.py、main.py | 33+8 定向测试全过；全量回归护航 | 见下方"修复设计" |
| S5（B2②） | P1-#6 | 修正 profile 漂移 + 双向锚点一致性测试 | skills/harness.py、tests/test_monitor_profile_consistency.py | 23 项锚点测试 + harness 17 测试全过 | **发现并已修真实漂移**：profile"一切改动需审批" vs md"低风险直接做"矛盾 |
| S6（B3） | P1-#7 | config.yaml=0.6.0 单一版本源，前端对齐 + 守护测试 | config.yaml、package.json、package-lock.json、tests/test_version_consistency.py | 3 测试过；旧 test_config_system_version 断言已更新 | 三处版本统一 |
| S7（C4） | P2-#9 | EnvSanitizer 增注入类变量精确名阻断（10 项，恒阻断） | sandbox/security.py、test_sandbox_security.py | 26 沙箱测试全过 | PYTHONPATH/NODE_OPTIONS 等不再透传沙箱 |
| S8（C2） | P2-#11 | 12 组 ground truth 查询 A/B 评估 | 临时评估脚本（已删） | recall@1：开=关=4/12；recall@5：开=关=6/12，**零差异** | 按既定规则**不启用**（中文变体近乎原串，混合检索已覆盖） |

## 三、S4 修复设计（本轮核心）

**内容层**：`MemoryManager._extract_memories` 此前用 `[session_id=X, turn=Y]` 占位符充当对话历史 → 现经 `MemoriesRepo.get_recent_dialogue()`（新增）取最近 16 条 user/assistant 消息，`_load_dialogue_window()` 单条截 300 字符、总量封 4000，无历史时兜底占位（防御）。

**触发层**：main.py 抽取 `_build_memory_manager(conn, cfg)` 共享构造（消除双份装配）；`WebSocketDisconnect` 分支接线 `_session_end_memory_extract()`——守卫：距上次间隔提取的尾部轮次 ≥2 才提取（office 主体形态的 1 轮短会话跳过，控 LLM 成本），memory_enabled=False 跳过，失败静默不影响断连。

**测试**：test_memory_manager.py +5（真实历史进 prompt/兜底/截断/空对话/scope 兜底不变）；新建 test_session_end_memory_extract.py 8 项（守卫全分支 + 异常静默）。

## 四、全量回归（21m27s，--basetemp=D:/Private agent/.pytest-tmp）

**1872 passed / 2 failed**，两失败均已归因：
1. `test_config_system_version` 硬编码 0.1.0 —— S6 预期变红，已更新断言为 0.6.0（单一版本源纪律写入 docstring）；
2. `test_delegate_parallel_two_subtasks` —— 负载抖动（隔离复跑 subagent 全文件 16/16 过；本次改动未触碰 subagent 链路）。

## 五、遗留（交蒋先生）

| 项 | 内容 | 需要您做什么 |
|---|---|---|
| A1 | Mission 层真实链路验收 | 打包后走真实路径创建 1 个长任务 mission 观察 |
| C1 | compress_model / judge_model 配置 | 拍板选模型（压缩目前主模型兼任） |
| C3 | 白圭批次4 风险问卷 UI | 裁定是否启动 |
| C5 | 卫生清理（.bak/测试残留会话/注释对齐） | 确认后执行 |
| commit | 本轮 11 文件改动 + 3 新测试文件 | **未提交，待您批准**（建议原子拆分：S4 记忆管线 / S5 无涯一致性 / S6 版本统一 / S7 沙箱阻断 / S3 KB 源） |
