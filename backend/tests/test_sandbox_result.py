"""测试 sandbox/result.py - SandboxResult + CodeWarning dataclass。"""
from __future__ import annotations

from private_agent.sandbox.result import CodeWarning, SandboxResult


def test_sandbox_result_defaults() -> None:
    """SandboxResult 默认值: duration_ms=0, generated_files=[], warnings=[]。"""
    r = SandboxResult(stdout="out", stderr="err", exit_code=0)
    assert r.stdout == "out"
    assert r.stderr == "err"
    assert r.exit_code == 0
    assert r.generated_files == []
    assert r.warnings == []
    assert r.duration_ms == 0


def test_sandbox_result_full() -> None:
    """SandboxResult 全字段构造。"""
    r = SandboxResult(
        stdout="hello",
        stderr="",
        exit_code=0,
        generated_files=["outputs/result.csv"],
        warnings=[CodeWarning(pattern="os.system", line=1, snippet="os.system('ls')")],
        duration_ms=123,
    )
    assert r.generated_files == ["outputs/result.csv"]
    assert len(r.warnings) == 1
    assert r.warnings[0].pattern == "os.system"
    assert r.warnings[0].line == 1
    assert r.warnings[0].snippet == "os.system('ls')"
    assert r.duration_ms == 123


def test_code_warning_fields() -> None:
    """CodeWarning 字段完整。"""
    w = CodeWarning(pattern="subprocess.run", line=5, snippet="subprocess.run(['ls'])")
    assert w.pattern == "subprocess.run"
    assert w.line == 5
    assert w.snippet == "subprocess.run(['ls'])"