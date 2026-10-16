r"""Event study: end-of-day snapshot (event_moves_eod) vs options-close snapshot (event_moves).

Same summary and event-premium definitions as summarize.py, side by side, and every
percentage figure that moved by more than COMPARE_THRESHOLD_PP percentage points.
Run after calc_moves.py:  .venv\Scripts\python.exe compare_snapshots.py
"""
import duckdb
import pandas as pd

import config
from summarize import TYPES, by_event_type, event_premium

COMPARE_THRESHOLD_PP = 0.03


def load(table):
    con = duckdb.connect(str(config.DUCKDB_PATH), read_only=True)
    df = con.execute(f"SELECT * FROM {table} ORDER BY date").df()
    con.close()
    return df


def stats(moves):
    rows = []
    for t in TYPES:
        g = by_event_type(moves).query("event_type == @t")
        rows.append(dict(event_type=t, n=len(g),
                         mean_implied=g.implied_move.mean() * 100, median_implied=g.implied_move.median() * 100,
                         mean_actual=g.actual_move.mean() * 100, median_actual=g.actual_move.median() * 100,
                         median_ratio=g.ratio.median(), share_above=(g.actual_move > g.implied_move).mean() * 100))
    return pd.DataFrame(rows).set_index("event_type")


def premium_stats(moves):
    prem = by_event_type(event_premium(moves))
    rows = []
    for t in TYPES[1:]:
        g = prem[prem.event_type == t]
        rows.append(dict(event_type=t, n=len(g), median_baseline=g.baseline_implied.median() * 100,
                         median_premium=g.premium.median() * 100, mean_premium=g.premium.mean() * 100,
                         median_variance_premium=g.variance_premium.median() * 1e4))
    return pd.DataFrame(rows).set_index("event_type")


def side_by_side(a, b, pp_cols):
    out = pd.concat({"EOD": a, "16:15": b}, axis=1).swaplevel(axis=1)
    cols = [c for c in a.columns]
    out = out[[(c, s) for c in cols for s in ("EOD", "16:15")]].round(3)
    moved = []
    for t in a.index:
        for c in pp_cols:
            d = b.loc[t, c] - a.loc[t, c]
            if abs(d) > COMPARE_THRESHOLD_PP:
                moved.append(f"{t} {c}: {a.loc[t, c]:.3f} -> {b.loc[t, c]:.3f} ({d:+.3f} pp)")
    return out, moved


def main():
    eod, new = load(f"{config.RESULTS_TABLE}_eod"), load(config.RESULTS_TABLE)
    pd.set_option("display.width", 250)
    print(f"Dates measured: EOD {len(eod)}, 16:15 {len(new)}")
    print(f"after_hours_gap dates: EOD {int(eod.after_hours_gap.sum())}, 16:15 {int(new.after_hours_gap.sum())}"
          + (": " + ", ".join(map(str, new.date[new.after_hours_gap])) if new.after_hours_gap.any() else ""))
    hd_eod, hd_new = set(eod.date[eod.half_day]), set(new.date[new.half_day])
    print(f"half-day snapshot dates measured: EOD {len(hd_eod)}, 16:15 {len(hd_new)} "
          f"(added: {', '.join(map(str, sorted(hd_new - hd_eod))) or 'none'})")
    gained, lost = sorted(set(new.date) - set(eod.date)), sorted(set(eod.date) - set(new.date))
    print(f"dates measured only at 16:15: {len(gained)}; only at EOD: {len(lost)}"
          + (f" ({', '.join(map(str, lost))})" if lost else ""))

    s, moved_s = side_by_side(stats(eod), stats(new), ["mean_implied", "median_implied", "mean_actual", "median_actual"])
    print("\nSummary by event type (moves in %, share_above in % of days)")
    print(s.to_string())
    p, moved_p = side_by_side(premium_stats(eod), premium_stats(new), ["median_baseline", "median_premium", "mean_premium"])
    print("\nEvent premium, rough estimate (pp; variance premium in %^2 straddle units)")
    print(p.to_string())
    print(f"\nPercentage figures that moved by more than {COMPARE_THRESHOLD_PP} pp:")
    for line in moved_s + moved_p:
        print("  " + line)
    if not moved_s + moved_p:
        print("  none")


if __name__ == "__main__":
    main()
