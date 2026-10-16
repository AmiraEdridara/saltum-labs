"""Tests run against the real database (read-only) with a test token, never the .env token."""
import pytest
from fastapi.testclient import TestClient

from api.auth import DevTokenAuth
from api.main import app

TEST_TOKEN = "test-token-not-the-real-one"
ENDPOINTS = ["/events?type=CPI&period=both", "/events/2026-09-11", "/events/index", "/events/next", "/surface/2026-09-11", "/dates",
             "/events/2026-09-11/skew", "/findings"]


@pytest.fixture(scope="session")
def client():
    saved = app.state.authenticator
    app.state.authenticator = DevTokenAuth(TEST_TOKEN)
    yield TestClient(app)
    app.state.authenticator = saved


@pytest.fixture(scope="session")
def get(client):
    def _get(url, status=200):
        r = client.get(url, headers={"Authorization": f"Bearer {TEST_TOKEN}"})
        assert r.status_code == status, (url, r.status_code, r.text[:300])
        return r.json()
    return _get
