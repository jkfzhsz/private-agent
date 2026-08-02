# Private Agent V2 工程完整度验收报告

**验收日期**: 2026-08-02
**验收 HEAD**: `b57b0fe`(master)
**验收范围**: V2 四项工作 = P1 沙箱执行收尾 + P2 MCP G2 深化 + P3 去预置化 + 上下文工程质量子集
**核对方式**: 真实代码 + git 提交对照 + 全量 pytest/vitest + 真实 WS 端到端冒烟 + 真实库迁移验证

---

## 执行摘要

V2 阶段共 5 个提交(见下表), 全部验收通过。后端测试从 V1.5 基线 546 增至 **869 passed**, 前端 13 passed; 真实 WS 对话冒烟验证了模型→工具→流式输出→最终回答的全链路; 真实库迁移(messages.reasoning_content 列、runtime fallback_chain)均已生效。V2 交接时的唯一前端技术债(sidecar.test.ts tsc 类型错误)已顺手修复, `tsc --noEmit` 完全干净。

**总体完整度: 4/4 项完全完成, 遗留 0 项阻塞。**

---

## V2 提交与验收对照表

| # | 提交 | 工作项 | 验收结论 |
|---|---|---|---|
| 1 | `d070bb5` | V2 P1 沙箱执行收尾 | ✅ 完全完成 |
| 2 | `6fa8960` | V2 P2 MCP G2 深化 | ✅ 完全完成 |
| 3 | `bc024bb` | V2 P3 去预置化 | ✅ 完全完成 |
| 4 | `55ca3e9` | V2 上下文工程质量子集 | ✅ 完全完成 |
| 5 | `b57b0fe` | 技术债: sidecar.test.ts 类型错误 | ✅ 已修复 |

---

## 1. V2 P1 沙箱执行收尾(提交 d070bb5)

| 验收项 | 实现证据 | 结果 |
|---|---|---|
| 沙箱流式输出 | `executor.py` read(4096) 4KB 分片 + on_output 回调 → `react_loop` 注入 `_on_output` 发 `sandbox_output` 事件(persist=False 仅 WS 不入库防事件风暴) | ✅ |
| 权限确认链路 | `PermissionManager.check_and_confirm`: 60s 超时自动拒绝 + 会话级缓存; outcome 四态 auto/approved/denied/timeout | ✅ |
| 工具分级 | `ToolDef.safety_level`(none/elevated/dangerous), `code_execution=elevated`; 仅 elevated 走 WS 确认 | ✅ |
| WS 确认消息 | 新 WS 消息 `tool_confirmation` 路由到 PermissionManager.resolve; react_events 白名单+CHECK 扩容 `tool_confirmation_required/result` | ✅ |
| B1 修复 | `set_sandbox_config` 注入生产路径(此前 code_execution 必报 "Sandbox not configured") | ✅ |
| B2 修复 | WS 主循环 user_message 改 `create_task` + per-session 锁, 运行期间可收确认消息 | ✅ |
| 测试 | test_permission_manager/test_react_loop_confirmation/test_sandbox_streaming 等, P1 新增 19 测试 | ✅ 全过 |

## 2. V2 P2 MCP G2 深化(提交 6fa8960)

| 验收项 | 实现证据 | 结果 |
|---|---|---|
| assemble 装配开关 | `mcp_tools.get_tools` 过滤 assemble!=False; `PUT /admin/settings/mcp/{name}/assemble` 持久化; 前端 McpRow "装配到对话" checkbox | ✅ |
| 工具速查指南 | `build_tools_guide`: 按 server 分类工具名速查注入 system prompt(读 tools cache 不重复连接; 静态稳定 KV Cache 友好; frozen_hash 变化由 replace_frozen_zone 兜底) | ✅ |
| 同轮 tool_call 并行 | Phase A 串行解析+权限确认 → Phase B `asyncio.gather` + `Semaphore(config tools.mcp.concurrent_limit=5)`, code_execution 串行避沙箱竞态 → Phase C 按原始顺序产出 tool_result | ✅ |
| 单工具失败不中断 | 未知工具/异常 → error 回传, 不中断整轮(旧语义整轮 ERROR 已改) | ✅ |
| qcc-document-mcp 删除 | 真实库 17→16 server(HTTP 版 qcc-document 保留) | ✅ |
| 测试 | test_mcp_assemble(6) + test_react_loop_parallel(3) | ✅ 全过 |

## 3. V2 P3 去预置化(提交 bc024bb)

| 验收项 | 实现证据 | 结果 |
|---|---|---|
| 删专用 Adapter | `models/adapters/{glm,deepseek,kimi}.py` 已删(目录仅剩 `__init__.py`) | ✅ |
| 统一动态注册 | `registry.ensure_registered` + `_make_factory(name, OpenAICompatibleAdapter)`, 全量运行时注册 | ✅ |
| 按模型名匹配 | `build_adapter_for_model_name(cfg, model_name)`: compress/judge 遍历 providers 匹配; 无匹配 → None 优雅降级 | ✅ |
| yaml 清空 | config.yaml providers 清空(仅注释示例), fallback_chain/compress_model/judge_model/fallback_cloud 置空 | ✅ |
| 真实库迁移 | runtime fallback_chain → `["deepseek-flash"]`(glm/kimi 过滤); compress_model=None(配置后自动启用) | ✅ |
| 前端引导 | 设置页空状态"添加第一个模型"(名称+Base URL+模型名+API Key) | ✅ |
| 测试 | test_providers_empty(9) 等, 全量 852 passed | ✅ 全过 |

## 4. V2 上下文工程质量子集(提交 55ca3e9)

### 4a. reasoning_content 回传(AI-Agents-in-Depth §2.3.1)

| 验收项 | 实现证据 | 结果 |
|---|---|---|
| 表结构 | messages/messages_archive 加 `reasoning_content TEXT`; migrate_all 幂等补列 | ✅ 真实库列已存在 |
| 持久化 | `append_assistant_message` 持久化 reasoning_content(含 tool_calls 分支, RETURNING id) | ✅ |
| 恢复 | `reload_from_db` 恢复 reasoning_content(续聊后历史思考原样回传) | ✅ |
| 内部字段剥离 | `get_messages` 仅输出 OpenAI 兼容字段(role/content/reasoning_content/tool_calls/tool_call_id/name), 剥离 zone/turn/msg_id/compressed(蓝图 §3.2 硬约束); 新增 `get_messages_with_meta` 供压缩内部使用 | ✅ |
| 双接口约定 | get_messages(API) / get_messages_with_meta(内部压缩) 职责分离 | ✅ |

### 4b. Agent 状态栏(AI-Agents-in-Depth §2.6)

| 验收项 | 实现证据 | 结果 |
|---|---|---|
| 纯代码维护 | `core/status_bar.py`: 工具调用计数(按名聚合)+失败计数+时间戳+状态/轮次/迭代, 键值对格式 `<agent_status>`(非散文) | ✅ |
| 注入点 | ReactLoop 每轮构建消息后追加 user-role meta 消息(仅内存不持久化, 追加末尾不破坏 KV Cache 前缀) | ✅ |
| 生命周期 | turn 开始 reset(防跨轮污染); record_tool_call/record_tool_result 在 Phase A/C 挂钩 | ✅ |
| 可配置 | config `context.status_bar.enabled/inject_per_turn`(默认开) | ✅ |

### 4c. 上下文压缩链路(AI-Agents-in-Depth §2.7.4 + 蓝图 §3.9)

| 验收项 | 实现证据 | 结果 |
|---|---|---|
| **潜伏 bug 修复** | `_maybe_compress` 引用未初始化 `self._token_estimator`(AttributeError 被 try/except 吞, 压缩事件从未真正入库) → `__init__` 初始化 | ✅ |
| 真实执行压缩 | `Compressor.execute`: 滑动窗口标记旧轮 compressed + 有 compress_adapter 时生成摘要(插入 active 头部) | ✅ |
| 摘要失败降级 | execute 摘要异常 → 降级纯滑动窗口 + 返回 summary_error(不中断对话) | ✅ |
| 落库+内存回写 | `_apply_compression`: DB `UPDATE messages SET compressed=TRUE` + 摘要 INSERT compressed_from + **按 msg_id 回写内存原消息**(浅拷贝坑已修) | ✅ |
| 过滤压缩消息 | `get_messages` 过滤 compressed=True(不进 API, 原文保留可恢复) | ✅ |
| 熔断器 | 连续摘要失败 3 次禁用本会话压缩(§2.7.4 第 5 层, 防反复烧钱) | ✅ |
| 压缩适配器注入 | main.py 传 `_build_compress_adapter(cfg)`(按 compress_model, 无则 None 纯滑动窗口) | ✅ |
| 事件入库 | compress 事件正确写入 react_events(trigger=token_limit/turn_limit) | ✅ |

---

## 测试结果

```
后端: 869 passed, 5 warnings in 409.74s (基线 546 → V2 后 869, 新增 323)
前端: 13 passed (vitest)
tsc : --noEmit 完全干净(0 错误, b57b0fe 修复后)
```

V2 新增测试分布:
- P1 沙箱/权限: test_permission_manager(10) + test_sandbox_streaming(6) + test_react_loop_confirmation(3)
- P2 MCP: test_mcp_assemble(6) + test_react_loop_parallel(3)
- P3 去预置化: test_providers_empty(9) 等
- 上下文工程质量: test_status_bar(6) + test_reasoning_content_roundtrip(5) + test_compression_execute(6)

## 端到端冒烟(真实环境)

临时后端(8770 端口, 真实 DB + deepseek-flash + 真实 MCP 16 server) WS 冒烟:

```
[OK] ping/pong
[事件分布] {"thinking": 166, "tool_call": 1, "tool_result": 1, "delta": 83, "final": 1, "turn_end": 1}
[OK] final 内容(158 字): 经 web_search 第三次检索...建议直接访问中国气象局官网...
[统计] thinking=166 delta=83 tool_call=1 tool_result=1
[OK] 工具调用: ['web_search']
```

全链路验证: 模型主动调用工具 → 工具结果回传 → 流式逐块输出 → 最终回答 → turn_end 正常。冒烟测试会话已清理(900001-900003 已删)。

## 真实库状态确认

| 项 | 值 | 状态 |
|---|---|---|
| messages.reasoning_content 列 | 已存在(迁移生效) | ✅ |
| runtime fallback_chain | `["deepseek-flash"]` | ✅ |
| runtime compress_model | None(配置后自动启用摘要) | ✅ |
| MCP servers | 16(7 iFind + 9 qcc, qcc-document-mcp 已删) | ✅ |

## 遗留项与技术债

| # | 事项 | 状态 |
|---|---|---|
| 1 | sidecar.test.ts:113 mock exit handler 类型错误(V2 交接遗留) | ✅ 已修复(b57b0fe), tsc 干净 |
| 2 | 蓝图远期 [V2] 项(JS 沙箱/Docker 隔离/沙箱 UI 面板等) | 不属于本次 V2 阶段范围, 留待后续里程碑 |

## 结论

**V2 四项工作全部完成且验收通过。** 核心能力: 沙箱流式+权限确认链路、MCP 装配/指南/并行、开放式 LLM 接入、上下文工程质量(reasoning_content 回传 / Agent 状态栏 / 真实压缩链路) 均已落地并经真实环境验证。测试基线 869 passed(后端) + 13 passed(前端), 工作区干净。
