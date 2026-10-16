r"""Pull the chains for the validation dates (data/validation_dates.csv).

Per date: the SPXW chain for every expiry out to SURFACE_MAX_DTE (one request), plus the
AM-settled SPX monthly when the VIX bracket needs it (one request per expiry). Dates already
saved are skipped, so the job can stop and resume.
Run:  .venv\Scripts\python.exe pull_validation.py --estimate   (count only, no requests)
      .venv\Scripts\python.exe pull_validation.py
"""
import datetime as dt
import sys
import time

import grpc
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from thetadata.errors import NoDataFoundError

import config
import theta
from analyze_event import SanityCheckFailed
from fetch_surface import fetch_all_expiries
from pull_chains import already_saved
from vix_check import bracket, eligible_expiries, trading_days

SECONDS_PER_REQUEST = 5.5           # measured on the event-study pull


def spx_saved(trade_date, expiry):
    folder = config.VIX_SPX_RAW_DIR_EOD / f"trade_date={trade_date.isoformat()}"
    for f in folder.glob("*.parquet"):
        try:
            exps = set(pd.to_datetime(pq.read_table(f, columns=["expiration"]).column(0).to_pandas()).dt.date)
        except Exception:
            f.unlink()                                          # unreadable partial file
            continue
        if expiry in exps:
            return True
    return False


def plan():
    dates = [d.date() for d in pd.to_datetime(pd.read_csv(config.VALIDATION_DATES_CSV).date)]
    tdays = trading_days(min(dates) - dt.timedelta(days=5), max(dates) + dt.timedelta(days=70))
    spxw_todo = [d for d in dates if not already_saved(d, config.SURFACE_RAW_DIR_EOD)]
    spx_todo = []
    for d in dates:
        for expiry, root, _ in bracket(d, eligible_expiries(d, tdays)):
            if root == "SPX" and not spx_saved(d, expiry):
                spx_todo.append((d, expiry))
    return dates, spxw_todo, spx_todo


def main(estimate_only):
    dates, spxw_todo, spx_todo = plan()
    n = len(spxw_todo) + len(spx_todo) + (1 if spxw_todo else 0)
    print(f"{len(dates)} dates: {len(spxw_todo)} SPXW chains + {len(spx_todo)} SPX monthly chains "
          f"+ {1 if spxw_todo else 0} expiry listing = {n} requests, about {n * SECONDS_PER_REQUEST / 60:.0f} min")
    if estimate_only or n == 0:
        return
    client = theta.make_client()
    errors, start = [], time.monotonic()
    if spxw_todo:
        listed = set(pd.to_datetime(theta.call(client.option_list_expirations, symbol="SPXW")["expiration"]).dt.date)
    for i, d in enumerate(spxw_todo, 1):
        try:
            chain = fetch_all_expiries(client, d, listed, eod=True)
            pq.write_to_dataset(pa.Table.from_pandas(chain, preserve_index=False), root_path=config.SURFACE_RAW_DIR_EOD,
                                partition_cols=["trade_date"], existing_data_behavior="delete_matching")
        except (SanityCheckFailed, NoDataFoundError, grpc.RpcError) as e:
            errors.append((d, "SPXW", e.details() if isinstance(e, grpc.RpcError) else str(e)))
        print(f"SPXW {i}/{len(spxw_todo)} done, {(time.monotonic() - start) / 60:.1f} min", flush=True)
    for i, (d, expiry) in enumerate(spx_todo, 1):
        try:
            chain = theta.call(client.option_history_eod, start_date=d, end_date=d, symbol="SPX", expiration=expiry)
            chain["trade_date"] = d.isoformat()
            pq.write_to_dataset(pa.Table.from_pandas(chain, preserve_index=False), root_path=config.VIX_SPX_RAW_DIR_EOD,
                                partition_cols=["trade_date"], basename_template=f"exp{expiry:%Y%m%d}-{{i}}.parquet")
        except (NoDataFoundError, grpc.RpcError) as e:
            errors.append((d, f"SPX {expiry}", e.details() if isinstance(e, grpc.RpcError) else str(e)))
        print(f"SPX {i}/{len(spx_todo)} done, {(time.monotonic() - start) / 60:.1f} min", flush=True)
    for e in errors:
        print("  failed:", *e)
    print(f"Pull finished: {len(errors)} failed requests")


if __name__ == "__main__":
    main("--estimate" in sys.argv)
