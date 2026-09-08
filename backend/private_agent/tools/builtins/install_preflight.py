"""0.6.0 F1-5: install_preflight 依赖安装预检(网络/硬件环境代码级守卫)。

设计文档: docs/next-phase-plan-2026-08-28-wuya-long-task-orchestration.md §4.2
设计原则: **环境约束是代码守卫而非提示词建议** —— 提示词会被模型忽略,
守卫不会。预检拒绝时返回结构化原因 + 替代镜像建议(模型可理解, 一次改道即成功),
而非让子代理在运行时反复试错(直接响应"境内网络不可盲目安装依赖"约束)。

检查项:
1. 域名连通性快测(HEAD 3s 超时) —— blocked/reachable 清单来自
   EnvironmentProfile(F1-4), 动态结果写入快照供裁决;
2. 内存水位(psutil) —— 需求超 mem_budget_mb 余量 → 拒绝(0.5.1 裁决线: 新增
   常驻 >0.5GB 即风险区);
3. 镜像建议 —— 目标域名不可达时按 kind 给出镜像(pip/npm/hf)。

使用方式:
- 独立工具 install_preflight(模型在装依赖前显式调用, ToolDef 见文末);
- 核心函数 preflight_check(...) 供 D 批 code_execution 装依赖路径编程调用。
"""
from __future__ import annotations

import time
from typing import Any

from private_agent.tools.defs import ToolDef, ToolResult

__all__ = [
    "preflight_check",
    "mirror_suggestion",
    "build_install_preflight_tool",
    "PREFLIGHT_TIMEOUT_SEC",
]

PREFLIGHT_TIMEOUT_SEC = 3.0

# 域名→镜像建议(EnvironmentProfile 默认档案同源; profile 可覆盖)
MIRROR_SUGGESTIONS: dict[str, str] = {
    "pip": "改用 pip 镜像: https://mirrors.aliyun.com/pypi/simple/ (-i 参数)",
    "npm": "改用 npm 镜像: https://registry.npmmirror.com (--registry 参数)",
    "hf": "改用 HF 镜像: https://hf-mirror.com (HF_ENDPOINT 环境变量)",
}


def mirror_suggestion(kind: str) -> str:
    """按资源类型给出镜像建议(未知类型给通用建议)。"""
    return MIRROR_SUGGESTIONS.get(kind, "改用境内可达镜像源(见 EnvironmentProfile)")


async def preflight_check(
    cfg: dict | None,
    *,
    domains: list[str] | None = None,
    mem_required_mb: float = 0,
    probe: bool = True,
) -> dict[str, Any]:
    """预检核心(纯 asyncio, 无模型调用)。

    Args:
        cfg: 合并配置(EnvironmentProfile 读取)。
        domains: 计划要访问的域名清单(如 pypi.org, files.pythonhosted.org)。
        mem_required_mb: 新增常驻内存需求 MB(装包场景 >0)。
        probe: 是否真实探测网络(测试可 False 只做清单/内存判定)。

    Returns:
        {
          "ok": bool,                    # 全部通过才 True
          "domain_results": [{domain, reachable, blocked, error?}],
          "mem": {required, avail, budget_ok},
          "suggestions": [str],          # 拒绝时的镜像建议(可能为空)
          "elapsed_sec": float,
        }
    """
    from private_agent.core.environment_profile import load_environment_profile

    started = time.monotonic()
    profile = load_environment_profile(cfg)
    net = profile["network"]
    hw = profile["hardware"]
    blocked = set(net.get("blocked_domains", []))
    reachable_known = set(net.get("reachable_domains", []))

    domain_results: list[dict] = []
    suggestions: list[str] = []
    for d in domains or []:
        host = d.strip().lower()
        entry: dict[str, Any] = {"domain": host, "blocked": False, "reachable": False}
        if any(host == b or host.endswith("." + b) for b in blocked):
            entry["blocked"] = True
            entry["error"] = "清单内不可达域名(EnvironmentProfile blocked_domains)"
            suggestions.append(f"{host}: {mirror_suggestion('pip' if 'pypi' in host or 'python' in host else 'npm' if 'npm' in host else 'hf' if 'hf' in host or 'hugging' in host else 'generic')}")
            domain_results.append(entry)
            continue
        if probe and host not in reachable_known:
            # 未验证过的域名 → 动态快测(HEAD 3s; reachable 清单内免测)
            ok, err = await _probe_domain(host)
            entry["reachable"] = ok
            if not ok:
                entry["error"] = err
                suggestions.append(f"{host}: {mirror_suggestion('generic')}")
        else:
            entry["reachable"] = True  # 清单内已验证/不探测
        domain_results.append(entry)

    # 内存水位判定(psutil; 获取失败不阻塞 —— 静默放行, 由 system_metrics 观测)
    mem_info: dict[str, Any] = {
        "required": mem_required_mb,
        "avail": None,
        "budget_ok": True,
    }
    if mem_required_mb > 0:
        try:
            import psutil

            avail_mb = psutil.virtual_memory().available / (1024 * 1024)
            mem_info["avail"] = round(avail_mb, 1)
            mem_info["budget_ok"] = avail_mb >= mem_required_mb
            if not mem_info["budget_ok"]:
                suggestions.append(
                    f"内存不足: 需 {mem_required_mb}MB, 可用 {mem_info['avail']}MB"
                    f"(预算线 {hw.get('mem_budget_mb')}MB)。放弃安装或提请用户批准。"
                )
        except Exception:  # noqa: BLE001
            mem_info["avail"] = None  # psutil 不可用: 放行, 不假拒

    ok = (
        all(r["reachable"] for r in domain_results)
        and mem_info["budget_ok"]
    )
    return {
        "ok": ok,
        "domain_results": domain_results,
        "mem": mem_info,
        "suggestions": suggestions,
        "elapsed_sec": round(time.monotonic() - started, 3),
    }


async def _probe_domain(host: str) -> tuple[bool, str | None]:
    """域名连通快测(HEAD, 3s 超时; 任意 DNS/TCP/TLS 失败即不可达)。"""
    try:
        import httpx

        # 2026-09-08: trust_env=False —— 连通预检必须测"直连能力", 否则会被
        # 失效系统代理(31181)污染为"不可达", 误导依赖安装决策。
        async with httpx.AsyncClient(
            timeout=PREFLIGHT_TIMEOUT_SEC, trust_env=False
        ) as client:
            resp = await client.head(f"https://{host}/", follow_redirects=True)
            # 4xx/5xx 也算"可达"(网络通, 服务拒绝是另一回事)
            _ = resp.status_code
            return True, None
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}"


def _format_output(result: dict[str, Any]) -> str:
    """结构化结果 → 面向模型可读文本(拒绝时建议直接给出, 一次改道即成功)。"""
    lines: list[str] = [f"preflight {'PASS' if result['ok'] else 'REJECTED'}"]
    for r in result["domain_results"]:
        state = "OK" if r["reachable"] else "FAIL"
        reason = f" ({r['error']})" if r.get("error") else ""
        lines.append(f"- {r['domain']}: {state}{reason}")
    mem = result["mem"]
    if mem["required"] > 0:
        lines.append(
            f"- 内存: 需 {mem['required']}MB / 可用 {mem['avail']}MB "
            f"{'OK' if mem['budget_ok'] else 'FAIL'}"
        )
    for s in result["suggestions"]:
        lines.append(f"建议: {s}")
    return "\n".join(lines)


def build_install_preflight_tool(cfg: dict) -> ToolDef:
    """构建 install_preflight 工具(闭包注入 cfg; main.py D 批装配)。"""

    async def _handler(args: dict) -> ToolResult:
        domains = args.get("domains") or []
        if not isinstance(domains, list) or not domains:
            return ToolResult(
                output="",
                error="install_preflight: domains 必填(计划访问的域名清单)",
            )
        mem_required = float(args.get("mem_required_mb") or 0)
        result = await preflight_check(
            cfg, domains=[str(d) for d in domains], mem_required_mb=mem_required
        )
        # 结果入 ToolResult.metadata(供 D 批 code_execution 守卫复核)
        return ToolResult(
            output=_format_output(result),
            error=None,
            metadata={"preflight": result},
        )

    return ToolDef(
        name="install_preflight",
        description=(
            "依赖安装/出网操作前置预检(强制): 校验目标域名在本机网络环境下"
            "可达性(境内网络约束)与内存水位。返回 PASS 才可执行安装; REJECTED"
            "时按返回的镜像建议改道, 禁止跳过预检直接尝试。"
        ),
        parameters_schema={
            "type": "object",
            "properties": {
                "domains": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "计划访问的域名清单(如 pypi.org, registry.npmjs.org)",
                },
                "mem_required_mb": {
                    "type": "number",
                    "description": "新增常驻内存需求 MB(装大包/模型权重时填)",
                },
            },
            "required": ["domains"],
        },
        handler=_handler,
        safety_level="none",
        risk_level="low",
        is_kernel=True,  # 环境守卫必须始终可见
    )
