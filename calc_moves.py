"""Step 4: implied and actual move for every date, same method as the single-event report.

Writes one row per date to DuckDB table event_moves. A date that fails a sanity
check (or has no saved chain) is logged to data/skipped_dates.csv and skipped.

Run after pull_chains.py:  .venv\\Scripts\\python.exe calc_moves.py
"""
import duckdb
import pandas as pd

import config
from analyze_event import SanityCheckFailed, fred_closes, measure


def main():
    dates = pd.read_csv(config.DATE_LIST_CSV, parse_dates=["date", "trade_date"])
    # One row per date; a day with two events gets a combined label such as "CPI+FOMC".
    per_date = (dates.groupby(["date", "trade_date"]).event_type
                .apply(lambda s: "+".join(sorted(s))).reset_index())
    closes = fred_closes(start=config.DATA_START, end=per_date.date.max().date())

    results, skipped = [], []
    for date, trade_date, event_type in zip(per_date.date.dt.date, per_date.trade_date.dt.date,
                                            per_date.event_type):
        try:
            m = measure(trade_date, date, closes[trade_date], closes[date])
        except SanityCheckFailed as e:
            skipped.append((date, event_type, str(e)))
            continue
        results.append(dict(date=date, event_type=event_type, trade_date=trade_date,
                            half_day=trade_date in config.HALF_DAYS, **m))

    out = pd.DataFrame(results)[["date", "event_type", "trade_date", "strike", "forward", "straddle",
                                 "implied_move", "actual_move", "ratio", "quotes_kept", "after_hours_gap", "half_day"]]
    con = duckdb.connect(str(config.DUCKDB_PATH))
    con.execute(f"CREATE OR REPLACE TABLE {config.RESULTS_TABLE} AS SELECT * FROM out")
    con.close()
    pd.DataFrame(skipped, columns=["date", "event_type", "reason"]).to_csv(config.SKIPPED_LOG, index=False)
    print(f"{len(out)} dates measured, {len(skipped)} skipped (see {config.SKIPPED_LOG})")
    for d, e, r in skipped:
        print(f"  skipped {d} [{e}]: {r}")


if __name__ == "__main__":
    main()
