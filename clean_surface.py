r"""Clean smiles and clean surface built from the computed implied vols (vol_surface table).

Method (fixed before looking at results):
- Expiries: Friday expiries (weeklies and monthlies) from CLEAN_MIN_DTE to CLEAN_MAX_DTE days.
- Points: computed OTM implied vols between the 10-delta put and the 10-delta call
  (x = 1 - undiscounted call delta in [0.10, 0.90]).
- Clean smile: per expiry, a least-squares cubic polynomial of implied vol in
  log-moneyness ln(K/F), fitted to those points; drawn only inside their range.
- Clean surface: each fitted smile evaluated on a regular x grid (delta, or strike % of
  forward), then light smoothing of total variance (vol^2 x t) across expiries at fixed x:
  1/2 own value + 1/2 the straight line in t through the two neighbouring expiries (a 1-2-1
  filter that allows for uneven spacing). No extrapolation: cells outside an expiry's points
  stay empty.
Checks: smoothed-vs-real gaps for smiles and surface; total variance non-decreasing in
expiry at every delta on the clean surface.
Run:  .venv\Scripts\python.exe clean_surface.py   (prints checks, writes the 2D PNGs)
"""
import math

import duckdb
import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

import config

_erf = np.vectorize(math.erf)
DELTA_TICKS = [(0.10, "10Δ put"), (0.25, "25Δ put"), (0.50, "ATM"), (0.75, "25Δ call"), (0.90, "10Δ call")]
METHOD = ("cubic least-squares fit of IV in ln(K/F) per expiry; surface = fitted smiles on a regular grid "
          "with light spacing-aware smoothing of total variance across neighbouring expiries")


def x_delta(k, sigma, t):
    """1 - undiscounted call delta N(d1): 0.10 = 10-delta put, 0.5 = ATM, 0.90 = 10-delta call."""
    sd = sigma * np.sqrt(t)
    d1 = (-k + 0.5 * sd * sd) / sd
    return 1.0 - 0.5 * (1.0 + _erf(d1 / math.sqrt(2.0)))


def load_points():
    con = duckdb.connect(str(config.DUCKDB_PATH), read_only=True)
    opts = con.execute(f"SELECT * FROM {config.SURFACE_TABLE}").df()
    con.close()
    opts["trade_date"] = opts.trade_date.astype(str).str[:10]
    opts = opts[opts.trade_date.isin([str(d) for d in config.SURFACE_DATES_ORIGINAL])].copy()   # pinned to the 8
    opts["expiration"] = pd.to_datetime(opts.expiration)
    opts["dte"] = (opts.expiration - pd.to_datetime(opts.trade_date)).dt.days
    opts = opts[(opts.expiration.dt.weekday == 4) & opts.dte.between(config.CLEAN_MIN_DTE, config.CLEAN_MAX_DTE)].copy()
    opts["expiration"] = opts.expiration.dt.date.astype(str)
    opts["x_delta"] = x_delta(opts.log_moneyness.values, opts.iv.values, opts.t_years.values)
    lo, hi = config.CLEAN_DELTA_RANGE
    return opts[opts.x_delta.between(lo, hi)].copy()


def fit_smiles(points):
    """{(date, expiry): dict(coef, kmin, kmax, t, dte, pts)} for expiries with enough points."""
    fits = {}
    for (day, exp), g in points.groupby(["trade_date", "expiration"]):
        if len(g) < config.CLEAN_MIN_POINTS:
            continue
        coef = np.polyfit(g.log_moneyness, g.iv, config.CLEAN_FIT_DEGREE)
        fits[(day, exp)] = dict(coef=coef, kmin=g.log_moneyness.min(), kmax=g.log_moneyness.max(),
                                t=g.t_years.iloc[0], dte=int(g.dte.iloc[0]), pts=g.sort_values("log_moneyness"))
    return fits


def smile_curve(f, n=200):
    k = np.linspace(f["kmin"], f["kmax"], n)
    iv = np.polyval(f["coef"], k)
    return k, iv, x_delta(k, iv, f["t"])


def row_on_grid(f, axis, grid):
    """Fitted IV of one expiry on the regular x grid (NaN outside its points' range)."""
    k, iv, xd = smile_curve(f, 400)
    if axis == "delta":
        order = np.argsort(xd)
        out = np.interp(grid, xd[order], iv[order])
        out[(grid < xd.min()) | (grid > xd.max())] = np.nan
    else:                                   # strike % of forward
        kg = np.log(grid / 100.0)
        out = np.polyval(f["coef"], kg)
        out[(kg < f["kmin"]) | (kg > f["kmax"])] = np.nan
    return out


def smooth_total_variance(rows, ts):
    """Light smoothing of total variance across expiries at each x: own value blended with the
    straight line (in t) through the two neighbouring expiries. Spacing-aware 1-2-1 filter;
    cells with a missing neighbour, and the first and last expiry, are left as fitted."""
    w = rows ** 2 * ts[:, None]
    out = w.copy()
    a = config.CLEAN_SMOOTH_OWN_WEIGHT
    for i in range(1, len(ts) - 1):
        line = w[i - 1] + (w[i + 1] - w[i - 1]) * (ts[i] - ts[i - 1]) / (ts[i + 1] - ts[i - 1])
        ok = ~np.isnan(w[i]) & ~np.isnan(line)
        out[i, ok] = a * w[i, ok] + (1 - a) * line[ok]
    return np.sqrt(out / ts[:, None])


def grid_for(day, fits, axis):
    lo, hi = config.CLEAN_DELTA_RANGE if axis == "delta" else config.CLEAN_STRIKE_PCT
    grid = np.linspace(lo, hi, config.CLEAN_GRID_POINTS)
    keys = sorted((k for k in fits if k[0] == day), key=lambda k: fits[k]["t"])
    ts = np.array([fits[k]["t"] for k in keys])
    rows = np.array([row_on_grid(fits[k], axis, grid) for k in keys])
    return grid, keys, rows, smooth_total_variance(rows, ts)


def point_x(pts, axis):
    return pts.x_delta.values if axis == "delta" else 100 * pts.strike.values / pts.forward.values


def checks(fits):
    report = {}
    for day in sorted({k[0] for k in fits}):
        keys = [k for k in fits if k[0] == day]
        # Smiles: fitted curve vs the real points it was fitted to.
        sg = []
        for k in keys:
            p = fits[k]["pts"]
            gap = np.abs(np.polyval(fits[k]["coef"], p.log_moneyness.values) - p.iv.values)
            sg += [(k[1], s, x, g) for s, x, g in zip(p.strike, p.x_delta, gap)]
        sg = pd.DataFrame(sg, columns=["expiration", "strike", "x_delta", "gap"])
        # Surface: smoothed surface (delta grid) at each real point's delta, on its expiry row.
        grid, gkeys, rows, smooth = grid_for(day, fits, "delta")
        fg = []
        for i, k in enumerate(gkeys):
            p = fits[k]["pts"]
            ok = ~np.isnan(smooth[i])
            vals = np.interp(p.x_delta.values, grid[ok], smooth[i][ok], left=np.nan, right=np.nan)
            fg += [(k[1], s, x, abs(v - r)) for s, x, v, r in zip(p.strike, p.x_delta, vals, p.iv) if v == v]
        fg = pd.DataFrame(fg, columns=["expiration", "strike", "x_delta", "gap"])
        # Total variance must not fall with expiry at any delta.
        ts = np.array([fits[k]["t"] for k in gkeys])
        w = smooth ** 2 * ts[:, None]
        tv = []
        for j, xv in enumerate(grid):
            col = [(ts[i], w[i, j], gkeys[i][1]) for i in range(len(ts)) if w[i, j] == w[i, j]]
            for (t0, w0, e0), (t1, w1, e1) in zip(col[:-1], col[1:]):
                if w1 < w0:
                    tv.append((xv, e0, e1, w1 / w0 - 1))
        report[day] = dict(smiles=sg, surface=fg, total_var=pd.DataFrame(tv, columns=["x_delta", "from", "to", "change"]),
                           expiries=len(keys), points=int(sum(len(fits[k]["pts"]) for k in keys)))
    return report


def shown_expiries(fits, day):
    keys = [k for k in fits if k[0] == day]
    chosen = []
    for target in config.CLEAN_SMILE_TARGET_DTES:
        best = min(keys, key=lambda k: abs(fits[k]["dte"] - target))
        if best not in chosen:
            chosen.append(best)
    return sorted(chosen, key=lambda k: fits[k]["dte"])


def smiles_png(fits, day):
    chosen = shown_expiries(fits, day)
    cmap = plt.get_cmap("viridis")
    fig, ax = plt.subplots(figsize=(8.5, 5.8))
    n, labels = 0, []
    for i, k in enumerate(chosen):
        f = fits[k]; color = cmap(0.85 * i / max(len(chosen) - 1, 1))   # stop before pale yellow
        _, iv, xd = smile_curve(f)
        ax.plot(xd, iv * 100, color=color, lw=1.6)
        ax.scatter(f["pts"].x_delta, f["pts"].iv * 100, color=color, s=6)
        n += len(f["pts"])
        j = np.argmax(xd)
        labels.append([iv[j] * 100, xd[j], f"{k[1]} ({f['dte']}d)", color])
    # Spread the end labels vertically so they do not overlap where the curves converge.
    labels.sort(key=lambda r: r[0])
    gap = 0.045 * (ax.get_ylim()[1] - ax.get_ylim()[0])
    for a_, b_ in zip(labels[:-1], labels[1:]):
        b_[0] = max(b_[0], a_[0] + gap)
    for y, x, text, color in labels:
        ax.annotate(text, (config.CLEAN_DELTA_RANGE[1] + 0.01, y), fontsize=7.5, color=color, va="center")
    ax.set_xticks([v for v, _ in DELTA_TICKS], [s for _, s in DELTA_TICKS])
    ax.set_xlim(config.CLEAN_DELTA_RANGE[0] - 0.02, config.CLEAN_DELTA_RANGE[1] + 0.12)
    ax.set(xlabel="delta (OTM puts left, OTM calls right)", ylabel="implied vol (%)",
           title=f"SPXW clean smiles, {day}")
    fig.text(0.5, 0.01, f"{day}: {len(chosen)} Friday expiries shown (14 days to ~6 months), {n} computed options "
             f"(dots) between the 10-delta put and 10-delta call. Lines: least-squares cubic in ln(K/F) per expiry, "
             f"drawn only within each expiry's points.", ha="center", fontsize=8, wrap=True)
    fig.tight_layout(rect=(0, 0.06, 1, 1))
    path = str(config.CLEAN_SMILES_PNG).format(date=day)
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return path


def main():
    fits = fit_smiles(load_points())
    report = checks(fits)
    for day, r in report.items():
        print(f"\n=== {day}: {r['expiries']} Friday expiries fitted, {r['points']} computed points in the 10-delta range ===")
        for name in ("smiles", "surface"):
            g = r[name]
            print(f"{name:8s}: gap to real points  mean {g.gap.mean() * 100:.3f}  max {g.gap.max() * 100:.3f} vol points "
                  f"(n={len(g)})")
            big = g[g.gap > config.CLEAN_GAP_LIMIT].sort_values("gap", ascending=False)
            if len(big):
                print(f"  {len(big)} points above {config.CLEAN_GAP_LIMIT * 100:.1f} vol points:")
                for e, grp in big.groupby("expiration"):
                    worst = grp.iloc[0]
                    print(f"    {e}: {len(grp)} points, worst strike {worst.strike:g} "
                          f"(x={worst.x_delta:.2f}) gap {worst.gap * 100:.2f}")
        tv = r["total_var"]
        print(f"total variance falls with expiry at {len(tv)} (delta, expiry pair) cells" + (":" if len(tv) else ""))
        for _, row in tv.iterrows():
            print(f"    x={row.x_delta:.2f}: {row['from']} -> {row['to']} ({row.change:+.2%})")
        print("PNG:", smiles_png(fits, day))


if __name__ == "__main__":
    main()
