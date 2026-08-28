"""0.6.0 F1-4: EnvironmentProfile —— 网络/硬件环境档案加载与规划注入片段。

设计文档: docs/next-phase-plan-2026-08-28-wuya-long-task-orchestration.md §4.2
- 静态档案来源 config.yaml environment 节(F1-4 已落);
- 注入时机: mission_create 轮(无涯构建 mission 计划时)以缓存片段注入
  system prompt —— 沿用 v2 按需自省理念, 不每轮全量注入;
- 执行时守卫: install_preflight(F1-5)独立拦截, 本模块只管"规划层感知"。

D 批接线点(D-1/D-2): main.py 构建 mission 工具集时调
build_environment_fragment(cfg) 一次并缓存, 拼入 mission_create 轮的
system prompt 附加段。

纯函数(读 cfg dict), 无 IO —— D1 档独立单测(本模块)。
"""
from __future__ import annotations

__all__ = [
    "DEFAULT_ENVIRONMENT",
    "load_environment_profile",
    "build_environment_fragment",
]

# config.yaml 未配置 environment 节时的兜底默认(与 config.yaml F1-4 节一致)
DEFAULT_ENVIRONMENT: dict = {
    "network": {
        "pip_index": "https://mirrors.aliyun.com/pypi/simple/",
        "npm_registry": "https://registry.npmmirror.com",
        "hf_endpoint": "https://hf-mirror.com",
        "blocked_domains": ["google.com", "github.com", "raw.githubusercontent.com", "huggingface.co"],
        "reachable_domains": ["mirrors.aliyun.com", "registry.npmmirror.com", "hf-mirror.com"],
        "proxy_trap": "本机 HTTPS 代理指向失效端口, 出网命令需 NO_PROXY=*",
    },
    "hardware": {
        "mem_budget_mb": 500,
        "cold_start_note": "Python 冷启动约 16s(杀软实时扫描), 勿误判为卡死重试",
    },
    "guard": True,
}


def _deep_merge(base: dict, override: dict) -> dict:
    """dict 深合并(override 优先; 列表/标量整体覆盖)。

    注意 0.5.1 教训(loader._deep_merge): 空 dict 覆盖语义 —— 此处 override
    显式提供空 dict 视为"覆盖为空"(整体语义), 与 loader 行为一致。
    """
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_environment_profile(cfg: dict | None) -> dict:
    """从合并配置加载环境档案(缺省键用 DEFAULT_ENVIRONMENT 补齐)。"""
    env = (cfg or {}).get("environment") or {}
    return _deep_merge(DEFAULT_ENVIRONMENT, env)


def build_environment_fragment(cfg: dict | None) -> str:
    """生成面向模型的规划注入片段(缓存友好: 纯 cfg 派生, 无 IO)。

    用于 mission_create 轮 system prompt 附加段 —— 让无涯在设计计划时即感知:
    镜像源、不可达域名(规划时绕开而非运行时试错)、代理陷阱、内存预算、冷启动特性。
    """
    p = load_environment_profile(cfg)
    net = p["network"]
    hw = p["hardware"]
    lines = [
        "## 运行环境约束(规划 mission 时必须遵守)",
        "- pip 镜像: " + str(net.get("pip_index", "-")),
        "- npm 镜像: " + str(net.get("npm_registry", "-")),
        "- HF 模型镜像: " + str(net.get("hf_endpoint", "-")),
        "- 境内不可达域名(计划中直接绕开, 禁止运行时反复尝试): "
        + ", ".join(net.get("blocked_domains", [])),
        "- 已验证可达: " + ", ".join(net.get("reachable_domains", [])),
    ]
    trap = net.get("proxy_trap")
    if trap:
        lines.append(f"- 代理陷阱: {trap}")
    lines.append(
        f"- 内存预算: 新增常驻进程 ≤{hw.get('mem_budget_mb', 500)}MB"
        "(超过需用户批准)"
    )
    note = hw.get("cold_start_note")
    if note:
        lines.append(f"- 冷启动: {note}")
    if p.get("guard", True):
        lines.append(
            "- 装依赖步骤必须先过 install_preflight 预检; 预检拒绝时改用"
            "上述镜像, 不得跳过预检直接尝试"
        )
    return "\n".join(lines)
