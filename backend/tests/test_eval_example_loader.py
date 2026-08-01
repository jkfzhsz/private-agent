"""M4 §8.4 ExampleLoader.load_test_set 测试(AC-7)。

Source: plan/m4-eval-foundation step 17
- load_test_set(skill_name): glob examples/test/*.json,按文件名排序
- 每文件 json.loads + EvalSample.model_validate(校验失败抛 InvalidSampleFormatError)
- 不做 token 截断(test 样本需完整结构)
- 无 test/ 目录返回空列表
"""
import json

import pytest

from private_agent.eval.models import InvalidSampleFormatError
from private_agent.skills.example_loader import ExampleLoader


def _write_sample(path, sample_id: str = "s1", scenario: str = "office") -> None:
    """写一个合法的 EvalSample JSON 文件。"""
    payload = {
        "sample_id": sample_id,
        "scenario": scenario,
        "skill_name": scenario,
        "skill_version": "1.0.0",
        "case_type": "normal",
        "difficulty": "easy",
        "split": "test",
        "input": "test input",
        "expected_react_trace": {
            "tool_calls": [{"tool": "calculator", "args": {"expr": "1+1"}}],
            "expected_output_contains": ["2"],
        },
        "expected_output": "2",
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_load_test_set_loads_json_samples(tmp_path):
    """AC-7: load_test_set 加载 examples/test/*.json,返回 EvalSample 列表。"""
    skill_dir = tmp_path / "office" / "examples" / "test"
    skill_dir.mkdir(parents=True)
    _write_sample(skill_dir / "office_001_normal.json", "s1")
    _write_sample(skill_dir / "office_002_normal.json", "s2")

    loader = ExampleLoader(dev_dir=str(tmp_path))
    import asyncio
    samples = asyncio.run(loader.load_test_set("office"))

    assert len(samples) == 2
    assert {s.sample_id for s in samples} == {"s1", "s2"}


def test_load_test_set_sorted_by_filename(tmp_path):
    """load_test_set 按文件名排序返回(保证可重现)。"""
    skill_dir = tmp_path / "office" / "examples" / "test"
    skill_dir.mkdir(parents=True)
    # 故意乱序写入
    _write_sample(skill_dir / "office_003.json", "s3")
    _write_sample(skill_dir / "office_001.json", "s1")
    _write_sample(skill_dir / "office_002.json", "s2")

    loader = ExampleLoader(dev_dir=str(tmp_path))
    import asyncio
    samples = asyncio.run(loader.load_test_set("office"))

    assert [s.sample_id for s in samples] == ["s1", "s2", "s3"]


def test_load_test_set_no_test_dir_returns_empty(tmp_path):
    """AC-7: 无 examples/test/ 目录 → 返回空列表(不抛异常)。"""
    (tmp_path / "office" / "examples" / "train").mkdir(parents=True)

    loader = ExampleLoader(dev_dir=str(tmp_path))
    import asyncio
    samples = asyncio.run(loader.load_test_set("office"))
    assert samples == []


def test_load_test_set_empty_test_dir_returns_empty(tmp_path):
    """test/ 目录存在但无 .json 文件 → 返回空列表。"""
    (tmp_path / "office" / "examples" / "test").mkdir(parents=True)

    loader = ExampleLoader(dev_dir=str(tmp_path))
    import asyncio
    samples = asyncio.run(loader.load_test_set("office"))
    assert samples == []


def test_load_test_set_invalid_json_raises(tmp_path):
    """非法 JSON 结构(缺 tool_calls)→ 抛 InvalidSampleFormatError。"""
    skill_dir = tmp_path / "office" / "examples" / "test"
    skill_dir.mkdir(parents=True)
    bad_payload = {
        "sample_id": "bad",
        "scenario": "office",
        "skill_name": "office",
        "skill_version": "1.0.0",
        "case_type": "normal",
        "difficulty": "easy",
        "split": "test",
        "input": "x",
        "expected_react_trace": {"expected_output_contains": []},  # 缺 tool_calls
        "expected_output": None,
    }
    (skill_dir / "bad.json").write_text(
        json.dumps(bad_payload, ensure_ascii=False), encoding="utf-8"
    )

    loader = ExampleLoader(dev_dir=str(tmp_path))
    import asyncio
    with pytest.raises(InvalidSampleFormatError):
        asyncio.run(loader.load_test_set("office"))


def test_load_test_set_no_token_truncation(tmp_path):
    """load_test_set 不做 token 截断(区别于 load() 的 max_token 预算)。"""
    skill_dir = tmp_path / "office" / "examples" / "test"
    skill_dir.mkdir(parents=True)
    # 写一个超长 input 的样本(远超 load() 的 4000 token 预算)
    long_input = "x" * 100000
    payload = {
        "sample_id": "long",
        "scenario": "office",
        "skill_name": "office",
        "skill_version": "1.0.0",
        "case_type": "normal",
        "difficulty": "easy",
        "split": "test",
        "input": long_input,
        "expected_react_trace": {
            "tool_calls": [],
            "expected_output_contains": [],
        },
        "expected_output": None,
    }
    (skill_dir / "long.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )

    loader = ExampleLoader(dev_dir=str(tmp_path))
    import asyncio
    samples = asyncio.run(loader.load_test_set("office"))
    assert len(samples) == 1
    assert samples[0].input == long_input
