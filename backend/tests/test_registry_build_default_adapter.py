"""B1 P1-10 AC-8 - build_default_adapter 返回首个 adapter。

Source: plan/b1-foundation-compliance step 19 (AC-8)
"""
from private_agent.models.registry import build_default_adapter


def test_build_default_adapter_returns_first():
    """AC-8: cfg 含 enabled provider → 返回非 None ModelAdapter(链首)。"""
    cfg = {
        "models": {
            "router": {"fallback_chain": ["glm"]},
            "providers": {
                "glm": {
                    "base_url": "https://open.bigmodel.cn/api/paas/v4",
                    "model_name": "glm-4",
                    "enabled": True,
                },
            },
        }
    }
    adapter = build_default_adapter(cfg)
    assert adapter is not None, "should return first adapter when chain non-empty"


def test_build_default_adapter_returns_none_when_chain_empty():
    """AC-8 补充: 所有 provider disabled → 返回 None。"""
    cfg = {
        "models": {
            "router": {"fallback_chain": ["glm"]},
            "providers": {
                "glm": {
                    "base_url": "https://open.bigmodel.cn/api/paas/v4",
                    "model_name": "glm-4",
                    "enabled": False,
                },
            },
        }
    }
    adapter = build_default_adapter(cfg)
    assert adapter is None, "should return None when fallback chain empty"
