"""Event study: GET /events, GET /events/{date}, and the skew numbers GET /events/{date}/skew.

Same selections and units as data/stage3_periods.txt: moves in %, premiums in percentage points,
variance premium in %^2 (straddle units). Values are unrounded.
"""
import pandas as pd

import config
from . import status, validation as v
from .common import EDU, LIVE_NOTE, NotFound, count_range, envelope

PROV_COLS = (', q.premium * 100 AS provisional_premium_pp, q.variance_premium * 1e4 AS provisional_variance_premium_pct2, q.baseline_implied * 100 AS provisional_baseline_pct, q.baseline_n AS provisional_baseline_n, q.window_days_used AS provisional_window_days_used, q.window_days AS provisional_window_days')

MOVE_SOURCE = ["event_moves", "event_premium", "date_list"]
SKEW_SOURCE = ["surface_metrics", "event_skew_changes", "event_fixed_strike"]
NOT_MEASURED = "no same-day SPXW expiry, so no implied move (data/stage3_periods.txt)"


def has_table(cur, name):
    return bool(cur.execute("SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [name]).fetchone()[0])


def event_rows(cur):
    """One row per measured event day, newest first (the 15 days with no same-day expiry are absent). Rows after
    the v1 cut-off are marked live. Until an event's baseline window is complete its premium is provisional (in
    separate provisional_* columns) and premium_pp is empty."""
    prov = has_table(cur, config.PROVISIONAL_TABLE)
    join = (f"LEFT JOIN {config.PROVISIONAL_TABLE} q ON CAST(q.date AS DATE) = m.date AND q.event_type = m.event_type"
            if prov else "")
    d = cur.execute(f"""
        SELECT m.date, m.event_type, m.trade_date, m.implied_move * 100 AS implied_move_pct,
               m.actual_move * 100 AS actual_move_pct, m.ratio, p.baseline_implied * 100 AS baseline_pct,
               p.baseline_n, p.premium * 100 AS premium_pp, p.variance_premium * 1e4 AS variance_premium_pct2,
               m.half_day, m.after_hours_gap {PROV_COLS if prov else ""}
        FROM event_moves m
        LEFT JOIN event_premium p ON CAST(p.date AS DATE) = m.date AND p.event_type = m.event_type
        {join}
        WHERE m.event_type <> 'NONE' ORDER BY m.date DESC""").df()
    for c in ("date", "trade_date"):
        d[c] = pd.to_datetime(d[c]).dt.date
    d["live"] = d.date.map(v.is_live).astype(bool)
    final = d.premium_pp.notna()
    provisional = d["provisional_premium_pp"].notna() if prov else pd.Series(False, index=d.index)
    d["premium_status"] = ["final" if f else "provisional" if p else "pending" for f, p in zip(final, provisional)]
    return d


def select(d, t, period, date_col="date"):
    if d.empty:                                    # an empty filter would select columns, not rows
        return d
    types = v.types_of(t)
    keep = d.event_type.str.split("+").map(lambda ts: any(x in types for x in ts))
    if period != "both":
        keep &= d[date_col].map(v.period_of) == period
    return d[keep]


def summary(d, t):
    """The Stage 3 summary for one event type (a CPI+FOMC day counts under both)."""
    g = d[d.event_type.str.split("+").map(lambda ts: t in ts)]
    return dict(event_type=t, n=len(g),
                mean_implied_move_pct=g.implied_move_pct.mean(), median_implied_move_pct=g.implied_move_pct.median(),
                mean_actual_move_pct=g.actual_move_pct.mean(), median_actual_move_pct=g.actual_move_pct.median(),
                median_ratio=g.ratio.median(),
                share_actual_above_implied_pct=(g.actual_move_pct > g.implied_move_pct).mean() * 100 if len(g) else None,
                median_baseline_pct=g.baseline_pct.median(), median_premium_pp=g.premium_pp.median(),
                mean_premium_pp=g.premium_pp.mean(), median_variance_premium_pct2=g.variance_premium_pct2.median(),
                positive_premium_n=int((g.premium_pp > 0).sum()))


def not_measured(cur, d, t, period):
    """v1 event days in date_list (within the selection) with no measured row (the frozen 15)."""
    dl = cur.execute("SELECT date, event_type FROM date_list WHERE event_type <> 'NONE'").df()
    dl["date"] = pd.to_datetime(dl.date).dt.date
    dl = select(dl[dl.date <= config.V1_END], t, period)
    miss = dl[~dl.date.isin(set(d.date))].drop_duplicates()
    return dict(n=len(miss), reason=NOT_MEASURED,
                by_type={x: int((miss.event_type == x).sum()) for x in v.types_of(t)},
                dates=[dict(date=r.date, event_type=r.event_type) for r in miss.sort_values("date").itertuples()])


def pending(cur, rows, t, period):
    """Live event days already in the date list but not measured yet (the daily update adds them)."""
    dl = cur.execute("SELECT date, event_type FROM date_list WHERE event_type <> 'NONE'").df()
    dl["date"] = pd.to_datetime(dl.date).dt.date
    dl = select(dl[dl.date > config.V1_END], t, period)
    miss = dl[~dl.date.isin(set(rows.date))].drop_duplicates()
    return [dict(date=r.date, event_type=r.event_type) for r in miss.sort_values("date").itertuples()]


def list_events(cur, type_, period, data="v1"):
    t, p, ds = v.event_type(type_), v.period(period), v.dataset(data)
    rows = select(event_rows(cur), t, p)
    base = rows if ds == "live" else rows[~rows.live]          # summaries: the v1 rows unless live data is asked for
    out = dict(summaries=[summary(base, x) for x in v.types_of(t)], summaries_data=ds,
               not_measured=not_measured(cur, rows, t, p), pending=pending(cur, rows, t, p),
               events=rows.to_dict("records"), live_rows=int(rows.live.sum()))
    ns = [s["n"] for s in out["summaries"]]
    caveat = ("Rough estimate: the baseline is the median implied move on ordinary days within 20 trading days. "
              f"{count_range(ns)} events per type in this selection. "
              + (f"{LIVE_NOTE} Premium medians use final premiums only. " if ds == "live"
                 else f"Summaries: frozen v1 results (data to {config.V1_END}). ") + EDU)
    return envelope(MOVE_SOURCE, len(rows), p, dict(type=t, period=p, data=ds), caveat, out)


def get_event(cur, date):
    day = v.date(date)
    rows = event_rows(cur)
    r = rows[rows.date == day]
    if not len(r):
        dl = cur.execute("SELECT event_type FROM date_list WHERE date = ? AND event_type <> 'NONE'", [day]).fetchall()
        raise NotFound(f"{day} is an event day ({dl[0][0]}) but was not measured: {NOT_MEASURED}" if dl
                       else f"{day} is not a CPI, NFP or FOMC event day")
    row = r.iloc[0].to_dict()
    note = f" {LIVE_NOTE}" if row["live"] else ""
    return envelope(MOVE_SOURCE, 1, v.period_of(day), dict(date=day.isoformat()),
                    f"One event proves nothing on its own.{note} {EDU}", row)


def metrics_row(cur, day):
    for table in ("surface_metrics", "control_surface_metrics"):
        m = cur.execute(f"SELECT * FROM {table} WHERE trade_date = ?", [day]).df()
        if len(m):
            return m.iloc[0].to_dict()
    return None


def get_skew(cur, date):
    day = v.date(date)
    ch = cur.execute("SELECT * FROM event_skew_changes WHERE event_date = ?", [day.isoformat()]).df()
    if not len(ch):
        dl = cur.execute("SELECT trade_date, event_type FROM date_list WHERE date = ? AND event_type <> 'NONE'", [day]).fetchall()
        if not dl:
            raise NotFound(f"{day} is not a CPI, NFP or FOMC event day")
        before = pd.Timestamp(dl[0][0]).date()
        why = "; ".join(f"{d}: {status.why_missing(d)}" for d in (before, day) if status.why_missing(d))
        raise NotFound(f"{day} ({dl[0][1]}) has no skew numbers: {why or 'not both days drawn'}")
    c = ch.iloc[0].to_dict()
    fs = cur.execute("SELECT k_atm30_before, d_atm30_fixed_strike FROM event_fixed_strike WHERE event_date = ?",
                     [day.isoformat()]).df()
    c.update(fs.iloc[0].to_dict() if len(fs) else dict(k_atm30_before=None, d_atm30_fixed_strike=None))
    keys = ["atm30", "rr25_30", "wing10_30", "atm_short", "rr25_short", "wing10_short", "short_expiry", "short_dte",
            "flagged", "fit_max", "range"]
    days = {}
    for name, d in (("day_before", c["day_before"]), ("event_day", c["event_date"])):
        m = metrics_row(cur, d) or {}
        days[name] = dict(date=d, **{k: m.get(k) for k in keys})
    change = {k: c[k] for k in ("d_atm30", "d_rr25_30", "d_wing10_30", "d_atm_short", "d_rr25_short", "d_wing10_short",
                                "d_atm30_fixed_strike", "k_atm30_before", "fwd_expiry", "fwd_return_pct",
                                "short_dte_before", "short_dte_event")}
    data = dict(event_type=c["event_type"], flagged=c["flagged"], units="vol points; fwd_return_pct in %", **days,
                change=change)
    return envelope(SKEW_SOURCE, 2, c["period"], dict(date=day.isoformat()),
                    f"One event proves nothing on its own. {EDU}", data)
