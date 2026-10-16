"""Page 6, the homepage: GET / for everyone, GET /home/data public with fixed v1 summaries only, every number on the
page traced to the API or data/findings_v1.md, the ten sections, phone width, keyboard tabs, reduced motion, and a
tutor demo that makes no requests."""
import json
import re

import pytest

import config
from web import home, settings
from webutil import sign_up

FINDINGS = (config.PROJECT_DIR / "data" / "findings_v1.md").read_text(encoding="utf-8")
STATIC = config.PROJECT_DIR / "web" / "static"
MINUS = "−"
SECTIONS = ["hero", "stats", "tour", "demo", "method", "honest", "findings", "next", "pricing", "footer"]


def signed(x, d):
    return (MINUS if x < 0 else "+") + f"{abs(x):.{d}f}"


FMT = {"text": str, "int": lambda x: f"{int(x):,}", "pp3": lambda x: signed(x, 3), "vp3": lambda x: signed(x, 3),
       "pct3": lambda x: f"{x:.3f}", "iv1": lambda x: f"{x:.1f}", "range2": lambda x: f"{x:.2f}"}


def pick(data, path):
    for k in path.split("."):
        data = data[int(k)] if isinstance(data, list) else data[k]
    return data


@pytest.fixture(autouse=True)
def fresh_cache():
    home.reset()
    yield
    home.reset()


# ---------------------------------------------------------------- routes
def test_root_serves_the_homepage_logged_out_and_logged_in(client):
    r = client.get("/")
    assert r.status_code == 200 and 'id="hero"' in r.text and r.headers["cache-control"] == "no-store"
    sign_up(client)
    r = client.get("/")
    assert r.status_code == 200 and 'id="hero"' in r.text            # logged-in users can still reach it


def test_home_data_is_public_fixed_and_has_no_quotes(client):
    r = client.get("/home/data")
    assert r.status_code == 200
    j = r.json()
    assert set(j) == {"v1_end", "stats", "event_study", "surface", "index_rows", "tutor_card", "method", "findings",
                      "pricing", "sources", "signed_in"}
    assert j["signed_in"] is False and j["v1_end"] == str(config.V1_END)
    text = json.dumps(j)
    for quote_field in ("bid", "ask", "straddle", "strike", "forward", "quotes_kept"):
        assert f'"{quote_field}"' not in text
    assert client.get("/home/data?type=CPI").status_code == 400           # no parameters
    sign_up(client)
    assert client.get("/home/data").json()["signed_in"] is True


def test_everything_else_still_needs_a_login(client):
    for path in ("/api/events", "/api/dates", "/api/findings", "/api/events/index", "/api/surface/2026-09-11",
                 "/account/me", "/tutor/status"):
        assert client.get(path).status_code == 401, path
    assert client.get("/terminal").status_code == 303


def test_security_policy_is_unchanged(client):
    assert client.get("/").headers["content-security-policy"] == client.get("/login").headers["content-security-policy"]
    assert "script-src" not in client.get("/").headers["content-security-policy"]       # 'self' only, via default-src


# ---------------------------------------------------------------- every field equals the logged-in API
@pytest.fixture()
def data_and_api(client):
    data = client.get("/home/data").json()
    sign_up(client)
    api = lambda path: client.get("/api" + path).json()                  # noqa: E731
    return data, api


def test_home_data_matches_the_api(data_and_api):
    data, api = data_and_api
    v1 = data["v1_end"]
    both = {s["event_type"]: s for s in api("/events?type=all&period=both")["data"]["summaries"]}
    for r in data["event_study"]:
        s = both[r["type"]]
        assert (r["n"], r["median_implied_pct"], r["median_actual_pct"]) == (s["n"], s["median_implied_move_pct"], s["median_actual_move_pct"])
    for period, key in (("2020-01..2023-05", "early"), ("2023-06..now", "late"), ("both", "all")):
        summ = {s["event_type"]: s for s in api(f"/events?type=all&period={period}")["data"]["summaries"]}
        for t, p in data["findings"]["premium"].items():
            assert (p[key], p[f"{key}_n"]) == (summ[t]["median_premium_pp"], summ[t]["n"]), (t, period)
    events = api("/events?type=all&period=both")["data"]["events"]
    assert data["stats"]["events"] == sum(not e["live"] for e in events) == 187
    # The per-type n's count a two-release day (CPI+FOMC) once for each type: they add to the event days plus those.
    two = [e["date"] for e in events if not e["live"] and "+" in e["event_type"]]
    assert data["stats"]["two_release_days"] == len(two) == 2 and sorted(two) == ["2020-06-10", "2024-06-12"]
    assert sum(r["n"] for r in data["event_study"]) == data["stats"]["events"] + data["stats"]["two_release_days"] == 195
    d = api("/dates")["data"]
    days = [x for x in d["dates"] if x["date"] <= v1]
    failed = [x for x in d["failed"] if x["date"] <= v1]
    assert data["stats"]["trading_days"] == len(days) + len(failed)
    assert (data["stats"]["first_year"], data["stats"]["last_year"]) == (days[0]["date"][:4], v1[:4])
    assert data["method"] == dict(surfaces_drawn=len(days), surfaces_flagged=sum(x["flagged"] for x in days), surfaces_failed=len(failed))
    index = api("/events/index")["data"]
    ordinary = next(r for r in index["summary"] if r["group"] == "ordinary pairs")
    assert data["stats"]["control_pairs"] == data["findings"]["ordinary_pairs"] == ordinary["n"] == 100
    tested = [r for r in index["summary"] if r["survives_both_controls"] is not None]
    assert data["findings"]["comparisons_tested"] == len(tested) == 9
    survived = {(r["measure"], r["group"]): r for r in tested if r["survives_both_controls"]}
    ex = {(r["measure"], r["group"]): r for r in api("/events/index?exclude_flagged=true")["data"]["summary"]}
    assert {(s["measure"], s["type"]) for s in data["findings"]["survivors"]} == set(survived)
    for s in data["findings"]["survivors"]:
        a, b = survived[(s["measure"], s["type"])], ex[(s["measure"], s["type"])]
        assert (s["diff"], s["ci"], s["n"]) == (a["event_minus_ordinary_volpts"], a["ci90_volpts"], a["n"])
        assert (s["diff_ex"], s["ci_ex"], s["n_ex"]) == (b["event_minus_ordinary_volpts"], b["ci90_volpts"], b["n"])
    rows = sorted((r for r in index["events"] if not r["live"] and r["implied_move_pct"] is not None),
                  key=lambda r: r["event_date"], reverse=True)[:5]
    assert data["index_rows"] == [{k: r[k] for k in ("event_date", "event_type", "implied_move_pct", "actual_move_pct", "d_atm30", "flagged")}
                                  for r in rows]
    sf = data["surface"]
    for key, day in (("before", sf["day_before"]), ("after", sf["event_date"])):
        term = api(f"/surface/{day}")["data"]["term"]
        assert sf[key] == [dict(dte=t["dte"], atm_iv_pct=t["atm_iv_pct"]) for t in term]
    card = api(f"/events/{data['tutor_card']['date']}")["data"]
    assert all(data["tutor_card"][k] == card[k] for k in ("event_type", "implied_move_pct", "actual_move_pct", "baseline_pct",
                                                          "baseline_n", "premium_pp"))
    assert data["pricing"] == dict(student_daily=settings.TUTOR_DAILY_FREE, tutor_plus_monthly=settings.TUTOR_MONTHLY_PLUS,
                                   tutor_plus_gbp_month=settings.TUTOR_PLUS_GBP_MONTH)


def test_findings_numbers_are_the_frozen_ones(client):
    f = client.get("/home/data").json()["findings"]
    table = FINDINGS.split("### Event premium")[1].split("**Controls")[0]
    for t in ("CPI", "FOMC", "NFP"):
        p = f["premium"][t]
        row = next(line for line in table.splitlines() if line.startswith(f"| {t} |"))
        assert f"{p['early_n']} / {p['late_n']} / {p['all_n']}" in row
        assert f"{signed(p['early'], 3)} / {signed(p['late'], 3)} / {signed(p['all'], 3)}".replace(MINUS, "−") in row
    stage5b = FINDINGS.split("**Event minus ordinary, median")[1].split("**Ordinary-pair medians")[0]
    names = {"30-day ATM (fixed strike)": "30-day ATM (fixed strike)", "30-day RR25": "30-day RR25", "30-day put wing": "30-day put wing"}
    for s in f["survivors"]:
        row = next(line for line in stage5b.splitlines() if line.startswith(f"| {names[s['measure']]} | {s['type']} |"))
        assert f"{signed(s['diff'], 3)} [{signed(s['ci'][0], 3)}, {signed(s['ci'][1], 3)}] ({s['n']})" in row
        assert f"{signed(s['diff_ex'], 3)} [{signed(s['ci_ex'][0], 3)}, {signed(s['ci_ex'][1], 3)}] ({s['n_ex']})" in row
        assert "**yes**" in row
    survivors = {(s["type"], s["measure"]) for s in f["survivors"]}
    assert survivors == {("CPI", "30-day ATM (fixed strike)"), ("CPI", "30-day RR25"), ("CPI", "30-day put wing"),
                         ("NFP", "30-day ATM (fixed strike)"), ("FOMC", "30-day RR25")}        # Finding 5.1, as written


# ---------------------------------------------------------------- the scripted demo
DEMO = (STATIC / "js" / "home_demo.js").read_text(encoding="utf-8")


def test_demo_numbers_match_the_api_and_findings(client):
    p = client.get("/home/data").json()["findings"]["premium"]
    want = {"+0.264": p["CPI"]["late"], "+0.352": p["CPI"]["early"], "+0.164": p["NFP"]["late"], "+0.018": p["NFP"]["early"]}
    for text, value in want.items():
        assert signed(value, 3) == text and text in FINDINGS and text in DEMO
    assert f"n {p['CPI']['late_n']}" in DEMO and f"n {p['CPI']['early_n']}" in DEMO and f"n {p['NFP']['late_n']}" in DEMO
    assert f"{p['CPI']['baseline_late_pct']:.3f}%" == "0.497%" and "0.497%" in DEMO
    assert "0.493–0.506 pp in\n2023–26" in FINDINGS                    # the frozen range that 0.497 sits in
    # Every number in the script is one of those (or a period, an n, or a finding number).
    script = DEMO.split("export const DEMOS")[1]
    script = re.sub(r"\d{4}-\d{2}\.\.(?:\d{4}-\d{2}|now)", "PERIOD", script)       # the tool calls' period arguments
    script = re.sub(r"20(?:20|23)–2[36]", "YEARS", script)                        # 2020–23, 2023–26
    allowed = set(want) | {"0.497", "39", "26", "1.3"}
    for tok in re.findall(r"(?<![\w.])[+−-]?\d+(?:\.\d+)?", script):
        assert tok in allowed, tok


def test_demo_makes_no_requests_by_construction():
    js = (STATIC / "js" / "home.js").read_text(encoding="utf-8")
    assert not re.search(r"\bfetch\b|XMLHttpRequest|import\s*\(|WebSocket|sendBeacon", DEMO)
    assert len(re.findall(r"\bfetch\(", js)) == 1 and 'fetch("/home/data"' in js       # one request, on load


# ---------------------------------------------------------------- in headless Edge
EXTRACT = r"""(() => {
  const txt = (e) => e.textContent.replace(/\s+/g, ' ').trim();
  const vis = (e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const nums = [...document.querySelectorAll('[data-src]')].map((e) => ({ src: e.dataset.src, fmt: e.dataset.fmt, text: e.textContent,
                                                                        inDemo: !!e.closest('#demo') }));
  // Visible text with the data-src numbers, axis ticks and the demo removed: what is left must be sourced in findings_v1.
  const clone = document.body.cloneNode(true);
  clone.querySelectorAll('[data-src], [data-tick], #demo-chat, script, .hidden').forEach((e) => e.remove());
  const words = [];
  for (const w = document.createTreeWalker(clone, NodeFilter.SHOW_TEXT); w.nextNode();) words.push(w.currentNode.nodeValue);
  const sections = [...document.querySelectorAll('main > section[id], body > footer[id]')].map((e) => ({ id: e.id, text: txt(e).length, visible: vis(e) }));
  const cut = [...document.querySelectorAll('body *')].filter((e) => vis(e) && !e.closest('.figures, .mini-table, svg'))
    .filter((e) => e.getBoundingClientRect().right > innerWidth + 1).map((e) => e.tagName + '.' + e.className);
  const edu = [...document.querySelectorAll('.masthead-line span')][0].getBoundingClientRect();
  const steps = [...document.querySelectorAll('#demo-chat .step')];
  return { nums, rest: words.join(' ').replace(/\s+/g, ' '), sections, cut: cut.slice(0, 10), scrollX: document.documentElement.scrollWidth > innerWidth,
           edu_visible: edu.top >= 0 && edu.bottom <= innerHeight, data: window.__home,
           demo: { done: document.getElementById('demo-chat').dataset.done, steps: steps.length, entering: steps.filter((s) => s.classList.contains('enter')).length,
                   transition: steps[0] ? getComputedStyle(steps[0]).transitionDuration : null, text: txt(document.getElementById('demo-chat')),
                   pressed: document.querySelector('.demo-pick[aria-pressed="true"]')?.dataset.demo },
           requests_after: window.__mark == null ? null : performance.getEntriesByType('resource').filter((e) => e.startTime > window.__mark).map((e) => e.name),
           requests_all: performance.getEntriesByType('resource').map((e) => e.name),
           tabs: [...document.querySelectorAll('[role=tab]')].map((t) => ({ id: t.id, selected: t.getAttribute('aria-selected'), tabindex: t.tabIndex,
                   panel_visible: !document.getElementById(t.getAttribute('aria-controls')).classList.contains('hidden') })),
           signed_in_visible: [...document.querySelectorAll('[data-signed-in]')].some(vis),
           signed_out_visible: [...document.querySelectorAll('[data-signed-out]')].some(vis),
           links: Object.fromEntries([...document.querySelectorAll('a.open, .actions a, .plan a')].filter(vis).map((a) => [txt(a), a.getAttribute('href')])) };
})()"""
READY = "document.body.dataset.ready === 'true'"
REDUCED = [{"name": "prefers-reduced-motion", "value": "reduce"}]


@pytest.fixture(scope="module")
def pages(browser):
    visits = {
        # Desktop, logged out: scroll to the demo, mark the time, replay and switch questions, then read every request since.
        "desktop": dict(url="/", logged_out=True, wait=READY,
                        wait_after="document.getElementById('demo-chat').dataset.done === 'true'"
                                   " && document.querySelector('.demo-pick[aria-pressed=\"true\"]').dataset.demo === '1'",
                        actions=["(() => { window.__mark = performance.now(); document.getElementById('demo').scrollIntoView(); })()",
                                 "document.getElementById('demo-replay').click()",
                                 "document.querySelector('.demo-pick[data-demo=\"1\"]').click()"],
                        extract=EXTRACT, timeout_s=60),
        "phone": dict(url="/", logged_out=True, width=390, height=844, mobile=True, media=REDUCED, wait=READY, extract=EXTRACT),
        "keyboard": dict(url="/", logged_out=True, wait=READY + " && (document.getElementById('tab-study').focus(), true)",
                         keys=["ArrowRight", "ArrowRight"], extract=EXTRACT),
        "keyboard_end": dict(url="/", logged_out=True, wait=READY + " && (document.getElementById('tab-study').focus(), true)",
                             keys=["End"], extract=EXTRACT),
        "signed_in": dict(url="/", wait=READY, media=REDUCED, extract=EXTRACT),
    }
    out = browser.visit(list(visits.values()))
    return {k: r for k, r in zip(visits, out)}


def test_pages_render_without_errors(pages):
    for k, r in pages.items():
        assert r["ready"] and r["error"] is None and r["errors"] == [], (k, r["error"], r["errors"])


def test_the_ten_sections_render_in_order(pages):
    for k in ("desktop", "phone"):
        secs = pages[k]["value"]["sections"]
        assert [s["id"] for s in secs] == SECTIONS
        assert all(s["visible"] and s["text"] > 40 for s in secs), secs
    for k in ("phone", "signed_in"):                                  # not scrolled (the desktop visit scrolls to the demo)
        assert pages[k]["value"]["edu_visible"]                       # "Education only, not advice" without scrolling


def test_every_number_matches_the_api_or_findings(pages):
    for k in ("desktop", "phone", "signed_in"):
        v = pages[k]["value"]
        assert len(v["nums"]) > 80
        for n in v["nums"]:
            assert n["text"] == FMT[n["fmt"]](pick(v["data"], n["src"])), (k, n)
        sizes = [abs(s["diff"]) for s in v["data"]["findings"]["survivors"]]
        assert v["data"]["findings"]["plain_range"] == [min(sizes), max(sizes)]
        # Numbers written into the page text (times, windows, finding numbers) must appear in findings_v1.md.
        for tok in set(re.findall(r"(?<![\w.:])[+−-]?\d[\d,]*(?:[.:]\d+)?%?", v["rest"])):
            tok = tok.strip()
            assert tok in FINDINGS or tok == "500", (k, tok)               # 500: the index's name, S&P 500


def test_the_demo_plays_without_any_request(pages):
    v = pages["desktop"]["value"]
    assert v["demo"]["done"] == "true" and v["demo"]["pressed"] == "1" and v["demo"]["steps"] == 5
    assert v["requests_after"] == [], v["requests_after"]              # replay and switching: no requests at all
    calls = [u for u in v["requests_all"] if "/static/" not in u]
    assert [u.split("127.0.0.1:")[1].split("/", 1)[1] for u in calls] == ["home/data"]
    assert "+0.018 pp" in v["demo"]["text"] and "Finding 1.3" in v["demo"]["text"]


def test_reduced_motion_shows_the_demo_at_once(pages):
    d = pages["phone"]["value"]["demo"]
    assert d["done"] == "true" and d["steps"] == 5 and d["entering"] == 0 and d["transition"] == "0s"


def test_phone_width(pages):
    v = pages["phone"]["value"]
    assert not v["scrollX"] and v["cut"] == [], v["cut"]


def test_tabs_work_from_the_keyboard(pages):
    def selected(k):
        return [t["id"] for t in pages[k]["value"]["tabs"] if t["selected"] == "true"]
    assert selected("desktop") == ["tab-study"]
    assert selected("keyboard") == ["tab-index"] and selected("keyboard_end") == ["tab-backtest"]
    for t in pages["keyboard"]["value"]["tabs"]:
        assert t["panel_visible"] == (t["selected"] == "true") and t["tabindex"] == (0 if t["selected"] == "true" else -1)


def test_links_and_signed_in_state(pages):
    out, signed_in = pages["desktop"]["value"], pages["signed_in"]["value"]
    assert out["links"]["Create a free account"] == "/login?mode=signup" and out["links"]["Open the terminal"] == "/terminal"
    assert out["links"]["Sign up"] == "/login?mode=signup" and out["links"]["Open the event study"].startswith("/terminal?a=event-study")
    assert out["signed_out_visible"] and not out["signed_in_visible"]
    assert signed_in["signed_in_visible"] and not signed_in["signed_out_visible"]
