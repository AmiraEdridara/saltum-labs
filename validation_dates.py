r"""Choose the validation dates by rule, once, before looking at any results.

One random trading day per calendar month of the data window (fixed seed), plus the 10
trading days with the highest VIX close (FRED VIXCLS). Written to data/validation_dates.csv;
if that file exists it is only printed, never rebuilt.
Run:  .venv\Scripts\python.exe validation_dates.py
"""
import numpy as np
import pandas as pd

import config
from analyze_event import fred_closes


def build():
    window = pd.read_csv(config.DATE_LIST_CSV, parse_dates=["trade_date"])
    days = sorted(window.trade_date.dt.date.unique())          # chain dates in the data window
    rng = np.random.default_rng(config.VALIDATION_SEED)
    by_month = pd.Series(days).groupby([d.strftime("%Y-%m") for d in days])
    monthly = {g.iloc[rng.integers(len(g))] for _, g in by_month}
    vix = fred_closes(days[0], days[-1], config.VIX_SERIES)
    vix = vix[vix.index.isin(days)]
    stressed = set(vix.nlargest(config.VALIDATION_TOP_VIX).index)
    rows = [dict(date=d, monthly=d in monthly, stressed=d in stressed, vix_close=vix.get(d))
            for d in sorted(monthly | stressed)]
    return pd.DataFrame(rows)


def main():
    if config.VALIDATION_DATES_CSV.exists():
        dates = pd.read_csv(config.VALIDATION_DATES_CSV)
        print(f"Using the existing list {config.VALIDATION_DATES_CSV} (not rebuilt)")
    else:
        dates = build()
        dates.to_csv(config.VALIDATION_DATES_CSV, index=False)
        print(f"Wrote {config.VALIDATION_DATES_CSV}")
    print(dates.to_string(index=False))
    print(f"{len(dates)} dates: {int(dates.monthly.sum())} monthly, {int(dates.stressed.sum())} stressed, "
          f"{int((dates.monthly & dates.stressed).sum())} both")


if __name__ == "__main__":
    main()
