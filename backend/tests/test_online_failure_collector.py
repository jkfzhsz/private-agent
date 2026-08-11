"""Phase 3 Task 3.1 - 在线失败案例采集器测试。

验证:
- 工具失败采集: 构建 item 且 failure_reason 含类型前缀 + 详情
- 迭代用尽采集
- 用户纠正采集
- 5 分钟去重窗口: 同 session + 同 failure_type 只写一次
- review_queue 写入异常时 collect 返回 None(不向上抛)
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from private_agent.eval.online_failure_collector import (
    FailureType,
    OnlineFailureCollector,
)


def _added_item(mock_review_queue: AsyncMock) -> dict:
    call = mock_review_queue.add.await_args
    assert call is not None
    return call.kwargs.get("item", call.args[0])


def test_collect_tool_failure():
    """工具执行失败 → 写入审核队列, 断言 failure_reason / scope / 事件摘要。"""
    mock_review_queue = AsyncMock()
    mock_review_queue.add = AsyncMock(return_value=1)
    collector = OnlineFailureCollector(mock_review_queue)

    item_id = asyncio.run(collector.collect(
        session_id=1,
        scope="office",
        user_message="帮我读取文件",
        failure_type=FailureType.TOOL_ERROR,
        failure_detail="file_read 工具执行超时",
        react_events=[
            {"event_type": "tool_call", "payload": {"tool_name": "file_read"}},
            {"event_type": "error", "payload": {"message": "timeout"}},
        ],
        final_output="⚠️ 程序异常：工具执行超时",
    ))

    assert item_id == 1
    mock_review_queue.add.assert_awaited_once()
    item = _added_item(mock_review_queue)
    assert "file_read 工具执行超时" in item["failure_reason"]
    assert item["failure_reason"].startswith("[tool_error]")
    assert item["scope"] == "office"
    assert item["source_session_id"] == 1
    assert item["source_run_id"] is None
    assert item["sample_input"] == "帮我读取文件"
    assert item["actual_output"] == "⚠️ 程序异常：工具执行超时"
    # 事件摘要使用生产键 tool_name
    assert item["actual_events"][0]["tool_name"] == "file_read"
    assert item["failure_type"] == "tool_error"
    assert item["status"] == "pending"


def test_collect_iteration_exhausted():
    """迭代用尽 → 写入审核队列, failure_reason 含"迭代"。"""
    mock_review_queue = AsyncMock()
    mock_review_queue.add = AsyncMock(return_value=2)
    collector = OnlineFailureCollector(mock_review_queue)

    item_id = asyncio.run(collector.collect(
        session_id=1,
        scope="data_analysis",
        user_message="分析这份数据",
        failure_type=FailureType.ITERATION_EXHAUSTED,
        failure_detail="达到最大迭代次数 10",
        react_events=[],
        final_output="⚠️ 能力边界：本轮已达步数上限",
    ))

    assert item_id == 2
    mock_review_queue.add.assert_awaited_once()
    item = _added_item(mock_review_queue)
    assert "迭代" in item["failure_reason"]
    assert item["failure_type"] == "iteration_exhausted"
    assert item["scope"] == "data_analysis"


def test_collect_user_correction():
    """用户纠正 → 写入审核队列。"""
    mock_review_queue = AsyncMock()
    mock_review_queue.add = AsyncMock(return_value=3)
    collector = OnlineFailureCollector(mock_review_queue)

    item_id = asyncio.run(collector.collect(
        session_id=1,
        scope="frontend_design",
        user_message="把这个报告美化一下",
        failure_type=FailureType.USER_CORRECTION,
        failure_detail="用户纠正：要求用深色主题而非浅色",
        react_events=[],
        final_output="已生成浅色主题报告",
    ))

    assert item_id == 3
    mock_review_queue.add.assert_awaited_once()
    item = _added_item(mock_review_queue)
    assert item["failure_type"] == "user_correction"
    assert "用户纠正" in item["failure_reason"]
    assert item["actual_output"] == "已生成浅色主题报告"


def test_deduplicate_repeated_failures():
    """相同失败不应重复采集(同一会话+同一失败类型 5 分钟内去重)。"""
    mock_review_queue = AsyncMock()
    mock_review_queue.add = AsyncMock(return_value=1)
    collector = OnlineFailureCollector(mock_review_queue)

    async def _run() -> None:
        for _ in range(3):
            await collector.collect(
                session_id=1,
                scope="office",
                user_message="读取文件",
                failure_type=FailureType.TOOL_ERROR,
                failure_detail="file_read 超时",
                react_events=[],
                final_output="错误",
            )

    asyncio.run(_run())
    assert mock_review_queue.add.await_count == 1  # 去重

    # 不同失败类型不互相去重
    async def _run2() -> None:
        await collector.collect(
            session_id=1,
            scope="office",
            user_message="读取文件",
            failure_type=FailureType.ITERATION_EXHAUSTED,
            failure_detail="步数超限",
            react_events=[],
            final_output="错误",
        )

    asyncio.run(_run2())
    assert mock_review_queue.add.await_count == 2


def test_collect_survives_repo_failure():
    """review_queue.add 抛异常 → collect 返回 None(不向上抛, 不阻塞对话)。"""
    mock_review_queue = AsyncMock()
    mock_review_queue.add = AsyncMock(side_effect=RuntimeError("disk full"))
    collector = OnlineFailureCollector(mock_review_queue)

    item_id = asyncio.run(collector.collect(
        session_id=1,
        scope="office",
        user_message="读取文件",
        failure_type=FailureType.TOOL_ERROR,
        failure_detail="file_read 超时",
        react_events=[],
        final_output="错误",
    ))

    assert item_id is None
