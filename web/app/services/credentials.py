"""凭据存取：加密落库、掩码、upsert/幂等删除/取明文（US1）。

安全口径：
- 加解密只走 web.app.crypto 唯一口径，主密钥只来自 settings.fernet_key；
- 本模块任何函数不得 print/logger 密钥明文；
- get_decrypted 是全代码库唯一的密钥解密取用处（worker 开跑时调用）。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..crypto import decrypt_secret, encrypt_secret, mask_secret
from ..models import Credential

# 固定顺序：前端据此渲染两个设置块
SERVICES: tuple[str, str] = ("mineru", "deepseek")
MAX_SECRET_LEN = 200


def _status(service: str, configured: bool, mask: str | None) -> dict:
    return {"service": service, "configured": configured, "mask": mask}


def list_statuses(db: Session, user_id: str) -> list[dict]:
    """返回固定两服务状态；未配置项左连接补空。"""
    rows = {
        row.service: row
        for row in db.scalars(
            select(Credential).where(Credential.user_id == user_id)
        )
    }
    return [
        _status(service, True, rows[service].mask)
        if service in rows
        else _status(service, False, None)
        for service in SERVICES
    ]


def upsert(db: Session, user_id: str, service: str, secret: str) -> dict:
    """保存（首次或覆盖）密钥；返回最新状态。"""
    plaintext = secret.strip()
    if not 1 <= len(plaintext) <= MAX_SECRET_LEN:
        raise ValueError("密钥不能为空，长度 1-200 个字符")

    key = get_settings().fernet_key
    token = encrypt_secret(plaintext, key)
    display_mask = mask_secret(plaintext)

    row = db.scalar(
        select(Credential).where(
            Credential.user_id == user_id, Credential.service == service
        )
    )
    if row is None:
        row = Credential(
            user_id=user_id,
            service=service,
            secret_encrypted=token,
            mask=display_mask,
        )
        db.add(row)
    else:
        row.secret_encrypted = token
        row.mask = display_mask
    db.commit()
    return _status(service, True, display_mask)


def delete(db: Session, user_id: str, service: str) -> dict:
    """删除整行；未配置也幂等成功。"""
    row = db.scalar(
        select(Credential).where(
            Credential.user_id == user_id, Credential.service == service
        )
    )
    if row is not None:
        db.delete(row)
        db.commit()
    return _status(service, False, None)


def get_decrypted(db: Session, user_id: str, service: str) -> str | None:
    """取解密后的密钥明文；未配置返回 None。仅供 worker 开跑时调用。"""
    row = db.scalar(
        select(Credential).where(
            Credential.user_id == user_id, Credential.service == service
        )
    )
    if row is None:
        return None
    return decrypt_secret(row.secret_encrypted, get_settings().fernet_key)
