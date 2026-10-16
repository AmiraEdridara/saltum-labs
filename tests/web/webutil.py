"""Shared helpers for the web tests."""
import httpx

from api.main import app as api_app

TOKEN = "web-test-token-not-the-real-one"
PASSWORD = "correct horse battery"


class RecordingTransport(httpx.AsyncBaseTransport):
    """Forwards to the real API app and records what the proxy sent."""
    def __init__(self):
        self.inner = httpx.ASGITransport(app=api_app)
        self.requests = []

    async def handle_async_request(self, request):
        self.requests.append(request)
        return await self.inner.handle_async_request(request)


def sign_up(client, email="student@example.com", password=PASSWORD, accept_terms=True):
    return client.post("/auth/signup", json=dict(email=email, password=password, accept_terms=accept_terms))
