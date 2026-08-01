"""蓝图 §2.14 异常分类体系。

M0 阶段仅定义 config 相关异常;后续模块按需扩展。
"""


class PrivateAgentError(Exception):
    """所有 Private Agent 异常的基类(蓝图 §2.14)。"""


class ConfigError(PrivateAgentError):
    """配置相关错误基类。"""


class ConfigNotSupportedInMVP(ConfigError):
    """配置项值在 MVP 阶段不支持(蓝图 §9.13)。

    例:tools.mcp.protocol_version == '2026-07-28' 在 MVP 锁定为 '2025-11-25',
    loader 对不支持的值抛此异常,防止 UI 误改导致静默失败。
    """


class McpHttpStubNotImplementedError(PrivateAgentError):
    """MCP HTTP 模式在 MVP 阶段未实现(蓝图 §5.x / spec m2-tools-lifecycle)。

    MVP 仅支持 stdio 模式通信;HTTP 模式仅保留类型定义和配置解析 stub,
    调用 connect/discover/call 时抛此异常。
    """


class SandboxTimeoutError(PrivateAgentError):
    """沙箱执行超时(蓝图 6.7 / spec m2-sandbox AC-2)。

    子进程执行超过 cpu_timeout_sec 时抛出,process.terminate() 后触发。
    """


class SandboxResourceError(PrivateAgentError):
    """沙箱资源超限(蓝图 6.7 / spec m2-sandbox)。

    磁盘超限/内存超限(Windows 无 setrlimit 时兜底)时抛出。
    """


class FrozenHashMismatchError(PrivateAgentError):
    """Frozen Zone hash 校验失败异常(B1 P1-4)。

    ensure_initial 加载时或 replace_frozen_zone 写入后,compute_frozen_hash()
    与 sessions.frozen_hash 不一致时抛出。

    环境变量 PA_FROZEN_HASH_VERIFY=0 可关闭校验(逃生通道,默认开启)。
    """
