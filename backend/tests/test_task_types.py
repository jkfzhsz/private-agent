"""2026-08-13 类型感知限流: infer_task_type / classify_tool 判定测试。

覆盖(方案 §6): 显式优先 / 关键词推断(中英文) / 空兜底默认 search /
显式非法走推断 / 工具分类。
"""
from private_agent.core.task_types import (
    TASK_TYPES,
    classify_tool,
    infer_task_type,
)


def test_explicit_wins():
    """显式 type 优先于 prompt 内容。"""
    assert infer_task_type("搜索苏州市项目", explicit="analysis") == "analysis"
    assert infer_task_type("写一个脚本", explicit="search") == "search"


def test_explicit_invalid_falls_back():
    """显式 type 非法 → 忽略走推断。"""
    assert infer_task_type("搜索苏州市项目", explicit="hack") == "search"


def test_chinese_keyword_search():
    assert infer_task_type("深挖六张网完整项目清单并搜索苏州相关") == "search"
    assert infer_task_type("帮我查一下苏州工业园区政策") == "search"


def test_chinese_keyword_analysis():
    assert infer_task_type("分析这批数据的统计特征") == "analysis"
    assert infer_task_type("整理并汇总销售报表") == "analysis"


def test_chinese_keyword_code():
    assert infer_task_type("编写一个 Python 脚本处理文件") == "code"
    assert infer_task_type("创建一份项目文档") == "code"


def test_english_keywords():
    assert infer_task_type("research suzhou infrastructure projects") == "search"
    assert infer_task_type("calculate the statistics and analyze") == "analysis"
    assert infer_task_type("write a script to modify files") == "code"


def test_empty_prompt_defaults_search():
    """空 prompt + 无显式 → 保守默认 search。"""
    assert infer_task_type("") == "search"
    assert infer_task_type(None) == "search"


def test_search_priority_over_others():
    """同时命中多个类型 → 取 search(反爬最敏感优先)。"""
    # 含"搜索"与"分析" → search
    assert infer_task_type("搜索资料并分析对比") == "search"
    # 含"查询"与"写" → search
    assert infer_task_type("查询政策并写总结") == "search"


def test_classify_tool():
    """工具分类: 搜索/金融数据/文件代码/其他。"""
    assert classify_tool("web_search") == "search"
    assert classify_tool("web_fetch") == "search"
    assert classify_tool("mcp__Searchpin__web_search") == "search"
    assert classify_tool("mcp__hexin-ifind-ds-stock-mcp__search_stocks") == "analysis"
    assert classify_tool("calculator") == "analysis"
    assert classify_tool("file_read") == "code"
    assert classify_tool("file_write") == "code"
    assert classify_tool("code_execution") == "code"
    assert classify_tool("datetime") == "other"


def test_task_types_enum():
    assert set(TASK_TYPES) == {"search", "analysis", "code", "other"}
