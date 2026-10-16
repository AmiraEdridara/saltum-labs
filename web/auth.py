"""Login, pluggable. The web server talks to an AuthProvider; swap DevAuthProvider for a Supabase one later.

DevAuthProvider (local only):
- sign-up with email, password (PBKDF2-SHA256, salted) and the terms; accounts are confirmed at once
  (no mail server locally), and password reset is not available until Supabase;
- a session is a random value in an HttpOnly cookie; only its SHA-256 is stored;
- api_token_for() returns the dev token for the API proxy. The token is loaded from .env the same way
  as in api/auth.py and stays on the server: it is never sent to the browser, printed or logged.
"""
import datetime as dt
import hashlib
import hmac
import os
import re
import secrets
import sqlite3

from dotenv import load_dotenv

import config
from . import settings
from .users import now

EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AuthError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class AuthProvider:
    """The interface the server uses. A Supabase provider implements the same methods."""
    name = "base"

    def sign_up(self, email, password, accept_terms): raise NotImplementedError
    def log_in(self, email, password): raise NotImplementedError          # -> (user, session value)
    def log_out(self, session): raise NotImplementedError
    def current_user(self, session): raise NotImplementedError            # -> user or None
    def delete_user(self, user): raise NotImplementedError
    def request_password_reset(self, email): raise NotImplementedError
    def api_token_for(self, user): raise NotImplementedError              # bearer token for the API
    def service_token(self): raise NotImplementedError                     # the server's own token (public homepage data)


def hash_password(password, salt=None, rounds=200_000):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), rounds).hex()
    return f"pbkdf2_sha256${rounds}${salt}${digest}"


def check_password(password, stored):
    try:
        _, rounds, salt, digest = stored.split("$")
    except (AttributeError, ValueError):
        return False
    return hmac.compare_digest(hash_password(password, salt, int(rounds)).split("$")[3], digest)


def session_hash(value):
    return hashlib.sha256(value.encode()).hexdigest()


class DevAuthProvider(AuthProvider):
    name = "dev"

    def __init__(self, store, dev_token):
        self.store = store
        self._token = dev_token or None

    def __repr__(self):
        return "DevAuthProvider(<token hidden>)"

    @property
    def token_configured(self):
        return self._token is not None

    def sign_up(self, email, password, accept_terms):
        email = (email or "").strip().lower()
        if not EMAIL.match(email):
            raise AuthError(400, "Enter a valid email address.")
        if len(password or "") < settings.MIN_PASSWORD:
            raise AuthError(400, f"The password needs at least {settings.MIN_PASSWORD} characters.")
        if accept_terms is not True:
            raise AuthError(400, "Accept the terms to create an account: education only, no redistribution of data.")
        try:
            self.store.create(email, hash_password(password))
        except sqlite3.IntegrityError:
            raise AuthError(409, "An account with this email already exists.") from None
        return self.log_in(email, password)

    def log_in(self, email, password):
        user = self.store.by_email((email or "").strip().lower())
        if not user or not check_password(password or "", user["password_hash"]):
            raise AuthError(401, "Wrong email or password.")
        value = secrets.token_urlsafe(32)
        self.store.add_session(session_hash(value), user["id"], now() + dt.timedelta(hours=settings.SESSION_HOURS))
        return user, value

    def log_out(self, session):
        if session:
            self.store.drop_session(session_hash(session))

    def current_user(self, session):
        return self.store.session_user(session_hash(session)) if session else None

    def delete_user(self, user):
        self.store.delete(user["id"])

    def request_password_reset(self, email):
        raise AuthError(501, "Password reset needs email, which comes with Supabase Auth. Not available in the local dev login.")

    def api_token_for(self, user):
        return self._token

    def service_token(self):
        return self._token


def default_provider(store):
    load_dotenv(dotenv_path=config.DOTENV_PATH)
    return DevAuthProvider(store, os.getenv("MACRO_WIRE_DEV_TOKEN"))
