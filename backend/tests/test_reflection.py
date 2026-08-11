"""ReflectionEngine 测试 - 任务完成后反思总结。

Source: plan/2026-08-11-dialogue-self-evolution-improvement Task 1.2
- 成功任务 → success 经验
- 失败任务 → failure 经验
- 工具链提取
- 寒暄跳过
- 双轨: monitor 场景 lesson_category=project_evolution
"""
import pytest
from unittest.mock import AsyncMock, MagicMock

from private_agent.core.reflection import ReflectionEngine, ReflectionResult


def _fake_react_events():
    """模拟一轮对话的 react_events。"""
    return [
        {"event_type": "thinking", "turn": 1, "payload": {"content": "用户要清洗销售数据"}},
        {"event_type": "tool_call", "turn": 1, "payload": {"tool": "file_read", "args": {"path": "sales.csv"}}},
        {"event_type": "tool_result", "turn": 1, "payload": {"tool": "file_read", "result": "100 rows"}},
        {"event_type": "tool_call", "turn": 2, "payload": {"tool": "code_execution", "args": {"code": "df=pd.read_csv(...)"}}},
        {"event_type": "tool_result", "turn": 2, "payload": {"tool": "code_execution", "result": "cleaned"}},
        {"event_type": "tool_call", "turn": 3, "payload": {"tool": "file_write", "args": {"path": "output.xlsx"}}},
        {"event_type": "tool_result", "turn": 3, "payload": {"tool": "file_write", "result": "written"}},
        {"event_type": "final", "turn": 3, "payload": {"content": "已完成数据清洗并生成输出文件"}},
    ]


@pytest.mark.asyncio
async def test_reflection_success_task():
    """成功任务应生成 success 类型经验。"""
    mock_adapter = AsyncMock()
    mock_adapter.chat = AsyncMock(return_value=MagicMock(
        content='{"lesson_type": "success", "task_summary": "清洗销售数据", "lesson_content": "先检查dtype再清洗", "importance": 0.8}',
        reasoning_content="",
    ))

    engine = ReflectionEngine(adapter=mock_adapter)
    result = await engine.reflect(
        scope="office",
        user_message="帮我清洗这份销售数据",
        react_events=_fake_react_events(),
        final_output="已完成数据清洗并生成输出文件",
        had_error=False,
    )

    assert result is not None
    assert result.lesson_type == "success"
    assert "dtype" in result.lesson_content
    assert result.importance == 0.8


@pytest.mark.asyncio
async def test_reflection_failure_task():
    """失败任务应生成 failure 类型经验。"""
    mock_adapter = AsyncMock()
    mock_adapter.chat = AsyncMock(return_value=MagicMock(
        content='{"lesson_type": "failure", "task_summary": "清洗销售数据", "lesson_content": "未检查编码导致中文乱码", "importance": 0.9}',
        reasoning_content="",
    ))

    engine = ReflectionEngine(adapter=mock_adapter)
    result = await engine.reflect(
        scope="office",
        user_message="帮我清洗这份销售数据",
        react_events=_fake_react_events(),
        final_output="⚠️ 程序异常：编码错误",
        had_error=True,
    )

    assert result.lesson_type == "failure"
    assert "编码" in result.lesson_content


@pytest.mark.asyncio
async def test_reflection_extracts_tool_chain():
    """反思应提取工具链序列。"""
    mock_adapter = AsyncMock()
    mock_adapter.chat = AsyncMock(return_value=MagicMock(
        content='{"lesson_type": "success", "task_summary": "test", "lesson_content": "ok", "importance": 0.5}',
        reasoning_content="",
    ))

    engine = ReflectionEngine(adapter=mock_adapter)
    result = await engine.reflect(
        scope="office",
        user_message="test",
        react_events=_fake_react_events(),
        final_output="done",
        had_error=False,
    )

    assert result.tool_chain == ["file_read", "code_execution", "file_write"]


@pytest.mark.asyncio
async def test_reflection_skips_trivial_conversations():
    """寒暄/闲聊类对话应跳过反思（返回 None）。"""
    mock_adapter = AsyncMock()
    engine = ReflectionEngine(adapter=mock_adapter)

    result = await engine.reflect(
        scope="office",
        user_message="你好",
        react_events=[{"event_type": "final", "turn": 1, "payload": {"content": "你好！有什么可以帮你的？"}}],
        final_output="你好！有什么可以帮你的？",
        had_error=False,
    )

    assert result is None  # 寒暄不反思
    mock_adapter.chat.assert_not_called()


@pytest.mark.asyncio
async def test_reflection_monitor_uses_project_evolution_category():
    """双轨: monitor(无涯)场景应生成 project_evolution 类型经验。"""
    mock_adapter = AsyncMock()
    mock_adapter.chat = AsyncMock(return_value=MagicMock(
        content='{"lesson_type": "success", "task_summary": "提取重复代码为工具函数", "lesson_content": "重复模式>=3处时提取函数", "importance": 0.7}',
        reasoning_content="",
    ))

    engine = ReflectionEngine(adapter=mock_adapter)
    result = await engine.reflect(
        scope="monitor",
        user_message="帮我重构重复代码",
        react_events=_fake_react_events(),
        final_output="已提取重复代码为工具函数",
        had_error=False,
    )

    assert result is not None
    assert result.lesson_category == "project_evolution"
    # monitor 场景应使用项目进化反思模板
    prompt = mock_adapter.chat.call_args.kwargs["messages"][0]["content"]
    assert "项目进化" in prompt
