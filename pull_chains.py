"""Step 3: pull the SPXW chain at the options close (16:15 ET, 13:15 on half days) for every
date in the date list.

For each date D: the chain on the trading day before D, for the expiry on D. Up to
MAX_CONCURRENT_REQUESTS requests run at once (theta.call limits, spaces and retries them).
Dates whose Parquet partition already exists are skipped, so the job can be stopped and resumed.

Run:  .venv\\Scripts\\python.exe pull_chains.py --estimate  (count only, no requests)
      .venv\\Scripts\\python.exe pull_chains.py            (all dates)
      .venv\\Scripts\\python.exe pull_chains.py --limit 5  (first 5 missing dates, for testing)
"""
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import grpc
import pandas as pd
import pyarrow.parquet as pq
from thetadata.errors import NoDataFoundError

import config
import theta
from analyze_event import SanityCheckFailed
from fetch_chain import fetch_snapshot_chain, load_duckdb, save_parquet


def already_saved(trade_date, root=config.RAW_DIR):
    """True only if the partition holds readable Parquet; a half-written one is removed and re-pulled."""
    folder = root / f"trade_date={trade_date.isoformat()}"
    files = list(folder.glob("*.parquet"))
    try:
        for f in files:
            pq.read_metadata(f)
        return bool(files)
    except Exception:
        shutil.rmtree(folder)
        return False


SECONDS_PER_REQUEST = 5.5          # measured: one-expiry at-time request


def main(limit=None, estimate=False):
    dates = pd.read_csv(config.DATE_LIST_CSV, parse_dates=["date", "trade_date"])
    dates = dates.drop_duplicates("date")[["date", "trade_date"]]
    todo = [(d.date(), t.date()) for d, t in zip(dates.date, dates.trade_date) if not already_saved(t.date())]
    saved = len(dates) - len(todo)
    if limit:
        todo = todo[:limit]
    n = len(todo) + (1 if todo else 0)
    print(f"{len(dates)} dates in list, {saved} already saved, {len(todo)} to pull: "
          f"{n} requests, about {n * SECONDS_PER_REQUEST / config.MAX_CONCURRENT_REQUESTS / 60:.0f} min "
          f"with {config.MAX_CONCURRENT_REQUESTS} at a time")
    if estimate or not todo:
        return

    client = theta.make_client()
    exps = theta.call(client.option_list_expirations, symbol=config.OPTION_SYMBOL)
    listed = set(pd.to_datetime(exps["expiration"]).dt.date)
    config.RAW_DIR.mkdir(parents=True, exist_ok=True)

    def one(date, trade_date):
        if date not in listed:
            raise SanityCheckFailed(f"{config.OPTION_SYMBOL} has no expiration on {date}")
        save_parquet(fetch_snapshot_chain(client, trade_date, date))

    errors, start, done = [], time.monotonic(), 0
    with ThreadPoolExecutor(max_workers=config.MAX_CONCURRENT_REQUESTS) as pool:
        futures = {pool.submit(one, d, t): (d, t) for d, t in todo}
        for fut in as_completed(futures):
            date, trade_date = futures[fut]
            done += 1
            try:
                fut.result()
            except (SanityCheckFailed, NoDataFoundError, grpc.RpcError) as e:
                reason = e.details() if isinstance(e, grpc.RpcError) else str(e)
                errors.append((date, trade_date, reason))
                print(f"  skipped {date}: {reason}", flush=True)
            if done % 25 == 0 or done == len(todo):
                elapsed = time.monotonic() - start
                print(f"{done}/{len(todo)} done, {elapsed / 60:.1f} min elapsed, "
                      f"~{elapsed / done * (len(todo) - done) / 60:.0f} min left", flush=True)

    # Rewritten every run: dates that failed earlier are retried, so only this run's failures matter.
    pd.DataFrame(errors, columns=["date", "trade_date", "reason"]).to_csv(config.PULL_ERRORS, index=False)
    con = load_duckdb()
    n = con.execute(f"SELECT count(DISTINCT trade_date) FROM {config.DUCKDB_TABLE}").fetchone()[0]
    con.close()
    print(f"Pull finished: {len(errors)} failed (see {config.PULL_ERRORS}); {n} trade dates in DuckDB")


if __name__ == "__main__":
    main(int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[1] == "--limit" else None, "--estimate" in sys.argv)
