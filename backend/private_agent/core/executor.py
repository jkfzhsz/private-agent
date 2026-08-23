"""蓝图 §2.15 core/executor.py - asyncio + ProcessPool 双轨 (§2.2/§2.6).

B2.2:最小 ProcessPoolExecutor(max_workers=2)+ 同步提交。
后续:§2.6 asyncio loop.run_in_executor 集成。
"""
from __future__ import annotations

import concurrent.futures
from typing import Any, Callable

# 蓝图 §9.13 system.worker.pool_size: 2
DEFAULT_WORKER_COUNT = 2

# 模块级进程池(懒创建)
_pool: concurrent.futures.ProcessPoolExecutor | None = None


def add_one(x: int) -> int:
    """B2.2 测试用模块级函数(可 pickle,Windows spawn 模式必需)。"""
    return x + 1


def get_pool() -> concurrent.futures.ProcessPoolExecutor:
    """获取进程池实例(懒创建,蓝图 §2.2 默认 2 worker)。"""
    global _pool
    if _pool is None:
        _pool = concurrent.futures.ProcessPoolExecutor(max_workers=DEFAULT_WORKER_COUNT)
    return _pool


def submit_to_worker(fn: Callable[..., Any], *args: Any) -> Any:
    """提交任务到 Worker 进程池并同步等待结果(B2.2 同步版)。

    后续 §2.6 将提供异步版本(asyncio loop.run_in_executor)。
    """
    pool = get_pool()
    future = pool.submit(fn, *args)
    return future.result()
