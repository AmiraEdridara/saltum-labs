"""Every event-study summary the API returns matches data/stage3_periods.txt and data/findings_v1.md."""
import re

import pytest

import config

DATA = config.PROJECT_DIR / "data"
PERIODS = {"2020-01..2023-05": 0, "2023-06..now": 1, "both": 2}       # column order in stage3_periods.txt
TYPES = ["CPI", "NFP", "FOMC"]
MOVE_COLS = ["n", "mean_implied_move_pct", "median_implied_move_pct", "mean_actual_move_pct",
             "median_actual_move_pct", "median_ratio", "share_actual_above_implied_pct"]
PREMIUM_COLS = ["n", "median_baseline_pct", "median_premium_pp", "mean_premium_pp", "median_variance_premium_pct2"]


def stage3_tables():
    """{type: {column: [early, late, combined]}} from the 'All dates' section."""
    text = (DATA / "stage3_periods.txt").read_text(encoding="utf-8").split("######## Excluding")[0]
    out = {t: {} for t in TYPES}
    for t in TYPES:
        rows = [l.split()[1:] for l in text.splitlines() if l.split()[:1] == [t]][:2]   # moves, then premium
        for cols, vals in ((MOVE_COLS, rows[0]), (PREMIUM_COLS, rows[1])):
            assert len(vals) == 3 * len(cols)
            for i, c in enumerate(cols):
                out[t][c] = [float(x) for x in vals[3 * i:3 * i + 3]]
    return out


S3 = stage3_tables()


@pytest.mark.parametrize("period", PERIODS)
@pytest.mark.parametrize("type_", TYPES + ["all"])
def test_summary_matches_stage3_periods(get, type_, period):
    j = get(f"/events?type={type_}&period={period}")
    summaries = {s["event_type"]: s for s in j["data"]["summaries"]}
    assert list(summaries) == (TYPES if type_ == "all" else [type_])     # no pooled summary
    for t, s in summaries.items():
        for c, vals in S3[t].items():
            assert round(s[c], 3) == pytest.approx(vals[PERIODS[period]], abs=1e-9), (t, period, c)


def findings_premium_table():
    text = (DATA / "findings_v1.md").read_text(encoding="utf-8")
    out = {}
    for t in TYPES:
        m = re.search(rf"^\| {t} \| (\d+) / (\d+) / (\d+) \| ([+-][\d.]+) / ([+-][\d.]+) / ([+-][\d.]+) \| "
                      rf"2020–23: (\d+)/(\d+) \(\d+%\); combined: (\d+)/(\d+) \|$", text, re.M)
        assert m, t
        g = m.groups()
        out[t] = dict(n=[int(x) for x in g[:3]], median_premium=[float(x) for x in g[3:6]],
                      positive_early=(int(g[6]), int(g[7])), positive_combined=(int(g[8]), int(g[9])))
    return out, text


def test_premiums_match_findings(get):
    table, _ = findings_premium_table()
    for t, f in table.items():
        for period, i in PERIODS.items():
            s = get(f"/events?type={t}&period={period}")["data"]["summaries"][0]
            assert s["n"] == f["n"][i]
            assert round(s["median_premium_pp"], 3) == pytest.approx(f["median_premium"][i], abs=1e-9)
        early = get(f"/events?type={t}&period=2020-01..2023-05")["data"]["summaries"][0]
        both = get(f"/events?type={t}&period=both")["data"]["summaries"][0]
        assert (early["positive_premium_n"], early["n"]) == f["positive_early"]
        assert (both["positive_premium_n"], both["n"]) == f["positive_combined"]


def test_combined_table_and_coverage_match_findings(get):
    _, text = findings_premium_table()
    for t in TYPES:
        m = re.search(rf"^\| {t} \| (\d+) \| ([\d.]+)% \| ([\d.]+)% \| ([\d.]+) \| ([\d.]+)% \|$", text, re.M)
        s = get(f"/events?type={t}&period=both")["data"]["summaries"][0]
        assert s["n"] == int(m.group(1))
        for c, x in zip(["median_implied_move_pct", "median_actual_move_pct", "median_ratio",
                         "share_actual_above_implied_pct"], m.groups()[1:]):
            assert round(s[c], 3) == pytest.approx(float(x), abs=1e-9), (t, c)
    # "15 event days in 2020-22 (14 CPI, 1 FOMC) have no same-day SPXW expiry and are left out"
    nm = get("/events?type=all&period=both")["data"]["not_measured"]
    assert "15 event days in 2020–22 (14 CPI, 1 FOMC)" in text
    assert nm["n"] == 15 and nm["by_type"] == {"CPI": 14, "NFP": 0, "FOMC": 1}
    assert all(d["date"] < "2023-01-01" for d in nm["dates"])


def test_one_event(get):
    j = get("/events/2026-09-11")
    rows = get("/events?type=CPI&period=2023-06..now")["data"]["events"]
    assert j["data"] == next(r for r in rows if r["date"] == "2026-09-11")
    assert j["n"] == 1 and j["period"] == "2023-06..now"


def test_skew_matches_tables(get):
    d = get("/events/2026-09-11/skew")["data"]
    row = next(r for r in get("/events/index?type=CPI")["data"]["events"] if r["event_date"] == "2026-09-11")
    assert d["change"]["d_atm30"] == row["d_atm30"] and d["change"]["d_atm30_fixed_strike"] == row["d_atm30_fixed_strike"]
    assert d["day_before"]["atm30"] == row["atm30_before"] and d["event_day"]["atm30"] == row["atm30_after"]


@pytest.mark.parametrize("url", ["/events?type=all&period=both", "/events?type=all&period=2020-01..2023-05",
                                 "/events?type=CPI&period=2023-06..now"])
def test_caveat_counts_match_returned_summaries(get, url):
    j = get(url)
    ns = [s["n"] for s in j["data"]["summaries"]]
    from api.common import count_range
    assert f"{count_range(ns)} events per type" in j["caveat"]
