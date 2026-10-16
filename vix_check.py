"""Replicate the VIX close from our EOD chains, following the Cboe VIX Methodology (v6.0,
revised 2026-02-26) and the Cboe Volatility Index Mathematics Methodology, then compare
with FRED VIXCLS.

Eligible expiries: AM-settled SPX monthlies, plus PM-settled SPXW expiring at the end of
the week (Fridays), excluding SPXW on the same date as an SPX monthly. Bracket method:
near term = last eligible expiry with <= 30 days, next term = first one after it.

Needs one ThetaData request (the SPX monthly that is the next term, both snapshot dates),
skipped if already saved. Treasury CMT rates come from FRED.
Run:  .venv\\Scripts\\python.exe vix_check.py
"""
import datetime as dt
import math
from zoneinfo import ZoneInfo

import duckdb
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

import config
import theta
from analyze_event import SanityCheckFailed, fail, fred_closes, snapshot_time

TZ = ZoneInfo(config.MARKET_TZ)
M365 = 365 * 1440


def third_friday(year, month):
    first = dt.date(year, month, 1)
    return first + dt.timedelta(days=(4 - first.weekday()) % 7 + 14)


def eligible_expiries(trade_date, trading_days, horizon_days=60):
    """(expiry, root, settlement time) candidates per the VIX methodology, from the trading
    calendar: SPX monthlies (third Friday, or the trading day before if it is a holiday,
    AM-settled) and SPXW expiring at the end of each week (the week's last trading day,
    PM-settled), excluding SPXW on the same date as an SPX monthly."""
    days = sorted(d for d in trading_days if trade_date < d <= trade_date + dt.timedelta(days=horizon_days))
    week_end = {}
    for d in days:
        week_end[d.isocalendar()[:2]] = d                      # last trading day of each ISO week
    out = []
    for e in sorted(week_end.values()):
        tf = third_friday(e.year, e.month)
        monthly = max(d for d in trading_days if d <= tf)      # third Friday, or the day before if a holiday
        if e == monthly:
            out.append((e, "SPX", dt.time(9, 30)))
        else:
            out.append((e, "SPXW", dt.time(16, 0)))
    return out


def bracket(trade_date, candidates):
    """Near term: eligible expiry with days to expiry <= 30; next term: the one after it."""
    days = [(e - trade_date).days for e, _, _ in candidates]
    near_idx = max(i for i, d in enumerate(days) if d <= config.VIX_CONSTANT_MATURITY_DAYS)
    return candidates[near_idx], candidates[near_idx + 1]


def minutes_to_expiry(trade_date, expiry, settle_time, calc_time=None):
    start = dt.datetime.combine(trade_date, calc_time or config.VIX_CALC_TIME, TZ)
    end = dt.datetime.combine(expiry, settle_time, TZ)
    return math.floor((end - start).total_seconds() / 60)


def natural_cubic_spline(x, y, xq):
    """Natural cubic spline through (x, y), evaluated at xq."""
    n = len(x) - 1
    h = np.diff(x)
    A = np.zeros((n + 1, n + 1))
    b = np.zeros(n + 1)
    A[0, 0] = A[n, n] = 1.0
    for i in range(1, n):
        A[i, i - 1], A[i, i], A[i, i + 1] = h[i - 1], 2 * (h[i - 1] + h[i]), h[i]
        b[i] = 3 * ((y[i + 1] - y[i]) / h[i] - (y[i] - y[i - 1]) / h[i - 1])
    c = np.linalg.solve(A, b)
    i = min(max(np.searchsorted(x, xq) - 1, 0), n - 1)
    d = xq - x[i]
    bi = (y[i + 1] - y[i]) / h[i] - h[i] * (2 * c[i] + c[i + 1]) / 3
    di = (c[i + 1] - c[i]) / (3 * h[i])
    return y[i] + bi * d + c[i] * d ** 2 + di * d ** 3


def cboe_rate(cmt_days, cmt_yields, days):
    """Bounded cubic spline on CMT (BEY, %) -> APY -> continuously compounded rate."""
    x, y = np.array(cmt_days, float), np.array(cmt_yields, float)
    bey = natural_cubic_spline(x, y, days)
    if days < x[0]:
        up = [j for j in range(1, len(y)) if y[j] >= y[0]]
        dn = [j for j in range(1, len(y)) if y[j] <= y[0]]
        m_lo = (y[up[0]] - y[0]) / (x[up[0]] - x[0]) if up else 0.0
        m_hi = (y[dn[0]] - y[0]) / (x[dn[0]] - x[0]) if dn else 0.0
        lo, hi = m_lo * days + (y[0] - m_lo * x[0]), m_hi * days + (y[0] - m_hi * x[0])
    else:
        j = np.searchsorted(x, days) - 1
        lo, hi = min(y[j], y[j + 1]), max(y[j], y[j + 1])
    bey = min(max(bey, min(lo, hi)), max(lo, hi)) / 100
    apy = (1 + bey / 2) ** 2 - 1
    return math.log(1 + apy)


def single_term_variance(q, T, R):
    """Cboe single-term variance from one expiry's quotes (columns strike, right, bid, ask)."""
    # A quote with ask = 0 (so bid = 0 too) means no market: treat it as a null quote, which
    # Cboe excludes from the forward search and removes before the strip walk.
    q = q.dropna(subset=["bid", "ask"])
    q = q[q.ask > 0]
    calls = q[q.right == "CALL"].set_index("strike").sort_index()
    puts = q[q.right == "PUT"].set_index("strike").sort_index()
    both = calls[["bid", "ask"]].join(puts[["bid", "ask"]], lsuffix="_c", rsuffix="_p", how="inner")
    ok = both[(both.bid_c <= both.ask_c) & (both.bid_p <= both.ask_p)]
    cmid, pmid = (ok.bid_c + ok.ask_c) / 2, (ok.bid_p + ok.ask_p) / 2
    diff = (cmid - pmid).abs()
    atm = diff[diff == diff.min()].index.min()          # ties -> lowest strike
    F = atm + math.exp(R * T) * (cmid[atm] - pmid[atm])
    k0 = max(k for k in both.index if k <= F)
    if k0 not in ok.index:
        fail(f"K0 {k0} put or call quote is null or crossed: VIX cannot be calculated")

    def walk(side, strikes):
        chosen, prev_zero = [], False
        for k in strikes:
            bid, ask = side.loc[k, "bid"], side.loc[k, "ask"]
            if bid == 0 or ask == 0:
                if prev_zero:
                    break                                   # two consecutive zero bids/asks
                prev_zero = True
                continue
            prev_zero = False
            chosen.append((k, (bid + ask) / 2))
        return chosen

    sel = walk(puts, [k for k in puts.index[::-1] if k < k0])[::-1]
    sel += [(k0, (cmid[k0] + pmid[k0]) / 2)]
    sel += walk(calls, [k for k in calls.index if k > k0])
    K = np.array([k for k, _ in sel])
    Q = np.array([v for _, v in sel])
    dK = np.empty_like(K)
    dK[1:-1] = (K[2:] - K[:-2]) / 2
    dK[0], dK[-1] = K[1] - K[0], K[-1] - K[-2]
    var = 2 / T * np.sum(dK / K ** 2 * math.exp(R * T) * Q) - (F / k0 - 1) ** 2 / T
    return var, F, k0, len(sel), K.min(), K.max()


def _rebuild_spx_table(con):
    con.execute(f"""CREATE OR REPLACE TABLE {config.VIX_SPX_RAW_TABLE} AS SELECT * FROM
        read_parquet('{config.VIX_SPX_RAW_DIR.as_posix()}/*/*.parquet', hive_partitioning = true, union_by_name = true)""")


def fetch_spx_if_needed(pairs):
    """Pull the SPX monthly quotes at the options close for each (date, expiry) not yet saved.
    One small request per pair; a failure is printed and does not stop the run."""
    con = duckdb.connect(str(config.DUCKDB_PATH))
    have = set()
    if any(config.VIX_SPX_RAW_DIR.glob("*/*.parquet")):
        _rebuild_spx_table(con)
        have = {(pd.Timestamp(t).date(), pd.Timestamp(e).date()) for t, e in con.execute(
            f"SELECT DISTINCT trade_date, expiration FROM {config.VIX_SPX_RAW_TABLE}").fetchall()}
    need = sorted(set(pairs) - have)
    if need:
        client = theta.make_client()
        for d, e in need:
            print(f"Requesting SPX (AM-settled) quotes at {snapshot_time(d):%H:%M} on {d}, expiry {e}")
            try:
                chain = theta.call(client.option_at_time_quote, symbol="SPX", start_date=d, end_date=d,
                                   time_of_day=snapshot_time(d).strftime("%H:%M:%S.000"), expiration=e)
            except Exception as err:
                print(f"  failed: {type(err).__name__}: {getattr(err, 'details', lambda: err)()}")
                continue
            chain["trade_date"] = d.isoformat()
            pq.write_to_dataset(pa.Table.from_pandas(chain, preserve_index=False), root_path=config.VIX_SPX_RAW_DIR,
                                partition_cols=["trade_date"], basename_template=f"exp{e:%Y%m%d}-{{i}}.parquet")
        _rebuild_spx_table(con)
    con.close()


def load_quotes(con, root, trade_date, expiry):
    """Quotes at the options close: SPX from its table, SPXW from the surface Parquet files."""
    if root == "SPX":
        return con.execute(f"""SELECT strike, upper("right") AS "right", bid, ask FROM {config.VIX_SPX_RAW_TABLE}
            WHERE CAST(trade_date AS DATE) = ? AND CAST(expiration AS DATE) = ?""", [trade_date, expiry]).df()
    from vol_surface import surface_parquet, surface_saved
    if not surface_saved(trade_date):
        return pd.DataFrame(columns=["strike", "right", "bid", "ask"])
    return con.execute(f"""SELECT strike, upper("right") AS "right", bid, ask FROM read_parquet('{surface_parquet(trade_date)}')
        WHERE CAST(expiration AS DATE) = ?""", [expiry]).df()


def load_quotes_eod(con, root, trade_date, expiry):
    """End-of-day quotes (used by the 51-date validation)."""
    table = config.VIX_SPX_RAW_TABLE_EOD if root == "SPX" else config.SURFACE_RAW_TABLE_EOD
    return con.execute(f"""SELECT strike, upper("right") AS "right", bid, ask FROM {table}
        WHERE CAST(trade_date AS DATE) = ? AND CAST(expiration AS DATE) = ?""", [trade_date, expiry]).df()


def trading_days(start, end):
    """FRED SP500 dates; after FRED's last observation, every weekday is assumed to trade
    (needed for expiries still in the future; no market holidays in the span used here)."""
    days = set(fred_closes(start, end).index)
    d = max(days) + dt.timedelta(days=1)
    while d <= end:
        if d.weekday() < 5:
            days.add(d)
        d += dt.timedelta(days=1)
    return days


def load_rates(start, end):
    return {s: fred_closes(start, end, s) for s in config.CMT_SERIES}


def vix_for_date(con, d, terms, cmt, verbose=False, quotes=None, calc_time=None):
    """Replicated VIX for snapshot d from the two bracket terms. Returns (vix, details)."""
    # "Most recent business day" curve: the previous day's, as in the Cboe worked example.
    curve_day = max(k for k in cmt["DGS1MO"].index if k < d)
    pts = [(n, cmt[s].get(curve_day)) for s, n in config.CMT_SERIES.items()]
    days, ylds = zip(*[(n, y) for n, y in pts if y == y and y is not None])
    res, details = [], []
    for expiry, root, settle in terms:
        q = (quotes or load_quotes)(con, root, d, expiry)     # quotes: optional loader for other tables
        if q.empty:
            fail(f"no {root} quotes saved for expiry {expiry}")
        m = minutes_to_expiry(d, expiry, settle, calc_time)
        T = m / M365
        R = cboe_rate(days, ylds, (expiry - d).days)
        var, F, k0, n, kmin, kmax = single_term_variance(q, T, R)
        res.append((m, T, var))
        details.append(f"{root} {expiry} ({(expiry - d).days}d) F={F:.2f} K0={k0:g} n={n} vol={math.sqrt(var) * 100:.2f}")
        if verbose:
            print(f"  {d} {root} {expiry}: T={m} min, R={R:.4%}, F={F:.2f}, K0={k0:g}, "
                  f"{n} strikes {kmin:g}-{kmax:g}, term vol {math.sqrt(var) * 100:.2f}")
    (m1, T1, v1), (m2, T2, v2) = res
    mcm = config.VIX_CONSTANT_MATURITY_DAYS * 1440
    vix = 100 * math.sqrt((T1 * v1 * (m2 - mcm) / (m2 - m1) + T2 * v2 * (mcm - m1) / (m2 - m1)) * M365 / mcm)
    return vix, "; ".join(details)


def main():
    start, end = min(config.SURFACE_DATES_ORIGINAL) - dt.timedelta(days=10), max(config.SURFACE_DATES_ORIGINAL)
    tdays = trading_days(start, end + dt.timedelta(days=60))
    plans = {}
    for d in config.SURFACE_DATES_ORIGINAL:
        near, nxt = bracket(d, eligible_expiries(d, tdays))
        plans[d] = (near, nxt)
        print(f"{d}: near term {near[1]} {near[0]} ({(near[0] - d).days}d), "
              f"next term {nxt[1]} {nxt[0]} ({(nxt[0] - d).days}d)")
    fetch_spx_if_needed([(d, t[0]) for d, p in plans.items() for t in p if t[1] == "SPX"])

    cmt = load_rates(start, end)
    vix = fred_closes(start, end, config.VIX_SERIES)
    con = duckdb.connect(str(config.DUCKDB_PATH), read_only=True)
    rows = []
    for d, terms in plans.items():
        try:      # per date: a missing chain or a failed check is reported and the run continues
            ours, _ = vix_for_date(con, d, terms, cmt, verbose=True, calc_time=snapshot_time(d))
            rows.append(dict(date=d, replicated=round(ours, 2), vixcls=vix[d], difference=round(ours - vix[d], 2), note=""))
        except Exception as e:
            rows.append(dict(date=d, replicated=float("nan"), vixcls=vix.get(d), difference=float("nan"),
                             note=f"{type(e).__name__}: {e}"))
    con.close()

    out = pd.DataFrame(rows)
    print("\nVIX replication vs published close (FRED VIXCLS)")
    print(out.to_string(index=False))
    big = out[out.difference.abs() > config.VIX_MAX_GAP_POINTS]
    if len(big):
        print(f"FLAG: difference above {config.VIX_MAX_GAP_POINTS} points on {', '.join(map(str, big.date))}; "
              "investigate before changing anything")


if __name__ == "__main__":
    try:
        main()
    except SanityCheckFailed as e:
        raise SystemExit(f"SANITY CHECK FAILED: {e}")
