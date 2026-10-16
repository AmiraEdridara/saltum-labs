r"""Stage 5b: controls for the mechanical effects in Stage 5 (definitions: README, "Stage 5b").

  --plan      choose the 100 ordinary-day pairs (fixed seed, written once to data/control_pairs.csv)
              and count the new surface dates; stops if more than CONTROL_MAX_NEW_DATES.
  (no flag)   compute fixed-strike changes for events, Stage 5 metrics and changes for the control
              pairs, the event-minus-ordinary comparison with bootstrap intervals, and write
              data/stage5b_summary.txt and data/stage5b_readme.md (the README section, generated).
Stage 5 tables (surface_metrics, event_skew_changes) are read, never changed.
Run:  .venv\Scripts\python.exe stage5b_controls.py --plan
      .venv\Scripts\python.exe stage5b_controls.py
"""
import datetime as dt
import io
import math
import sys

import duckdb
import numpy as np
import pandas as pd

import config
import vol_page
from skew_metrics import TARGET, at_30, build_changes, iv_at, metrics_at

CONTROL_PAIRS_CSV = config.PROJECT_DIR / "data" / "control_pairs.csv"
CONTROL_SEED = 20261012
CONTROL_N = 100
CONTROL_MAX_NEW_DATES = 210
BOOT_N, BOOT_SEED = 5000, 20261013
TYPES = ["CPI", "NFP", "FOMC"]


# ---------------------------------------------------------------- plan
def eligible_pairs():
    dl = pd.read_csv(config.DATE_LIST_CSV, parse_dates=["date", "trade_date"])
    dl["date"], dl["trade_date"] = dl.date.dt.date, dl.trade_date.dt.date
    events = set(dl[dl.event_type != "NONE"].date)
    days = sorted(set(dl.date) | set(dl.trade_date))
    nxt = {a: b for a, b in zip(days[:-1], days[1:])}
    one = dl.groupby("date").agg(event_type=("event_type", lambda x: "+".join(sorted(x))), trade_date=("trade_date", "first"))
    rows = []
    for d2, r in one.iterrows():
        d1 = r.trade_date
        if r.event_type != "NONE" or d1 in events or d2 in events:
            continue
        after = nxt.get(d2)
        if after is None or after in events:
            continue
        rows.append((d1, d2))
    return sorted(rows, key=lambda p: p[1])


def plan():
    if CONTROL_PAIRS_CSV.exists():
        pairs = pd.read_csv(CONTROL_PAIRS_CSV)
        print(f"Using the existing {CONTROL_PAIRS_CSV} (never redrawn)")
    else:
        el = eligible_pairs()
        rng = np.random.default_rng(CONTROL_SEED)
        groups = np.array_split(np.arange(len(el)), CONTROL_N)
        pick = [el[int(rng.choice(g))] for g in groups]
        pairs = pd.DataFrame(pick, columns=["day1", "day2"])
        pairs.to_csv(CONTROL_PAIRS_CSV, index=False)
        print(f"{len(el)} eligible pairs; wrote {len(pairs)} to {CONTROL_PAIRS_CSV}")
    dates = set(pd.to_datetime(pairs.day1).dt.date) | set(pd.to_datetime(pairs.day2).dt.date)
    new = sorted(dates - set(config.SURFACE_DATES))
    print(f"{len(pairs)} pairs, {len(dates)} distinct dates, {len(new)} not yet surface dates "
          f"(cap {CONTROL_MAX_NEW_DATES}); {pairs.day2.min()} to {pairs.day2.max()}")
    print("pairs by year:", pd.to_datetime(pairs.day2).dt.year.value_counts().sort_index().to_dict())
    if len(new) > CONTROL_MAX_NEW_DATES:
        raise SystemExit("More than the cap: stop and ask before fetching.")
    print(f"Fetch: about {len(new) + 1} requests (2 at a time, ~10 s each): ~{(len(new) + 1) * 10 / 2 / 60:.0f} min")
    return pairs


# ---------------------------------------------------------------- fixed strike
def strike_at(row, x, grid):
    """Strike of the fitted smile at call-equivalent delta x (linear in delta along the grid)."""
    g, k = np.asarray(grid)[::-1], np.asarray(row["K"], float)[::-1]
    return float(np.interp(x, g, k)) if g[0] - 1e-9 <= x <= g[-1] + 1e-9 else np.nan


def iv_at_strike(row, K):
    """Fitted IV (vol points) at strike K, linear in strike along the smile; NaN outside (no extrapolation)."""
    k, v = np.asarray(row["K"], float), np.asarray(row["iv"], float)
    order = np.argsort(k)
    k, v = k[order], v[order]
    return float(np.interp(K, k, v)) if k[0] <= K <= k[-1] else np.nan


def bracket(E):
    below = [e for e in E if e["dte"] <= TARGET]
    above = [e for e in E if e["dte"] >= TARGET]
    if not below or not above:
        return None, None
    return max(below, key=lambda e: e["dte"]), min(above, key=lambda e: e["dte"])


def k_atm30(b):
    e1, e2 = bracket(b["expiries"])
    if e1 is None:
        return np.nan
    k1, k2 = strike_at(e1, 0.5, b["grid"]), strike_at(e2, 0.5, b["grid"])
    return k1 if e1["dte"] == e2["dte"] else k1 + (k2 - k1) * (TARGET - e1["dte"]) / (e2["dte"] - e1["dte"])


def iv30_at_strike(b, K):
    e1, e2 = bracket(b["expiries"])
    if e1 is None or K != K:
        return np.nan
    a, c = iv_at_strike(e1, K), iv_at_strike(e2, K)
    if e1["dte"] == e2["dte"]:
        return a
    w1, w2 = a * a * e1["dte"], c * c * e2["dte"]
    w = w1 + (w2 - w1) * (TARGET - e1["dte"]) / (e2["dte"] - e1["dte"])
    return math.sqrt(w / TARGET) if w == w and w > 0 else np.nan


def fixed_strike_change(b0, b1):
    K = k_atm30(b0)
    return K, iv30_at_strike(b1, K) - iv30_at_strike(b0, K)


# ---------------------------------------------------------------- metrics for any set of dates
def metrics_for(days, opts, exps):
    limit, rows, built = config.CLEAN_GAP_LIMIT * 100, [], {}
    for day in days:
        b = vol_page.build_date(day, opts, exps)
        if b is None:
            continue
        built[day] = b
        E = sorted(b["expiries"], key=lambda e: e["dte"])
        a30, rr30, w30 = at_30(b["grid"], E)
        a_s, rr_s, w_s = metrics_at(b["grid"], E[0]["iv"])
        rows.append(dict(trade_date=day, flagged=bool(b["fit_max"] > limit or b["tv_drops"] > 0), fit_max=b["fit_max"],
                         range=b["range_label"], atm30=a30, rr25_30=rr30, wing10_30=w30, short_expiry=E[0]["exp"],
                         short_dte=E[0]["dte"], atm_short=a_s, rr25_short=rr_s, wing10_short=w_s))
    return pd.DataFrame(rows), built


# ---------------------------------------------------------------- comparison
def boot_ci(a, b, rng):
    a, b = np.asarray(a), np.asarray(b)
    if len(a) == 0 or len(b) == 0:
        return np.nan, np.nan
    da = np.median(rng.choice(a, (BOOT_N, len(a))), axis=1)
    db = np.median(rng.choice(b, (BOOT_N, len(b))), axis=1)
    return tuple(np.percentile(da - db, [5, 95]))


MEASURES = [("d_atm30", "30-day ATM (50-delta)"), ("d_atm30_fixed_strike", "30-day ATM (fixed strike)"),
            ("d_rr25_30", "30-day RR25"), ("d_wing10_30", "30-day put wing")]


def compare(ev, ctrl):
    rng = np.random.default_rng(BOOT_SEED)
    long = ev.assign(event_type=ev.event_type.str.split("+")).explode("event_type")
    rows = []
    for col, name in MEASURES:
        o = ctrl[col].dropna().values
        rows.append(dict(measure=name, group="ordinary pairs", n=len(o), median=np.median(o) if len(o) else np.nan,
                         share_positive=(o > 0).mean() if len(o) else np.nan, diff=np.nan, ci_lo=np.nan, ci_hi=np.nan))
        for t in TYPES:
            e = long[long.event_type == t][col].dropna().values
            lo, hi = boot_ci(e, o, rng)
            rows.append(dict(measure=name, group=t, n=len(e), median=np.median(e) if len(e) else np.nan,
                             share_positive=(e > 0).mean() if len(e) else np.nan,
                             diff=(np.median(e) - np.median(o)) if len(e) and len(o) else np.nan, ci_lo=lo, ci_hi=hi))
    return pd.DataFrame(rows)


def slopes(ev, ctrl):
    long = ev.assign(event_type=ev.event_type.str.split("+")).explode("event_type")
    rows = []
    for g, df in [(t, long[long.event_type == t]) for t in TYPES] + [("all events", ev), ("ordinary pairs", ctrl)]:
        for col, name in [("d_atm30", "50-delta"), ("d_atm30_fixed_strike", "fixed strike")]:
            v = df[["fwd_return_pct", col]].dropna()
            ok = len(v) >= 3 and v.fwd_return_pct.std() > 0
            rows.append(dict(group=g, atm=name, n=len(v),
                             slope_volpts_per_1pct=np.polyfit(v.fwd_return_pct, v[col], 1)[0] if ok else np.nan,
                             correlation=v.fwd_return_pct.corr(v[col]) if ok else np.nan))
    return pd.DataFrame(rows)


def fmt_compare(c):
    c = c.copy()
    c["median"] = c["median"].map(lambda v: f"{v:+.3f}")
    c["share_positive"] = c.share_positive.map(lambda v: f"{v:.0%}")
    c["event_minus_ordinary"] = [("–" if d != d else f"{d:+.3f}") for d in c["diff"]]
    c["ci90"] = [("–" if lo != lo else f"[{lo:+.3f}, {hi:+.3f}]") for lo, hi in zip(c.ci_lo, c.ci_hi)]
    return c[["measure", "group", "n", "median", "share_positive", "event_minus_ordinary", "ci90"]]


def survives(c_all, c_ex):
    out = []
    for col, name in [("d_atm30_fixed_strike", "30-day ATM (fixed strike)"), ("d_rr25_30", "30-day RR25"),
                      ("d_wing10_30", "30-day put wing")]:
        for t in TYPES:
            ok = []
            for c in (c_all, c_ex):
                r = c[(c.measure == name) & (c.group == t)].iloc[0]
                ok.append(r.ci_lo == r.ci_lo and (r.ci_lo > 0 or r.ci_hi < 0))
            out.append(dict(measure=name, event_type=t, all_dates=ok[0], excluding_flagged=ok[1], survives=all(ok)))
    return pd.DataFrame(out)


def compute():
    pairs = pd.read_csv(CONTROL_PAIRS_CSV)
    con = duckdb.connect(str(config.DUCKDB_PATH))
    opts, exps, events = vol_page.load(con)
    fx = con.execute(f"SELECT trade_date, expiration, forward FROM {config.SURFACE_EXPIRY_TABLE}").df()
    fwd = {(str(t)[:10], str(e)[:10]): f for t, e, f in zip(fx.trade_date, fx.expiration, fx.forward)}
    ev = con.execute("SELECT * FROM event_skew_changes").df()              # Stage 5, unchanged
    ev_dates = sorted(set(ev.event_date.astype(str)) | set(ev.day_before.astype(str)))
    _, built_ev = metrics_for(ev_dates, opts, exps)
    fs = [fixed_strike_change(built_ev[b0], built_ev[b1]) for b0, b1 in zip(ev.day_before.astype(str), ev.event_date.astype(str))]
    ev["k_atm30_before"], ev["d_atm30_fixed_strike"] = [k for k, _ in fs], [d for _, d in fs]
    ctrl_days = sorted(set(pairs.day1) | set(pairs.day2))
    cm, built_c = metrics_for(ctrl_days, opts, exps)
    pseudo = pd.DataFrame({"date": pairs.day2, "trade_date": pairs.day1, "event_type": "ORDINARY"})
    ctrl = build_changes(cm, built_c, pseudo, fwd)
    fsc = [fixed_strike_change(built_c[a], built_c[b]) if a in built_c and b in built_c else (np.nan, np.nan)
           for a, b in zip(ctrl.day_before, ctrl.event_date)]
    ctrl["k_atm30_before"], ctrl["d_atm30_fixed_strike"] = [k for k, _ in fsc], [d for _, d in fsc]
    con.register("fs_df", ev[["event_date", "event_type", "day_before", "flagged", "k_atm30_before", "d_atm30",
                              "d_atm30_fixed_strike", "fwd_return_pct"]])
    con.register("cm_df", cm); con.register("cc_df", ctrl)
    con.execute("CREATE OR REPLACE TABLE event_fixed_strike AS SELECT * FROM fs_df")
    con.execute("CREATE OR REPLACE TABLE control_surface_metrics AS SELECT * FROM cm_df")
    con.execute("CREATE OR REPLACE TABLE control_pair_changes AS SELECT * FROM cc_df")
    con.close()

    out, md = io.StringIO(), io.StringIO()
    pd.set_option("display.width", 220)
    out.write(f"Control pairs: {len(pairs)} chosen, {len(ctrl)} with both days drawn ({int(ctrl.flagged.sum())} with a "
              f"flagged day). Events: {len(ev)} ({int(ev.flagged.sum())} with a flagged day). "
              f"Fixed-strike change empty for {int(ev.d_atm30_fixed_strike.isna().sum())} events and "
              f"{int(ctrl.d_atm30_fixed_strike.isna().sum())} pairs (strike outside the drawn smile).\n")
    md.write(f"### Results (generated by `stage5b_controls.py` from its output)\n\n"
             f"- **Control pairs:** {len(pairs)} chosen ({pairs.day2.min()} to {pairs.day2.max()}); "
             f"{len(ctrl)} have both days drawn, of which {int(ctrl.flagged.sum())} have a flagged day.\n"
             f"- **Events:** {len(ev)} (Stage 5), of which {int(ev.flagged.sum())} have a flagged day.\n"
             f"- **Empty fixed-strike changes** (strike outside the drawn smile, so not extrapolated): "
             f"{int(ev.d_atm30_fixed_strike.isna().sum())} events and {int(ctrl.d_atm30_fixed_strike.isna().sum())} pairs.\n\n")
    results = {}
    for label, e, c in [("ALL", ev, ctrl), ("EXCLUDING FLAGGED", ev[~ev.flagged], ctrl[~ctrl.flagged])]:
        cmp_, sl = compare(e, c), slopes(e, c)
        results[label] = cmp_
        out.write(f"\n######## {label} ########\nMedian change, day before -> event day (ordinary: day 1 -> day 2), vol points\n")
        out.write(fmt_compare(cmp_).to_string(index=False) + "\n")
        out.write("\nSpot-vol slope of the 30-day ATM change on the forward return (vol points per 1%)\n")
        out.write(sl.round(3).to_string(index=False) + "\n")
        md.write(f"**{label.capitalize()}: median change (vol points), event minus ordinary, bootstrap 90% interval**\n\n"
                 "| Measure | Group | n | Median | Share > 0 | Event − ordinary | 90% interval |\n|---|---|---|---|---|---|---|\n")
        for r in fmt_compare(cmp_).itertuples():
            md.write(f"| {r.measure} | {r.group} | {r.n} | {r.median} | {r.share_positive} | {r.event_minus_ordinary} | {r.ci90} |\n")
        md.write(f"\n**{label.capitalize()}: spot-vol slope (vol points per 1% forward return)**\n\n"
                 "| Group | ATM | n | Slope | Correlation |\n|---|---|---|---|---|\n")
        for r in sl.itertuples():
            md.write(f"| {r.group} | {r.atm} | {r.n} | {r.slope_volpts_per_1pct:+.3f} | {r.correlation:+.3f} |\n")
        md.write("\n")
    sv = survives(results["ALL"], results["EXCLUDING FLAGGED"])
    out.write("\nSurvives both controls (90% interval of event minus ordinary excludes 0, with and without flagged dates):\n")
    out.write(sv.to_string(index=False) + "\n")
    md.write("**Survives both controls** (fixed-strike ATM for the level; rule defined above)\n\n"
             "| Measure | Event type | All dates | Excluding flagged | Survives |\n|---|---|---|---|---|\n")
    for r in sv.itertuples():
        md.write(f"| {r.measure} | {r.event_type} | {'yes' if r.all_dates else 'no'} | {'yes' if r.excluding_flagged else 'no'} | "
                 f"**{'yes' if r.survives else 'no'}** |\n")
    (config.PROJECT_DIR / "data" / "stage5b_summary.txt").write_text(out.getvalue(), encoding="utf-8")
    (config.PROJECT_DIR / "data" / "stage5b_readme.md").write_text(md.getvalue(), encoding="utf-8")
    print(out.getvalue())


if __name__ == "__main__":
    plan() if "--plan" in sys.argv else compute()
