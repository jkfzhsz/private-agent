"""Test sandbox/workspace.py - WorkspaceManager."""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from private_agent.sandbox.workspace import WorkspaceManager


def test_get_or_create_creates_directories(tmp_path: Path) -> None:
    """AC-3: get_or_create creates scripts/outputs/artifacts dirs."""
    mgr = WorkspaceManager(tmp_path)
    session_dir = mgr.get_or_create("test-session-1")
    assert (session_dir / "scripts").is_dir()
    assert (session_dir / "outputs").is_dir()
    assert (session_dir / "artifacts").is_dir()

def test_get_or_create_idempotent(tmp_path: Path) -> None:
    """AC-3: same session_id returns same path."""
    mgr = WorkspaceManager(tmp_path)
    p1 = mgr.get_or_create("test-session-2")
    p2 = mgr.get_or_create("test-session-2")
    assert p1 == p2

def test_cleanup_expired_moves_old_dirs(tmp_path: Path) -> None:
    """AC-4: cleanup_expired moves old dirs to _archive/."""
    mgr = WorkspaceManager(tmp_path)
    old_session = mgr.get_or_create("old-session")
    old_time = time.time() - 8 * 86400
    os.utime(old_session, (old_time, old_time))
    count = mgr.cleanup_expired(retention_days=7)
    assert count == 1
    archive_dir = tmp_path / ".sandbox" / "_archive" / "old-session"
    assert archive_dir.is_dir()
    assert not old_session.is_dir()

def test_cleanup_expired_skips_recent_dirs(tmp_path: Path) -> None:
    """AC-4: recent dirs not moved."""
    mgr = WorkspaceManager(tmp_path)
    mgr.get_or_create("recent-session")
    count = mgr.cleanup_expired(retention_days=7)
    assert count == 0

def test_check_disk_usage_under_limit(tmp_path: Path) -> None:
    """Disk check: under limit returns True."""
    assert WorkspaceManager.check_disk_usage(tmp_path, limit_mb=100)

def test_check_disk_usage_exceeded(tmp_path: Path) -> None:
    """Disk check: exceeded returns False."""
    (tmp_path / "big_file.bin").write_bytes(b"x" * 1024 * 1024)
    assert not WorkspaceManager.check_disk_usage(tmp_path, limit_mb=0)