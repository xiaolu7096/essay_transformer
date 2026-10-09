"""认证路由：register（T020）；login/me/logout 在 US3/US4 追加。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_current_user
from ..models import User
from ..schemas import LoginRequest, TokenResponse, UserCreate, UserOut
from ..security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/register", response_model=UserOut, status_code=201)
def register(payload: UserCreate, db: Session = Depends(get_db)) -> User:
    username_key = payload.username.lower()
    if db.query(User).filter(User.username_key == username_key).first():
        raise HTTPException(status_code=409, detail="用户名已被占用")

    user = User(
        username=payload.username,
        username_key=username_key,
        password_hash=hash_password(payload.password),
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        # 并发竞争兜底：两个请求都通过了上面的查重，唯一索引让恰一个失败
        db.rollback()
        raise HTTPException(status_code=409, detail="用户名已被占用")
    db.refresh(user)
    return user


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)) -> dict:
    user = (
        db.query(User)
        .filter(User.username_key == payload.username.lower())
        .first()
    )
    # 用户不存在与密码错误返回完全相同的 401，不泄露账号是否存在
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="用户名或密码无效")

    token, expires_at = create_access_token(user.id, user.token_version)
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_at": expires_at,
    }


@router.get("/me", response_model=UserOut)
def me(current_user: User = Depends(get_current_user)) -> User:
    return current_user


@router.post("/logout")
def logout(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    # 服务端令旧令牌立即失效：版本原子 +1，旧 JWT 的 ver 不再匹配
    current_user.token_version = User.token_version + 1
    db.commit()
    return {"status": "logged_out"}
