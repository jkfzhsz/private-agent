# B3 注入防护 + Checkpoint 实施方案

> Status: APPROVED
> Source: .claude/artifacts/designs/b3-injection-protection-checkpoint.md
> Mode: default (Planner → Architect → Critic, 1 iteration)
> Iterations: 1 / 3
> Author: user
> Last updated: 2026-08-01

## Requirements summary

B3 修复 M1-b 的 2 个 P0 阻塞项:
- P0-2: 注入防护(三层: role 隔离已有 + 长度截断 + 关键词过滤),新增 `core/injection_guard.py`,集成到 `react_loop.py`
- P0-3: checkpoint + interrupted 标记,新增 `core/checkpoint.py`,集成到 `react_loop.py` + `main.py`

依赖 B1 的 CHECK 扩容(已就绪),完成后解锁 B4(P0-1 压缩 + P0-4 计费)。

## Acceptance criteria

继承自 spec 的 AC-1..AC-12:
- AC-1..7: 注入防护(7 条)
- AC-8..11: checkpoint(4 条)
- AC-12: 全量 pytest 通过

## RALPLAN-DR

### Principles

- **最小代码**: 新增 2 个模块,仅修改 3 个文件,总计约 300 行
- **告警不阻断**: 注入检测命中不打断 ReAct 循环,checkpoint 写入失败不抛异常
- **配置可关**: `injection_guard.enabled: false` 关闭全部注入防护,便于调试
- **测试优先**: 每个新模块先写测试,再写实现

### Decision drivers

1. **集成点选择**: `react_loop.py` 是唯一工具执行入口,注入扫描和 checkpoint 都应在此集成
2. **模块边界**: `injection_guard.py` 和 `checkpoint.py` 独立,无相互依赖,可并行开发
3. **降级安全**: 注入扫描/checkpoint 写入失败不能阻断会话,必须 try/except 包裹

### Viable options

**Option A: 注入扫描在 react_loop.py 中集成,checkpoint 在 react_loop.py 和 main.py 双集成** (favored)
- 实现思路: `react_loop.py` `run_turn` 中 tool_result 产出后立即 scan + truncate;每轮结束后 save_checkpoint;`main.py` `WebSocketDisconnect` 分支调 mark_session_interrupted
- 改动文件: `core/injection_guard.py`(新增), `core/checkpoint.py`(新增), `core/react_loop.py`, `main.py`, `config/config.yaml`
- Pros: 集成点单一明确,不扩散到 `sandbox/service.py` 或 `tools/` 层
- Cons: `react_loop.py` 需要访问 cfg(目前不持有),需新增 cfg 参数

**Invalidation rationale for 其他选项**:
- "注入扫描在 sandbox/service.py 中集成" → 仅覆盖沙箱工具,不覆盖 MCP/HTTP 工具,违反蓝图 §3.12 对 MCP 工具的要求
- "checkpoint 仅在 main.py 中写" → 违反蓝图 §2.14 "每轮结束自动写入",断连时才写会丢失正常结束的中间 checkpoint

### Implementation steps

**P0-2 注入防护(6 步)**

1. 新增 `backend/private_agent/core/injection_guard.py` — 模块文件:
   - `InjectionAlert` dataclass: pattern/call_id/risk/source/snippet
   - `InjectionScanResult` dataclass: high_alerts/low_alerts
   - `InjectionGuard` 类:
     - `HIGH_RISK_PATTERNS`: 7 条正则(蓝图 §3.12 原文)
     - `LOW_RISK_PATTERNS`: 2 条正则
     - `MAX_TOOL_RESULT_TOKENS_MCP = 4000`, `MAX_TOOL_RESULT_TOKENS_SANDBOX = 2000`
     - `truncate_tool_result(result, source)`: 按 token 估算截断(3 字符/token)
     - `scan(tool_result, call_id, source)`: 遍历 HIGH_RISK + LOW_RISK 正则
     - `is_enabled(cfg)`: 读 `cfg["injection_guard"]["enabled"]`(默认 True)

2. 修改 `backend/private_agent/core/react_loop.py` — 构造函数新增 `cfg: dict` 参数:
   ```python
   def __init__(self, ..., cfg: dict | None = None) -> None:
       self._cfg = cfg or {}
   ```
   在 `run_turn` 中,`tool_result` event 产出后(L190-200 附近),`append_tool_message` 之前,插入:
   ```python
   guard = InjectionGuard()
   if guard.is_enabled(self._cfg):
       tool_output = tool_result.output or ""
       source = "sandbox" if tool_name == "code_execution" else "mcp"
       truncated = guard.truncate_tool_result(tool_output, source)
       scan_result = guard.scan(truncated, tool_call_id, source)
       if scan_result.high_alerts:
           for alert in scan_result.high_alerts:
               await self._emit_event("injection_alert", payload={
                   "pattern": alert.pattern, "call_id": alert.call_id,
                   "risk": alert.risk, "source": alert.source,
                   "snippet": alert.snippet,
               })
   ```

3. 修改 `backend/private_agent/main.py` — WS endpoint 中 `ReactLoop(...)` 构造传入 `cfg=cfg`(L220-230 附近)

4. 修改 `backend/config/config.yaml` — 新增:
   ```yaml
   injection_guard:
     enabled: true
   ```

**P0-3 Checkpoint(5 步)**

5. 新增 `backend/private_agent/core/checkpoint.py` — 模块文件:
   - `CheckpointManager` 类:
     - `save_checkpoint(conn, session_id, turn, ctx_summary)`: 写入 `react_events`(event_type='checkpoint'),payload 含 turn + ctx_summary(frozen_zone_len/stable_zone_len/active_zone_msg_count/active_zone_turn_range)
     - `mark_session_interrupted(conn, session_id)`: `UPDATE sessions SET status='interrupted' WHERE id=$1`

6. 修改 `backend/private_agent/core/react_loop.py` — `run_turn` 末尾(return 前或 loop 结束后),调用:
   ```python
   try:
       ctx_summary = {
           "frozen_zone_len": len(self._context_manager.frozen_zone.messages),
           "stable_zone_len": len(self._context_manager.stable_zone.messages),
           "active_zone_msg_count": len(self._context_manager.active_zone.messages),
           "active_zone_turn_range": [self._turn, self._turn],
       }
       await CheckpointManager.save_checkpoint(
           self._conn, self._session_id, self._turn, ctx_summary
       )
   except Exception as e:
       self._logger.warning(f"checkpoint save failed: {e}")
   ```

7. 修改 `backend/private_agent/main.py` — `WebSocketDisconnect` 分支(L253):
   ```python
   except WebSocketDisconnect:
       try:
           conn = await db.connect()
           try:
               await CheckpointManager.mark_session_interrupted(conn, session_id)
           finally:
               await conn.close()
       except Exception:
           _logger.exception("Failed to mark session interrupted on disconnect")
       pass
   ```
   注: `session_id` 需要从外层 scope 可访问,将 `conn = await db.connect()` 之前的 `session_id` 提取到 `try` 块外部

8. 修改 `backend/private_agent/main.py` — `user_message` 异常路径(L244-248):
   ```python
   except Exception:
       _logger.exception("user_message handling failed")
       try:
           await CheckpointManager.mark_session_interrupted(conn, session_id)
       except Exception:
           pass
       await ws.send_json({"type": "error", "message": "user_message_failed"})
   ```

**测试(9 步)**

9. 新增 `backend/tests/test_injection_guard.py` — 6 个测试:
   - `test_scan_high_risk_chinese`: AC-1 中文高危命中
   - `test_scan_high_risk_english`: AC-2 英文高危命中
   - `test_scan_low_risk_only`: AC-3 低风险命中, high_alerts 为空
   - `test_scan_clean_text`: AC-4 无害文本返回空
   - `test_truncate_mcp_4000`: AC-5 MCP 截断 4000
   - `test_truncate_sandbox_2000`: AC-5 沙箱截断 2000

10. 新增 `backend/tests/test_injection_guard_db.py` — 1 个测试:
    - `test_high_alert_writes_react_event`: AC-7 真实 DB 写入 `injection_alert` event_type

11. 新增 `backend/tests/test_checkpoint_manager.py` — 3 个测试:
    - `test_save_checkpoint_writes_react_event`: AC-8 写入 checkpoint event
    - `test_save_checkpoint_payload_excludes_full_messages`: AC-9 payload 仅含摘要
    - `test_mark_session_interrupted_updates_status`: AC-10 sessions.status='interrupted'

12. 新增 `backend/tests/test_main_ws_disconnect.py` — 1 个测试:
    - `test_ws_disconnect_marks_session_interrupted`: AC-11 模拟 WS 断连,验证 status

   实现方式: 不依赖真实 WebSocket,用 `asyncpg` 直接创建 session,模拟 `CheckpointManager.mark_session_interrupted` 调用,验证 DB 状态

**验证(1 步)**

13. 全量 `python -m pytest` — 707 现有 + B3 新增 11 测试,无新增失败

## Workspace setup

- 当前 working tree 有 B1 修复未提交(dirty),但 B3 与 B1 无文件冲突(新增文件不同)
- 继续在当前目录开发,不入新 worktree

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| `react_loop.py` 新增 cfg 参数后,现有测试(如 `test_react_loop.py`)的 `ReactLoop(...)` 调用会报 TypeError | 测试中 `ReactLoop(...)` 调用不加 cfg(默认 None),`is_enabled` 返回 True(默认值),注入扫描正常执行但扫描结果为空(clean tool_result) |
| 注入扫描正则可能误报(合法文本含 "system:" 等) | 低风险模式仅日志,不推送前端;`injection_guard.enabled: false` 可关闭 |
| `main.py` `session_id` 在 `WebSocketDisconnect` 分支不可访问 | 将 `session_id = int(msg["session_id"])` 提取到 `try` 块外部 |
| checkpoint 写入失败时 `react_loop.py` 抛异常阻断会话 | 整个 save_checkpoint 包 try/except,失败仅 log warning |

## Verification steps

- AC-1..6: `python -m pytest tests/test_injection_guard.py -v`
- AC-7: `python -m pytest tests/test_injection_guard_db.py -v`
- AC-8..10: `python -m pytest tests/test_checkpoint_manager.py -v`
- AC-11: `python -m pytest tests/test_main_ws_disconnect.py -v`
- AC-12: 全量 `$env:PA_DB_PASSWORD="123123"; $env:PA_TEST_DSN="..."; python -m pytest`

## ADR

- **Decision**: B3 采用独立模块 + 单点集成方案,注入扫描和 checkpoint 均在 `react_loop.py` 集成,`main.py` 仅处理 WebSocketDisconnect 的 interrupted 标记
- **Drivers**:
  - 集成点单一(决定不在 sandbox/tools 层分散集成)
  - 蓝图合规(§3.12 三层防护 + §2.14 每轮 checkpoint)
  - spec In scope 边界(不扩到 V2 断点续传)
- **Alternatives considered**:
  - 注入扫描在 sandbox/service.py 中集成(否决: 仅覆盖沙箱,不覆盖 MCP 工具)
  - checkpoint 仅在 main.py 断连时写(否决: 违反蓝图 §2.14 "每轮结束自动写入")
- **Why chosen**: 最小代码路径,集成点明确,两个模块独立无相互依赖
- **Consequences**:
  - 正面: B3 完成后解锁 B4(P0-1 压缩需要 injection_alert event_type,P0-4 计费需要 checkpoint 机制)
  - 负面: `react_loop.py` 新增 cfg 参数,调用方需更新(仅 main.py 一处)
  - 中性: `injection_guard.enabled` 配置项成为永久配置
- **Follow-ups**:
  - B4: 注入扫描结果可用于压缩策略(高危工具结果优先压缩)
  - V2: 从 checkpoint 恢复 ReAct 循环(断点续传执行器)

## Review trail

- Planner draft v1: 2 个新模块 + 3 处集成点,13 步实施计划(6+5+9+1),4 条 risks + mitigations
- Architect challenge v1:
  - Steelman: "注入扫描应该在 `tools/` 层做,而非 `react_loop.py`,因为未来可能有非 ReAct 的调用路径" → 反驳成立时需重构工具调用链;但 MVP 仅 ReAct 一条路径,蓝图 §3.12 也明确"工具结果回灌前"扫描,react_loop 是唯一回灌点
  - Tradeoff tension: 模块内聚 vs 调用链耦合 — 选模块内聚(独立 injection_guard.py + checkpoint.py),react_loop 只在回灌前/循环后调用,耦合度低
  - Synthesis: 不需要综合,Option A 已是最小路径
  - Principle violations: 无
- Critic verdict v1: APPROVED
  - Principle consistency ✓ (4 项 Principles 均与 Option A 一致)
  - Alternative exploration ✓ (1 favored + invalidation rationale)
  - Risk mitigation clarity ✓ (4 条 risk 均有具体 mitigation)
  - AC testability ✓ (12 条 AC 均有对应测试 step)
  - Verification concreteness ✓ (每条 AC 给出具体 pytest 命令)
  - File/line coverage ✓ (13 步均 cite 具体文件路径)
- Reservations:
  1. **Reservation 1**: `react_loop.py` 构造函数新增 cfg 参数后,现有 `test_react_loop.py` 的 `ReactLoop(...)` 调用可能因缺少 cfg 参数报 TypeError。Mitigation: cfg 默认 None,现有测试不传即可;但需在 step 13 全量验证确认无回归。
  2. **Reservation 2**: `main.py` 中 `session_id` 变量在 `WebSocketDisconnect` 分支不可访问(当前在 `user_message` 分支内部定义)。Mitigation: step 7 已明确"提取到 try 块外部",需在实施时验证。
  3. **Reservation 3**: `test_injection_guard_db.py` 需要真实 DB 连接,与 `test_injection_guard.py`(纯函数)测试风格不同,混合在同一文件会增加 fixture 复杂度。建议拆分为两个文件,plan 已按此分拆。

- Final iterations: 1 / 3