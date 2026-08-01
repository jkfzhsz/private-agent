# B5 沙箱安全 实施方案

> Status: APPROVED
> Source: .claude/artifacts/designs/b5-sandbox-security.md
> Iterations: 1 / 3

## RALPLAN-DR

### Principles
- **最小代码**: 新增 1 模块, 修改 2 文件, 约 80 行
- **平台兼容**: Windows preexec_fn=None, 仅超时兜底
- **配置可关**: `memory_limit_mb: 0` 跳过 RLIMIT

### Implementation steps

1. 新增 `backend/private_agent/sandbox/resource_limiter.py` — ResourceLimiter + _disable_network
2. 修改 `service.py` — 读 memory_limit_mb, 构造 ResourceLimiter
3. 修改 `executor.py` — 传 preexec_fn 到 create_subprocess_exec
4. 测试: `test_resource_limiter.py` — 6 测试(AC-1..7)
5. 全量 pytest

### ADR
- 网络隔离: 应用层代理阻断(HTTP_PROXY=invalid), 非 Docker 内核级
- 内存限制: Linux RLIMIT_AS, Windows 仅超时兜底