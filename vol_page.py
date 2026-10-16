"""Build the interactive volatility page: clean 3D surface, clean smiles and at-the-money
term structure, for every snapshot date stored in the vol_surface table.

Run:  .venv\\Scripts\\python.exe vol_page.py                         (the original 8 dates: vol_page.html)
      .venv\\Scripts\\python.exe vol_page.py 2026-09-10 2026-09-11   (only these dates)
      .venv\\Scripts\\python.exe vol_page.py --events                (one page per event + index, data/figures/events/)

Reads the database read-only and never changes it. Writes, under data/figures/:
  vol_page.html           the page; open it in any browser, no Python needed
  vol_page_data.json      the numbers the page draws
  plotly-strict.min.js    the charting library, copied from vendor/ so the page works offline

Method (settings in config.py):
  - Friday expiries from CLEAN_MIN_DTE to CLEAN_MAX_DTE days.
  - x axis: call-equivalent delta N(d1) from each option's own implied vol, running from the
    10-delta put (0.90) through at-the-money (0.50) to the 10-delta call (0.10).
  - Delta range per date: the widest range (never wider than the 10-delta put to the 10-delta
    call) that at least half of the eligible expiries cover. An expiry is eligible when it has
    VOL_PAGE_MIN_POINTS computed options in the 10-to-10 range (plus VOL_PAGE_FIT_PAD).
  - Smile: least-squares polynomial of degree CLEAN_FIT_DEGREE in log-moneyness, per expiry,
    fitted to that expiry's computed implied vols. No smoothing across expiries and no
    extrapolation: an expiry whose options do not span the date's delta range is left out.
  - Checks printed per date: gap between fitted smile and computed options, and whether total
    variance ever falls as expiry lengthens.
To add dates, see README.md, "Volatility page".
"""
import datetime as dt
import html
import json
import math
import shutil
import sys

import duckdb
import numpy as np
import pandas as pd

import config

GRID_HI, GRID_LO = 0.90, 0.10   # call-equivalent delta at the 10-delta put and the 10-delta call
_erf = np.vectorize(math.erf)


def norm_cdf(x):
    return 0.5 * (1.0 + _erf(np.asarray(x, dtype=float) / math.sqrt(2.0)))


def call_delta(k, sigma, t):
    """Undiscounted Black-76 call delta N(d1) for log-moneyness k = ln(K/F)."""
    return norm_cdf((-k + 0.5 * sigma * sigma * t) / (sigma * math.sqrt(t)))


def load(con, days=None):
    """days: optional list of dates to read (default: all)."""
    only = f" AND trade_date IN ({', '.join(repr(str(d)) for d in days)})" if days else ""
    opts = con.execute(
        f"SELECT trade_date, expiration, strike, forward, t_years, log_moneyness AS k, iv "
        f"FROM {config.SURFACE_TABLE} WHERE iv IS NOT NULL{only} ORDER BY 1, 2, 3").df()
    exps = con.execute(
        f"SELECT trade_date, expiration, dte, atm_iv FROM {config.SURFACE_EXPIRY_TABLE} "
        f"WHERE true{only} ORDER BY 1, 2").df()
    for frame in (opts, exps):
        for col in ("trade_date", "expiration"):
            frame[col] = frame[col].astype(str).str[:10]
    try:
        events = con.execute(
            f"SELECT date, event_type, trade_date FROM {config.DATE_LIST_TABLE} "
            f"WHERE event_type <> 'NONE'").df()
        for col in ("date", "trade_date"):
            events[col] = events[col].astype(str).str[:10]
    except duckdb.Error:
        events = None
    return opts, exps, events


def label_for(day, events):
    """'CPI day', 'day before CPI', or '' for an ordinary day."""
    if events is None:
        return ""
    names = config.VOL_PAGE_EVENT_NAMES
    parts = [f"{names.get(t, t)} day" for t in events[events.date == day].event_type]
    parts += [f"day before {names.get(t, t)}" for t in events[events.trade_date == day].event_type]
    return " and ".join(dict.fromkeys(parts))


def range_label(hi, lo):
    """'10-delta put to 10-delta call' style label for a call-equivalent delta range."""
    put = f"{round((1 - hi) * 100)}-delta put" if hi > 0.5 else "at the money"
    call = f"{round(lo * 100)}-delta call" if lo < 0.5 else "at the money"
    return f"{put} to {call}"


def choose_range(cover):
    """Widest [lo, hi] in call-equivalent delta (lo < 0.5 < hi, inside [GRID_LO, GRID_HI]) that
    at least half of the eligible expiries cover. cover: (lo_e, hi_e) per eligible expiry.
    Candidate ends come from the expiries' own coverage, rounded inward to 0.01."""
    need = math.ceil(len(cover) / 2)
    los = sorted({GRID_LO} | {min(max(math.ceil(lo * 100) / 100, GRID_LO), 0.49) for lo, _ in cover})
    his = sorted({GRID_HI} | {max(min(math.floor(hi * 100) / 100, GRID_HI), 0.51) for _, hi in cover})
    best = None
    for lo in los:
        for hi in his:
            n = sum(1 for lo_e, hi_e in cover if lo_e <= lo and hi_e >= hi)
            if n >= need:
                key = (round(hi - lo, 6), n, hi)
                if best is None or key > best[0]:
                    best = (key, lo, hi)
    return (best[1], best[2]) if best else (None, None)


def build_date(day, opts, exps):
    """Clean smiles for one snapshot date. Returns None when fewer than two expiries can be fitted."""
    exps = exps[exps.trade_date == day]
    dte_of = dict(zip(exps.expiration, exps.dte.astype(int)))
    term = [[int(r.dte), round(float(r.atm_iv) * 100, 3), r.expiration]
            for r in exps.itertuples() if r.atm_iv == r.atm_iv and 1 <= r.dte <= config.CLEAN_MAX_DTE]
    pad = config.VOL_PAGE_FIT_PAD
    # Eligible expiries, and the delta range each one's computed options cover.
    cands = []
    for expiration, s in opts[opts.trade_date == day].groupby("expiration"):
        dte = dte_of.get(expiration)
        if dte is None or dt.date.fromisoformat(expiration).weekday() != 4:
            continue
        if not config.CLEAN_MIN_DTE <= dte <= config.CLEAN_MAX_DTE:
            continue
        t = float(s.t_years.iloc[0])
        k, v, strike = s.k.to_numpy(float), s.iv.to_numpy(float), s.strike.to_numpy(float)
        cd = call_delta(k, v, t)
        use = (cd <= GRID_HI + pad) & (cd >= GRID_LO - pad)
        if use.sum() < config.VOL_PAGE_MIN_POINTS:
            continue
        cands.append(dict(exp=expiration, dte=dte, t=t, forward=float(s.forward.iloc[0]),
                          k=k[use], v=v[use], strike=strike[use], cd=cd[use]))
    if len(cands) < 2:
        return None
    lo, hi = choose_range([(c["cd"].min(), c["cd"].max()) for c in cands])
    if lo is None:
        return None
    grid = np.round(np.linspace(hi, lo, config.CLEAN_GRID_POINTS), 4)
    rows, dropped, residuals = [], [], []
    for c in cands:
        expiration, dte, t, forward = c["exp"], c["dte"], c["t"], c["forward"]
        if c["cd"].max() < hi or c["cd"].min() > lo:
            dropped.append([expiration, dte])
            continue
        fit = (c["cd"] <= hi + pad) & (c["cd"] >= lo - pad)
        k, v, strike, cd = c["k"][fit], c["v"][fit], c["strike"][fit], c["cd"][fit]
        coef = np.polyfit(k, v, config.CLEAN_FIT_DEGREE)
        inner = (cd <= hi) & (cd >= lo)
        fine_k = np.linspace(k.min(), k.max(), 500)
        fine_v = np.polyval(coef, fine_k)
        if (fine_v <= 0).any():
            dropped.append([expiration, dte])
            continue
        fine_cd = call_delta(fine_k, fine_v, t)
        order = np.argsort(fine_cd)
        grid_v = np.interp(grid, fine_cd[order], fine_v[order], left=np.nan, right=np.nan)
        grid_k = np.interp(grid, fine_cd[order], fine_k[order], left=np.nan, right=np.nan)
        if np.isnan(grid_v).any():
            dropped.append([expiration, dte])
            continue
        residuals += (np.abs(np.polyval(coef, k) - v)[inner] * 100).tolist()
        rows.append({
            "exp": expiration, "dte": dte,
            "iv": [round(float(x) * 100, 3) for x in grid_v],
            "K": [int(round(forward * math.exp(float(x)))) for x in grid_k],
            "pts": [[round(float(a_), 4), round(float(b_) * 100, 2), float(c_)]
                    for a_, b_, c_ in zip(cd[inner], v[inner], strike[inner])],
        })
    if len(rows) < 2:
        return None
    rows.sort(key=lambda r: r["dte"])
    z = np.array([r["iv"] for r in rows]) / 100.0
    total_variance = z * z * np.array([r["dte"] for r in rows])[:, None]
    full = lo <= GRID_LO + 1e-9 and hi >= GRID_HI - 1e-9
    return {
        "grid": grid.tolist(), "range": [hi, lo], "range_label": range_label(hi, lo), "full_range": full,
        "expiries": rows, "term": term, "dropped": dropped,
        "fit_mean": round(float(np.mean(residuals)), 3), "fit_max": round(float(np.max(residuals)), 3),
        "n_pts": len(residuals), "tv_drops": int((np.diff(total_variance, axis=0) < 0).sum()),
        "half_day": dt.date.fromisoformat(day) in config.HALF_DAYS,
    }


def write_page(data, page_path=None, data_path=None):
    """Fill the template (unchanged) with data. The charting library is copied once into the
    page's folder, so every page in a folder shares one copy."""
    page_path = page_path or config.VOL_PAGE_PATH
    page_path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, separators=(",", ":"), allow_nan=False)
    if data_path is None and page_path == config.VOL_PAGE_PATH:
        data_path = config.VOL_PAGE_DATA
    if data_path is not None:
        data_path.write_text(text, encoding="utf-8")
    template = config.VOL_PAGE_TEMPLATE.read_text(encoding="utf-8")
    if "__DATA__" not in template:
        raise SystemExit(f"{config.VOL_PAGE_TEMPLATE} has no __DATA__ placeholder")
    head = ('<!doctype html>\n<html lang="en">\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1">\n')
    page = head + template.replace("__DATA__", text.replace("</", "<\\/"))
    page_path.write_text(page, encoding="utf-8")
    library = page_path.parent / config.VOL_PAGE_LIBRARY.name
    if not library.exists() or library.stat().st_size != config.VOL_PAGE_LIBRARY.stat().st_size:
        shutil.copyfile(config.VOL_PAGE_LIBRARY, library)



INDEX_TEMPLATE = """<!doctype html>
<html lang="en">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Event volatility pages</title>
<style>
:root { --bg: #f4f5f3; --panel: #fbfbfa; --fg: #14181d; --muted: #5b6470; --rule: #d3d8dc; --warn: #a3521a; --link: #1f5f9e; }
@media (prefers-color-scheme: dark) { :root { --bg: #12151a; --panel: #181c22; --fg: #e8ebee; --muted: #9aa4b0; --rule: #2c333c; --warn: #e39a5c; --link: #7fb2e5; } }
body { margin: 0; background: var(--bg); color: var(--fg); font: 14px/1.45 system-ui, -apple-system, "Segoe UI", sans-serif; }
.wrap { max-width: 1280px; margin: 0 auto; padding: 24px 16px 48px; }
h1 { font-size: 22px; margin: 0 0 6px; }
p { color: var(--muted); max-width: 80ch; margin: 0 0 10px; }
.scroll { overflow-x: auto; background: var(--panel); border: 1px solid var(--rule); border-radius: 6px; }
table { border-collapse: collapse; width: 100%; font-variant-numeric: tabular-nums; }
th, td { padding: 6px 9px; border-bottom: 1px solid var(--rule); text-align: right; white-space: nowrap; }
th:nth-child(-n+3), td:nth-child(-n+3) { text-align: left; }
th { position: sticky; top: 0; background: var(--panel); cursor: pointer; user-select: none; font-weight: 600; }
th[aria-sort="ascending"]::after { content: " \\25B2"; } th[aria-sort="descending"]::after { content: " \\25BC"; }
a { color: var(--link); } .flag { color: var(--warn); } .muted { color: var(--muted); }
</style>
<div class="wrap">
<h1>Event volatility pages</h1>
<p>__SUMMARY__</p>
<p>Each row links to a page that shows the close on the event day next to the close on the trading day before (16:15 ET; 13:15 on half days). Fit gaps are the largest and mean distance, in vol points, between each expiry's fitted cubic smile and its computed options. A flag means a gap above __LIMIT__ vol points, total variance falling with longer expiry, a day that could not be drawn, or a half-day session. The 30-day ATM vol columns are at-the-money (forward) vol from all expiries; the 25-delta risk reversal (25-delta put IV minus 25-delta call IV, at 30 days, in vol points) is from the fitted smiles, as defined in the README (Stage 5). Click a column heading to sort. Private working draft: the underlying quotes are licensed for personal use. For education only, not investment advice.</p>
<div class="scroll"><table id="t"><thead><tr>
<th>Event date</th><th>Type</th><th>Range drawn (day before | event day)</th>
<th>Expiries (before | event)</th><th>Fit max (before | event)</th><th>Fit mean (before | event)</th>
<th>30-day ATM vol before</th><th>30-day ATM vol event day</th><th>Change</th><th>30-day 25-delta RR change</th><th>Flags</th>
</tr></thead><tbody>
__ROWS__
</tbody></table></div>
</div>
<script>
const tb = document.querySelector('#t tbody');
document.querySelectorAll('#t th').forEach((th, i) => th.addEventListener('click', () => {
  const asc = th.getAttribute('aria-sort') !== 'ascending';
  document.querySelectorAll('#t th').forEach(h => h.removeAttribute('aria-sort'));
  th.setAttribute('aria-sort', asc ? 'ascending' : 'descending');
  const key = r => r.children[i].dataset.v !== undefined ? r.children[i].dataset.v : r.children[i].textContent;
  [...tb.rows].sort((a, b) => { const x = key(a), y = key(b), nx = parseFloat(x), ny = parseFloat(y);
    const c = (!isNaN(nx) && !isNaN(ny)) ? nx - ny : String(x).localeCompare(String(y)); return asc ? c : -c; })
    .forEach(r => tb.appendChild(r));
}));
</script>
"""


def _cell(v, sortv=None, cls=""):
    attr = f' data-v="{sortv}"' if sortv is not None else ""
    c = f' class="{cls}"' if cls else ""
    return f"<td{attr}{c}>{html.escape(str(v))}</td>"


def build_events(opts, exps, events):
    """One page per event (the trading day before next to the event day) and an index page."""
    if events is None:
        raise SystemExit("No date_list table: run build_dates.py first.")
    con = duckdb.connect(str(config.DUCKDB_PATH), read_only=True)
    summ = con.execute(f"SELECT trade_date, atm30, flags FROM {config.SURFACE_SUMMARY_TABLE}").df()
    try:
        met = con.execute("SELECT * FROM surface_metrics").df()
        met = {str(r.trade_date)[:10]: r for r in met.itertuples()}
    except duckdb.Error:
        met = {}
    con.close()

    def metrics_text(day):
        r = met.get(day)
        if r is None:
            return ""
        f = lambda v: "n/a" if v != v else f"{v:.2f}"
        return (f"Skew metrics (README, Stage 5): at 30 days, ATM vol {f(r.atm30)}%, 25-delta risk reversal "
                f"{f(r.rr25_30)} and put wing (10-delta put minus ATM) {f(r.wing10_30)} vol points; at the "
                f"shortest expiry ({r.short_dte} days) {f(r.atm_short)}%, {f(r.rr25_short)} and {f(r.wing10_short)}.")
    atm = {str(d)[:10]: a for d, a in zip(summ.trade_date, summ.atm30)}
    failed = {}
    if config.SURFACE_FETCH_ERRORS_CSV.exists():          # fetch failures (warnings are not failures)
        f = pd.read_csv(config.SURFACE_FETCH_ERRORS_CSV)
        f = f[f.level == "failed"] if "level" in f.columns else f
        failed.update({str(d)[:10]: "fetch failed: " + r for d, r in zip(f.date, f.reason)})
    if config.SURFACE_FAILURES_CSV.exists():
        f = pd.read_csv(config.SURFACE_FAILURES_CSV)
        failed.update({str(d)[:10]: "surface failed: " + r for d, r in zip(f.date, f.reason)})
    stored = set(opts.trade_date.unique())
    per_event = events.groupby(["date", "trade_date"]).event_type.apply(lambda x: "+".join(sorted(x))).reset_index()
    cache, limit = {}, config.CLEAN_GAP_LIMIT * 100

    def built(day):
        if day not in cache:
            if day not in stored:
                cache[day] = (None, failed.get(day, "no surface (not fetched)"))
            else:
                b = build_date(day, opts, exps)
                if b is not None:
                    b["label"] = label_for(day, events)
                    if metrics_text(day):
                        b["metrics"] = metrics_text(day)
                cache[day] = (b, "" if b is not None else "fewer than two expiries can be fitted")
        return cache[day]

    out_dir = config.VOL_EVENTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    rows, html_rows = [], []
    generated = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    for ev_day, before, types in zip(per_event.date, per_event.trade_date, per_event.event_type):
        (b0, why0), (b1, why1) = built(before), built(ev_day)
        name = f"{ev_day}_{types.replace('+', '-')}.html"
        page = None
        if b0 is not None or b1 is not None:
            data = {"smile_targets": config.CLEAN_SMILE_TARGET_DTES, "pair_max_days": config.VOL_PAGE_PAIR_MAX_DAYS,
                    "generated": generated, "dates": {d: b for d, b in [(before, b0), (ev_day, b1)] if b is not None}}
            write_page(data, page_path=out_dir / name)
            page = name
        flags = []
        for tag, b, why in [("day before", b0, why0), ("event day", b1, why1)]:
            if b is None:
                flags.append(f"{tag} not drawn ({why})")
                continue
            if b["fit_max"] > limit:
                flags.append(f"{tag} fit gap {b['fit_max']:.2f}")
            if b["tv_drops"]:
                flags.append(f"{tag} total variance falls ({b['tv_drops']})")
            if b["half_day"]:
                flags.append(f"{tag} half day")
        a0, a1 = atm.get(before), atm.get(ev_day)
        m0, m1 = met.get(before), met.get(ev_day)
        rr_chg = (m1.rr25_30 - m0.rr25_30) if m0 is not None and m1 is not None else None
        rr_chg = None if rr_chg is not None and rr_chg != rr_chg else rr_chg
        rows.append(dict(event_date=ev_day, event_type=types, day_before=before, page=page or "",
                         pair_drawable=b0 is not None and b1 is not None,
                         range_before=b0["range_label"] if b0 else "", range_event=b1["range_label"] if b1 else "",
                         expiries_before=len(b0["expiries"]) if b0 else None, expiries_event=len(b1["expiries"]) if b1 else None,
                         fit_max_before=b0["fit_max"] if b0 else None, fit_max_event=b1["fit_max"] if b1 else None,
                         fit_mean_before=b0["fit_mean"] if b0 else None, fit_mean_event=b1["fit_mean"] if b1 else None,
                         atm30_before=round(a0 * 100, 2) if a0 == a0 and a0 is not None else None,
                         atm30_event=round(a1 * 100, 2) if a1 == a1 and a1 is not None else None,
                         rr25_30_change=round(rr_chg, 3) if rr_chg is not None else None,
                         flags="; ".join(flags)))
        r = rows[-1]
        fmt = lambda v, f="{:.3f}": "–" if v is None else f.format(v)
        link = f'<a href="{html.escape(page)}">{ev_day}</a>' if page else ev_day
        chg = None if r["atm30_before"] is None or r["atm30_event"] is None else r["atm30_event"] - r["atm30_before"]
        rng = (r["range_before"] or "–") + " | " + (r["range_event"] or "–")
        html_rows.append("<tr>" + f'<td data-v="{ev_day}">{link}</td>' + _cell(types) + _cell(rng)
                         + _cell(f"{fmt(r['expiries_before'], '{}')} | {fmt(r['expiries_event'], '{}')}", sortv=r["expiries_event"] or -1)
                         + _cell(f"{fmt(r['fit_max_before'])} | {fmt(r['fit_max_event'])}",
                                 sortv=max([v for v in (r['fit_max_before'], r['fit_max_event']) if v is not None], default=-1))
                         + _cell(f"{fmt(r['fit_mean_before'])} | {fmt(r['fit_mean_event'])}",
                                 sortv=max([v for v in (r['fit_mean_before'], r['fit_mean_event']) if v is not None], default=-1))
                         + _cell(fmt(r["atm30_before"], "{:.2f}%"), sortv=r["atm30_before"] if r["atm30_before"] is not None else -1)
                         + _cell(fmt(r["atm30_event"], "{:.2f}%"), sortv=r["atm30_event"] if r["atm30_event"] is not None else -1)
                         + _cell(fmt(chg, "{:+.2f}"), sortv=chg if chg is not None else -99)
                         + _cell(fmt(r["rr25_30_change"], "{:+.2f}"), sortv=r["rr25_30_change"] if r["rr25_30_change"] is not None else -99)
                         + _cell(r["flags"] or "none", cls="flag" if r["flags"] else "muted") + "</tr>")
    status = pd.DataFrame(rows)
    status.to_csv(config.VOL_EVENTS_STATUS_CSV, index=False)
    n, pairs, pages = len(status), int(status.pair_drawable.sum()), int((status.page != "").sum())
    summary = (f"{n} events (CPI, FOMC and payrolls, {status.event_date.min()} to {status.event_date.max()}). "
               f"{pairs} have both days drawn; {pages - pairs} have one day drawn; {n - pages} have neither. "
               f"Built {generated}.")
    (out_dir / "index.html").write_text(INDEX_TEMPLATE.replace("__SUMMARY__", html.escape(summary))
                                        .replace("__LIMIT__", f"{limit:.2f}").replace("__ROWS__", "\n".join(html_rows)),
                                        encoding="utf-8")
    print(summary)
    print(f"Wrote {pages} event pages and index.html to {out_dir} (one shared {config.VOL_PAGE_LIBRARY.name}); "
          f"status for every event in {config.VOL_EVENTS_STATUS_CSV}")
    return status


def main(argv):
    con = duckdb.connect(str(config.DUCKDB_PATH), read_only=True)
    opts, exps, events = load(con)
    con.close()
    if argv and argv[0] == "--events":
        build_events(opts, exps, events)
        return
    stored = sorted(opts.trade_date.unique())
    wanted = argv or [str(d) for d in config.SURFACE_DATES_ORIGINAL]   # the 8-date page
    missing = [d for d in wanted if d not in stored]
    if missing:
        print(f"Not in {config.SURFACE_TABLE} (add them to config.SURFACE_DATES, then run "
              f"fetch_surface.py and vol_surface.py): {', '.join(missing)}")
    data = {"smile_targets": config.CLEAN_SMILE_TARGET_DTES,
            "pair_max_days": config.VOL_PAGE_PAIR_MAX_DAYS,
            "generated": dt.datetime.now().strftime("%Y-%m-%d %H:%M"), "dates": {}}
    limit = config.CLEAN_GAP_LIMIT * 100
    print(f"{'date':<12}{'expiries':>9}{'options':>9}{'fit mean':>10}{'fit max':>9}{'TV drops':>10}  "
          f"{'range drawn':<36}notes")
    flagged = 0
    for day in [d for d in wanted if d in stored]:
        built = build_date(day, opts, exps)
        if built is None:
            print(f"{day:<12}  skipped: fewer than two expiries can be fitted")
            continue
        built["label"] = label_for(day, events)
        data["dates"][day] = built
        notes = []
        if built["label"]:
            notes.append(built["label"])
        if built["half_day"]:
            notes.append("HALF DAY, quotes unreliable")
        if built["dropped"]:
            notes.append("left out " + ", ".join(e for e, _ in built["dropped"]))
        problem = built["fit_max"] > limit or built["tv_drops"] > 0
        flagged += problem
        print(f"{day:<12}{len(built['expiries']):>9}{built['n_pts']:>9}{built['fit_mean']:>10.3f}"
              f"{built['fit_max']:>9.3f}{built['tv_drops']:>10}  {built['range_label']:<36}"
              f"{'FLAG ' if problem else ''}{'; '.join(notes)}")
    if not data["dates"]:
        raise SystemExit("No dates to draw. Nothing written.")
    write_page(data)
    print(f"\nFit gaps are in vol points; FLAG means a gap above {limit:.2f} or total variance falling "
          f"with longer expiry. Flagged dates: {flagged} of {len(data['dates'])}.")
    print(f"Wrote {config.VOL_PAGE_PATH} ({config.VOL_PAGE_PATH.stat().st_size / 1e6:.1f} MB, "
          f"dates: {len(data['dates'])}). Open it in a browser.")


if __name__ == "__main__":
    main(sys.argv[1:])
