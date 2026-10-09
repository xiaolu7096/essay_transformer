"""密码哈希与 JWT —— 全代码库唯一的算法口径（宪法原则四）。

- 密码：passlib pbkdf2_sha256（纯 Python，无 bcrypt 原生依赖）
- 令牌：HS256 JWT，claim = sub(用户UUID) / ver(token_version) / iat / exp
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from passlib.context import CryptContext

from .config import get_settings

# 全项目唯一一份 CryptContext；禁止在别处另建哈希口径
pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

ALGORITHM = "HS256"


def hash_password(plain: str) -> str:
    return pwd_context.hash(plain)


def verify_password(plain: str, password_hash: str) -> bool:
    return pwd_context.verify(plain, password_hash)


def create_access_token(user_id: str, token_version: int) -> tuple[str, datetime]:
    """签发令牌，返回 (token, expires_at_utc_naive)。"""
    settings = get_settings()
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(minutes=settings.token_expire_minutes)
    payload: dict[str, Any] = {
        "sub": user_id,
        "ver": token_version,
        "iat": int(now.timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    token = jwt.encode(payload, settings.jwt_secret, algorithm=ALGORITHM)
    return token, expires_at.replace(tzinfo=None)


def decode_access_token(token: str) -> dict[str, Any]:
    """校验签名与有效期并返回 payload；任何问题（过期/伪造/损坏）均抛 jwt 异常。"""
    settings = get_settings()
    return jwt.decode(token, settings.jwt_secret, algorithms=[ALGORITHM])
