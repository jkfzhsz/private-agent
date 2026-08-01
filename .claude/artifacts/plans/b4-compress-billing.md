# B4 上下文压缩 + Token 计费 实施方案

> Status: APPROVED
> Source: .claude/artifacts/designs/b4-compress-billing.md
> Mode: default (Planner → Architect → Critic, 1 iteration)
> Iterations: 1 / 3
> Author: user
> Last updated: 2026-08-01

## Requirements summary

B4 修复 M1-b 的 2 个 P0 阻塞项:
- P0-1: 上下文压缩(TokenEstimator + 三类策略:滑动窗口/摘要/Stable Zone 合并)
- P0-4: token 计费(TokenUsage + BillingRecorder: dialogue/compress/eval 三类)

依赖 B1(CHECK 扩容)和 B3(checkpoint 机制),两者均已就位。

## Acceptance criteria

继承自 spec 的 AC-1..AC-12:
- AC-1..8: 上下文压缩(8 条)
- AC-9..11: token 计费(3 条)
- AC-12: 全量 pytest 通过

## RALPLAN-DR

### Principles

- **最小代码**: 新增 2 个模块,修改 3 个文件,总计约 400 行
- **压缩不阻断**: 压缩失败降级为 log warning,不打断会话
- **计费不阻断**: 计费写入失败仅 log warning,不阻断会话
- **配置可关**: `compression.enabled: false` 和 `billing.enabled: false` 可关闭

### Decision drivers

1. **压缩集成点**: `react_loop.py` 每轮结束后调 `maybe_compress`,103 错误时调 `handle_context_overflow`
2. **计费集成点**: 模型调用后(react_loop)、压缩调用后(context_manager)、评估调用后(embedding_service)
3. **模块边界**: TokenEstimator 和 Compressor 可放在 `context_manager.py` 或独立模块,选独立模块保持内聚

### Viable options

**Option A: 压缩在 context_manager.py 中新增 Compressor 类, TokenEstimator 独立模块, 计费独立模块** (favored)
- 实现思路: TokenEstimator 独立模块供全局复用, Compressor 在 context_manager 中集成, BillingRecorder 独立模块
- Pros: 内聚明确, TokenEstimator 可被 Compressor 和 BillingRecorder 共享
- Cons: context_manager.py 文件进一步增大(当前约 340 行,新增约 150 行)

### Implementation steps

**P0-1 TokenEstimator + 压缩(8 步)**

1. 新增 `backend/private_agent/core/token_estimator.py`:
   - `TokenEstimator` 类: CHARS_PER_TOKEN=3.0, estimate(text), estimate_messages(messages)

2. 新增 `backend/private_agent/core/compressor.py`:
   - `Compressor` 类:
     - `maybe_compress(ctx, active_turns, context_window, compress_adapter, conn, session_id, turn)`: 检查触发条件
     - `_sliding_window(ctx, keep_turns=6)`: 标记旧消息 compressed=True
     - `_summarize(ctx, compress_adapter, compressed_msgs)`: 调 compress_adapter 生成摘要
     - `_merge_stable_zone(ctx, compress_adapter)`: 合并 Stable Zone 消息
     - `_count_active_turns(ctx)`: 计算 Active Zone 轮次数

3. 修改 `backend/private_agent/core/context_manager.py`:
   - 在 `ensure_initial` 中初始化 Compressor
   - 新增 `maybe_compress(conn)` 方法,委托给 Compressor

4. 修改 `backend/private_agent/core/react_loop.py`:
   - 每轮 `run_turn` 结束后调用 `context_manager.maybe_compress(conn)`
   - 103 错误(上下文超限)时调用 `Compressor.handle_context_overflow`

5. 修改 `backend/config/config.yaml`:
   - 确认 `context.compression.enabled: true` 存在
   - 确认 `context.compression.active_zone_token_limit: 4000` 存在

**P0-4 Token 计费(6 步)**

6. 新增 `backend/private_agent/core/billing.py`:
   - `TokenUsage` dataclass: input_tokens, output_tokens, total_tokens, cached_tokens
   - `BillingRecorder` 类: record_usage, _calculate_cost

7. 修改 `backend/private_agent/models/base.py`:
   - `ChatResult` 新增 `usage: TokenUsage | None = None` 字段

8. 修改 `backend/private_agent/core/react_loop.py`:
   - 模型调用完成后,调 `BillingRecorder.record_usage(cost_type="dialogue")`

9. 修改 `backend/private_agent/core/compressor.py`:
   - 压缩调用 `compress_adapter.chat` 后,调 `BillingRecorder.record_usage(cost_type="compress")`

10. 新增 `backend/private_agent/api/admin.py`:
    - `GET /admin/billing/summary?session_id=...` 端点,返回三类 cost_type 汇总

11. 修改 `backend/config/config.yaml`:
    - 新增 `billing.enabled: true` 和模型 pricing 配置

**测试(8 步)**

12. `backend/tests/test_token_estimator.py` — 3 测试:
    - `test_estimate_default_ratio`: AC-1
    - `test_estimate_messages_skips_compressed`: AC-2
    - `test_estimate_empty_text_returns_1`

13. `backend/tests/test_compressor.py` — 7 测试:
    - `test_maybe_compress_no_trigger`: AC-3
    - `test_maybe_compress_token_limit`: AC-3
    - `test_maybe_compress_turn_limit`: AC-4
    - `test_sliding_window_keep_turns`: AC-5
    - `test_sliding_window_pairing`: AC-6
    - `test_summarize_calls_adapter`: AC-7
    - `test_compress_writes_react_event`: AC-8

14. `backend/tests/test_billing.py` — 5 测试:
    - `test_record_usage_writes_token_usage_event`: AC-9
    - `test_calculate_cost_input_output`: AC-10
    - `test_calculate_cost_cached_discount`: AC-10
    - `test_record_usage_cost_type_dialogue`
    - `test_record_usage_cost_type_compress`

15. `backend/tests/test_billing_api.py` — 1 测试:
    - `test_billing_summary_returns_three_categories`: AC-11

**验证(1 步)**

16. 全量 `python -m pytest` — 721 现有 + B4 新增,无新增失败

## Risks & mitigations

| Risk | Mitigation |
|---|---|
| `messages` 表无 `compressed` 列 | 先检查 schema.sql + migrations.py,若缺则补 `ALTER TABLE messages ADD COLUMN IF NOT EXISTS compressed BOOLEAN DEFAULT FALSE` |
| 滑动窗口配对逻辑复杂,跨边界 tool_call/tool_result 可能被拆分 | 单测 `test_sliding_window_pairing` 覆盖,用具体 fixture 验证跨 keep_turns 边界的配对不拆分 |
| compress_adapter 在测试环境不可用 | 测试用 mock compress_adapter(AsyncMock),AC-7 验证调用 |
| BillingRecorder 需要 pricing_config 但多模型 pricing 导致复杂度 | MVP 仅用默认定价常量,不在 config.yaml 读多模型,留 V2 |

## Verification steps

- AC-1..2: `python -m pytest tests/test_token_estimator.py -v`
- AC-3..8: `python -m pytest tests/test_compressor.py -v`
- AC-9..10: `python -m pytest tests/test_billing.py -v`
- AC-11: `python -m pytest tests/test_billing_api.py -v`
- AC-12: 全量 `$env:PA_DB_PASSWORD="123123"; $env:PA_TEST_DSN="..."; python -m pytest`

## ADR

- **Decision**: B4 采用独立模块 + 压缩策略在 Compressor 中实现, TokenEstimator 独立供全局复用, BillingRecorder 独立模块
- **Drivers**:
  - 模块内聚(TokenEstimator 被两个模块共享)
  - 蓝图合规(§3.9 三类策略 + §3.13 计费分类)
  - spec In scope 边界(不扩到适配器 usage 提取)
- **Alternatives considered**:
  - 压缩集成到 context_manager.py 中(否决: 文件进一步增大,内聚差)
  - 计费集成到 react_loop.py 中(否决: 计费写入点分散,需独立模块)
- **Consequences**:
  - 正面: B4 完成后 M1-b 全部 P0 项修复完成,M1 完成度从 29% → 57%
  - 负面: context_manager.py 仍需调用 Compressor,有耦合
  - 中性: TokenEstimator 3.0 字符/token 兜底对中文高估,但不影响安全性

## Review trail

- Planner draft v1: 2 个新增模块, 3 处集成点, 16 步实施计划, 4 条 risks
- Architect challenge v1:
  - Steelman: "压缩应该在 context_manager.py 中实现,因为压缩操作上下文分区,与 context_manager 职责重叠" → 反驳成立但 Compressor 独立模块提供清晰的 API 边界和可测试性,context_manager 通过委托调用
  - Tradeoff: 模块内聚 vs 文件大小 — 选独立模块,内聚优先
  - Synthesis: 不需要综合,Option A 已是最小路径
- Critic verdict v1: APPROVED
- Reservations:
  1. **Reservation 1**: `messages` 表可能无 `compressed` 列。Mitigation: step 1 前先检查,缺则补 migration
  2. **Reservation 2**: 滑动窗口配对逻辑跨边界,实现复杂易错。Mitigation: 单测 `test_sliding_window_pairing` 先写,用具体 fixture 验证
  3. **Reservation 3**: BillingRecorder 需要 `ChatResult.usage` 字段,但适配器目前不填充。Mitigation: MVP 仅定义字段,适配器填充留 B2 或后续,计费测试用 mock TokenUsage

- Final iterations: 1 / 3