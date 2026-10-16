"""Web server tests: a temporary user store, a test API token, and the real API app in-process
(read-only database), so nothing touches web/var/users.sqlite or the .env token."""
import pytest
from fastapi.testclient import TestClient

from api.auth import DevTokenAuth
from api.main import app as api_app
from web.auth import DevAuthProvider
from web.server import app as web_app
from web.users import UserStore
from webutil import TOKEN, RecordingTransport
from browser_harness import browser  # noqa: F401  (session fixture for the Edge page tests)


@pytest.fixture()
def web(tmp_path):
    saved = (web_app.state.store, web_app.state.provider, web_app.state.api_transport, api_app.state.authenticator)
    store = UserStore(tmp_path / "users.sqlite")
    web_app.state.store = store
    web_app.state.provider = DevAuthProvider(store, TOKEN)
    web_app.state.api_transport = RecordingTransport()
    api_app.state.authenticator = DevTokenAuth(TOKEN)
    yield web_app
    (web_app.state.store, web_app.state.provider, web_app.state.api_transport, api_app.state.authenticator) = saved


@pytest.fixture()
def client(web):
    return TestClient(web, base_url="http://127.0.0.1:8080", follow_redirects=False)
