"""蓝图 §6.7 沙箱资源限制 — 内存 RLIMIT_AS + 网络隔离(应用层)。

B5 P0-7: Linux/macOS 用 resource.setrlimit,Windows 返回 None(仅超时兜底)。
网络隔离: 应用层代理阻断(HTTP_PROXY=invalid),V2 用 Docker --network=none。
"""
from __future__ import annotations

import os


def disable_network(env: dict[str, str]) -> dict[str, str]:
    """应用层网络隔离(蓝图 §6.7 line 5432-5444)。

    设置 HTTP_PROXY/HTTPS_PROXY 为 invalid,阻断子进程网络访问。
    """
    result = dict(env)
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        result[key] = "invalid"
    result["NO_PROXY"] = "*"
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