"""T010：第三方密钥管理端到端（加密落库、掩码回显、归属、幂等删除）。"""

from __future__ import annotations

import uuid

import pytest

from web.app.db import SessionLocal
from web.app.models import Credential


def _register_and_token(client, username: str | None = None) -> tuple[str, str]:
    username = username or f"user_{uuid.uuid4().hex[:10]}"
    password = "password123"
    r = client.post("/api/auth/register", json={"username": username, "password": password})
    assert r.status_code == 201, r.text
    r = client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return username, r.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def alice(client) -> tuple[str, str]:
    return _register_and_token(client, "alice_cred")


def test_unauthenticated_all_endpoints(client):
    assert client.get("/api/credentials").status_code == 401
    assert client.put("/api/credentials/mineru", json={"secret": "x"}).status_code == 401
    assert client.delete("/api/credentials/mineru").status_code == 401


def test_initial_status_two_services(client, alice):
    _name, token = alice
    r = client.get("/api/credentials", headers=_auth(token))
    assert r.status_code == 200
    creds = r.json()["credentials"]
    assert [c["service"] for c in creds] == ["mineru", "deepseek"]
    assert all(c["configured"] is False and c["mask"] is None for c in creds)


def test_put_then_get_shows_mask_only(client, alice):
    _name, token = alice
    secret = "mineru-secret-key-1234567890ab"
    r = client.put("/api/credentials/mineru", json={"secret": secret}, headers=_auth(token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body == {"service": "mineru", "configured": True, "mask": "••••90ab"}
    assert "secret" not in body and "secret_encrypted" not in body

    # 库中密文不含明文子串
    db = SessionLocal()
    try:
        row = db.query(Credential).filter(Credential.service == "mineru").one()
        assert secret not in row.secret_encrypted
        assert row.mask == "••••90ab"
    finally:
        db.close()

    r = client.get("/api/credentials", headers=_auth(token))
    statuses = {c["service"]: c for c in r.json()["credentials"]}
    assert statuses["mineru"]["mask"] == "••••90ab"
    assert statuses["deepseek"]["configured"] is False
    assert all("secret" not in c and "secret_encrypted" not in c
               for c in r.json()["credentials"])


def test_overwrite_changes_mask(client, alice):
    _name, token = alice
    client.put("/api/credentials/mineru", json={"secret": "first-secret-1111aaaa"},
               headers=_auth(token))
    r = client.put("/api/credentials/mineru", json={"secret": "second-secret-2222bbbb"},
                   headers=_auth(token))
    assert r.status_code == 200
    assert r.json()["mask"] == "••••bbbb"

    db = SessionLocal()
    try:
        # upsert：仍只有一行
        assert db.query(Credential).filter(Credential.service == "mineru").count() == 1
    finally:
        db.close()


def test_delete_is_idempotent(client, alice):
    _name, token = alice
    client.put("/api/credentials/deepseek", json={"secret": "dk-0000111122223333"},
               headers=_auth(token))
    r = client.delete("/api/credentials/deepseek", headers=_auth(token))
    assert r.status_code == 200
    assert r.json() == {"service": "deepseek", "configured": False, "mask": None}
    # 重复删除幂等
    r = client.delete("/api/credentials/deepseek", headers=_auth(token))
    assert r.status_code == 200
    assert r.json()["configured"] is False


@pytest.mark.parametrize("bad_secret", ["", "   ", "\t\n"])
def test_blank_secret_422_without_echo(client, alice, bad_secret):
    _name, token = alice
    r = client.put("/api/credentials/mineru", json={"secret": bad_secret},
                   headers=_auth(token))
    assert r.status_code == 422
    text = r.text
    assert bad_secret.strip() not in text or not bad_secret.strip()
    # 脱敏处理器：错误体不含 input 回显
    assert "input" not in text


def test_invalid_service_path_422(client, alice):
    _name, token = alice
    # GET 仅有集合端点；带服务名的写/删路径非法值由 Literal 自动 422
    assert client.put("/api/credentials/google", json={"secret": "abcdefgh"},
                      headers=_auth(token)).status_code == 422
    assert client.delete("/api/credentials/google",
                         headers=_auth(token)).status_code == 422


def test_users_isolated(client, alice):
    _a_name, token_a = alice
    client.put("/api/credentials/mineru", json={"secret": "a-user-secret-xxxx1234"},
               headers=_auth(token_a))

    _b_name, token_b = _register_and_token(client, "bob_cred")
    r = client.get("/api/credentials", headers=_auth(token_b))
    assert all(c["configured"] is False for c in r.json()["credentials"])

    # B 删不到 A 的凭据
    r = client.delete("/api/credentials/mineru", headers=_auth(token_b))
    assert r.json()["configured"] is False
    db = SessionLocal()
    try:
        assert db.query(Credential).filter(Credential.service == "mineru").count() == 1
    finally:
        db.close()


def test_secret_too_long_422(client, alice):
    _name, token = alice
    r = client.put("/api/credentials/mineru", json={"secret": "x" * 201},
                   headers=_auth(token))
    assert r.status_code == 422

