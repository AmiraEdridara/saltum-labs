"""GET /events/next: the next scheduled CPI, NFP or FOMC release, and what past events of that type show.

Reads event_calendar.csv for the dates and the existing tables for everything else. History only,
never a forecast. "Priced now" is the stored implied move for that event date when a row exists
(the day-before 16:15 snapshot was measured); otherwise "not priced yet". It never returns an
actual move for the next event.
"""
import datetime as dt
from zoneinfo import ZoneInfo

import pandas as pd

import config
from . import events, index, validation as v
from .common import EDU, envelope

SOURCE = ["event_calendar.csv", "event_moves", "event_premium", "event_skew_changes", "event_fixed_strike",
          "control_pair_changes"]
CAVEAT = f"History, not a forecast. {EDU}"
AFTER_MEASURES = ("30-day ATM (fixed strike)", "30-day RR25", "30-day put wing")     # the survival rule's measures


def today_ny():
    return dt.datetime.now(ZoneInfo("America/New_York")).date()


def calendar():
    c = pd.read_csv(config.EVENT_CALENDAR, dtype=str)
    c["date"] = pd.to_datetime(c.date).dt.date
    return c


def in_the_past(rows, t):
    rows = rows[~rows.live]                       # history from the frozen v1 rows
    out = []
    for p in v.PERIODS:
        g = events.select(rows, t, p)
        g = g[g.event_type.str.split("+").map(lambda ts: t in ts)]
        out.append(dict(period=p, n=len(g), median_implied_move_pct=g.implied_move_pct.median(),
                        median_actual_move_pct=g.actual_move_pct.median(),
                        share_actual_below_implied_pct=(g.actual_move_pct < g.implied_move_pct).mean() * 100
                        if len(g) else None))
    return out


def after_the_event(t):
    rows = index.summary_rows(t, False)
    ordinary = {r["measure"]: r for r in rows if r["group"] == "ordinary pairs"}
    return [dict(measure=r["measure"], n=r["n"], median_volpts=r["median_volpts"],
                 ordinary_median_volpts=ordinary[r["measure"]]["median_volpts"],
                 ordinary_n=ordinary[r["measure"]]["n"], event_minus_ordinary_volpts=r["event_minus_ordinary_volpts"],
                 ci90_volpts=r["ci90_volpts"], survives_both_controls=r["survives_both_controls"])
            for r in rows if r["group"] == t and r["measure"] in AFTER_MEASURES]


def next_event(cur, type_, today=None):
    t = v.event_type(type_)
    today = today or today_ny()
    cal = calendar()
    rows = events.event_rows(cur)
    latest = cur.execute("SELECT max(trade_date) FROM surface_date_summary").fetchone()[0]
    priced_rows = {}
    if events.has_table(cur, config.PRICED_TABLE):
        pr = cur.execute(f"SELECT date, event_type, trade_date, implied_move * 100 AS implied_move_pct, after_hours_gap "
                         f"FROM {config.PRICED_TABLE}").df()
        for r in pr.to_dict("records"):
            for et in r["event_type"].split("+"):
                priced_rows[(pd.Timestamp(r["date"]).date(), et)] = dict(r, trade_date=pd.Timestamp(r["trade_date"]).date(),
                                                                     after_hours_gap=None if r["after_hours_gap"] != r["after_hours_gap"] or r["after_hours_gap"] is None else bool(r["after_hours_gap"]))
    by_type = {}
    for x in v.types_of(t):
        up = cal[(cal.event_type == x) & (cal.date >= today)].sort_values("date")
        if not len(up):
            by_type[x] = dict(next=None, note="no upcoming date in event_calendar.csv")
            continue
        d = up.date.iloc[0]
        # Priced only from a stored day-before snapshot taken no later than today (no look-ahead).
        priced = rows[(rows.date == d) & (rows.trade_date <= today) & rows.event_type.str.split("+").map(lambda ts: x in ts)]
        entry = dict(next=d, days_away=(d - today).days, calendar_source=up.source.iloc[0])
        early = priced_rows.get((d, x))
        if len(priced):
            entry.update(priced_now=dict(implied_move_pct=priced.implied_move_pct.iloc[0],
                                         trade_date=priced.trade_date.iloc[0], source="event_moves"),
                         priced_note=f"implied move from the {priced.trade_date.iloc[0]} 16:15 close")
        elif early is not None and early["trade_date"] <= today:
            entry.update(priced_now=dict(implied_move_pct=early["implied_move_pct"], trade_date=early["trade_date"],
                                         source=config.PRICED_TABLE, live=True),
                         priced_note=f"implied move from the {early['trade_date']} 16:15 close"
                                     + ("" if early["after_hours_gap"] is not None else
                                        "; after-hours check pending until FRED has that day's close"))
        else:
            entry.update(priced_now=None,
                         priced_note=f"not priced yet: no 16:15 snapshot for the trading day before {d} "
                                     f"(the data ends at the {pd.Timestamp(latest).date()} close)")
        by_type[x] = entry
    dated = {x: e for x, e in by_type.items() if e["next"] is not None}
    soonest = None
    if dated:
        first = min(e["next"] for e in dated.values())
        types = [x for x, e in dated.items() if e["next"] == first]
        soonest = dict(date=first, event_types=types, days_away=(first - today).days)
    data = dict(today=today, soonest=soonest, by_type=by_type,
                in_the_past={x: in_the_past(rows, x) for x in v.types_of(t)},
                in_the_past_note="Same event type, each period. The share counts days where the actual move "
                                 "was smaller than the implied move.",
                after_the_event={x: after_the_event(x) for x in v.types_of(t)},
                after_the_event_note=("Median change from the day before to the event day, against ordinary-day "
                                      "pairs, in vol points; Stage 5b summary, both periods pooled, all events."))
    return envelope(SOURCE, len(dated), "both", dict(type=t, today=today.isoformat()), CAVEAT, data)
