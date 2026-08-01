"""M3 Skills 框架 - ExampleLoader token 预算截断(spec AC-6)。

Source: plan/m3-skills-office step 10, 蓝图 §7.7
- load(skill_name, max_examples, max_token): glob examples/*.md
- token 估算用 len//4 简化(Critic OQ-2 建议)
- 超 max_token 时停止累积,返回已累积的 examples
"""
import pytest

from private_agent.skills.example_loader import ExampleLoader


class TestExampleLoader:
    """AC-6: examples 总 token 超 max_frozen_token → 自动减少示例数量。"""

    def test_load_all_examples_under_budget(self, tmp_path):
        """token 未超预算 → 返回全部 examples。"""
        skill_dir = tmp_path / "office"
        ex_dir = skill_dir / "examples"
        ex_dir.mkdir(parents=True)
        (ex_dir / "a.md").write_text("示例 A:简短内容", encoding="utf-8")
        (ex_dir / "b.md").write_text("示例 B:简短内容", encoding="utf-8")

        loader = ExampleLoader(dev_dir=str(tmp_path))
        import asyncio
        examples = asyncio.run(loader.load("office", max_examples=3, max_token=4000))

        assert len(examples) == 2

    def test_truncate_when_over_budget(self, tmp_path):
        """AC-6: token 超预算 → 只返回能装下的 examples。"""
        skill_dir = tmp_path / "office"
        ex_dir = skill_dir / "examples"
        ex_dir.mkdir(parents=True)
        (ex_dir / "a.md").write_text("A" * 200, encoding="utf-8")  # ~50 token
        (ex_dir / "b.md").write_text("B" * 200, encoding="utf-8")  # ~50 token
        (ex_dir / "c.md").write_text("C" * 200, encoding="utf-8")  # ~50 token

        loader = ExampleLoader(dev_dir=str(tmp_path))
        import asyncio
        examples = asyncio.run(loader.load("office", max_examples=3, max_token=80))

        # 80 token / 50 per example → 只能装 1 个
        assert len(examples) == 1
        assert "A" in examples[0]

    def test_no_examples_dir_returns_empty(self, tmp_path):
        """无 examples/ 目录 → 返回空列表。"""
        (tmp_path / "office").mkdir()
        loader = ExampleLoader(dev_dir=str(tmp_path))

        import asyncio
        examples = asyncio.run(loader.load("office", max_examples=3, max_token=4000))

        assert examples == []

    def test_max_examples_limits_count(self, tmp_path):
        """max_examples 限制返回数量(即使 token 够)。"""
        skill_dir = tmp_path / "office"
        ex_dir = skill_dir / "examples"
        ex_dir.mkdir(parents=True)
        for i in range(5):
            (ex_dir / f"{i}.md").write_text(f"示例 {i}", encoding="utf-8")

        loader = ExampleLoader(dev_dir=str(tmp_path))
        import asyncio
        examples = asyncio.run(loader.load("office", max_examples=2, max_token=4000))

        assert len(examples) == 2

    def test_examples_sorted_by_filename(self, tmp_path):
        """examples 按文件名排序加载。"""
        skill_dir = tmp_path / "office"
        ex_dir = skill_dir / "examples"
        ex_dir.mkdir(parents=True)
        (ex_dir / "02_b.md").write_text("second", encoding="utf-8")
        (ex_dir / "01_a.md").write_text("first", encoding="utf-8")

        loader = ExampleLoader(dev_dir=str(tmp_path))
        import asyncio
        examples = asyncio.run(loader.load("office", max_examples=3, max_token=4000))

        assert examples[0] == "first"
        assert examples[1] == "second"
