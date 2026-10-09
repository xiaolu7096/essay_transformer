"""鉴权依赖：全项目唯一的受保护入口（FR-009）。

任何非法情况（无令牌/格式错误/伪造签名/过期/ver 不符/用户不存在）
统一返回 401 `未授权或登录已过期`，不向调用方区分原因。
"""

from __future__ import annotations

import jwt
from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from .db import get_db
from .models import Task, User
from .security import decode_access_token

UNAUTHORIZED = HTTPException(
    status_code=401,
    detail="未授权或登录已过期",
    headers={"WWW-Authenticate": "Bearer"},
)


def get_current_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise UNAUTHORIZED

    token = authorization[len("Bearer ") :].strip()
    if not token:
        raise UNAUTHORIZED

    try:
        payload = decode_access_token(token)
    except jwt.PyJWTError:
        raise UNAUTHORIZED

    user_id = payload.get("sub")
    token_version = payload.get("ver")
    if not isinstance(user_id, str) or not isinstance(token_version, int):
        raise UNAUTHORIZED

    user = db.get(User, user_id)
    if user is None or user.token_version != token_version:
        raise UNAUTHORIZED

    return user


def get_executor(request: Request):
    """从 app.state 取执行器（lifespan 构造；测试可替换为替身）。"""
    return request.app.state.executor


def get_hub(request: Request):
    """从 app.state 取实时事件中枢（lifespan 绑定主循环的单例）。"""
    return request.app.state.hub


def get_owned_task(
    task_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Task:
    """按 id + 属主取任务；不存在与越权统一 404（不泄露 id 是否存在）。"""
    task = db.get(Task, task_id)
    if task is None or task.user_id != user.id:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


class TaskAccessError(Exception):
    """扁平错误体 ``{"detail": ..., "code": ...}``（main 注册处理器）。"""

    def __init__(self, status_code: int, detail: str, code: str) -> None:
        self.status_code = status_code
        self.detail = detail
        self.code = code
        super().__init__(detail)


def get_owned_succeeded_task(
    task: Task = Depends(get_owned_task),
) -> Task:
    """归属校验同 get_owned_task；非 succeeded 一律 409 task_not_ready。"""
    if task.status != "succeeded":
        raise TaskAccessError(
            status_code=409,
            detail="任务尚未完成，暂无可阅读的成品",
            code="task_not_ready",
        )
    return task
