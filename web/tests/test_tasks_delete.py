"""任务删除：DELETE /api/tasks/{id}。

- 终态任务删除成功 → 204，列表不再返回，runs 目录被清理；
- queued/running 删除 → 409 task_active；
- 他人任务 → 404；未认证 → 401；不存在 → 404。
"""

from __future__ import annotations

import uuid

from web.app.db import SessionLocal
from web.app.models import Task, User
from web.app.runner.paths import task_run_dir

ARXIV_URL = "https://arxiv.org/abs/1706.03762"


class _DummyExecutor:
    async def submit(self, task_id, user_id):
        pass

    def cancel(self, task_id):
        pass


def _register(client, username: str | None = None) -> tuple[str, str]:
    username = username or f"user_{uuid.uuid4().hex[:10]}"
    r = client.post("/api/auth/register",
                    json={"username": username, "password": "password123"})
    assert r.status_code == 201, r.text
    r = client.post("/api/auth/login",
                    json={"username": username, "password": "password123"})
    assert r.status_code == 200, r.text
    return username, r.json()["access_token"]


def _h(token):
    return {"Authorization": f"Bearer {token}"}


def _user_id(username):
    db = SessionLocal()
    try:
        return db.query(User).filter(User.username_key == username.lower()).one().id
    finally:
        db.close()


def _submit_queued(client, token):
    client.app.state.executor = _DummyExecutor()
    r = client.post("/api/tasks/from-url", json={"source": ARXIV_URL}, headers=_h(token))
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _set_terminal(tid, status="succeeded"):
    db = SessionLocal()
    try:
        row = db.get(Task, tid)
        row.status = status
        row.active_slot = None
        if status == "succeeded":
            row.stage = "assemble"
            row.progress = 1.0
            row.result_title = "x"
            row.result_section_count = 7
            row.result_total_chars = 100
        db.commit()
    finally:
        db.close()


def test_delete_terminal_ok(make_client):
    client = make_client(fake_mode=True)
    u, token = _register(client)
    tid = _submit_queued(client, token)
    uid = _user_id(u)

    # 造一个产物文件，验证目录被清理
    run_dir = task_run_dir(uid, tid)
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "output.md").write_text("x", encoding="utf-8")
    _set_terminal(tid)

    r = client.delete(f"/api/tasks/{tid}", headers=_h(token))
    assert r.status_code == 204, r.text
    assert r.content == b""

    db = SessionLocal()
    try:
        assert db.get(Task, tid) is None
    finally:
        db.close()
    assert not run_dir.exists()

    r = client.get("/api/tasks", headers=_h(token))
    assert r.json()["tasks"] == []


def test_delete_active_409(make_client):
    client = make_client(fake_mode=True)
    _u, token = _register(client)
    tid = _submit_queued(client, token)  # 停留 queued

    r = client.delete(f"/api/tasks/{tid}", headers=_h(token))
    assert r.status_code == 409, r.text
    assert r.json()["code"] == "task_active"

    db = SessionLocal()
    try:
        assert db.get(Task, tid) is not None  # 未被删除
    finally:
        db.close()


def test_delete_other_user_404(make_client):
    client = make_client(fake_mode=True)
    _a, token_a = _register(client)
    _b, token_b = _register(client)
    tid = _submit_queued(client, token_a)
    _set_terminal(tid)

    r = client.delete(f"/api/tasks/{tid}", headers=_h(token_b))
    assert r.status_code == 404
    assert r.json() == {"detail": "任务不存在"}

    # 所有者仍可见
    db = SessionLocal()
    try:
        assert db.get(Task, tid) is not None
    finally:
        db.close()


def test_delete_missing_404(make_client):
    client = make_client(fake_mode=True)
    _u, token = _register(client)
    r = client.delete(f"/api/tasks/{uuid.uuid4()}", headers=_h(token))
    assert r.status_code == 404


def test_delete_requires_auth(make_client):
    client = make_client(fake_mode=True)
    r = client.delete(f"/api/tasks/{uuid.uuid4()}")
    assert r.status_code == 401
