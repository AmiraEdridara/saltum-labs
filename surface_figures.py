"""Part B step 7: smiles, term structure, surface, and before/after term structure.

Plots the computed points only (no model fit, no smoothing). The surface picture alone
interpolates between points, linearly on a triangulation, and its caption says so.
Run after vol_surface.py:  .venv\\Scripts\\python.exe surface_figures.py
"""
import duckdb
import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.tri as mtri  # noqa: E402

import config


def load():
    con = duckdb.connect(str(config.DUCKDB_PATH), read_only=True)
    opts = con.execute(f"SELECT * FROM {config.SURFACE_TABLE}").df()
    exps = con.execute(f"SELECT * FROM {config.SURFACE_EXPIRY_TABLE}").df()
    con.close()
    for df in (opts, exps):
        df["trade_date"] = df.trade_date.astype(str)
        df["expiration"] = df.expiration.astype(str)
    exps = exps.dropna(subset=["atm_iv"])
    opts = opts.merge(exps[["trade_date", "expiration", "atm_iv", "dte"]], on=["trade_date", "expiration"])
    # Plot window widens with sqrt(t): |log(K/F)| <= PLOT_SD * atm_iv * sqrt(t).
    opts["in_window"] = opts.log_moneyness.abs() <= config.PLOT_SD * opts.atm_iv * np.sqrt(opts.t_years)
    return opts, exps


def save(fig, name, caption):
    fig.text(0.5, 0.01, caption, ha="center", va="bottom", fontsize=8, wrap=True)
    fig.tight_layout(rect=(0, 0.07, 1, 1))
    config.FIG_DIR.mkdir(parents=True, exist_ok=True)
    path = config.FIG_DIR / name
    fig.savefig(path, dpi=150)
    plt.close(fig)
    print(f"  {path}")


def smiles(opts, exps, day):
    e = exps[exps.trade_date == day]
    chosen = sorted({e.loc[(e.dte - d).abs().idxmin(), "expiration"] for d in config.SMILE_DTES})
    fig, ax = plt.subplots(figsize=(8, 5.5))
    n = 0
    for exp in chosen:
        g = opts[(opts.trade_date == day) & (opts.expiration == exp) & opts.in_window].sort_values("log_moneyness")
        n += len(g)
        ax.plot(g.log_moneyness, g.iv * 100, marker="o", ms=2.5, lw=0.8,
                label=f"{exp} ({int(g.dte.iloc[0])}d, n={len(g)})")
    ax.axvline(0, color="grey", lw=0.6)
    ax.set(xlabel="log-moneyness ln(K / F)", ylabel="implied vol (%)", title=f"SPXW smiles, {day}")
    ax.legend(fontsize=8)
    save(fig, f"smiles_{day}.png",
         f"{day}: {n} OTM options plotted (puts left of 0, calls right), {len(chosen)} expiries. "
         f"Window |ln(K/F)| <= {config.PLOT_SD:g} x ATM vol x sqrt(t). Computed points joined by lines; no fitting.")


def term_structure(exps, day):
    e = exps[exps.trade_date == day].sort_values("dte")
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.plot(e.dte, e.atm_iv * 100, marker="o", lw=0.8)
    ax.set(xscale="log", xlabel="days to expiry (log scale)", ylabel="ATM implied vol (%)",
           title=f"SPXW at-the-money term structure, {day}")
    ax.xaxis.set_major_formatter(matplotlib.ticker.ScalarFormatter())
    save(fig, f"term_structure_{day}.png",
         f"{day}: {len(e)} expiries, one ATM vol each (interpolated in log-moneyness between the nearest "
         f"OTM put and call). Calendar time ACT/365 to 16:00 ET, so a Friday-to-Monday expiry includes the weekend.")


def surface(opts, day):
    g = opts[(opts.trade_date == day) & opts.in_window]
    x, y, z = g.log_moneyness.values, np.log10(g.dte.values), g.iv.values * 100
    fig, ax = plt.subplots(figsize=(8.5, 6))
    tri = mtri.Triangulation(x, y)
    # Hide triangles spanning a wide moneyness range: they only bridge the empty space
    # outside the quoted strikes (the triangulation's convex hull), not data.
    xs = x[tri.triangles]
    tri.set_mask(xs.max(axis=1) - xs.min(axis=1) > 0.06)
    cf = ax.tricontourf(tri, z, levels=20, cmap="viridis")
    ax.scatter(x, y, s=1.5, color="white", alpha=0.6)
    fig.colorbar(cf, ax=ax, label="implied vol (%)")
    ticks = [1, 3, 7, 14, 30, 60, 120, 180]
    ax.set_yticks(np.log10(ticks), [str(t) for t in ticks])
    ax.set(xlabel="log-moneyness ln(K / F)", ylabel="days to expiry (log scale)",
           title=f"SPXW implied vol surface, {day}")
    save(fig, f"surface_{day}.png",
         f"{day}: {len(g)} OTM options (white dots). Colours BETWEEN the dots are linear interpolation on a "
         f"Delaunay triangulation (triangles wider than 0.06 in ln(K/F) hidden), for display only; no model fit. Window |ln(K/F)| <= {config.PLOT_SD:g} x ATM vol x sqrt(t).")


def before_after(exps):
    fig, ax = plt.subplots(figsize=(8.5, 5))
    counts = []
    for day, color in zip(config.SURFACE_DATES_ORIGINAL, ["#1f77b4", "#d62728"]):
        e = exps[exps.trade_date == str(day)].sort_values("expiration")
        counts.append(f"{day}: {len(e)} expiries")
        ax.plot(np.array(e.expiration, dtype="datetime64[D]"), e.atm_iv * 100, marker="o", lw=0.8,
                color=color, label=f"snapshot {day}")
    ax.axvline(np.datetime64(config.EVENT_DATE), color="grey", ls="--", lw=0.8, label=f"CPI release {config.EVENT_DATE}")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m-%d"))
    fig.autofmt_xdate()
    ax.set(xlabel="expiry date", ylabel="ATM implied vol (%)",
           title="ATM term structure before and after the August 2026 CPI release")
    ax.legend(fontsize=8)
    save(fig, "term_structure_before_after.png",
         f"{'; '.join(counts)}. Plotted against expiry date so the same contracts line up; the "
         f"{config.EVENT_DATE} expiry exists only on the first snapshot. Computed points; no fitting.")


def main():
    opts, exps = load()
    print("Figures:")
    for day in map(str, config.SURFACE_DATES_ORIGINAL):
        smiles(opts, exps, day)
        term_structure(exps, day)
        surface(opts, day)
    before_after(exps)


if __name__ == "__main__":
    main()
