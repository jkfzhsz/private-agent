"""阶段2(agent-upgrader 设计文档 §2.1/§4): pytest_run —— PA 自身测试运行器。

无涯(monitor)开发闭环核心工具: 在 PA 后端源码树跑 pytest(含加载 .env),
验证代码改动无回归 —— 打通"改代码 → 跑测试 → 提交"全链路。

安全边界(蒋先生 2026-08-16 拍板决策 2):
- 仅 monitor 会话装配(专属白名单, 场景会话不可见)
- cwd = 会话工作区下的 backend(PA 后端源码树)
- 继承当前进程环境(PA_DB_PASSWORD/PA_MASTER_KEY 已加载, Electron 启动时
  注入; 只读使用, 不落盘不回传)
- 限制: 仅允许 pytest 命令; 测试库并发保护(勿与全量回归同时跑);
  timeout 兜底; 输出截断

用法: pytest_run(tests="tests/test_xxx.py", timeout=180)
  - tests 为空 → 全量(忽略 test_eval_full_cycle.py)
  - 建议无涯聚焦单文件/单用例, 全量由用户或 CI 承担
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from private_agent.tools.defs import ToolDef, ToolResult

logger = logging.getLogger(__name__)

__all__ = ["PYTEST_RUN_TOOL", "_pytest_run_handler", "resolve_backend_dir"]

# 输出截断上限(≈ 4k token)
_MAX_OUTPUT_CHARS = 4000 * 4
# 默认超时(秒) —— 单文件聚焦测试; 全量回归由用户手动跑
_DEFAULT_TIMEOUT = 180


def resolve_backend_dir(workspace: str) -> str | None:
    """从会话工作区解析 PA 后端源码树路径。

    - workspace = PA 源码根(D:\\Private agent) → {workspace}/backend
    - workspace 已是 backend 目录(兼容) → 原样
    """
    root = os.path.expandvars(str(workspace))
    candidate = Path(root) / "backend"
    if (candidate / "config" / "config.yaml").exists():
        return str(candidate)
    if (Path(root) / "config" / "config.yaml").exists():
        return root
    return None


async def _pytest_run_handler(args: dict) -> ToolResult:
    """在 PA 后端跑 pytest(开发沙箱, 决策 2)。

    Args:
        tests: 测试路径/选择器(可选, 如 'tests/test_harness.py' 或
               'tests/test_harness.py::test_xxx'; 空=全量忽略 eval_full_cycle)。
        timeout: 超时秒数(默认 180)。
        workspace: 会话工作区(PA 源码根), 服务端自动注入。
    """
    ws = args.get("workspace") or args.get("data_dir")
    if not ws:
        return ToolResult(output="", error="workspace required(pytest_run 需会话工作区)")
    backend_dir = resolve_backend_dir(ws)
    if backend_dir is None:
        return ToolResult(
            output="", error=f"未找到 PA 后端源码树(workspace={ws}), 无法跑 pytest"
        )
    tests = str(args.get("tests") or "").strip()
    timeout = int(args.get("timeout") or _DEFAULT_TIMEOUT)

    cmd = [
        ".venv/Scripts/python.exe", "-m", "pytest",
        "-q", "-p", "no:cacheprovider",
        "--timeout=180", "--timeout-method=thread",
    ]
    if tests:
        # 2026-08-16(阶段2 实测反馈): 支持空格分隔的多个选择器 ——
        # 无涯一次传 'tests/a.py tests/b.py' 时原实现拼成单个字符串参数
        # → pytest 报 "file or directory not found"。shlex 按 shell 规则拆分。
        import shlex

        selectors = shlex.split(tests)
        if not selectors:
            return ToolResult(output="", error="tests 参数无效(空)")
        cmd.extend(selectors)
    else:
        # 全量: 忽略 eval_full_cycle(依赖真实 LLM 链路, 不在开发闭环内)
        cmd.append("--ignore=test_eval_full_cycle.py")

    # 环境: 继承当前进程(PA_DB_PASSWORD/PA_MASTER_KEY 已注入), 覆盖 WORKSPACE
    env = dict(os.environ)
    env["WORKSPACE"] = "backend"
    # 测试库隔离: pytest 用 PA_TEST_DSN(默认 private_agent_test), 不污染生产库
    env.setdefault(
        "PA_TEST_DSN",
        "postgresql://postgres:123123@localhost:5432/private_agent_test",
    )

    import subprocess

    try:
        proc = await asyncio.to_thread(
            subprocess.run,
            cmd,
            cwd=backend_dir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            shell=False,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return ToolResult(output="", error=f"pytest 超时({timeout}s), 建议聚焦单文件")
    except FileNotFoundError:
        return ToolResult(output="", error="venv python 未找到(backend/.venv 缺失)")
    except Exception as e:  # noqa: BLE001
        return ToolResult(output="", error=f"pytest 执行异常: {type(e).__name__}: {e}")

    # 输出: 取最后部分(总结行), 失败时带错误摘要
    stdout = (proc.stdout or "") + (proc.stderr or "")
    tail = stdout.strip()[-_MAX_OUTPUT_CHARS:] if stdout else ""
    if proc.returncode == 0:
        summary = (
            "pytest 全部通过 ✅\n" + tail[-1500:]
            if tail else "pytest 全部通过 ✅"
        )
        return ToolResult(output=summary)
    # 失败: 定位失败项 + 总结
    failed_lines = [
        ln for ln in (proc.stdout or "").splitlines()
        if ln.startswith("FAILED") or "failed" in ln and "passed" in ln
    ]
    detail = "\n".join(failed_lines[-5:]) if failed_lines else tail[-800:]
    return ToolResult(
        output="",
        error=f"pytest 失败(exit={proc.returncode})\n{detail}",
    )


PYTEST_RUN_TOOL = ToolDef(
    name="pytest_run",
    description=(
        "在 PA 后端源码树运行 pytest 测试(开发沙箱)。用于验证代码改动无回归。"
        "tests 可限定单文件/用例(推荐, 快); 空=全量(忽略 eval_full_cycle, 慢)。"
        "自动加载后端环境(WORKSPACE=backend + 测试库), 只读安全。"
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "tests": {
                "type": "string",
                "description": (
                    "测试选择器(可选): 'tests/test_harness.py' 或 "
                    "'tests/test_harness.py::test_xxx'; 空=全量"
                ),
            },
            "timeout": {
                "type": "integer",
                "description": "超时秒数(默认 180)",
            },
            "workspace": {
                "type": "string",
                "description": "会话工作区(PA 源码根), 服务端自动注入",
            },
        },
    },
    handler=_pytest_run_handler,
    is_kernel=False,
    safety_level="safe",  # 只读运行测试, 不落盘; 测试库隔离
    risk_level="low",
)
