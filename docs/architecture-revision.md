# 架构修订采纳分析 & 上下文与工具管理底层重构实施设计

> 项目：私人智能体（Private Agent）
> 日期：2026-08-04
> 依据：`AUDIT-REPORT.md`（Bug+安全审计）与 `private-agent-深度审查报告.md`（结构性失效审计）
> 背景：近期暴露对话上下文管理与工具管理的底层缺陷（工具配对 400、MCP 调用卡死、思考过重、压缩边界错误），本次基于两份审查报告做采纳分析，并给出以 **P1-7（压缩 Zone 隔离）+ A.2.9（事务化写入）** 为核心的底层重构实施设计。

---

# 第一部分：审查报告采纳分析

## 1. 报告定位与交叉印证

| 报告 | 视角 | 核心结论 |
|---|---|---|
| AUDIT-REPORT | Bug + 安全审计 | 3 个致命：沙箱失效、文件工具路径校验可被 LLM 绕过、admin 无鉴权+CORS 全开；整体工程素养良好、无恶意代码痕迹 |
| 深度审查报告 | 结构性失效审计 | "大量子系统是结构性空壳"：计费死代码、RAG 空壳、记忆读占位符、eval 契约错误；并发一致性缺陷 |

两报告在 **上下文管理** 与 **工具管理** 上交叉印证多个条目，与近期线上问题直接对应。

## 2. 采纳点清单（按域分类）

### 2.1 对话上下文管理（高优先，含治本项）

| # | 来源 | 问题 | 采纳 | 与近期问题的关系 |
|---|---|---|---|---|
| C-1 | P1-7 | 压缩滑动窗口**不区分 Zone**：system prompt / 记忆 / KB 被标 compressed 过滤出 API 上下文，system prompt 甚至被截断进摘要 | ✅ **采纳（核心）** | 上下文混乱的架构根因 |
| C-2 | A.2.9 / P2-14 | assistant + tool 消息写入**非事务**，任一步失败留半残状态 → 模型 400 | ✅ **采纳（核心）** | 直接根治工具配对 400 |
| C-3 | A.3.5 | 压缩只在 final 分支触发，error / 迭代上限路径不压缩 → 上下文无限增长 | ✅ 采纳 | 长对话上下文失控 |
| C-4 | P0-5 | 事件用 turn 粒度 offset 追踪，推送丢失即永久丢；offset 无单调保护 | ⚠️ 采纳（第二阶段） | 重连丢事件 |
| C-5 | P2-6 | 重放取 `role='user'` 无 Zone 过滤 → KB/记忆被重放成"用户气泡" | ✅ 采纳 | 界面/上下文污染 |

### 2.2 工具管理（高优先）

| # | 来源 | 问题 | 采纳 | 关系 |
|---|---|---|---|---|
| T-1 | A.1.4 / B.1.2 | file_read/write 路径校验开关（`data_dir`）由 LLM 决定，省略即跳过校验 | ✅ 采纳（安全硬伤） | 任意文件读写 |
| T-2 | A.2.5 | react_loop 读 `tools.tool_timeout_sec`（yaml 无此键）→ 工具执行恒 120s，配置体系失效 | ✅ 采纳 | 工具执行超时失控 |
| T-3 | P0-4 | `_session_tasks` 单槽覆盖，cancel 打错目标 / 排队 turn 不可取消 | ✅ 采纳 | "停止"按钮失效 |
| T-4 | P2-4 | MCP 工具 `safety_level=none` 绕过权限门；`2026-07-28` 协议臆造 | ✅ 采纳（协议核实） | MCP 链无统一权限 |
| T-5 | A.2.1 / A.2.2 | 权限缓存 key 硬编码 "default"（丢 skill 隔离）；超时即拒绝会污染后续请求 | ✅ 采纳 | 越权/卡确认 |
| T-6 | P2-3 | `chat_stream` 不包 ProviderError → 流式错误不走 fallback | ✅ 采纳（第二阶段） | 400 后降级不可靠 |

### 2.3 安全硬边界（第二阶段，独立批次）

- 沙箱隔离（A.1.1/B.1.1）：Windows Job Object + 网络拦截
- admin 鉴权 + CORS 收窄（A.3.10/B.2.1）
- SSRF 防护（A.2.3/B.1.3）
- 注入防护"移除+告警"（B.2.3/P1-1）

### 2.4 功能复活（第三阶段）

- RAG 打通（P1-8）、记忆复活（P1-9/P2-5）、eval 修正（P0-7/P2-1/P2-2）、计费打通（P0-6）

## 3. 优先级矩阵

```
阶段一（本设计）：上下文管理 C-1/C-2/C-3/C-5 + 工具管理 T-1/T-2/T-3  → 正确性
阶段二：安全硬边界 + C-4 + T-4/T-5/T-6                              → 安全与一致
阶段三：功能复活（RAG/记忆/eval/计费）                                → 能力
```

---

# 第二部分：上下文与工具管理底层重构实施设计

## 4. 设计目标

1. **根治 400**：同轮消息写入事务化（A.2.9），杜绝半残状态
2. **压缩边界正确**：压缩只作用于 Active Zone（P1-7），Frozen/Stable 永不压缩
3. **超时配置生效**：统一工具执行超时来源（A.2.5）
4. **取消目标正确**：per-session task 集合管理（P0-4）
5. **文件工具服务端约束**：路径校验由后端强制注入（A.1.4）

## 5. 模块一：上下文管理重构（P1-7 + A.2.9 + A.3.5）

### 5.1 压缩 Zone 隔离（P1-7）——核心

**现状**：`core/compressor.py:157-188 _sliding_window` 按 `turn < keep_from` 统一标记 compressed，不区分 Zone；Frozen 的 system 消息（无 turn=0）、Stable 的记忆/KB（携带注入时旧 turn）会被误标并过滤出 API 上下文。

**设计**：
```python
# compressor.py _sliding_window 增加 zone 参数
def sliding_window(messages, keep_turns):
    """仅标记 active zone 内 turn < keep_from 的消息为 compressed。
    Frozen/Stable 永不压缩(原则: 系统提示与长期记忆常驻)。"""
    for m in messages:
        if m.get("zone") not in (None, "active"):
            continue  # frozen/stable 跳过
        if m.get("turn", 0) < keep_from and not m.get("compressed"):
            m["compressed"] = True
```

**要点**：
- 压缩输入改用 `get_messages_with_meta()`（含 zone 字段），输出仍按 zone 分组回写
- `_apply_compression`（react_loop.py:983-1033）落库时**只对 active zone 消息**写 `compressed=True`
- **新增保护**：`build_messages` 过滤 compressed 前，断言 zone∈(active) 的消息才可能 compressed（防御性）

### 5.2 同轮写入事务化（A.2.9）——根治 400

**现状**：react_loop Phase C 对每个 plan 独立 `append_tool_message`（各自 INSERT），assistant 消息先写；任一条失败（DB 抖动/取消）→ 残留 assistant(tool_calls) 无对应 tool 消息 → 下次模型调用 400。

**设计**：同轮全部写入包进一个事务：
```python
# react_loop.py Phase A~C 重构: 收集后统一提交
async with self._conn.transaction():
    await self._context_manager.append_assistant_message(conn, ...)  # 含 tool_calls
    for plan in plans:  # Phase B 执行(不落库)后
        await self._context_manager.append_tool_message(conn, ...)
```
- 工具**执行**（Phase B，含权限确认）保持在事务外（长耗时、可能被取消）
- 仅**落库**（assistant + 全部 tool 消息）在一个事务内——失败则整体回滚，DB 不留半残
- `ContextManager.append_*` 增加"批量事务模式"（复用传入的 conn，由调用方管理事务边界）

### 5.3 压缩覆盖所有退出路径（A.3.5）

**设计**：`run_turn` 的所有 return 前（final / error / max_iterations / 工具循环强制终止）统一调 `_maybe_compress()`；用 try/finally 包主循环。

### 5.4 重放 Zone 过滤（C-5 / P2-6）

**设计**：`eval/replay.py` 或重放查询加 `zone='active'` 过滤（仅用户/助手对话重放为气泡，KB/记忆注入不重放）。

## 6. 模块二：工具管理重构（T-1/T-2/T-3）

### 6.1 服务端强制路径校验（T-1 / A.1.4）

**现状**：`file_read.py:58` 等 `if data_dir:` 由 LLM args 决定是否校验。

**设计**：
- `data_dir`/`workspace` 从 ToolDef schema **移除**（不再由 LLM 提供）
- 后端在 `_exec_plan` 执行前，从 `session.workspace` / `cfg.workspace_root` 注入到 handler 内部（新参数通道 `ctx`，不污染 args）
- 校验用 `Path.resolve()` + `is_relative_to()`，拒绝符号链接

### 6.2 超时配置统一（T-2 / A.2.5）

**现状**：react_loop.py:585-589 读 `tools.tool_timeout_sec`（yaml 无此键 → 恒 120s）。

**设计**：
```python
# react_loop 改为按类别分级读取(config 已有 tools.timeout.categories)
timeout_cfg = self._cfg.get("tools", {}).get("timeout", {})
default = float(timeout_cfg.get("default_sec", 30))
cat_cfg = timeout_cfg.get("categories", {})
self._tool_timeouts = {k: float(v) for k, v in cat_cfg.items()}  # 按工具名
# 执行时: timeout = self._tool_timeouts.get(tool_name, default)
```
- 同步校验 `config.yaml` 的 `tools.timeout.*` 与代码读取一致

### 6.3 per-session task 集合管理（T-3 / P0-4）

**现状**：`main.py _session_tasks[sid] = task` 单槽，第二个 user_message 覆盖第一个。

**设计**：
```python
_session_tasks: dict[int, set[asyncio.Task]] = {}
# create_task 时 add; done_callback 时 discard
# cancel 消息: 遍历集合 cancel 全部(或仅最新)
```

## 7. 实施顺序与依赖

```
批次 1(正确性核心): 5.1 压缩 Zone 隔离 → 5.2 事务化写入 → 5.3 压缩全覆盖
批次 2(工具正确性): 6.1 路径强制 → 6.2 超时统一 → 6.3 task 集合
批次 3(一致性): 5.4 重放过滤 → 6.4 MCP 权限门(T-4) → C-4 事件级去重
```
每批次独立提交 + 全量 pytest 回归 + 单会话真机验证（A股场景）。

## 8. 关键文件清单

| 文件 | 改动 |
|---|---|
| `core/compressor.py` | 滑动窗口 zone 隔离（5.1） |
| `core/react_loop.py` | 事务化写入（5.2）、压缩全覆盖（5.3）、超时分级（6.2）、task 集合联动 |
| `core/context_manager.py` | 批量事务模式、zone 断言、data_dir 注入通道（6.1） |
| `tools/builtins/file_read.py` / `file_write.py` / `read_artifact.py` | 移除 args.data_dir、改 ctx 注入（6.1） |
| `main.py` | `_session_tasks` 集合化（6.3） |
| `config/config.yaml` | 校准 tools.timeout 配置（6.2） |
| 测试 | 新增 `tests/test_compressor_zone.py`、`tests/test_transactional_write.py`、`tests/test_tool_timeout.py`；回归 `test_react_loop.py`/`test_compressor.py` |

## 9. 测试方案要点
- **compressor_zone**：frozen/stable 消息永不被标 compressed；active 按 turn 正确标记
- **transactional_write**：注入 DB 故障 → assistant+tools 整体回滚（无半残）；配对完整性断言
- **tool_timeout**：分级超时生效（code_execution 300s / web_search 30s）
- **task 集合**：双 user_message 并发 → 都能被 cancel 命中
- **路径注入**：args 无 data_dir 也能安全（服务端注入）；`../` 逃逸被拒
- 回归：后端 pytest 全量 + 前端 vitest 13

## 10. 风险与兼容
- **压缩行为变化**：Zone 隔离后 Stable 常驻 → 长对话 Stable 增长需 `stable_zone_size_limit` 合并兜底（已有机制）；需回归 `test_compressor.py`
- **事务化**：长事务锁风险低（单轮消息量小）；DB 连接异常时事务自动回滚（符合预期）
- **路径注入**：handler 签名变更影响测试与调用方（code_execution 等）——用默认参数兼容
- **取消集合化**：与"拒绝并发 user_message"策略二选一（设计取集合化，兼容排队）

## 11. 预期效果
- 工具配对 400：**根治**（事务保证无半残，保险箱作为最后兜底保留）
- 上下文正确性：system prompt/记忆/KB 不再被压缩误伤 → 长对话行为稳定
- 超时可控：按工具类别分级，不再"统一 120s"
- 停止按钮：取消目标正确

## 12. 采纳状态跟踪（随实施更新）
- [x] C-1 压缩 Zone 隔离（批次 1，提交 1523892）
- [x] C-2 事务化写入（批次 1，提交 1523892）
- [x] C-3 压缩全覆盖（批次 1，提交 1523892）
- [x] T-1 路径强制（批次 2，待提交）
- [x] T-2 超时统一（批次 2，待提交）
- [x] T-3 task 集合（批次 2，待提交）
- [x] C-5 重放 Zone 过滤（批次 3，待提交）
- [x] T-4 MCP 安全加固（批次 3，待提交）
- [ ] C-4 事件级去重（批次 3）
