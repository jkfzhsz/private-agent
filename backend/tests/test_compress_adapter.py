"""M3 P0.1 - compress_adapter 构造(蓝图 §4.2 / spec AC-7)。

Source: plan/m3-skills-office step 3
- build_compress_adapter(cfg) 按 cfg['models']['compress_model'] 构造单 GLM adapter
- provider disabled 时返回 None(Critic reservation 3)
- 返回的 adapter.model_name = compress_model(覆盖 provider 默认 model_name)
"""
import pytest

from private_agent.models.adapters.glm import GlmAdapter
from private_agent.models.base import ModelAdapter
from private_agent.models.registry import build_compress_adapter


def _make_cfg(compress_model="glm-4-flash", glm_enabled=True):
    """构造测试用 cfg(模拟 config.yaml 的 models 段)。"""
    return {
        "models": {
            "providers": {
                "glm": {
                    "base_url": "http://glm.test",
                    "model_name": "glm-4",
                    "enabled": glm_enabled,
                },
            },
            "router": {
                "type": "manual",
                "fallback_chain": ["glm"],
            },
            "compress_model": compress_model,
        }
    }


class TestBuildCompressAdapter:
    """AC-7: build_compress_adapter 返回单 GLM adapter。"""

    def test_returns_glm_adapter_when_enabled(self):
        """compress_model='glm-4-flash' 且 glm enabled → 返回 GlmAdapter。"""
        cfg = _make_cfg(compress_model="glm-4-flash", glm_enabled=True)

        adapter = build_compress_adapter(cfg)

        assert adapter is not None
        assert isinstance(adapter, GlmAdapter)
        assert adapter.model_name == "glm-4-flash"

    def test_returns_none_when_provider_disabled(self):
        """Critic reservation 3: glm provider disabled → 返回 None。"""
        cfg = _make_cfg(compress_model="glm-4-flash", glm_enabled=False)

        adapter = build_compress_adapter(cfg)

        assert adapter is None

    def test_returns_none_when_no_compress_model(self):
        """无 compress_model 配置 → 返回 None(向后兼容)。"""
        cfg = _make_cfg()
        del cfg["models"]["compress_model"]

        adapter = build_compress_adapter(cfg)

        assert adapter is None

    def test_adapter_is_model_adapter_protocol(self):
        """返回的 adapter 满足 ModelAdapter Protocol。"""
        cfg = _make_cfg()

        adapter = build_compress_adapter(cfg)

        assert isinstance(adapter, ModelAdapter)
        assert adapter.provider_name == "glm"

    def test_uses_glm_provider_base_url_and_api_key(self, monkeypatch):
        """复用 glm provider 的 base_url + env api_key。"""
        monkeypatch.setenv("PA_GLM_API_KEY", "test-key-123")
        cfg = _make_cfg()

        adapter = build_compress_adapter(cfg)

        assert adapter is not None
        assert adapter.base_url == "http://glm.test"
        assert adapter.api_key == "test-key-123"


class TestMainInjectsCompressAdapter:
    """AC-7: main.py 和 admin.py 注入 compress_adapter(两处)。"""

    def test_main_has_build_compress_adapter_helper(self):
        """main 模块有 _build_compress_adapter helper 函数。"""
        from private_agent import main as main_mod

        assert hasattr(main_mod, "_build_compress_adapter")
        assert callable(main_mod._build_compress_adapter)

    def test_main_build_compress_adapter_returns_adapter(self):
        """_build_compress_adapter(cfg) 返回 GlmAdapter。"""
        from private_agent import main as main_mod

        cfg = _make_cfg()
        adapter = main_mod._build_compress_adapter(cfg)

        assert adapter is not None
        assert isinstance(adapter, GlmAdapter)
        assert adapter.model_name == "glm-4-flash"

    def test_admin_has_build_compress_adapter_helper(self):
        """admin 模块有 _build_compress_adapter helper 函数。"""
        from private_agent.api import admin as admin_mod

        assert hasattr(admin_mod, "_build_compress_adapter")
        assert callable(admin_mod._build_compress_adapter)
