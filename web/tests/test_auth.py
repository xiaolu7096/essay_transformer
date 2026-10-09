"""US2：用户注册测试（T017）。登录、me、logout 测试在后续任务顺序追加。"""

from __future__ import annotations


def _register(client, username: str, password: str):
    return client.post(
        "/api/auth/register",
        json={"username": username, "password": password},
    )


def test_register_success(client):
    resp = _register(client, "Alice_01", "correct horse battery")
    assert resp.status_code == 201
    body = resp.json()
    assert set(body.keys()) == {"id", "username", "created_at"}
    assert body["username"] == "Alice_01"
    assert isinstance(body["id"], str) and len(body["id"]) == 36
    # 响应体中绝不回显密码
    assert "correct horse battery" not in resp.text


def test_register_chinese_username_ok(client):
    resp = _register(client, "张三001", "another-strong-pw")
    assert resp.status_code == 201
    assert resp.json()["username"] == "张三001"


def test_register_duplicate_conflict(client):
    assert _register(client, "Alice_02", "correct horse battery").status_code == 201
    resp = _register(client, "Alice_02", "correct horse battery")
    assert resp.status_code == 409
    assert resp.json() == {"detail": "用户名已被占用"}


def test_register_username_case_insensitive(client):
    assert _register(client, "Alice_03", "correct horse battery").status_code == 201
    # Alice 与 alice 视为同一用户名
    resp = _register(client, "alice_03", "correct horse battery")
    assert resp.status_code == 409


def test_register_invalid_username(client):
    for bad in ["ab", "a" * 31, "   ", "has space", "bad-name"]:
        resp = _register(client, bad, "correct horse battery")
        assert resp.status_code == 422, f"用户名 {bad!r} 应被拒绝"


def test_register_invalid_password(client):
    for bad in ["short", "", "a" * 65]:
        resp = _register(client, "Alice_04", bad)
        assert resp.status_code == 422, f"密码 {bad!r} 应被拒绝"


# ---------------------------------------------------------------------------
# US3：登录（T022）
# ---------------------------------------------------------------------------

def _login(client, username: str, password: str):
    return client.post(
        "/api/auth/login",
        json={"username": username, "password": password},
    )


def test_login_success(client):
    assert _register(client, "Bob_01", "correct horse battery").status_code == 201
    resp = _login(client, "Bob_01", "correct horse battery")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"access_token", "token_type", "expires_at"}
    assert body["token_type"] == "bearer"
    assert isinstance(body["access_token"], str) and body["access_token"].count(".") == 2
    # expires_at 是合法的 ISO 时间字符串
    from datetime import datetime

    datetime.fromisoformat(body["expires_at"])


def test_login_unknown_user_unauthorized(client):
    resp = _login(client, "nobody_xx", "correct horse battery")
    assert resp.status_code == 401
    assert resp.json() == {"detail": "用户名或密码无效"}


def test_login_wrong_password_unauthorized(client):
    assert _register(client, "Bob_02", "correct horse battery").status_code == 201
    resp = _login(client, "Bob_02", "wrong password indeed")
    assert resp.status_code == 401
    assert resp.json() == {"detail": "用户名或密码无效"}


def test_login_empty_fields_422(client):
    resp = client.post("/api/auth/login", json={"username": "", "password": ""})
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# US3：GET /me 与五种非法令牌（T023）
# ---------------------------------------------------------------------------

ME_DETAIL = {"detail": "未授权或登录已过期"}


def _registered_token(client, username: str = "Carol_01", password: str = "correct horse battery"):
    assert _register(client, username, password).status_code == 201
    resp = _login(client, username, password)
    assert resp.status_code == 200
    return resp.json()["access_token"], username


def _me(client, token: str | None, header: str | None = None):
    if header is not None:
        return client.get("/api/auth/me", headers={"Authorization": header})
    if token is None:
        return client.get("/api/auth/me")
    return client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})


def test_me_with_valid_token(client):
    token, username = _registered_token(client)
    resp = _me(client, token)
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"id", "username", "created_at"}
    assert body["username"] == username


def test_me_without_token_401(client):
    resp = _me(client, None)
    assert resp.status_code == 401
    assert resp.json() == ME_DETAIL


def test_me_forged_signature_401(client):
    import jwt

    _registered_token(client)
    forged = jwt.encode({"sub": "x", "ver": 0}, "totally-wrong-secret", algorithm="HS256")
    resp = _me(client, forged)
    assert resp.status_code == 401
    assert resp.json() == ME_DETAIL


def test_me_expired_token_401(client):
    from datetime import datetime, timedelta, timezone

    import jwt

    from web.app.config import get_settings
    from web.app.models import User
    from web.app.db import SessionLocal

    _registered_token(client, username="Carol_exp")
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username_key == "carol_exp").one()
        uid, ver = user.id, user.token_version
    finally:
        db.close()

    past = datetime.now(timezone.utc) - timedelta(hours=1)
    expired = jwt.encode(
        {
            "sub": uid,
            "ver": ver,
            "iat": past - timedelta(minutes=10),
            "exp": past,
        },
        get_settings().jwt_secret,
        algorithm="HS256",
    )
    resp = _me(client, expired)
    assert resp.status_code == 401
    assert resp.json() == ME_DETAIL


def test_me_token_version_mismatch_401(client):
    import jwt

    from web.app.config import get_settings

    _registered_token(client, username="Carol_ver")
    # ver 与库中不符（库中为 0）
    bad = jwt.encode(
        {"sub": "any-id", "ver": 999, "iat": 0, "exp": 9_999_999_999},
        get_settings().jwt_secret,
        algorithm="HS256",
    )
    resp = _me(client, bad)
    assert resp.status_code == 401
    assert resp.json() == ME_DETAIL


def test_me_malformed_token_401(client):
    _registered_token(client, username="Carol_bad")
    for raw in ["not-a-jwt", "aaa.bbb.ccc", "BearerHasNoSpace.tok"]:
        resp = _me(client, raw)
        assert resp.status_code == 401, f"{raw!r} 应被拒绝"
        assert resp.json() == ME_DETAIL


# ---------------------------------------------------------------------------
# US3：重启后账户持久化（T030，SC-005）
# ---------------------------------------------------------------------------

def test_account_persists_after_app_restart(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from fastapi.testclient import TestClient

    from web.app.db import Base, get_db
    from web.app.main import create_app
    from web.app.models import User
    from web.app.security import hash_password

    db_url = f"sqlite:///{(tmp_path / 'persist.db').as_posix()}"

    # 第一次启动：建表并写入用户，随后销毁连接
    engine1 = create_engine(db_url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine1)
    Session1 = sessionmaker(engine1)
    with Session1() as db:
        db.add(
            User(
                username="Persist_01",
                username_key="persist_01",
                password_hash=hash_password("correct horse battery"),
            )
        )
        db.commit()
    engine1.dispose()

    # “重启”：全新引擎/应用连接同一文件型库
    engine2 = create_engine(db_url, connect_args={"check_same_thread": False})
    Session2 = sessionmaker(engine2)

    def _override_db():
        session = Session2()
        try:
            yield session
        finally:
            session.close()

    app = create_app()
    app.dependency_overrides[get_db] = _override_db
    with TestClient(app) as c:
        resp = c.post(
            "/api/auth/login",
            json={"username": "Persist_01", "password": "correct horse battery"},
        )
        assert resp.status_code == 200
        assert resp.json()["token_type"] == "bearer"
    engine2.dispose()


# ---------------------------------------------------------------------------
# US4：退出登录（T031）
# ---------------------------------------------------------------------------

def test_logout_ok_and_old_token_invalidated(client):
    token, _ = _registered_token(client, username="Dave_01")
    headers = {"Authorization": f"Bearer {token}"}

    resp = client.post("/api/auth/logout", headers=headers)
    assert resp.status_code == 200
    assert resp.json() == {"status": "logged_out"}

    # 旧令牌立即失效（token_version 已 +1）
    resp = client.get("/api/auth/me", headers=headers)
    assert resp.status_code == 401
    assert resp.json() == ME_DETAIL


def test_logout_without_token_401(client):
    resp = client.post("/api/auth/logout")
    assert resp.status_code == 401
    assert resp.json() == ME_DETAIL


def test_relogin_after_logout_works(client):
    token, _ = _registered_token(client, username="Dave_02")
    client.post("/api/auth/logout", headers={"Authorization": f"Bearer {token}"})

    # 重新登录拿到新令牌，可正常访问 /me
    resp = _login(client, "Dave_02", "correct horse battery")
    assert resp.status_code == 200
    new_token = resp.json()["access_token"]
    resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {new_token}"})
    assert resp.status_code == 200
    assert resp.json()["username"] == "Dave_02"


def test_concurrent_duplicate_register_one_wins_one_409(client):
    # T043：两个请求都通过先查后插的查重窗口，竞争失败者必须 409 而非 500
    import threading

    results = []
    barrier = threading.Barrier(2)

    def worker():
        barrier.wait()  # 尽量同时发出请求
        resp = client.post(
            "/api/auth/register",
            json={"username": "Racy_01", "password": "correct horse battery"},
        )
        results.append(resp.status_code)

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert sorted(results) == [201, 409]
    # 库里恰有一个账户
    from web.app.db import SessionLocal
    from web.app.models import User

    db = SessionLocal()
    try:
        assert db.query(User).filter(User.username_key == "racy_01").count() == 1
    finally:
        db.close()


def test_validation_errors_do_not_echo_password(client):
    # T041：超长密码触发 422，响应体不得回显提交的明文
    secret_value = "x" * 65
    resp = _register(client, "Alice_05", secret_value)
    assert resp.status_code == 422
    assert secret_value not in resp.text
    assert '"input"' not in resp.text

    resp = client.post(
        "/api/auth/login",
        json={"username": "Alice_05", "password": secret_value},
    )
    assert resp.status_code == 422
    assert secret_value not in resp.text


def test_token_rejected_after_configured_expiry(client, monkeypatch):
    # T035：等价于 WEB_TOKEN_EXPIRE_MINUTES 到期后受保护操作被统一 401
    _registered_token(client, username="Erin_exp")
    monkeypatch.setenv("WEB_TOKEN_EXPIRE_MINUTES", "-1")

    from web.app.db import SessionLocal
    from web.app.models import User
    from web.app.security import create_access_token

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username_key == "erin_exp").one()
        stale_token, _ = create_access_token(user.id, user.token_version)
    finally:
        db.close()

    resp = _me(client, stale_token)
    assert resp.status_code == 401
    assert resp.json() == ME_DETAIL
