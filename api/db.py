"""Read-only DuckDB access, one short connection per request, so the daily update (daily_update.py) can take its
brief write lock between requests. If the database is being written, a request retries for a moment and then gets
a clear "data is updating" answer (503) instead of an error."""
import contextvars
import time

import duckdb

import config

RETRY_SECONDS = 4
_current = contextvars.ContextVar("duckdb_connection", default=None)
_fallback = None                  # outside a request (scripts, tests): one shared read-only connection


class DataUpdating(Exception):
    pass


def _open():
    end = time.time() + RETRY_SECONDS
    while True:
        try:
            return duckdb.connect(str(config.DUCKDB_PATH), read_only=True)
        except duckdb.IOException:
            if time.time() > end:
                raise DataUpdating("The data is updating. Try again in a moment.") from None
            time.sleep(0.25)


def begin_request():
    return _current.set([])


def end_request(token):
    for con in _current.get() or []:
        try:
            con.close()
        except duckdb.Error:
            pass
    _current.reset(token)


def cursor():
    global _fallback
    held = _current.get()
    if held is None:                                   # not inside a request
        if _fallback is None:
            _fallback = _open()
        return _fallback.cursor()
    if not held:
        held.append(_open())
    return held[0].cursor()
