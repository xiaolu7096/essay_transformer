"""ORM 模型（Spec 1 仅 users 一张表；字段逐字按 data-model §1）。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _new_uuid() -> str:
    return str(uuid.uuid4())


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    username: Mapped[str] = mapped_column(String(30), nullable=False)
    # 小写化唯一键：登录查找与判重；Alice 与 alice 视为同名
    username_key: Mapped[str] = mapped_column(
        String(30), nullable=False, unique=True, index=True
    )
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    token_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=_utcnow)


class Credential(Base):
    """每用户每服务一份的加密第三方密钥（Spec 2 US1）。"""

    __tablename__ = "credentials"
    __table_args__ = (
        UniqueConstraint("user_id", "service", name="uq_credentials_user_service"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    service: Mapped[str] = mapped_column(String(20), nullable=False)
    secret_encrypted: Mapped[str] = mapped_column(String(255), nullable=False)
    mask: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        nullable=False, default=_utcnow, onupdate=_utcnow
    )


class Task(Base):
    """转写任务（Spec 2 US2）。字段严格对应 data-model §1.2。"""

    __tablename__ = "tasks"
    __table_args__ = (
        # 列表主查询：按属主、创建时间倒序
        Index("ix_tasks_user_created", "user_id", text("created_at DESC")),
        # 部分唯一索引：每用户至多一个 queued/running 活动任务（active_slot=1）
        Index(
            "uq_tasks_one_active_per_user",
            "user_id",
            "active_slot",
            unique=True,
            sqlite_where=text("status IN ('queued', 'running')"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    source_kind: Mapped[str] = mapped_column(String(10), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, default="queued")
    stage: Mapped[str | None] = mapped_column(String(12), nullable=True)
    progress: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    message: Mapped[str | None] = mapped_column(String(300), nullable=True)
    failure_category: Mapped[str | None] = mapped_column(String(10), nullable=True)
    failure_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    failure_message: Mapped[str | None] = mapped_column(String(300), nullable=True)
    result_title: Mapped[str | None] = mapped_column(String(300), nullable=True)
    result_section_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result_total_chars: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fake_mode: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # 活动任务恒为 1，终态置 NULL（部分唯一索引槽位列）
    active_slot: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False, default=_utcnow)
    started_at: Mapped[datetime | None] = mapped_column(nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(nullable=True)
