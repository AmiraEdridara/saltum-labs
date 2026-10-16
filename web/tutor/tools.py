"""The tutor's five read-only tools. Each calls the existing API (as the page proxy does) and returns a
compact result that keeps the API's source, n, period and caveat, so every number can be traced to a table."""
import re
import urllib.parse

DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TYPES = ["CPI", "NFP", "FOMC", "all"]
PERIODS = ["2020-01..2023-05", "2023-06..now", "both"]

TOOLS = [
    {"name": "list_events",
     "description": "Event-study summary for an event type and period, from the event_moves and event_premium tables: "
                    "median implied and actual move, median baseline, median premium and variance premium, share of days "
                    "with the actual move above the implied move, positive-premium count, and n. With include_events, also "
                    "one row per event (date, type, implied move, actual move, premium). Moves in %, premium in pp.",
     "input_schema": {"type": "object", "properties": {
         "type": {"type": "string", "enum": TYPES, "description": "CPI, NFP (payrolls), FOMC, or all (the three side by side)"},
         "period": {"type": "string", "enum": PERIODS, "description": "2020-01..2023-05, 2023-06..now, or both"},
         "include_events": {"type": "boolean", "description": "Also return one row per event (longer)."}},
         "required": ["type", "period"]}},
    {"name": "get_event",
     "description": "One event day (YYYY-MM-DD, the event date): implied and actual move, baseline and premium "
                    "(event_moves, event_premium), the skew numbers for the day before and the event day and their change, "
                    "including the fixed-strike ATM change (surface_metrics, event_skew_changes, event_fixed_strike), and "
                    "which day is flagged (data/all_dates_status.csv).",
     "input_schema": {"type": "object", "properties": {"date": {"type": "string", "description": "Event date, YYYY-MM-DD"}},
                      "required": ["date"]}},
    {"name": "get_surface_metrics",
     "description": "For any drawn trading day (YYYY-MM-DD): 30-day ATM vol, 25-delta risk reversal and put wing, the same "
                    "at the shortest expiry, whether the day is flagged and why, and the delta range drawn "
                    "(surface_metrics or control_surface_metrics, data/all_dates_status.csv).",
     "input_schema": {"type": "object", "properties": {"date": {"type": "string", "description": "YYYY-MM-DD"}},
                      "required": ["date"]}},
    {"name": "get_findings",
     "description": "The frozen results and caveats in data/findings_v1.md. Without a section, the list of sections. "
                    "Sections: 1 event study (premiums, which held in both periods), 2 exploratory payrolls check, "
                    "3 volatility surfaces, 4 skew and vanna, 5 Stage 5b controls (what survives both controls), "
                    "what-this-does-not-show.",
     "input_schema": {"type": "object", "properties": {"section": {"type": "string", "description": "Section id, e.g. 1 or 5"}}}},
    {"name": "get_definition",
     "description": "Plain definitions of the terms the site uses (implied move, baseline, premium, variance premium, "
                    "30-day ATM vol, risk reversal, put wing, fixed-strike ATM change, vanna, flagged day, ordinary control "
                    "pair, survives both controls, 90% interval, ...). Without a term, all of them.",
     "input_schema": {"type": "object", "properties": {"term": {"type": "string"}}}},
]
SIMPLE_TOOLS = [t for t in TOOLS if t["name"] == "get_definition"]


def r4(v):
    """Round floats to 3 decimals, as the pages and findings_v1.md show them (and to keep results short)."""
    if isinstance(v, float):
        return round(v, 3)
    if isinstance(v, dict):
        return {k: r4(x) for k, x in v.items()}
    if isinstance(v, list):
        return [r4(x) for x in v]
    return v


def head(j):
    return {k: j[k] for k in ("source", "n", "period", "caveat") if k in j}


class ToolRunner:
    """api_get(path) -> (status, json). Results are plain dicts; errors are returned as {"error": ...}."""

    def __init__(self, api_get):
        self.api_get = api_get

    async def run(self, name, args):
        try:
            fn = getattr(self, f"_{name}")
        except AttributeError:
            return {"error": f"unknown tool {name}"}
        try:
            return r4(await fn(**(args or {})))
        except TypeError as e:
            return {"error": f"bad arguments: {e}"}

    async def _get(self, path):
        status, j = await self.api_get(path)
        return status, j

    async def _list_events(self, type="all", period="both", include_events=False):
        if type not in TYPES or period not in PERIODS:
            return {"error": f"type must be one of {TYPES} and period one of {PERIODS}"}
        status, j = await self._get(f"/events?type={type}&period={period}")
        if status != 200:
            return {"error": j.get("detail") or j.get("error"), "status": status}
        out = dict(head(j), summaries=j["data"]["summaries"],
                   not_measured={k: j["data"]["not_measured"][k] for k in ("n", "by_type", "reason")})
        if include_events:
            out["events"] = [{k: e[k] for k in ("date", "event_type", "implied_move_pct", "actual_move_pct", "premium_pp")}
                             for e in j["data"]["events"]]
        return out

    async def _get_event(self, date):
        if not DATE.match(str(date)):
            return {"error": "date must be YYYY-MM-DD"}
        s1, ev = await self._get(f"/events/{date}")
        s2, sk = await self._get(f"/events/{date}/skew")
        s3, dates = await self._get("/dates")
        out = {"date": date}
        out["moves"] = dict(head(ev), **ev["data"]) if s1 == 200 else {"not_available": ev.get("detail")}
        out["skew"] = dict(head(sk), **sk["data"]) if s2 == 200 else {"not_available": sk.get("detail")}
        if s3 == 200 and s2 == 200:
            by = {d["date"]: d for d in dates["data"]["dates"]}
            out["flags"] = {k: bool(by.get(sk["data"][k]["date"], {}).get("flagged")) for k in ("day_before", "event_day")}
            out["flags_source"] = "data/all_dates_status.csv"
        return out

    async def _get_surface_metrics(self, date):
        if not DATE.match(str(date)):
            return {"error": "date must be YYYY-MM-DD"}
        status, j = await self._get(f"/surface/{date}")
        if status != 200:
            return {"error": j.get("detail") or j.get("error"), "status": status}
        d = j["data"]["day"]
        return dict(head(j), date=date, metrics=j["data"]["metrics"], units="vol points (ATM in %)",
                    day={k: d[k] for k in ("day_type", "events", "events_next_day", "flagged", "half_day", "range_label",
                                           "full_range", "fit_max_volpts", "total_variance_drops")},
                    expiries_drawn=j["n"])

    async def _get_findings(self, section=None):
        status, j = await self._get("/findings")
        if status != 200:
            return {"error": j.get("detail") or j.get("error"), "status": status}
        secs = j["data"]["sections"]
        if not section:
            return dict(head(j), sections=[{"id": s["id"], "title": s["title"]} for s in secs])
        s = next((s for s in secs if s["id"] == str(section).strip().lower()), None)
        if s is None:
            return {"error": f"no section {section!r}", "sections": [x["id"] for x in secs]}
        return dict(head(j), id=s["id"], title=s["title"], markdown=s["markdown"])

    async def _get_definition(self, term=None):
        status, j = await self._get("/definitions" + (f"?term={urllib.parse.quote(str(term))}" if term else ""))
        if status != 200:
            status, j = await self._get("/definitions")                 # unknown term: return the full list
        return dict(head(j), terms=j["data"]["terms"])
