"""Sign-up, log in, log out, delete, reset, and the session cookie."""
import sqlite3

from webutil import PASSWORD, sign_up


def test_sign_up_logs_in_with_a_safe_cookie(client):
    r = sign_up(client)
    assert r.status_code == 201
    assert r.json()["email"] == "student@example.com" and r.json()["plan"] == "student"
    cookie = r.headers["set-cookie"].lower()
    assert "mw_session=" in cookie and "httponly" in cookie and "samesite=strict" in cookie
    assert client.get("/account/me").status_code == 200


def test_sign_up_rules(client):
    assert sign_up(client, accept_terms=False).status_code == 400          # terms are required
    assert sign_up(client, accept_terms="yes").status_code == 400
    assert sign_up(client, email="not-an-email").status_code == 400
    assert sign_up(client, password="short").status_code == 400
    assert sign_up(client).status_code == 201
    assert sign_up(client, email="STUDENT@example.com").status_code == 409  # same email, any case


def test_password_is_stored_hashed(client, web):
    sign_up(client)
    stored = web.state.store.by_email("student@example.com")["password_hash"]
    assert stored.startswith("pbkdf2_sha256$") and PASSWORD not in stored


def test_log_in_and_out(client):
    sign_up(client)
    client.post("/auth/logout", json={})
    assert client.get("/account/me").status_code == 401
    assert client.post("/auth/login", json=dict(email="student@example.com", password="wrong password")).status_code == 401
    assert client.post("/auth/login", json=dict(email="nobody@example.com", password=PASSWORD)).status_code == 401
    assert client.post("/auth/login", json=dict(email="Student@Example.com", password=PASSWORD)).status_code == 200
    assert client.get("/account/me").status_code == 200


def test_logged_out_session_value_is_dead(client):
    r = sign_up(client)
    value = r.cookies["mw_session"]
    client.post("/auth/logout", json={})
    client.cookies.set("mw_session", value)
    assert client.get("/account/me").status_code == 401


def test_only_the_session_hash_is_stored(client, web):
    value = sign_up(client).cookies["mw_session"]
    with sqlite3.connect(web.state.store.path) as con:
        stored = [r[0] for r in con.execute("SELECT id_hash FROM sessions")]
    assert stored and value not in stored


def test_delete_account(client, web):
    sign_up(client)
    assert client.request("DELETE", "/account", json=dict(confirm_email="other@example.com")).status_code == 400
    r = client.request("DELETE", "/account", json=dict(confirm_email="student@example.com"))
    assert r.status_code == 200
    assert web.state.store.by_email("student@example.com") is None
    assert client.get("/account/me").status_code == 401
    with sqlite3.connect(web.state.store.path) as con:
        assert con.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0


def test_reset_says_it_needs_supabase(client):
    r = client.post("/auth/reset", json=dict(email="student@example.com"))
    assert r.status_code == 501 and "Supabase" in r.json()["error"]


def test_state_changes_need_same_origin_json(client):
    assert client.post("/auth/login", data="email=x").status_code == 415
    r = client.post("/auth/login", json={}, headers={"Origin": "http://evil.example"})
    assert r.status_code == 403
