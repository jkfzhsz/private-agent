# B4 上下文压缩 + Token 计费 Spec

> Status: ALIGNED
> Author: user
> Last updated: 2026-08-01

## Background

B4 是 P0-P1 修复方案第四批次,修复 M1-b 阶段的 2 个 P0 阻塞项:
- P0-1: 上下文压缩完全缺失(三类策略,蓝图 §3.9/§3.10/§3.11)
- P0-4: token 计费完全缺失(三类计费,蓝图 §3.13/§3.14)

依赖 B1 的 P0-8(CHECK 扩容)和 B3 的 checkpoint 机制,两者均已就位。

## In scope

### P0-1 上下文压缩
- 新增 `core/token_estimator.py` — TokenEstimator 类(3.0 字符/token 兜底)
- `context_manager.py` 新增 `Compressor` 类(或集成进 ContextManager):
  - `maybe_compress()` — 检查 token 超限/轮次超限触发压缩
  - `handle_context_overflow()` — API 103 错误紧急压缩
  - 三类策略:滑动窗口 / 摘要 / Stable Zone 合并
- `react_loop.py` 每轮结束后调 `maybe_compress` + 103 错误时调 `handle_context_overflow`
- 压缩后 hash: Stable Zone 合并更新 `base_stable_hash`,Active Zone 滑动窗口不改 hash

### P0-4 token 计费
- 新增 `core/billing.py` — TokenUsage dataclass + BillingRecorder
- 三类计费: dialogue(对话模型调用) / compress(压缩模型调用) / eval(评估/embedding 调用)
- 写入 `react_events` 表 event_type='token_usage'
- 各适配器提取 API 响应的 usage 字段
- 新增 `GET /admin/billing/summary?session_id=...` 端点

## Out of scope

- tiktoken 注册(留 V2,TokenEstimator 用字符数兜底)
- 价格版本快照(version_snapshots scope=model_pricing)
- 适配器 usage 提取(仅 ChatResult 加 usage 字段,适配器具体实现留 B2 或后续)
- 压缩模型适配器接入(compress_adapter 已存在,复用)
- 前端计费展示面板

## Assumptions

1. `compress_adapter` 已通过 `build_compress_adapter()` 构造,在 `main.py` 和 `context_manager.py` 中可用
2. `config.yaml` 中 `context.compression.active_zone_token_limit: 4000` 等配置已存在
3. `react_events` CHECK 约束已含 `compress` 和 `token_usage` 事件类型(B1 完成)
4. `messages` 表有 `compressed BOOLEAN DEFAULT FALSE` 列(需确认,若缺则 migration 补)
5. `ChatResult` 需新增 `usage: TokenUsage | None` 字段

## Solution

### P0-1 TokenEstimator

```python
class TokenEstimator:
    CHARS_PER_TOKEN = 3.0
    
    def estimate(self, text: str, model_id: str | None = None) -> int:
        return max(1, int(len(text) / self.CHARS_PER_TOKEN))
    
    def estimate_messages(self, messages: list[dict]) -> int:
        total = 0
        for m in messages:
            if m.get("compressed"):
                continue
            content = m.get("content", "") or ""
            total += self.estimate(content)
            for tc in m.get("tool_calls", []):
                total += self.estimate(json.dumps(tc))
        return total
```

### P0-1 压缩触发条件

```python
def maybe_compress(self, ctx) -> bool:
    estimator = TokenEstimator()
    tokens = estimator.estimate_messages(ctx)
    active_turns = self._count_active_turns()
    
    if tokens > self.context_window * 0.8:
        self._apply_compression("token_limit")
        return True
    if active_turns > 10:
        self._apply_compression("turn_limit")
        return True
    return False
```

### P0-1 三类压缩策略

1. **滑动窗口**(默认): 保留最近 6 轮,标记旧消息 `compressed=True`
2. **摘要**: 调用 `compress_adapter.chat(summary_prompt)` 生成摘要消息
3. **Stable Zone 合并**: 每 5 轮或 Stable Zone > 20 条时,合并所有 stable 消息

### P0-4 BillingRecorder

```python
class BillingRecorder:
    def __init__(self, conn, pricing_config: dict): ...
    def record_usage(session_id, turn, model_id, usage, cost_type) -> None: ...
    def _calculate_cost(model_id, usage, cost_type) -> float: ...
```

## Edge cases & risks

| Category | Notes |
|---|---|
| 压缩触发 | 压缩不阻断会话,失败降级为 log warning |
| 滑动窗口配对 | tool_call/tool_result 配对不可拆分,跨边界需扩展 keep_from_turn |
| hash 一致性 | Active Zone 滑动窗口只标 compressed 不删,不改 hash |
| token 估算 | 3.0 字符/token 兜底,中文约 1.5 字符/token 会被高估但安全 |
| 计费写入 | 写入失败不阻断会话,仅 log warning |

## Acceptance criteria

- AC-1 (P0-1): TokenEstimator.estimate 返回 len(text)//3
- AC-2 (P0-1): TokenEstimator.estimate_messages 跳过 compressed 消息
- AC-3 (P0-1): maybe_compress token 超限(>0.8*context_window)触发压缩
- AC-4 (P0-1): maybe_compress 轮次超限(>10 轮)触发压缩
- AC-5 (P0-1): 滑动窗口保留最近 6 轮,旧消息标记 compressed=True
- AC-6 (P0-1): 滑动窗口 tool_call/tool_result 配对不拆分
- AC-7 (P0-1): 摘要模式调用 compress_adapter.chat
- AC-8 (P0-1): 压缩事件写入 react_events(event_type='compress')
- AC-9 (P0-4): BillingRecorder.record_usage 写入 react_events(event_type='token_usage')
- AC-10 (P0-4): _calculate_cost 正确计算 input/output/cached 三类单价
- AC-11 (P0-4): GET /admin/billing/summary 返回三类 cost_type 汇总
- AC-12 (闭环): 全量 pytest 通过(721 现有 + B4 新增,无新增失败)

## Core entities

| Entity | Type | Key fields |
|---|---|---|
| TokenEstimator | class | CHARS_PER_TOKEN=3.0, estimate(), estimate_messages() |
| Compressor | class | maybe_compress(), handle_context_overflow(), _sliding_window(), _summarize(), _merge_stable_zone() |
| TokenUsage | dataclass | input_tokens, output_tokens, total_tokens, cached_tokens |
| BillingRecorder | class | record_usage(), _calculate_cost(), pricing_config |