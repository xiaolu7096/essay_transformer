"""pytest 公共夹具：临时 SQLite + TestClient，全程零网络。

必须在导入 web.app.* 之前设置环境变量，使模块级引擎指向临时库。
Spec 2 起增加：Fernet 测试主密钥、fake/real 两套 settings、
WEB_RUNS_DIR 自动指向 tmp_path、可按用例重建 Settings 与 TestClient。
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

# --- 测试环境变量（先于 web.app 的任何导入） ---
_TMP_DIR = Path(tempfile.mkdtemp(prefix="et_web_test_"))
os.environ["WEB_JWT_SECRET"] = "test-jwt-secret-not-for-production-0123456789"
os.environ["WEB_DATABASE_URL"] = f"sqlite:///{(_TMP_DIR / 'test.db').as_posix()}"
os.environ["WEB_TOKEN_EXPIRE_MINUTES"] = "720"
os.environ["WEB_CORS_ORIGINS"] = "http://localhost:5173"
# Spec 2：固定测试 Fernet 主密钥（仅测试用，非生产密钥）
os.environ.setdefault("WEB_MASTER_KEY", "1VE0q-AyhTj9hLtTGLAcOLvfOOdfaEA_N3JLIGu-nNI=")
# 默认不显式设置 WEB_FAKE_MODE：缺省即真实模式语义（fake 用夹具显式打开）

from fastapi.testclient import TestClient  # noqa: E402

_SETTINGS_ENV = {
    "fake_mode": ("WEB_FAKE_MODE", lambda v: "1" if v else "0"),
    "max_concurrent_tasks": ("WEB_MAX_CONCURRENT_TASKS", str),
    "upload_max_mb": ("WEB_UPLOAD_MAX_MB", str),
    "runs_dir": ("WEB_RUNS_DIR", str),
}


@pytest.fixture(autouse=True)
def _isolate_runs_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """测试产物绝不写仓库 runs/：每个用例独立 tmp 目录。"""
    monkeypatch.setenv("WEB_RUNS_DIR", str(tmp_path / "runs"))


@pytest.fixture()
def make_settings(monkeypatch: pytest.MonkeyPatch):
    """按覆盖项重建 Settings 的工厂（覆盖项写入环境变量，worker 线程同口径）。"""
    from web.app.config import Settings

    def _make(**overrides) -> Settings:
        for key, value in overrides.items():
            if key not in _SETTINGS_ENV:
                raise KeyError(f"未知配置项：{key}")
            env_name, conv = _SETTINGS_ENV[key]
            monkeypatch.setenv(env_name, conv(value))
        return Settings()

    return _make


@pytest.fixture()
def fake_settings(make_settings):
    """假模式：免密钥、零外部调用。"""
    return make_settings(fake_mode=True)


@pytest.fixture()
def real_settings(make_settings):
    """真实模式：需凭据（主密钥已由测试环境提供）。"""
    return make_settings(fake_mode=False)


@pytest.fixture()
def make_client(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest):
    """按配置覆盖构造 TestClient 的工厂；进入上下文触发生命周期并清表。

    用法：client = make_client(fake_mode=True)
    （用例结束自动退出上下文，关闭执行器线程池）
    """
    from web.app.db import Base, engine
    from web.app.main import create_app

    def _make(**overrides) -> TestClient:
        for key, value in overrides.items():
            if key not in _SETTINGS_ENV:
                raise KeyError(f"未知配置项：{key}")
            env_name, conv = _SETTINGS_ENV[key]
            monkeypatch.setenv(env_name, conv(value))
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
        app = create_app()
        client = TestClient(app)
        client.__enter__()
        request.addfinalizer(lambda: client.__exit__(None, None, None))
        return client

    return _make


@pytest.fixture()
def client() -> Iterator[TestClient]:
    """默认夹具（真实模式语义，测试主密钥已就位）：每用例干净表。"""
    from web.app.db import Base, engine
    from web.app.main import create_app

    app = create_app()
    with TestClient(app) as c:  # 进入上下文触发 startup 建表
        # 每个用例使用干净的表（同一临时库文件，保证用例间隔离）
        Base.metadata.drop_all(bind=engine)
        Base.metadata.create_all(bind=engine)
        yield c
