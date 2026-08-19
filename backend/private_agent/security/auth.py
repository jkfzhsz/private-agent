"""阶段二批次 1: admin 控制面鉴权基础设施。

背景: 审查报告 A.3.10/B.2.1 —— admin/eval/files 43 个端点无鉴权 + CORS `*` 全开,
任意可访问 8765 的本机网页/进程可读改全部密钥(provider API key / MCP Bearer token)。

方案:
- 独立 token `PA_ADMIN_TOKEN`(与 PA_MASTER_KEY 职责分离: master 仅用于 AES 加密)
- token 优先级: 环境变量 > backend/.env(首次启动由 ensure_admin_token 生成持久化)
- FastAPI 依赖 `require_admin`: 校验 `X-Admin-Token` 头, `hmac.compare_digest`
  常量时间比较防时序攻击
- 生产路径(run_sidecar)启动时 ensure; 测试/uvicorn 直接跑 app 不自动写文件
"""
from __future__ import annotations

import hmac
import os
import secrets
from pathlib import Path

from fastapi import Header, HTTPException, status

_HEADER = "X-Admin-Token"

# backend/.env 定位: cwd(生产启动 cwd=backend) > 包相对路径探测
_PACKAGE_ROOT = Path(__file__).resolve().parents[2]  # .../backend/private_agent → backend


def _user_env_path() -> Path:
    """Electron 用户可写配置(打包版与 dev 统一持久化位置):
    Windows: %APPDATA%\\Private Agent\\backend.env; 其他平台: XDG 配置目录。

    2026-08-06: 打包版 backend/.env 只读(resourcesPath), token 生成后写
    这里才能持久化 —— 否则每次启动新 token, 前端已注入 token 失效 → 401。
    """
    if os.name == "nt":
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "Private Agent" / "backend.env"


def _read_token_from(path: Path | None, key: str) -> str:
    """从 .env 文件读取指定 KEY 的值(不存在/异常 → "")。"""
    if path is None or not path.is_file():
        return ""
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip()
    except OSError:
        return ""
    return ""


def _upsert_env_key(path: Path | None, key: str, value: str) -> bool:
    """在 .env 文件更新/新增 KEY=VALUE(保留注释与其他行)。成功返回 True。"""
    if path is None:
        return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        lines: list[str] = []
        seen = False
        if path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines(keepends=True):
                stripped = line.strip()
                if (
                    stripped
                    and not stripped.startswith("#")
                    and "=" in stripped
                    and stripped.split("=", 1)[0].strip() == key
                ):
                    lines.append(f"{key}={value}\n")
                    seen = True
                    continue
                lines.append(line)
        if not seen:
            lines.append(f"{key}={value}\n")
        path.write_text("".join(lines), encoding="utf-8")
        return True
    except OSError:
        return False


def _env_path() -> Path | None:
    """定位 backend/.env(可能不存在)。"""
    for candidate in (Path.cwd() / ".env", _PACKAGE_ROOT / ".env"):
        if candidate.is_file():
            return candidate
    return None


def _read_env_token() -> str:
    """从 backend/.env 读取 PA_ADMIN_TOKEN(env 未注入时的 dev 兜底)。"""
    return _read_token_from(_env_path(), "PA_ADMIN_TOKEN")


def get_admin_token() -> str:
    """当前生效的 admin token(环境变量 > 用户配置 backend.env > backend/.env)。"""
    token = os.environ.get("PA_ADMIN_TOKEN")
    if token:
        return token
    token = _read_token_from(_user_env_path(), "PA_ADMIN_TOKEN")
    if token:
        return token
    return _read_env_token()


def ensure_admin_token() -> str:
    """确保 PA_ADMIN_TOKEN 可用: env 优先; 缺失时生成 64 hex 并持久化。

    2026-08-06 打包版修复: 持久化位置 = Electron 用户配置
    %APPDATA%/Private Agent/backend.env(可写) —— 原实现只写
    backend/.env, 打包版 resourcesPath 只读 → 写失败 → 每次启动新
    token → 前端注入的旧 token 全部 401。

    仅生产启动(run_sidecar)调用 —— 测试/uvicorn 直接跑 app 不触发写文件。
    """
    token = get_admin_token()
    if token:
        os.environ["PA_ADMIN_TOKEN"] = token
        return token
    new_token = secrets.token_hex(32)  # 64 hex chars = 32 bytes
    # 用户配置优先(打包版可写, dev 同样生效)
    _upsert_env_key(_user_env_path(), "PA_ADMIN_TOKEN", new_token)
    # 兼容 dev: backend/.env 存在则同步追加
    if _env_path() is not None:
        _upsert_env_key(_env_path(), "PA_ADMIN_TOKEN", new_token)
    os.environ["PA_ADMIN_TOKEN"] = new_token
    return new_token


def require_admin(
    x_admin_token: str | None = Header(default=None, alias=_HEADER),
) -> str:
    """FastAPI 依赖: 校验 X-Admin-Token, 失败 401。

    - 未配置 token(测试/未初始化) → 401 "admin token not configured"
    - 常量时间比较(hmac.compare_digest)防时序攻击
    """
    expected = get_admin_token()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="admin token not configured",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not x_admin_token or not hmac.compare_digest(x_admin_token, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid admin token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return x_admin_token
