"""Steps 3-7: clean the chain, recover the forward, compute implied vs actual move.

Run after fetch_chain.py:  .venv\\Scripts\\python.exe analyze_event.py
The functions here are also used by calc_moves.py for every date in the study.
"""
import io

import duckdb
import httpx
import pandas as pd

import config
import theta


class SanityCheckFailed(Exception):
    pass


def snapshot_time(trade_date):
    """Options close: 16:15 ET, or 13:15 ET on a half day."""
    return config.SNAPSHOT_TIME_HALF_DAY if trade_date in config.HALF_DAYS else config.SNAPSHOT_TIME


def fail(msg):
    raise SanityCheckFailed(msg)


def load_chain(trade_date=config.TRADE_DATE, expiration=config.EXPIRATION):
    con = duckdb.connect(str(config.DUCKDB_PATH), read_only=True)
    chain = con.execute(f"""
        SELECT strike, upper("right") AS "right", bid, ask
        FROM {config.DUCKDB_TABLE}
        WHERE CAST(trade_date AS DATE) = ? AND CAST(expiration AS DATE) = ?
    """, [trade_date, expiration]).df()
    con.close()
    if chain.empty:
        fail(f"no rows in DuckDB for trade date {trade_date}, exp {expiration}")
    return chain


def clean_chain(chain):
    """Keep two-sided, uncrossed quotes whose spread is at most MAX_RELATIVE_SPREAD of mid
    OR at most MAX_ABSOLUTE_SPREAD index points (the second rule keeps cheap options)."""
    c = chain.dropna(subset=["bid", "ask"])
    c = c[c.ask > 0]                               # ask = 0 means no quote (same rule as vix_check.py)
    c = c[(c.bid > 0) & (c.ask >= c.bid)].copy()   # zero bid / crossed quote
    c["mid"] = (c.bid + c.ask) / 2
    spread = c.ask - c.bid
    return c[(spread / c.mid <= config.MAX_RELATIVE_SPREAD) | (spread <= config.MAX_ABSOLUTE_SPREAD)]


def paired_mids(clean):
    """One row per strike that has both a clean call and a clean put."""
    wide = clean.pivot_table(index="strike", columns="right", values="mid")
    wide = wide.reindex(columns=["CALL", "PUT"])   # keep both columns even if one side was cleaned away
    pairs = wide.dropna(subset=["CALL", "PUT"]).sort_index()
    if len(pairs) < config.MIN_PAIRED_STRIKES:
        fail(f"only {len(pairs)} strikes have both legs after cleaning "
             f"(need {config.MIN_PAIRED_STRIKES})")
    return pairs


def forward_from_parity(pairs):
    """Put-call parity: C - P = DF * (F - K). The strike with the smallest |C - P| is closest to ATM.

    Rates are ignored (DF = 1): for a one-day option DF = exp(-r * 1/365) is about
    0.9999, which moves F by well under a point. Dividends need no separate handling
    because parity gives the forward directly.
    """
    gap = (pairs.CALL - pairs.PUT).abs()
    k = gap.idxmin()
    if k in (pairs.index.min(), pairs.index.max()):
        fail(f"closest call/put strike {k} is the lowest or highest usable strike")
    call_mid, put_mid = pairs.loc[k, "CALL"], pairs.loc[k, "PUT"]
    if abs(call_mid - put_mid) > config.MAX_FORWARD_STRIKE_GAP:
        # |F - K| = |C - P|: a large gap means the strikes nearest the forward were cleaned away.
        fail(f"forward is {abs(call_mid - put_mid):.2f} points from the nearest clean strike {k} "
             f"(max {config.MAX_FORWARD_STRIKE_GAP})")
    return k, call_mid, put_mid, k + (call_mid - put_mid)


def fred_closes(start=None, end=None, series=config.FRED_SERIES):
    """Daily FRED values (default S&P 500 closes; no API key needed for this CSV). Defaults: around the event."""
    theta.apply_ca_bundle()
    start = start or config.TRADE_DATE - pd.Timedelta(days=10)
    end = end or config.EVENT_DATE
    resp = httpx.get(config.FRED_CSV_URL, timeout=30, params={
        "id": series, "cosd": start.isoformat(), "coed": end.isoformat()})
    resp.raise_for_status()
    df = pd.read_csv(io.StringIO(resp.text), na_values=".")
    df["date"] = pd.to_datetime(df.iloc[:, 0]).dt.date
    return df.dropna(subset=[series]).set_index("date")[series]


def measure(trade_date, expiration, close_before, close_event):
    """Implied and actual move for one expiry, from the chain saved for trade_date."""
    chain = load_chain(trade_date, expiration)
    clean = clean_chain(chain)
    strike, call_mid, put_mid, forward = forward_from_parity(paired_mids(clean))
    # Option quotes are ~16:15 ET, after the 16:00 cash close: a large gap usually means
    # after-hours news moved the market. Flagged here, not dropped.
    after_hours_gap = bool(abs(forward / close_before - 1) > config.FORWARD_TOLERANCE)

    straddle = call_mid + put_mid               # ATM straddle at the parity strike
    implied_move = straddle / forward           # market-implied expected absolute move
    # Start from the forward, the same ~16:15 reference point the straddle was priced off,
    # and end at the release-day 16:00 close (SPXW weeklies settle on that close).
    actual_move = abs(close_event / forward - 1)
    return dict(strike=strike, call_mid=call_mid, put_mid=put_mid, forward=forward,
                straddle=straddle, implied_move=implied_move, actual_move=actual_move,
                ratio=actual_move / implied_move, quotes_kept=len(clean), quotes_total=len(chain),
                after_hours_gap=after_hours_gap)


def main():
    closes = fred_closes()
    if config.EVENT_DATE not in closes.index:
        fail(f"FRED {config.FRED_SERIES} has no close for {config.EVENT_DATE}")
    prev_day = closes.index[closes.index < config.EVENT_DATE].max()
    if prev_day != config.TRADE_DATE:
        fail(f"TRADE_DATE {config.TRADE_DATE} is not the trading day before the event "
             f"(FRED says {prev_day})")
    close_before, close_event = closes[prev_day], closes[config.EVENT_DATE]

    m = measure(config.TRADE_DATE, config.EXPIRATION, close_before, close_event)
    if m["after_hours_gap"]:   # the single-event report still stops on this
        fail(f"forward {m['forward']:.2f} is more than {config.FORWARD_TOLERANCE:.1%} "
             f"from the FRED close {close_before:.2f}")
    one_sigma = m["implied_move"] * 1.25        # straddle ~ 0.8 sigma, so sigma ~ 1.25 x straddle
    print(f"Cleaning: kept {m['quotes_kept']} of {m['quotes_total']} quotes "
          f"(max spread {config.MAX_RELATIVE_SPREAD:.0%} of mid or {config.MAX_ABSOLUTE_SPREAD} points)")
    print(f"""
=== {config.EVENT_NAME}: implied vs actual S&P 500 move ===
Event (release) date        : {config.EVENT_DATE}
Chain snapshot (EOD) date   : {config.TRADE_DATE}
Option expiry used          : {config.OPTION_SYMBOL} {config.EXPIRATION}
Strike used (ATM)           : {m['strike']:.0f}   call mid {m['call_mid']:.2f}, put mid {m['put_mid']:.2f}
Forward (put-call parity)   : {m['forward']:.2f}   (FRED close {prev_day}: {close_before:.2f})
ATM straddle (mid)          : {m['straddle']:.2f}
Implied move (straddle / F) : {m['implied_move']:.2%}   expected absolute move
Implied 1-sigma (x1.25)     : {one_sigma:.2%}   one standard deviation
Actual move (from forward)  : {m['actual_move']:.2%}   {m['forward']:.2f} -> {close_event:.2f} (FRED close {config.EVENT_DATE})
Ratio actual / implied move : {m['ratio']:.2f}
Ratio actual / 1-sigma      : {m['actual_move'] / one_sigma:.2f}
""")


if __name__ == "__main__":
    try:
        main()
    except SanityCheckFailed as e:
        raise SystemExit(f"SANITY CHECK FAILED: {e}")
