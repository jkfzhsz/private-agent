"""ReactLoop mcp_browse 装配标记解析 —— 类型防御回归测试。

背景(2026-08-31 session-76 整轮崩溃):
`_parse_assembly_marker` 只 try/except 了 `json.loads`, 却把 `data.get()`
写在 try 块之外。json.loads 只保证"是合法 JSON", 不保证"是 dict" ——
`mcp_browse action=exec` 的 output 由被调用的 MCP server 决定(mempalace
3.8.0 的 event_list/find_tunnels/search 等常返回数组), 解析出 list/标量时
`.get()` 抛 AttributeError 且 except 兜不住, 异常沿 asyncio.gather 冒泡
炸掉整轮, 用户侧表现为"连催两次零输出"。

本文件锁定: 任何非 dict 的合法 JSON 一律安全返回 None, 绝不抛异常。
纯函数测试, 不依赖数据库。
"""
from __future__ import annotations

import json

import pytest

from private_agent.core.react_loop import _parse_assembly_marker


# --- 安全输入: 不得抛异常, 返回值符合预期 ---------------------------------

@pytest.mark.parametrize(
    "output,expected",
    [
        # 空/空白
        ("", None),
        (None, None),
        # 非 JSON(mcp_browse action=list 的纯文本索引) —— loads 失败
        ("MCP 全局工具索引(全部已启用 server):\n- mempalace [type=stdio]", None),
        ("not json at all", None),
        # 合法 JSON 但非 dict —— 本次修复的核心场景
        (json.dumps([{"id": "evt_1"}, {"id": "evt_2"}]), None),  # exec 返回数组
        (json.dumps(["a", "b"]), None),                          # 裸数组
        (json.dumps("hello"), None),                             # 字符串标量
        (json.dumps(123), None),                                 # 数字
        (json.dumps(12.5), None),                                # 浮点
        (json.dumps(True), None),                                # 布尔
        (json.dumps(None), None),                                # null
        (json.dumps([]), None),                                  # 空数组
        # dict 但无标记 / 标记类型不对
        (json.dumps({"ok": True}), None),
        (json.dumps({"__mcp_assembly": "not-a-dict"}), None),
        (json.dumps({"__mcp_assembly": None}), None),
    ],
)
def test_non_marker_outputs_return_none(output, expected):
    """非装配标记的一切输入 → None(绝不抛异常)。"""
    assert _parse_assembly_marker(output) is expected


# --- 正向: 真实标记必须仍能识别 -------------------------------------------

def test_assemble_marker_parsed():
    marker = {"action": "assemble", "server_ids": ["mempalace", "codegraph"]}
    out = json.dumps({"__mcp_assembly": marker}, ensure_ascii=False)
    assert _parse_assembly_marker(out) == marker


def test_remove_marker_parsed():
    marker = {"action": "remove", "server_ids": ["Searchpin"]}
    out = json.dumps({"__mcp_assembly": marker}, ensure_ascii=False)
    assert _parse_assembly_marker(out) == marker


def test_marker_with_unicode_server_ids():
    """中文 server id 不被 ensure_ascii 破坏。"""
    marker = {"action": "assemble", "server_ids": ["记忆宫殿"]}
    out = json.dumps({"__mcp_assembly": marker}, ensure_ascii=False)
    assert _parse_assembly_marker(out) == marker


# --- 回归护栏: 直接复现 session-76 的崩溃形态 -------------------------------

def test_mempalace_38_exec_array_output_does_not_raise():
    """mempalace 3.8.0 event_list 风格返回(顶层数组)不抛 AttributeError。

    这是 session-76 的精确崩溃形态: data 为 list → data.get() → AttributeError。
    """
    payload = [
        {"event_id": "evt_001", "wing": "monitor", "kind": "artifact_put"},
        {"event_id": "evt_002", "wing": "monitor", "kind": "event_append"},
    ]
    # 修复前: AttributeError: 'list' object has no attribute 'get'
    assert _parse_assembly_marker(json.dumps(payload, ensure_ascii=False)) is None
