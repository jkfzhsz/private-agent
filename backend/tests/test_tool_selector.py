"""ToolSelector 动态工具选择测试(对话流畅度优化方向一)。"""

from __future__ import annotations

import pytest

from private_agent.tools.defs import ToolDef
from private_agent.tools.selector import ToolSelector


async def _noop_handler(args: dict):  # noqa: ANN001
    return None


def _tool(name: str, desc: str = "") -> ToolDef:
    return ToolDef(
        name=name,
        description=desc,
        parameters_schema={"type": "object", "properties": {}},
        handler=_noop_handler,
    )


@pytest.fixture
def selector_cfg() -> dict:
    return {
        "tools": {
            "tool_selection": {
                "enabled": True,
                "top_n": 5,
                "min_pool_size": 3,
                "always_include": ["code_execution"],
            }
        }
    }


def test_selector_disabled_returns_all():
    sel = ToolSelector({"tools": {"tool_selection": {"enabled": False}}})
    tools = [_tool(f"t{i}") for i in range(10)]
    assert sel.select(tools, "hello") == tools


def test_small_pool_full_injection():
    # 池 ≤ min_pool_size → 全量注入(小池无需裁剪)
    sel = ToolSelector({"tools": {"tool_selection": {"enabled": True, "top_n": 5, "min_pool_size": 3}}})
    tools = [_tool(f"t{i}") for i in range(3)]
    assert sel.select(tools, "anything") == tools


def test_top_n_cut():
    sel = ToolSelector({"tools": {"tool_selection": {"enabled": True, "top_n": 2, "min_pool_size": 3}}})
    tools = [_tool(f"t{i}") for i in range(10)]
    selected = sel.select(tools, "unrelated query xyz")
    assert len(selected) <= 2


def test_always_include_anchored():
    sel = ToolSelector({
        "tools": {"tool_selection": {
            "enabled": True, "top_n": 2, "min_pool_size": 3,
            "always_include": ["code_execution"],
        }}
    })
    tools = [_tool(f"t{i}") for i in range(10)] + [_tool("code_execution", "run python")]
    selected = sel.select(tools, "random text")
    names = {t.name for t in selected}
    assert "code_execution" in names  # 锚点必含
    assert len(selected) <= 3  # top_n(2) + 锚点(1)


def test_keyword_relevance_ranking():
    # 相关工具(描述含关键词)应优先于无关工具
    sel = ToolSelector({"tools": {"tool_selection": {"enabled": True, "top_n": 3, "min_pool_size": 3}}})
    tools = [
        _tool("search_stock", "查询股票行情数据"),
        _tool("send_email", "发送邮件"),
        _tool("get_weather", "天气"),
    ]
    selected = sel.select(tools, "帮我查一下股票的价格行情")
    names = [t.name for t in selected]
    assert "search_stock" in names
    # 相关工具排名应高于无关工具
    assert names.index("search_stock") < names.index("get_weather")


def test_usage_weighting():
    # 用过的工具获得历史加权, 更可能保留
    sel = ToolSelector({"tools": {"tool_selection": {"enabled": True, "top_n": 1, "min_pool_size": 3}}})
    tools = [_tool("tool_a", "alpha beta gamma"), _tool("tool_b", "x y z w")]
    sel.record_usage("tool_a")
    sel.record_usage("tool_a")
    selected = sel.select(tools, "完全无关的查询内容 无关键词")
    assert selected[0].name == "tool_a"


def test_deterministic_same_input_same_output():
    sel = ToolSelector({"tools": {"tool_selection": {"enabled": True, "top_n": 5, "min_pool_size": 3}}})
    tools = [_tool(f"t{i}", f"desc number {i}") for i in range(10)]
    q = "查询 t1 相关的数据信息内容"
    assert [t.name for t in sel.select(tools, q)] == [t.name for t in sel.select(tools, q)]


def test_order_stable_from_pool():
    # 返回子集保持工具池原始顺序(不重排, KV 友好)
    sel = ToolSelector({"tools": {"tool_selection": {"enabled": True, "top_n": 5, "min_pool_size": 3}}})
    tools = [_tool(f"t{i}") for i in range(10)]
    selected = sel.select(tools, "query stock price 股票 行情 数据 分析")
    idx = [tools.index(t) for t in selected]
    assert idx == sorted(idx)
