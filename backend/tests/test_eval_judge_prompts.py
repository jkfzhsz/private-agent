"""M4 §8.8 judge_prompts/general.md - 通用 Judge 模板测试。

Source: plan/m4-eval-foundation step 12-13 (AC-9)
- general.md 文件存在
- 含 {user_input} / {agent_response} / {expected_output} 三个模板变量
"""
from pathlib import Path


def _general_md_path() -> Path:
    """返回 general.md 路径(backend/config/judge_prompts/general.md)。

    测试文件位于 backend/tests/,因此 general.md 相对路径为 ../config/judge_prompts/general.md。
    """
    here = Path(__file__).resolve().parent
    return here.parent / "config" / "judge_prompts" / "general.md"


def test_general_md_exists():
    """AC-9: backend/config/judge_prompts/general.md 文件存在。"""
    assert _general_md_path().exists(), f"general.md 不存在: {_general_md_path()}"


def test_general_md_contains_user_input_placeholder():
    """AC-9: general.md 含 {user_input} 模板变量。"""
    content = _general_md_path().read_text(encoding="utf-8")
    assert "{user_input}" in content, "general.md 缺 {user_input} 模板变量"


def test_general_md_contains_agent_response_placeholder():
    """AC-9: general.md 含 {agent_response} 模板变量。"""
    content = _general_md_path().read_text(encoding="utf-8")
    assert "{agent_response}" in content, "general.md 缺 {agent_response} 模板变量"


def test_general_md_contains_expected_output_placeholder():
    """AC-9: general.md 含 {expected_output} 模板变量。"""
    content = _general_md_path().read_text(encoding="utf-8")
    assert "{expected_output}" in content, "general.md 缺 {expected_output} 模板变量"
