"""Append-only guard. Before a run writes, every row of every table it can touch is summed by row hash; after the
writes, the same sum over all rows except the ones inserted by this run must be unchanged. A second, fixed set of
checksums covers the frozen v1 rows (dated on or before config.V1_END) and is checked on every run.

event_premium_provisional is the only table whose rows may change (a provisional premium is recomputed each run
until its window is complete), so it is not guarded."""
import csv

import config

# table -> its date column (the key the daily update appends by)
TABLES = {
    "date_list": "date", "event_moves": "date", "event_premium": "date", "event_priced": "date",
    config.DUCKDB_TABLE: "trade_date", "vol_surface": "trade_date", "surface_expiries": "trade_date",
    "surface_date_summary": "trade_date", "surface_metrics": "trade_date", "event_skew_changes": "event_date",
    "event_fixed_strike": "event_date", "control_surface_metrics": "trade_date", "control_pair_changes": "event_date",
}
NOT_GUARDED = ("event_premium_provisional",)


class ChecksumChanged(Exception):
    pass


def _existing(con):
    names = {r[0] for r in con.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    return [t for t in TABLES if t in names]


def _sum(con, table, where=""):
    n, s = con.execute(f"SELECT count(*), coalesce(sum(hash(x)::HUGEINT), 0) FROM {table} x {where}").fetchone()
    return int(n), int(s)


def _not_in(col, dates):
    if not dates:
        return ""
    lits = ", ".join(f"DATE '{d}'" for d in sorted({str(d)[:10] for d in dates}))
    return f"WHERE CAST({col} AS DATE) NOT IN ({lits})"


def snapshot(con):
    return {t: _sum(con, t) for t in _existing(con)}


def assert_new(con, table, dates):
    """Append precondition: no row of `table` has any of these dates yet."""
    if not dates:
        return
    lits = ", ".join(f"DATE '{d}'" for d in sorted({str(d)[:10] for d in dates}))
    n = con.execute(f"SELECT count(*) FROM {table} WHERE CAST({TABLES[table]} AS DATE) IN ({lits})").fetchone()[0]
    if n:
        raise ChecksumChanged(f"{table} already has rows for {sorted(dates)}; appending would duplicate them")


def verify(con, before, inserted):
    """inserted: {table: dates inserted this run}. Raises ChecksumChanged on any difference."""
    bad = []
    for t, (n0, s0) in before.items():
        n, s = _sum(con, t, _not_in(TABLES[t], inserted.get(t, ())))
        if (n, s) != (n0, s0):
            bad.append(f"{t}: {n0} rows before, {n} now outside this run's new dates (sums {'equal' if s == s0 else 'differ'})")
    if bad:
        raise ChecksumChanged("existing rows changed: " + "; ".join(bad))


def v1_checksums(con):
    out = {}
    for t in _existing(con):
        # The event-study quote table is keyed by the day before: its v1 rows are those before the cut-off (the
        # 2026-10-09 chain prices 2026-10-12, a live date). Every other table is keyed by its own date.
        op = "<" if t == config.DUCKDB_TABLE else "<="
        out[t] = _sum(con, t, f"WHERE CAST({TABLES[t]} AS DATE) {op} DATE '{config.V1_END}'")
    return out


def save_v1(path, sums):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["table", "rows", "hash_sum"])
        for t, (n, s) in sorted(sums.items()):
            w.writerow([t, n, s])


def load_v1(path):
    with open(path, encoding="utf-8") as f:
        return {r["table"]: (int(r["rows"]), int(r["hash_sum"])) for r in csv.DictReader(f)}


def verify_v1(con, path):
    saved, now = load_v1(path), v1_checksums(con)
    bad = [f"{t}: saved {saved[t]}, now {now.get(t)}" for t in saved if now.get(t) != saved[t]]
    if bad:
        raise ChecksumChanged("frozen v1 rows changed: " + "; ".join(bad))
