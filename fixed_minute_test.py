r"""Test only: do fixed-minute quotes (Value subscription) fix the stressed-day gaps?

Pulls SPXW quotes at 16:00 and 16:15 ET (13:00 / 13:15 on a half day) with option_at_time_quote,
plus the SPX monthly the VIX bracket needs at 16:15, into NEW raw folders and NEW tables.
End-of-day data, tables and results are not touched. Same cleaning and VIX method as before.

Run:  .venv\Scripts\python.exe fixed_minute_test.py --estimate   (dates + request count, no requests)
      .venv\Scripts\python.exe fixed_minute_test.py --pull       (resumable)
      .venv\Scripts\python.exe fixed_minute_test.py              (compute and report from saved data)
"""
import datetime as dt
import math
import sys
import time
from zoneinfo import ZoneInfo

import duckdb
import grpc
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from thetadata.errors import NoDataFoundError

import config
import theta
from analyze_event import SanityCheckFailed, clean_chain, fred_closes
from vix_check import bracket, eligible_expiries, load_rates, trading_days, vix_for_date
from vol_surface import forward_and_df, greeks, implied_vol

TZ = ZoneInfo(config.MARKET_TZ)
SECONDS_PER_REQUEST = 12.0          # at-time quotes with expiration="*" are slower than EOD


# ---------- dates (fixed once written) ----------
def test_dates():
    if config.FM_DATES_CSV.exists():
        return pd.read_csv(config.FM_DATES_CSV, parse_dates=["date"]).assign(date=lambda x: x.date.dt.date)
    v = pd.read_csv(config.VALIDATION_DATES_CSV, parse_dates=["date"]).assign(date=lambda x: x.date.dt.date)
    stressed = v[v.stressed].date.tolist()
    calm_pool = [d for d in v[v.monthly].date if d not in config.HALF_DAYS]
    rng = np.random.default_rng(config.FM_SEED)
    calm = sorted(rng.choice(calm_pool, size=config.FM_CALM_COUNT, replace=False).tolist())
    rows = [(d, "stressed") for d in stressed] + [(dt.date(2024, 12, 24), "half_day")] + [(d, "calm") for d in calm]
    out = pd.DataFrame(rows, columns=["date", "group"]).sort_values("date")
    out.to_csv(config.FM_DATES_CSV, index=False)
    return out


def times_for(d):
    return config.FM_TIMES_HALF_DAY if d in config.HALF_DAYS else config.FM_TIMES


def vix_terms(d, tdays):
    return bracket(d, eligible_expiries(d, tdays))


# ---------- pull ----------
def saved(root, d, snap, expiry=None):
    folder = root / f"trade_date={d.isoformat()}" / f"snap={snap[:5].replace(':', '')}"
    if expiry is None:
        return any(folder.glob("*.parquet"))
    return any(folder.glob(f"exp{expiry:%Y%m%d}-*.parquet"))


def plan(dates):
    tdays = trading_days(min(dates) - dt.timedelta(days=5), max(dates) + dt.timedelta(days=70))
    todo = []
    for d in dates:
        for snap in times_for(d):
            if not saved(config.FM_SPXW_RAW_DIR, d, snap):
                todo.append(("SPXW", d, snap, None))
        close = times_for(d)[1]
        for expiry, root, _ in vix_terms(d, tdays):
            if root == "SPX" and not saved(config.FM_SPX_RAW_DIR, d, close, expiry):
                todo.append(("SPX", d, close, expiry))
    return todo


def pull(todo):
    client = theta.make_client()
    start, failed = time.monotonic(), []
    for i, (root, d, snap, expiry) in enumerate(todo, 1):
        try:
            if root == "SPXW":
                df = theta.call(client.option_at_time_quote, symbol="SPXW", start_date=d, end_date=d, time_of_day=snap,
                                expiration="*", max_dte=config.FM_MAX_DTE)
                base, dest = "part-{i}.parquet", config.FM_SPXW_RAW_DIR
            else:
                df = theta.call(client.option_at_time_quote, symbol="SPX", start_date=d, end_date=d, time_of_day=snap,
                                expiration=expiry)
                base, dest = f"exp{expiry:%Y%m%d}-{{i}}.parquet", config.FM_SPX_RAW_DIR
            df["trade_date"] = d.isoformat()
            df["snap"] = snap[:5].replace(":", "")
            pq.write_to_dataset(pa.Table.from_pandas(df, preserve_index=False), root_path=dest,
                                partition_cols=["trade_date", "snap"], basename_template=base)
            print(f"{i}/{len(todo)} {root} {d} {snap[:5]} {expiry or '*'}: {len(df)} rows, "
                  f"{(time.monotonic() - start) / 60:.1f} min", flush=True)
        except (NoDataFoundError, grpc.RpcError) as e:
            failed.append((root, d, snap, e.details() if isinstance(e, grpc.RpcError) else str(e)))
            print(f"{i}/{len(todo)} {root} {d} {snap[:5]}: FAILED {failed[-1][3]}", flush=True)
    print(f"Pull finished: {len(failed)} failed requests")


# ---------- compute ----------
def load_tables(con):
    for table, root in [(config.FM_SPXW_TABLE, config.FM_SPXW_RAW_DIR), (config.FM_SPX_TABLE, config.FM_SPX_RAW_DIR)]:
        con.execute(f"""CREATE OR REPLACE TABLE {table} AS SELECT * FROM read_parquet(
            '{root.as_posix()}/*/*/*.parquet', hive_partitioning = true, union_by_name = true)""")


def fm_quotes(snap):
    """Quote loader for vix_for_date reading the fixed-minute tables at one snapshot time."""
    def load(con, root, d, expiry):
        table = config.FM_SPX_TABLE if root == "SPX" else config.FM_SPXW_TABLE
        return con.execute(f"""SELECT strike, upper("right") AS "right", bid, ask FROM {table}
            WHERE CAST(trade_date AS DATE) = ? AND CAST(snap AS VARCHAR) = ? AND CAST(expiration AS DATE) = ?""",
                           [d, snap, expiry]).df()
    return load


def eod_quotes(con, d, expiry):
    return con.execute(f"""SELECT strike, upper("right") AS "right", bid, ask FROM {config.SURFACE_RAW_TABLE_EOD}
        WHERE CAST(trade_date AS DATE) = ? AND CAST(expiration AS DATE) = ?""", [d, expiry]).df()


def parity_forward(q):
    """Event-study forward: strike with the smallest |C - P| among clean pairs, F = K + C - P."""
    c = clean_chain(q)
    w = c.pivot_table(index="strike", columns="right", values="mid").reindex(columns=["CALL", "PUT"]).dropna()
    if w.empty:
        return np.nan
    k = (w.CALL - w.PUT).abs().idxmin()
    return k + w.CALL[k] - w.PUT[k]


def quality(q, d, expiry, snap_time):
    """Median spread (% of mid) of OTM options within 10% of the forward, and the lowest call delta
    and put delta (magnitude) reached after cleaning; same forward/vol method as the surface."""
    c = clean_chain(q)
    F, df, _ = forward_and_df(c)
    if F != F:
        return np.nan, np.nan, np.nan
    t = (dt.datetime.combine(expiry, config.EXPIRY_TIME, TZ) - dt.datetime.combine(d, snap_time, TZ)).total_seconds() / (365 * 86400)
    otm = c[((c.right == "PUT") & (c.strike < F)) | ((c.right == "CALL") & (c.strike >= F))].copy()
    near = otm[(otm.strike / F - 1).abs() <= 0.10]
    spread = ((near.ask - near.bid) / near.mid).median() * 100
    is_call = (otm.right == "CALL").values
    iv = implied_vol(otm.mid.values, F, otm.strike.values, t, df, is_call)
    ok = ~np.isnan(iv)
    delta = greeks(F, otm.strike.values[ok], t, df, iv[ok], is_call[ok])[0]
    calls, puts = delta[is_call[ok]], delta[~is_call[ok]]
    return spread, (calls.min() if len(calls) else np.nan), (np.abs(puts).min() if len(puts) else np.nan)


def nearest_30(con, table_sql, d):
    exps = [pd.Timestamp(e).date() for (e,) in con.execute(table_sql, [d]).fetchall()]
    exps = [e for e in exps if e > d]
    return min(exps, key=lambda e: abs((e - d).days - 30)) if exps else None


def compute(dates):
    con = duckdb.connect(str(config.DUCKDB_PATH))
    load_tables(con)
    start, end = min(dates.date) - dt.timedelta(days=10), max(dates.date)
    cmt, vixcls, spx = load_rates(start, end), fred_closes(start, end, config.VIX_SERIES), fred_closes(start, end)
    tdays = trading_days(start, end + dt.timedelta(days=70))
    eod = con.execute(f"SELECT date, vix_replicated FROM {config.VALIDATION_TABLE}").df()
    eod_vix = dict(zip(pd.to_datetime(eod.date).dt.date, eod.vix_replicated))
    vix_rows, fwd_rows, q_rows = [], [], []
    for d, group in zip(dates.date, dates.group):
        cash_snap, close_snap = (s[:5].replace(":", "") for s in times_for(d))
        close_time = dt.time.fromisoformat(times_for(d)[1][:8])
        cash_time = dt.time.fromisoformat(times_for(d)[0][:8])
        # 1. VIX from the options-close snapshot
        try:
            fm_vix, _ = vix_for_date(con, d, vix_terms(d, tdays), cmt, quotes=fm_quotes(close_snap), calc_time=close_time)
            fm_note = ""
        except Exception as e:                                   # recorded, never dropped
            fm_vix, fm_note = np.nan, f"{type(e).__name__}: {e}"
        pub = vixcls.get(d, np.nan)
        e_v = eod_vix.get(d, np.nan)
        vix_rows.append(dict(date=d, group=group, published=pub, eod=round(e_v, 2) if e_v == e_v else np.nan,
                             fixed_minute=round(fm_vix, 2) if fm_vix == fm_vix else np.nan,
                             eod_gap=round(e_v - pub, 2) if e_v == e_v else np.nan,
                             fm_gap=round(fm_vix - pub, 2) if fm_vix == fm_vix else np.nan, note=fm_note))
        # 2. forward at the cash-close snapshot vs FRED close, expiry nearest 30 days
        exp30 = nearest_30(con, f"SELECT DISTINCT expiration FROM {config.FM_SPXW_TABLE} WHERE CAST(trade_date AS DATE) = ?", d)
        fmq = fm_quotes(cash_snap)(con, "SPXW", d, exp30) if exp30 else pd.DataFrame()
        eoq = eod_quotes(con, d, exp30) if exp30 else pd.DataFrame()
        close = spx.get(d, np.nan)
        f_fm = parity_forward(fmq) if len(fmq) else np.nan
        f_eod = parity_forward(eoq) if len(eoq) else np.nan
        fwd_rows.append(dict(date=d, group=group, expiry=exp30, fred_close=close,
                             eod_forward=round(f_eod, 2), eod_gap_pct=round((f_eod / close - 1) * 100, 3),
                             fm_forward=round(f_fm, 2), fm_gap_pct=round((f_fm / close - 1) * 100, 3)))
        # 3. quote quality, same expiry: fixed minute (cash close) vs end of day
        fs, fc, fp = quality(fmq, d, exp30, cash_time) if len(fmq) else (np.nan,) * 3
        es, ec, ep = quality(eoq, d, exp30, config.SNAPSHOT_TIME) if len(eoq) else (np.nan,) * 3
        q_rows.append(dict(date=d, group=group, expiry=exp30, eod_spread_pct=round(es, 1), fm_spread_pct=round(fs, 1),
                           eod_min_call_delta=round(ec, 3), fm_min_call_delta=round(fc, 3),
                           eod_min_put_delta=round(ep, 3), fm_min_put_delta=round(fp, 3)))
    vix_t, fwd_t, q_t = pd.DataFrame(vix_rows), pd.DataFrame(fwd_rows), pd.DataFrame(q_rows)
    con.register("vix_df", vix_t); con.register("fwd_df", fwd_t); con.register("q_df", q_t)
    con.execute("CREATE OR REPLACE TABLE fixed_minute_vix AS SELECT * FROM vix_df")
    con.execute("CREATE OR REPLACE TABLE fixed_minute_forward AS SELECT * FROM fwd_df")
    con.execute("CREATE OR REPLACE TABLE fixed_minute_quality AS SELECT * FROM q_df")
    con.close()

    pd.set_option("display.width", 220)
    print("1. VIX: published vs end-of-day replication vs fixed-minute (options close) replication")
    print(vix_t.to_string(index=False))
    print("\nSummary of |gap| to published VIX (n = dates computed)")
    summ = []
    for g in ["stressed", "calm", "half_day"]:
        s = vix_t[vix_t.group == g]
        for col, name in [("eod_gap", "end of day"), ("fm_gap", "fixed minute")]:
            a = s[col].abs().dropna()
            summ.append(dict(group=g, snapshot=name, n=len(a), mean_abs_gap=round(a.mean(), 3) if len(a) else np.nan,
                             max_abs_gap=round(a.max(), 2) if len(a) else np.nan,
                             within_05=f"{int((a <= 0.5).sum())}/{len(a)}"))
    print(pd.DataFrame(summ).to_string(index=False))
    print("\n2. Forward (expiry nearest 30 days) vs FRED S&P 500 close, % gap: end of day vs fixed minute (cash close)")
    print(fwd_t.to_string(index=False))
    print("\n3. Quote quality, expiry nearest 30 days: end of day vs fixed minute (cash close)")
    print(q_t.to_string(index=False))


def main(args):
    dates = test_dates()
    print(f"Test dates ({len(dates)}): " + ", ".join(f"{d} [{g}]" for d, g in zip(dates.date, dates.group)))
    if "--estimate" in args or "--pull" in args:
        todo = plan(list(dates.date))
        n_spxw = sum(1 for t in todo if t[0] == "SPXW"); n_spx = len(todo) - n_spxw
        print(f"Requests: {n_spxw} SPXW at-time + {n_spx} SPX at-time = {len(todo)}, "
              f"about {len(todo) * SECONDS_PER_REQUEST / 60:.0f} min (one at a time)")
        if "--pull" in args and todo:
            pull(todo)
        return
    compute(dates)


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except SanityCheckFailed as e:
        raise SystemExit(f"SANITY CHECK FAILED: {e}")
