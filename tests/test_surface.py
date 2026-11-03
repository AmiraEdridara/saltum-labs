"""One surface date matches its prototype event page (data/figures/events/2026-09-11_CPI.html)."""
from tests.private_files import require

require("database", "event pages")
import json
import re

import pytest

import config

PAGE = config.PROJECT_DIR / "data" / "figures" / "events" / "2026-09-11_CPI.html"


def page_data():
    text = PAGE.read_text(encoding="utf-8")
    start = text.index("const DATA = ") + len("const DATA = ")
    return json.JSONDecoder().raw_decode(text[start:])[0]["dates"]


DATES = page_data()


@pytest.mark.parametrize("day", ["2026-09-10", "2026-09-11"])
def test_surface_matches_prototype_page(get, day):
    p, j = DATES[day], get(f"/surface/{day}")
    d = j["data"]
    assert j["n"] == len(p["expiries"])
    assert d["grid_call_delta"] == p["grid"]
    assert d["day"]["range"] == p["range"] and d["day"]["range_label"] == p["range_label"]
    assert d["day"]["full_range"] == p["full_range"] and d["day"]["half_day"] == p["half_day"]
    assert (d["day"]["fit_mean_volpts"], d["day"]["fit_max_volpts"]) == (p["fit_mean"], p["fit_max"])
    assert (d["day"]["fitted_points"], d["day"]["total_variance_drops"]) == (p["n_pts"], p["tv_drops"])
    assert [[e["expiration"], e["dte"]] for e in d["day"]["dropped"]] == p["dropped"]
    assert [[t["dte"], t["atm_iv_pct"], t["expiration"]] for t in d["term"]] == p["term"]
    for a, e in zip(d["expiries"], p["expiries"], strict=True):
        assert (a["expiration"], a["dte"], a["iv_pct"], a["strike"]) == (e["exp"], e["dte"], e["iv"], e["K"])
        assert [[o["call_delta"], o["iv_pct"], o["strike"]] for o in a["options"]] == e["pts"]
        assert all(isinstance(o["vanna"], float) for o in a["options"])


@pytest.mark.parametrize("day", ["2026-09-10", "2026-09-11"])
def test_metrics_match_prototype_text(get, day):
    nums = [float(x) for x in re.findall(r"-?\d+\.\d+", DATES[day]["metrics"])]
    m = get(f"/surface/{day}")["data"]["metrics"]
    api = [m["atm30"], m["rr25_30"], m["wing10_30"], m["atm_short"], m["rr25_short"], m["wing10_short"]]
    assert [round(x, 2) for x in api] == nums


def test_dates(get):
    j = get("/dates")
    assert len([d for d in j["data"]["dates"] if not d["live"]]) == 1695          # v1; live dates are added daily
    assert len([d for d in j["data"]["failed"] if d["date"] <= "2026-10-09"]) == 7
    days = {d["date"]: d for d in j["data"]["dates"]}
    assert days["2026-09-11"]["day_type"] == "event day" and days["2026-09-10"]["events_next_day"] == "CPI"
