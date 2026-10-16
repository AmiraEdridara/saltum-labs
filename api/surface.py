"""Vol surface: GET /surface/{date} (clean smiles and term structure) and GET /dates (the picker).

/surface uses vol_page.build_date, the same function that drew the prototype event pages, and adds
per-option vanna from vol_surface (change in delta per 1 vol point, README "Skew and vanna"). No
option prices, mids or spreads are returned.
"""
import vol_page
from . import events, status, validation as v
from .common import EDU, NotFound, envelope

SURFACE_SOURCE = ["vol_surface", "surface_expiries", "surface_metrics", "control_surface_metrics", status.SOURCE]


def get_surface(cur, date):
    day = v.date(date)
    why = status.why_missing(day)
    if why:
        raise NotFound(f"{day} has no surface: {why}")
    key = day.isoformat()
    opts, exps, _ = vol_page.load(cur, [key])
    b = vol_page.build_date(key, opts, exps)
    if b is None:
        raise NotFound(f"{day} has no surface: fewer than two expiries can be drawn")
    vn = cur.execute("SELECT expiration, strike, vanna FROM vol_surface WHERE trade_date = ? AND iv IS NOT NULL",
                     [day]).df()
    vanna = {(str(e)[:10], float(k)): x for e, k, x in zip(vn.expiration, vn.strike, vn.vanna)}
    expiries = [dict(expiration=e["exp"], dte=e["dte"], iv_pct=e["iv"], strike=e["K"],
                     options=[dict(call_delta=cd, iv_pct=iv, strike=k, vanna=vanna.get((e["exp"], float(k))))
                              for cd, iv, k in e["pts"]])
                for e in b["expiries"]]
    s = status.row(day)
    m = events.metrics_row(cur, key) or {}
    data = dict(
        day=dict(live=v.is_live(day), day_type=s["day_type"], events=s["events"] or None, events_next_day=s["events_next_day"] or None,
                 flagged=s["flagged"], half_day=b["half_day"], range=b["range"], range_label=b["range_label"],
                 full_range=b["full_range"], fit_mean_volpts=b["fit_mean"], fit_max_volpts=b["fit_max"],
                 fitted_points=b["n_pts"], total_variance_drops=b["tv_drops"],
                 dropped=[dict(expiration=e, dte=d) for e, d in b["dropped"]]),
        metrics={k: m.get(k) for k in ("atm30", "rr25_30", "wing10_30", "short_expiry", "short_dte", "atm_short",
                                        "rr25_short", "wing10_short")},
        units="iv in %, metrics in vol points (ATM in %), vanna in delta per vol point",
        grid_call_delta=b["grid"], expiries=expiries,
        term=[dict(dte=d, atm_iv_pct=a, expiration=e) for d, a, e in b["term"]])
    caveat = (f"Clean view: Friday expiries {14} days to about 6 months, fitted smiles. Flagged if the fit gap is above "
              f"0.5 vol points or total variance falls with longer expiry. One day proves nothing on its own. {EDU}")
    return envelope(SURFACE_SOURCE, len(expiries), v.period_of(day), dict(date=key), caveat, data)


def get_dates():
    s = status.table()
    drawn = s[s.drawn]
    import datetime as dt
    dates = [dict(date=r.date, day_type=r.day_type, events=r.events or None, events_next_day=r.events_next_day or None,
                  flagged=r.flagged, live=v.is_live(dt.date.fromisoformat(r.date))) for r in drawn.itertuples()]
    failed = [dict(date=r.date, day_type=r.day_type, stage=r.stage, reason=r.reason) for r in s[s.failed].itertuples()]
    caveat = f"{len(failed)} trading days have no surface (see failed). {EDU}"
    return envelope([status.SOURCE], len(dates), f"{s.date.min()}..{s.date.max()}", {}, caveat,
                    dict(dates=dates, failed=failed, data_to=dates[-1]["date"] if dates else None))
