"""GET /events/next: the right next date from event_calendar.csv, history from existing tables only,
and never an invented or look-ahead priced move."""
import datetime as dt

import pandas as pd
import pytest

import config
from api import db, next_event as ne
from test_events import PERIODS, S3

CAL = pd.read_csv(config.EVENT_CALENDAR, dtype=str)


def nxt(today, type_="all"):
    return ne.next_event(db.cursor(), type_, today)


def expected_next(today, t):
    up = sorted(d for d, e in zip(CAL.date, CAL.event_type) if e == t and d >= today.isoformat())
    return up[0] if up else None


@pytest.mark.parametrize("today", [dt.date(2026, 10, 15), dt.date(2026, 9, 10), dt.date(2026, 9, 11),
                                   dt.date(2026, 10, 3), dt.date(2027, 12, 9), dt.date(2027, 12, 11)])
def test_next_date_per_type_comes_from_the_calendar(today):
    d = nxt(today)["data"]
    for t in ("CPI", "NFP", "FOMC"):
        assert d["by_type"][t]["next"] == expected_next(today, t), (today, t)
    dated = [e["next"] for e in d["by_type"].values() if e["next"]]
    assert (d["soonest"] or {}).get("date") == (min(dated) if dated else None)


def test_today_has_the_added_calendar_dates():
    d = nxt(dt.date(2026, 10, 15))["data"]
    assert {t: e["next"] for t, e in d["by_type"].items()} == {"CPI": "2026-11-10", "NFP": "2026-11-06", "FOMC": "2026-10-28"}
    assert d["soonest"] == dict(date="2026-10-28", event_types=["FOMC"], days_away=13)


def test_future_event_is_not_priced_yet():
    for t, e in nxt(dt.date(2026, 10, 15))["data"]["by_type"].items():
        assert e["priced_now"] is None and e["priced_note"].startswith("not priced yet"), t


def test_priced_now_is_the_stored_implied_move_and_nothing_else(get):
    e = nxt(dt.date(2026, 9, 10))["data"]["by_type"]["CPI"]
    stored = get("/events/2026-09-11")["data"]
    assert e["priced_now"] == dict(implied_move_pct=stored["implied_move_pct"], trade_date="2026-09-10", source="event_moves")
    assert "actual" not in str(e).lower()


def test_no_look_ahead():
    # On 2026-09-10 the 2026-10-01 snapshot behind the next NFP did not exist yet.
    by = nxt(dt.date(2026, 9, 10))["data"]["by_type"]
    assert by["NFP"]["next"] == "2026-10-02" and by["NFP"]["priced_now"] is None
    assert by["FOMC"]["next"] == "2026-09-16" and by["FOMC"]["priced_now"] is None


def test_after_the_last_calendar_date():
    j = nxt(dt.date(2027, 12, 11))
    assert j["n"] == 0 and j["data"]["soonest"] is None
    assert all(e["note"] == "no upcoming date in event_calendar.csv" for e in j["data"]["by_type"].values())


@pytest.mark.parametrize("t", ["CPI", "NFP", "FOMC"])
def test_in_the_past_matches_stage3(t):
    for row in nxt(dt.date(2026, 10, 15), t)["data"]["in_the_past"][t]:
        i = PERIODS[row["period"]]
        assert row["n"] == S3[t]["n"][i]
        assert round(row["median_implied_move_pct"], 3) == pytest.approx(S3[t]["median_implied_move_pct"][i], abs=1e-9)
        assert round(row["median_actual_move_pct"], 3) == pytest.approx(S3[t]["median_actual_move_pct"][i], abs=1e-9)
        # No ties in the data, so "smaller than priced" is 100% minus Stage 3's "actual > implied".
        assert round(row["share_actual_below_implied_pct"], 3) == pytest.approx(
            100 - S3[t]["share_actual_above_implied_pct"][i], abs=2e-3)


@pytest.mark.parametrize("t", ["CPI", "NFP", "FOMC"])
def test_after_the_event_matches_stage5b(get, t):
    frozen = {(r["measure"], r["group"]): r for r in get("/events/index")["data"]["summary"]}
    rows = nxt(dt.date(2026, 10, 15), t)["data"]["after_the_event"][t]
    assert [r["measure"] for r in rows] == ["30-day ATM (fixed strike)", "30-day RR25", "30-day put wing"]
    for r in rows:
        f = frozen[(r["measure"], t)]
        assert (r["n"], r["event_minus_ordinary_volpts"], r["ci90_volpts"], r["survives_both_controls"]) == \
               (f["n"], f["event_minus_ordinary_volpts"], f["ci90_volpts"], f["survives_both_controls"])


def test_endpoint_wording_and_auth(client, get):
    j = get("/events/next")
    assert j["caveat"] == "History, not a forecast. Education only, not advice."
    text = str(j).lower()
    assert " will " not in text and "expect" not in text
    assert client.get("/events/next").status_code == 401
    assert get("/events/next?type=xyz", status=400)["error"] == "bad input"
