from __future__ import annotations

import os
import time
from pathlib import Path


class WorkspaceManager:
    """沙箱工作目录管理器(蓝图 §6.4 / spec m2-sandbox AC-3, AC-4)。

    按 session_id 隔离目录,7 天保留,与 file_read/file_write 白名单互通。
    """

    def __init__(self, workspace_root: str | Path) -> None:
        self._root = Path(workspace_root).resolve()

    def get_or_create(self, session_id: str) -> Path:
        """创建或返回会话工作目录(AC-3)。

        目录结构: {workspace_root}/.sandbox/{session_id}/{scripts,outputs,artifacts}
        同 session_id 二次调用返回同一路径不重建。
        """
        session_dir = self._root / ".sandbox" / session_id
        for subdir in ("scripts", "outputs", "artifacts"):
            (session_dir / subdir).mkdir(parents=True, exist_ok=True)
        return session_dir

    def cleanup_expired(self, retention_days: int = 7) -> int:
        """将 retention_days 天前的会话目录移动到 _archive/ (AC-4)。

        Returns:
            已归档的会话数量。
        """
        sandbox_root = self._root / ".sandbox"
        archive_dir = sandbox_root / "_archive"
        archive_dir.mkdir(parents=True, exist_ok=True)

        cutoff = time.time() - retention_days * 86400
        count = 0
        for entry in sandbox_root.iterdir():
            if not entry.is_dir() or entry.name.startswith("_"):
                continue
            try:
                mtime = entry.stat().st_mtime
            except OSError:
                continue
            if mtime < cutoff:
                dest = archive_dir / entry.name
                entry.rename(dest)
                count += 1
        return count

    @staticmethod
    def check_disk_usage(workspace: str | Path, limit_mb: int) -> bool:
        """检查工作目录磁盘使用量是否在限制内(蓝图 §6.7)。

        Returns:
            True 表示未超限,False 表示磁盘已超限。
        """
        total = 0
        for dirpath, _, filenames in os.walk(str(workspace)):
            for fn in filenames:
                try:
                    total += os.path.getsize(os.path.join(dirpath, fn))
                except OSError:
                    continue
        return total < limit_mb * 1024 * 1024
