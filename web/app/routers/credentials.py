"""第三方密钥管理路由（US1）：查看状态 / 保存覆盖 / 删除。

全部经唯一鉴权依赖 get_current_user；按当前用户操作，不接收他人标识。
任何响应只回掩码与 configured，不含明文/密文。
"""

from __future__ import annotations

import logging
from typing import Literal

from cryptography.fernet import InvalidToken
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_current_user
from ..models import User
from ..schemas import (
    CredentialListOut,
    CredentialStatusOut,
    CredentialUpsertRequest,
    ServiceName,
)
from ..services import credentials as svc

logger = logging.getLogger("web")

router = APIRouter(prefix="/api/credentials", tags=["credentials"])

_UNAVAILABLE = HTTPException(status_code=500, detail="服务暂时不可用，请联系维护者")


@router.get("", response_model=CredentialListOut)
def list_credentials(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CredentialListOut:
    return CredentialListOut(credentials=svc.list_statuses(db, current_user.id))


@router.put("/{service}", response_model=CredentialStatusOut)
def upsert_credential(
    service: ServiceName,
    payload: CredentialUpsertRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CredentialStatusOut:
    # service 非法值由 Literal 路径参数自动 422（标准校验错误体）
    try:
        status = svc.upsert(db, current_user.id, service, payload.secret)
    except (RuntimeError, InvalidToken) as exc:
        # 主密钥配置错误等：只回统一文案，内部原因仅进服务端日志（不含明文）
        logger.error("凭据保存失败（配置问题）：%s", type(exc).__name__)
        raise _UNAVAILABLE
    return CredentialStatusOut(**status)


@router.delete("/{service}", response_model=CredentialStatusOut)
def delete_credential(
    service: Literal["mineru", "deepseek"],
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> CredentialStatusOut:
    status = svc.delete(db, current_user.id, service)
    return CredentialStatusOut(**status)
