"""Spec 003 US1（T004）：任务事件实时推送测试。

覆盖：401/404 建流前拒绝、晚订阅终态双帧关流、ProgressHub
顺序/多订阅/水位/队列压力（终态不丢）、心跳、重启恢复、
假模式端到端帧序列、载荷无路径/密文。全程离线零网络。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid

import pytest

from web.app.db import SessionLocal
from web.app.models import Task, User
from web.app.runner.events import QUEUE_MAXSIZE, ProgressHub

ARXIV_URL = "https://arxiv.org/abs/1706.03762"
MESSAGE_INTERRUPTED = "服务曾重启，该任务被中断，请重新提交"


# --------------------------------------------------------------------------
# 辅助
# --------------------------------------------------------------------------

class _DummyExecutor:
    async def submit(self, task_id, user_id):
        pass

    def cancel(self, task_id):
        pass


def _register(client, username=None):
    username = username or f"rt_{uuid.uuid4().hex[:10]}"
    client.post("/api/auth/register",
                json={"username": username, "password": "password123"})
    r = client.post("/api/auth/login",
                    json={"username": username, "password": "password123"})
    return username, r.json()["access_token"]


def _h(token):
    return {"Authorization": f"Bearer {token}"}


def _user_id(username):
    db = SessionLocal()
    try:
        return db.query(User).filter(User.username_key == username.lower()).one().id
    finally:
        db.close()


def _insert_task(user_id: str, status: str, **overrides) -> str:
    db = SessionLocal()
    try:
        task = Task(
            user_id=user_id,
            source_kind="arxiv",
            source_ref="1706.03762",
            status=status,
            progress=overrides.get("progress", 0.0),
            stage=overrides.get("stage"),
            message=overrides.get("message"),
            fake_mode=True,
            failure_category=overrides.get("failure_category"),
            failure_code=overrides.get("failure_code"),
            failure_message=overrides.get("failure_message"),
            result_title=overrides.get("result_title"),
            result_section_count=overrides.get("result_section_count"),
            result_total_chars=overrides.get("result_total_chars"),
            active_slot=1 if status in ("queued", "running") else None,
        )
        db.add(task)
        db.commit()
        return task.id
    finally:
        db.close()


def _succeeded_row(user_id: str) -> str:
    return _insert_task(
        user_id, "succeeded", progress=1.0, stage="assemble",
        message="完成：999 字符",
        result_title="Sample Paper: A Demonstration of the Pipeline",
        result_section_count=7, result_total_chars=999,
    )


def _drain(q) -> list[dict]:
    frames = []
    while not q.empty():
        frames.append(q.get_nowait())
    return frames


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def read_sse(client, task_id: str, token: str, *, stop_after_terminal=True):
    """同步读取 SSE 流，返回 [{event,data|raw}]；终态后服务端关流。"""
    url = f"/api/tasks/{task_id}/events"
    events: list[dict] = []
    current: dict = {}
    with client.stream("GET", url, headers=_h(token)) as resp:
        yield_status = resp.status_code
        yield_headers = dict(resp.headers)
        if resp.status_code != 200:
            return [{"_status": resp.status_code, "_body": resp.read().decode("utf-8"),
                     "_headers": dict(resp.headers)}]
        for raw_line in resp.iter_lines():
            line = raw_line if isinstance(raw_line, str) else raw_line.decode("utf-8")
            line = line.strip()
            if line == "":
                if current:
                    events.append(current)
                    current = {}
                    if stop_after_terminal and events[-1].get("event") == "terminal":
                        break
                continue
            if line.startswith(":"):
                events.append({"event": "comment", "raw": line})
                continue
            if line.startswith("event:"):
                current["event"] = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                current["data"] = json.loads(line.split(":", 1)[1].strip())
    out = [{"_status": yield_status, "_headers": yield_headers}]
    out.extend(events)
    return out


# --------------------------------------------------------------------------
# ① 401：未认证建流前 JSON 拒绝
# --------------------------------------------------------------------------

def test_events_requires_auth(make_client):
    client = make_client(fake_mode=True)
    _u, token = _register(client)
    tid = _succeeded_row(_user_id(_u))
    with client.stream("GET", f"/api/tasks/{tid}/events") as resp:
        assert resp.status_code == 401
        body = json.loads(resp.read().decode("utf-8"))
        assert body == {"detail": "未授权或登录已过期"}


# --------------------------------------------------------------------------
# ② 404：越权与不存在统一"任务不存在"，建流前返回
# --------------------------------------------------------------------------

def test_events_other_user_and_missing_404(make_client):
    client = make_client(fake_mode=True)
    _a, token_a = _register(client)
    _b, token_b = _register(client)
    tid = _succeeded_row(_user_id(_a))

    with client.stream("GET", f"/api/tasks/{tid}/events",
                       headers=_h(token_b)) as resp:
        assert resp.status_code == 404
        body = json.loads(resp.read().decode("utf-8"))
        assert body == {"detail": "任务不存在"}

    with client.stream("GET", f"/api/tasks/{uuid.uuid4()}/events",
                       headers=_h(token_a)) as resp:
        assert resp.status_code == 404


# --------------------------------------------------------------------------
# ③ 晚订阅终态：snapshot + 同载荷 terminal，随后关流
# --------------------------------------------------------------------------

def test_events_terminal_task_snapshot_then_terminal(make_client):
    client = make_client(fake_mode=True)
    username, token = _register(client)
    tid = _succeeded_row(_user_id(username))

    frames = read_sse(client, tid, token)
    assert frames[0]["_status"] == 200
    assert frames[0]["_headers"]["content-type"].startswith("text/event-stream")
    assert frames[0]["_headers"]["cache-control"] == "no-store"
    assert frames[0]["_headers"]["x-accel-buffering"] == "no"

    data_frames = [f for f in frames[1:] if f.get("event") != "comment"]
    assert [f["event"] for f in data_frames] == ["snapshot", "terminal"]
    snap = data_frames[0]["data"]
    term = data_frames[1]["data"]
    assert snap["id"] == tid
    assert snap["status"] == "succeeded"
    assert snap["result"] == {
        "title": "Sample Paper: A Demonstration of the Pipeline",
        "section_count": 7,
        "total_chars": 999,
    }
    # terminal 与 snapshot 同载荷（含相同 seq）
    assert term == snap
    assert term["seq"] >= 0


# --------------------------------------------------------------------------
# ④ ProgressHub 单元：seq 单调、多订阅者独立、unsubscribe 后不收帧
# --------------------------------------------------------------------------

def test_hub_order_multi_subscribers_unsubscribe():
    async def scen():
        hub = ProgressHub()
        hub.bind_loop(asyncio.get_running_loop())
        q1 = hub.subscribe("t1")
        q2 = hub.subscribe("t1")

        for i in range(3):
            hub.publish("t1", {"n": i})
        await asyncio.sleep(0)

        f1, f2 = _drain(q1), _drain(q2)
        assert [f["seq"] for f in f1] == [1, 2, 3]
        assert [d["payload"]["n"] for d in f1] == [0, 1, 2]
        assert f1 == f2  # 多标签：两个订阅者各自独立收到全帧

        hub.unsubscribe("t1", q1)
        hub.publish("t1", {"n": 9})
        await asyncio.sleep(0)
        assert q1.empty()
        assert q2.qsize() == 1
        assert q2.get_nowait()["seq"] == 4

    _run(scen())


# --------------------------------------------------------------------------
# ⑤ 水位过滤：seq <= snap_seq 不下发；新帧正常转发
# --------------------------------------------------------------------------

def test_hub_watermark_filtering():
    async def scen():
        hub = ProgressHub()
        hub.bind_loop(asyncio.get_running_loop())
        q = hub.subscribe("t1")

        # 模拟"订阅登记后、读快照前"一帧已提交（先 commit 后 publish）
        hub.publish("t1", {"status": "running"})
        await asyncio.sleep(0)
        snap_seq = hub.current_seq("t1")
        assert snap_seq == 1

        # 该状态必已在快照行内：<= 水位的帧不下发，不重复
        forwarded = [f for f in _drain(q) if f["seq"] > snap_seq]
        assert forwarded == []

        hub.publish("t1", {"status": "running", "stage": "parse"})
        await asyncio.sleep(0)
        forwarded = [f for f in _drain(q) if f["seq"] > snap_seq]
        assert len(forwarded) == 1
        assert forwarded[0]["seq"] == 2

    _run(scen())


# --------------------------------------------------------------------------
# ⑥ 队列压力：满队丢最旧非终态，terminal 必达且只有一帧
# --------------------------------------------------------------------------

def test_hub_queue_pressure_terminal_never_dropped():
    async def scen():
        hub = ProgressHub()
        hub.bind_loop(asyncio.get_running_loop())
        q = hub.subscribe("t1")

        for i in range(QUEUE_MAXSIZE):
            hub.publish("t1", {"i": i})
        await asyncio.sleep(0)
        assert q.qsize() == QUEUE_MAXSIZE

        hub.publish("t1", {"i": QUEUE_MAXSIZE})  # 挤掉 seq=1
        await asyncio.sleep(0)
        hub.publish("t1", {"final": True}, terminal=True)  # 挤掉 seq=2
        await asyncio.sleep(0)

        frames = _drain(q)
        assert len(frames) == QUEUE_MAXSIZE
        seqs = [f["seq"] for f in frames]
        assert seqs == sorted(seqs)
        assert 1 not in seqs and 2 not in seqs
        terminals = [f for f in frames if f["event"] == "terminal"]
        assert len(terminals) == 1
        assert frames[-1] is terminals[0]

    _run(scen())


# --------------------------------------------------------------------------
# ⑦ 假模式端到端：snapshot → update*（若赶上）→ terminal 单调收敛
# --------------------------------------------------------------------------

def test_events_fake_run_end_to_end(make_client, monkeypatch):
    client = make_client(fake_mode=True)
    username, token = _register(client)

    r = client.post("/api/tasks/from-url", json={"source": ARXIV_URL},
                    headers=_h(token))
    assert r.status_code == 201, r.text
    tid = r.json()["id"]

    frames = read_sse(client, tid, token)
    assert frames[0]["_status"] == 200
    data_frames = [f for f in frames[1:] if f.get("event") != "comment"]
    names = [f["event"] for f in data_frames]
    assert names[0] == "snapshot"
    assert names[-1] == "terminal"
    assert set(names) <= {"snapshot", "update", "terminal"}

    seqs = [f["data"]["seq"] for f in data_frames]
    # snapshot 之后只接受更大 seq：无重复、无倒退
    after_snap = seqs[1:]
    assert after_snap == sorted(set(after_snap))
    assert all(s > seqs[0] for s in after_snap)

    term = data_frames[-1]["data"]
    assert term["status"] == "succeeded"
    assert term["stage"] == "assemble"
    assert term["result"]["section_count"] == 7
    assert term["result"]["total_chars"] > 0
    assert term["finished_at"]


# --------------------------------------------------------------------------
# ⑧ 心跳：活动任务 15s（测试 monkeypatch 极小值）收到 ': ping' 注释帧
# --------------------------------------------------------------------------

@pytest.fixture()
def anyio_backend() -> str:
    return "asyncio"


async def _drive_sse_raw(app, path: str, token: str):
    """原生 ASGI 驱动（真流式）。

    httpx 0.28 的 ASGITransport 与 Starlette 同步 TestClient 都会把
    响应整体缓冲（等应用退出才返回），无法测试永不关流的活动 SSE。
    这里直接构造 ASGI 作用域：返回 (meta, chunks, task)；
    用例读完后 cancel(task) 模拟客户端断开。
    """
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": b"",
        "headers": [
            (b"host", b"testserver"),
            (b"authorization", f"Bearer {token}".encode("utf-8")),
        ],
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 123),
        "root_path": "",
        "app": app,
    }
    chunks: asyncio.Queue[bytes] = asyncio.Queue()
    started = asyncio.Event()
    finished = asyncio.Event()
    meta: dict = {}

    async def receive():
        await finished.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.start":
            meta["status"] = message["status"]
            meta["headers"] = message.get("headers", [])
            started.set()
        elif message["type"] == "http.response.body":
            body = message.get("body", b"")
            if body:
                await chunks.put(body)
            if not message.get("more_body", False):
                finished.set()

    task = asyncio.create_task(app(scope, receive, send))
    await started.wait()
    return meta, chunks, task


@pytest.mark.anyio
async def test_events_heartbeat_comment(make_client, monkeypatch):
    from web.app.routers import tasks as tasks_router

    monkeypatch.setattr(tasks_router, "PING_INTERVAL_SECONDS", 0.05)
    client = make_client(fake_mode=True)
    client.app.state.executor = _DummyExecutor()  # 任务永驻 queued
    username, token = _register(client)

    r = client.post("/api/tasks/from-url", json={"source": ARXIV_URL},
                    headers=_h(token))
    tid = r.json()["id"]

    meta, chunks, task = await _drive_sse_raw(
        client.app, f"/api/tasks/{tid}/events", token
    )
    try:
        assert meta["status"] == 200
        headers = {k.decode(): v.decode() for k, v in meta["headers"]}
        assert headers["content-type"].startswith("text/event-stream")
        assert headers["cache-control"] == "no-store"
        assert headers["x-accel-buffering"] == "no"

        first = await asyncio.wait_for(chunks.get(), timeout=2)
        assert b"event: snapshot" in first

        # 0.05s 心跳间隔：持续收帧直到出现注释帧（流保持打开，不关流）
        buf = b""
        got_ping = False
        for _ in range(20):
            try:
                buf += await asyncio.wait_for(chunks.get(), timeout=0.3)
            except asyncio.TimeoutError:
                break
            if b": ping" in buf:
                got_ping = True
                break
        assert got_ping, f"未收到心跳帧，已有数据：{buf!r}"
    finally:
        # 模拟客户端断开：生成器被取消，finally 必须退订
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    assert task.done()
    assert tid not in client.app.state.hub._subs


# --------------------------------------------------------------------------
# ⑨ 重启恢复：残留任务重连即 failed/system/interrupted_restart + 关流
# --------------------------------------------------------------------------

def test_events_after_recovery_interrupted(make_client):
    from web.app.recovery import recover_interrupted

    client = make_client(fake_mode=True)
    username, token = _register(client)
    uid = _user_id(username)
    tid = _insert_task(uid, "queued", progress=0.3, stage="parse",
                       message="解析中…")

    assert recover_interrupted() == 1

    frames = read_sse(client, tid, token)
    data_frames = [f for f in frames[1:] if f.get("event") != "comment"]
    assert [f["event"] for f in data_frames] == ["snapshot", "terminal"]
    snap = data_frames[0]["data"]
    assert snap["status"] == "failed"
    assert snap["failure"] == {
        "category": "system",
        "code": "interrupted_restart",
        "message": MESSAGE_INTERRUPTED,
    }


# --------------------------------------------------------------------------
# ⑩ 载荷不含文件系统路径/密文
# --------------------------------------------------------------------------

def test_events_payload_has_no_paths_or_secrets(make_client, tmp_path):
    client = make_client(fake_mode=True)
    username, token = _register(client)
    tid = _succeeded_row(_user_id(username))

    raw_parts = []
    with client.stream("GET", f"/api/tasks/{tid}/events",
                       headers=_h(token)) as resp:
        for chunk in resp.iter_lines():
            raw_parts.append(chunk if isinstance(chunk, str)
                             else chunk.decode("utf-8"))
    raw = "\n".join(raw_parts)
    assert "run_dir" not in raw
    assert str(tmp_path) not in raw
    assert "runs" + "\\" not in raw and "runs/" not in raw
    assert "Bearer" not in raw
    assert "source_ref" not in raw
