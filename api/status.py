"""data/all_dates_status.csv: one row per trading day since 2020 (drawn, flagged, failed and why)."""
import functools

import pandas as pd

import config

SOURCE = "data/all_dates_status.csv"


def path():
    """The daily update's live copy (data/live/) once it exists; the frozen file before that."""
    return config.LIVE_STATUS_CSV if config.LIVE_STATUS_CSV.exists() else config.ALL_DATES_STATUS_CSV


def table():
    p = path()
    return _table(str(p), p.stat().st_mtime)


@functools.lru_cache(maxsize=2)
def _table(p, mtime):
    s = pd.read_csv(p, dtype=str, keep_default_na=False)
    for c in ("drawn", "flagged", "failed"):
        s[c] = s[c] == "True"
    return s.set_index("date", drop=False)


def row(day):
    s = table()
    key = str(day)
    return s.loc[key].to_dict() if key in s.index else None


def why_missing(day):
    """None if the day is drawn; otherwise the reason it has no surface."""
    r = row(day)
    if r is None:
        import datetime as dt
        d = dt.date.fromisoformat(str(day)[:10])
        if d > config.V1_END and d.weekday() < 5:
            return "not processed yet (the daily update adds each trading day once FRED has its inputs)"
        return "not a trading day in the date list"
    if r["drawn"]:
        return None
    return f"{r['stage']} failed: {r['reason']}" if r["failed"] else "not drawn"
