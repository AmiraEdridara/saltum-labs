"""The /api proxy: refuses logged-out requests, adds the token server-side, and never shows it."""
import pytest

from webutil import TOKEN, sign_up

API_URLS = ["/api/dates", "/api/events?type=CPI&period=both", "/api/events/2026-09-11", "/api/events/index",
            "/api/events/2026-09-11/skew", "/api/surface/2026-09-11", "/api/findings"]


@pytest.mark.parametrize("url", API_URLS)
def test_logged_out_request_is_refused_before_the_api(client, web, url):
    assert client.get(url).status_code == 401
    assert web.state.api_transport.requests == []


@pytest.mark.parametrize("url", API_URLS)
def test_logged_in_request_reaches_the_api_with_the_token(client, web, url):
    sign_up(client)
    r = client.get(url)
    assert r.status_code == 200, r.text[:200]
    assert r.json()["source"] and isinstance(r.json()["n"], int)
    sent = web.state.api_transport.requests[-1]
    assert sent.headers["authorization"] == f"Bearer {TOKEN}"
    assert sent.url.host == "127.0.0.1" and sent.url.port == 8000
    assert TOKEN not in r.text and TOKEN not in str(r.headers)            # the browser never sees it


def test_query_and_errors_pass_through(client):
    sign_up(client)
    assert client.get("/api/events?type=CPI&period=2023-06..now").json()["data"]["summaries"][0]["n"] == 39
    assert client.get("/api/events?type=xyz").status_code == 400
    assert client.get("/api/surface/2022-04-12").status_code == 404


def test_only_api_endpoints_are_forwarded(client, web):
    sign_up(client)
    for url in ("/api/", "/api/vol_surface", "/api/events/2026-09-11/skew/extra", "/api/docs", "/api/openapi.json"):
        assert client.get(url).status_code == 404, url
    assert client.post("/api/dates", json={}).status_code == 405
    assert web.state.api_transport.requests == []


def test_api_token_refused_is_not_reported_as_logout(client, web):
    from api.auth import DevTokenAuth
    from api.main import app as api_app
    sign_up(client)
    api_app.state.authenticator = DevTokenAuth("a-different-token")
    r = client.get("/api/dates")
    assert r.status_code == 502 and "MACRO_WIRE_DEV_TOKEN" in r.json()["error"]


def test_api_down_gives_a_clear_502(client, web):
    import httpx

    class Down(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            raise httpx.ConnectError("refused")

    sign_up(client)
    web.state.api_transport = Down()
    r = client.get("/api/dates")
    assert r.status_code == 502 and "not reachable" in r.json()["error"]
