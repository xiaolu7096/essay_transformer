"""T026：任务历史列表/详情（仅本人、倒序、同形、越权 404）与三分类失败。"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from core.errors import InputError, ServiceError
from web.app.db import SessionLocal
from web.app.models import Task, User
from web.app.runner import pipeline_run


def _naive_utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)

ARXIV_URL = "https://arxiv.org/abs/1706.03762"


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


def _insert_task(user_id: str, **overrides) -> str:
    base = dict(
        user_id=user_id,
        source_kind="arxiv",
        source_ref="1706.03762",
        status="succeeded",
        stage="assemble",
        progress=1.0,
        message="完成",
        fake_mode=True,
        active_slot=None,
    )
    base.update(overrides)
    db = SessionLocal()
    try:
        task = Task(**base)
        db.add(task)
        db.commit()
        return task.id
    finally:
        db.close()


def _wait_terminal(task_id: str, timeout: float = 10.0) -> Task:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        db = SessionLocal()
        try:
            task = db.get(Task, task_id)
            if task and task.status in ("succeeded", "failed", "cancelled"):
                return task
        finally:
            db.close()
        time.sleep(0.02)
    raise AssertionError(f"任务 {task_id} 未终态化")


# ---------- 列表 ----------

def test_list_only_own_and_desc(make_client):
    client = make_client(fake_mode=True)
    name_a, token_a = _register(client)
    name_b, token_b = _register(client)
    a, b = _user_id(name_a), _user_id(name_b)

    now = _naive_utcnow()
    id_a1 = _insert_task(a, created_at=now - timedelta(hours=2))
    id_a2 = _insert_task(a, created_at=now - timedelta(hours=1))
    _insert_task(b, created_at=now)

    r = client.get("/api/tasks", headers=_h(token_a))
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body.keys()) == {"tasks"}
    ids = [t["id"] for t in body["tasks"]]
    assert ids == [id_a2, id_a1]


def test_list_requires_auth(make_client):
    client = make_client(fake_mode=True)
    assert client.get("/api/tasks").status_code == 401


def test_list_item_shape_succeeded(make_client):
    client = make_client(fake_mode=True)
    _u, token = _register(client)
    tid = _insert_task(
        _user_id(_u),
        status="succeeded",
        stage="assemble",
        progress=1.0,
        result_title="Attention Is All You Need",
        result_section_count=7,
        result_total_chars=12345,
        started_at=_naive_utcnow(),
        finished_at=_naive_utcnow(),
    )
    r = client.get("/api/tasks", headers=_h(token))
    item = next(t for t in r.json()["tasks"] if t["id"] == tid)
    assert item["result"] == {
        "title": "Attention Is All You Need",
        "section_count": 7,
        "total_chars": 12345,
    }
    assert item["failure"] is None
    # 不含路径/密文字段
    assert "run_dir" not in item and "path" not in item


def test_list_item_shape_failed(make_client):
    client = make_client(fake_mode=True)
    _u, token = _register(client)
    tid = _insert_task(
        _user_id(_u),
        status="failed",
        failure_category="service",
        failure_code="service_unavailable",
        failure_message="外部解析或模型服务暂时不可用（超时或服务故障），请稍后重试",
        finished_at=_naive_utcnow(),
    )
    r = client.get("/api/tasks", headers=_h(token))
    item = next(t for t in r.json()["tasks"] if t["id"] == tid)
    assert item["result"] is None
    assert item["failure"]["category"] == "service"
    assert item["failure"]["code"] == "service_unavailable"
    assert item["failure"]["message"]


# ---------- 详情 ----------

def test_detail_own_200(make_client):
    client = make_client(fake_mode=True)
    _u, token = _register(client)
    tid = _insert_task(_user_id(_u), status="queued", progress=0.0,
                       stage=None, message="已入队，等待执行…")
    r = client.get(f"/api/tasks/{tid}", headers=_h(token))
    assert r.status_code == 200, r.text
    assert r.json()["id"] == tid
    assert r.json()["status"] == "queued"


def test_detail_other_user_404(make_client):
    client = make_client(fake_mode=True)
    _a, token_a = _register(client)
    _b, token_b = _register(client)
    tid = _insert_task(_user_id(_a))
    r = client.get(f"/api/tasks/{tid}", headers=_h(token_b))
    assert r.status_code == 404
    assert r.json() == {"detail": "任务不存在"}


def test_detail_missing_404(make_client):
    client = make_client(fake_mode=True)
    _u, token = _register(client)
    r = client.get(f"/api/tasks/{uuid.uuid4()}", headers=_h(token))
    assert r.status_code == 404
    assert r.json() == {"detail": "任务不存在"}


def test_detail_requires_auth(make_client):
    client = make_client(fake_mode=True)
    assert client.get(f"/api/tasks/{uuid.uuid4()}").status_code == 401


# ---------- worker 三分类（替身注入故障） ----------

CASES = [
    (
        InputError("这是不支持的链接类型：ftp://x"),
        ("input", "invalid_source"),
    ),
    (
        InputError("MinerU 鉴权失败：HTTP 401，API Key 缺失、错误或已失效。"),
        ("service", "key_rejected"),
    ),
    (
        InputError("DEEPSEEK_API_KEY 未设置（请传入 api_key 参数或设置环境变量）"),
        ("service", "key_missing"),
    ),
    (
        ServiceError("请求超时：read timed out"),
        ("service", "service_unavailable"),
    ),
    (
        ValueError("secret boom xyz Bearer abcdef1234567890ABCDEF1234567890"),
        ("system", "internal_error"),
    ),
]


@pytest.mark.parametrize("exc,expected", CASES)
def test_worker_failure_classification(make_client, monkeypatch, exc, expected):
    client = make_client(fake_mode=True)
    username, token = _register(client)

    def _boom(*_args, **_kwargs):
        raise exc

    # 真实执行器跑 worker，但 core 入口被替身替换为抛指定异常
    import core.pipeline as core_pipeline

    monkeypatch.setattr(core_pipeline, "transform_paper", _boom)

    r = client.post("/api/tasks/from-url", json={"source": ARXIV_URL}, headers=_h(token))
    assert r.status_code == 201, r.text
    task = _wait_terminal(r.json()["id"])

    category, code = expected
    assert task.status == "failed"
    assert task.failure_category == category
    assert task.failure_code == code
    assert task.failure_message
    # 固定模板：不得出现异常原文、密钥片段
    forbidden = ["secret boom", "Bearer", "abcdef", "ftp://x", "timed out"]
    for word in forbidden:
        assert word not in (task.failure_message or ""), (word, task.failure_message)
    assert task.result_section_count is None
    assert task.active_slot is None and task.finished_at is not None
