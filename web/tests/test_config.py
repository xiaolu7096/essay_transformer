"""T005：Settings 新增假模式/并发/产物目录/上传上限与 Fernet 主密钥策略。

全部通过环境变量构造（与生产口径一致）；不触网。
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from web.app.config import REPO_ROOT, Settings

_JWT = "test-jwt-secret-not-for-production-0123456789"


def _env(monkeypatch, **kw: str) -> None:
    monkeypatch.setenv("WEB_JWT_SECRET", _JWT)
    monkeypatch.delenv("WEB_MASTER_KEY", raising=False)
    monkeypatch.delenv("WEB_FAKE_MODE", raising=False)
    monkeypatch.delenv("WEB_MAX_CONCURRENT_TASKS", raising=False)
    monkeypatch.delenv("WEB_RUNS_DIR", raising=False)
    monkeypatch.delenv("WEB_UPLOAD_MAX_MB", raising=False)
    for key, value in kw.items():
        monkeypatch.setenv(key, value)


def test_defaults(monkeypatch):
    _env(monkeypatch, WEB_MASTER_KEY=Fernet.generate_key().decode())
    s = Settings()
    assert s.fake_mode is False
    assert s.max_concurrent_tasks == 2
    assert s.upload_max_mb == 50
    assert s.runs_dir == REPO_ROOT / "runs"


def test_env_overrides(monkeypatch):
    _env(
        monkeypatch,
        WEB_FAKE_MODE="1",
        WEB_MAX_CONCURRENT_TASKS="4",
        WEB_UPLOAD_MAX_MB="10",
        WEB_RUNS_DIR="x/./runs_here",
    )
    s = Settings()
    assert s.fake_mode is True
    assert s.max_concurrent_tasks == 4
    assert s.upload_max_mb == 10
    assert s.runs_dir == Path("x/runs_here")


def test_blank_runs_dir_falls_back_to_default(monkeypatch):
    _env(monkeypatch, WEB_RUNS_DIR="   ", WEB_MASTER_KEY=Fernet.generate_key().decode())
    assert Settings().runs_dir == REPO_ROOT / "runs"


@pytest.mark.parametrize("bad", ["0", "-1"])
def test_concurrent_must_be_positive(monkeypatch, bad):
    _env(monkeypatch, WEB_MAX_CONCURRENT_TASKS=bad)
    with pytest.raises(ValidationError):
        Settings()


@pytest.mark.parametrize("bad", ["0", "-5"])
def test_upload_mb_must_be_positive(monkeypatch, bad):
    _env(monkeypatch, WEB_UPLOAD_MAX_MB=bad)
    with pytest.raises(ValidationError):
        Settings()


def test_real_mode_requires_master_key(monkeypatch):
    _env(monkeypatch)  # fake 关、master key 空
    with pytest.raises(RuntimeError, match="WEB_MASTER_KEY"):
        Settings()


def test_fake_mode_allows_blank_master_key_with_temp_key(monkeypatch, caplog):
    _env(monkeypatch, WEB_FAKE_MODE="1")
    with caplog.at_level(logging.WARNING, logger="web"):
        s = Settings()
    key = s.fernet_key
    assert isinstance(key, bytes)
    # 合法 Fernet 密钥（urlsafe base64 32 字节）
    Fernet(key)
    # 进程内缓存：重复取用同一把临时密钥
    assert s.fernet_key is key
    assert any("临时" in rec.message or "WEB_MASTER_KEY" in rec.message for rec in caplog.records)


def test_valid_master_key_returned(monkeypatch):
    raw = Fernet.generate_key()
    _env(monkeypatch, WEB_MASTER_KEY=raw.decode())
    s = Settings()
    assert s.fernet_key == raw


def test_invalid_master_key_fails_fast(monkeypatch):
    _env(monkeypatch, WEB_MASTER_KEY="not-a-valid-fernet-key")
    s = Settings()
    with pytest.raises(RuntimeError, match="Fernet|WEB_MASTER_KEY"):
        s.fernet_key
