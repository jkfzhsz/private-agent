"""M4 m4-continuous-evolution AC-2..AC-6 - ReviewQueueRepo 测试。

Source: spec/m4-continuous-evolution AC-2..AC-6
- AC-2: add(item) 添加审核项,返回 item_id,status="pending"
- AC-3: list_pending() 返回 status="pending" 的审核项列表
- AC-4: update_status(item_id, status="approved", decision="prompt_defect_edit",
        edited_sample) 将编辑后样本入库 eval_datasets(split="test", case_type="boundary")
- AC-5: update_status(item_id, status="rejected", decision="model_limitation_drop")
        丢弃样本,不入库
- AC-6: 入库前调 validate_expected_trace,非法样本抛 InvalidSampleFormatError

ReviewQueueRepo 用 JSON 文件存储(spec §B),路径从 queue_file 参数读取。
"""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from private_agent.eval.models import (
    EvalSample,
    ExpectedToolCall,
    ExpectedTrace,
    InvalidSampleFormatError,
)
from private_agent.eval.repos import ReviewQueueRepo


def _make_sample(
    sample_id: str = "office_weak_001",
    scenario: str = "office",
    case_type: str = "boundary",
    split: str = "test",
) -> EvalSample:
    return EvalSample(
        sample_id=sample_id,
        scenario=scenario,
        skill_name=scenario,
        skill_version="1.0.0",
        case_type=case_type,  # type: ignore[arg-type]
        difficulty="medium",
        split=split,  # type: ignore[arg-type]
        input="低分案例原始输入",
        expected_react_trace=ExpectedTrace(
            tool_calls=[ExpectedToolCall(tool="calculator", args={"expr": "1+1"})],
            expected_output_contains=["2"],
        ),
        expected_output="2",
    )


# ── AC-2: add ───────────────────────────────────────────────────────────


async def test_add_returns_item_id_and_status_pending(tmp_path):
    """AC-2: add 返回递增 item_id,新增项 status='pending'。"""
    queue_file = str(tmp_path / ".eval_review_queue.json")
    repo = ReviewQueueRepo(queue_file=queue_file)
    item_id = await repo.add({
        "source_run_id": "run-uuid-1",
        "sample_input": "hello",
        "actual_output": "wrong",
        "actual_events": [],
        "failure_reason": "low completion_rate",
        "suggested_as": "boundary",
    })
    assert isinstance(item_id, int)
    assert item_id >= 1
    # 文件已写入
    pending = await repo.list_pending()
    assert len(pending) == 1
    assert pending[0]["id"] == item_id
    assert pending[0]["status"] == "pending"
    assert pending[0]["source_run_id"] == "run-uuid-1"


async def test_add_increments_item_id(tmp_path):
    """AC-2: 多次 add 返回递增的 item_id(next_id 持久化)。"""
    queue_file = str(tmp_path / ".eval_review_queue.json")
    repo = ReviewQueueRepo(queue_file=queue_file)
    id1 = await repo.add({"sample_input": "a"})
    id2 = await repo.add({"sample_input": "b"})
    id3 = await repo.add({"sample_input": "c"})
    assert id2 == id1 + 1
    assert id3 == id2 + 1


async def test_add_persists_across_repo_instances(tmp_path):
    """AC-2: 数据持久化到文件,新 repo 实例可读取。"""
    queue_file = str(tmp_path / ".eval_review_queue.json")
    repo1 = ReviewQueueRepo(queue_file=queue_file)
    await repo1.add({"sample_input": "persisted"})
    # 新实例从同一文件加载
    repo2 = ReviewQueueRepo(queue_file=queue_file)
    pending = await repo2.list_pending()
    assert len(pending) == 1
    assert pending[0]["sample_input"] == "persisted"


async def test_add_sets_created_at_and_decided_at_null(tmp_path):
    """AC-2: 新增项含 created_at,decided_at 为 null。"""
    queue_file = str(tmp_path / ".eval_review_queue.json")
    repo = ReviewQueueRepo(queue_file=queue_file)
    item_id = await repo.add({"sample_input": "x"})
    pending = await repo.list_pending()
    item = next(i for i in pending if i["id"] == item_id)
    assert item["created_at"] is not None
    assert item["decided_at"] is None
    assert item["decision"] is None


# ── AC-3: list_pending / list_all ───────────────────────────────────────


async def test_list_pending_returns_only_pending(tmp_path):
    """AC-3: list_pending 只返回 status='pending' 的项。"""
    queue_file = str(tmp_path / ".eval_review_queue.json")
    repo = ReviewQueueRepo(queue_file=queue_file,
                           dataset_repo=AsyncMock())
    id1 = await repo.add({"sample_input": "a"})
    id2 = await repo.add({"sample_input": "b"})
    # 决策 id1 → approved(用 mock dataset_repo 避免真实 DB)
    await repo.update_status(
        id1, status="approved", decision="prompt_defect_edit",
        edited_sample=_make_sample("edited_a"),
    )
    pending = await repo.list_pending()
    pending_ids = [i["id"] for i in pending]
    assert id1 not in pending_ids
    assert id2 in pending_ids


async def test_list_pending_empty_returns_empty_list(tmp_path):
    """AC-3: 空队列返回空列表(不是 None)。"""
    queue_file = str(tmp_path / ".eval_review_queue.json")
    repo = ReviewQueueRepo(queue_file=queue_file)
    result = await repo.list_pending()
    assert result == []


async def test_list_all_with_status_filter(tmp_path):
    """list_all 按 status 过滤,默认返回全部。"""
    queue_file = str(tmp_path / ".eval_review_queue.json")
    repo = ReviewQueueRepo(queue_file=queue_file,
                           dataset_repo=AsyncMock())
    id1 = await repo.add({"sample_input": "a"})
    id2 = await repo.add({"sample_input": "b"})
    await repo.update_status(
        id1, status="rejected", decision="model_limitation_drop",
    )
    all_items = await repo.list_all()
    assert len(all_items) == 2
    rejected = await repo.list_all(status="rejected")
    assert len(rejected) == 1
    assert rejected[0]["id"] == id1
    pending = await repo.list_all(status="pending")
    assert len(pending) == 1
    assert pending[0]["id"] == id2


async def test_list_pending_respects_limit(tmp_path):
    """list_pending 受 limit 参数限制。"""
    queue_file = str(tmp_path / ".eval_review_queue.json")
    repo = ReviewQueueRepo(queue_file=queue_file)
    for i in range(5):
        await repo.add({"sample_input": f"item-{i}"})
    limited = await repo.list_pending(limit=2)
    assert len(limited) == 2


# ── AC-4: update_status approved + prompt_defect_edit → 入库 ────────────


async def test_update_status_approved_inserts_into_eval_datasets(tmp_path):
    """AC-4: status='approved' + decision='prompt_defect_edit' 调 dataset_repo.insert 入库。"""
    queue_file = str(tmp_path / ".eval_review_queue.json")
    mock_dataset_repo = AsyncMock()
    mock_dataset_repo.insert = AsyncMock(return_value=42)
    repo = ReviewQueueRepo(queue_file=queue_file, dataset_repo=mock_dataset_repo)

    item_id = await repo.add({"sample_input": "low-score-case"})
    sample = _make_sample("weak_sample_001")
    await repo.update_status(
        item_id, status="approved", decision="prompt_defect_edit",
        edited_sample=sample,
    )
    # 入库被调用一次,且传入的是同一 sample
    mock_dataset_repo.insert.assert_awaited_once()
    inserted_sample = mock_dataset_repo.insert.await_args.args[0]
    assert inserted_sample.sample_id == "weak_sample_001"
    # AC-4: case_type=boundary, split=test
    assert inserted_sample.case_type == "boundary"
    assert inserted_sample.split == "test"


async def test_update_status_approved_updates_queue_item(tmp_path):
    """AC-4: 决策后 queue 项 status='approved',decision='prompt_defect_edit',decided_at 非空。"""
    queue_file = str(tmp_path / ".eval_review_queue.json")
    repo = ReviewQueueRepo(queue_file=queue_file, dataset_repo=AsyncMock())

    item_id = await repo.add({"sample_input": "x"})
    await repo.update_status(
        item_id, status="approved", decision="prompt_defect_edit",
        edited_sample=_make_sample(),
    )
    all_items = await repo.list_all()
    item = next(i for i in all_items if i["id"] == item_id)
    assert item["status"] == "approved"
    assert item["decision"] == "prompt_defect_edit"
    assert item["decided_at"] is not None


async def test_update_status_edited_treated_as_approved(tmp_path):
    """AC-4: status='edited' 等价于 approved,样本入库。"""
    queue_file = str(tmp_path / ".eval_review_queue.json")
    mock_dataset_repo = AsyncMock()
    mock_dataset_repo.insert = AsyncMock(return_value=1)
    repo = ReviewQueueRepo(queue_file=queue_file, dataset_repo=mock_dataset_repo)

    item_id = await repo.add({"sample_input": "x"})
    await repo.update_status(
        item_id, status="edited", decision="prompt_defect_edit",
        edited_sample=_make_sample(),
    )
    mock_dataset_repo.insert.assert_awaited_once()


# ── AC-5: update_status rejected + model_limitation_drop → 不入库 ────────


async def test_update_status_rejected_does_not_insert(tmp_path):
    """AC-5: status='rejected' + decision='model_limitation_drop' 不入库。"""
    queue_file = str(tmp_path / ".eval_review_queue.json")
    mock_dataset_repo = AsyncMock()
    mock_dataset_repo.insert = AsyncMock(return_value=99)
    repo = ReviewQueueRepo(queue_file=queue_file, dataset_repo=mock_dataset_repo)

    item_id = await repo.add({"sample_input": "model-limit-case"})
    await repo.update_status(
        item_id, status="rejected", decision="model_limitation_drop",
    )
    mock_dataset_repo.insert.assert_not_awaited()
    # 队列项状态更新
    all_items = await repo.list_all()
    item = next(i for i in all_items if i["id"] == item_id)
    assert item["status"] == "rejected"
    assert item["decision"] == "model_limitation_drop"


# ── AC-6: 入库前 validate_expected_trace ─────────────────────────────────


async def test_update_status_invalid_sample_raises(tmp_path, monkeypatch):
    """AC-6: edited_sample 校验失败时抛 InvalidSampleFormatError,不入库。"""
    queue_file = str(tmp_path / ".eval_review_queue.json")
    mock_dataset_repo = AsyncMock()
    mock_dataset_repo.insert = AsyncMock(return_value=1)
    repo = ReviewQueueRepo(queue_file=queue_file, dataset_repo=mock_dataset_repo)

    item_id = await repo.add({"sample_input": "x"})

    # patch validate_expected_trace 抛错
    from private_agent.eval import repos as repos_mod

    def _raise_invalid(_trace):
        raise InvalidSampleFormatError("mocked invalid trace")

    monkeypatch.setattr(repos_mod, "validate_expected_trace", _raise_invalid)

    with pytest.raises(InvalidSampleFormatError):
        await repo.update_status(
            item_id, status="approved", decision="prompt_defect_edit",
            edited_sample=_make_sample(),
        )
    # 入库未被调用
    mock_dataset_repo.insert.assert_not_awaited()
    # 队列项状态保持 pending(未决策)
    pending = await repo.list_pending()
    assert any(i["id"] == item_id for i in pending)


# ── 边界 / 异常 ──────────────────────────────────────────────────────────


async def test_update_status_unknown_item_raises(tmp_path):
    """update_status 不存在的 item_id 抛 KeyError。"""
    queue_file = str(tmp_path / ".eval_review_queue.json")
    repo = ReviewQueueRepo(queue_file=queue_file, dataset_repo=AsyncMock())
    with pytest.raises(KeyError):
        await repo.update_status(
            9999, status="rejected", decision="model_limitation_drop",
        )


async def test_update_status_invalid_decision_raises(tmp_path):
    """update_status 非法 decision 抛 ValueError。"""
    queue_file = str(tmp_path / ".eval_review_queue.json")
    repo = ReviewQueueRepo(queue_file=queue_file, dataset_repo=AsyncMock())
    item_id = await repo.add({"sample_input": "x"})
    with pytest.raises(ValueError):
        await repo.update_status(
            item_id, status="approved", decision="unknown_decision",
        )


async def test_update_status_prompt_defect_edit_requires_edited_sample(tmp_path):
    """decision='prompt_defect_edit' 时 edited_sample=None 抛 ValueError。"""
    queue_file = str(tmp_path / ".eval_review_queue.json")
    repo = ReviewQueueRepo(queue_file=queue_file, dataset_repo=AsyncMock())
    item_id = await repo.add({"sample_input": "x"})
    with pytest.raises(ValueError):
        await repo.update_status(
            item_id, status="approved", decision="prompt_defect_edit",
            edited_sample=None,
        )


async def test_update_status_atomic_write_uses_temp_file(tmp_path):
    """update_status 后文件存在且可读(原子写入:临时文件 + rename)。"""
    queue_file = str(tmp_path / ".eval_review_queue.json")
    repo = ReviewQueueRepo(queue_file=queue_file, dataset_repo=AsyncMock())
    item_id = await repo.add({"sample_input": "x"})
    await repo.update_status(
        item_id, status="rejected", decision="model_limitation_drop",
    )
    # 多次决策后文件依然可读
    id2 = await repo.add({"sample_input": "y"})
    await repo.update_status(
        id2, status="rejected", decision="model_limitation_drop",
    )
    all_items = await repo.list_all()
    assert len(all_items) == 2
    # 临时文件应已被清理(原子 rename 后不留 .tmp)
    tmp_files = list(tmp_path.glob("*.tmp"))
    assert tmp_files == []
