"""Interactive 3D implied vol views (Plotly), one standalone HTML file.

Smiles view: one line per expiry, only computed points. Surface view: each expiry's smile
interpolated onto a common log-moneyness grid inside the range that expiry covers (NaN
outside, no extrapolation), with the computed points overlaid. Plotly buttons switch the
date, the view and the y axis (days or square root of days); a small script embedded in the
file keeps the three choices in sync. plotly.js is embedded, so it opens offline.

Run after vol_surface.py:  .venv\\Scripts\\python.exe surface_3d.py
"""
import json

import duckdb
import numpy as np
import pandas as pd
import plotly.graph_objects as go

import config
import clean_surface as cs
from vol_surface import greeks

COLORSCALE = "Viridis"


def load():
    con = duckdb.connect(str(config.DUCKDB_PATH), read_only=True)
    opts = con.execute(f"""
        SELECT o.*, e.atm_iv, e.dte FROM {config.SURFACE_TABLE} o
        JOIN {config.SURFACE_EXPIRY_TABLE} e USING (trade_date, expiration)
        WHERE e.atm_iv IS NOT NULL ORDER BY o.trade_date, o.expiration, o.log_moneyness
    """).df()
    con.close()
    opts["trade_date"] = opts.trade_date.astype(str)
    opts["expiration"] = opts.expiration.astype(str)
    # Same plotting window as the 2D figures: |ln(K/F)| <= PLOT_SD * ATM vol * sqrt(t).
    window = opts.log_moneyness.abs() <= config.PLOT_SD * opts.atm_iv * np.sqrt(opts.t_years)
    return opts[window].copy()


def ys(dte):
    dte = np.asarray(dte, float)
    return dte.tolist(), np.sqrt(dte).tolist()


def smile_traces(day_opts, day, zmin, zmax):
    traces = []
    for exp, g in day_opts.groupby("expiration"):
        ylin, ysqrt = ys(g.dte)
        scale = dict(colorscale=COLORSCALE, cmin=zmin, cmax=zmax)
        traces.append(go.Scatter3d(
            x=g.log_moneyness, y=ylin, z=g.iv * 100, mode="lines+markers",
            line=dict(color=g.iv * 100, width=3, **scale), marker=dict(size=1.8, color=g.iv * 100, **scale),
            name=f"{exp} ({int(g.dte.iloc[0])}d)", showlegend=False,
            customdata=np.c_[g.strike, g.delta],
            text=[exp] * len(g),
            hovertemplate="strike %{customdata[0]:.0f}<br>expiry %{text}<br>days %{meta.dte}"
                          "<br>IV %{z:.2f}%<br>delta %{customdata[1]:.3f}<extra>computed</extra>",
            meta=dict(mode="full", xaxis="any", date=day, view="smiles", ylin=ylin, ysqrt=ysqrt, dte=int(g.dte.iloc[0])),
            visible=False))
    return traces


def surface_traces(day_opts, day, grid, zmin, zmax):
    rows, strikes, deltas, exps, dtes = [], [], [], [], []
    for exp, g in day_opts.groupby("expiration"):
        k, iv = g.log_moneyness.values, g.iv.values
        z = np.full(len(grid), np.nan)
        inside = (grid >= k.min()) & (grid <= k.max())        # no extrapolation
        z[inside] = np.interp(grid[inside], k, iv)
        F, df, t = g.forward.iloc[0], g.discount_factor.iloc[0], g.t_years.iloc[0]
        K = F * np.exp(grid)
        d = np.full(len(grid), np.nan)
        d[inside] = greeks(F, K[inside], t, df, z[inside], K[inside] >= F)[0]   # OTM side, as computed
        rows.append(z * 100)
        strikes.append(K)
        deltas.append(d)
        exps.append([exp] * len(grid))
        dtes.append(int(g.dte.iloc[0]))
    ylin, ysqrt = ys(dtes)
    surface = go.Surface(
        x=grid, y=ylin, z=np.array(rows), colorscale=COLORSCALE, cmin=zmin, cmax=zmax,
        colorbar=dict(title="IV (%)", len=0.6), opacity=0.92,
        customdata=np.dstack([np.array(strikes), np.array(deltas)]), text=np.array(exps),
        hovertemplate="strike %{customdata[0]:.0f}<br>expiry %{text}<br>days %{y}<br>IV %{z:.2f}%"
                      "<br>delta %{customdata[1]:.3f}<extra>interpolated</extra>",
        meta=dict(mode="full", xaxis="any", date=day, view="surface", ylin=ylin, ysqrt=ysqrt), visible=False)
    pts_lin, pts_sqrt = ys(day_opts.dte)
    points = go.Scatter3d(
        x=day_opts.log_moneyness, y=pts_lin, z=day_opts.iv * 100, mode="markers",
        marker=dict(size=1.2, color="black", opacity=0.4), showlegend=False,
        customdata=np.c_[day_opts.strike, day_opts.delta, day_opts.dte], text=day_opts.expiration,
        hovertemplate="strike %{customdata[0]:.0f}<br>expiry %{text}<br>days %{customdata[2]}"
                      "<br>IV %{z:.2f}%<br>delta %{customdata[1]:.3f}<extra>computed</extra>",
        meta=dict(mode="full", xaxis="any", date=day, view="surface", ylin=pts_lin, ysqrt=pts_sqrt), visible=False)
    return [surface, points]


def caption(day_opts, day):
    return (f"{day}: {len(day_opts)} options (OTM, |ln(K/F)| ≤ {config.PLOT_SD:g} × ATM vol × √t), "
            f"{day_opts.expiration.nunique()} expiries. Smiles view: computed points only.<br>Surface view: "
            f"interpolated between computed points (linear along each smile, linear shading between "
            f"expiries), gaps where an expiry has no quotes; black dots are the computed points.")


def delta_label(x):
    """x = 1 - call delta -> signed delta of the OTM option (puts negative)."""
    x = np.asarray(x, float)
    return np.where(x <= 0.5, -x, 1 - x)


def clean_traces(fits, day, axis, zmin, zmax):
    """Clean smiles (fitted curves through real points) and clean surface for one date and x axis."""
    scale = dict(colorscale=COLORSCALE, cmin=zmin, cmax=zmax)
    meta = lambda view, y: dict(mode="clean", xaxis=axis, date=day, view=view, ylin=ys(y)[0], ysqrt=ys(y)[1])
    traces, counts = [], {}
    # --- smiles: at most 7 expiries spread across the range
    chosen = cs.shown_expiries(fits, day)
    n_pts, lx, ly, lz, lt = 0, [], [], [], []
    for k in chosen:
        f = fits[k]
        kk, iv, xd = cs.smile_curve(f)
        x = xd if axis == "delta" else 100 * np.exp(kk)
        dte = [f["dte"]] * len(x)
        traces.append(go.Scatter3d(x=x, y=dte, z=iv * 100, mode="lines", line=dict(color=iv * 100, width=5, **scale),
                                   showlegend=False, text=[k[1]] * len(x),
                                   hovertemplate="FITTED smile<br>expiry %{text}<br>IV %{z:.2f}%<extra>cubic fit</extra>",
                                   meta=meta("smiles", dte), visible=False))
        p = f["pts"]
        px = cs.point_x(p, axis)
        traces.append(go.Scatter3d(x=px, y=[f["dte"]] * len(p), z=p.iv * 100, mode="markers",
                                   marker=dict(size=2.2, color="black", opacity=0.6), showlegend=False,
                                   customdata=np.c_[p.strike, p.delta], text=[k[1]] * len(p),
                                   hovertemplate="COMPUTED point<br>strike %{customdata[0]:.0f}<br>expiry %{text}"
                                                 "<br>IV %{z:.2f}%<br>delta %{customdata[1]:.3f}<extra>computed</extra>",
                                   meta=meta("smiles", [f["dte"]] * len(p)), visible=False))
        n_pts += len(p)
        j = int(np.argmax(x))
        lx.append(x[j]); ly.append(f["dte"]); lz.append(iv[j] * 100); lt.append(f"  {k[1]}")
    traces.append(go.Scatter3d(x=lx, y=ly, z=lz, mode="text", text=lt, textposition="middle right",
                               textfont=dict(size=11, color="#333"), hoverinfo="skip", showlegend=False,
                               meta=meta("smiles", ly), visible=False))
    counts["smiles"] = (n_pts, len(chosen))
    # --- surface: fitted smiles on a regular grid, light smoothing across expiries
    grid, keys, rows, smooth = cs.grid_for(day, fits, axis)
    dtes = [fits[k]["dte"] for k in keys]
    near = np.full(smooth.shape + (4,), np.nan)
    for i, k in enumerate(keys):
        p = fits[k]["pts"]; px = cs.point_x(p, axis)
        idx = np.abs(px[None, :] - grid[:, None]).argmin(axis=1)          # nearest computed point per cell
        near[i, :, 0] = p.strike.values[idx]; near[i, :, 1] = p.iv.values[idx] * 100
        near[i, :, 2] = p.delta.values[idx]; near[i, :, 3] = rows[i]  * 100   # fitted, before smoothing
    exps = np.array([[k[1]] * len(grid) for k in keys])
    traces.append(go.Surface(
        x=grid, y=dtes, z=smooth * 100, colorbar=dict(title="IV (%)", len=0.6), opacity=0.92, **scale,
        customdata=near, text=exps,
        hovertemplate="SMOOTHED surface value: IV %{z:.2f}%<br>expiry %{text}"
                      "<br>— nearest COMPUTED point on this expiry —<br>strike %{customdata[0]:.0f}, "
                      "IV %{customdata[1]:.2f}%, delta %{customdata[2]:.3f}<extra>clean surface</extra>",
        meta=meta("surface", dtes), visible=False))
    allp = pd.concat([fits[k]["pts"].assign(dte=fits[k]["dte"]) for k in keys])
    traces.append(go.Scatter3d(
        x=cs.point_x(allp, axis), y=allp.dte, z=allp.iv * 100, mode="markers",
        marker=dict(size=1.4, color="black", opacity=0.45), showlegend=False,
        customdata=np.c_[allp.strike, allp.delta], text=allp.expiration,
        hovertemplate="COMPUTED point<br>strike %{customdata[0]:.0f}<br>expiry %{text}<br>IV %{z:.2f}%"
                      "<br>delta %{customdata[1]:.3f}<extra>computed</extra>",
        meta=meta("surface", list(allp.dte)), visible=False))
    counts["surface"] = (len(allp), len(keys))
    return traces, counts


def main():
    opts = load()
    fits = cs.fit_smiles(cs.load_points())
    zmin, zmax = float(opts.iv.min() * 100), float(opts.iv.max() * 100)
    grid = np.round(np.arange(opts.log_moneyness.min(), opts.log_moneyness.max() + config.SURFACE_3D_GRID_STEP, config.SURFACE_3D_GRID_STEP), 4)
    days = [str(d) for d in config.SURFACE_DATES_ORIGINAL]

    fig = go.Figure()
    captions = {}
    for day in days:
        day_opts = opts[opts.trade_date == day]
        full_caption = caption(day_opts, day)
        captions[f"full|smiles|{day}"] = captions[f"full|surface|{day}"] = full_caption
        for tr in smile_traces(day_opts, day, zmin, zmax) + surface_traces(day_opts, day, grid, zmin, zmax):
            fig.add_trace(tr)
        for axis in ("delta", "strike"):
            traces, counts = clean_traces(fits, day, axis, zmin, zmax)
            for tr in traces:
                fig.add_trace(tr)
        n, e = counts["smiles"]
        captions[f"clean|smiles|{day}"] = (
            f"{day}: {n} computed options (black dots) on {e} Friday expiries, 14 days to ~6 months, 10-delta put to "
            f"10-delta call.<br>Each line is a least-squares cubic fit of IV in ln(K/F) through that expiry's points, "
            f"drawn only within them.")
        n, e = counts["surface"]
        captions[f"clean|surface|{day}"] = (
            f"{day}: {n} computed options (black dots) on {e} Friday expiries, 14 days to ~6 months, 10-delta put to "
            f"10-delta call.<br>Surface: cubic-fitted smiles on a regular grid, then light smoothing of total variance "
            f"across neighbouring expiries (½ own value + ½ straight line in time through the two neighbours). No extrapolation.")

    button = lambda label: dict(label=label, method="skip", args=[None])   # handled by the script below
    # Two rows of button groups: (title, labels, x, y_buttons)
    menus = [("Mode", ["Clean", "Full (raw)"], 0.00, 1.12), ("View", ["Smiles", "Surface"], 0.27, 1.12),
             ("Date", days, 0.52, 1.12),
             ("X axis (Clean)", ["Delta", "Strike % of forward"], 0.00, 1.035), ("Y axis", ["Days (linear)", "√Days"], 0.40, 1.035)]
    fig.update_layout(
        template="plotly_white", height=900, margin=dict(l=0, r=0, t=210, b=80),
        title=dict(y=0.99, yref="container", x=0.01, font=dict(size=17)),
        scene=dict(yaxis_title="days to expiry", zaxis_title="implied vol (%)", zaxis_range=[zmin, zmax],
                   bgcolor="white", camera=dict(eye=dict(x=-1.6, y=-1.6, z=0.9))),
        updatemenus=[dict(type="buttons", direction="right", x=x, y=y, xanchor="left", yanchor="bottom",
                          showactive=True, active=0, buttons=[button(lbl) for lbl in labels])
                     for _, labels, x, y in menus],
        annotations=[dict(text=f"<b>{name}</b>", x=x, y=y + 0.045, xref="paper", yref="paper",
                          showarrow=False, xanchor="left", yanchor="bottom") for name, _, x, y in menus])

    js = """
var gd = document.getElementById('{plot_id}');
var CAPTIONS = %s, TICKS = %s, DATES = %s, DELTA_TICKS = %s;
var state = {mode: 'clean', view: 'smiles', date: DATES[0], x: 'delta', y: 'lin'};
var MENU_ANN = gd.layout.annotations.slice(0, %d);
var LABELS = {'Clean': ['mode', 'clean'], 'Full (raw)': ['mode', 'full'], 'Smiles': ['view', 'smiles'],
              'Surface': ['view', 'surface'], 'Delta': ['x', 'delta'], 'Strike %% of forward': ['x', 'strike'],
              'Days (linear)': ['y', 'lin'], '\u221aDays': ['y', 'sqrt']};
function render() {
  var vis = [], yv = [];
  gd.data.forEach(function (t) {
    var m = t.meta;
    vis.push(m.mode === state.mode && m.view === state.view && m.date === state.date &&
             (m.mode === 'full' || m.xaxis === state.x));
    yv.push(state.y === 'sqrt' ? m.ysqrt : m.ylin);
  });
  Plotly.restyle(gd, {visible: vis, y: yv});
  var yaxis = state.y === 'sqrt'
    ? {title: {text: 'days to expiry (square-root scale)'}, tickvals: TICKS.map(Math.sqrt), ticktext: TICKS.map(String)}
    : {title: {text: 'days to expiry'}, tickvals: null, ticktext: null};
  var xaxis;
  if (state.mode === 'full') xaxis = {title: {text: 'log-moneyness ln(K/F)'}, tickvals: null, ticktext: null, autorange: true};
  else if (state.x === 'delta') xaxis = {title: {text: 'delta (OTM puts left, OTM calls right)'},
      tickvals: DELTA_TICKS.map(function (d) { return d[0]; }), ticktext: DELTA_TICKS.map(function (d) { return d[1]; }), autorange: true};
  else xaxis = {title: {text: 'strike (%% of forward)'}, tickvals: null, ticktext: null, autorange: true};
  Plotly.relayout(gd, {
    'title.text': 'SPXW implied volatility, snapshot ' + state.date + ' (August 2026 CPI released 2026-09-11) - ' +
                  (state.mode === 'clean' ? 'Clean' : 'Full (raw)') + ' ' + state.view,
    'scene.yaxis': Object.assign({}, gd.layout.scene.yaxis, yaxis),
    'scene.xaxis': Object.assign({}, gd.layout.scene.xaxis, xaxis),
    annotations: MENU_ANN.concat([{text: CAPTIONS[state.mode + '|' + state.view + '|' + state.date], x: 0.5, y: -0.06,
                                   xref: 'paper', yref: 'paper', showarrow: false, font: {size: 11}, xanchor: 'center'}])
  });
}
gd.on('plotly_buttonclicked', function (e) {
  var hit = LABELS[e.button.label];
  if (hit) state[hit[0]] = hit[1]; else if (DATES.indexOf(e.button.label) >= 0) state.date = e.button.label;
  render();
});
render();
""" % (json.dumps(captions), json.dumps(config.SURFACE_3D_DAY_TICKS), json.dumps(days),
       json.dumps(cs.DELTA_TICKS), len(menus))

    config.FIG_DIR.mkdir(parents=True, exist_ok=True)
    path = config.SURFACE_3D_PATH
    fig.write_html(path, include_plotlyjs=True, full_html=True, post_script=js,
                   config=dict(displaylogo=False))
    print(f"Saved {path} ({path.stat().st_size / 1e6:.1f} MB, {len(fig.data)} traces)")
    for key in sorted(captions):
        if key.startswith("clean"):
            print(" ", key, "|", captions[key].split("<br>")[0].encode("ascii", "replace").decode())


if __name__ == "__main__":
    main()
