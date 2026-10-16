"""The event index summary matches data/stage5b_summary.txt exactly."""
import re

import pytest

import config

ROW = re.compile(r"^\s*(30-day .+?)\s+(ordinary pairs|CPI|NFP|FOMC)\s+(\d+)\s+([+-]\d+\.\d{3})\s+(\d+%)\s+(\S+)\s+(–|\[.+\])$")
SURV = re.compile(r"^\s*(30-day .+?)\s+(CPI|NFP|FOMC)\s+(True|False)\s+(True|False)\s+(True|False)$")


def frozen():
    text = (config.PROJECT_DIR / "data" / "stage5b_summary.txt").read_text(encoding="utf-8")
    sections = {}
    for label, flag in (("ALL", False), ("EXCLUDING FLAGGED", True)):
        block = text.split(f"######## {label} ########")[1].split("Spot-vol")[0]
        sections[flag] = [m.groups() for m in map(ROW.match, block.splitlines()) if m]
    surv = [m.groups() for m in map(SURV.match, text.split("Survives both controls")[1].splitlines()) if m]
    return sections, surv


SECTIONS, SURVIVAL = frozen()


def fmt(r):
    """The API row in stage5b_controls.fmt_compare's text format."""
    d, (lo, hi) = r["event_minus_ordinary_volpts"], r["ci90_volpts"]
    return (r["measure"], r["group"], str(r["n"]), f"{r['median_volpts']:+.3f}", f"{r['share_positive_pct'] / 100:.0%}",
            "–" if d is None else f"{d:+.3f}", "–" if lo is None else f"[{lo:+.3f}, {hi:+.3f}]")


@pytest.mark.parametrize("exclude_flagged", [False, True])
def test_summary_matches_stage5b_exactly(get, exclude_flagged):
    assert len(SECTIONS[exclude_flagged]) == 16
    rows = get(f"/events/index?exclude_flagged={str(exclude_flagged).lower()}")["data"]["summary"]
    assert [fmt(r) for r in rows] == SECTIONS[exclude_flagged]


def test_survives_column_matches_stage5b(get):
    assert len(SURVIVAL) == 9
    rows = get("/events/index")["data"]["summary"]
    api = {(r["measure"], r["group"]): r["survives_both_controls"] for r in rows}
    for measure, group, _, _, survives in SURVIVAL:
        assert api[(measure, group)] == (survives == "True"), (measure, group)
    assert all(api[(m, g)] is None for m, g in api if m == "30-day ATM (50-delta)" or g == "ordinary pairs")


@pytest.mark.parametrize("type_", ["CPI", "NFP", "FOMC"])
def test_type_filter_selects_frozen_rows(get, type_):
    rows = get(f"/events/index?type={type_}")["data"]["summary"]
    assert [fmt(r) for r in rows] == [r for r in SECTIONS[False] if r[1] in ("ordinary pairs", type_)]


def test_single_period_has_rows_but_no_summary(get):
    j = get("/events/index?period=2020-01..2023-05")
    assert j["data"]["summary"] == [] and "pools both periods" in j["data"]["summary_note"]
    assert j["n"] > 0 and all(r["period"] == "2020-01..2023-05" for r in j["data"]["events"])


def test_rows_and_filters(get):
    all_rows = get("/events/index")
    v1 = [r for r in all_rows["data"]["events"] if not r["live"]]        # the frozen rows; live ones are added daily
    assert len(v1) == 205 and all(r["event_date"] > "2026-10-09" for r in all_rows["data"]["events"] if r["live"])
    with_ordinary = get("/events/index?include_ordinary=true")
    assert with_ordinary["n"] == all_rows["n"] + 100
    ex = get("/events/index?exclude_flagged=true")["data"]["events"]
    assert len([r for r in ex if not r["live"]]) == 143 and not any(r["flagged"] for r in ex)
    for r in get("/events/index?type=all")["data"]["missing"]:
        assert r["reason"]


@pytest.mark.parametrize("url", ["/events/index", "/events/index?exclude_flagged=true", "/events/index?type=FOMC"])
def test_caveat_counts_match_returned_rows(get, url):
    j = get(url)
    ns = [r["n"] for r in j["data"]["summary"] if r["group"] != "ordinary pairs"]
    from api.common import count_range
    assert f"with {count_range(ns)} events per type" in j["caveat"]
