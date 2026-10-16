"""A server running older code than the files on disk marks its responses, so the terminal can ask for a
restart (2026-10-15: an old web server's security policy blocked Plotly and no surface was drawn)."""
import time

import pytest

import codecheck
from webutil import sign_up


@pytest.fixture()
def started(monkeypatch):
    def set_start(t):
        monkeypatch.setattr(codecheck, "STARTED", t)
        codecheck._cache.clear()
    yield set_start
    codecheck._cache.clear()


def test_changed_since_start():
    assert codecheck.changed_since_start("api", ".", started=0.0)                 # every file is newer than 1970
    assert not codecheck.changed_since_start("api", ".", started=time.time() + 3600)


def test_fresh_servers_do_not_ask_for_a_restart(client, started):
    started(time.time() + 3600)
    sign_up(client)
    for path in ("/account/me", "/api/dates", "/login"):
        assert codecheck.HEADER not in client.get(path).headers, path


def test_old_servers_are_named_in_the_header(client, started):
    started(0.0)                      # both apps run in this process, so both count as started before the code
    sign_up(client)
    assert client.get("/api/dates").headers[codecheck.HEADER] == "api,web"       # the API's mark is passed on
    assert client.get("/account/me").headers[codecheck.HEADER] == "web"


def test_banner_text_is_in_the_page_code():
    import config
    js = (config.PROJECT_DIR / "web" / "static" / "js" / "api.js").read_text(encoding="utf-8")
    html = (config.PROJECT_DIR / "web" / "static" / "terminal.html").read_text(encoding="utf-8")
    assert 'id="stale-banner"' in html and "X-MW-Code-Changed" in js and "Restart both servers" in js
