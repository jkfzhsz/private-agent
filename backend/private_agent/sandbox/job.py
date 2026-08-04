"""Windows Job Object 沙箱资源约束(阶段二批次 3, 审查 A.1.1/B.1.1)。

背景: SandboxExecutor 直接 create_subprocess_exec, Windows 上无任何资源
隔离(RLIMIT 仅 POSIX 有效)——恶意代码可无限吃内存/CPU、无限 spawn 进程、
创建窗口。本模块用 ctypes 调 kernel32 实现 Job Object 约束, 零新依赖:

- 内存上限(JOB_OBJECT_LIMIT_PROCESS_MEMORY): 超限分配失败(MemoryError)
- CPU 总时长(JOB_OBJECT_LIMIT_JOB_TIME): 超时由系统终止全部进程
- 活动进程数(JOB_OBJECT_LIMIT_ACTIVE_PROCESS): 防进程树爆炸
- KILL_ON_JOB_CLOSE: Job 句柄释放即杀全树(句柄须在子进程结束后才关闭)
- UI 限制: 禁剪贴板写/系统参数/退出窗口等

已知边界:
- 若父进程(本 sidecar)自身处于某 Job 且未设置 BREAKAWAY_OK, AssignProcessToJobObject
  会失败(ERROR_ACCESS_DENIED) → attach_pid 返回 False 降级, 不阻断执行
- create_subprocess_exec 返回后子进程已启动, attach 存在毫秒级竞态(超短代码
  可能在 attach 前完成)——严格 CREATE_SUSPENDED 方案列为后续增强
"""
from __future__ import annotations

import ctypes
import logging
import os
from ctypes import wintypes

logger = logging.getLogger(__name__)

# JobObjectInfoClass
_JobObjectExtendedLimitInformation = 9
_JobObjectBasicUIRestrictions = 4

# LimitFlags
_JOB_OBJECT_LIMIT_JOB_TIME = 0x00000004
_JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
_JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
_JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION = 0x00000400
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000

# UI 限制(基础集: 不限制 HANDLES 以免破坏正常读写)
_JOB_OBJECT_UILIMIT_READCLIPBOARD = 0x0002
_JOB_OBJECT_UILIMIT_WRITECLIPBOARD = 0x0004
_JOB_OBJECT_UILIMIT_SYSTEMPARAMETERS = 0x0008
_JOB_OBJECT_UILIMIT_DISPLAYSETTINGS = 0x0010
_JOB_OBJECT_UILIMIT_GLOBALATOMS = 0x0020
_JOB_OBJECT_UILIMIT_DESKTOP = 0x0040
_JOB_OBJECT_UILIMIT_EXITWINDOWS = 0x0080

# OpenProcess 权限
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001

_ERROR_ACCESS_DENIED = 5


class _IO_COUNTERS(ctypes.Structure):
    _fields_ = [
        ("ReadOperationCount", ctypes.c_ulonglong),
        ("WriteOperationCount", ctypes.c_ulonglong),
        ("OtherOperationCount", ctypes.c_ulonglong),
        ("ReadTransferCount", ctypes.c_ulonglong),
        ("WriteTransferCount", ctypes.c_ulonglong),
        ("OtherTransferCount", ctypes.c_ulonglong),
    ]


class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
        ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
        ("IoInfo", _IO_COUNTERS),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _JOBOBJECT_BASIC_UI_RESTRICTIONS(ctypes.Structure):
    _fields_ = [("UIRestrictionsClass", wintypes.DWORD)]


def _kernel32():
    return ctypes.windll.kernel32


class SandboxJob:
    """Windows Job Object 沙箱(资源/进程/UI 限制 + KILL_ON_JOB_CLOSE)。

    用法:
        job = SandboxJob(memory_limit_mb=512, cpu_timeout_sec=300,
                         active_process_limit=4)
        # 子进程 spawn 后尽快 attach_pid(pid)
        ok = job.attach_pid(pid)
        # 子进程全部结束后 close()(KILL_ON_JOB_CLOSE: 提前关闭会杀进程)
        job.close()
    """

    def __init__(
        self,
        memory_limit_mb: int = 512,
        cpu_timeout_sec: int = 300,
        active_process_limit: int = 4,
    ) -> None:
        self._memory_limit = memory_limit_mb * 1024 * 1024
        self._cpu_timeout_100ns = cpu_timeout_sec * 10_000_000  # 1s = 1e7 个 100ns 单位
        self._active_process_limit = active_process_limit
        self._hjob = None  # type: ignore[assignment]
        self._closed = False

    # ── 生命周期 ──────────────────────────────────────────────────────────────

    def _create(self) -> None:
        """创建 Job 并设置限制(惰性, 首次 attach 时)。"""
        if self._hjob is not None:
            return
        k32 = _kernel32()
        hjob = k32.CreateJobObjectW(None, None)
        if not hjob:
            raise OSError(
                f"CreateJobObjectW failed: winerror={ctypes.get_last_error()}"
            )
        self._hjob = hjob

        limit_flags = (
            _JOB_OBJECT_LIMIT_JOB_TIME
            | _JOB_OBJECT_LIMIT_ACTIVE_PROCESS
            | _JOB_OBJECT_LIMIT_PROCESS_MEMORY
            | _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        )
        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.PerJobUserTimeLimit = wintypes.LARGE_INTEGER(
            self._cpu_timeout_100ns
        )
        info.BasicLimitInformation.LimitFlags = limit_flags
        info.BasicLimitInformation.ActiveProcessLimit = self._active_process_limit
        info.ProcessMemoryLimit = self._memory_limit
        ok = k32.SetInformationJobObject(
            hjob,
            _JobObjectExtendedLimitInformation,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not ok:
            self._close_handle(hjob)
            self._hjob = None  # type: ignore[assignment]
            raise OSError(
                f"SetInformationJobObject failed: winerror={ctypes.get_last_error()}"
            )

        # UI 限制: 禁剪贴板写/系统参数/显示设置/全局原子/桌面/退出窗口
        ui = _JOBOBJECT_BASIC_UI_RESTRICTIONS(
            _JOB_OBJECT_UILIMIT_READCLIPBOARD
            | _JOB_OBJECT_UILIMIT_WRITECLIPBOARD
            | _JOB_OBJECT_UILIMIT_SYSTEMPARAMETERS
            | _JOB_OBJECT_UILIMIT_DISPLAYSETTINGS
            | _JOB_OBJECT_UILIMIT_GLOBALATOMS
            | _JOB_OBJECT_UILIMIT_DESKTOP
            | _JOB_OBJECT_UILIMIT_EXITWINDOWS
        )
        k32.SetInformationJobObject(
            hjob,
            _JobObjectBasicUIRestrictions,
            ctypes.byref(ui),
            ctypes.sizeof(ui),
        )
        logger.debug("sandbox job created (mem=%dMB cpu=%ds procs=%d)",
                     self._memory_limit // (1024 * 1024),
                     self._cpu_timeout_100ns // 10_000_000,
                     self._active_process_limit)

    def attach_pid(self, pid: int) -> bool:
        """将子进程 pid 挂入 Job, 成功返回 True。

        Failures 降级返回 False(不阻断执行): 子进程已退出 / 父进程在
        其他 Job 且无 BREAKAWAY(ERROR_ACCESS_DENIED) / 平台非 Windows。
        """
        if os.name != "nt" or pid <= 0:
            return False
        try:
            self._create()
            if self._hjob is None:
                return False
            k32 = _kernel32()
            hproc = k32.OpenProcess(
                _PROCESS_SET_QUOTA | _PROCESS_TERMINATE, False, pid
            )
            if not hproc:
                logger.warning(
                    "sandbox job: OpenProcess(%d) failed winerror=%d",
                    pid, ctypes.get_last_error(),
                )
                return False
            try:
                ok = k32.AssignProcessToJobObject(self._hjob, hproc)
                if not ok:
                    err = ctypes.get_last_error()
                    if err == _ERROR_ACCESS_DENIED:
                        logger.warning(
                            "sandbox job: AssignProcessToJobObject denied "
                            "(parent in another job without BREAKAWAY), "
                            "fallback to unconstrained run"
                        )
                    else:
                        logger.warning(
                            "sandbox job: AssignProcessToJobObject failed "
                            "winerror=%d", err,
                        )
                    return False
                return True
            finally:
                k32.CloseHandle(hproc)
        except OSError as e:  # 句柄操作异常 → 降级
            logger.warning("sandbox job attach failed: %s", e)
            return False

    def close(self) -> None:
        """释放 Job 句柄(必须在 Job 内进程全部结束后调用)。

        KILL_ON_JOB_CLOSE 语义: 句柄关闭时若有进程仍在内 → 全部终止。
        """
        if self._closed:
            return
        self._closed = True
        if self._hjob is not None:
            self._close_handle(self._hjob)
            self._hjob = None  # type: ignore[assignment]

    @staticmethod
    def _close_handle(h) -> None:
        try:
            _kernel32().CloseHandle(h)
        except Exception:  # noqa: BLE001
            pass

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:  # noqa: BLE001
            pass
