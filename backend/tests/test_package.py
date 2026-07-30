"""B1.1 - Python 包 private_agent 可被 import 并暴露 __version__。

Source: plan/m0-implementation step 1 (蓝图 §9.6 step1 + §2.15)
"""


def test_package_importable():
    """包 private_agent 可被 import。"""
    import private_agent  # noqa: F401


def test_package_version():
    """包 __version__ == '0.1.0'(对应 config.yaml system.version)。"""
    import private_agent

    assert private_agent.__version__ == "0.1.0"
