"""Per-user plan, tutor message counters, layout settings; pages, redirects and static files."""
import datetime as dt
import sqlite3

import pytest

from webutil import sign_up
from web import settings


def test_me_has_plan_counters_and_limits(client):
    sign_up(client)
    me = client.get("/account/me").json()
    assert me["plan"] == "student" and me["provider"] == "dev"
    assert me["tutor_msgs_today"] == 0 and me["tutor_msgs_month"] == 0
    assert me["limits"] == dict(student_daily=7, tutor_plus_monthly=250, enforced=False)
    assert "password_hash" not in me


def test_plan_is_student_or_tutor_plus(client, web):
    sign_up(client)
    uid = web.state.store.by_email("student@example.com")["id"]
    web.state.store.set_plan(uid, "tutor_plus")
    assert client.get("/account/me").json()["plan"] == "tutor_plus"
    with pytest.raises(sqlite3.IntegrityError):
        web.state.store.set_plan(uid, "gold")
    assert settings.PLANS == ("student", "tutor_plus")


def test_tutor_counters_restart_each_day_and_month(client, web):
    sign_up(client)
    store, uid = web.state.store, web.state.store.by_email("student@example.com")["id"]
    t = dt.datetime(2026, 10, 30, 12, tzinfo=dt.timezone.utc)
    for _ in range(3):
        u = store.count_tutor_message(uid, t)
    assert (u["tutor_msgs_today"], u["tutor_msgs_month"]) == (3, 3)
    u = store.count_tutor_message(uid, t + dt.timedelta(days=1))           # new day, same month
    assert (u["tutor_msgs_today"], u["tutor_msgs_month"]) == (1, 4)
    u = store.count_tutor_message(uid, t + dt.timedelta(days=2))           # 2026-11-01: new month
    assert (u["tutor_msgs_today"], u["tutor_msgs_month"]) == (1, 1) and u["tutor_month"] == "2026-11"


def test_layout_is_remembered(client):
    sign_up(client)
    assert client.get("/account/me").json()["layout"] == {}
    assert client.put("/account/layout", json=dict(split=True, tutor=False, other="x")).json() == dict(split=True, tutor=False)
    assert client.get("/account/me").json()["layout"] == dict(split=True, tutor=False)


def test_terminal_needs_login_and_keeps_the_view(client):
    view = "/terminal?a=event-index&a.type=CPI&split=1&b=vol-surface&b.d1=2026-09-10&b.d2=2026-09-11&active=b"
    r = client.get(view)
    assert r.status_code == 303 and r.headers["location"].startswith("/login?next=%2Fterminal%3Fa%3Devent-index")
    sign_up(client)
    r = client.get(view)
    assert r.status_code == 200 and 'id="pane-b"' in r.text
    assert client.get("/").status_code == 200                # the homepage, for logged-in users too (Page 6)


def test_pages_static_and_headers(client):
    r = client.get("/login")
    assert r.status_code == 200 and "Create a free account" in r.text
    assert "default-src 'self'" in r.headers["content-security-policy"]
    for f in ("css/terminal.css", "css/fonts.css", "js/app.js", "js/state.js", "js/pages.js", "js/api.js",
              "js/login.js", "fonts/Newsreader.woff2", "fonts/Inter.woff2", "fonts/IBMPlexMono-400.woff2"):
        assert client.get(f"/static/{f}").status_code == 200, f
    assert client.get("/docs").status_code == 404
    css = client.get("/static/css/terminal.css").text
    assert "#1f3a9e" in css.lower() and "max-width: 720px" in css


def test_tutor_width_saved_with_the_layout(client):
    sign_up(client)
    client.put("/account/layout", json=dict(split=True, tutor=True))
    assert client.put("/account/layout", json=dict(tutor_width=430)).json() == dict(split=True, tutor=True, tutor_width=430)
    for bad in (100, 5000, "430", 430.5, True, None):                       # out of range or not a whole number
        client.put("/account/layout", json=dict(tutor_width=bad))
        assert client.get("/account/me").json()["layout"]["tutor_width"] == 430
    assert client.put("/account/layout", json=dict(split=False)).json() == dict(split=False, tutor=True, tutor_width=430)


def test_update_status_route(client, tmp_path, monkeypatch):
    import json
    import config
    sign_up(client)
    monkeypatch.setattr(config, "UPDATE_STATUS", tmp_path / "missing.json")
    assert client.get("/status/update").json() == {"configured": False}
    p = tmp_path / "update_status.json"
    p.write_text(json.dumps(dict(ok=False, error="ConnectionError: FRED did not answer", data_to="2026-10-15",
                                 last_failure="2026-10-16T22:46:00", extra="not passed on")), encoding="utf-8")
    monkeypatch.setattr(config, "UPDATE_STATUS", p)
    s = client.get("/status/update").json()
    assert s["configured"] and s["ok"] is False and s["error"].startswith("ConnectionError") and "extra" not in s
    assert s["v1_end"] == "2026-10-09"
    client.post("/auth/logout", json={})
    assert client.get("/status/update").status_code == 401
