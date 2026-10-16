r"""Stage 5: skew metrics per drawn surface date and their change across each event.

Definitions are in README.md, "Skew and vanna (Stage 5)", written before any result was seen.
From the page's fitted smiles (vol_page.build_date); no downloads; method unchanged.
Writes DuckDB tables surface_metrics and event_skew_changes, prints the summaries (all events,
and excluding events with a flagged day), and saves them to data/skew_summary.txt.
Run after vol_surface.py:  .venv\Scripts\python.exe skew_metrics.py
"""
import datetime as dt
import io
import math

import duckdb
import numpy as np
import pandas as pd

import config
import vol_page

ATM, P25, C25, P10 = 0.50, 0.75, 0.25, 0.90       # call-equivalent delta N(d1)
TARGET = 30                                       # days
SPLIT = dt.date(2023, 6, 1)
TYPES = ["CPI", "NFP", "FOMC"]
CHANGES = ["d_atm30", "d_rr25_30", "d_wing10_30", "d_atm_short", "d_rr25_short", "d_wing10_short", "fwd_return_pct"]


def iv_at(grid, row, x):
    """Fitted IV (vol points) at call-equivalent delta x; NaN outside the drawn range."""
    g = np.asarray(grid)[::-1]                    # increasing
    v = np.asarray(row)[::-1]
    if x < g[0] - 1e-9 or x > g[-1] + 1e-9:
        return np.nan
    return float(np.interp(x, g, v))


def metrics_at(grid, row):
    atm = iv_at(grid, row, ATM)
    return atm, iv_at(grid, row, P25) - iv_at(grid, row, C25), iv_at(grid, row, P10) - atm


def at_30(grid, expiries):
    """Total variance interpolated linearly in days at each delta, between the drawn expiries
    either side of 30 days; IV30 = sqrt(w30 / 30)."""
    below = [e for e in expiries if e["dte"] <= TARGET]
    above = [e for e in expiries if e["dte"] >= TARGET]
    if not below or not above:
        return (np.nan,) * 3
    e1, e2 = max(below, key=lambda e: e["dte"]), min(above, key=lambda e: e["dte"])

    def iv30(x):
        a, b = iv_at(grid, e1["iv"], x), iv_at(grid, e2["iv"], x)
        if e1["dte"] == e2["dte"]:
            return a
        w1, w2 = a * a * e1["dte"], b * b * e2["dte"]
        w = w1 + (w2 - w1) * (TARGET - e1["dte"]) / (e2["dte"] - e1["dte"])
        return math.sqrt(w / TARGET) if w == w and w > 0 else np.nan

    atm = iv30(ATM)
    return atm, iv30(P25) - iv30(C25), iv30(P10) - atm


def build_metrics(opts, exps, days=None):
    """days: the dates to cover (default: every date in vol_surface)."""
    limit, rows, built = config.CLEAN_GAP_LIMIT * 100, [], {}
    for day in sorted(days if days is not None else opts.trade_date.unique()):
        b = vol_page.build_date(day, opts, exps)
        if b is None:
            continue
        built[day] = b
        E = sorted(b["expiries"], key=lambda e: e["dte"])
        a30, rr30, w30 = at_30(b["grid"], E)
        s = E[0]
        a_s, rr_s, w_s = metrics_at(b["grid"], s["iv"])
        rows.append(dict(trade_date=day, flagged=bool(b["fit_max"] > limit or b["tv_drops"] > 0),
                         fit_max=b["fit_max"], range=b["range_label"], atm30=a30, rr25_30=rr30, wing10_30=w30,
                         short_expiry=s["exp"], short_dte=s["dte"], atm_short=a_s, rr25_short=rr_s, wing10_short=w_s))
    return pd.DataFrame(rows), built


def build_changes(metrics, built, events, fwd):
    m = metrics.set_index("trade_date")
    per_event = events.groupby(["date", "trade_date"]).event_type.apply(lambda x: "+".join(sorted(x))).reset_index()
    rows = []
    for ev_day, before, types in zip(per_event.date, per_event.trade_date, per_event.event_type):
        if ev_day not in m.index or before not in m.index:
            continue
        a, b = m.loc[before], m.loc[ev_day]
        # Forward return: drawn Friday expiry nearest 30 days on the event day that has a forward on both days.
        r = np.nan
        for e in sorted(built[ev_day]["expiries"], key=lambda e: abs(e["dte"] - TARGET)):
            f0, f1 = fwd.get((before, e["exp"])), fwd.get((ev_day, e["exp"]))
            if f0 and f1 and f0 == f0 and f1 == f1:
                r, fexp = (f1 / f0 - 1) * 100, e["exp"]
                break
        rows.append(dict(event_date=ev_day, event_type=types, day_before=before,
                         period="2020-01..2023-05" if dt.date.fromisoformat(ev_day) < SPLIT else "2023-06..now",
                         flagged=bool(a.flagged or b.flagged),
                         d_atm30=b.atm30 - a.atm30, d_rr25_30=b.rr25_30 - a.rr25_30, d_wing10_30=b.wing10_30 - a.wing10_30,
                         d_atm_short=b.atm_short - a.atm_short, d_rr25_short=b.rr25_short - a.rr25_short,
                         d_wing10_short=b.wing10_short - a.wing10_short,
                         short_dte_before=a.short_dte, short_dte_event=b.short_dte,
                         fwd_expiry=fexp if r == r else None, fwd_return_pct=r))
    return pd.DataFrame(rows)


def groups(ch):
    long = ch.assign(event_type=ch.event_type.str.split("+")).explode("event_type")
    for t in TYPES + ["all events"]:
        g = long[long.event_type == t] if t != "all events" else ch
        for p in ["2020-01..2023-05", "2023-06..now", "combined"]:
            yield t, p, (g if p == "combined" else g[g.period == p])


def summary(ch, col):
    rows = []
    for t, p, g in groups(ch):
        v = g[col].dropna()
        rows.append(dict(event_type=t, period=p, n=len(v), median=round(v.median(), 3) if len(v) else np.nan,
                         mean=round(v.mean(), 3) if len(v) else np.nan,
                         share_positive=f"{int((v > 0).sum())}/{len(v)}" + (f" ({(v > 0).mean():.0%})" if len(v) else "")))
    return pd.DataFrame(rows)


def spot_vol(ch):
    rows = []
    for t, p, g in groups(ch):
        v = g[["fwd_return_pct", "d_atm30"]].dropna()
        if len(v) >= 3 and v.fwd_return_pct.std() > 0:
            slope = np.polyfit(v.fwd_return_pct, v.d_atm30, 1)[0]
            corr = v.fwd_return_pct.corr(v.d_atm30)
        else:
            slope = corr = np.nan
        rows.append(dict(event_type=t, period=p, n=len(v), slope_volpts_per_1pct=round(slope, 3), correlation=round(corr, 3)))
    return pd.DataFrame(rows)


def report(ch, title, out):
    out.write(f"\n######## {title}: {len(ch)} events ########\n")
    names = {"d_atm30": "30-day ATM vol change (vol points)", "d_rr25_30": "30-day 25-delta risk reversal change",
             "d_wing10_30": "30-day put wing (10-delta put - ATM) change", "d_atm_short": "Shortest-expiry ATM vol change",
             "d_rr25_short": "Shortest-expiry 25-delta risk reversal change", "d_wing10_short": "Shortest-expiry put wing change",
             "fwd_return_pct": "Forward return, day before to event day (%)"}
    for col in CHANGES:
        out.write(f"\n{names[col]}\n{summary(ch, col).to_string(index=False)}\n")
    out.write(f"\nSpot-vol: OLS slope of the 30-day ATM change on the forward return, and correlation\n"
              f"{spot_vol(ch).to_string(index=False)}\n")


def main():
    con = duckdb.connect(str(config.DUCKDB_PATH))
    fx = con.execute(f"SELECT trade_date, expiration, forward FROM {config.SURFACE_EXPIRY_TABLE}").df()
    fwd = {(str(t)[:10], str(e)[:10]): f for t, e, f in zip(fx.trade_date, fx.expiration, fx.forward)}
    # surface_metrics covers every drawn date (Stage 7) except the Stage 5b control dates, which are
    # in control_surface_metrics. Read 100 dates at a time to keep memory low.
    drawn = sorted(str(d)[:10] for d in con.execute(f"SELECT trade_date FROM {config.SURFACE_SUMMARY_TABLE}").df().trade_date)
    try:
        controls = set(con.execute("SELECT trade_date FROM control_surface_metrics").df().trade_date.astype(str).str[:10])
    except duckdb.Error:
        controls = set()
    days = [d for d in drawn if d not in controls]
    _, _, events = vol_page.load(con, days[:1])
    ev_days = set(events.date) | set(events.trade_date)
    parts, built = [], {}
    for i in range(0, len(days), 100):
        opts, exps, _ = vol_page.load(con, days[i:i + 100])
        m, b = build_metrics(opts, exps, days[i:i + 100])
        parts.append(m)
        built.update({d: x for d, x in b.items() if d in ev_days})
    metrics = pd.concat(parts, ignore_index=True)
    # The Stage 5 results (event changes and the summary file) use event days and the days before only.
    ev = metrics[metrics.trade_date.isin(ev_days)]
    ch = build_changes(ev, built, events, fwd)
    con.register("m_df", metrics); con.register("c_df", ch)
    con.execute("CREATE OR REPLACE TABLE surface_metrics AS SELECT * FROM m_df")
    con.execute("CREATE OR REPLACE TABLE event_skew_changes AS SELECT * FROM c_df")
    con.close()
    out = io.StringIO()
    pd.set_option("display.width", 200)
    # This header counts event days and the days before only, so the frozen file stays as it was.
    out.write(f"surface_metrics: {len(ev)} drawn dates ({int(ev.flagged.sum())} flagged); "
              f"empty 30-day metrics: atm {int(ev.atm30.isna().sum())}, rr {int(ev.rr25_30.isna().sum())}, "
              f"wing {int(ev.wing10_30.isna().sum())}\n")
    out.write(f"event_skew_changes: {len(ch)} events with both days drawn ({int(ch.flagged.sum())} with a flagged day)\n")
    report(ch, "ALL EVENTS", out)
    report(ch[~ch.flagged], "EXCLUDING EVENTS WITH A FLAGGED DAY", out)
    text = out.getvalue()
    (config.PROJECT_DIR / "data" / "skew_summary.txt").write_text(text, encoding="utf-8")
    print(text)
    print(f"surface_metrics table: {len(metrics)} dates in all ({int(metrics.flagged.sum())} flagged)")


if __name__ == "__main__":
    main()
