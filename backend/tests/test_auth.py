from __future__ import annotations

import dataclasses
import hashlib
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.settings import load_settings


ORIGIN = "http://testserver"
SIGNUP = {
    "name": "Demo Shopper",
    "email": "Shopper@example.com",
    "password": "correct-horse-demo-pass",
}


@pytest.fixture
def auth_setup(tmp_path: Path):
    settings = load_settings()
    auth_path = tmp_path / "auth.sqlite"
    auth = dataclasses.replace(settings.auth, database_path=str(auth_path))
    isolated_settings = dataclasses.replace(settings, auth=auth)
    app = create_app(isolated_settings)
    with TestClient(app, base_url=ORIGIN) as client:
        yield client, isolated_settings, auth_path


def _csrf(client: TestClient, origin: str = ORIGIN) -> str:
    response = client.get("/api/auth/csrf", headers={"Origin": origin})
    assert response.status_code == 200, response.text
    return response.json()["csrf_token"]


def _auth_post(client: TestClient, path: str, payload: dict, origin: str = ORIGIN):
    token = _csrf(client, origin)
    return client.post(
        path,
        json=payload,
        headers={"Origin": origin, "X-CSRF-Token": token},
    )


def test_signup_hashes_password_and_persists_session_across_app_restart(auth_setup):
    client, settings, auth_path = auth_setup
    response = _auth_post(client, "/api/auth/signup", SIGNUP)

    assert response.status_code == 201, response.text
    user = response.json()
    assert user["email"] == "shopper@example.com"
    assert "password" not in user and "password_hash" not in user
    cookie_header = response.headers["set-cookie"]
    assert "truthcart_session=" in cookie_header
    assert "HttpOnly" in cookie_header
    assert "SameSite=lax" in cookie_header
    assert "Max-Age=604800" in cookie_header
    assert "Secure" not in cookie_header  # explicitly local HTTP

    raw_token = client.cookies.get("truthcart_session")
    assert raw_token
    conn = sqlite3.connect(auth_path)
    try:
        stored_hash, email = conn.execute("SELECT password_hash, email FROM users").fetchone()
        token_hashes = [row[0] for row in conn.execute("SELECT token_hash FROM sessions")]
    finally:
        conn.close()
    assert email == "shopper@example.com"
    assert stored_hash != SIGNUP["password"]
    assert stored_hash.startswith("$argon2id$")
    assert hashlib.sha256(raw_token.encode("utf-8")).hexdigest() in token_hashes
    assert raw_token not in token_hashes
    assert raw_token.encode("utf-8") not in auth_path.read_bytes()
    assert SIGNUP["password"].encode("utf-8") not in auth_path.read_bytes()

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json() == user

    # A new application instance sees the same SQLite session from the cookie.
    restarted_app = create_app(settings)
    with TestClient(restarted_app, base_url=ORIGIN, cookies={"truthcart_session": raw_token}) as refreshed:
        restored = refreshed.get("/api/auth/me")
    assert restored.status_code == 200
    assert restored.json() == user


def test_duplicate_normalized_email_and_generic_login_errors(auth_setup):
    client, _, _ = auth_setup
    created = _auth_post(client, "/api/auth/signup", SIGNUP)
    assert created.status_code == 201

    duplicate = _auth_post(
        client,
        "/api/auth/signup",
        {**SIGNUP, "email": "  shopper@EXAMPLE.com  "},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "ACCOUNT_NOT_CREATED"

    wrong_password = _auth_post(
        client,
        "/api/auth/login",
        {"email": "shopper@example.com", "password": "incorrect-password"},
    )
    unknown_email = _auth_post(
        client,
        "/api/auth/login",
        {"email": "unknown@example.com", "password": "incorrect-password"},
    )
    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json()["error"]["code"] == unknown_email.json()["error"]["code"]
    assert wrong_password.json()["error"]["message"] == unknown_email.json()["error"]["message"]
    assert wrong_password.json()["error"]["code"] == "INVALID_CREDENTIALS"
    assert wrong_password.json()["error"]["message"] == "Email or password is incorrect."


def test_successful_login_restores_account_and_rotates_session(auth_setup):
    client, settings, _ = auth_setup
    created = _auth_post(client, "/api/auth/signup", SIGNUP)
    assert created.status_code == 201
    original_token = client.cookies.get("truthcart_session")

    logged_in = _auth_post(
        client,
        "/api/auth/login",
        {"email": " SHOPPER@example.com ", "password": SIGNUP["password"]},
    )
    assert logged_in.status_code == 200, logged_in.text
    assert logged_in.json() == created.json()
    new_token = client.cookies.get("truthcart_session")
    assert new_token and new_token != original_token
    with TestClient(create_app(settings), base_url=ORIGIN, cookies={"truthcart_session": new_token}) as refreshed:
        assert refreshed.get("/api/auth/me").json() == created.json()
    with TestClient(create_app(settings), base_url=ORIGIN, cookies={"truthcart_session": original_token}) as old_session:
        assert old_session.get("/api/auth/me").status_code == 401


def test_me_requires_a_valid_session(auth_setup):
    client, _, _ = auth_setup
    response = client.get("/api/auth/me")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_REQUIRED"


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "Demo", "email": "not-an-email", "password": "correct-horse-demo-pass"},
        {"name": "Demo", "email": "ok@example.com", "password": "short"},
        {"name": "   ", "email": "ok@example.com", "password": "correct-horse-demo-pass"},
    ],
)
def test_malformed_signup_inputs_are_rejected(auth_setup, payload):
    client, _, _ = auth_setup
    response = _auth_post(client, "/api/auth/signup", payload)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


@pytest.mark.parametrize(
    "payload",
    [
        {"email": "not-an-email", "password": "correct-horse-demo-pass"},
        {"email": "shopper@example.com", "password": ""},
        {"email": "shopper@example.com", "password": "x" * 129},
    ],
)
def test_malformed_login_inputs_are_rejected(auth_setup, payload):
    client, _, _ = auth_setup
    response = _auth_post(client, "/api/auth/login", payload)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_csrf_origin_cookie_header_and_single_use_are_enforced(auth_setup):
    client, _, auth_path = auth_setup
    assert client.get("/api/auth/csrf").status_code == 403  # no Origin
    same_origin_fetch = client.get("/api/auth/csrf", headers={"Sec-Fetch-Site": "same-origin"})
    assert same_origin_fetch.status_code == 200
    assert same_origin_fetch.json()["csrf_token"]
    same_origin_referer = client.get("/api/auth/csrf", headers={"Referer": f"{ORIGIN}/"})
    assert same_origin_referer.status_code == 200
    token = _csrf(client)
    csrf_cookie = client.cookies.get("truthcart_csrf")
    assert csrf_cookie == token
    csrf_cookie_header = next(value for value in client.cookies.jar if value.name == "truthcart_csrf")
    assert csrf_cookie_header.has_nonstandard_attr("HttpOnly")

    cross_site = client.post(
        "/api/auth/signup",
        json=SIGNUP,
        headers={"Origin": "https://attacker.example", "X-CSRF-Token": token},
    )
    assert cross_site.status_code == 403
    mismatch = client.post(
        "/api/auth/signup",
        json=SIGNUP,
        headers={"Origin": ORIGIN, "X-CSRF-Token": "different"},
    )
    assert mismatch.status_code == 403

    accepted = client.post(
        "/api/auth/signup",
        json=SIGNUP,
        headers={"Origin": ORIGIN, "X-CSRF-Token": token},
    )
    assert accepted.status_code == 201

    login_csrf = _csrf(client)
    login_without_header = client.post(
        "/api/auth/login",
        json={"email": "shopper@example.com", "password": SIGNUP["password"]},
        headers={"Origin": ORIGIN},
    )
    assert login_without_header.status_code == 403
    assert client.cookies.get("truthcart_csrf") == login_csrf

    logout_csrf = _csrf(client)
    logout_without_header = client.post("/api/auth/logout", headers={"Origin": ORIGIN})
    assert logout_without_header.status_code == 403
    assert client.cookies.get("truthcart_csrf") == logout_csrf

    replay = client.post(
        "/api/auth/login",
        json={"email": "shopper@example.com", "password": SIGNUP["password"]},
        headers={"Origin": ORIGIN, "X-CSRF-Token": token},
    )
    assert replay.status_code == 403
    conn = sqlite3.connect(auth_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
    finally:
        conn.close()


def test_logout_invalidates_session(auth_setup):
    client, settings, _ = auth_setup
    signed_up = _auth_post(client, "/api/auth/signup", SIGNUP)
    assert signed_up.status_code == 201
    old_token = client.cookies.get("truthcart_session")

    logout = client.post(
        "/api/auth/logout",
        headers={"Origin": ORIGIN, "X-CSRF-Token": _csrf(client)},
    )
    assert logout.status_code == 200
    assert logout.json() == {"logged_out": True}
    assert "Max-Age=0" in logout.headers["set-cookie"]
    with TestClient(create_app(settings), base_url=ORIGIN, cookies={"truthcart_session": old_token}) as logged_out:
        assert logged_out.get("/api/auth/me").status_code == 401

def test_expired_session_is_rejected(auth_setup):
    client, settings, auth_path = auth_setup
    second = _auth_post(client, "/api/auth/signup", {**SIGNUP, "email": "expired@example.com"})
    assert second.status_code == 201
    expired_token = client.cookies.get("truthcart_session")
    conn = sqlite3.connect(auth_path)
    try:
        conn.execute("UPDATE sessions SET expires_at = 0")
        conn.commit()
    finally:
        conn.close()
    assert client.get("/api/auth/me").status_code == 401
    with TestClient(create_app(settings), base_url=ORIGIN, cookies={"truthcart_session": expired_token}) as expired:
        assert expired.get("/api/auth/me").status_code == 401


def test_https_requests_receive_secure_auth_cookies(auth_setup):
    _, settings, auth_path = auth_setup
    https_app = create_app(settings)
    with TestClient(https_app, base_url="https://testserver") as secure_client:
        response = _auth_post(
            secure_client,
            "/api/auth/signup",
            {**SIGNUP, "email": "secure@example.com"},
            origin="https://testserver",
        )
    assert response.status_code == 201
    assert "Secure" in response.headers["set-cookie"]
    assert auth_path.exists()


def test_guest_catalogue_shopping_remains_available_without_auth(auth_setup):
    client, _, _ = auth_setup
    response = client.post(
        "/api/chat",
        json={"session_id": "auth-guest-shopping", "message": "Show me black shoes under ₹2000"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["decision"] == "ANSWER"


def test_auth_schema_initialization_does_not_change_catalogue_database(auth_setup):
    _, settings, auth_path = auth_setup
    catalogue_path = settings.base_dir / settings.app.paths["sqlite_db"]
    before = catalogue_path.read_bytes()
    conn = sqlite3.connect(catalogue_path)
    try:
        before_count = conn.execute("SELECT COUNT(*) FROM products").fetchone()[0]
    finally:
        conn.close()

    create_app(settings)

    assert auth_path.exists()
    conn = sqlite3.connect(auth_path)
    try:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    finally:
        conn.close()
    assert {"users", "sessions", "csrf_tokens"}.issubset(tables)
    assert catalogue_path.read_bytes() == before
    conn = sqlite3.connect(catalogue_path)
    try:
        assert conn.execute("SELECT COUNT(*) FROM products").fetchone()[0] == before_count
    finally:
        conn.close()
