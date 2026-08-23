"""B1 P1-10 AC-9 - HybridEvaluator.from_cfg 返回实例。

Source: plan/b1-foundation-compliance step 20 (AC-9)
"""
from unittest.mock import MagicMock

from private_agent.eval.hybrid_eval import HybridEvaluator


def test_hybrid_evaluator_from_cfg():
    """AC-9: HybridEvaluator.from_cfg(cfg) 返回 HybridEvaluator 实例。"""
    # mock build_judge_adapter + load_judge_prompt 避免真实文件/网络依赖
    import private_agent.eval.hybrid_eval as he_module

    mock_adapter = MagicMock()
    mock_prompt = "template {user_input} {agent_response} {expected_output}"

    original_build = he_module.build_judge_adapter
    original_load = he_module.load_judge_prompt
    he_module.build_judge_adapter = lambda cfg: mock_adapter
    he_module.load_judge_prompt = lambda cfg: mock_prompt

    try:
        cfg = {"eval": {"judge_model": "glm-4-flash"}}
        evaluator = HybridEvaluator.from_cfg(cfg)
        assert isinstance(evaluator, HybridEvaluator), "should return HybridEvaluator instance"
    finally:
        he_module.build_judge_adapter = original_build
        he_module.load_judge_prompt = original_load
