"""M4 m4-version-compare-rollback AC-11 - 前端评估面板静态检查测试。

Source: spec/m4-version-compare-rollback AC-11 + plan step 15
- AC-11: 前端评估面板含运行列表 + 版本趋势折线图 + 版本对比表格 + 退化告警标记

前端单元测试基础设施缺失,MVP 用静态检查 + grep 验证:
1. App.tsx 含 EvalPanel 组件定义
2. App.tsx 含 SVG 折线图相关代码(polyline / path / svg)
3. App.tsx 含退化告警标记(degraded / red badge)
4. chat.html 含评估面板入口
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_TSX = REPO_ROOT / "frontend" / "renderer" / "App.tsx"
CHAT_HTML = REPO_ROOT / "frontend" / "static" / "chat.html"


def test_app_tsx_contains_eval_panel_component():
    """AC-11: App.tsx 含 EvalPanel 组件。"""
    content = APP_TSX.read_text(encoding="utf-8")
    assert "EvalPanel" in content, "App.tsx 缺 EvalPanel 组件"


def test_app_tsx_contains_trend_chart_svg():
    """AC-11: App.tsx 含 SVG 折线图(polyline)。"""
    content = APP_TSX.read_text(encoding="utf-8")
    assert "polyline" in content or "<svg" in content, "App.tsx 缺 SVG 折线图"


def test_app_tsx_contains_degradation_badge():
    """AC-11: App.tsx 含退化告警标记(degraded / red badge)。"""
    content = APP_TSX.read_text(encoding="utf-8")
    assert "degraded" in content.lower(), "App.tsx 缺退化告警标记"


def test_chat_html_contains_eval_panel_entry():
    """AC-11: chat.html 含评估面板入口。"""
    content = CHAT_HTML.read_text(encoding="utf-8")
    assert "eval" in content.lower() or "评估" in content, "chat.html 缺评估面板入口"
