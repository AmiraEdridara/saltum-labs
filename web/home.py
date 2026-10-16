"""GET /home/data: the homepage's numbers, for logged-out visitors too (docs/prd.md, Page 6).

A fixed bundle of frozen v1 summaries only: counts, medians, one term structure, five index rows and the findings
figures. It takes no parameters and returns no quotes. It is built from the logged-in API (the same endpoints the
terminal uses, called with the server's token), once per process: v1 rows never change."""
import asyncio

import httpx

from . import settings

EARLY, LATE = "2020-01..2023-05", "2023-06..now"
_cache = None
_lock = asyncio.Lock()


async def _get(client, path, token):
    r = await client.get(path, headers={"Authorization": f"Bearer {token or ''}"})
    r.raise_for_status()
    return r.json()


def _by_type(summaries):
    return {s["event_type"]: s for s in summaries}


async def build(transport, token):
    async with httpx.AsyncClient(base_url=settings.API_BASE, transport=transport, timeout=60) as c:
        # One request at a time: this runs once per process, and a burst can catch a freshly started API.
        both = await _get(c, "/events?type=all&period=both", token)
        early = await _get(c, f"/events?type=all&period={EARLY}", token)
        late = await _get(c, f"/events?type=all&period={LATE}", token)
        index = await _get(c, "/events/index", token)
        index_ex = await _get(c, "/events/index?exclude_flagged=true", token)
        dates = await _get(c, "/dates", token)
        v1_end = dates["v1_end"]
        days = [d for d in dates["data"]["dates"] if d["date"] <= v1_end]
        failed = [d for d in dates["data"]["failed"] if d["date"] <= v1_end]
        # The latest v1 CPI day whose day before is drawn: the vol surface and tutor previews.
        pair = next((days[i - 1]["date"], days[i]["date"]) for i in range(len(days) - 1, 0, -1)
                    if "CPI" in (days[i]["events"] or "") and "CPI" in (days[i - 1]["events_next_day"] or ""))
        before = await _get(c, f"/surface/{pair[0]}", token)
        after = await _get(c, f"/surface/{pair[1]}", token)
        card = await _get(c, f"/events/{pair[1]}", token)

    b, e, l = _by_type(both["data"]["summaries"]), _by_type(early["data"]["summaries"]), _by_type(late["data"]["summaries"])
    premium = {t: dict(early=e[t]["median_premium_pp"], early_n=e[t]["n"], late=l[t]["median_premium_pp"], late_n=l[t]["n"],
                       all=b[t]["median_premium_pp"], all_n=b[t]["n"], baseline_late_pct=l[t]["median_baseline_pct"])
               for t in ("CPI", "FOMC", "NFP")}
    ex = {(r["measure"], r["group"]): r for r in index_ex["data"]["summary"]}
    tested = [r for r in index["data"]["summary"] if r["survives_both_controls"] is not None]
    survivors = [dict(measure=r["measure"], type=r["group"], diff=r["event_minus_ordinary_volpts"], ci=r["ci90_volpts"], n=r["n"],
                      diff_ex=ex[(r["measure"], r["group"])]["event_minus_ordinary_volpts"],
                      ci_ex=ex[(r["measure"], r["group"])]["ci90_volpts"], n_ex=ex[(r["measure"], r["group"])]["n"])
                 for r in tested if r["survives_both_controls"]]
    ordinary = next(r for r in index["data"]["summary"] if r["group"] == "ordinary pairs")
    v1_rows = [r for r in index["data"]["events"] if not r["live"] and r["implied_move_pct"] is not None]
    v1_rows.sort(key=lambda r: r["event_date"], reverse=True)
    ev = card["data"]
    return dict(
        v1_end=v1_end,
        stats=dict(first_year=days[0]["date"][:4], last_year=v1_end[:4], trading_days=len(days) + len(failed),
                   events=sum(not r["live"] for r in both["data"]["events"]),
                   two_release_days=sum(not r["live"] and "+" in r["event_type"] for r in both["data"]["events"]),
                   control_pairs=ordinary["n"]),
        event_study=[dict(type=t, n=b[t]["n"], median_implied_pct=b[t]["median_implied_move_pct"],
                          median_actual_pct=b[t]["median_actual_move_pct"]) for t in ("CPI", "FOMC", "NFP")],
        surface=dict(day_before=pair[0], event_date=pair[1], event_type="CPI",
                     before=[dict(dte=x["dte"], atm_iv_pct=x["atm_iv_pct"]) for x in before["data"]["term"]],
                     after=[dict(dte=x["dte"], atm_iv_pct=x["atm_iv_pct"]) for x in after["data"]["term"]]),
        index_rows=[dict(event_date=r["event_date"], event_type=r["event_type"], implied_move_pct=r["implied_move_pct"],
                         actual_move_pct=r["actual_move_pct"], d_atm30=r["d_atm30"], flagged=r["flagged"]) for r in v1_rows[:5]],
        tutor_card=dict(date=ev["date"], event_type=ev["event_type"], baseline_n=ev["baseline_n"],
                        **{k: ev[k] for k in ("implied_move_pct", "actual_move_pct", "baseline_pct", "premium_pp")}),
        method=dict(surfaces_drawn=len(days), surfaces_flagged=sum(d["flagged"] for d in days), surfaces_failed=len(failed)),
        findings=dict(premium=premium, survivors=survivors, comparisons_tested=len(tested),
                      ordinary_pairs=ordinary["n"]),
        pricing=dict(student_daily=settings.TUTOR_DAILY_FREE, tutor_plus_monthly=settings.TUTOR_MONTHLY_PLUS,
                     tutor_plus_gbp_month=settings.TUTOR_PLUS_GBP_MONTH),
        sources={"stats": ["/dates", "/events", "/events/index"], "event_study": ["/events?type=all&period=both"],
                 "surface": [f"/surface/{pair[0]}", f"/surface/{pair[1]}"], "index_rows": ["/events/index"],
                 "tutor_card": [f"/events/{pair[1]}"], "findings": ["/events (per period)", "/events/index"],
                 "file": "data/findings_v1.md"})


async def home_data(transport, token):
    global _cache
    async with _lock:
        if _cache is None:
            try:
                _cache = await build(transport, token)
            except (httpx.ReadError, httpx.RemoteProtocolError):     # a dropped connection: try once more
                await asyncio.sleep(1)
                _cache = await build(transport, token)
    return _cache


def reset():
    global _cache
    _cache = None
