"""M3 P0.1 / V2 P3 - compress_adapter 构造(蓝图 §4.2 / spec AC-7, 去预置化)。

V2 P3 变更: 不再硬编码 glm/GlmAdapter/PA_GLM_API_KEY, 按 compress_model
(模型名)匹配 providers 动态注册 OpenAI 兼容 adapter; 无匹配 → None(优雅降级)。
"""
import pytest

from private_agent.models.adapters import OpenAICompatibleAdapter
from private_agent.models.base import ModelAdapter
from private_agent.models.registry import build_compress_adapter


def _make_cfg(compress_model="flash-m", enabled=True, model_name="flash-m"):
    """构造测试用 cfg: provider 的 model_name 与 compress_model 匹配。"""
    return {
        "models": {
            "providers": {
                "my-llm": {
                    "base_url": "http://llm.test",
                    "model_name": model_name,
                    "enabled": enabled,
                },
            },
            "router": {
                "type": "manual",
                "fallback_chain": ["my-llm"],
            },
            "compress_model": compress_model,
        }
    }


class TestBuildCompressAdapter:
    """AC-7(V2 P3): build_compress_adapter 按 model_name 匹配 provider。"""

    def test_returns_adapter_when_model_name_matches(self):
        """compress_model 与 provider.model_name 相等 → 返回该 provider 的 adapter。"""
        cfg = _make_cfg(compress_model="flash-m", model_name="flash-m")

        adapter = build_compress_adapter(cfg)

        assert adapter is not None
        assert isinstance(adapter, OpenAICompatibleAdapter)
        assert adapter.model_name == "flash-m"
        assert adapter.provider_name == "my-llm"

    def test_returns_none_when_provider_disabled(self):
        """model_name 匹配但 provider disabled → 返回 None。"""
        cfg = _make_cfg(compress_model="flash-m", enabled=False)

        adapter = build_compress_adapter(cfg)

        assert adapter is None

    def test_returns_none_when_no_compress_model(self):
        """无 compress_model 配置 → 返回 None(压缩降级)。"""
        cfg = _make_cfg()
        del cfg["models"]["compress_model"]

        adapter = build_compress_adapter(cfg)

        assert adapter is None

    def test_returns_none_when_no_matching_provider(self):
        """compress_model 无匹配 provider → None(去预置化核心: 不隐式绑定)。"""
        cfg = _make_cfg(compress_model="ghost-model", model_name="flash-m")
        assert build_compress_adapter(cfg) is None

    def test_adapter_is_model_adapter_protocol(self):
        """返回的 adapter 满足 ModelAdapter Protocol。"""
        cfg = _make_cfg()

        adapter = build_compress_adapter(cfg)

        assert isinstance(adapter, ModelAdapter)
        assert adapter.provider_name == "my-llm"

    def test_uses_provider_base_url_and_env_api_key(self, monkeypatch):
        """复用匹配 provider 的 base_url + PA_{NAME}_API_KEY(NAME 大写原样)。"""
        monkeypatch.setenv("PA_MY-LLM_API_KEY", "test-key-123")
        cfg = _make_cfg()

        adapter = build_compress_adapter(cfg)

        assert adapter is not None
        assert adapter.base_url == "http://llm.test"
        assert adapter.api_key == "test-key-123"


class TestMainInjectsCompressAdapter:
    """AC-7: main.py 和 admin.py 注入 compress_adapter(两处)。"""

    def test_main_has_build_compress_adapter_helper(self):
        """main 模块有 _build_compress_adapter helper 函数。"""
        from private_agent import main as main_mod

        assert hasattr(main_mod, "_build_compress_adapter")
        assert callable(main_mod._build_compress_adapter)

    def test_main_build_compress_adapter_returns_adapter(self):
        """_build_compress_adapter(cfg) 按 model_name 匹配返回 adapter。"""
        from private_agent import main as main_mod

        cfg = _make_cfg()
        adapter = main_mod._build_compress_adapter(cfg)

        assert adapter is not None
        assert isinstance(adapter, OpenAICompatibleAdapter)
        assert adapter.model_name == "flash-m"

    def test_admin_has_build_compress_adapter_helper(self):
        """admin 模块有 _build_compress_adapter helper 函数。"""
        from private_agent.api import admin as admin_mod

        assert hasattr(admin_mod, "_build_compress_adapter")
        assert callable(admin_mod._build_compress_adapter)
