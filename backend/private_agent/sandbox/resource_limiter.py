"""蓝图 §6.7 沙箱资源限制 — 内存 RLIMIT_AS + 网络隔离(应用层)。

B5 P0-7: Linux/macOS 用 resource.setrlimit,Windows 返回 None(仅超时兜底)。
网络隔离: 应用层代理阻断(HTTP_PROXY=invalid),V2 用 Docker --network=none。
"""
from __future__ import annotations

import os


def disable_network(env: dict[str, str]) -> dict[str, str]:
    """应用层网络隔离(蓝图 §6.7 line 5432-5444, 阶段二批次 3 修正)。

    设置 HTTP_PROXY/HTTPS_PROXY 为无效代理(本机无服务的端口), 阻断子进程
    经 HTTP 库(requests/httpx 等读环境变量的库)访问网络。

    修正说明(2026-08-04): 原实现同时设置 NO_PROXY=* —— 与代理拦截
    自相矛盾(所有主机绕过代理直连, 拦截完全失效)。现已移除 NO_PROXY。

    已知边界: 仅对读环境变量代理的 HTTP 库有效; socket 直连 / Windows
    内置 urllib(读注册表代理)可绕过 —— 见 docs/security-model.md。
    """
    result = dict(env)
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
                "ALL_PROXY", "all_proxy"):
        result[key] = "http://127.0.0.1:9"  # 本机无服务的端口 → 连接必然失败
    # 不设置 NO_PROXY(否则绕过代理直连)
    result.pop("NO_PROXY", None)
    result.pop("no_proxy", None)
    return result


class ResourceLimiter:
    """子进程资源限制器(蓝图 §6.7)。

    内存: Linux/macOS 用 RLIMIT_AS,Windows 不支持(返回 None)。
    CPU: 用 RLIMIT_CPU 软限制,超时由 asyncio.wait_for 兜底。
    """

    def __init__(self, memory_limit_mb: int, cpu_timeout_sec: int) -> None:
        self.memory_limit = memory_limit_mb * 1024 * 1024
        self.cpu_timeout = cpu_timeout_sec

    def get_preexec_fn(self):
        """返回 preexec_fn(仅 Linux/macOS,Windows 返回 None)。"""
        if os.name == "nt":
            return None
        import resource

        memory_limit = self.memory_limit
        cpu_timeout = self.cpu_timeout

        def _set_limits():
            resource.setrlimit(resource.RLIMIT_AS, (memory_limit, memory_limit))
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_timeout, cpu_timeout))

        return _set_limits