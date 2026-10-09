"""SQLAlchemy 同步引擎与会话（本特性低并发本地负载，同步栈最简最稳）。"""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import REPO_ROOT, get_settings


class Base(DeclarativeBase):
    """所有 ORM 模型的声明式基类。"""


def _build_engine():
    settings = get_settings()
    url = settings.database_url
    if url.startswith("sqlite"):
        # 确保 SQLite 父目录存在（默认 data/）
        db_path = url.removeprefix("sqlite:///")
        if db_path and ":memory:" not in db_path:
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(
            url,
            connect_args={"check_same_thread": False},
            future=True,
        )

        @event.listens_for(engine, "connect")
        def _enable_fk(dbapi_connection, _record):  # noqa: ANN001
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        return engine
    return create_engine(url, future=True, pool_pre_ping=True)


# 启动时确保默认 data/ 目录存在（自定义 url 的情况由上面逻辑处理）
(REPO_ROOT / "data").mkdir(parents=True, exist_ok=True)

engine = _build_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db() -> Generator[Session, None, None]:
    """FastAPI 依赖：请求级 Session，异常回滚、结束关闭。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
