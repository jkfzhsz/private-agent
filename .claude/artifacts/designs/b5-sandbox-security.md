# B5 沙箱内存限制 + 网络隔离 Spec

> Status: ALIGNED
> Author: user
> Last updated: 2026-08-01

## Background

B5 修复 P0-7: 沙箱 512MB 内存限制 + 禁网络(M2-AC-6,蓝图 §6.7/§6.8)。

现状: executor.py 无 RLIMIT, service.py 未读 memory_limit_mb, 无网络隔离。

## In scope

- 新增 `sandbox/resource_limiter.py` — ResourceLimiter 类(preexec_fn RLIMIT_AS/CPU)
- service.py 读取 `memory_limit_mb`(默认 512),构造 ResourceLimiter
- executor.py 传 `preexec_fn` 到 `create_subprocess_exec`
- 新增 `_disable_network(env)` 函数 — 应用层代理阻断
- Windows 平台: preexec_fn=None, 仅依赖超时兜底
- config.yaml 确认 `sandbox.limits.memory_limit_mb: 512` 存在

## Out of scope

- Docker 内核级网络隔离(V2)
- psutil 内存监控(Windows 兜底,留 V2)
- 磁盘限制(已实现前置检查)

## Acceptance criteria

- AC-1: ResourceLimiter.get_preexec_fn 在 Linux 返回 callable
- AC-2: ResourceLimiter.get_preexec_fn 在 Windows 返回 None
- AC-3: preexec_fn 设置 RLIMIT_AS 以 512MB
- AC-4: SandboxService 读取 memory_limit_mb 配置
- AC-5: SandboxExecutor.execute 传入 preexec_fn
- AC-6: _disable_network 设置无效代理环境变量
- AC-7: sandbox 子进程环境变量含无效代理
- AC-8: 全量 pytest 通过(737 现有 + B5 新增)