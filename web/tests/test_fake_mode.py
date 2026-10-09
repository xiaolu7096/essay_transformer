"""T040（US5）：假模式收口——免密钥、零外部调用、开关只由运维控制。"""

from __future__ import annotations

import time
import uuid

import openai
import requests

from web.app.config import WEB_DIR, get_settings
from web.app.db import SessionLocal
from web.app.models import Task, User

ARXIV_URL = "https://arxiv.org/abs/1706.03762"
FIXTURE_PARSED = WEB_DIR / "fixtures" / "fake_run" / "parsed" / "content_list.json"


class _DummyExecutor:
    async def submit(self, task_id, user_id):
        pass

    def cancel(self, task_id):
        pass


def _register(client, username=None):
    username = username or f"user_{uuid.uuid4().hex[:10]}"
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


def _wait_terminal(task_id, timeout=20.0):
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
    raise AssertionError("任务未终态化")


def _install_network_sentinels(monkeypatch):
    """任何 HTTP 出口 / OpenAI 实例化都视为测试失败。"""

    def _fail(*_args, **_kwargs):
        raise AssertionError("假模式发生了真实网络调用")

    class _FailOpenAI:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("假模式实例化了 OpenAI 客户端")

    monkeypatch.setattr(requests.Session, "get", _fail)
    monkeypatch.setattr(requests.Session, "post", _fail)
    monkeypatch.setattr(requests.Session, "put", _fail)
    monkeypatch.setattr(requests.Session, "request", _fail)
    monkeypatch.setattr(openai, "OpenAI", _FailOpenAI)


# ①a 无任何凭据：from-url 201 并一路 succeeded，TaskOut.fake_mode=true
def test_fake_from_url_no_credentials(make_client, monkeypatch):
    _install_network_sentinels(monkeypatch)
    client = make_client(fake_mode=True)
    _u, token = _register(client)

    r = client.post("/api/tasks/from-url", json={"source": ARXIV_URL}, headers=_h(token))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["fake_mode"] is True

    task = _wait_terminal(body["id"])
    assert task.status == "succeeded"
    assert task.fake_mode is True
    assert task.result_section_count == 7
    assert task.result_total_chars > 0
    assert task.result_title


# ①b 无凭据：upload 同样端到端 succeeded
def test_fake_upload_no_credentials(make_client, monkeypatch):
    _install_network_sentinels(monkeypatch)
    client = make_client(fake_mode=True)
    _u, token = _register(client)

    pdf_bytes = (WEB_DIR / "fixtures" / "fake_run" / "source.pdf").read_bytes()
    r = client.post(
        "/api/tasks/upload",
        files={"file": ("paper.pdf", pdf_bytes, "application/pdf")},
        headers=_h(token),
    )
    assert r.status_code == 201, r.text
    assert r.json()["fake_mode"] is True

    task = _wait_terminal(r.json()["id"])
    assert task.status == "succeeded"
    assert task.result_section_count == 7


# ② 零网络哨兵已在 ① 中启用；这里再断言全程无任何凭据行也能成功
def test_fake_runs_without_credential_rows(make_client, monkeypatch):
    _install_network_sentinels(monkeypatch)
    client = make_client(fake_mode=True)
    username, token = _register(client)

    r = client.post("/api/tasks/from-url", json={"source": ARXIV_URL}, headers=_h(token))
    _wait_terminal(r.json()["id"])

    from web.app.models import Credential

    db = SessionLocal()
    try:
        assert db.query(Credential).filter(
            Credential.user_id == _user_id(username)
        ).count() == 0
    finally:
        db.close()


# ③ 开关不可由请求影响：real 模式请求体夹带 fake_mode:true 被忽略
def test_request_cannot_enable_fake_mode(make_client):
    client = make_client(fake_mode=False)
    client.app.state.executor = _DummyExecutor()
    _u, token = _register(client)

    # 先配好两凭据（任意值；dummy 执行器不会真正调用）
    for svc in ("mineru", "deepseek"):
        rr = client.put(f"/api/credentials/{svc}",
                        json={"secret": f"test-key-{svc}"}, headers=_h(token))
        assert rr.status_code == 200, rr.text

    r = client.post(
        "/api/tasks/from-url",
        json={"source": ARXIV_URL, "fake_mode": True},
        headers=_h(token),
    )
    assert r.status_code == 201, r.text
    # 服务端只认 WEB_FAKE_MODE：任务必须是真实模式
    assert r.json()["fake_mode"] is False


# ④ real 模式无凭据：夹带字段也救不了，仍 400 credentials_required
def test_real_mode_still_requires_credentials_with_extra_field(make_client):
    client = make_client(fake_mode=False)
    client.app.state.executor = _DummyExecutor()
    _u, token = _register(client)
    r = client.post(
        "/api/tasks/from-url",
        json={"source": ARXIV_URL, "fake_mode": True},
        headers=_h(token),
    )
    assert r.status_code == 400
    assert r.json()["code"] == "credentials_required"


# U2（LOW 收口）：内置物料缺失（部署残缺）时优雅 failed/system，不向用户泄露路径
def test_fake_missing_fixture_is_system_failure(make_client, monkeypatch):
    from web.app.runner import pipeline_run

    client = make_client(fake_mode=True)
    _u, token = _register(client)

    monkeypatch.setattr(
        pipeline_run, "FAKE_FIXTURE_DIR",
        WEB_DIR / "fixtures" / "__definitely_missing__",
    )

    r = client.post("/api/tasks/from-url", json={"source": ARXIV_URL}, headers=_h(token))
    task = _wait_terminal(r.json()["id"])
    assert task.status == "failed"
    assert task.failure_category == "system"
    assert task.failure_code == "internal_error"
    assert "__definitely_missing__" not in (task.failure_message or "")


# ⑤ 产物 content_list 字节与 fixture 完全一致（来自内置物料，非网络）
def test_fake_artifacts_match_fixture(make_client, monkeypatch):
    _install_network_sentinels(monkeypatch)
    client = make_client(fake_mode=True)
    username, token = _register(client)

    r = client.post("/api/tasks/from-url", json={"source": ARXIV_URL}, headers=_h(token))
    _wait_terminal(r.json()["id"])

    run_dir = get_settings().runs_dir / _user_id(username) / r.json()["id"]
    produced = run_dir / "parsed" / "content_list.json"
    assert produced.exists()
    assert produced.read_bytes() == FIXTURE_PARSED.read_bytes()
    # output.md 由 core 基于 fixture 物料生成
    assert (run_dir / "output.md").read_text(encoding="utf-8").startswith("# ")


# --------------------------------------------------------------------------
# Spec 003 US4（T021）：无密钥假模式端到端新链路
# 事件流 → reader（8 节）→ 图片 → zip，全程零网络哨兵
# --------------------------------------------------------------------------

def _read_sse(client, task_id, token):
    """同步读到终态关流，返回 data 帧 [{event,data}]。"""
    import json as _json

    frames = []
    current = {}
    with client.stream(
        "GET", f"/api/tasks/{task_id}/events", headers=_h(token)
    ) as resp:
        assert resp.status_code == 200
        for raw_line in resp.iter_lines():
            line = raw_line if isinstance(raw_line, str) else raw_line.decode("utf-8")
            line = line.strip()
            if line == "":
                if current:
                    frames.append(current)
                    current = {}
                continue
            if line.startswith(":"):
                continue
            if line.startswith("event:"):
                current["event"] = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                current["data"] = _json.loads(line.split(":", 1)[1].strip())
    return frames


def test_fake_full_new_chain_events_reader_images_zip(make_client, monkeypatch):
    import io
    import zipfile

    from web.app.runner.paths import task_run_dir
    from web.app.services.artifacts import safe_filename

    _install_network_sentinels(monkeypatch)
    client = make_client(fake_mode=True)
    username, token = _register(client)

    r = client.post("/api/tasks/from-url", json={"source": ARXIV_URL}, headers=_h(token))
    assert r.status_code == 201, r.text
    tid = r.json()["id"]
    uid = _user_id(username)

    # ① 事件流：snapshot 起步、terminal 收尾，中间只允许 update
    frames = _read_sse(client, tid, token)
    names = [f["event"] for f in frames]
    assert names[0] == "snapshot"
    assert names[-1] == "terminal"
    assert set(names) <= {"snapshot", "update", "terminal"}
    assert len(names) >= 2
    assert frames[-1]["data"]["status"] == "succeeded"

    run_dir = task_run_dir(uid, tid)

    # ② reader：8 节（7 章 + 附录）；T017 后概览/架构类图判 narrate 留主线，不进折叠区
    rr = client.get(f"/api/tasks/{tid}/reader", headers=_h(token))
    assert rr.status_code == 200, rr.text
    reader = rr.json()
    assert reader["fake_mode"] is True
    assert [s["slug"] for s in reader["sections"]] == [f"s{i}" for i in range(8)]
    appendix = reader["sections"][-1]
    assert appendix["heading"] == "附录（折叠区）"
    assert "images/sample.png" not in appendix["content"]
    assert "<details" in appendix["content"]

    # ③ 图片：200 image/png，字节与落盘文件一致
    png_path = run_dir / "images" / "sample.png"
    ir = client.get(
        f"/api/tasks/{tid}/artifacts/images/sample.png", headers=_h(token)
    )
    assert ir.status_code == 200
    assert ir.headers["content-type"] == "image/png"
    assert "content-disposition" not in {k.lower() for k in ir.headers.keys()}
    assert ir.content == png_path.read_bytes()

    # ④ zip：恰 2 条目，md 与 png 字节逐字一致
    zr = client.get(f"/api/tasks/{tid}/artifact/download", headers=_h(token))
    assert zr.status_code == 200
    assert zr.headers["content-type"] == "application/zip"
    zf = zipfile.ZipFile(io.BytesIO(zr.content))
    task = _wait_terminal(tid)
    base = safe_filename(task.result_title, tid)
    assert sorted(zf.namelist()) == sorted(
        [f"{base}/{base}.md", f"{base}/images/sample.png"]
    )
    assert zf.read(f"{base}/{base}.md") == (run_dir / "output.md").read_bytes()
    assert zf.read(f"{base}/images/sample.png") == png_path.read_bytes()


# ③ 开关只由运维控制：reader/images/download/events 附加 fake_mode 参数无效
def test_fake_flag_param_ignored_on_new_chain_endpoints(make_client):
    from web.app.runner.paths import task_run_dir

    client = make_client(fake_mode=False)
    client.app.state.executor = _DummyExecutor()
    _u, token = _register(client)
    uid = _user_id(_u)

    db = SessionLocal()
    try:
        row = Task(
            user_id=uid,
            source_kind="arxiv",
            source_ref="1706.03762",
            status="succeeded",
            progress=1.0,
            stage="assemble",
            fake_mode=False,
            result_title="Real Legacy Paper",
            result_section_count=7,
            result_total_chars=100,
        )
        db.add(row)
        db.commit()
        tid = row.id
    finally:
        db.close()

    run_dir = task_run_dir(uid, tid)
    run_dir.mkdir(parents=True, exist_ok=True)
    md = "# Real Legacy Paper\n\n" + "".join(
        f"## C{i}\n\n正文{i}\n" for i in range(7)
    )
    (run_dir / "output.md").write_text(md, encoding="utf-8", newline="")

    # query 夹带 fake_mode=true：不得 422，且响应口径仍是真实模式
    rr = client.get(
        f"/api/tasks/{tid}/reader?fake_mode=true", headers=_h(token)
    )
    assert rr.status_code == 200, rr.text
    assert rr.json()["fake_mode"] is False

    zr = client.get(
        f"/api/tasks/{tid}/artifact/download?fake_mode=true", headers=_h(token)
    )
    assert zr.status_code == 200

    frames = []
    with client.stream(
        "GET", f"/api/tasks/{tid}/events?fake_mode=true", headers=_h(token)
    ) as resp:
        assert resp.status_code == 200
        for raw_line in resp.iter_lines():
            line = raw_line if isinstance(raw_line, str) else raw_line.decode("utf-8")
            if line.startswith("data:"):
                import json as _json

                frames.append(_json.loads(line.split(":", 1)[1].strip()))
    assert frames and frames[0]["fake_mode"] is False

