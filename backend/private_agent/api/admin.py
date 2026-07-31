"""M1 Phase 1 step 5 - GET /admin/disk-status HTTP 端点。

Source: plan/m1-react-loop step 5 (蓝图 §2.10 第 6 条 + §9.4 AC-4)

GET /admin/disk-status 返回 {"level", "message", "size_bytes"}。
从 db.get_pool() 获取 conn,调用 get_disk_status。
异常时返回 503 + {"error": "disk_status_unavailable"}。
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from private_agent.storage import db
from private_agent.storage.disk_alert import get_disk_status

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/disk-status", response_model=None)
async def disk_status():
    """返回磁盘占用分级状态(蓝图 §2.10 第 6 条)。

    Returns:
        200: {"level": "none|yellow|orange|red", "message": "...", "size_bytes": N}
        503: {"error": "disk_status_unavailable"}
    """
    try:
        pool = await db.get_pool()
        async with pool.acquire() as conn:
            return await get_disk_status(conn)
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"error": "disk_status_unavailable"},
        )
