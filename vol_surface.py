"""Part B steps 2-6: forward and discount factor per expiry, Black-76 implied vols, Greeks, checks.

Reads the chains saved by fetch_surface.py (no downloads except FRED SOFR / DGS3MO / VIXCLS).
Writes DuckDB tables vol_surface (one row per option) and surface_expiries (one row per expiry).
Run:  .venv\\Scripts\\python.exe vol_surface.py
"""
import datetime as dt
import math
import time
from zoneinfo import ZoneInfo

import duckdb
import numpy as np
import pandas as pd

import config
from analyze_event import SanityCheckFailed, clean_chain, fail, fred_closes, snapshot_time

_erf = np.vectorize(math.erf)
TZ = ZoneInfo(config.MARKET_TZ)


def norm_cdf(x):
    return 0.5 * (1.0 + _erf(x / math.sqrt(2.0)))


def norm_pdf(x):
    return np.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def black76(F, K, t, df, sigma, is_call):
    """Black-76 price of a European option on the forward F, discounted with df."""
    sd = sigma * np.sqrt(t)
    d1 = (np.log(F / K) + 0.5 * sd * sd) / sd
    d2 = d1 - sd
    call = df * (F * norm_cdf(d1) - K * norm_cdf(d2))
    put = df * (K * norm_cdf(-d2) - F * norm_cdf(-d1))
    return np.where(is_call, call, put)


def implied_vol(price, F, K, t, df, is_call):
    """Bisection (a bracketing root finder) on sigma. NaN where the price is outside the bracket."""
    lo = np.full(len(price), config.SOLVER_BOUNDS[0])
    hi = np.full(len(price), config.SOLVER_BOUNDS[1])
    ok = (black76(F, K, t, df, lo, is_call) < price) & (price < black76(F, K, t, df, hi, is_call))
    for _ in range(config.SOLVER_ITERATIONS):
        mid = 0.5 * (lo + hi)
        too_low = black76(F, K, t, df, mid, is_call) < price
        lo, hi = np.where(too_low, mid, lo), np.where(too_low, hi, mid)
    return np.where(ok, 0.5 * (lo + hi), np.nan)


def greeks(F, K, t, df, sigma, is_call):
    """Black-76 Greeks in index points: delta and gamma w.r.t. the forward (gamma per 1 point),
    vega per 1 vol point, theta per calendar day with the forward held fixed."""
    sd = sigma * np.sqrt(t)
    d1 = (np.log(F / K) + 0.5 * sd * sd) / sd
    pdf = norm_pdf(d1)
    delta = np.where(is_call, df * norm_cdf(d1), -df * norm_cdf(-d1))
    gamma = df * pdf / (F * sd)
    vega = df * F * pdf * np.sqrt(t) / 100.0
    r = -np.log(df) / t
    value = black76(F, K, t, df, sigma, is_call)
    theta = (r * value - df * F * pdf * sigma / (2.0 * np.sqrt(t))) / 365.0
    return delta, gamma, vega, theta


def vanna(F, K, t, df, sigma):
    """Black-76 vanna = d2V / dF dsigma = -DF * n(d1) * d2 / sigma (same for calls and puts), per
    1 vol point: change in delta per 1 vol point, equivalently change in vega (per vol point)
    per 1 index point of the forward."""
    sd = sigma * np.sqrt(t)
    d1 = (np.log(F / K) + 0.5 * sd * sd) / sd
    d2 = d1 - sd
    return -df * norm_pdf(d1) * d2 / sigma / 100.0


def years_to_expiry(trade_date, expiration):
    """Calendar time ACT/365 from the options close on the trade date (16:15 ET, 13:15 on a half
    day) to 16:00 ET on expiry."""
    start = dt.datetime.combine(trade_date, snapshot_time(trade_date), TZ)
    end = dt.datetime.combine(expiration, config.EXPIRY_TIME, TZ)
    return (end - start).total_seconds() / (365.0 * 86400.0)


def forward_and_df(clean):
    """Regress (C - P) on K over the clean paired strikes nearest the money.
    C - P = DF*F - DF*K, so slope = -DF and intercept / DF = F."""
    wide = clean.pivot_table(index="strike", columns="right", values="mid").reindex(columns=["CALL", "PUT"])
    pairs = wide.dropna().sort_index()
    if pairs.empty:
        return np.nan, np.nan, 0
    diff = pairs.CALL - pairs.PUT
    k0 = diff.abs().idxmin()
    near = pairs.index[np.argsort(np.abs(pairs.index - k0))[:config.REGRESSION_STRIKES]]
    if len(near) < config.MIN_REGRESSION_STRIKES:
        return np.nan, np.nan, len(near)
    slope, intercept = np.polyfit(near.values, diff.loc[near].values, 1)
    df = -slope
    return intercept / df, df, len(near)


def atm_vol(opts, F):
    """IV at log-moneyness 0, linear in log-moneyness between the nearest OTM put below F
    and the nearest OTM call above F. NaN if either side is missing."""
    ok = opts.dropna(subset=["iv"])
    put = ok[(ok.right == "PUT")].nlargest(1, "strike")
    call = ok[(ok.right == "CALL")].nsmallest(1, "strike")
    if put.empty or call.empty:
        return np.nan
    kp, kc = put.log_moneyness.iloc[0], call.log_moneyness.iloc[0]
    vp, vc = put.iv.iloc[0], call.iv.iloc[0]
    return vp + (0.0 - kp) * (vc - vp) / (kc - kp) if kc != kp else 0.5 * (vp + vc)


def bill_rate(curve, days):
    """Treasury bill yield (decimal) for a maturity in days: linear between the CMT points."""
    x, y = zip(*sorted(curve))
    return float(np.interp(days, x, y)) / 100


def bill_curve_on(bills, d):
    """[(days, yield %)] from the latest FRED observation on or before d, per bill series."""
    pts = []
    for series, days in config.BILL_SERIES.items():
        s = bills[series][bills[series].index <= d]
        if len(s):
            pts.append((days, s.iloc[-1]))
    return pts


def surface_parquet(trade_date):
    """Glob for one date's raw surface chain (kept as Parquet only, not loaded into DuckDB)."""
    return (config.SURFACE_RAW_DIR / f"trade_date={trade_date.isoformat()}" / "*.parquet").as_posix()


def surface_saved(trade_date):
    return any((config.SURFACE_RAW_DIR / f"trade_date={trade_date.isoformat()}").glob("*.parquet"))


def process_date(con, trade_date, curve, raw_table=None):
    """raw_table: a DuckDB table to read instead (the 51-date validation uses the end-of-day table)."""
    if raw_table is None and not surface_saved(trade_date):
        fail(f"no surface chain saved for {trade_date}; run fetch_surface.py")
    source = raw_table or f"read_parquet('{surface_parquet(trade_date)}')"
    raw = con.execute(f"""
        SELECT CAST(expiration AS DATE) AS expiration, strike, upper("right") AS "right", bid, ask
        FROM {source} WHERE CAST(trade_date AS DATE) = ?
    """ if raw_table else f"""
        SELECT CAST(expiration AS DATE) AS expiration, strike, upper("right") AS "right", bid, ask
        FROM {source}
    """, [trade_date] if raw_table else []).df()
    if raw.empty:
        fail(f"no surface chain saved for {trade_date}; run fetch_surface.py")
    options, expiries, counts = [], [], dict(below_intrinsic=0, solver_failed=0, no_forward=0)
    for expiration, chain in raw.groupby("expiration"):
        expiration = pd.Timestamp(expiration).date()
        t = years_to_expiry(trade_date, expiration)
        clean = clean_chain(chain)
        F, df, n_reg = forward_and_df(clean)
        rate = -math.log(df) / t if df == df and df > 0 else np.nan
        exp_row = dict(trade_date=trade_date, expiration=expiration, dte=(expiration - trade_date).days,
                       t_years=t, regression_strikes=n_reg, forward=F, discount_factor=df,
                       implied_rate=rate, bill_rate=bill_rate(curve, (expiration - trade_date).days),
                       half_day=trade_date in config.HALF_DAYS)
        if F != F:
            counts["no_forward"] += len(clean)
            expiries.append(exp_row)
            continue
        # Out-of-the-money only: puts below the forward, calls at or above it.
        # Out-of-the-money candidates: two-sided quotes with bid > 0 (puts below F, calls at/above F).
        q = chain.dropna(subset=["bid", "ask"])
        q = q[(q.ask > 0) & (q.bid > 0) & (q.ask >= q.bid)]
        otm = q[((q.right == "PUT") & (q.strike < F)) | ((q.right == "CALL") & (q.strike >= F))].copy()
        otm["mid"] = (otm.bid + otm.ask) / 2
        is_call = (otm.right == "CALL").values
        K = otm.strike.values
        # Kept if it passes the price-spread rule (same as clean_chain) OR its vol spread
        # (IV at ask - IV at bid) is at most SURFACE_MAX_VOL_SPREAD_POINTS.
        spread = (otm.ask - otm.bid).values
        price_ok = (spread / otm.mid.values <= config.MAX_RELATIVE_SPREAD) | (spread <= config.MAX_ABSOLUTE_SPREAD)
        vol_spread = (implied_vol(otm.ask.values, F, K, t, df, is_call)
                      - implied_vol(otm.bid.values, F, K, t, df, is_call))
        keep = price_ok | (vol_spread <= config.SURFACE_MAX_VOL_SPREAD_POINTS / 100)
        counts["price_rule"] = counts.get("price_rule", 0) + int(price_ok.sum())
        counts["vol_rule_added"] = counts.get("vol_rule_added", 0) + int((keep & ~price_ok).sum())
        otm["vol_spread"] = vol_spread
        otm = otm[keep].copy()
        is_call = (otm.right == "CALL").values
        K, mid = otm.strike.values, otm.mid.values
        intrinsic = np.where(is_call, df * np.maximum(F - K, 0), df * np.maximum(K - F, 0))
        below = mid <= intrinsic
        iv = implied_vol(mid, F, K, t, df, is_call)
        iv[below] = np.nan
        counts["below_intrinsic"] += int(below.sum())
        counts["solver_failed"] += int((np.isnan(iv) & ~below).sum())
        otm["iv"] = iv
        otm = otm[~np.isnan(iv)].copy()
        c = (otm.right == "CALL").values
        d, g, v, th = greeks(F, otm.strike.values, t, df, otm.iv.values, c)
        otm = otm.assign(trade_date=trade_date, expiration=expiration, forward=F, discount_factor=df,
                         t_years=t, log_moneyness=np.log(otm.strike / F), delta=d, gamma=g, vega=v, theta=th,
                         vanna=vanna(F, otm.strike.values, t, df, otm.iv.values),
                         half_day=trade_date in config.HALF_DAYS)
        exp_row["atm_iv"] = atm_vol(otm, F)
        exp_row["options"] = len(otm)
        options.append(otm)
        expiries.append(exp_row)
    cols = ["trade_date", "expiration", "strike", "right", "mid", "forward", "discount_factor",
            "t_years", "log_moneyness", "iv", "vol_spread", "delta", "gamma", "vega", "theta", "vanna", "half_day"]
    if not options:
        fail(f"{trade_date}: no expiry produced implied vols")
    return pd.concat(options)[cols], pd.DataFrame(expiries), counts


def run_checks(opts, exps, trade_date, vix):
    flags = []   # (check, message)
    # Self-test: repricing at the solved IV must return the mid.
    err = np.abs(black76(opts.forward.values, opts.strike.values, opts.t_years.values,
                         opts.discount_factor.values, opts.iv.values, (opts.right == "CALL").values) - opts.mid.values)
    if err.max() > 1e-6:
        flags.append(("reprice", f"{trade_date}: repricing at solved IV misses the mid by up to {err.max():.2e}"))

    for _, e in exps.iterrows():
        if e.regression_strikes < config.MIN_REGRESSION_STRIKES:
            flags.append(("no_forward", f"{e.expiration}: only {e.regression_strikes} clean paired strikes, no forward"))
            continue
        if not (config.DF_BOUNDS[0] < e.discount_factor <= config.DF_BOUNDS[1]):
            flags.append(("discount_factor", f"{e.expiration}: discount factor {e.discount_factor:.5f} outside {config.DF_BOUNDS}"))
        if abs(e.implied_rate - e.bill_rate) > config.RATE_TOLERANCE:   # information, not a failure
            flags.append(("rate_info", f"{e.expiration} ({e.dte}d): implied rate {e.implied_rate:.2%} vs T-bill "
                                       f"{e.bill_rate:.2%} (more than {config.RATE_TOLERANCE:.1%} apart)"))
        if e.atm_iv != e.atm_iv:
            flags.append(("no_atm", f"{e.expiration}: no ATM vol (missing OTM put or call next to the forward)"))

    # Total ATM variance should not fall as expiry lengthens.
    s = exps.dropna(subset=["atm_iv"]).sort_values("t_years")
    w = (s.atm_iv ** 2 * s.t_years).values
    for i in range(1, len(w)):
        if w[i] < w[i - 1]:
            flags.append(("total_variance", f"{s.expiration.iloc[i]}: total ATM variance falls from {w[i-1]:.6f} "
                         f"({s.expiration.iloc[i-1]}) to {w[i]:.6f} ({(w[i] / w[i-1] - 1):+.1%})"))

    # 30-day ATM vol (total variance interpolated linearly in t) vs VIX.
    t30 = 30 / 365
    atm30 = math.sqrt(np.interp(t30, s.t_years.values, w) / t30)
    gap = vix / 100 - atm30
    if atm30 >= vix / 100 or gap > config.ATM_VIX_MAX_REL_GAP * atm30:
        flags.append(("atm30_vs_vix", f"30-day ATM vol {atm30:.2%} vs VIX {vix:.2f}: VIX is {gap / atm30:+.0%} above ATM vol, "
                                      f"outside (0, {config.ATM_VIX_MAX_REL_GAP:.0%}]"))

    out_of_range = opts[(opts.iv < config.IV_BOUNDS[0]) | (opts.iv > config.IV_BOUNDS[1])]
    if len(out_of_range):
        by_exp = out_of_range.groupby("expiration").size()
        for e, n in by_exp.items():
            flags.append(("iv_range", f"{e}: {n} options with IV outside {config.IV_BOUNDS[0]:.0%}-{config.IV_BOUNDS[1]:.0%}"))
    return atm30, flags


def main():
    """Every date in config.all_surface_dates() (SURFACE_DATES and all_dates_status.csv) with a saved
    chain. Dates already stored are kept as they are; only missing ones are computed and appended,
    so a run never drops rows (an empty database is built from scratch). A date that fails (no usable
    expiry, repricing self-test, any error) is recorded in SURFACE_FAILURES_CSV and left out; the run
    continues. Dates whose chain is not fetched yet are counted, not treated as failures."""
    wanted = config.all_surface_dates()
    dates = [d for d in wanted if surface_saved(d)]
    not_fetched = len(wanted) - len(dates)
    con = duckdb.connect(str(config.DUCKDB_PATH))
    tables = set(con.execute("SELECT table_name FROM information_schema.tables").df().table_name)
    if config.SURFACE_SUMMARY_TABLE in tables:
        con.execute("BEGIN TRANSACTION")
        summ, failures = append_dates(con, dates)
        con.execute("COMMIT")
    else:
        all_opts, all_exps, summary, failures = compute_dates(con, dates)
        opts, exps, summ = pd.concat(all_opts), pd.concat(all_exps), pd.DataFrame(summary)
        con.execute(f"CREATE OR REPLACE TABLE {config.SURFACE_TABLE} AS SELECT * FROM opts")
        con.execute(f"CREATE OR REPLACE TABLE {config.SURFACE_EXPIRY_TABLE} AS SELECT * FROM exps")
        con.execute(f"CREATE OR REPLACE TABLE {config.SURFACE_SUMMARY_TABLE} AS SELECT * FROM summ")
    n_opts, n_exps, n_dates = (con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in
                               (config.SURFACE_TABLE, config.SURFACE_EXPIRY_TABLE, config.SURFACE_SUMMARY_TABLE))
    # Link to the event study: the 09-11 expiry seen from 09-10.
    e = con.execute(f"SELECT * FROM {config.SURFACE_EXPIRY_TABLE} WHERE trade_date = ? AND expiration = ?",
                    [config.TRADE_DATE, config.EXPIRATION]).df()
    con.close()
    pd.DataFrame(failures, columns=["date", "reason"]).to_csv(config.SURFACE_FAILURES_CSV, index=False)
    print(f"\nAdded {len(summ)} dates this run. Stored {n_opts} rows in '{config.SURFACE_TABLE}', {n_exps} rows in "
          f"'{config.SURFACE_EXPIRY_TABLE}', {n_dates} dates in '{config.SURFACE_SUMMARY_TABLE}'. Failed: {len(failures)} "
          f"(see {config.SURFACE_FAILURES_CSV}). Not fetched yet: {not_fetched}.")
    if len(summ):
        print(f"Seconds per date: median {summ.seconds.median():.1f}, max {summ.seconds.max():.1f}")

    if len(e):
        e = e.iloc[0]
        print(f"Cross-check vs single-event report: {config.EXPIRATION} expiry on {config.TRADE_DATE}: "
              f"regression forward {e.forward:.2f} (parity at one strike gave 7598.30), ATM IV {e.atm_iv:.2%} "
              f"(straddle 0.80% implies about 19%)")


def append_dates(con, dates):
    """Stage 7: compute `dates` (saved chains only, not yet in the summary table) and append their
    rows to the three surface tables. Existing rows are never rewritten. Returns (summary, failures)."""
    have = set(con.execute(f"SELECT trade_date FROM {config.SURFACE_SUMMARY_TABLE}").df().trade_date.map(
        lambda x: pd.Timestamp(x).date()))
    dates = [d for d in dates if d not in have and surface_saved(d)]
    if not dates:
        return pd.DataFrame(), []
    all_opts, all_exps, summary, failures = compute_dates(con, dates)
    summ = pd.DataFrame(summary)
    if all_opts:
        opts, exps = pd.concat(all_opts), pd.concat(all_exps)
        con.execute(f"INSERT INTO {config.SURFACE_TABLE} BY NAME SELECT * FROM opts")
        con.execute(f"INSERT INTO {config.SURFACE_EXPIRY_TABLE} BY NAME SELECT * FROM exps")
        con.execute(f"INSERT INTO {config.SURFACE_SUMMARY_TABLE} BY NAME SELECT * FROM summ")
    return summ, failures


def compute_dates(con, dates):
    """The per-date surface calculation and checks for `dates`; prints the per-date report."""
    start, end = min(dates) - dt.timedelta(days=10), max(dates)
    bills = {s: fred_closes(start, end, s) for s in config.BILL_SERIES}
    vix = fred_closes(start, end, config.VIX_SERIES)
    all_opts, all_exps, summary, failures = [], [], [], []
    for trade_date in dates:
        t0 = time.monotonic()
        try:
            opts, exps, counts = process_date(con, trade_date, bill_curve_on(bills, trade_date))
            atm30, flags = run_checks(opts, exps, trade_date, vix.get(trade_date, float("nan")))
            if any(check == "reprice" for check, _ in flags):
                fail(next(m for c, m in flags if c == "reprice"))
        except Exception as e:          # recorded per date; the run continues
            failures.append((trade_date, f"{type(e).__name__}: {e}"))
            print(f"\n=== {trade_date} === FAILED: {failures[-1][1]}", flush=True)
            continue
        all_opts.append(opts)
        all_exps.append(exps)
        problems = [(c, m) for c, m in flags if not c.endswith("_info")]
        summary.append(dict(trade_date=trade_date, half_day=trade_date in config.HALF_DAYS, options=len(opts),
                            expiries=int(exps.forward.notna().sum()), atm30=atm30, vix=vix.get(trade_date, float("nan")),
                            flags=len(problems), flag_checks=",".join(sorted({c for c, _ in problems})),
                            seconds=round(time.monotonic() - t0, 1)))

        print(f"\n=== {trade_date} ===")
        print(f"Options with an implied vol: {len(opts)} across {exps.forward.notna().sum()} expiries "
              f"(price-spread rule {counts.get('price_rule', 0)}, added by the vol-spread rule {counts.get('vol_rule_added', 0)} "
              f"before the IV solve)")
        print(f"Dropped: below intrinsic {counts['below_intrinsic']} (OTM options have zero intrinsic "
              f"value, so this is expected to be 0), solver failed {counts['solver_failed']}, "
              f"no forward {counts['no_forward']}")
        print(f"Repricing self-test passed (all options repriced to mid within 1e-6)")
        show = exps.assign(DF=exps.discount_factor.round(6), F=exps.forward.round(2),
                           rate=(exps.implied_rate * 100).round(2), bill=(exps.bill_rate * 100).round(2),
                           atm=(exps.atm_iv * 100).round(2))
        print(show[["expiration", "dte", "regression_strikes", "F", "DF", "rate", "bill", "atm", "options"]]
              .to_string(index=False))
        print(f"30-day ATM vol {atm30:.2%} vs VIX close {vix.get(trade_date, float('nan')):.2f}. VIX prices 30-day "
              f"variance from the whole OTM strip, and the put skew lifts it above at-the-money vol.")
        print("FLAGS:" if problems else "FLAGS: none")
        for _, f in problems:
            print(f"  FLAG {f}")
        info = [m for c, m in flags if c.endswith("_info")]
        print(f"INFO: {len(info)} expiries with implied rate more than {config.RATE_TOLERANCE:.1%} from the T-bill yield")
        for m in info:
            print(f"  info {m}")
    return all_opts, all_exps, summary, failures


if __name__ == "__main__":
    try:
        main()
    except SanityCheckFailed as e:
        raise SystemExit(f"SANITY CHECK FAILED: {e}")
