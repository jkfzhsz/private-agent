"""Test sandbox/security.py - CodeScanner, EnvSanitizer, PathFilter."""
from __future__ import annotations

from private_agent.sandbox.security import CodeScanner, EnvSanitizer, PathFilter


class TestCodeScanner:
    def test_scan_detects_os_system(self) -> None:
        """AC-5: scan detects os.system."""
        scanner = CodeScanner()
        code = "import os; os.system('ls')"
        warnings = scanner.scan(code)
        assert len(warnings) >= 1
        assert "os.system" in warnings[0].snippet

    def test_scan_detects_subprocess(self) -> None:
        """AC-5: scan detects subprocess.run."""
        scanner = CodeScanner()
        code = "import subprocess; subprocess.run(['ls'])"
        warnings = scanner.scan(code)
        assert len(warnings) >= 1

    def test_scan_detects_shutil_rmtree(self) -> None:
        """AC-5: scan detects shutil.rmtree."""
        scanner = CodeScanner()
        code = "import shutil; shutil.rmtree('/tmp')"
        warnings = scanner.scan(code)
        assert len(warnings) >= 1

    def test_scan_clean_code_no_warnings(self) -> None:
        """AC-5: clean code no warnings."""
        scanner = CodeScanner()
        code = "print('hello world')"
        warnings = scanner.scan(code)
        assert warnings == []


class TestEnvSanitizer:
    def test_sanitize_filters_api_key(self) -> None:
        """AC-6: filter API_KEY."""
        sanitizer = EnvSanitizer()
        env = {"API_KEY": "secret123", "PATH": "/usr/bin", "HOME": "/home/user"}
        result = sanitizer.sanitize(env)
        assert "API_KEY" not in result
        assert result["PATH"] == "/usr/bin"

    def test_sanitize_filters_token(self) -> None:
        """AC-6: filter TOKEN."""
        sanitizer = EnvSanitizer()
        env = {"GITHUB_TOKEN": "ghp_xxx", "USER": "test"}
        result = sanitizer.sanitize(env)
        assert "GITHUB_TOKEN" not in result

    def test_sanitize_retains_basic_vars(self) -> None:
        """AC-6: retain PATH/HOME/USER/LANG."""
        sanitizer = EnvSanitizer()
        env = {"PATH": "/usr/bin", "HOME": "/home", "USER": "tester", "LANG": "en_US"}
        result = sanitizer.sanitize(env)
        assert result["PATH"] == "/usr/bin"
        assert result["HOME"] == "/home"
        assert result["USER"] == "tester"
        assert result["LANG"] == "en_US"


class TestPathFilter:
    def test_readonly_path_allowed(self, tmp_path) -> None:
        """AC-7: readonly path read returns True."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        test_file = data_dir / "test.txt"
        test_file.write_text("hello")
        pf = PathFilter(readonly=[str(data_dir)], writable=[])
        assert pf.validate_file_access(str(test_file), write=False)

    def test_outside_path_denied(self, tmp_path) -> None:
        """AC-7: outside path returns False."""
        pf = PathFilter(readonly=[], writable=[])
        assert not pf.validate_file_access(str(tmp_path / "secret.txt"), write=False)

    def test_writable_path_write_allowed(self, tmp_path) -> None:
        """AC-7: writable path write returns True."""
        out_dir = tmp_path / "output"
        out_dir.mkdir()
        pf = PathFilter(readonly=[], writable=[str(out_dir)])
        assert pf.validate_file_access(str(out_dir / "result.csv"), write=True)

    def test_readonly_path_write_denied(self, tmp_path) -> None:
        """AC-7: readonly path write returns False."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        pf = PathFilter(readonly=[str(data_dir)], writable=[])
        assert not pf.validate_file_access(str(data_dir / "out.txt"), write=True)