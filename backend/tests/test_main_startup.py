"""B2.1 - Sidecar 入口可启动并打印 HTTP 端口 8765。

Source: plan/m0-implementation step 2 (蓝图 §9.6 step2 + §2.2 uvicorn+asyncio)
"""
import subprocess
import sys
import time


def test_sidecar_startup_prints_http_port():
    """`python -m private_agent.main` 启动后 stdout 包含 HTTP 端口 8765。"""
    proc = subprocess.Popen(
        [sys.executable, "-m", "private_agent.main"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
    )
    try:
        deadline = time.time() + 15
        output_lines: list[str] = []
        while time.time() < deadline:
            line = proc.stdout.readline()
            if not line:
                break
            output_lines.append(line.rstrip())
            if "8765" in line:
                break
        assert any("8765" in line for line in output_lines), (
            f"Expected port 8765 in stdout, got: {output_lines}"
        )
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
