"""Auth, input validation, the response envelope, and no raw quotes."""
import pytest

from api.auth import DevTokenAuth
from conftest import ENDPOINTS, TEST_TOKEN

QUOTE_KEYS = {"bid", "ask", "mid", "price", "close", "open", "high", "low", "vol_spread", "straddle", "bid_size",
              "ask_size"}


@pytest.mark.parametrize("url", ENDPOINTS)
def test_no_token_is_refused(client, url):
    r = client.get(url)
    assert r.status_code == 401
    assert r.headers.get("www-authenticate") == "Bearer"


@pytest.mark.parametrize("headers", [{"Authorization": "Bearer wrong"}, {"Authorization": f"Basic {TEST_TOKEN}"},
                                     {"Authorization": "Bearer "}, {"Authorization": TEST_TOKEN}])
def test_bad_token_is_refused(client, headers):
    for url in ENDPOINTS:
        assert client.get(url, headers=headers).status_code == 401


def test_token_in_url_is_refused(client):
    assert client.get(f"/dates?token={TEST_TOKEN}").status_code == 401


def test_unconfigured_token_refuses_everything():
    assert DevTokenAuth(None).verify("") is None and DevTokenAuth("").verify("") is None
    assert "hidden" in repr(DevTokenAuth("secret")) and "secret" not in repr(DevTokenAuth("secret"))


def test_docs_are_off(client):
    for url in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(url).status_code in (401, 404)


@pytest.mark.parametrize("url", ["/events?type=xyz", "/events?period=2021", "/events/2026-13-01", "/events/20260911",
                                 "/events/index?exclude_flagged=maybe", "/events/index?include_ordinary=2",
                                 "/surface/yesterday", "/events/abc/skew", "/events/index?period=now"])
def test_bad_input_gets_400(get, url):
    j = get(url, status=400)
    assert j["error"] == "bad input" and j["detail"]


@pytest.mark.parametrize("url, text", [("/events/2020-01-14", "no same-day SPXW expiry"),
                                       ("/events/2026-09-14", "not a CPI, NFP or FOMC event day"),
                                       ("/surface/2022-04-12", "expiries within 14 days missing"),
                                       ("/surface/2026-10-10", "not a trading day"),
                                       ("/events/2026-09-14/skew", "not a CPI, NFP or FOMC event day")])
def test_missing_data_gets_404_with_reason(get, url, text):
    assert text in get(url, status=404)["detail"]


def _keys(v):
    if isinstance(v, dict):
        for k, x in v.items():
            yield k
            yield from _keys(x)
    elif isinstance(v, list):
        for x in v:
            yield from _keys(x)


@pytest.mark.parametrize("url", ENDPOINTS + ["/events/index?include_ordinary=true"])
def test_envelope_and_no_quotes(get, url):
    j = get(url)
    assert {"source", "n", "period", "params", "caveat", "data"} <= set(j)
    assert isinstance(j["n"], int) and j["source"] and "not advice" in j["caveat"]
    assert not QUOTE_KEYS & set(_keys(j["data"]))
