"""T018：任务提交（链接/上传）与后台执行端到端，全程离线。"""

from __future__ import annotations

import threading
import time
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from web.app.config import get_settings
from web.app.db import SessionLocal
from web.app.models import Task, User
from web.app.runner import pipeline_run

STAGES = ["ingest", "parse", "normalize", "plan", "rewrite", "assemble"]
ARXIV_URL = "https://arxiv.org/abs/1706.03762"
ARXIV_ID = "1706.03762"


class _DummyExecutor:
    """不入池、不执行：用于纯提交路径测试。"""

    def __init__(self) -> None:
        self.submitted: list[tuple[str, str]] = []

    async def submit(self, task_id: str, user_id: str) -> None:
        self.submitted.append((task_id, user_id))

    def cancel(self, task_id: str) -> None:
        pass


def _register(client, username: str | None = None) -> tuple[str, str]:
    username = username or f"user_{uuid.uuid4().hex[:10]}"
    password = "password123"
    r = client.post("/api/auth/register", json={"username": username, "password": password})
    assert r.status_code == 201, r.text
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return username, r.json()["access_token"]


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _user_id(username: str) -> str:
    db = SessionLocal()
    try:
        return db.query(User).filter(User.username_key == username.lower()).one().id
    finally:
        db.close()


def _task(task_id: str) -> Task:
    db = SessionLocal()
    try:
        return db.get(Task, task_id)
    finally:
        db.close()


def _wait_terminal(task_id: str, timeout: float = 20.0) -> Task:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = _task(task_id)
        if task and task.status in ("succeeded", "failed", "cancelled"):
            return task
        time.sleep(0.02)
    raise AssertionError(f"任务 {task_id} 未在 {timeout}s 内终态化")


# ① 合法链接 → 201 queued（dummy 执行器，不跑 worker）
def test_from_url_creates_queued(make_client):
    client = make_client(fake_mode=True)
    client.app.state.executor = _DummyExecutor()
    _u, token = _register(client)
    r = client.post("/api/tasks/from-url", json={"source": ARXIV_URL}, headers=_h(token))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "queued"
    assert body["source_kind"] == "arxiv"
    assert body["source_ref"] == ARXIV_ID
    assert body["fake_mode"] is True
    assert body["failure"] is None and body["result"] is None


# ② 非法链接/空串/超长 → 422，无任务行残留
def test_invalid_source_422(make_client):
    client = make_client(fake_mode=True)
    client.app.state.executor = _DummyExecutor()
    _u, token = _register(client)
    r = client.post("/api/tasks/from-url",
                    json={"source": "https://example.com/x"}, headers=_h(token))
    assert r.status_code == 422
    db = SessionLocal()
    try:
        assert db.query(Task).count() == 0
    finally:
        db.close()
    assert client.post("/api/tasks/from-url", json={"source": ""},
                       headers=_h(token)).status_code == 422
    assert client.post("/api/tasks/from-url", json={"source": "x" * 501},
                       headers=_h(token)).status_code == 422


# ③ 真实模式无凭据 → 400 credentials_required
def test_real_mode_requires_credentials(make_client):
    client = make_client(fake_mode=False)
    client.app.state.executor = _DummyExecutor()
    _u, token = _register(client)
    r = client.post("/api/tasks/from-url", json={"source": ARXIV_URL}, headers=_h(token))
    assert r.status_code == 400
    assert r.json()["code"] == "credentials_required"


# ④ 已有活动任务 → 409
def test_active_task_conflict(make_client):
    client = make_client(fake_mode=True)
    dummy = _DummyExecutor()
    client.app.state.executor = dummy
    username, token = _register(client)
    r1 = client.post("/api/tasks/from-url", json={"source": ARXIV_URL}, headers=_h(token))
    assert r1.status_code == 201
    r2 = client.post("/api/tasks/from-url", json={"source": ARXIV_URL}, headers=_h(token))
    assert r2.status_code == 409
    assert r2.json()["code"] == "active_task_exists"


# ⑤ 两线程并发提交：恰一个 201、一个 409（部分唯一索引兜底）
def test_concurrent_submit_single_winner(make_client):
    app_client = make_client(fake_mode=True)
    app = app_client.app
    app.state.executor = _DummyExecutor()
    username, token = _register(app_client)

    barrier = threading.Barrier(2)
    results: list[int] = []
    errors: list[Exception] = []

    def worker() -> None:
        local = TestClient(app)
        try:
            barrier.wait()
            r = local.post("/api/tasks/from-url", json={"source": ARXIV_URL},
                           headers=_h(token))
            results.append(r.status_code)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert not errors, errors
    assert sorted(results) == [201, 409]


# ⑥ 合法 PDF 上传 → 201，source.pdf 与上传一致
def test_upload_pdf_stored(make_client):
    client = make_client(fake_mode=True)
    client.app.state.executor = _DummyExecutor()
    username, token = _register(client)
    content = b"%PDF-1.4\nhello-upload-bytes"
    r = client.post(
        "/api/tasks/upload",
        files={"file": ("mypaper.pdf", content, "application/pdf")},
        headers=_h(token),
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["source_kind"] == "upload"
    assert body["source_ref"] == "mypaper.pdf"

    saved = get_settings().runs_dir / _user_id(username) / body["id"] / "source.pdf"
    assert saved.read_bytes() == content


# ⑦ .txt 扩展名 → 422 invalid_file_type
def test_upload_bad_extension(make_client):
    client = make_client(fake_mode=True)
    client.app.state.executor = _DummyExecutor()
    _u, token = _register(client)
    r = client.post(
        "/api/tasks/upload",
        files={"file": ("notes.txt", b"plain text", "text/plain")},
        headers=_h(token),
    )
    assert r.status_code == 422
    assert r.json()["code"] == "invalid_file_type"


# ⑧ 非 %PDF 头 → 422 invalid_pdf
def test_upload_bad_magic(make_client):
    client = make_client(fake_mode=True)
    client.app.state.executor = _DummyExecutor()
    _u, token = _register(client)
    r = client.post(
        "/api/tasks/upload",
        files={"file": ("fake.pdf", b"NOT A PDF CONTENT", "application/pdf")},
        headers=_h(token),
    )
    assert r.status_code == 422
    assert r.json()["code"] == "invalid_pdf"


# ⑨ 超限 → 413，无任务行、无半成品目录
def test_upload_too_large(make_client):
    client = make_client(upload_max_mb=1)
    client.app.state.executor = _DummyExecutor()
    username, token = _register(client)
    big = b"%PDF-1.4\n" + b"x" * (1024 * 1024 + 100)
    r = client.post(
        "/api/tasks/upload",
        files={"file": ("big.pdf", big, "application/pdf")},
        headers=_h(token),
    )
    assert r.status_code == 413
    assert r.json()["code"] == "file_too_large"
    db = SessionLocal()
    try:
        assert db.query(Task).count() == 0
    finally:
        db.close()
    assert not (get_settings().runs_dir / _user_id(username)).exists()


# ⑩ 未认证
def test_submit_requires_auth(make_client):
    client = make_client(fake_mode=True)
    assert client.post("/api/tasks/from-url", json={"source": ARXIV_URL}).status_code == 401
    r = client.post("/api/tasks/upload",
                    files={"file": ("a.pdf", b"%PDF-1.4", "application/pdf")})
    assert r.status_code == 401


# ⑪ 提交 1 秒内返回（不等转写）
def test_submit_returns_immediately(make_client):
    client = make_client(fake_mode=True)
    client.app.state.executor = _DummyExecutor()
    _u, token = _register(client)
    start = time.monotonic()
    r = client.post("/api/tasks/from-url", json={"source": ARXIV_URL}, headers=_h(token))
    elapsed = time.monotonic() - start
    assert r.status_code == 201
    assert elapsed < 1.0


# ⑫ 上传传输中断：流式接收中途异常，不留任务行与目录
def test_upload_interrupted_no_residue(make_client, monkeypatch):
    import asyncio

    from starlette.datastructures import UploadFile as StarletteUploadFile

    from web.app.routers import tasks as tasks_router

    # 12a. 单元层：接收函数本身在中途异常时向上抛出
    class _BrokenUpload(StarletteUploadFile):
        def __init__(self) -> None:
            self.calls = 0

        async def read(self, size: int = -1) -> bytes:
            self.calls += 1
            if self.calls >= 2:
                raise ConnectionError("connection lost mid stream")
            return b"%PDF-1.4\npart1"

    with pytest.raises(ConnectionError):
        asyncio.run(tasks_router._receive_upload_bytes(_BrokenUpload(), 10 * 1024 * 1024))

    # 12b. 端点层：接收失败 → 4xx，且无任务行、无任何用户产物目录
    client = make_client(fake_mode=True)
    _u, token = _register(client)

    async def _broken_stream(*_args, **_kwargs):
        raise ConnectionError("connection lost mid stream")

    monkeypatch.setattr(tasks_router, "_receive_upload_bytes", _broken_stream)
    r = client.post(
        "/api/tasks/upload",
        files={"file": ("broken.pdf", b"%PDF-1.4\nwhatever", "application/pdf")},
        headers=_h(token),
    )
    assert 400 <= r.status_code < 500
    db = SessionLocal()
    try:
        assert db.query(Task).count() == 0
    finally:
        db.close()
    assert not any(get_settings().runs_dir.glob("*"))


# ⑬ 假模式端到端：六阶段推进 → succeeded 与结果摘要、产物齐全
def test_fake_end_to_end(make_client, monkeypatch):
    client = make_client(fake_mode=True)
    username, token = _register(client)

    observed: list[tuple[str, str]] = []
    orig_persist = pipeline_run.persist_progress

    def _spy(task_id, stage, progress, message):
        observed.append((stage, message))
        return orig_persist(task_id, stage, progress, message)

    monkeypatch.setattr(pipeline_run, "persist_progress", _spy)

    r = client.post("/api/tasks/from-url", json={"source": ARXIV_URL}, headers=_h(token))
    assert r.status_code == 201, r.text
    task_id = r.json()["id"]

    task = _wait_terminal(task_id)
    assert task.status == "succeeded"
    assert task.stage == "assemble"
    assert task.progress == pytest.approx(1.0)
    assert task.result_section_count == 7
    assert task.result_total_chars and task.result_total_chars > 0
    assert task.result_title
    assert task.failure_category is None
    assert task.failure_code is None
    assert task.failure_message is None
    assert task.finished_at is not None and task.active_slot is None

    seen_stages = [stage for stage, _msg in observed]
    assert seen_stages == STAGES or _is_ordered_subsequence(STAGES, seen_stages), seen_stages
    for stage in STAGES:
        assert stage in seen_stages, f"未观测到阶段 {stage}：{seen_stages}"

    run_dir = get_settings().runs_dir / _user_id(username) / task_id
    assert (run_dir / "source.pdf").exists()
    assert (run_dir / "parsed" / "content_list.json").exists()
    assert (run_dir / "output.md").exists()


def _is_ordered_subsequence(expected: list[str], actual: list[str]) -> bool:
    pos = 0
    for item in actual:
        if pos < len(expected) and item == expected[pos]:
            pos += 1
    return pos == len(expected)


# T041：错误体脱敏——非法 PDF 字节内容不得回显
def test_invalid_pdf_error_does_not_echo_bytes(make_client):
    client = make_client(fake_mode=True)
    client.app.state.executor = _DummyExecutor()
    _u, token = _register(client)
    # 非 %PDF 开头 → 422；载荷中的独特标记不得出现在错误体
    payload = b"NOTPDF" + b"Z-UNIQUE-PAYLOAD-MARKER-xyz" * 4
    r = client.post(
        "/api/tasks/upload",
        files={"file": ("a.pdf", payload, "application/pdf")},
        headers=_h(token),
    )
    assert r.status_code == 422
    assert b"UNIQUE-PAYLOAD-MARKER" not in r.content
    assert r.json()["code"] == "invalid_pdf"


# T041：multipart 缺 file 字段的框架级 422 同样不泄露内部 ctx
def test_upload_missing_field_422_sanitized(make_client):
    client = make_client(fake_mode=True)
    _u, token = _register(client)
    r = client.post("/api/tasks/upload", headers=_h(token))
    assert r.status_code == 422
    body = r.json()
    # 全局脱敏处理器已剔除 input/ctx
    for err in body["detail"]:
        assert "input" not in err and "ctx" not in err
