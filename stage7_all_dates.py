r"""Stage 7: surfaces for every trading day in date_list since 2020-01-02 (README, "Stage 7").
Same method, cleaning rules and limits; existing rows are never rewritten.

  --plan        count the trading days not yet in vol_surface; estimate time and disk
  --checksums   save row checksums of the existing tables (once; never overwritten)
  --verify      check those checksums against the existing rows now
  --batch N     fetch, compute and append batch N (about 100 dates), then verify
  (no flag)     run every batch not yet done, each in a fresh process (low memory), then --status
  --status      write data/all_dates_status.csv
Run:  .venv\Scripts\python.exe stage7_all_dates.py --plan
"""
import datetime as dt
import subprocess
import sys
import time

import duckdb
import pandas as pd

import config
import fetch_surface
import skew_metrics
import vol_page
import vol_surface
from pull_chains import already_saved

START = dt.date(2020, 1, 2)
BATCH = 100
TODO_CSV = config.PROJECT_DIR / "data" / "stage7_todo.csv"                  # fixed list, written once
CHECKSUMS_CSV = config.PROJECT_DIR / "data" / "stage7_checksums_before.csv"
EXISTING_CSV = config.PROJECT_DIR / "data" / "stage7_existing_dates.csv"     # dates each checksum covers
FETCH_ERRORS_CSV = config.PROJECT_DIR / "data" / "all_dates_fetch_errors.csv"
SURFACE_FAILURES_CSV = config.PROJECT_DIR / "data" / "all_dates_surface_failures.csv"
STATUS_CSV = config.PROJECT_DIR / "data" / "all_dates_status.csv"
LOG = config.PROJECT_DIR / "data" / "stage7_log.txt"
# Tables checked; those with dates are checked on the dates present before Stage 7.
DATED = ["vol_surface", "surface_expiries", "surface_date_summary", "surface_metrics"]
WHOLE = ["event_skew_changes", "event_fixed_strike", "control_pair_changes"]


def trading_days():
    dl = pd.read_csv(config.DATE_LIST_CSV, parse_dates=["date", "trade_date"])
    return sorted(d for d in set(dl.date.dt.date) | set(dl.trade_date.dt.date) if d >= START), dl


def plan():
    days, _ = trading_days()
    con = duckdb.connect(str(config.DUCKDB_PATH), read_only=True)
    have = set(con.execute(f"SELECT DISTINCT trade_date FROM {config.SURFACE_TABLE}").df().trade_date.map(
        lambda x: pd.Timestamp(x).date()))
    con.close()
    new = [d for d in days if d not in have]
    raw = [f for f in config.SURFACE_RAW_DIR.rglob("*") if f.is_file()]
    n_raw = sum(1 for p in config.SURFACE_RAW_DIR.iterdir() if p.is_dir())
    mb_date = sum(f.stat().st_size for f in raw) / 1e6 / n_raw
    print(f"Trading days in date_list {days[0]} to {days[-1]}: {len(days)}; in {config.SURFACE_TABLE}: {len(have)}; "
          f"not yet: {len(new)}")
    print("Not yet, by year:", pd.Series([d.year for d in new]).value_counts().sort_index().to_dict())
    fetch_h, comp_h = len(new) * 9.5 / 3600, len(new) * 1.5 / 3600
    print(f"Estimated time: fetch ~{fetch_h:.1f} h (about 9.5 s per date at 2 at a time, as in Stages 4 and 5b), "
          f"compute ~{comp_h * 60:.0f} min; total ~{fetch_h + comp_h:.1f} h")
    print(f"Estimated disk: Parquet +{len(new) * mb_date / 1000:.2f} GB ({mb_date:.2f} MB per date); "
          f"DuckDB +{len(new) * 0.5 / 1000:.1f} to +{len(new) * 1.4 / 1000:.1f} GB "
          f"(now {config.DUCKDB_PATH.stat().st_size / 1e9:.2f} GB)")
    if fetch_h + comp_h > 6 or len(new) * (mb_date + 1.4) / 1000 > 3:
        raise SystemExit("Over 6 hours or 3 GB: stop and report.")
    if not TODO_CSV.exists():
        pd.DataFrame({"date": new}).to_csv(TODO_CSV, index=False)
        print(f"Wrote {TODO_CSV} ({len(new)} dates, {-(-len(new) // BATCH)} batches)")


def table_checksums(con, existing):
    rows = []
    for t in DATED + WHOLE:
        where = ""
        if t in DATED:
            ds = existing[existing.table == t].trade_date
            con.register("old_dates", pd.DataFrame({"d": ds.astype(str)}))
            where = " WHERE CAST(trade_date AS VARCHAR) IN (SELECT d FROM old_dates)"
        n, h = con.execute(f"SELECT count(*), sum(hash(t)::HUGEINT) FROM {t} t{where}").fetchone()
        rows.append(dict(table=t, rows=n, hash_sum=str(h)))
    return pd.DataFrame(rows)


def save_checksums():
    if CHECKSUMS_CSV.exists():
        raise SystemExit(f"{CHECKSUMS_CSV} exists; it is never overwritten.")
    con = duckdb.connect(str(config.DUCKDB_PATH), read_only=True)
    existing = pd.concat([con.execute(f"SELECT DISTINCT '{t}' AS table, CAST(trade_date AS VARCHAR) AS trade_date "
                                      f"FROM {t}").df() for t in DATED])
    existing.to_csv(EXISTING_CSV, index=False)
    cs = table_checksums(con, existing)
    con.close()
    cs.to_csv(CHECKSUMS_CSV, index=False)
    print(cs.to_string(index=False))


def verify(con=None):
    own = con is None
    con = con or duckdb.connect(str(config.DUCKDB_PATH), read_only=True)
    now = table_checksums(con, pd.read_csv(EXISTING_CSV, dtype=str))
    if own:
        con.close()
    before = pd.read_csv(CHECKSUMS_CSV, dtype={"hash_sum": str})
    m = before.merge(now, on="table", suffixes=("_before", "_now"))
    m["match"] = (m.rows_before == m.rows_now) & (m.hash_sum_before == m.hash_sum_now)
    return m


def merge_log(path, new, dates):
    """Replace the rows of `dates` in a per-date log CSV with `new`."""
    old = pd.read_csv(path, dtype=str) if path.exists() else pd.DataFrame(columns=new.columns)
    old = old[~old.date.isin([str(d) for d in dates])]
    pd.concat([old, new.astype(str)]).sort_values("date").to_csv(path, index=False)


def batch(n):
    todo = [dt.date.fromisoformat(d) for d in pd.read_csv(TODO_CSV, dtype=str).date]
    dates = todo[n * BATCH:(n + 1) * BATCH]
    t0 = time.monotonic()
    attempted = [d for d in dates if not already_saved(d, config.SURFACE_RAW_DIR)]
    if attempted:
        merge_log(FETCH_ERRORS_CSV, fetch_surface.main(attempted), attempted)
    t1 = time.monotonic()
    con = duckdb.connect(str(config.DUCKDB_PATH))
    con.execute("BEGIN TRANSACTION")
    summ, failures = vol_surface.append_dates(con, dates)
    if len(summ):
        days = [str(pd.Timestamp(d).date()) for d in summ.trade_date]
        have = set(con.execute("SELECT trade_date FROM surface_metrics").df().trade_date)
        days = [d for d in days if d not in have]
        opts, exps, _ = vol_page.load(con, days)
        metrics, _ = skew_metrics.build_metrics(opts, exps, days)
        if len(metrics):
            con.execute("INSERT INTO surface_metrics BY NAME SELECT * FROM metrics")
    con.execute("COMMIT")
    tried = [d for d in dates if already_saved(d, config.SURFACE_RAW_DIR)]
    merge_log(SURFACE_FAILURES_CSV, pd.DataFrame(failures, columns=["date", "reason"]), tried)
    v = verify(con)
    con.close()
    print(f"STAGE7 batch {n}: {len(dates)} dates, fetched {len(attempted)}, computed {len(summ)}, "
          f"surface failures {len(failures)}; fetch {(t1 - t0) / 60:.1f} min, compute {(time.monotonic() - t1) / 60:.1f} min; "
          f"checksums {'all match' if v.match.all() else 'CHANGED'}", flush=True)
    if not v.match.all():
        print(v.to_string(index=False))
        raise SystemExit("CHECKSUM CHANGED: stop and report.")


def run():
    todo = pd.read_csv(TODO_CSV, dtype=str).date
    n_batches = -(-len(todo) // BATCH)
    t0 = time.monotonic()
    for n in range(n_batches):
        with open(LOG, "a", encoding="utf-8") as log:
            log.write(f"\n######## batch {n} started {dt.datetime.now():%Y-%m-%d %H:%M:%S} ########\n")
            log.flush()
            r = subprocess.run([sys.executable, __file__, "--batch", str(n)], stdout=log, stderr=subprocess.STDOUT)
        lines = [l for l in LOG.read_text(encoding="utf-8").splitlines() if l.startswith(("STAGE7", "CHECKSUM"))]
        print(lines[-1] if lines else "", f"(total {(time.monotonic() - t0) / 60:.0f} min)", flush=True)
        if r.returncode != 0:
            raise SystemExit(f"Batch {n} stopped (exit {r.returncode}); see {LOG}")
    status()


def status():
    days, dl = trading_days()
    ev = dl[dl.event_type != "NONE"]
    ev_types = ev.groupby(ev.date.dt.date).event_type.apply(lambda x: "+".join(sorted(set(x))))
    before = ev.groupby(ev.trade_date.dt.date).event_type.apply(lambda x: "+".join(sorted(set(x))))
    con = duckdb.connect(str(config.DUCKDB_PATH), read_only=True)
    surf = set(con.execute(f"SELECT trade_date FROM {config.SURFACE_SUMMARY_TABLE}").df().trade_date.map(
        lambda x: pd.Timestamp(x).date()))
    met = pd.concat([con.execute("SELECT trade_date, flagged, fit_max, range FROM surface_metrics").df(),
                     con.execute("SELECT trade_date, flagged, fit_max, range FROM control_surface_metrics").df()])
    con.close()
    met = met.drop_duplicates("trade_date").set_index(met.drop_duplicates("trade_date").trade_date.astype(str))
    # Fetch outcomes: Stage 7 log first, then the earlier runs (surface_date_status.csv, Stages 4/5b).
    fe = pd.read_csv(FETCH_ERRORS_CSV, dtype=str) if FETCH_ERRORS_CSV.exists() else pd.DataFrame(columns=["date", "level", "reason"])
    # A fetch failure from an interrupted run (e.g. a network drop) is stale once the date is saved.
    stale = (fe.level == "failed") & fe.date.map(lambda s: already_saved(dt.date.fromisoformat(s), config.SURFACE_RAW_DIR))
    fe = fe[~stale]
    fe.to_csv(FETCH_ERRORS_CSV, index=False)
    old = pd.read_csv(config.PROJECT_DIR / "data" / "surface_date_status.csv", dtype=str)
    last5b = pd.read_csv(config.SURFACE_FETCH_ERRORS_CSV, dtype=str).drop_duplicates(["date", "level", "reason"])
    sf = pd.read_csv(SURFACE_FAILURES_CSV, dtype=str) if SURFACE_FAILURES_CSV.exists() else pd.DataFrame(columns=["date", "reason"])
    rows = []
    for d in days:
        s = str(d)
        kind = [k for k, c in [("event day", d in ev_types.index), ("day before an event", d in before.index)] if c]
        warn = "; ".join(fe[(fe.date == s) & (fe.level == "warning")].reason)
        if not warn:
            warn = "; ".join(x for x in old[old.date == s].warning.dropna()) or \
                   "; ".join(last5b[(last5b.date == s) & (last5b.level == "warning")].reason)
        r = dict(date=s, year=d.year, day_type=" and ".join(kind) or "ordinary",
                 events=ev_types.get(d, "") or "", events_next_day=before.get(d, "") or "",
                 drawn=False, flagged=False, failed=False, stage="", reason="", fit_max="", range="", warning=warn)
        fail_fetch = fe[(fe.date == s) & (fe.level == "failed")]
        if s in met.index:
            m = met.loc[s]
            r.update(drawn=True, flagged=bool(m.flagged), fit_max=round(float(m.fit_max), 3), range=m.range)
        elif d in surf:
            r.update(failed=True, stage="page", reason="computed, but no expiry can be drawn on the page")
        elif len(sf[sf.date == s]):
            r.update(failed=True, stage="surface", reason=sf[sf.date == s].reason.iloc[-1])
        elif len(fail_fetch):
            r.update(failed=True, stage="fetch", reason=fail_fetch.reason.iloc[-1])
        else:
            r.update(failed=True, stage="fetch", reason="not fetched")
        rows.append(r)
    out = pd.DataFrame(rows)
    out.to_csv(STATUS_CSV, index=False)
    out["status"] = out.drawn.map({True: "drawn"}).fillna("failed")
    out.loc[out.flagged, "status"] = "drawn, flagged"
    print(f"Wrote {STATUS_CSV}: {len(out)} trading days")
    print(pd.crosstab(out.year, out.status, margins=True).to_string())
    print(pd.crosstab(out.day_type, out.status, margins=True).to_string())
    print(out[out.failed].groupby(["stage"]).size().to_string())


if __name__ == "__main__":
    a = sys.argv[1:]
    if "--plan" in a:
        plan()
    elif "--checksums" in a:
        save_checksums()
    elif "--verify" in a:
        v = verify()
        print(v.to_string(index=False))
    elif "--batch" in a:
        batch(int(a[a.index("--batch") + 1]))
    elif "--status" in a:
        status()
    else:
        run()
