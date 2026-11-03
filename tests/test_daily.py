"""The daily update, offline (no downloads): New York time and the clock changes, half days and holidays, the
append-only guard, provisional and final premiums, the frozen files, and the API's answer while the data updates."""
import datetime as dt
import hashlib
import re
import subprocess
import sys
from zoneinfo import ZoneInfo

import duckdb
import pandas as pd
import pytest

import config
import summarize
from daily import calendar as cal
from daily import guard, premium
from daily import run as daily
from tests.private_files import needs

UK, NY = ZoneInfo("Europe/London"), ZoneInfo("America/New_York")


# ------------------------------------------------------------ New York time, half days, holidays
@pytest.mark.parametrize("uk_time, want", [
    ("2026-10-15 21:45", "2026-10-15"),     # both on summer time: 16:45 New York, today is ready
    ("2026-10-15 21:30", "2026-10-14"),     # 16:30 New York: not yet
    ("2026-10-26 21:45", "2026-10-26"),     # UK back on GMT, US still on summer time: 17:45 New York
    ("2026-10-26 20:30", "2026-10-23"),     # 16:30 New York on a Monday: the Friday
    ("2027-03-15 20:50", "2027-03-15"),     # US on summer time, UK not yet: 16:50 New York
    ("2026-10-17 22:45", "2026-10-16"),     # Saturday: the Friday
])
def test_new_york_time_decides(uk_time, want):
    now = dt.datetime.fromisoformat(uk_time).replace(tzinfo=UK)
    assert cal.latest_closed_day(now) == dt.date.fromisoformat(want)


def test_half_day_closes_at_13_15(monkeypatch):
    monkeypatch.setattr(config, "NYSE_HOLIDAYS", {dt.date(2026, 11, 26)})          # Thanksgiving
    d = dt.date(2026, 11, 27)
    assert d in config.HALF_DAYS
    assert cal.latest_closed_day(dt.datetime(2026, 11, 27, 13, 50, tzinfo=NY)) == d
    assert cal.latest_closed_day(dt.datetime(2026, 11, 27, 13, 30, tzinfo=NY)) == dt.date(2026, 11, 25)


def test_holidays_are_skipped(monkeypatch):
    monkeypatch.setattr(config, "NYSE_HOLIDAYS", {dt.date(2026, 11, 26)})
    assert cal.next_trading_day(dt.date(2026, 11, 25)) == dt.date(2026, 11, 27)
    assert cal.previous_trading_day(dt.date(2026, 11, 27)) == dt.date(2026, 11, 25)
    assert cal.trading_days_after(dt.date(2026, 11, 24), dt.date(2026, 11, 30)) == \
        [dt.date(2026, 11, 25), dt.date(2026, 11, 27), dt.date(2026, 11, 30)]


# ------------------------------------------------------------ FRED inputs on a bond-market holiday
def test_veterans_day_surface_waits_only_for_the_next_yield():
    import vol_surface
    d = dt.date(2026, 11, 11)                         # Veterans Day: stocks trade, no bill yield is published
    def series(days, v):
        return pd.Series([v + i * 0.01 for i in range(len(days))], index=days)
    bonds = [dt.date(2026, 11, 9), dt.date(2026, 11, 10)]
    vix = series(bonds + [d], 17.0)
    bills = {s: series(bonds, 4.0) for s in config.BILL_SERIES}
    assert not daily.fred_ready(bills, vix, d)        # nothing on or after 11-11 yet: wait
    bills = {s: series(bonds + [dt.date(2026, 11, 12)], 4.0) for s in config.BILL_SERIES}
    assert daily.fred_ready(bills, vix, d)            # 11-12 is out: ready, with no 11-11 row ever
    assert [y for _, y in vol_surface.bill_curve_on(bills, d)] == [pytest.approx(4.01)] * len(config.BILL_SERIES)
    assert not daily.fred_ready(bills, series(bonds, 17.0), d)   # VIX publishes on 11-11, so it is still awaited


# ------------------------------------------------------------ the append-only guard
@pytest.fixture()
def small_db(tmp_path, monkeypatch):
    con = duckdb.connect(str(tmp_path / "t.duckdb"))
    con.execute("CREATE TABLE event_moves (date DATE, event_type VARCHAR, implied_move DOUBLE)")
    con.execute("INSERT INTO event_moves VALUES ('2026-10-08', 'NONE', 0.005), ('2026-10-09', 'NONE', 0.006)")
    con.execute(f"CREATE TABLE {config.DUCKDB_TABLE} (trade_date DATE, strike DOUBLE)")
    con.execute(f"INSERT INTO {config.DUCKDB_TABLE} VALUES ('2026-10-08', 7600)")
    yield con
    con.close()


def test_guard_accepts_appends_and_catches_changes(small_db):
    before = guard.snapshot(small_db)
    small_db.execute("INSERT INTO event_moves VALUES ('2026-10-12', 'NONE', 0.007)")
    guard.verify(small_db, before, {"event_moves": {dt.date(2026, 10, 12)}})          # an append is fine
    small_db.execute("UPDATE event_moves SET implied_move = 0.0061 WHERE date = '2026-10-09'")
    with pytest.raises(guard.ChecksumChanged, match="event_moves"):
        guard.verify(small_db, before, {"event_moves": {dt.date(2026, 10, 12)}})
    with pytest.raises(guard.ChecksumChanged, match="already has rows"):
        guard.assert_new(small_db, "event_moves", {dt.date(2026, 10, 9)})


def test_v1_checksums_cover_only_the_frozen_rows(small_db, tmp_path):
    path = tmp_path / "v1.csv"
    guard.save_v1(path, guard.v1_checksums(small_db))
    small_db.execute("INSERT INTO event_moves VALUES ('2026-10-12', 'NONE', 0.007)")
    small_db.execute(f"INSERT INTO {config.DUCKDB_TABLE} VALUES ('2026-10-09', 7650)")   # prices 10-12: live
    guard.verify_v1(small_db, path)
    small_db.execute("DELETE FROM event_moves WHERE date = '2026-10-08'")
    with pytest.raises(guard.ChecksumChanged, match="frozen v1 rows changed"):
        guard.verify_v1(small_db, path)


def test_the_provisional_table_is_not_guarded():
    assert config.PROVISIONAL_TABLE not in guard.TABLES and config.PROVISIONAL_TABLE in guard.NOT_GUARDED


# ------------------------------------------------------------ provisional and final premiums
def synthetic(days_after):
    """A trading-day list with an event at position 30 and `days_after` days after it, and its event moves."""
    start = dt.date(2026, 8, 3)
    days = [start + dt.timedelta(days=i) for i in range(120) if (start + dt.timedelta(days=i)).weekday() < 5][:31 + days_after]
    ev = days[30]
    dl = pd.DataFrame(dict(date=days, event_type=["CPI" if d == ev else "NONE" for d in days],
                           trade_date=[days[max(0, i - 1)] for i in range(len(days))], half_day=False))
    moves = pd.DataFrame(dict(date=pd.to_datetime(days), event_type=dl.event_type,
                              half_day=False, implied_move=[0.006 + 0.0001 * (i % 7) for i in range(len(days))]))
    moves.loc[moves.event_type == "CPI", "implied_move"] = 0.009
    return dl, moves, ev


def test_premium_is_provisional_until_the_window_is_complete(tmp_path):
    dl, moves, ev = synthetic(5)
    path = tmp_path / "dl.csv"
    dl.to_csv(path, index=False)
    out = premium.premiums(moves, path, [ev], set(dl.date))
    [(kind, row)] = out[ev]
    assert kind == "provisional" and row["window_days_used"] == 25 and row["window_days"] == 40
    with premium.live_date_list(path):
        frozen = summarize.event_premium(moves)
    assert row["premium"] == pytest.approx(frozen.premium.iloc[0])                     # the frozen method, as is


def test_premium_turns_final_once_20_days_after_are_in(tmp_path):
    dl, moves, ev = synthetic(20)
    path = tmp_path / "dl.csv"
    dl.to_csv(path, index=False)
    assert premium.premiums(moves, path, [ev], set(dl.date))[ev][0][0] == "final"
    late = set(dl.date) - {dl.date.iloc[-1]}                                           # the last day not processed yet
    assert premium.premiums(moves, path, [ev], late)[ev][0][0] == "provisional"


# ------------------------------------------------------------ the frozen files
def recorded_hashes():
    text = (config.PROJECT_DIR / "data" / "findings_v1.md").read_text(encoding="utf-8")
    out = {}
    for h, f in re.findall(r"^([0-9a-f]{64})  (\S+)$", text, re.M):
        out[f] = h                                    # later entries in the freeze record replace earlier ones
    return out


@needs("findings")
def test_frozen_files_match_the_freeze_record():
    rec = recorded_hashes()
    for f in daily.FROZEN_FILES:
        if f == "data/findings_v1.md":
            continue
        assert hashlib.sha256((config.PROJECT_DIR / f).read_bytes()).hexdigest() == rec[f], f


def test_the_update_never_writes_frozen_paths():
    src = (config.PROJECT_DIR / "daily" / "run.py").read_text(encoding="utf-8")
    assert "LIVE_FETCH_ERRORS_CSV" in src and "config.DATE_LIST_CSV, config.LIVE_DATE_LIST_CSV" in src
    for path in ("LIVE_DATE_LIST_CSV", "LIVE_STATUS_CSV", "LIVE_SKIPPED_CSV", "UPDATE_LOG", "UPDATE_STATUS", "V1_CHECKSUMS_CSV"):
        assert getattr(config, path).parent == config.LIVE_DIR


# ------------------------------------------------------------ the API while the data updates
@needs("database")
def test_api_says_data_is_updating_while_the_writer_holds_the_lock(client):
    from api import db
    if db._fallback is not None:                      # this test process's own read connection would block the writer
        db._fallback.close()
        db._fallback = None
    probe = subprocess.run([sys.executable, "-c", "import duckdb, sys; duckdb.connect(sys.argv[1]).close()",
                            str(config.DUCKDB_PATH)], capture_output=True, text=True)
    if probe.returncode:
        pytest.skip("another process holds the database open (a running API server started before this change?)")
    holder = subprocess.Popen([sys.executable, "-c", (
        "import duckdb, time, sys; c = duckdb.connect(sys.argv[1]); print('held', flush=True); time.sleep(12)"),
        str(config.DUCKDB_PATH)], stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "held"
        r = client.get("/events?type=CPI", headers={"Authorization": "Bearer test-token-not-the-real-one"})
        assert r.status_code == 503 and r.json()["error"] == "data updating"
        assert "updating" in r.json()["detail"] and r.headers["retry-after"] == "5"
    finally:
        holder.kill()
        holder.wait()
    r = client.get("/events?type=CPI", headers={"Authorization": "Bearer test-token-not-the-real-one"})
    assert r.status_code == 200                       # and back to normal once the writer is gone
