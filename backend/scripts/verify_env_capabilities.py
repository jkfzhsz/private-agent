"""环境能力验证：沙箱 Python 路径 / 网络隔离行为 / pip 与关键库可用性。

驱动真实 SandboxService（沙箱执行器真实路径），不依赖后端进程与 DB（conn=None）。
用法（cwd=backend）：
  .venv/Scripts/python.exe scripts/verify_env_capabilities.py
"""
from __future__ import annotations

import asyncio
import os
import sys

BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND_ROOT)
os.chdir(BACKEND_ROOT)

# PA_USER_DATA 与 Electron 侧一致（%APPDATA%\\Private Agent）
os.environ.setdefault(
    "PA_USER_DATA", os.path.join(os.environ.get("APPDATA", ""), "Private Agent")
)

import yaml  # noqa: E402
from private_agent.sandbox.service import SandboxService  # noqa: E402


PROBE_CODE = r"""
import socket, sys, urllib.request, os
print("EXE:", sys.executable)
print("PREFIX:", sys.prefix)
try:
    r = urllib.request.urlopen("https://www.baidu.com", timeout=10)
    print("URLLIB_NET_OK", r.status, r.read(120).decode("utf-8", "replace").strip()[:60])
except Exception as e:
    print("URLLIB_NET_FAIL", type(e).__name__, str(e)[:160])
try:
    s = socket.create_connection(("www.baidu.com", 443), timeout=10)
    s.close()
    print("SOCKET_NET_OK")
except Exception as e:
    print("SOCKET_NET_FAIL", type(e).__name__, str(e)[:160])
print("PROXY:", os.environ.get("HTTPS_PROXY", os.environ.get("https_proxy", "NONE")))
"""

PIP_PROBE = r"""
import sys, subprocess
print("EXE:", sys.executable)
r = subprocess.run([sys.executable, "-m", "pip", "--version"], capture_output=True, text=True, timeout=60)
print("PIP_VERSION:", (r.stdout + r.stderr).strip()[:120])
for m in ["weasyprint", "pptx", "matplotlib", "reportlab", "PIL", "pypdf"]:
    try:
        mod = __import__(m)
        print("MOD", m, "OK", getattr(mod, "__version__", ""))
    except Exception as e:
        print("MOD", m, "MISSING", type(e).__name__)
"""


async def main() -> None:
    with open(os.path.join(BACKEND_ROOT, "config", "config.yaml"), encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    svc = SandboxService(cfg, conn=None)
    print("=== 场景 A: allow_network=False（默认禁网）===")
    ra = await svc.execute(PROBE_CODE, session_id="env-probe-a", allow_network=False)
    print("-- stdout --\n" + ra.stdout)
    print("-- stderr --\n" + (ra.stderr or "")[:500])
    print("exit:", ra.exit_code)
    print()
    print("=== 场景 B: allow_network=True（工具显式放行）===")
    rb = await svc.execute(PROBE_CODE, session_id="env-probe-b", allow_network=True)
    print("-- stdout --\n" + rb.stdout)
    print("-- stderr --\n" + (rb.stderr or "")[:500])
    print("exit:", rb.exit_code)
    print()
    print("=== pip 与关键库探测（沙箱解释器视角）===")
    rc = await svc.execute(PIP_PROBE, session_id="env-probe-c", allow_network=False)
    print("-- stdout --\n" + rc.stdout)
    print("-- stderr --\n" + (rc.stderr or "")[:500])
    print("exit:", rc.exit_code)


if __name__ == "__main__":
    asyncio.run(main())
