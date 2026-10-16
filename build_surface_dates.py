r"""Write data/surface_dates.csv: every CPI, FOMC and payrolls day in the date list, the trading
day before each, the original 8 surface dates, and the Stage 5b ordinary-day control pairs. fetch_surface.py and vol_surface.py read it.
Run:  .venv\Scripts\python.exe build_surface_dates.py
"""
import pandas as pd

import config


def main():
    d = pd.read_csv(config.DATE_LIST_CSV, parse_dates=["date", "trade_date"])
    ev = d[d.event_type != "NONE"]
    days = set(ev.date.dt.date) | set(ev.trade_date.dt.date) | set(config.SURFACE_DATES_ORIGINAL)
    control = config.PROJECT_DIR / "data" / "control_pairs.csv"      # Stage 5b ordinary-day pairs
    if control.exists():
        c = pd.read_csv(control)
        days |= set(pd.to_datetime(c.day1).dt.date) | set(pd.to_datetime(c.day2).dt.date)
    out = pd.DataFrame({"date": sorted(days)})
    out.to_csv(config.SURFACE_DATES_CSV, index=False)
    print(f"Wrote {config.SURFACE_DATES_CSV}: {len(out)} dates ({out.date.min()} to {out.date.max()})")


if __name__ == "__main__":
    main()
