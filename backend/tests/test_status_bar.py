"""V2 上下文工程 - Agent 状态栏测试(AI-Agents-in-Depth §2.6)。

覆盖:
- 渲染格式: <agent_status> 键值对(非散文), 含时间/轮次/迭代/状态/工具计数
- record_tool_call / record_tool_result 计数与失败计数
- reset 跨轮重置
- build_status_bar_message 构造 user-role meta 消息
"""
import re

from private_agent.core.status_bar import AgentStatusBar, build_status_bar_message


def test_render_basic_structure():
    """渲染输出 <agent_status> 包裹的键值对文本。"""
    bar = AgentStatusBar()
    text = bar.render(state="acting", turn=3, iteration=2, max_iterations=10)
    assert text.startswith("<agent_status>")
    assert text.endswith("</agent_status>")
    assert "当前时间:" in text
    assert "对话轮次: 第 3 轮" in text
    assert "工具迭代: 2/10" in text
    assert "当前状态: acting" in text
    assert "工具调用: 无" in text
    assert "工具失败: 无" in text


def test_record_tool_call_and_result():
    """工具调用计数与失败计数正确聚合。"""
    bar = AgentStatusBar()
    bar.record_tool_call("web_search")
    bar.record_tool_call("web_search")
    bar.record_tool_call("code_execution")
    bar.record_tool_result("web_search", error=None)
    bar.record_tool_result("code_execution", error="Permission denied")

    counts = bar.counts()
    assert counts["web_search"] == 2
    assert counts["code_execution"] == 1

    text = bar.render(state="observing", turn=1, iteration=1, max_iterations=10)
    # 工具计数进状态栏(键值对, 不是散文)
    assert "工具调用: web search x2, code execution x1" in text or "web search x2" in text
    assert "工具失败: code execution x1" in text


def test_reset_clears_state():
    """reset 清空计数(新 turn 开始调用, 避免跨轮污染)。"""
    bar = AgentStatusBar()
    bar.record_tool_call("web_search")
    bar.reset()
    assert bar.counts() == {}
    assert "工具调用: 无" in bar.render()


def test_build_status_bar_message():
    """build_status_bar_message 返回 user-role meta 消息。"""
    bar = AgentStatusBar()
    bar.record_tool_call("echo")
    msg = build_status_bar_message(bar, state="idle", turn=1, iteration=0, max_iterations=10)
    assert msg["role"] == "user"
    assert "<agent_status>" in msg["content"]
    assert "工具调用: echo x1" in msg["content"]


def test_render_key_value_not_prose():
    """状态栏必须是键值对格式(书中强调散文格式效果更差)。"""
    bar = AgentStatusBar()
    bar.record_tool_call("web_search")
    text = bar.render()
    # 每行都是 "标签: 值" 形式
    for line in text.splitlines():
        if line.startswith("<agent_status>") or line.startswith("</agent_status>"):
            continue
        assert re.match(r"^[^:]+: .+", line), f"非键值对行: {line}"
