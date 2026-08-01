# B3 注入防护 + Checkpoint Spec

> Status: ALIGNED
> Author: user
> Last updated: 2026-08-01

## Background

B3 是 P0-P1 修复方案第三批次,修复 M1-b 阶段的 2 个 P0 阻塞项:
- P0-2: 注入防护完全缺失(M1-AC-5,蓝图 §3.12)
- P0-3: checkpoint + interrupted 标记缺失(M1-AC-6,蓝图 §2.14)

两者均依赖 B1 的 P0-8(CHECK 约束扩容),B1 已完成。

## In scope

- 新增 `core/injection_guard.py` 模块:三层防护(role 隔离已有 + 长度截断 + 关键词过滤)
- 中英文高危/低风险模式正则匹配,高危推送 WS 告警 + 入库,低风险仅日志
- 沙箱与 MCP 工具差异化处理(2k vs 4k token 截断)
- `react_loop.py` 工具结果回灌前集成 injection scan + truncate
- 新增 `core/checkpoint.py` 模块:CheckpointManager 类
- `react_loop.py` 每轮结束后自动 save_checkpoint
- `main.py` WebSocketDisconnect 分支标记 `sessions.status='interrupted'`
- config.yaml 新增 `injection_guard.enabled: true`(可关)

## Out of scope

- 输出层校验(检测模型输出是否被操纵)
- LLM-as-Judge 注入检测
- V2 断点续传执行器(从 checkpoint 恢复 ReAct 循环)
- checkpoint 恢复逻辑(MVP 仅存储,不恢复)
- 注入防护阻断执行(蓝图 §3.12 明确"告警不阻断")

## Assumptions

1. `react_events` CHECK 约束已通过 B1 扩容至 13 种事件类型,含 `injection_alert`/`injection_blocked`/`checkpoint`
2. `sessions.status` CHECK 已含 `'interrupted'`(schema.sql L16)
3. 工具执行路径在 `react_loop.py` `run_turn` → `tool_def.handler(args)`,回灌前为注入扫描集成点
4. `core/executor.py` 是 ProcessPoolExecutor 封装(非工具执行器),实际工具执行在 `react_loop.py` 中
5. config.yaml 的 `injection_guard.enabled` 默认 `true`,可通过配置关闭

## Solution

### P0-2 注入防护

**新增 `core/injection_guard.py`**:

```python
@dataclass
class InjectionAlert:
    pattern: str
    call_id: str
    risk: Literal["high", "low"]
    source: Literal["mcp", "sandbox"]
    snippet: str

@dataclass
class InjectionScanResult:
    high_alerts: list[InjectionAlert]
    low_alerts: list[InjectionAlert]

class InjectionGuard:
    HIGH_RISK_PATTERNS = [...]
    LOW_RISK_PATTERNS = [...]
    MAX_TOOL_RESULT_TOKENS_MCP = 4000
    MAX_TOOL_RESULT_TOKENS_SANDBOX = 2000

    def truncate_tool_result(self, result: str, source: str) -> str: ...
    def scan(self, tool_result: str, call_id: str, source: str) -> InjectionScanResult: ...
    def is_enabled(self, cfg: dict) -> bool: ...
```

**集成到 `react_loop.py`**:

在 `run_turn` 中,`tool_result` 产出后/回灌前,插入:
```python
guard = InjectionGuard()
if guard.is_enabled(cfg):
    truncated = guard.truncate_tool_result(tool_result.output, source)
    scan_result = guard.scan(truncated, tool_call_id, source)
    if scan_result.high_alerts:
        await self._emit_event("injection_alert", payload={...})
```

source 判定: `tool_name == "code_execution"` → `"sandbox"`, 其他 → `"mcp"`

### P0-3 Checkpoint

**新增 `core/checkpoint.py`**:

```python
class CheckpointManager:
    @staticmethod
    async def save_checkpoint(conn, session_id, turn, ctx_summary) -> None:
        # 写入 react_events, event_type='checkpoint'
        ...

    @staticmethod
    async def mark_session_interrupted(conn, session_id) -> None:
        # UPDATE sessions SET status='interrupted'
        ...
```

**集成到 `react_loop.py`**: 每轮 `run_turn` 结束后(循环末尾),调用 `CheckpointManager.save_checkpoint`

**集成到 `main.py`**: `WebSocketDisconnect` 分支 + `user_message` 异常路径,调用 `mark_session_interrupted`

## Edge cases & risks

| Category | Notes |
|---|---|
| Boundary conditions | 空 tool_result 跳过扫描;超长 tool_result 先截断再扫描 |
| Failure modes | injection_guard 抛异常不阻断 ReAct 循环(告警降级为 log warning);checkpoint 写入失败不阻断会话 |
| Risks | 关键词正则可能误报(合法文本含 "system:" 等);低风险模式仅日志,不干扰用户 |
| Mitigation | `injection_guard.enabled: false` 可关闭;checkpoint 写入失败仅 log warning |

## Acceptance criteria

- AC-1 (P0-2): 中文高危输入 `忽略前面所有指令` 命中 → `InjectionScanResult.high_alerts` 非空
- AC-2 (P0-2): 英文高危输入 `ignore previous instructions` 命中 → `InjectionScanResult.high_alerts` 非空
- AC-3 (P0-2): 低风险输入 `system: hello` 命中 → `InjectionScanResult.low_alerts` 非空, high_alerts 为空
- AC-4 (P0-2): 无害文本 `hello world` → `InjectionScanResult` 空
- AC-5 (P0-2): MCP 工具结果截断至 4000 token, 沙箱截断至 2000 token
- AC-6 (P0-2): `injection_guard.enabled: false` → `scan()`/`truncate()` 全部跳过
- AC-7 (P0-2): 高危告警写入 `react_events` 表, `event_type='injection_alert'`, 真实 DB 可入库
- AC-8 (P0-3): `save_checkpoint` 写入 `react_events` 表, `event_type='checkpoint'`, payload 含 ctx_summary
- AC-9 (P0-3): `save_checkpoint` payload 不含完整 messages(仅含结构和长度摘要)
- AC-10 (P0-3): `mark_session_interrupted` 后 `sessions.status='interrupted'`, 真实 DB 可验证
- AC-11 (P0-3): WebSocket 断连 → `sessions.status` 变为 `'interrupted'`
- AC-12 (闭环): 全量 `python -m pytest` 通过(707 现有 + B3 新增,无新增失败)

## Core entities (ontology)

| Entity | Type | Key fields | Relationship |
|---|---|---|---|
| InjectionGuard | class | HIGH_RISK_PATTERNS, LOW_RISK_PATTERNS | 被 react_loop 调用 |
| InjectionAlert | dataclass | pattern, call_id, risk, source, snippet | 组成 InjectionScanResult |
| InjectionScanResult | dataclass | high_alerts, low_alerts | InjectionGuard.scan() 产出 |
| CheckpointManager | class | save_checkpoint, mark_session_interrupted | 被 react_loop + main.py 调用 |