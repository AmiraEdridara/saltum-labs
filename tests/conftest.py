"""Tests run on a private copy of the real database, with a test token, never the .env token. The API answers 503 while
the daily update writes to the real file, so tests that read it directly fail whenever the update runs."""
import pathlib
import shutil
import tempfile
import time

import duckdb
import pytest
from fastapi.testclient import TestClient

import config as project

from api.auth import DevTokenAuth
from api.main import app

TEST_TOKEN = "test-token-not-the-real-one"
ENDPOINTS = ["/events?type=CPI&period=both", "/events/2026-09-11", "/events/index", "/events/next", "/surface/2026-09-11", "/dates",
             "/events/2026-09-11/skew", "/findings"]


def pytest_configure(config):
    real, lock = project.DUCKDB_PATH, project.LIVE_DIR / "update.lock"
    config.db_copy_folder = pathlib.Path(tempfile.mkdtemp(prefix="saltum-test-db-"))
    copy = config.db_copy_folder / real.name
    if not real.exists():                             # a clone without the private database: those tests skip
        project.DUCKDB_PATH = copy
        return
    end = time.time() + 300
    while True:                                       # not while the update is writing: the copy would be torn
        try:
            if lock.exists():
                raise OSError("the daily update is running")
            shutil.copyfile(real, copy)
            if lock.exists():
                raise OSError("the daily update started during the copy")
            duckdb.connect(str(copy), read_only=True).close()
            break
        except (OSError, duckdb.Error):
            if time.time() > end:
                raise
            time.sleep(2)
    project.DUCKDB_PATH = copy


def pytest_unconfigure(config):
    shutil.rmtree(config.db_copy_folder, ignore_errors=True)


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
