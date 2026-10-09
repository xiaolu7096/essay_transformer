"""T037：阶段边界取消（US4）。

- queued 开跑前取消 → cancelled，不调 core；
- running 中取消 → cancelled，停在取消发生的阶段，不进下一阶段；
- 终态取消 409 task_not_active；他人 404；
- 成功/失败条件更新并发竞争终态唯一；
- cancelled 释放活动槽，同用户可再次提交。
"""

from __future__ import annotations

import threading
import time
import uuid

import pytest

from core.errors import ETError
from web.app.db import SessionLocal
from web.app.models import Task, User
from web.app.runner import cancel_registry, pipeline_run

ARXIV_URL = "https://arxiv.org/abs/1706.03762"


class _DummyExecutor:
    """不入池：任务停留在 queued，由测试驱动 worker；取消仍置位 registry。"""

    async def submit(self, task_id, user_id):
        pass

    def cancel(self, task_id):
        cancel_registry.set_event(task_id)


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


def _get(task_id):
    db = SessionLocal()
    try:
        return db.get(Task, task_id)
    finally:
        db.close()


def _wait_terminal(task_id, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        task = _get(task_id)
        if task and task.status in ("succeeded", "failed", "cancelled"):
            return task
        time.sleep(0.01)
    raise AssertionError("未终态化")


def _submit_queued(client, token):
    client.app.state.executor = _DummyExecutor()
    r = client.post("/api/tasks/from-url", json={"source": ARXIV_URL}, headers=_h(token))
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ① queued：开跑前事件已置位 → cancelled，无 result
def test_cancel_queued_before_worker_starts(make_client):
    client = make_client(fake_mode=True)
    _u, token = _register(client)
    tid = _submit_queued(client, token)

    r = client.post(f"/api/tasks/{tid}/cancel", headers=_h(token))
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "queued"  # 立即返回快照，不假装已终态

    # worker 真正开跑时应直接收口为 cancelled，不进入 running/不调 core
    pipeline_run.run_task(tid, _user_id(_u))
    task = _get(tid)
    assert task.status == "cancelled"
    assert task.stage is None or task.stage != "assemble"
    assert task.result_title is None
    assert task.result_section_count is None
    assert task.finished_at is not None
    assert task.active_slot is None


# ② running：在 rewrite 阶段被取消 → cancelled，不进入 assemble
def test_cancel_running_at_stage_boundary(make_client, monkeypatch):
    import core.pipeline as core_pipeline

    client = make_client(fake_mode=True)
    _u, token = _register(client)
    tid = _submit_queued(client, token)

    entered_rewrite = threading.Event()

    def _fake_transform(source, *, output_dir, mineru_api_key=None,
                        deepseek_api_key=None, fake=False, progress_cb=None,
                        cancel_event=None):
        for stage, p, msg in [
            ("ingest", 0.05, "ingest"),
            ("parse", 0.25, "parse"),
            ("normalize", 0.35, "normalize"),
            ("plan", 0.4, "plan"),
        ]:
            progress_cb(stage, p, msg)
        entered_rewrite.set()
        # 等待取消（真实语义：取消异常由 core 在阶段边界抛出）
        if cancel_event is not None:
            cancel_event.wait(5.0)
        raise ETError("任务已被用户取消")

    monkeypatch.setattr(core_pipeline, "transform_paper", _fake_transform)

    t = threading.Thread(target=pipeline_run.run_task, args=(tid, _user_id(_u)))
    t.start()
    try:
        assert entered_rewrite.wait(5.0)
        r = client.post(f"/api/tasks/{tid}/cancel", headers=_h(token))
        assert r.status_code == 200, r.text

        task = _wait_terminal(tid)
        assert task.status == "cancelled"
        assert task.stage != "assemble"
        assert task.result_section_count is None
        assert task.active_slot is None
    finally:
        t.join(timeout=10)


# ③ 终态取消 → 409
@pytest.mark.parametrize("terminal_status", ["succeeded", "failed", "cancelled"])
def test_cancel_terminal_409(make_client, terminal_status):
    client = make_client(fake_mode=True)
    _u, token = _register(client)
    tid = _submit_queued(client, token)

    db = SessionLocal()
    try:
        row = db.get(Task, tid)
        row.status = terminal_status
        row.active_slot = None
        if terminal_status == "succeeded":
            row.stage = "assemble"
            row.progress = 1.0
            row.result_title = "x"
            row.result_section_count = 7
            row.result_total_chars = 100
        if terminal_status == "failed":
            row.failure_category = "system"
            row.failure_code = "internal_error"
            row.failure_message = "任务因系统内部问题终止，请联系维护者"
        db.commit()
    finally:
        db.close()

    r = client.post(f"/api/tasks/{tid}/cancel", headers=_h(token))
    assert r.status_code == 409
    assert r.json()["code"] == "task_not_active"


# ④ 他人任务 → 404
def test_cancel_other_user_404(make_client):
    client = make_client(fake_mode=True)
    _a, token_a = _register(client)
    _b, token_b = _register(client)
    tid = _submit_queued(client, token_a)
    r = client.post(f"/api/tasks/{tid}/cancel", headers=_h(token_b))
    assert r.status_code == 404
    assert r.json() == {"detail": "任务不存在"}


def test_cancel_requires_auth(make_client):
    client = make_client(fake_mode=True)
    assert client.post(f"/api/tasks/{uuid.uuid4()}/cancel").status_code == 401


# ⑤ 成功/失败条件更新并发：终态唯一，竞争失败一路 rowcount=0 不覆盖
def test_terminal_update_race_single_winner(make_client):
    client = make_client(fake_mode=True)
    _u, _token = _register(client)
    tid = _submit_queued(client, _token)

    from web.app.runner.pipeline_run import _finish_failure, _finish_success

    class _FakeResult:
        title = "Paper Title"
        sections = [1, 2, 3]
        total_chars = 999

    barrier = threading.Barrier(2)
    outcomes = []

    def _success():
        db = SessionLocal()
        try:
            barrier.wait()
            outcomes.append(("success", _finish_success(db, tid, _FakeResult())))
            db.commit()
        finally:
            db.close()

    def _failure():
        db = SessionLocal()
        try:
            barrier.wait()
            outcomes.append(("failure",
                             _finish_failure(db, tid, "input", "invalid_source", "msg")))
            db.commit()
        finally:
            db.close()

    threads = [threading.Thread(target=_success), threading.Thread(target=_failure)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)

    rowcounts = dict(outcomes)
    assert sorted(rowcounts.values()) == [0, 1]

    task = _get(tid)
    if task.status == "succeeded":
        assert task.result_section_count == 3
        assert task.failure_code is None
    else:
        assert task.status == "failed"
        assert task.failure_code == "invalid_source"
        assert task.result_section_count is None
    assert task.active_slot is None


# ⑥ cancelled 释放活动槽：同用户可再提交
def test_resubmit_after_cancel(make_client):
    client = make_client(fake_mode=True)
    _u, token = _register(client)
    tid = _submit_queued(client, token)
    cancel_registry.set_event(tid)
    pipeline_run.run_task(tid, _user_id(_u))
    assert _get(tid).status == "cancelled"

    r = client.post("/api/tasks/from-url", json={"source": ARXIV_URL}, headers=_h(token))
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "queued"
