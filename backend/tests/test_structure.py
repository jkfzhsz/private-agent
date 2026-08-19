"""B1.2 - 蓝图 §2.15 定义的目录结构完整可 import。

Source: plan/m0-implementation step 1 (蓝图 §9.6 step1 + §2.15)
"""
import importlib


# 蓝图 §2.15 定义的 9 个子包
SUBPACKAGES = [
    "core",
    "models",
    "tools",
    "skills",
    "storage",
    "eval",
    "api",
    "config",
    "observability",
]

# 蓝图 §2.15 定义的顶层模块
TOP_MODULES = ["errors", "main"]


def test_subpackages_importable():
    """9 个子包均可被 import。"""
    for name in SUBPACKAGES:
        importlib.import_module(f"private_agent.{name}")


def test_top_modules_importable():
    """顶层模块 errors.py / main.py 可被 import。"""
    for name in TOP_MODULES:
        importlib.import_module(f"private_agent.{name}")
