"""T030：重启恢复——残留 queued/running 统一收口为 system/interrupted_restart。"""

from __future__ import annotations

import uuid

from web.app.db import SessionLocal
from web.app.models import Task, User
from web.app.recovery import recover_interrupted

MESSAGE = "服务曾重启，该任务被中断，请重新提交"


def _register_user(client) -> str:
    username = f"recovery_{uuid.uuid4().hex[:8]}"
    r = client.post("/api/auth/register",
                    json={"username": username, "password": "password123"})
    assert r.status_code == 201, r.text
    db = SessionLocal()
    try:
        return db.query(User).filter(User.username_key == username.lower()).one().id
    finally:
        db.close()


def _insert(user_id: str, status: str, active_slot) -> str:
    db = SessionLocal()
    try:
        task = Task(
            user_id=user_id,
            source_kind="arxiv",
            source_ref="1706.03762",
            status=status,
            progress=0.3,
            stage="parse",
            fake_mode=True,
            active_slot=active_slot,
        )
        db.add(task)
        db.commit()
        return task.id
    finally:
        db.close()


def test_recover_only_active_rows(make_client):
    client = make_client(fake_mode=True)  # 经 lifespan 建表
    user_a = _register_user(client)
    user_b = _register_user(client)

    # 每用户至多一条活动行（部分唯一索引约束），两个用户各造一种活动态
    queued_id = _insert(user_a, "queued", 1)
    running_id = _insert(user_b, "running", 1)
    succeeded_id = _insert(user_a, "succeeded", None)
    failed_id = _insert(user_b, "failed", None)

    changed = recover_interrupted()
    assert changed == 2

    db = SessionLocal()
    try:
        for tid in (queued_id, running_id):
            row = db.get(Task, tid)
            assert row.status == "failed"
            assert row.failure_category == "system"
            assert row.failure_code == "interrupted_restart"
            assert row.failure_message == MESSAGE
            assert row.finished_at is not None
            assert row.active_slot is None

        assert db.get(Task, succeeded_id).status == "succeeded"
        failed_row = db.get(Task, failed_id)
        assert failed_row.status == "failed"
        assert failed_row.failure_code is None

        # 幂等：再跑一次没有行被改
        assert recover_interrupted() == 0
    finally:
        db.close()
