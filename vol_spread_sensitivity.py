r"""Sensitivity of the surface to the vol-spread limit (SURFACE_MAX_VOL_SPREAD_POINTS).

From saved 16:15 chains only: rebuilds each of the original 8 surfaces (SURFACE_DATES_ORIGINAL) in memory at limits of 2, 3
and 4 vol points with the same code as vol_surface.py and vol_page.py, and reports what the
page would draw. Writes no tables and does not change the setting (which stays at 3).
Run:  .venv\Scripts\python.exe vol_spread_sensitivity.py
"""
import duckdb
import numpy as np
import pandas as pd

import config
import vol_page
from vol_surface import process_date, run_checks

LIMITS = [2.0, 3.0, 4.0]
NO_CURVE = [(30, np.nan), (365, np.nan)]   # rates only feed the information column bill_rate


def page_frames(opts, exps):
    """The two frames vol_page.load() would read from the tables."""
    o = opts.rename(columns={"log_moneyness": "k"})[["trade_date", "expiration", "strike", "forward", "t_years", "k", "iv"]].copy()
    e = exps[["trade_date", "expiration", "dte", "atm_iv"]].copy()
    for f in (o, e):
        for col in ("trade_date", "expiration"):
            f[col] = f[col].astype(str).str[:10]
    return o, e


def main():
    setting = config.SURFACE_MAX_VOL_SPREAD_POINTS
    con = duckdb.connect(str(config.DUCKDB_PATH), read_only=True)
    rows = []
    for limit in LIMITS:
        config.SURFACE_MAX_VOL_SPREAD_POINTS = limit
        for d in config.SURFACE_DATES_ORIGINAL:   # pinned to the original 8 dates
            opts, exps, _ = process_date(con, d, NO_CURVE)
            atm30, _ = run_checks(opts, exps, d, float("nan"))
            o, e = page_frames(opts, exps)
            built = vol_page.build_date(str(d), o, e)
            rows.append(dict(
                date=str(d), limit=limit, options=len(opts), atm30=round(atm30 * 100, 2),
                drawn=built is not None,
                range=built["range_label"] if built else "not drawn",
                expiries=len(built["expiries"]) if built else 0,
                fit_mean=built["fit_mean"] if built else np.nan, fit_max=built["fit_max"] if built else np.nan,
                tv_drops=built["tv_drops"] if built else np.nan))
    con.close()
    config.SURFACE_MAX_VOL_SPREAD_POINTS = setting
    res = pd.DataFrame(rows).sort_values(["date", "limit"])
    pd.set_option("display.width", 220)
    print(res.to_string(index=False))

    print("\nMaterial changes between limits 2, 3 and 4 (30-day ATM vol > 0.1 vol points, a different "
          "delta range, or drawn vs not drawn):")
    for d, g in res.groupby("date"):
        notes = []
        spread = g.atm30.max() - g.atm30.min()
        if spread > 0.1:
            notes.append(f"30-day ATM vol varies by {spread:.2f} vol points")
        if g["range"].nunique() > 1:
            notes.append("delta range differs: " + "; ".join(f"{l:g}: {r}" for l, r in zip(g.limit, g["range"])))
        if g.drawn.nunique() > 1:
            notes.append("drawn at " + ", ".join(f"{l:g}" for l in g.limit[g.drawn]) + " only")
        print(f"  {d}: " + ("; ".join(notes) if notes else "none"))


if __name__ == "__main__":
    main()
