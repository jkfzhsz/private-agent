"""M3 Skills 框架 - ExampleLoader 少样本加载(蓝图 §7.7,spec AC-6)。

Source: plan/m3-skills-office step 10
- load(skill_name, max_examples, max_token): glob examples/*.md 按文件名排序
- token 估算用 len//4 简化(Critic OQ-2 建议)
- 超 max_token 时停止累积,返回已累积的 examples(AC-6)
"""
from __future__ import annotations

from pathlib import Path


class ExampleLoader:
    """蓝图 §7.7 少样本加载器。"""

    def __init__(self, dev_dir: str = "./skills"):
        self.dev_dir = dev_dir

    async def load(
        self,
        skill_name: str,
        max_examples: int = 3,
        max_token: int = 4000,
    ) -> list[str]:
        """加载 examples/*.md,按 token 预算截断。

        Args:
            skill_name: Skill 名。
            max_examples: 最大示例数。
            max_token: token 预算上限(len//4 估算)。

        Returns:
            示例文本列表(按文件名排序,超预算时截断)。
        """
        ex_dir = Path(self.dev_dir) / skill_name / "examples"
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
