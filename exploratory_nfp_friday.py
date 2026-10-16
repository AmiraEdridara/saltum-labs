r"""EXPLORATORY, chosen after seeing the Stage 3 results: payrolls event premium with a
Friday-only baseline (ordinary Fridays within the same 20-trading-day window), next to the
standard baseline (all ordinary days in the window). Changes no table, rule or limit.
Saves the printout to data/exploratory_nfp_friday.txt.
Run:  .venv\Scripts\python.exe exploratory_nfp_friday.py
"""
import datetime as dt

import duckdb
import pandas as pd

import config
from summarize import by_event_type, event_premium

SPLIT = dt.date(2023, 6, 1)


def nfp_premium(moves):
    p = by_event_type(event_premium(moves))
    p = p[p.event_type == "NFP"].copy()
    p["date"] = pd.to_datetime(p.date).dt.date
    return p


def row(p, label):
    return dict(baseline=label, n=len(p), median_baseline=round(p.baseline_implied.median() * 100, 3),
                median_premium=round(p.premium.median() * 100, 3),
                positive=f"{int((p.premium > 0).sum())}/{len(p)} ({(p.premium > 0).mean():.0%})",
                median_baseline_days=int(p.baseline_n.median()))


def main():
    con = duckdb.connect(str(config.DUCKDB_PATH), read_only=True)
    m = con.execute(f"SELECT * FROM {config.RESULTS_TABLE} ORDER BY date").df()
    con.close()
    m["date"] = pd.to_datetime(m.date).dt.date
    # Friday-only baseline: drop ordinary (NONE) days that are not Fridays; event days are kept.
    fri = m[(m.event_type != "NONE") | m.date.map(lambda d: d.weekday() == 4)]
    std, fon = nfp_premium(m), nfp_premium(fri)
    rows = []
    for name, cond in [("2020-01..2023-05", lambda p: p.date < SPLIT), ("2023-06..2026-10", lambda p: p.date >= SPLIT),
                       ("combined", lambda p: p.date == p.date)]:
        rows.append(dict(period=name, **row(std[cond(std)], "all ordinary days (existing)")))
        rows.append(dict(period=name, **row(fon[cond(fon)], "ordinary Fridays only (exploratory)")))
    pd.set_option("display.width", 200)
    nf = m[m.event_type.str.contains("NFP")]
    off = nf[nf.date.map(lambda d: d.weekday() != 4)]
    text = ("EXPLORATORY (chosen after the fact): payrolls premium, standard vs Friday-only baseline (pp)\n"
            + pd.DataFrame(rows).to_string(index=False) + "\n"
            + f"\nPayrolls days measured that are not Fridays ({len(off)} of {len(nf)}): "
            + ", ".join(f"{d} {d.strftime('%a')}" for d in off.date) + "\n")
    (config.PROJECT_DIR / "data" / "exploratory_nfp_friday.txt").write_text(text, encoding="utf-8")
    print(text, end="")


if __name__ == "__main__":
    main()
