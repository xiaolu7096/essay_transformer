"""健康检查：无认证、不依赖数据库与任何外部服务（断网可用）。"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
