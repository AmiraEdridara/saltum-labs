r"""SPXW chains for every expiry from 1 day to ~6 months, per surface date, at the options close
(16:15 ET; 13:15 on half days). Saved as Parquet only (data/raw/spxw_surface_1615), not loaded
into DuckDB. One request per date (expiration="*") plus one expiry listing, SURFACE_MAX_WORKERS
at a time. Dates already saved are skipped (resumable); a date that fails is recorded in
data/surface_fetch_errors.csv and the run continues.
Run:  .venv\Scripts\python.exe fetch_surface.py                         (all of data/surface_dates.csv)
      .venv\Scripts\python.exe fetch_surface.py 2020-03-10 2020-03-11  (only these dates)
"""
import datetime as dt
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

import config
import theta
from analyze_event import fail, snapshot_time
from pull_chains import already_saved


def fetch_all_expiries(client, trade_date, listed, eod=False):
    """Every SPXW expiry from 1 day to SURFACE_MAX_DTE. Default: quotes at the options close
    (option_at_time_quote, 16:15 ET / 13:15 on half days). eod=True: ThetaData's end-of-day
    report (used by the 51-date validation)."""
    if eod:
        chain = theta.call(
            client.option_history_eod,
            start_date=trade_date, end_date=trade_date, symbol=config.OPTION_SYMBOL,
            expiration="*", strike="*", right="both", max_dte=config.SURFACE_MAX_DTE,
        )
    else:
        # option_at_time_quote ignores max_dte and returns every listed expiry; trimmed below.
        chain = theta.call(
            client.option_at_time_quote,
            start_date=trade_date, end_date=trade_date, symbol=config.OPTION_SYMBOL,
            time_of_day=snapshot_time(trade_date).strftime("%H:%M:%S.000"),
            expiration="*", strike="*", right="both",
        )
    if chain is None or len(chain) == 0:
        fail(f"empty chain for {trade_date}")
    chain["expiration"] = pd.to_datetime(chain["expiration"]).dt.date
    # Drop the same-day expiry (it settles at this close) and anything past SURFACE_MAX_DTE.
    same_day = chain.expiration <= trade_date
    too_far = chain.expiration > trade_date + dt.timedelta(days=config.SURFACE_MAX_DTE)
    chain = chain[~same_day & ~too_far].copy()
    got = set(chain.expiration)
    horizon = trade_date + dt.timedelta(days=config.SURFACE_MAX_DTE)
    # `listed` is today's expiry list. Expiries are listed only a few weeks ahead (in 2020-22 a
    # Wednesday exactly 30 days out, or Tuesday/Thursday dailies 2-4 weeks out, were often not
    # listed yet; confirmed by requesting them alone: no data). So a missing expiry within
    # SURFACE_COVERAGE_HARD_DAYS fails the date; 15-30 days out it is only recorded as a warning.
    missing = sorted({e for e in listed if trade_date < e <= horizon} - got)
    near = [e for e in missing if (e - trade_date).days <= config.SURFACE_COVERAGE_HARD_DAYS]
    if near:
        fail(f"{trade_date}: expiries within {config.SURFACE_COVERAGE_HARD_DAYS} days missing from the response: {near}")
    warn = [e for e in missing if (e - trade_date).days <= config.SURFACE_COVERAGE_WARN_DAYS]
    chain.attrs["warnings"] = ([f"expiries {config.SURFACE_COVERAGE_HARD_DAYS + 1}-{config.SURFACE_COVERAGE_WARN_DAYS} days out "
                                f"missing from the response (probably not listed yet): {', '.join(map(str, warn))}"]
                               if warn else [])
    if missing:
        print(f"{trade_date}: {len(missing)} expiries listed today but absent on this date "
              f"(probably not yet listed then): {', '.join(str(e) for e in missing)}")
    chain["trade_date"] = trade_date.isoformat()
    print(f"{trade_date}: {len(chain)} rows, {len(got)} expiries "
          f"({min(got)} to {max(got)}); dropped {int(same_day.sum())} same-day-expiry rows")
    return chain


def main(only=None):
    """only: optional list of dates (a pilot). Default: every date in config.SURFACE_DATES.
    Appends this run's failures and warnings to SURFACE_FETCH_ERRORS_CSV and returns them."""
    run_date = dt.datetime.now().isoformat(sep=" ", timespec="seconds")
    dates = only or config.SURFACE_DATES
    # Dates whose chains are already saved (and readable) are not downloaded again.
    todo = [d for d in dates if not already_saved(d, config.SURFACE_RAW_DIR)]
    print(f"{len(dates)} dates, {len(todo)} to download, {config.SURFACE_MAX_WORKERS} at a time", flush=True)
    errors = []
    if todo:
        client = theta.make_client()
        exps = theta.call(client.option_list_expirations, symbol=config.OPTION_SYMBOL)
        listed = set(pd.to_datetime(exps["expiration"]).dt.date)
        config.SURFACE_RAW_DIR.mkdir(parents=True, exist_ok=True)

        def one(trade_date):
            chain = fetch_all_expiries(client, trade_date, listed)
            for w in chain.attrs.get("warnings", []):
                errors.append((trade_date, "warning", w))
            pq.write_to_dataset(pa.Table.from_pandas(chain, preserve_index=False),
                                root_path=config.SURFACE_RAW_DIR, partition_cols=["trade_date"],
                                existing_data_behavior="delete_matching")

        start_t, done = time.monotonic(), 0
        with ThreadPoolExecutor(max_workers=config.SURFACE_MAX_WORKERS) as pool:
            futures = {pool.submit(one, d): d for d in todo}
            for fut in as_completed(futures):
                d, done = futures[fut], done + 1
                try:
                    fut.result()
                except Exception as e:      # recorded per date; the run continues
                    reason = e.details() if hasattr(e, "details") else str(e)
                    errors.append((d, "failed", f"{type(e).__name__}: {reason}"))
                    print(f"  FAILED {d}: {errors[-1][2]}", flush=True)
                if done % 10 == 0 or done == len(todo):
                    el = time.monotonic() - start_t
                    print(f"{done}/{len(todo)} done, {el / 60:.1f} min, ~{el / done * (len(todo) - done) / 60:.0f} min left",
                          flush=True)
    errs = pd.DataFrame(errors, columns=["date", "level", "reason"])
    # One log of every run: earlier rows are kept (rows from before run_date existed have it blank).
    log = errs.astype(str).assign(run_date=run_date)
    if config.SURFACE_FETCH_ERRORS_CSV.exists():
        log = pd.concat([pd.read_csv(config.SURFACE_FETCH_ERRORS_CSV, dtype=str), log])
    log.to_csv(config.SURFACE_FETCH_ERRORS_CSV, index=False)
    saved = sum(1 for d in dates if already_saved(d, config.SURFACE_RAW_DIR))
    print(f"Fetch finished: {int((errs.level == 'failed').sum())} failed, {int((errs.level == 'warning').sum())} "
          f"warnings (see {config.SURFACE_FETCH_ERRORS_CSV}); "
          f"{saved} of {len(dates)} dates saved as Parquet under {config.SURFACE_RAW_DIR} (not loaded into DuckDB)")
    return errs


if __name__ == "__main__":
    main([dt.date.fromisoformat(a) for a in sys.argv[1:]] or None)
