"""M4 §8.8 LLM-as-Judge 模块(蓝图 §8.8)。

Source: plan/m4-metrics-judge step 7 (AC-6..AC-8)
- build_judge_adapter: 工厂函数,按 cfg["eval"]["judge_model"] 匹配 provider
- load_judge_prompt: 加载 judge_prompts/general.md 模板
- LLMJudge: 调用 Judge 模型,解析 JSON,降级返回 0 分

Judge 模型与主模型分离(规避同模型自评偏见)。
Judge 调用失败(网络/超时/解析失败)降级返回 0 分,不阻塞评估流程。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from private_agent.models.base import ModelAdapter, ProviderError
from private_agent.models.registry import build_adapter_for_model_name

__all__ = ["LLMJudge", "build_judge_adapter", "load_judge_prompt"]


def build_judge_adapter(cfg: dict) -> ModelAdapter | None:
    """工厂函数:按 cfg["eval"]["judge_model"] 匹配 provider 构造 adapter(蓝图 §8.8)。

    V2 P3 去预置化: 不再硬编码 glm/PA_GLM_API_KEY, 按 judge_model(模型名)
    匹配任意 enabled provider; 无匹配 → None(评测优雅降级)。

    Args:
        cfg: 配置 dict,需含 eval.judge_model + models.providers。

    Returns:
        ModelAdapter 实例;无匹配/未配置时返回 None。
    """
    judge_model = cfg.get("eval", {}).get("judge_model")
    if not judge_model:
        return None
    return build_adapter_for_model_name(cfg, judge_model)


def load_judge_prompt(cfg: dict) -> str:
    """加载 cfg["eval"]["judge_prompt_dir"]/general.md 模板(蓝图 §8.8)。

    Args:
        cfg: 配置 dict,需含 eval.judge_prompt_dir。

    Returns:
        模板字符串,含 {user_input}/{agent_response}/{expected_output} 变量。
    """
    prompt_dir = Path(cfg.get("eval", {}).get("judge_prompt_dir", "./config/judge_prompts"))
    # 相对路径基于 backend/ 解析
    if not prompt_dir.is_absolute():
        prompt_dir = Path(__file__).resolve().parents[2] / prompt_dir
    return (prompt_dir / "general.md").read_text(encoding="utf-8")


class LLMJudge:
    """LLM-as-Judge 模块:调用 Judge 模型评分(蓝图 §8.8)。

    Args:
        adapter: 模型适配器(按 judge_model 匹配的任意 provider adapter)。
        prompt_template: Judge prompt 模板,含 {user_input}/{agent_response}/{expected_output}。
    """

    def __init__(self, *, adapter: ModelAdapter, prompt_template: str) -> None:
        self._adapter = adapter
        self._prompt_template = prompt_template

    async def judge(
        self,
        *,
        user_input: str,
        agent_response: str,
        expected_output: str | None,
    ) -> dict:
        """调用 Judge 模型,返回评分结果(蓝图 §8.8)。

        Args:
            user_input: 用户请求文本。
            agent_response: Agent 实际响应文本。
            expected_output: 期望输出(参考答案),可为 None。

        Returns:
            {response_quality: 1-5, task_completion: 1-5,
             quality_reason: str, completion_reason: str}
            解析失败/调用失败时降级返回 0 分 + reason 标识。
        """
        # 填充模板变量(用 str.replace,非 f-string,因模板是运行时加载的)
        prompt = self._prompt_template.replace("{user_input}", user_input)
        prompt = prompt.replace("{agent_response}", agent_response)
        prompt = prompt.replace("{expected_output}", expected_output or "")

        # 调用 Judge 模型
        try:
            result = await self._adapter.chat(messages=[{"role": "user", "content": prompt}])
        except (ProviderError, Exception) as exc:
            return {
                "response_quality": 0,
                "task_completion": 0,
                "quality_reason": f"judge_call_failed: {type(exc).__name__}: {str(exc)[:100]}",
                "completion_reason": "judge_call_failed",
            }

        # 解析 JSON(三步容错:Critic reservation 1)
        content = result.content or ""
        parsed = self._parse_judge_json(content)
        if parsed is None:
            return {
                "response_quality": 0,
                "task_completion": 0,
                "quality_reason": f"judge_parse_error: content={content[:100]}",
                "completion_reason": "judge_parse_error",
            }

        return {
            "response_quality": parsed.get("response_quality", 0),
            "task_completion": parsed.get("task_completion", 0),
            "quality_reason": parsed.get("quality_reason", ""),
            "completion_reason": parsed.get("completion_reason", ""),
        }

    @staticmethod
    def _parse_judge_json(content: str) -> dict | None:
        """三步解析 Judge 模型输出(蓝图 §8.8 + Critic reservation 1)。

        步骤:
        1. 提取 ```json ... ``` 代码块,json.loads
        2. 整段 json.loads
        3. 正则提取第一个 { 到最后一个 } 的子串,json.loads

        Args:
            content: Judge 模型原始输出。

        Returns:
            解析后的 dict;三步均失败返回 None。
        """
        # 步骤 1:提取 ```json``` 代码块
        md_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", content, re.DOTALL)
        if md_match:
            try:
                return json.loads(md_match.group(1))
            except json.JSONDecodeError:
                pass

        # 步骤 2:整段 json.loads
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass

        # 步骤 3:正则提取第一个 { 到最后一个 } 的子串
        brace_match = re.search(r"\{.*\}", content, re.DOTALL)
        if brace_match:
            try:
                return json.loads(brace_match.group(0))
            except json.JSONDecodeError:
                pass

        return None
