"""Authentication. Every endpoint depends on `require_user`; a request without a valid token gets 401.

Pluggable: `app.state.authenticator` is any object with `verify(token) -> user id or None`.
- Now: DevTokenAuth, a local dev token. MACRO_WIRE_DEV_TOKEN is loaded from .env the same way the
  thetadata library loads THETADATA_API_KEY (load_dotenv, then os.getenv).
- Later: a Supabase JWT authenticator with the same verify() method.
The token is read from the Authorization: Bearer header only (never the URL, which servers log),
compared in constant time, and never printed, logged or returned.
"""
import hmac
import os

from dotenv import load_dotenv
from fastapi import HTTPException, Request

import config


class DevTokenAuth:
    def __init__(self, token):
        self._token = token or None

    @property
    def configured(self):
        return self._token is not None

    def verify(self, token):
        if self._token and token and hmac.compare_digest(token.encode(), self._token.encode()):
            return "dev"
        return None

    def __repr__(self):
        return "DevTokenAuth(<hidden>)"


def default_authenticator():
    load_dotenv(dotenv_path=config.DOTENV_PATH)
    return DevTokenAuth(os.getenv("MACRO_WIRE_DEV_TOKEN"))


def require_user(request: Request):
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    user = request.app.state.authenticator.verify(token.strip()) if scheme.lower() == "bearer" else None
    if user is None:
        raise HTTPException(401, "missing or invalid token", headers={"WWW-Authenticate": "Bearer"})
    return user
