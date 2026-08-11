# Bug: test_detect_same_tool_loop 断言与 2026-08-10 same_tool 修复语义冲突

> Status: FIXED
> Mode: --quick
> Severity: functional（测试套件红，实现行为正确）
> Author: jkfzhsz
> Last updated: 2026-08-11

## Symptom
`test_detect_same_tool_loop` 断言失败：`assert None == 'same_tool'`。测试用 5 个完全不同的参数（query0~query4）连续调用 `web_search`，期望第 5 次触发 `same_tool` 循环检测，实际返回 `None`。

## Expected
2026-08-10 修复后，参数各异的高频同工具调用（如批量记忆多只股票）应视为合法批量操作放行（返回 None）；只有参数高度重复（种类 < 3）的高频调用才判死循环。测试断言应与修复后语义一致。

## Reproduction
- 命令：`cd backend && python -m pytest tests/test_react_loop_loop_detection.py::test_detect_same_tool_loop -v`
- 测试位置：`backend/tests/test_react_loop_loop_detection.py:133`
- 复现稳定性：3/3 reliably fails

## Hypotheses & diagnosis
| # | Hypothesis | Verdict | Evidence |
|---|---|---|---|
| H1 | 2026-08-10 修复在 same_tool 分支新增 `len(set(same_tool_keys)) < 3` 条件，测试用 5 个不同参数（`len(set)=5`）不满足该条件，故返回 None；测试断言滞后于修复语义 | confirmed (root cause) | 逐次调用追踪表：第 5 次 window 长度 5 ≥ 5 ✓、count=5 ≥ 5 ✓、但 `len(set)=5`，`5 < 3` 为 False → 返回 None；实现行为正确，测试断言过时 |

## Root cause
2026-08-10 为修复"批量记忆多只股票被误判死循环"（蒋先生反馈），在 `_detect_tool_loop` 的 same_tool 分支新增 `len(set(same_tool_keys)) < 3` 条件（react_loop.py:1807），要求"参数种类 < 3"才判死循环。但 `test_detect_same_tool_loop` 仍用 5 个完全不同参数期望触发 same_tool，与修复后语义直接冲突。这是**测试断言滞后**，非实现 bug。

## Fix
- 改动文件：`backend/tests/test_react_loop_loop_detection.py`
- 一句话改了什么：
  1. 修正 `test_detect_same_tool_loop` 参数序列为 2 种参数交替（`["query0","query0","query1","query1","query0"]`），满足"参数种类 < 3"触发条件
  2. 新增 `test_same_tool_allows_distinct_args_batch` 回归测试：6 个完全不同参数应放行（返回 None），锁死 2026-08-10 修复语义
- 代码 diff 摘要：
  - 原：`for i in range(4): assert ... {"q": f"query{i}"} is None` + `assert ... {"q": "query4"} == "same_tool"`
  - 新：`params = ["query0","query0","query1","query1","query0"]` + 第 5 次断言 `== "same_tool"`
  - 新增测试：6 个 `{"key": f"stock_{i}"}` 全不同，断言全 `is None`

## Verification
- V-1: failing test → GREEN ✓（2 passed in 2.19s）
- V-2: stash fix → test 重新 RED ✓（1 failed in 1.69s，证明原断言确实抓到行为差异）；pop 后 → GREEN ✓（2 passed in 2.28s）
- V-3: 修改文件所在 package 全部 test → all GREEN ✓（test_react_loop_loop_detection.py 7 passed；ReactLoop 相关 5 文件 58 passed）

## Regression test
- 路径：`backend/tests/test_react_loop_loop_detection.py`
- 名称：`test_same_tool_allows_distinct_args_batch`（新增，防止未来回退到"批量记忆误判"）
- 名称：`test_detect_same_tool_loop`（修正，覆盖"参数种类 < 3 触发 same_tool"的正确语义）

## Pattern analysis
本次 root cause 模式形状：**行为修复后测试断言未同步更新**。

| 搜索方式 | 命中数 | 是否本次同类隐患 |
|---|---|---|
| `grep -n "len(set(" backend/private_agent/core/react_loop.py` | 2 | 均为本次修复相关逻辑（same_args 的 `==1` 与 same_tool 的 `<3`），无其他同类隐患 |
| `git log --oneline --since="2026-08-09" -- backend/tests/` | 多次提交 | 2026-08-10 修复提交（`feat(backend): 核心功能累积`）未同步更新本测试 |

无同类隐患需单独处理。

## Open questions / Follow-ups
- 无。本次 fix 仅改测试断言，未动实现代码，无邻近代码风险。
