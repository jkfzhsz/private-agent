"""M3 数据分析前端预览卡片 - 文件服务 HTTP 端点(蓝图 §7.12,plan AC-8)。

提供 GET /files/outputs/{filename} 端点,返回 {workspace_root}/outputs/ 下的图片文件,
供前端 PreviewCard 通过 <img src="/files/outputs/xxx.png"> 渲染。

安全约束:
- 文件名仅允许字母/数字/下划线/连字符/点,禁止路径分隔符与 ..
- 路径穿越一律返回 404(不泄漏目录结构)
"""
from __future__ import annotations

import os
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from private_agent.config import loader

router = APIRouter(prefix="/files", tags=["files"])

# 安全文件名:仅允许字母/数字/下划线/连字符/点,禁止 / \ 与 ..
_SAFE_FILENAME_RE = re.compile(r"^[A-Za-z0-9_\-\.]+$")

# 扩展名 → MIME 类型(仅图片,符合 PreviewCard 用途)
_EXT_CONTENT_TYPE = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
    # V2: 首页视频背景(<video> 循环播放需要正确 Content-Type)
    ".mp4": "video/mp4",
    ".webm": "video/webm",
}


def _get_outputs_dir() -> Path:
    """获取 outputs 目录路径(从 config 读取 workspace_root)。

    Returns:
        Path(workspace_root) / "outputs"

    Notes:
        - workspace_root 形如 "${WORKSPACE}",需展开环境变量占位符。
        - 占位符缺失或环境变量未设置时回退为当前目录。
    """
    try:
        cfg = loader.load_config()
        workspace = cfg.get("system", {}).get("workspace_root", ".")
    except Exception:
        workspace = "."
    # 展开 ${VAR} 形式占位符
    workspace = os.path.expandvars(workspace)
    if workspace.startswith("${") and workspace.endswith("}"):
        # 环境变量未设置,expandvars 原样保留 → 回退到当前目录
        workspace = "."
    return Path(workspace) / "outputs"


def _search_sandbox_outputs(filename: str) -> Path | None:
    """2026-08-16: 回退搜索沙箱产物目录(按文件名, 取最新 mtime)。

    搜索范围(存在即扫):
    - {PA_USER_DATA}/.sandbox/{session_id}/outputs/  (沙箱原生产物)
    - {PA_USER_DATA}/.sandbox-artifacts/{session_id}/ (阶段1-C 同步产物)
    - {workspace_root}/.sandbox/... / .sandbox-artifacts/... (workspace 变体)

    Returns:
        命中文件 Path(最新 mtime), 未命中 None。
    """
    roots: list[Path] = []
    # 从 config workspace_root 推导候选根
    try:
        cfg = loader.load_config()
        ws = cfg.get("system", {}).get("workspace_root", ".")
        ws = os.path.expandvars(ws)
        if ws.startswith("${") and ws.endswith("}"):
            ws = "."
        ws_path = Path(ws)
        roots.append(ws_path / ".sandbox")
        roots.append(ws_path / ".sandbox-artifacts")
    except Exception:  # noqa: BLE001
        pass
    # 额外: 沙箱配置的 workspace_root(可能与 system 不同)
    try:
        cfg = loader.load_config()
        sand_ws = cfg.get("sandbox", {}).get("workspace_root", "")
        if sand_ws:
            sand_ws = os.path.expandvars(sand_ws)
            if not (sand_ws.startswith("${") and sand_ws.endswith("}")):
                roots.append(Path(sand_ws) / ".sandbox")
                roots.append(Path(sand_ws) / ".sandbox-artifacts")
    except Exception:  # noqa: BLE001
        pass

    best: Path | None = None
    best_mtime = 0.0
    for root in roots:
        if not root.is_dir():
            continue
        # .sandbox/{session_id}/outputs/ 结构 + .sandbox-artifacts/{session_id}/ 结构
        try:
            for session_dir in root.iterdir():
                if not session_dir.is_dir():
                    continue
                candidates = [session_dir]
                # .sandbox 结构含 outputs 子目录
                if (session_dir / "outputs").is_dir():
                    candidates.append(session_dir / "outputs")
                for cand in candidates:
                    fp = (cand / filename).resolve()
                    if not fp.is_file():
                        continue
                    try:
                        mtime = fp.stat().st_mtime
                    except OSError:
                        mtime = 0.0
                    if mtime > best_mtime:
                        best = fp
                        best_mtime = mtime
        except OSError:
            continue
    return best


@router.get("/outputs/{filename}", response_model=None)
async def get_output_file(filename: str):
    """返回 outputs 目录下的图片文件(AC-8)。

    Args:
        filename: 文件名(仅字母/数字/下划线/连字符/点)。

    Returns:
        200: FileResponse(image content-type)
        404: 文件不存在或文件名非法
    """
    # 文件名合法性校验
    if not filename or not _SAFE_FILENAME_RE.match(filename):
        raise HTTPException(status_code=404, detail="file_not_found")
    if filename in {".", ".."}:
        raise HTTPException(status_code=404, detail="file_not_found")

    outputs_dir = _get_outputs_dir()
    file_path = (outputs_dir / filename).resolve()

    # 二次校验:resolved 路径必须仍在 outputs_dir 内
    try:
        outputs_dir_resolved = outputs_dir.resolve()
    except OSError:
        raise HTTPException(status_code=404, detail="file_not_found")
    if not str(file_path).startswith(str(outputs_dir_resolved) + os.sep):
        raise HTTPException(status_code=404, detail="file_not_found")

    if not file_path.exists() or not file_path.is_file():
        # 2026-08-16(蒋先生反馈: 回答中 .png 损坏): 标准 outputs/ 未命中时
        # 回退扫描沙箱产物目录 —— 沙箱 outputs/ 落
        # {PA_USER_DATA}/.sandbox/{session_id}/outputs/, 阶段1-C 同步到
        # {workspace}/.sandbox-artifacts/{session_id}/, 均不在前端
        # files/outputs/ 服务目录下 → img 404 显示破损图标。
        # 按文件名匹配(取最新 mtime), 兼容多会话同名产物。
        fallback = _search_sandbox_outputs(filename)
        if fallback is None:
            raise HTTPException(status_code=404, detail="file_not_found")
        file_path = fallback
        ext = file_path.suffix.lower()
        content_type = _EXT_CONTENT_TYPE.get(ext, "application/octet-stream")
        return FileResponse(
            path=str(file_path),
            media_type=content_type,
            headers={"Cache-Control": "no-store"},
        )

    ext = file_path.suffix.lower()
    content_type = _EXT_CONTENT_TYPE.get(ext, "application/octet-stream")
    # 产物/壁纸为运行时动态文件,文件名固定(如 wallpaper.jpeg),必须禁止浏览器缓存,
    # 否则换图后 URL 不变,浏览器复用旧缓存导致永远显示第一张。
    return FileResponse(
        path=str(file_path),
        media_type=content_type,
        headers={"Cache-Control": "no-store"},
    )
