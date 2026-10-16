"""Step 2: build the list of dates to study.

Every trading day D whose previous trading day is inside the data window gets one row
per event on D (CPI, FOMC, NFP), or one row with event_type NONE. Trading days come
from FRED SP500 (it has no value on market holidays).

Run:  .venv\\Scripts\\python.exe build_dates.py
"""
import datetime as dt

import duckdb
import pandas as pd

import config
from analyze_event import fred_closes


def trading_days():
    closes = fred_closes(start=config.DATA_START, end=dt.date.today())
    return sorted(closes.index)


def build_date_list(days):
    events = pd.read_csv(config.EVENT_CALENDAR, parse_dates=["date"])
    events["date"] = events["date"].dt.date
    # D needs a chain on the previous trading day, which must be inside the window.
    prev = {d: p for p, d in zip(days[:-1], days[1:]) if p >= config.DATA_START}
    first, last = min(prev), max(prev)

    in_window = events[(events.date >= first) & (events.date <= last)]
    market_closed = in_window[~in_window.date.isin(prev.keys())]
    on_trading_days = in_window[in_window.date.isin(prev.keys())]

    rows = [(d, e) for d, e in zip(on_trading_days.date, on_trading_days.event_type)]
    event_days = set(on_trading_days.date)
    rows += [(d, "NONE") for d in prev if d not in event_days]
    out = pd.DataFrame(rows, columns=["date", "event_type"])
    out["trade_date"] = out["date"].map(prev)
    out["half_day"] = out["trade_date"].isin(config.HALF_DAYS)   # chain from an early-close session: unreliable
    return out.sort_values(["date", "event_type"]).reset_index(drop=True), market_closed


def main():
    days = trading_days()
    date_list, market_closed = build_date_list(days)
    config.DATE_LIST_CSV.parent.mkdir(parents=True, exist_ok=True)
    date_list.to_csv(config.DATE_LIST_CSV, index=False)
    con = duckdb.connect(str(config.DUCKDB_PATH))
    con.register("date_list_df", date_list)   # distinct name: "FROM date_list" would read the old table
    con.execute(f"CREATE OR REPLACE TABLE {config.DATE_LIST_TABLE} AS SELECT * FROM date_list_df")
    con.close()

    print(f"Window: event dates {date_list.date.min()} to {date_list.date.max()} "
          f"(chains from {date_list.trade_date.min()} to {date_list.trade_date.max()})")
    print(f"Distinct dates: {date_list.date.nunique()}")
    print(date_list.event_type.value_counts().rename("rows").to_string())
    shared = date_list[date_list.event_type != "NONE"].groupby("date").event_type.apply("+".join)
    print("Days with two events:", ", ".join(f"{d} ({e})" for d, e in shared[shared.str.contains(r"\+")].items()) or "none")
    print("Release days skipped because the market was closed:",
          ", ".join(f"{d} ({e})" for d, e in zip(market_closed.date, market_closed.event_type)) or "none")
    print(f"Saved {config.DATE_LIST_CSV} and table '{config.DATE_LIST_TABLE}'")


if __name__ == "__main__":
    main()
