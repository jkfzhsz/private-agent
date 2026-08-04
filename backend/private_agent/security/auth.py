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


def _env_path() -> Path | None:
    """定位 backend/.env(可能不存在)。"""
    for candidate in (Path.cwd() / ".env", _PACKAGE_ROOT / ".env"):
        if candidate.is_file():
            return candidate
    return None


def _read_env_token() -> str:
    """从 backend/.env 读取 PA_ADMIN_TOKEN(env 未注入时的兜底)。"""
    p = _env_path()
    if p is None:
        return ""
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("PA_ADMIN_TOKEN="):
                return line.split("=", 1)[1].strip()
    except OSError:
        return ""
    return ""


def get_admin_token() -> str:
    """当前生效的 admin token(环境变量 > backend/.env)。"""
    return os.environ.get("PA_ADMIN_TOKEN") or _read_env_token()


def ensure_admin_token() -> str:
    """确保 PA_ADMIN_TOKEN 可用: env 优先; 缺失时生成 64 hex 并持久化到 backend/.env。

    仅生产启动(run_sidecar)调用 —— 测试/uvicorn 直接跑 app 不触发写文件。
    """
    token = os.environ.get("PA_ADMIN_TOKEN")
    if token:
        return token
    token = _read_env_token()
    if token:
        os.environ["PA_ADMIN_TOKEN"] = token
        return token
    new_token = secrets.token_hex(32)  # 64 hex chars = 32 bytes
    p = _env_path()
    if p is not None:
        try:
            lines = p.read_text(encoding="utf-8").splitlines()
            if not any(l.startswith("PA_ADMIN_TOKEN=") for l in lines):
                lines.append(f"PA_ADMIN_TOKEN={new_token}")
                p.write_text("\n".join(lines) + "\n", encoding="utf-8")
            os.environ["PA_ADMIN_TOKEN"] = new_token
        except OSError:
            # 写入失败(.env 不可写)时仅内存生效, 本次运行仍受保护
            os.environ["PA_ADMIN_TOKEN"] = new_token
    else:
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
