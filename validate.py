r"""Run every surface check and the VIX replication on every validation date.

Same method as vol_surface.py and vix_check.py, unchanged. A date that fails a check is
recorded with the reason, never dropped. Writes DuckDB tables validation_results (one row
per date) and validation_flags (one row per flag), prints the summary, saves one figure.
Run after pull_validation.py:  .venv\Scripts\python.exe validate.py
"""
import datetime as dt

import duckdb
import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

import config
from analyze_event import fred_closes
from vix_check import bracket, eligible_expiries, load_quotes_eod, load_rates, trading_days, vix_for_date
from vol_surface import bill_curve_on, process_date, run_checks

CHECKS = ["reprice", "total_variance", "iv_range", "rate_info", "no_forward", "discount_factor", "no_atm", "atm30_vs_vix"]


def main():
    dates = pd.read_csv(config.VALIDATION_DATES_CSV, parse_dates=["date"])
    dates["date"] = dates.date.dt.date
    start, end = min(dates.date) - dt.timedelta(days=10), max(dates.date)
    bills = {s: fred_closes(start, end, s) for s in config.BILL_SERIES}
    vixcls = fred_closes(start, end, config.VIX_SERIES)
    cmt = load_rates(start, end)
    tdays = trading_days(start, end + dt.timedelta(days=70))

    con = duckdb.connect(str(config.DUCKDB_PATH))
    # The 51-date validation uses the end-of-day chains.
    for table, root in [(config.SURFACE_RAW_TABLE_EOD, config.SURFACE_RAW_DIR_EOD),
                        (config.VIX_SPX_RAW_TABLE_EOD, config.VIX_SPX_RAW_DIR_EOD)]:
        con.execute(f"""CREATE OR REPLACE TABLE {table} AS SELECT * FROM
            read_parquet('{root.as_posix()}/*/*.parquet', hive_partitioning = true, union_by_name = true)""")

    rows, flag_rows = [], []
    for d, monthly, stressed in zip(dates.date, dates.monthly, dates.stressed):
        row = dict(date=d, sample="stressed" if stressed else "monthly", half_day=d in config.HALF_DAYS,
                   vix_published=vixcls.get(d),
                   vix_replicated=np.nan, difference=np.nan, vix_terms="", options=0, failure="")
        row.update({c: 0 for c in CHECKS})
        try:
            opts, exps, counts = process_date(con, d, bill_curve_on(bills, d), raw_table=config.SURFACE_RAW_TABLE_EOD)
            _, flags = run_checks(opts, exps, d, vixcls[d])
            row["options"] = len(opts)
            row["iv_options_outside"] = int(((opts.iv < config.IV_BOUNDS[0]) | (opts.iv > config.IV_BOUNDS[1])).sum())
            for check, msg in flags:
                row[check] += 1
                flag_rows.append(dict(date=d, check=check, message=msg))
        except Exception as e:   # recorded, never dropped
            row["failure"] += f"surface: {type(e).__name__}: {e}. "
            flag_rows.append(dict(date=d, check="surface_failed", message=str(e)))
        try:
            terms = bracket(d, eligible_expiries(d, tdays))
            vix, row["vix_terms"] = vix_for_date(con, d, terms, cmt, quotes=load_quotes_eod)
            row["vix_replicated"] = round(vix, 2)
            row["difference"] = round(vix - row["vix_published"], 2)
            if abs(row["difference"]) > config.VIX_MAX_GAP_POINTS:
                flag_rows.append(dict(date=d, check="vix_gap", message=f"replicated {vix:.2f} vs published "
                                      f"{row['vix_published']:.2f} ({row['difference']:+.2f})"))
        except Exception as e:   # recorded, never dropped
            row["failure"] += f"vix: {type(e).__name__}: {e}. "
            flag_rows.append(dict(date=d, check="vix_failed", message=str(e)))
        rows.append(row)
        print(f"{d} {row['sample']:8s} VIX {row['vix_replicated']:6.2f} vs {row['vix_published']:6.2f} "
              f"({row['difference']:+.2f})  flags " + " ".join(f"{c}={row[c]}" for c in CHECKS if row[c])
              + (f"  FAILED {row['failure']}" if row["failure"] else ""), flush=True)

    res = pd.DataFrame(rows)
    res["iv_options_outside"] = res.get("iv_options_outside", 0)
    res["iv_options_outside"] = res.iv_options_outside.fillna(0).astype(int)
    flags_df = pd.DataFrame(flag_rows, columns=["date", "check", "message"])
    con.execute(f"CREATE OR REPLACE TABLE {config.VALIDATION_TABLE} AS SELECT * FROM res")
    con.execute(f"CREATE OR REPLACE TABLE {config.VALIDATION_FLAGS_TABLE} AS SELECT * FROM flags_df")
    con.close()

    summary = []
    for name, g in [("monthly", res[res["sample"] == "monthly"]), ("stressed", res[res["sample"] == "stressed"]), ("all", res)]:
        a = g.difference.abs().dropna()
        summary.append(dict(sample=name, n=len(a), mean_abs_diff=round(a.mean(), 3), max_abs_diff=round(a.max(), 2),
                            within_025=f"{int((a <= 0.25).sum())}/{len(a)} ({(a <= 0.25).mean():.0%})",
                            within_05=f"{int((a <= 0.5).sum())}/{len(a)} ({(a <= 0.5).mean():.0%})",
                            mean_signed_diff=round(g.difference.mean(), 3)))
    print("\nVIX replication summary")
    print(pd.DataFrame(summary).to_string(index=False))
    print(f"\nFlag counts across {len(res)} dates (number of dates with at least one flag / total flags):")
    for c in CHECKS:
        print(f"  {c:16s} {int((res[c] > 0).sum()):3d} dates / {int(res[c].sum()):4d} flags")
    print(f"  dates with a failure: {int((res.failure != '').sum())}")
    print(f"  half-day snapshot dates (unreliable): {', '.join(map(str, res.date[res.half_day])) or 'none'}")
    print("  rate_info is information only: implied rate more than 1.5 points from the matching T-bill yield")

    fig, ax = plt.subplots(figsize=(7, 7))
    for name, color, marker in [("monthly", "#1f77b4", "o"), ("stressed", "#d62728", "^")]:
        g = res[res["sample"] == name].dropna(subset=["vix_replicated"])
        ax.scatter(g.vix_published, g.vix_replicated, color=color, marker=marker, s=36, label=f"{name} (n={len(g)})")
    hi = np.nanmax([res.vix_published.max(), res.vix_replicated.max()]) * 1.05
    lo = np.nanmin([res.vix_published.min(), res.vix_replicated.min()]) * 0.95
    ax.plot([lo, hi], [lo, hi], color="black", lw=1, ls="--", label="replicated = published (45° line)")
    ax.set(xlim=(lo, hi), ylim=(lo, hi), xlabel="published VIX close (FRED VIXCLS)",
           ylabel="replicated VIX from ThetaData EOD chains", title="VIX replication check")
    ax.legend(fontsize=9)
    n_ok = int(res.vix_replicated.notna().sum())
    fig.text(0.5, 0.01, f"{n_ok} of {len(res)} dates plotted ({int((res['sample'] == 'monthly').sum())} monthly random draws, "
             f"{int((res['sample'] == 'stressed').sum())} highest-VIX days), 2023-06 to 2026-10. Cboe method, EOD NBBO quotes.",
             ha="center", fontsize=8, wrap=True)
    fig.tight_layout(rect=(0, 0.04, 1, 1))
    fig.savefig(config.VALIDATION_FIGURE, dpi=150)
    print(f"\nFigure saved: {config.VALIDATION_FIGURE}")


if __name__ == "__main__":
    main()
