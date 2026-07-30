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
