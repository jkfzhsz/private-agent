"""M3 Skills 框架 - ExampleLoader 少样本加载(蓝图 §7.7/§7.16,spec AC-6)。

Source: plan/m3-remaining-done-criteria step 7-8
- load(skill_name, max_examples, max_token): glob examples/train/*.md 按文件名排序
- token 估算用 len//4 简化(Critic OQ-2 建议)
- 超 max_token 时停止累积,返回已累积的 examples(AC-6)
- from_cfg(cfg) 类方法从 config 读取 dev_dir(AC-7)

M4 §8.4 扩展(AC-7):
- load_test_set(skill_name): glob examples/test/*.json,返回 list[EvalSample]
"""
from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from private_agent.eval.models import (
    EvalSample,
    InvalidSampleFormatError,
    validate_expected_trace,
)


class ExampleLoader:
    """蓝图 §7.7/§7.16 少样本加载器,从 examples/train/ 加载训练集示例。"""

    def __init__(self, dev_dir: str = "./skills"):
        self.dev_dir = dev_dir

    @classmethod
    def from_cfg(cls, cfg: dict) -> "ExampleLoader":
        """从 config 构造 ExampleLoader(AC-7)。

        Args:
            cfg: 配置字典,读取 cfg["skills"]["storage"]["dev_dir"]。

        Returns:
            ExampleLoader 实例。
        """
        dev_dir = cfg.get("skills", {}).get("storage", {}).get("dev_dir", "./skills")
        return cls(dev_dir=dev_dir)

    async def load(
        self,
        skill_name: str,
        max_examples: int = 3,
        max_token: int = 4000,
    ) -> list[str]:
        """加载 examples/train/*.md,按 token 预算截断。

        Args:
            skill_name: Skill 名。
            max_examples: 最大示例数。
            max_token: token 预算上限(len//4 估算)。

        Returns:
            示例文本列表(按文件名排序,超预算时截断)。
        """
        ex_dir = Path(self.dev_dir) / skill_name / "examples" / "train"
        if not ex_dir.exists():
            return []
        files = sorted(ex_dir.glob("*.md"))
        examples: list[str] = []
        total_token = 0
        for f in files:
            if len(examples) >= max_examples:
                break
            text = f.read_text(encoding="utf-8")
            token = len(text) // 4
            if total_token + token > max_token and examples:
                break
            examples.append(text)
            total_token += token
        return examples

    async def load_test_set(self, skill_name: str) -> list[EvalSample]:
        """加载 examples/test/*.json,解析为 EvalSample 列表(蓝图 §8.4,AC-7)。

        与 load() 的区别:
        - 加载 test/ 子目录而非 train/
        - 加载 .json 而非 .md
        - 不做 token 截断(test 样本需完整结构)
        - 每文件经 Pydantic 校验,失败抛 InvalidSampleFormatError

        Args:
            skill_name: Skill 名。

        Returns:
            EvalSample 列表(按文件名排序,空目录返回空列表)。

        Raises:
            InvalidSampleFormatError: 任一样本 JSON 结构非法时抛出。
        """
        ex_dir = Path(self.dev_dir) / skill_name / "examples" / "test"
        if not ex_dir.exists():
            return []
        files = sorted(ex_dir.glob("*.json"))
        samples: list[EvalSample] = []
        for f in files:
            data = json.loads(f.read_text(encoding="utf-8"))
            # 入库前校验 expected_react_trace 结构(AC-2/AC-3 同一入口)
            validate_expected_trace(data.get("expected_react_trace"))
            try:
                samples.append(EvalSample.model_validate(data))
            except ValidationError as exc:
                raise InvalidSampleFormatError(
                    f"EvalSample 校验失败({f.name}): {exc}"
                ) from exc
        return samples
