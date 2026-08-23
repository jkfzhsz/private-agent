"""沙箱 Python 解释器切换至 D 盘 venv（config_runtime 覆盖，铁律一：依赖不装 C 盘）。

背景（2026-08-15 环境验证）：
- 沙箱默认解释器 = 系统 Python 3.10（C 盘 site-packages），新增依赖会落 C 盘，违反铁律。
- 决策：依赖统一装 D 盘 venv（backend/.venv），config_runtime 覆盖
  sandbox.languages.python.command → venv 的 python.exe。
- 本脚本幂等可复现（对齐 configure_glm_vision.py 风格）。

用法（cwd=backend，PA_DB_PASSWORD 从 backend/.env 加载）：
  .venv/Scripts/python.exe scripts/configure_sandbox_python.py [venv_python_path]
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
os.chdir(BACKEND)
sys.path.insert(0, str(BACKEND))

DSN = "postgresql://postgres:{pwd}@127.0.0.1:5432/private_agent"


def _load_env() -> None:
    env_path = BACKEND / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


async def main() -> None:
    _load_env()
    pwd = os.environ.get("PA_DB_PASSWORD")
    if not pwd:
        sys.exit("PA_DB_PASSWORD 未找到（backend/.env 缺失或未设置）")
    dsn = DSN.format(pwd=pwd)

    venv_py = str(Path(sys.executable).resolve()) if len(sys.argv) < 2 else sys.argv[1]
    if not Path(venv_py).is_file():
        sys.exit(f"venv python 不存在: {venv_py}")

    import asyncpg

    from private_agent.config import loader

    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute(
            """
            INSERT INTO config_runtime (key, value) VALUES ($1, $2::jsonb)
            ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
            """,
            "sandbox.languages.python.command",
            json.dumps(venv_py),
        )
        cfg = await loader.load_config_with_overrides(conn)
    finally:
        await conn.close()

    actual = cfg["sandbox"]["languages"]["python"]["command"]
    assert actual == venv_py, f"合并后未生效: {actual} != {venv_py}"
    print("config_runtime 写入成功, sandbox.languages.python.command =", actual)

    # 真实沙箱执行验证：解释器 + 关键库
    from private_agent.sandbox.service import SandboxService

    svc = SandboxService(cfg, conn=None)
    code = (
        "import sys\n"
        "print('EXE:', sys.executable)\n"
        "for m in ['pptx', 'matplotlib', 'PIL', 'typst', 'pypdf']:\n"
        "    try:\n"
        "        mod = __import__(m)\n"
        "        print('MOD', m, 'OK', getattr(mod, '__version__', ''))\n"
        "    except Exception as e:\n"
        "        print('MOD', m, 'MISSING', type(e).__name__)\n"
    )
    r = await svc.execute(code, session_id="sandbox-py-verify", allow_network=False)
    print("-- 沙箱输出 --")
    print(r.stdout)
    if r.exit_code != 0:
        print("stderr:", r.stderr[:500])


if __name__ == "__main__":
    asyncio.run(main())
