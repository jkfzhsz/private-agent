"""测试 errors.py - SandboxTimeoutError, SandboxResourceError。"""
from __future__ import annotations

import pytest

from private_agent.errors import (
    PrivateAgentError,
    SandboxResourceError,
    SandboxTimeoutError,
)


def test_sandbox_timeout_error_is_private_agent_error() -> None:
    """SandboxTimeoutError 继承自 PrivateAgentError。"""
    err = SandboxTimeoutError("timeout")
    assert isinstance(err, PrivateAgentError)


def test_sandbox_resource_error_is_private_agent_error() -> None:
    """SandboxResourceError 继承自 PrivateAgentError。"""
    err = SandboxResourceError("disk limit")
    assert isinstance(err, PrivateAgentError)


def test_sandbox_errors_raise_and_catch() -> None:
    """两种异常可 raise 和 caught。"""
    with pytest.raises(SandboxTimeoutError):
        raise SandboxTimeoutError("execution exceeded 300s")
    with pytest.raises(SandboxResourceError):
        raise SandboxResourceError("disk limit exceeded 100MB")