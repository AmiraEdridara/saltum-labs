"""Step 5: summary statistics by event type, a rough event-premium estimate, and one figure.

Run after calc_moves.py:  .venv\\Scripts\\python.exe summarize.py
"""
import duckdb
import matplotlib
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

import config

TYPES = ["NONE", "CPI", "NFP", "FOMC"]


def load_moves():
    con = duckdb.connect(str(config.DUCKDB_PATH), read_only=True)
    moves = con.execute(f"SELECT * FROM {config.RESULTS_TABLE} ORDER BY date").df()
    con.close()
    return moves


def by_event_type(moves):
    """One row per (date, single event type); a CPI+FOMC day counts under both."""
    return moves.assign(event_type=moves.event_type.str.split("+")).explode("event_type")


def small_flag(n):
    return f"n<{config.SMALL_SAMPLE}: too few to rely on" if n < config.SMALL_SAMPLE else ""


def summary_table(long):
    rows = []
    for t in TYPES:
        g = long[long.event_type == t]
        n, above = len(g), int((g.actual_move > g.implied_move).sum())
        if n == 0:
            continue
        rows.append({
            "event_type": t, "n": n,
            "mean_implied": f"{g.implied_move.mean():.2%}", "median_implied": f"{g.implied_move.median():.2%}",
            "mean_actual": f"{g.actual_move.mean():.2%}", "median_actual": f"{g.actual_move.median():.2%}",
            "median_ratio": f"{g.ratio.median():.2f}",
            "actual>implied": f"{above}/{n} ({above / n:.0%})",
            "note": small_flag(n),
        })
    return pd.DataFrame(rows)


def event_premium(moves):
    """Each event day's implied move minus the median implied move of NONE days within
    BASELINE_WINDOW trading days either side (positions in the study's trading-day list)."""
    all_dates = sorted(pd.read_csv(config.DATE_LIST_CSV, parse_dates=["date"]).date.dt.date.unique())
    pos = {d: i for i, d in enumerate(all_dates)}
    moves = moves.assign(pos=moves.date.map(lambda d: pos[d.date() if hasattr(d, "date") else d]))
    none = moves[moves.event_type == "NONE"]
    rows = []
    for _, ev in moves[moves.event_type != "NONE"].iterrows():
        near = none[(none.pos - ev.pos).abs() <= config.BASELINE_WINDOW]
        base = near.implied_move.median()
        rows.append(dict(date=ev.date, event_type=ev.event_type, half_day=ev.half_day, implied_move=ev.implied_move,
                         baseline_implied=base, baseline_n=len(near),
                         premium=ev.implied_move - base,
                         variance_premium=ev.implied_move ** 2 - base ** 2))
    return pd.DataFrame(rows)


def premium_summary(prem):
    long = by_event_type(prem)
    rows = []
    for t in TYPES[1:]:
        g = long[long.event_type == t]
        n = len(g)
        if n == 0:
            continue
        rows.append({
            "event_type": t, "n": n,
            "median_baseline": f"{g.baseline_implied.median():.2%}",
            "baseline_days_per_event (median)": int(g.baseline_n.median()),
            "median_premium": f"{g.premium.median():+.2%}",
            "mean_premium": f"{g.premium.mean():+.2%}",
            "median_variance_premium (%^2, straddle units)":f"{g.variance_premium.median() * 1e4:+.3f}",
            "premium>0": f"{int((g.premium > 0).sum())}/{n}",
            "note": small_flag(n),
        })
    return pd.DataFrame(rows)


def scatter(moves, counts):
    fig, ax = plt.subplots(figsize=(7.5, 7))
    style = {"NONE": ("#9aa0a6", 12, 0.45), "CPI": ("#d62728", 36, 0.9),
             "NFP": ("#1f77b4", 36, 0.9), "FOMC": ("#2ca02c", 36, 0.9)}
    for label in ["NONE"] + sorted(set(moves.event_type) - {"NONE"}):
        g = moves[moves.event_type == label]
        color, size, alpha = style.get(label, ("#9467bd", 60, 0.95))   # combined days, e.g. CPI+FOMC
        ax.scatter(g.implied_move * 100, g.actual_move * 100, s=size, alpha=alpha, color=color,
                   label=f"{label} (n={len(g)})", edgecolors="none")
    hi = max(moves.implied_move.max(), moves.actual_move.max()) * 100 * 1.05
    ax.plot([0, hi], [0, hi], color="black", lw=1, ls="--", label="actual = implied (45° line)")
    ax.set(xlim=(0, hi), ylim=(0, hi), xlabel="Implied move, straddle / forward (%)",
           ylabel="Actual move, |close / forward - 1| (%)",
           title="SPX one-day options: implied vs actual move")
    ax.legend(loc="upper left", fontsize=9)
    caption = (f"SPXW EOD chains {pd.Timestamp(moves.trade_date.min()).date()} to {pd.Timestamp(moves.trade_date.max()).date()}, expiry next trading day. "
               f"Days: {', '.join(f'{t} {n}' for t, n in counts.items())} "
               f"(a CPI+FOMC day counts under both). Educational illustration only.")
    fig.text(0.5, 0.01, caption, ha="center", fontsize=8, wrap=True)
    fig.tight_layout(rect=(0, 0.05, 1, 1))
    fig.savefig(config.FIGURE_PATH, dpi=150)


def main():
    moves = load_moves()
    long = by_event_type(moves)
    summary = summary_table(long)
    prem = event_premium(moves)
    prem_sum = premium_summary(prem)

    con = duckdb.connect(str(config.DUCKDB_PATH))
    con.execute("CREATE OR REPLACE TABLE event_premium AS SELECT * FROM prem")
    con.close()
    counts = summary.set_index("event_type").n
    scatter(moves, counts)

    pd.set_option("display.width", 200)
    print(f"Implied vs actual one-day move by event type, ALL dates ({len(moves)} dates)\n")
    print(summary.to_string(index=False))
    kept = moves[~moves.after_hours_gap]
    print(f"\nSame, EXCLUDING after_hours_gap dates ({len(kept)} dates, "
          f"{int(moves.after_hours_gap.sum())} excluded)\n")
    print(summary_table(by_event_type(kept)).to_string(index=False))
    print(f"\nEvent premium (ROUGH ESTIMATE): event-day implied move minus the median implied move "
          f"of NONE days within {config.BASELINE_WINDOW} trading days either side\n")
    print(prem_sum.to_string(index=False))
    print("Variance premium is in straddle units: (straddle/F)^2 is about 0.64 sigma^2; "
          "multiply by 1.56 (= 1.25^2) for sigma^2.")

    hd = moves[moves.half_day]
    print(f"\nHalf-day snapshots (chain from an early-close session, unreliable): {len(hd)} dates: "
          + ", ".join(f"{d} [{e}]" for d, e in zip(hd.date, hd.event_type)))

    none = moves[moves.event_type == "NONE"]
    span = (pd.to_datetime(none.date) - pd.to_datetime(none.trade_date)).dt.days
    one, multi = none[span == 1], none[span >= 3]
    print(f"\nCalendar span of NONE days: median implied move {one.implied_move.median():.2%} "
          f"when the option covers 1 calendar day (n={len(one)}), "
          f"{multi.implied_move.median():.2%} when it covers 3+ days, i.e. weekends/holidays (n={len(multi)}).")

    print("""
How to read these numbers: if the straddle were exactly the expected absolute move of a
normal distribution, mean actual would be close to mean implied, the median ratio would be
about 0.85 (0.674 sigma / 0.798 sigma), and actual would exceed implied on about 42% of days
(P(|Z| > 0.798)). Fat tails push the median ratio and that share lower still. So a median
ratio below 1 or a share below 50% is expected even when options are priced in line with
outcomes on average.""")
    print(f"\nFigure saved: {config.FIGURE_PATH}")


if __name__ == "__main__":
    main()
