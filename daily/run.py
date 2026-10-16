"""One daily update run: catch up every trading day not yet processed, with the existing scripts' functions and
the frozen method, rules and limits. Downloads first (FRED, then ThetaData), then all database writes in one
transaction under the append-only guard (daily/guard.py), then the live files in data/live/.

Each step runs only once its inputs exist, so a run never stores a number it would later need to change:
- the date list and event moves wait for FRED's S&P 500 close of the day (and of the day before);
- surfaces wait for FRED's bill yields and VIX for the day (the frozen method's inputs);
- "priced now" (event_priced) needs only the day-before 16:15 chain, so it is stored the same evening;
- a premium is final once its 20 following trading days are in; until then event_premium_provisional holds a
  provisional one, recomputed each run (the only table whose rows change).
No keys or tokens are printed or logged."""
import contextlib
import datetime as dt
import hashlib
import json
import math
import os
import re
import shutil
import tempfile
import time
import traceback
from collections import defaultdict

import duckdb
import pandas as pd
import pyarrow.parquet as pq

import analyze_event
import build_dates
import config
import skew_metrics
import stage5b_controls
import vol_page
import vol_surface
from . import calendar as cal
from . import guard, premium

FROZEN_FILES = ["data/summary_stage3.txt", "data/stage3_periods.txt", "data/summary_1615.txt",
                "data/fixed_minute_results.txt", "data/validate_log.txt", "data/skew_summary.txt",
                "data/stage5b_summary.txt", "data/stage5b_readme.md", "data/control_pairs.csv", "data/date_list.csv",
                "data/surface_dates.csv", "data/surface_date_status.csv", "data/surface_fetch_errors.csv",
                "data/vol_event_pages.csv", "data/exploratory_nfp_friday.txt", "data/findings_v1.md"]
FRED_FROM = config.V1_END - dt.timedelta(days=45)


# ------------------------------------------------------------ logging, status, lock
def log(msg):
    line = f"{dt.datetime.now().isoformat(timespec='seconds')} {msg}"
    print(line, flush=True)
    config.LIVE_DIR.mkdir(parents=True, exist_ok=True)
    with open(config.UPDATE_LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def write_status(**fields):
    old = {}
    config.LIVE_DIR.mkdir(parents=True, exist_ok=True)
    if config.UPDATE_STATUS.exists():
        with contextlib.suppress(ValueError):
            old = json.loads(config.UPDATE_STATUS.read_text(encoding="utf-8"))
    old.update(fields)
    config.UPDATE_STATUS.write_text(json.dumps(old, indent=1, default=str), encoding="utf-8")


@contextlib.contextmanager
def run_lock():
    path = config.LIVE_DIR / "update.lock"
    config.LIVE_DIR.mkdir(parents=True, exist_ok=True)
    if path.exists() and time.time() - path.stat().st_mtime < 3 * 3600:
        raise RuntimeError("another update is running (data/live/update.lock is less than 3 hours old)")
    path.write_text(str(os.getpid()), encoding="utf-8")
    try:
        yield
    finally:
        path.unlink(missing_ok=True)


@contextlib.contextmanager
def live_logs():
    """fetch_surface appends its errors to data/surface_fetch_errors.csv, a frozen file: send them to data/live/."""
    saved = config.SURFACE_FETCH_ERRORS_CSV
    config.SURFACE_FETCH_ERRORS_CSV = config.LIVE_FETCH_ERRORS_CSV
    try:
        yield
    finally:
        config.SURFACE_FETCH_ERRORS_CSV = saved


def frozen_hashes():
    return {f: hashlib.sha256((config.PROJECT_DIR / f).read_bytes()).hexdigest() for f in FROZEN_FILES}


# ------------------------------------------------------------ inputs
def fred_inputs():
    """S&P 500 closes (all, to define trading days), and the bill and VIX series from shortly before the cut-off."""
    today = dt.date.today()
    closes = analyze_event.fred_closes(start=config.DATA_START, end=today)
    bills = {s: analyze_event.fred_closes(FRED_FROM, today, s) for s in config.BILL_SERIES}
    vix = analyze_event.fred_closes(FRED_FROM, today, config.VIX_SERIES)
    return closes, bills, vix


def fred_final(series, d):
    """The frozen method uses the latest observation on or before d; it is final once the series has one dated d or later."""
    return len(series) > 0 and max(series.index) >= d


def fred_ready(bills, vix, d):
    """A surface for d waits until every bill series and VIX has published d or a later date. A bond-market holiday
    (no bill yield on d, e.g. Columbus Day, Veterans Day) is ready once the next yield is out, and then uses the
    latest yield on or before d (vol_surface.bill_curve_on), so it never waits for a day FRED will not publish."""
    return all(fred_final(s, d) for s in bills.values()) and fred_final(vix, d)


def chain_from_parquet(trade_date, expiration):
    """analyze_event.load_chain, read from the saved Parquet instead of DuckDB (the same rows; DuckDB is open for
    writing in this process)."""
    path = config.RAW_DIR / f"trade_date={trade_date}"
    df = pq.read_table(path).to_pandas()
    df = df[df.expiration.astype(str).str[:10] == str(expiration)]
    if df.empty:
        analyze_event.fail(f"no saved quotes for trade date {trade_date}, exp {expiration}")
    return pd.DataFrame({"strike": df.strike, "right": df["right"].str.upper(), "bid": df.bid, "ask": df.ask})


def calendar_events():
    c = pd.read_csv(config.EVENT_CALENDAR, dtype=str)
    c["date"] = pd.to_datetime(c.date).dt.date
    return c.groupby("date").event_type.apply(lambda x: "+".join(sorted(set(x)))).to_dict()


def db_dates(con, table, col):
    try:
        return {pd.Timestamp(x).date() for x in con.execute(f"SELECT DISTINCT {col} FROM {table}").df()[col]}
    except duckdb.Error:
        return set()


def connect_write(wait_s=180):
    """The API holds short read-only connections; wait for a moment with none open."""
    end = time.time() + wait_s
    while True:
        try:
            return duckdb.connect(str(config.DUCKDB_PATH))
        except duckdb.IOException:
            if time.time() > end:
                raise RuntimeError("the database stayed busy for 3 minutes (is a pipeline script running?)") from None
            time.sleep(2)


# ------------------------------------------------------------ the plan
def plan(now=None):
    """What a run would do now. Reads FRED and the database (read-only); no ThetaData requests."""
    closes, bills, vix = fred_inputs()
    latest = cal.latest_closed_day(now)
    con = duckdb.connect(str(config.DUCKDB_PATH), read_only=True)
    last_chain = max(db_dates(con, config.DUCKDB_TABLE, "trade_date"))
    last_surface = max(db_dates(con, config.SURFACE_SUMMARY_TABLE, "trade_date"))
    last_moves = max(db_dates(con, config.RESULTS_TABLE, "date"))
    priced = db_dates(con, config.PRICED_TABLE, "date")
    con.close()
    from pull_chains import already_saved
    events = calendar_events()
    days = sorted(set(cal.trading_days_after(min(last_chain, last_surface), latest)))
    rows = []
    for d in days:
        nxt = cal.next_trading_day(d)
        chain = d > last_chain
        surf = d > last_surface
        rows.append(dict(
            day=d, half_day=d in config.HALF_DAYS,
            event_chain=("saved" if already_saved(d) else f"fetch (expiry {nxt})") if chain else "in database",
            surface_chain=("saved" if vol_surface.surface_saved(d) else "fetch") if surf else "in database",
            fred_close="yes" if d in closes.index else "not yet",
            fred_bills_vix=("yes" if fred_ready(bills, vix, d) else "not yet")
                           + ("*" if fred_ready(bills, vix, d)
                              and not all(d in s.index for s in bills.values()) else ""),
            moves_for=f"{nxt}" + (f" ({events[nxt]})" if nxt in events else ""),
            event=events.get(d, "")))
    nxt_events = [(e, t) for e, t in sorted(events.items()) if e > last_moves][:3]
    return dict(now_new_york=(now or cal.ny_now()).isoformat(timespec="minutes"), latest_closed_day=latest,
                last_in_db=dict(event_chain=last_chain, surface=last_surface, event_moves=last_moves),
                fred_last_close=max(closes.index), days=rows, next_events=nxt_events,
                priced_rows=sorted(priced), holidays_loaded=len(cal.holidays()))


def print_plan(p):
    print(f"New York time now: {p['now_new_york']}; latest closed trading day: {p['latest_closed_day']}")
    print(f"In the database: event-study chains to {p['last_in_db']['event_chain']}, surfaces to "
          f"{p['last_in_db']['surface']}, event moves to {p['last_in_db']['event_moves']}; FRED S&P 500 closes to "
          f"{p['fred_last_close']}")
    if not p["holidays_loaded"]:
        print("NYSE holidays are not loaded yet (config.NYSE_HOLIDAYS): weekdays are assumed to trade for scheduling.")
    print(f"\n{'day':<12}{'half':<6}{'event-study chain':<28}{'surface chain':<15}{'FRED close':<12}{'FRED bills+VIX':<16}"
          f"{'its chain prices':<24}{'event on the day'}")
    for r in p["days"]:
        print(f"{str(r['day']):<12}{'yes' if r['half_day'] else '':<6}{r['event_chain']:<28}{r['surface_chain']:<15}"
              f"{r['fred_close']:<12}{r['fred_bills_vix']:<16}{r['moves_for']:<24}{r['event']}")
    if any(r["fred_bills_vix"] == "yes*" for r in p["days"]):
        print("* no FRED bill yield on the day (a bond-market holiday): the surface uses the latest yield before it")
    print("\nThen, in one transaction (only where the inputs above exist):")
    print("- date_list and event_moves rows for each day FRED has closed; event_moves for a day uses the chain of the day before")
    print("- event_priced ('priced now') for any upcoming event whose day-before chain is saved")
    print("- event_premium (final) or event_premium_provisional for each event after the cut-off")
    print("- vol_surface, surface_expiries, surface_date_summary, surface_metrics for each day with FRED bills and VIX")
    print("- event_skew_changes and event_fixed_strike for each event whose two days are drawn")
    print("Upcoming events in the calendar:", ", ".join(f"{e} {t}" for e, t in p["next_events"]))


# ------------------------------------------------------------ fetching
def fetch(days_chain, days_surface):
    """ThetaData downloads (2 at a time, the existing rules). Returns a list of (day, what, reason) failures."""
    import fetch_surface
    import theta
    from fetch_chain import fetch_snapshot_chain, save_parquet
    from pull_chains import already_saved
    failures = []
    todo_chain = [d for d in days_chain if not already_saved(d)]
    if todo_chain:
        client = theta.make_client()
        exps = theta.call(client.option_list_expirations, symbol=config.OPTION_SYMBOL)
        listed = set(pd.to_datetime(exps["expiration"]).dt.date)
        config.RAW_DIR.mkdir(parents=True, exist_ok=True)
        for d in todo_chain:
            nxt = cal.next_trading_day(d)
            try:
                if nxt not in listed:
                    raise analyze_event.SanityCheckFailed(f"{config.OPTION_SYMBOL} has no expiration on {nxt}")
                save_parquet(fetch_snapshot_chain(client, d, nxt))
                log(f"saved event-study chain {d} (expiry {nxt})")
            except Exception as e:                       # recorded; the next run retries
                failures.append((d, "event-study chain", f"{type(e).__name__}: {e}"))
                log(f"FAILED event-study chain {d}: {type(e).__name__}: {e}")
    todo_surface = [d for d in days_surface if not vol_surface.surface_saved(d)]
    if todo_surface:
        with live_logs():
            errs = fetch_surface.main(todo_surface)
        for r in errs.itertuples():
            if r.level == "failed":
                failures.append((r.date, "surface chain", r.reason))
        log(f"surface chains: {len(todo_surface)} attempted, {int((errs.level == 'failed').sum()) if len(errs) else 0} failed")
    return failures


# ------------------------------------------------------------ writing
def ensure_tables(con):
    con.execute(f"""CREATE TABLE IF NOT EXISTS {config.PRICED_TABLE} (date DATE, event_type VARCHAR, trade_date DATE,
        strike DOUBLE, forward DOUBLE, straddle DOUBLE, implied_move DOUBLE, quotes_kept BIGINT,
        after_hours_gap BOOLEAN, half_day BOOLEAN, computed_at TIMESTAMP)""")
    con.execute(f"""CREATE TABLE IF NOT EXISTS {config.PROVISIONAL_TABLE} (date TIMESTAMP, event_type VARCHAR,
        half_day BOOLEAN, implied_move DOUBLE, baseline_implied DOUBLE, baseline_n BIGINT, premium DOUBLE,
        variance_premium DOUBLE, window_days_used BIGINT, window_days BIGINT, updated_at TIMESTAMP)""")


def init_live(con):
    """First run: live copies of the frozen lists, and the fixed checksums of the v1 rows."""
    config.LIVE_DIR.mkdir(parents=True, exist_ok=True)
    if not config.LIVE_DATE_LIST_CSV.exists():
        shutil.copyfile(config.DATE_LIST_CSV, config.LIVE_DATE_LIST_CSV)
    if not config.LIVE_STATUS_CSV.exists():
        shutil.copyfile(config.ALL_DATES_STATUS_CSV, config.LIVE_STATUS_CSV)
    if not config.V1_CHECKSUMS_CSV.exists():
        guard.save_v1(config.V1_CHECKSUMS_CSV, guard.v1_checksums(con))
        log(f"saved the v1 checksums to {config.V1_CHECKSUMS_CSV.name}")


def insert(con, table, df, inserted, key):
    if df is None or not len(df):
        return 0
    dates = {pd.Timestamp(x).date() for x in df[key]}
    guard.assert_new(con, table, dates)
    con.register("_new", df)
    con.execute(f"INSERT INTO {table} BY NAME SELECT * FROM _new")
    con.unregister("_new")
    inserted[table] |= dates
    return len(df)


def write(con, closes, bills, vix, fetch_failures):
    """All database writes for this run. Returns a summary dict and the new live files to write after commit."""
    inserted, summary = defaultdict(set), defaultdict(list)
    events = calendar_events()

    # 1. Event-study quotes saved since the last run.
    have = db_dates(con, config.DUCKDB_TABLE, "trade_date")
    for part in sorted(config.RAW_DIR.glob("trade_date=*")):
        d = dt.date.fromisoformat(part.name.split("=")[1])
        if d > max(have):
            guard.assert_new(con, config.DUCKDB_TABLE, {d})
            con.execute(f"INSERT INTO {config.DUCKDB_TABLE} BY NAME SELECT * FROM read_parquet(?, hive_partitioning = true)",
                        [f"{part.as_posix()}/*.parquet"])
            inserted[config.DUCKDB_TABLE].add(d)

    # 2. Date list: rebuilt from FRED's closes; rows already stored must be identical, new rows are appended.
    days = sorted(closes.index)
    full, _ = build_dates.build_date_list(days)
    stored = con.execute("SELECT date, event_type, trade_date, half_day FROM date_list").df()
    stored_max = pd.Timestamp(stored.date.max()).date()
    old = full[full.date <= stored_max].reset_index(drop=True)
    st = stored.assign(date=pd.to_datetime(stored.date).dt.date, trade_date=pd.to_datetime(stored.trade_date).dt.date)
    st = st.sort_values(["date", "event_type"]).reset_index(drop=True)
    if not old.equals(st[old.columns]):
        raise guard.ChecksumChanged("the date list rebuilt from FRED and the calendar differs for dates already stored")
    new_dl = full[full.date > stored_max]
    summary["date_list"] = sorted({str(d) for d in new_dl.date})
    insert(con, "date_list", new_dl, inserted, "date")
    live_dl = pd.concat([pd.read_csv(config.LIVE_DATE_LIST_CSV, dtype=str), new_dl.astype(str)], ignore_index=True)
    tmp_dl = os.path.join(tempfile.mkdtemp(), "date_list.csv")
    live_dl.to_csv(tmp_dl, index=False)

    # 3. Event moves for every date-list day not measured yet whose two closes and chain exist.
    from pull_chains import already_saved
    dl_all = pd.read_csv(tmp_dl, parse_dates=["date", "trade_date"])
    per_date = dl_all.groupby(["date", "trade_date"]).event_type.apply(lambda s: "+".join(sorted(s))).reset_index()
    measured = db_dates(con, config.RESULTS_TABLE, "date")
    skipped = pd.read_csv(config.LIVE_SKIPPED_CSV, dtype=str) if config.LIVE_SKIPPED_CSV.exists() else \
        pd.DataFrame(columns=["date", "event_type", "reason"])
    done_skip = {dt.date.fromisoformat(x) for x in skipped.date}
    rows, new_skips = [], []
    analyze_event.load_chain, saved_loader = chain_from_parquet, analyze_event.load_chain
    try:
        for d, t, et in zip(per_date.date.dt.date, per_date.trade_date.dt.date, per_date.event_type):
            if d <= config.V1_END or d in measured or d in done_skip:
                continue
            if t not in closes.index or d not in closes.index or not already_saved(t):
                continue
            try:
                m = analyze_event.measure(t, d, closes[t], closes[d])
            except analyze_event.SanityCheckFailed as e:
                new_skips.append(dict(date=str(d), event_type=et, reason=str(e)))
                continue
            rows.append(dict(date=d, event_type=et, trade_date=t, half_day=t in config.HALF_DAYS,
                             **{k: m[k] for k in ("strike", "forward", "straddle", "implied_move", "actual_move", "ratio",
                                                  "quotes_kept", "after_hours_gap")}))

        # 4. "Priced now": upcoming events whose day-before chain is saved (implied move only).
        priced = db_dates(con, config.PRICED_TABLE, "date")
        priced_rows = []
        for e, et in sorted(events.items()):
            if e <= max(measured | {r["date"] for r in rows}) or e in priced:
                continue
            t = cal.previous_trading_day(e)
            if not already_saved(t) or cal.next_trading_day(t) != e:
                continue
            try:
                m = analyze_event.measure(t, e, closes.get(t, math.nan), math.nan)
            except analyze_event.SanityCheckFailed as ex:
                log(f"priced now for {e} not stored: {ex}")
                continue
            priced_rows.append(dict(date=e, event_type=et, trade_date=t, strike=m["strike"], forward=m["forward"],
                                    straddle=m["straddle"], implied_move=m["implied_move"], quotes_kept=m["quotes_kept"],
                                    after_hours_gap=m["after_hours_gap"] if t in closes.index else None,
                                    half_day=t in config.HALF_DAYS, computed_at=dt.datetime.now()))
    finally:
        analyze_event.load_chain = saved_loader
    moves_df = pd.DataFrame(rows)
    summary["event_moves"] = [f"{r['date']} {r['event_type']}" for r in rows]
    insert(con, config.RESULTS_TABLE, moves_df, inserted, "date")
    summary["event_priced"] = [f"{r['date']} {r['event_type']}" for r in priced_rows]
    insert(con, config.PRICED_TABLE, pd.DataFrame(priced_rows), inserted, "date")

    # 5. Premiums for events after the cut-off: final once the window is complete, provisional until then.
    moves = con.execute(f"SELECT date, event_type, half_day, implied_move FROM {config.RESULTS_TABLE}").df()
    final_have = {pd.Timestamp(x).date() for x in con.execute("SELECT date FROM event_premium").df().date}
    ev_days = sorted({pd.Timestamp(x).date() for x, t in zip(moves.date, moves.event_type)
                      if t != "NONE" and pd.Timestamp(x).date() > config.V1_END} - final_have)
    attempted = {pd.Timestamp(x).date() for x in moves.date} | done_skip | {dt.date.fromisoformat(s["date"]) for s in new_skips}
    attempted |= {d.date() for d in dl_all.date if d.date() <= config.V1_END}
    res = premium.premiums(moves, tmp_dl, ev_days, attempted)
    finals, provisionals = [], []
    for ev, items in res.items():
        for kind, r in items:
            (finals if kind == "final" else provisionals).append(r)
    insert(con, "event_premium", pd.DataFrame(finals), inserted, "date")
    for r in finals:
        con.execute(f"DELETE FROM {config.PROVISIONAL_TABLE} WHERE CAST(date AS DATE) = ?", [pd.Timestamp(r["date"]).date()])
    for r in provisionals:                                  # the one table whose rows are replaced
        con.execute(f"DELETE FROM {config.PROVISIONAL_TABLE} WHERE CAST(date AS DATE) = ? AND event_type = ?",
                    [pd.Timestamp(r["date"]).date(), r["event_type"]])
    if provisionals:
        con.register("_prov", pd.DataFrame(provisionals).assign(updated_at=dt.datetime.now()))
        con.execute(f"INSERT INTO {config.PROVISIONAL_TABLE} BY NAME SELECT * FROM _prov")
        con.unregister("_prov")
    summary["event_premium"] = [f"{pd.Timestamp(r['date']).date()} {r['event_type']}" for r in finals]
    summary["event_premium_provisional"] = [f"{pd.Timestamp(r['date']).date()} {r['event_type']} "
                                            f"({r['window_days_used']} of {r['window_days']} days)" for r in provisionals]

    # 6. Surfaces, metrics, and event skew changes, for days whose FRED inputs are final.
    have_s = db_dates(con, config.SURFACE_SUMMARY_TABLE, "trade_date")
    ready = [d for d in sorted(cal.trading_days_after(max(have_s), max(closes.index)))
             if d in closes.index and vol_surface.surface_saved(d)
             and fred_ready(bills, vix, d)]
    surface_failures = []
    if ready:
        _, failures = vol_surface.append_dates(con, ready)
        surface_failures = [(d, "surface", r) for d, r in failures]
        new_s = db_dates(con, config.SURFACE_SUMMARY_TABLE, "trade_date") - have_s
        for t in ("vol_surface", "surface_expiries", "surface_date_summary"):
            inserted[t] |= new_s
        days_str = [str(d) for d in sorted(new_s)]
        if days_str:
            opts, exps, _ = vol_page.load(con, days_str)
            metrics, _ = skew_metrics.build_metrics(opts, exps, days_str)
            insert(con, "surface_metrics", metrics, inserted, "trade_date")
        summary["surfaces"] = days_str
    pairs = dl_all[dl_all.event_type != "NONE"][["date", "trade_date", "event_type"]].copy()
    pairs["date"], pairs["trade_date"] = pairs.date.dt.date.astype(str), pairs.trade_date.dt.date.astype(str)
    have_m = {str(x)[:10] for x in con.execute("SELECT trade_date FROM surface_metrics").df().trade_date}
    have_ch = {str(x)[:10] for x in con.execute("SELECT event_date FROM event_skew_changes").df().event_date}
    todo = pairs[(pairs.date > str(config.V1_END)) & pairs.date.isin(have_m) & pairs.trade_date.isin(have_m)
                 & ~pairs.date.isin(have_ch)]
    if len(todo):
        days_str = sorted(set(todo.date) | set(todo.trade_date))
        opts, exps, _ = vol_page.load(con, days_str)
        metrics, built = skew_metrics.build_metrics(opts, exps, days_str)
        fx = con.execute("SELECT trade_date, expiration, forward FROM surface_expiries").df()
        fwd = {(str(t)[:10], str(e)[:10]): f for t, e, f in zip(fx.trade_date, fx.expiration, fx.forward)}
        ch = skew_metrics.build_changes(metrics, built, todo, fwd)
        insert(con, "event_skew_changes", ch, inserted, "event_date")
        fs = [stage5b_controls.fixed_strike_change(built[b0], built[b1]) for b0, b1 in zip(ch.day_before, ch.event_date)]
        fsd = ch[["event_date", "event_type", "day_before", "flagged", "d_atm30", "fwd_return_pct"]].assign(
            k_atm30_before=[k for k, _ in fs], d_atm30_fixed_strike=[d for _, d in fs])
        insert(con, "event_fixed_strike", fsd, inserted, "event_date")
        summary["event_skew_changes"] = [f"{a} {b}" for a, b in zip(ch.event_date, ch.event_type)]
    return inserted, summary, dict(tmp_dl=tmp_dl, new_skips=new_skips, failures=fetch_failures + surface_failures)


def write_live_files(con, extra):
    shutil.copyfile(extra["tmp_dl"], config.LIVE_DATE_LIST_CSV)
    if extra["new_skips"]:
        pd.DataFrame(extra["new_skips"]).to_csv(config.LIVE_SKIPPED_CSV, mode="a", index=False,
                                                header=not config.LIVE_SKIPPED_CSV.exists())
    # Status rows for days whose surface is now decided (drawn or failed), in the frozen file's columns.
    st = pd.read_csv(config.LIVE_STATUS_CSV, dtype=str, keep_default_na=False)
    last = dt.date.fromisoformat(st.date.max())
    dl = pd.read_csv(config.LIVE_DATE_LIST_CSV, parse_dates=["date", "trade_date"])
    ev = dl[dl.event_type != "NONE"]
    ev_on = ev.groupby(ev.date.dt.date).event_type.apply(lambda x: "+".join(sorted(set(x)))).to_dict()
    ev_next = ev.groupby(ev.trade_date.dt.date).event_type.apply(lambda x: "+".join(sorted(set(x)))).to_dict()
    met = con.execute("SELECT trade_date, flagged, fit_max, range FROM surface_metrics").df()
    met = {str(r.trade_date)[:10]: r for r in met.itertuples()}
    failed = {str(d)[:10]: (what, reason) for d, what, reason in extra["failures"] if what in ("surface chain", "surface")}
    rows = []
    for d in sorted(x.date() for x in dl.date.unique()):
        if d <= last:
            continue
        s = str(d)
        if s not in met and s not in failed:
            break                                            # not decided yet: wait, so the file stays in date order
        kind = [k for k, c in [("event day", d in ev_on), ("day before an event", d in ev_next)] if c]
        r = dict(date=s, year=str(d.year), day_type=" and ".join(kind) or "ordinary", events=ev_on.get(d, ""),
                 events_next_day=ev_next.get(d, ""), drawn="False", flagged="False", failed="False", stage="",
                 reason="", fit_max="", range="", warning="")
        if s in met:
            m = met[s]
            r.update(drawn="True", flagged=str(bool(m.flagged)), fit_max=str(round(float(m.fit_max), 3)), range=m.range)
        else:
            r.update(failed="True", stage=failed[s][0], reason=failed[s][1])
        rows.append(r)
    if rows:
        pd.concat([st, pd.DataFrame(rows)], ignore_index=True).to_csv(config.LIVE_STATUS_CSV, index=False)
    return [r["date"] for r in rows]


def run(now=None, fetch_fn=fetch):
    started = dt.datetime.now()
    write_status(last_started=started, running=True)
    log("run started")
    hashes_before = frozen_hashes()
    try:
        with run_lock():
            closes, bills, vix = fred_inputs()
            latest = cal.latest_closed_day(now)
            ro = duckdb.connect(str(config.DUCKDB_PATH), read_only=True)
            last_chain = max(db_dates(ro, config.DUCKDB_TABLE, "trade_date"))
            last_surface = max(db_dates(ro, config.SURFACE_SUMMARY_TABLE, "trade_date"))
            ro.close()
            failures = fetch_fn(cal.trading_days_after(last_chain, latest), cal.trading_days_after(last_surface, latest))
            con = connect_write()
            try:
                init_live(con)
                ensure_tables(con)
                before = guard.snapshot(con)
                con.execute("BEGIN TRANSACTION")
                try:
                    inserted, summary, extra = write(con, closes, bills, vix, failures)
                    guard.verify(con, before, inserted)
                    guard.verify_v1(con, config.V1_CHECKSUMS_CSV)
                except Exception:
                    con.execute("ROLLBACK")
                    raise
                con.execute("COMMIT")
                status_days = write_live_files(con, extra)
                data_to = max(db_dates(con, config.SURFACE_SUMMARY_TABLE, "trade_date"))
                moves_to = max(db_dates(con, config.RESULTS_TABLE, "date"))
            finally:
                con.close()
        if frozen_hashes() != hashes_before:
            changed = [f for f in FROZEN_FILES if frozen_hashes()[f] != hashes_before[f]]
            raise guard.ChecksumChanged(f"frozen files changed: {changed}")
        for k, v in summary.items():
            if v:
                log(f"{k}: {', '.join(v)}")
        if status_days:
            log(f"status rows: {', '.join(status_days)}")
        for d, what, reason in extra["failures"]:
            log(f"not available yet: {d} {what}: {reason}")
        log(f"run finished: surfaces to {data_to}, event moves to {moves_to}; checksums of existing rows unchanged")
        write_status(running=False, ok=True, last_success=dt.datetime.now(), data_to=data_to, event_moves_to=moves_to,
                     error=None, warnings=[f"{d} {w}: {r}" for d, w, r in extra["failures"]])
        return 0
    except Exception as e:
        msg = re.sub(r"(api[_-]?key|token)\S*", "[hidden]", f"{type(e).__name__}: {e}", flags=re.I)
        log(f"RUN FAILED: {msg}")
        log(traceback.format_exc().strip().splitlines()[-1])
        write_status(running=False, ok=False, last_failure=dt.datetime.now(), error=msg)
        return 1
