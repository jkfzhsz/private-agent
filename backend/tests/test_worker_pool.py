"""B2.2 - Worker 进程池可 spawn(最小 2 worker)。

Source: plan/m0-implementation step 2 (蓝图 §9.6 step2 + §2.2)
"""
from private_agent.core import executor


def test_default_worker_count_is_two():
    """蓝图 §9.13 system.worker.pool_size: 2。"""
    assert executor.DEFAULT_WORKER_COUNT == 2


def test_worker_pool_can_compute():
    """Worker 进程池可提交任务并返回结果(蓝图 §2.2 纯计算节点)。"""
    result = executor.submit_to_worker(executor.add_one, 41)
    assert result == 42
