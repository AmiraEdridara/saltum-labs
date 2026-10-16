"""Event index: GET /events/index (rows and the Stage 5b summary).

The summary is computed with the pipeline's own functions (stage5b_controls.compare and survives)
on the tables in their stored order, so it reproduces data/stage5b_summary.txt exactly. It covers
both periods only; a single period returns the rows and an empty summary with a note.
"""
import functools

import pandas as pd

import config
import stage5b_controls
from . import db, events, status, validation as v
from .common import EDU, count_range, envelope

SOURCE = ["event_skew_changes", "event_fixed_strike", "control_pair_changes", "surface_metrics",
          "control_surface_metrics", "event_premium", "event_moves", "date_list", status.SOURCE]


def _inputs(cur, v1_only=False):
    ev = cur.execute("SELECT * FROM event_skew_changes").df()            # stored order, as stage5b_controls
    fs = cur.execute("SELECT event_date, event_type, k_atm30_before, d_atm30_fixed_strike FROM event_fixed_strike").df()
    ev = ev.merge(fs, on=["event_date", "event_type"], how="left")       # left order kept
    if v1_only:                                                           # the frozen v1 rows, in their stored order
        ev = ev[ev.event_date.astype(str).str[:10] <= str(config.V1_END)].reset_index(drop=True)
    ctrl = cur.execute("SELECT * FROM control_pair_changes").df()
    return ev, ctrl


@functools.lru_cache(maxsize=1)
def frozen_summary():
    """{exclude_flagged: comparison table}, plus the survival table (5,000 resamples each; computed once)."""
    ev, ctrl = _inputs(db.cursor(), v1_only=True)          # frozen v1 only: live rows never change this summary
    c_all = stage5b_controls.compare(ev, ctrl)
    c_ex = stage5b_controls.compare(ev[~ev.flagged], ctrl[~ctrl.flagged])
    return {False: c_all, True: c_ex}, stage5b_controls.survives(c_all, c_ex)


def summary_rows(t, exclude_flagged):
    tables, sv = frozen_summary()
    c = tables[exclude_flagged]
    c = c[c.group.isin(("ordinary pairs",) + tuple(v.types_of(t)))]
    surv = {(r.measure, r.event_type): r.survives for r in sv.itertuples()}
    return [dict(measure=r.measure, group=r.group, n=int(r.n), median_volpts=r.median,
                 share_positive_pct=r.share_positive * 100, event_minus_ordinary_volpts=r.diff,
                 ci90_volpts=[r.ci_lo, r.ci_hi], survives_both_controls=surv.get((r.measure, r.group)))
            for r in c.itertuples()]


def _atm(cur):
    m = pd.concat([cur.execute(f"SELECT trade_date, atm30 FROM {t}").df()
                   for t in ("surface_metrics", "control_surface_metrics")])
    return dict(zip(m.trade_date.astype(str), m.atm30))


def get_index(cur, type_, period, exclude_flagged, include_ordinary):
    t, p = v.event_type(type_), v.period(period)
    ex, inc = v.flag(exclude_flagged, "exclude_flagged"), v.flag(include_ordinary, "include_ordinary")
    ev, ctrl = _inputs(cur)
    types = v.types_of(t)
    ev = ev[ev.event_type.str.split("+").map(lambda ts: any(x in types for x in ts))]
    if inc:
        ev = pd.concat([ev, ctrl.assign(event_type="ordinary")], ignore_index=True)
    if p != "both":
        ev = ev[ev.period == p]
    if ex:
        ev = ev[~ev.flagged]
    atm = _atm(cur)
    moves = events.event_rows(cur)
    mv = {(d.isoformat(), e): r for d, e, r in zip(moves.date, moves.event_type, moves.to_dict("records"))}
    rows = []
    for r in ev.sort_values("event_date", ascending=False).itertuples():
        m = mv.get((r.event_date, r.event_type), {})
        rows.append(dict(event_date=r.event_date, event_type=r.event_type, day_before=r.day_before, period=r.period,
                         live=str(r.event_date)[:10] > str(config.V1_END),
                         days_drawn=2, flagged=r.flagged, atm30_before=atm.get(r.day_before),
                         atm30_after=atm.get(r.event_date), d_atm30=r.d_atm30,
                         d_atm30_fixed_strike=r.d_atm30_fixed_strike, d_rr25_30=r.d_rr25_30,
                         d_wing10_30=r.d_wing10_30, implied_move_pct=m.get("implied_move_pct"),
                         actual_move_pct=m.get("actual_move_pct"), premium_pp=m.get("premium_pp")))
    # Events without both days drawn, and why (data/all_dates_status.csv).
    dl = cur.execute("SELECT date, trade_date, event_type FROM date_list WHERE event_type <> 'NONE'").df()
    for c in ("date", "trade_date"):
        dl[c] = pd.to_datetime(dl[c]).dt.date
    dl = events.select(dl, t, p)
    have = set(_inputs(cur)[0].event_date)
    missing = []
    for r in dl[~dl.date.map(lambda d: d.isoformat() in have)].drop_duplicates("date").sort_values("date").itertuples():
        days = sum(status.why_missing(d) is None for d in (r.trade_date, r.date))
        why = "; ".join(f"{d}: {status.why_missing(d)}" for d in (r.trade_date, r.date) if status.why_missing(d))
        missing.append(dict(event_date=r.date, event_type=r.event_type, day_before=r.trade_date, days_drawn=days,
                            reason=why or "both days drawn, but no skew change recorded"))
    if p == "both":
        summ, note = summary_rows(t, ex), None
    else:
        summ, note = [], ("The Stage 5b summary pools both periods; a single-period summary is not in the frozen "
                          "results, so only the rows are filtered.")
    data = dict(summary=summ, summary_note=note,
                summary_units="vol points; median change, day before to event day (ordinary: day 1 to day 2)",
                events=rows, missing=missing)
    ns = [r["n"] for r in summ if r["group"] != "ordinary pairs"]
    caveat = ("9 combinations were tested by the survival rule, so one could pass by chance. "
              + (f"The summary pools both periods, with {count_range(ns)} events per type. " if ns
                 else f"{len(rows)} rows in this selection; no summary. ") + EDU)
    return envelope(SOURCE, len(rows), p, dict(type=t, period=p, exclude_flagged=ex, include_ordinary=inc), caveat, data)
