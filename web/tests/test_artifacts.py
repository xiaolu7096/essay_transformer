"""Spec 003 US2/US3（T012/T018）：成品阅读器与 zip 下载测试。

全程离线：假模式 e2e 产出真实 output.md + images/sample.png；
解析纯函数、门禁（401/404/409）、图片逃逸、zip 结构/文件名安全
全部在本文件断言。
"""

from __future__ import annotations

import io
import re
import time
import uuid
import zipfile

import pytest

from web.app.db import SessionLocal
from web.app.models import Task, User
from web.app.runner.paths import task_run_dir

ARXIV_URL = "https://arxiv.org/abs/1706.03762"
NOT_READY_DETAIL = "任务尚未完成，暂无可阅读的成品"
ARTIFACT_MISSING_DETAIL = "成品文件缺失或已损坏，请重新提交任务"
TITLE = "Sample Paper: A Demonstration of the Pipeline"


# --------------------------------------------------------------------------
# 辅助
# --------------------------------------------------------------------------

def _register(client, username=None):
    username = username or f"art_{uuid.uuid4().hex[:10]}"
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


def _submit_fake_succeeded(client, username, token, *, source="arxiv"):
    """走完整假管线：返回 (task_id, run_dir)，产物真实落盘。"""
    if source == "arxiv":
        r = client.post("/api/tasks/from-url",
                        json={"source": ARXIV_URL}, headers=_h(token))
    else:
        raise NotImplementedError
    assert r.status_code in (200, 201), r.text
    tid = r.json()["id"]
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        d = client.get(f"/api/tasks/{tid}", headers=_h(token)).json()
        if d["status"] in ("succeeded", "failed", "cancelled"):
            break
        time.sleep(0.02)
    assert d["status"] == "succeeded", d
    uid = _user_id(username)
    return tid, task_run_dir(uid, tid)


_SLOT_COUNTER = iter(range(1, 1_000_000))


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
            active_slot=next(_SLOT_COUNTER) if status in ("queued", "running") else None,
        )
        db.add(task)
        db.commit()
        return task.id
    finally:
        db.close()


# --------------------------------------------------------------------------
# T013：parse_reader / read_output / resolve_image / safe_filename 纯函数
# --------------------------------------------------------------------------

def test_parse_reader_real_fixture_8_sections(make_client):
    from web.app.services.artifacts import parse_reader, read_output

    client = make_client(fake_mode=True)
    username, token = _register(client)
    _, run_dir = _submit_fake_succeeded(client, username, token)

    md = read_output(run_dir)
    reader = parse_reader(md, TITLE)

    assert reader["title"] == TITLE
    assert [s["slug"] for s in reader["sections"]] == [f"s{i}" for i in range(8)]
    headings = [s["heading"] for s in reader["sections"]]
    assert headings[0] == "0 · 30 秒摘要"
    assert headings[-1] == "附录（折叠区）"
    appendix = reader["sections"][-1]["content"]
    assert "<details" in appendix
    # T017：caption 为概览/架构类图（overview/pipeline）判 narrate，留主线、不进折叠区；
    # 图片仍拷贝到输出 images/，zip 测试在 test_fake_mode 中覆盖
    assert "images/sample.png" not in appendix
    assert (run_dir / "images" / "sample.png").exists()

    # 公式与行内 HTML 在解析后必须原样保留（交给前端 texmath/DOMPurify）
    math_md = (
        "# Math Title\n\n"
        "## 0 · 公式\n\n"
        "行内公式 $E = mc^2$ 与块级公式：\n\n"
        "$$\n\\int_0^1 x\\,dx = \\tfrac12\n$$\n"
    )
    math_reader = parse_reader(math_md, TITLE)
    math_content = math_reader["sections"][0]["content"]
    assert "$E = mc^2$" in math_content
    assert "\\int_0^1" in math_content


def test_parse_reader_seven_sections_without_appendix():
    from web.app.services.artifacts import parse_reader

    body = ["# Only Title", ""]
    for i in range(7):
        body += [f"## Chapter {i}", "", f"正文 {i}", ""]
    reader = parse_reader("\n".join(body), "fallback")
    assert reader["title"] == "Only Title"
    assert len(reader["sections"]) == 7
    assert [s["slug"] for s in reader["sections"]] == [f"s{i}" for i in range(7)]
    assert reader["sections"][3]["heading"] == "Chapter 3"
    assert reader["sections"][3]["content"] == f"正文 3"


def test_parse_reader_title_fallbacks():
    from web.app.services.artifacts import parse_reader

    md = "## s0 章\n\n正文"
    # 无一级标题 → 用 result_title
    reader = parse_reader(md, "结果标题")
    assert reader["title"] == "结果标题"
    # 两者皆无 → 调用方传 source_ref 兜底
    reader = parse_reader(md, "1706.03762")
    assert reader["title"] == "1706.03762"


def test_parse_reader_preamble_merges_into_first_section():
    from web.app.services.artifacts import parse_reader

    md = "# T\n\n前置导语行\n\n## 第一章\n\n正文"
    reader = parse_reader(md, "x")
    assert "前置导语行" in reader["sections"][0]["content"]


def test_read_output_and_parse_missing_or_empty(tmp_path):
    from web.app.services.artifacts import (
        ArtifactMissingError,
        parse_reader,
        read_output,
    )

    # 目录存在但 output.md 缺失
    with pytest.raises(ArtifactMissingError):
        read_output(tmp_path)

    (tmp_path / "output.md").write_text("", encoding="utf-8")
    with pytest.raises(ArtifactMissingError):
        read_output(tmp_path)

    # 有一级标题但 0 个 ## 章节
    (tmp_path / "output.md").write_text("# 只有标题\n无章节\n", encoding="utf-8")
    with pytest.raises(ArtifactMissingError):
        read_output(tmp_path)

    with pytest.raises(ArtifactMissingError):
        parse_reader("完全无标题结构", "fb")


def test_resolve_image_allows_whitelist_and_rejects_escapes(tmp_path):
    from web.app.services.artifacts import ImageNotFoundError, resolve_image

    images = tmp_path / "images"
    images.mkdir()
    (images / "a.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (images / "b.JPG").write_bytes(b"jpgbytes")
    (tmp_path / "outside.png").write_bytes(b"outside")

    assert resolve_image(images, "a.png").read_bytes() == b"\x89PNG\r\n\x1a\n"
    # 扩展名白名单大小写：大写后缀拒绝（只放小写白名单）
    with pytest.raises(ImageNotFoundError):
        resolve_image(images, "b.JPG")

    for bad in [
        "../outside.png",
        "a/b.png",
        "x.svg",
        "x.SVG",
        "x.txt",
        "has space.png",
        "missing.png",
        "..",
        "",
    ]:
        with pytest.raises(ImageNotFoundError):
            resolve_image(images, bad)


@pytest.mark.parametrize(
    "title,expected",
    [
        ("a/b\\c:d*e?f\"g<h>i|j", "abcdefghij"),
        ("CON", "paper-{tid}"),
        # Windows 保留名无视扩展名：PRN.txt 同样回退
        ("PRN.txt", "paper-{tid}"),
        ("   ", "paper-{tid}"),
        ("\u0000\u0001控制", "控制"),
        # 全角冒号 U+FF1A 不在 ASCII 非法字符集，原样保留
        ("中文标题：你好", "中文标题：你好"),
    ],
)
def test_safe_filename_basic(title, expected):
    from web.app.services.artifacts import safe_filename

    out = safe_filename(title, "01234567abcdef")
    if expected == "paper-{tid}":
        assert out == "paper-01234567"
    else:
        assert out == expected


def test_safe_filename_reserved_names_and_length():
    from web.app.services.artifacts import safe_filename

    tid = "abcdef0123456789"
    for reserved in ["CON", "PRN", "AUX", "NUL",
                     "COM1", "COM9", "LPT1", "LPT9",
                     "con.txt", "Prn", "nul.md"]:
        assert safe_filename(reserved, tid) == f"paper-{tid[:8]}"
    long_title = "字" * 200
    out = safe_filename(long_title, tid)
    assert len(out) == 80
    # 纯符号（净化后为空）→ 回退
    assert safe_filename("///??***", tid) == f"paper-{tid[:8]}"
    # 空白折叠
    assert safe_filename("a   b\tc", tid) == "a b c"
    # 首尾点/空格修剪
    assert safe_filename(" .abc. ", tid) == ".abc"


# --------------------------------------------------------------------------
# T012：GET /reader 端点门禁与内容
# --------------------------------------------------------------------------

def test_reader_endpoint_succeeded_200(make_client):
    client = make_client(fake_mode=True)
    username, token = _register(client)
    tid, _ = _submit_fake_succeeded(client, username, token)

    r = client.get(f"/api/tasks/{tid}/reader", headers=_h(token))
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["task_id"] == tid
    assert data["title"] == TITLE
    assert data["fake_mode"] is True
    assert len(data["sections"]) == 8
    assert data["total_chars"] > 0
    assert data["sections"][0]["slug"] == "s0"
    # 载荷只有契约字段，无 run_dir 等路径泄露
    assert set(data.keys()) == {
        "task_id", "title", "fake_mode", "total_chars", "sections",
    }
    for s in data["sections"]:
        assert set(s.keys()) == {"slug", "heading", "content"}


def test_reader_endpoint_status_gates(make_client):
    client = make_client(fake_mode=True)
    username, token = _register(client)
    uid = _user_id(username)

    for status in ("queued", "running", "failed", "cancelled"):
        overrides = {}
        if status == "failed":
            overrides = dict(failure_category="system",
                             failure_code="boom", failure_message="坏了")
        tid = _insert_task(uid, status, **overrides)
        r = client.get(f"/api/tasks/{tid}/reader", headers=_h(token))
        assert r.status_code == 409, (status, r.status_code, r.text)
        body = r.json()
        assert body == {"detail": NOT_READY_DETAIL, "code": "task_not_ready"}


def test_reader_endpoint_artifact_missing(make_client):
    client = make_client(fake_mode=True)
    username, token = _register(client)
    tid, run_dir = _submit_fake_succeeded(client, username, token)

    (run_dir / "output.md").unlink()
    r = client.get(f"/api/tasks/{tid}/reader", headers=_h(token))
    assert r.status_code == 409
    body = r.json()
    assert body == {"detail": ARTIFACT_MISSING_DETAIL, "code": "artifact_missing"}
    # 不含路径/堆栈特征
    assert "Traceback" not in r.text
    assert str(run_dir) not in r.text


def test_reader_endpoint_auth_and_ownership(make_client):
    client = make_client(fake_mode=True)
    username_a, token_a = _register(client)
    tid_a, _ = _submit_fake_succeeded(client, username_a, token_a)
    _, token_b = _register(client)

    assert client.get(f"/api/tasks/{tid_a}/reader").status_code == 401
    r = client.get(f"/api/tasks/{tid_a}/reader", headers=_h(token_b))
    assert r.status_code == 404
    assert r.json() == {"detail": "任务不存在"}
    r = client.get("/api/tasks/nonexistent-id/reader", headers=_h(token_a))
    assert r.status_code == 404


# --------------------------------------------------------------------------
# T012：图片端点
# --------------------------------------------------------------------------

def test_image_endpoint_serves_bytes(make_client):
    client = make_client(fake_mode=True)
    username, token = _register(client)
    tid, run_dir = _submit_fake_succeeded(client, username, token)
    raw = (run_dir / "images" / "sample.png").read_bytes()

    r = client.get(f"/api/tasks/{tid}/artifacts/images/sample.png",
                   headers=_h(token))
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "image/png"
    assert r.content == raw
    assert "no-store" in r.headers["cache-control"]
    # 不附带会泄露文件名/路径的 Content-Disposition
    assert "content-disposition" not in {k.lower() for k in r.headers}


@pytest.mark.parametrize(
    "name",
    ["../outside.png", "..%2Foutside.png", "%2E%2E%2Foutside.png",
     "a/b.png", "x.svg", "x.SVG", "x.txt", "has%20space.png", "missing.png"],
)
def test_image_endpoint_rejects_bad_names(make_client, name):
    client = make_client(fake_mode=True)
    username, token = _register(client)
    tid, run_dir = _submit_fake_succeeded(client, username, token)
    # images 目录之外确实存在同名文件，验证越界不可达
    (run_dir / "outside.png").write_bytes(b"outside")

    r = client.get(f"/api/tasks/{tid}/artifacts/images/{name}",
                   headers=_h(token))
    assert r.status_code == 404, (name, r.status_code)
    assert str(run_dir) not in r.text


def test_image_endpoint_gates(make_client):
    client = make_client(fake_mode=True)
    username, token_a = _register(client)
    tid_a, _ = _submit_fake_succeeded(client, username, token_a)
    _, token_b = _register(client)
    uid = _user_id(username)
    tid_q = _insert_task(uid, "queued")

    # 401
    assert client.get(
        f"/api/tasks/{tid_a}/artifacts/images/sample.png").status_code == 401
    # 404 越权 + 不存在
    assert client.get(
        f"/api/tasks/{tid_a}/artifacts/images/sample.png",
        headers=_h(token_b)).status_code == 404
    assert client.get(
        "/api/tasks/nope/artifacts/images/x.png",
        headers=_h(token_a)).status_code == 404
    # 409 任务未完成
    r = client.get(f"/api/tasks/{tid_q}/artifacts/images/x.png",
                   headers=_h(token_a))
    assert r.status_code == 409
    assert r.json()["code"] == "task_not_ready"


# --------------------------------------------------------------------------
# T018：zip 下载
# --------------------------------------------------------------------------

def test_zip_download_structure_and_bytes(make_client):
    client = make_client(fake_mode=True)
    username, token = _register(client)
    tid, run_dir = _submit_fake_succeeded(client, username, token)
    md_bytes = (run_dir / "output.md").read_bytes()
    png_bytes = (run_dir / "images" / "sample.png").read_bytes()

    r = client.get(f"/api/tasks/{tid}/artifact/download", headers=_h(token))
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/zip"
    cd = r.headers["content-disposition"]
    assert "filename=" in cd
    assert str(run_dir) not in cd

    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = zf.namelist()
    # 恰两条（无裸目录项）
    assert len(names) == 2, names
    base = safe_base_from(tid)
    assert f"{base}/{base}.md" in names
    assert f"{base}/images/sample.png" in names
    assert zf.read(f"{base}/{base}.md") == md_bytes
    assert zf.read(f"{base}/images/sample.png") == png_bytes
    for n in names:
        assert ".." not in n
        assert not n.startswith("/")
        assert ":" not in n
        assert "\\" not in n


def safe_base_from(tid):
    """zip base 由 safe_filename(result_title, tid) 决定。"""
    from web.app.services.artifacts import safe_filename

    db = SessionLocal()
    try:
        task = db.get(Task, tid)
        return safe_filename(task.result_title, tid)
    finally:
        db.close()


def test_zip_download_no_images(make_client):
    client = make_client(fake_mode=True)
    username, token = _register(client)
    uid = _user_id(username)
    tid = _insert_task(
        uid, "succeeded", progress=1.0, stage="assemble",
        result_title="Plain Old Task", result_section_count=7,
        result_total_chars=100,
    )
    run = task_run_dir(uid, tid)
    run.mkdir(parents=True, exist_ok=True)
    md = "# Plain Old Task\n\n" + "".join(
        f"## C{i}\n\n正文{i}\n" for i in range(7))
    (run / "output.md").write_text(md, encoding="utf-8", newline="")

    r = client.get(f"/api/tasks/{tid}/artifact/download", headers=_h(token))
    assert r.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = zf.namelist()
    assert names == ["Plain Old Task/Plain Old Task.md"]
    assert zf.read(names[0]).decode("utf-8") == md


def test_zip_download_dead_link_and_svg_excluded(make_client):
    client = make_client(fake_mode=True)
    username, token = _register(client)
    uid = _user_id(username)
    tid = _insert_task(
        uid, "succeeded", progress=1.0, stage="assemble",
        result_title="Dead Link Task", result_section_count=7,
        result_total_chars=100,
    )
    run = task_run_dir(uid, tid)
    (run / "images").mkdir(parents=True)
    (run / "images" / "ok.png").write_bytes(b"png")
    (run / "images" / "evil.svg").write_bytes(b"<svg></svg>")
    md = ("# Dead Link Task\n\n"
          + "".join(f"## C{i}\n\n正文\n" for i in range(7))
          + "![missing](images/missing.png)\n")
    (run / "output.md").write_text(md, encoding="utf-8")

    r = client.get(f"/api/tasks/{tid}/artifact/download", headers=_h(token))
    assert r.status_code == 200
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert any(n.endswith("images/ok.png") for n in names)
    assert not any("missing" in n for n in names)
    assert not any(n.endswith(".svg") for n in names)


def test_zip_download_unicode_filename_header(make_client):
    client = make_client(fake_mode=True)
    username, token = _register(client)
    uid = _user_id(username)
    tid = _insert_task(
        uid, "succeeded", progress=1.0, stage="assemble",
        result_title="深度学习综述：2026", result_section_count=7,
        result_total_chars=100,
    )
    run = task_run_dir(uid, tid)
    run.mkdir(parents=True, exist_ok=True)
    md = "# 深度学习综述：2026\n\n" + "".join(
        f"## C{i}\n\n正文\n" for i in range(7))
    (run / "output.md").write_text(md, encoding="utf-8")

    r = client.get(f"/api/tasks/{tid}/artifact/download", headers=_h(token))
    assert r.status_code == 200
    cd = r.headers["content-disposition"]
    # ASCII 回退文件名（非 ASCII 以 - 替代）
    assert re.search(r'filename="[\x21-\x7e -]+\.zip"', cd)
    # RFC 5987 UTF-8 百分号编码
    assert "filename*=UTF-8''" in cd
    assert "%E7%BB%BC%E8%BF%B0" in cd  # 综述
    assert str(run) not in cd


@pytest.mark.parametrize("status", ["queued", "running", "failed", "cancelled"])
def test_zip_download_gates(make_client, status):
    client = make_client(fake_mode=True)
    username, token = _register(client)
    uid = _user_id(username)
    overrides = {}
    if status == "failed":
        overrides = dict(failure_category="system",
                         failure_code="x", failure_message="坏")
    tid = _insert_task(uid, status, **overrides)
    r = client.get(f"/api/tasks/{tid}/artifact/download", headers=_h(token))
    assert r.status_code == 409
    assert r.json()["code"] == "task_not_ready"


def test_zip_download_401_404_and_missing(make_client):
    client = make_client(fake_mode=True)
    username_a, token_a = _register(client)
    tid_a, _ = _submit_fake_succeeded(client, username_a, token_a)
    _, token_b = _register(client)

    assert client.get(f"/api/tasks/{tid_a}/artifact/download").status_code == 401
    assert client.get(f"/api/tasks/{tid_a}/artifact/download",
                      headers=_h(token_b)).status_code == 404

    # artifact_missing：另跑一个成功任务后删除 output.md
    tid_c, run_c = _submit_fake_succeeded(client, username_a, token_a)
    (run_c / "output.md").unlink()
    r = client.get(f"/api/tasks/{tid_c}/artifact/download", headers=_h(token_a))
    assert r.status_code == 409
    assert r.json()["code"] == "artifact_missing"


# --------------------------------------------------------------------------
# T022：002 历史成功任务零迁移兼容
# 手工写"旧格式" output.md（7 个 ##、无附录、无 images 目录）
# --------------------------------------------------------------------------

def test_legacy_output_seven_sections_reader_and_zip(make_client):
    client = make_client(fake_mode=True)
    username, token = _register(client)
    uid = _user_id(username)
    tid = _insert_task(
        uid, "succeeded", progress=1.0, stage="assemble",
        result_title="Legacy Paper", result_section_count=7,
        result_total_chars=100,
    )
    run = task_run_dir(uid, tid)
    run.mkdir(parents=True, exist_ok=True)
    # 注意：刻意不创建 images/ 目录
    md = "# Legacy Paper\n\n" + "".join(
        f"## 旧章 {i}\n\n这是第 {i} 章正文。\n" for i in range(7)
    )
    (run / "output.md").write_text(md, encoding="utf-8", newline="")

    # reader：7 节、标题正确，无需任何数据迁移
    r = client.get(f"/api/tasks/{tid}/reader", headers=_h(token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["title"] == "Legacy Paper"
    assert [s["slug"] for s in body["sections"]] == [f"s{i}" for i in range(7)]
    assert body["sections"][3]["heading"] == "旧章 3"
    assert "第 3 章" in body["sections"][3]["content"]

    # 图片端点：无 images 目录，任意名一律 404
    ir = client.get(
        f"/api/tasks/{tid}/artifacts/images/sample.png", headers=_h(token)
    )
    assert ir.status_code == 404

    # zip：仅 1 条 .md，字节逐字一致，正常 200
    zr = client.get(f"/api/tasks/{tid}/artifact/download", headers=_h(token))
    assert zr.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(zr.content))
    assert zf.namelist() == ["Legacy Paper/Legacy Paper.md"]
    assert zf.read("Legacy Paper/Legacy Paper.md").decode("utf-8") == md
