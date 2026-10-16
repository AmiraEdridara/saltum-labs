r"""Out-of-sample check: event study by period (method frozen; same definitions as summarize.py).

Periods (by event date D): new 2020-01 to 2023-05, existing 2023-06 onward, and combined.
Also: the same tables excluding the COVID crash window, the share of positive premiums in the
new period, a check of the Monday/Wednesday/Friday-only baseline, and the skipped dates.
Run after pull_chains.py, calc_moves.py and summarize.py:
      .venv\Scripts\python.exe stage3_periods.py
"""
import datetime as dt

import duckdb
import pandas as pd

import config
from compare_snapshots import premium_stats, stats
from summarize import TYPES, by_event_type, event_premium

SPLIT = dt.date(2023, 6, 1)                                  # first event date of the existing period
COVID = (dt.date(2020, 2, 20), dt.date(2020, 4, 30))
DAILY_FROM = dt.date(2022, 5, 13)                            # SPXW expiries on every weekday from here


def load():
    con = duckdb.connect(str(config.DUCKDB_PATH), read_only=True)
    m = con.execute(f"SELECT * FROM {config.RESULTS_TABLE} ORDER BY date").df()
    con.close()
    m["date"] = pd.to_datetime(m.date).dt.date
    return m


def periods(m):
    return {"2020-01..2023-05": m[m.date < SPLIT], "2023-06..2026-10": m[m.date >= SPLIT], "combined": m}


def side_by_side(frames, fn):
    out = pd.concat({k: fn(v) for k, v in frames.items()}, axis=1).swaplevel(axis=1)
    cols = list(fn(next(iter(frames.values()))).columns)
    return out[[(c, k) for c in cols for k in frames]].round(3)


def premium_by_period(m):
    """Premium per event day (baseline from NONE days within 20 trading days, all periods), split by period."""
    prem = event_premium(m)
    prem["date"] = pd.to_datetime(prem.date).dt.date
    return prem


def premium_table(prem):
    frames = {"2020-01..2023-05": prem[prem.date < SPLIT], "2023-06..2026-10": prem[prem.date >= SPLIT], "combined": prem}
    rows = {}
    for name, p in frames.items():
        long = by_event_type(p)
        rows[name] = pd.DataFrame([dict(event_type=t, n=int((long.event_type == t).sum()),
                                        median_baseline=long[long.event_type == t].baseline_implied.median() * 100,
                                        median_premium=long[long.event_type == t].premium.median() * 100,
                                        mean_premium=long[long.event_type == t].premium.mean() * 100,
                                        median_variance_premium=long[long.event_type == t].variance_premium.median() * 1e4)
                                   for t in TYPES[1:]]).set_index("event_type")
    out = pd.concat(rows, axis=1).swaplevel(axis=1)
    cols = list(next(iter(rows.values())).columns)
    return out[[(c, k) for c in cols for k in rows]].round(3), frames


def report(m, title):
    print(f"\n######## {title} ########")
    fr = periods(m)
    print("Dates measured: " + ", ".join(f"{k} {len(v)}" for k, v in fr.items()))
    print("\nSummary by event type (moves in %; share_above = % of days with actual > implied)")
    print(side_by_side(fr, stats).to_string())
    prem = premium_by_period(m)
    table, pfr = premium_table(prem)
    print("\nEvent premium, rough estimate (pp; variance premium in %^2 straddle units)")
    print(table.to_string())
    new = by_event_type(pfr["2020-01..2023-05"])
    print("\nNew period: share of event days with a positive premium")
    for t in TYPES[1:]:
        g = new[new.event_type == t]
        k = int((g.premium > 0).sum())
        print(f"  {t:5s} {k}/{len(g)}" + (f" ({k / len(g):.0%})" if len(g) else ""))


def baseline_check(m):
    print("\n######## Baseline check: Monday/Wednesday/Friday NONE days vs all weekdays (2023-06 onward) ########")
    ex = m[(m.date >= SPLIT) & (m.event_type == "NONE")].copy()
    ex["wd"] = [d.strftime("%a") for d in ex.date]
    mwf = ex[ex.wd.isin(["Mon", "Wed", "Fri"])]
    print(f"median implied move: all weekdays {ex.implied_move.median() * 100:.3f}% (n={len(ex)}), "
          f"Mon/Wed/Fri {mwf.implied_move.median() * 100:.3f}% (n={len(mwf)}), "
          f"difference {(mwf.implied_move.median() - ex.implied_move.median()) * 100:+.3f} pp")
    print("by weekday: " + ", ".join(f"{w} {g.implied_move.median() * 100:.3f}% (n={len(g)})"
                                     for w, g in ex.groupby("wd")))
    # Effect on the premium: existing-period event days, baseline from all NONE days vs Mon/Wed/Fri only.
    full = m[m.date >= SPLIT]
    drop = full[(full.event_type == "NONE") & ~full.date.map(lambda d: d.strftime("%a") in ("Mon", "Wed", "Fri"))]
    a = by_event_type(premium_by_period(full)); b = by_event_type(premium_by_period(full[~full.index.isin(drop.index)]))
    print("existing-period median premium with baseline from all weekdays vs Mon/Wed/Fri only:")
    for t in TYPES[1:]:
        x, y = a[a.event_type == t].premium.median() * 100, b[b.event_type == t].premium.median() * 100
        print(f"  {t:5s} {x:+.3f} vs {y:+.3f} pp (difference {y - x:+.3f}; n={int((a.event_type == t).sum())})")


def skipped():
    print("\n######## Dates skipped (2020-01..2023-05) ########")
    dl = pd.read_csv(config.DATE_LIST_CSV, parse_dates=["date"]); dl["date"] = dl.date.dt.date
    sk = pd.read_csv(config.SKIPPED_LOG, parse_dates=["date"]); sk["date"] = sk.date.dt.date
    pe = pd.read_csv(config.PULL_ERRORS, parse_dates=["date"]) if config.PULL_ERRORS.stat().st_size > 30 else pd.DataFrame(columns=["date", "reason"])
    if len(pe):
        pe["date"] = pd.to_datetime(pe.date).dt.date
    reason = dict(zip(pe.date, pe.reason))
    sk = sk[sk.date < SPLIT].copy()
    sk["reason"] = [reason.get(d, r) for d, r in zip(sk.date, sk.reason)]
    sk["kind"] = sk.reason.str.replace(r"[\d.:-]+", "#", regex=True).str.slice(0, 60)
    print(f"{len(sk)} dates skipped of {int((dl.date < SPLIT).sum())} event-study rows before {SPLIT}")
    print(sk.groupby(["event_type", "kind"]).size().to_string())
    ev = sk[sk.event_type != "NONE"].sort_values("date")
    print("\nEvent days skipped:")
    print(ev[["date", "event_type", "reason"]].to_string(index=False))


def main():
    m = load()
    pd.set_option("display.width", 260)
    report(m, "All dates")
    c = m[~((m.date >= COVID[0]) & (m.date <= COVID[1]))]
    report(c, f"Excluding {COVID[0]} to {COVID[1]} (COVID crash): {len(m) - len(c)} dates removed")
    baseline_check(m)
    skipped()


if __name__ == "__main__":
    main()
