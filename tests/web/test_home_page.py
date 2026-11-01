"""Page 6, the homepage: a public page with no real numbers. GET / for everyone, no data route and no data request, the
six sections, a tutor demo on sample figures, findings in words, no internal wording, phone width, reduced motion. Also the
public /method page, under the same rules. The terminal preview in section 2 is tested in test_preview.py."""
import json
import re

import pytest

import config
from web import settings
from webutil import sign_up

FINDINGS = (config.PROJECT_DIR / "data" / "findings_v1.md").read_text(encoding="utf-8")
STATIC = config.PROJECT_DIR / "web" / "static"
SAMPLE = json.loads((STATIC / "preview" / "sample.json").read_text(encoding="utf-8"))
HTML = (STATIC / "home.html").read_text(encoding="utf-8")
JS = (STATIC / "js" / "home.js").read_text(encoding="utf-8")
DEMO = (STATIC / "js" / "home_demo.js").read_text(encoding="utf-8")
SECTIONS = ["hero", "tour", "demo", "findings", "honest", "pricing", "footer"]
POINTS = ["Every event is compared with ordinary days.", "Results are checked in a second period.", "Weak results stay labelled weak.",
          "Education only: nothing here says a trade would have paid."]
METHOD_TEXT = ["How the numbers are made", "SPX and SPXW option quotes", "Implied and actual move", "Baselines", "Flagged days", "What we don't claim",
               "Weak results stay labelled weak", "One event is not a pattern", "S&amp;P 500 options only", "Four checks",
               "an independent recheck from the raw chains with separate code."]
INTERNAL = r"frozen|findings_v1|findings v1|\bv1\b|\blive\b|Stage \d|local version|local edition|on this computer|ThetaData|FRED|provisional"
NUMBER = r"(?<![\w.:])[+−-]?\d[\d,]*(?:[.:]\d+)?%?"


def sample_premium(period, t):
    s = SAMPLE["summaries"][period][t]
    return f"+{s['median_premium_pp']:.3f}", s["n"]


EARLY, LATE = "earlier", "later"
DEMO_FIGURES = {sample_premium(p, t)[0] for p in (EARLY, LATE) for t in ("CPI", "NFP")} | {f"{SAMPLE['summaries'][LATE]['CPI']['median_baseline_pct']:.3f}%"}
DEMO_COUNTS = {str(sample_premium(p, t)[1]) for p in (EARLY, LATE) for t in ("CPI", "NFP")}
PRICES = {str(settings.TUTOR_DAILY_FREE), str(settings.TUTOR_MONTHLY_PLUS), str(settings.TUTOR_PLUS_GBP_MONTH)}
ALLOWED = DEMO_FIGURES | DEMO_COUNTS | PRICES | {"500"}                # 500: the index's name, S&P 500


def real_figures():
    """The figures and dates of the frozen findings, and the counts the old homepage showed."""
    decimals = re.findall(r"\d+\.\d{2,}", FINDINGS)
    dates = re.findall(r"\d{4}-\d{2}-\d{2}", FINDINGS)
    years = re.findall(r"\b20(?:20|2[1-6])[–-]\d{2}\b", FINDINGS)
    return set(decimals) | set(dates) | set(years) | {str(config.V1_END), "1,650", "1,630", "187", "411", "2023-05", "2023-06"}


def real_in(text):
    """Whole dates are compared whole, so that an invented day in a real month does not count as the real month."""
    iso = r"\b\d{4}-\d{2}-\d{2}\b"
    real, rest = real_figures(), re.sub(iso, " ", text)
    return sorted((set(re.findall(iso, text)) & real) | {f for f in real if re.search(rf"(?<![\d.]){re.escape(f)}(?![\d])", rest)})


# ---------------------------------------------------------------- routes
def test_root_serves_the_homepage_logged_out_and_logged_in(client):
    r = client.get("/")
    assert r.status_code == 200 and 'id="hero"' in r.text and r.headers["cache-control"] == "no-store"
    assert "<body>" in r.text and "signed-in\">" not in r.text.split("<body>")[1][:5]
    sign_up(client)
    r = client.get("/")
    assert r.status_code == 200 and '<body class="signed-in">' in r.text   # logged-in users can still reach it


def test_there_is_no_homepage_data_route(client):
    assert client.get("/home/data").status_code == 404
    sign_up(client)
    assert client.get("/home/data").status_code == 404


def test_every_data_route_needs_a_login(client):
    for path in ("/api/events", "/api/dates", "/api/findings", "/api/events/index", "/api/surface/2026-09-11",
                 "/account/me", "/tutor/status"):
        assert client.get(path).status_code == 401, path
    assert client.get("/terminal").status_code == 303


def test_the_method_page_is_public_and_holds_the_full_text(client):
    r = client.get("/method")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assert all(s in re.sub(r"\s+", " ", r.text) for s in METHOD_TEXT)
    assert r.headers["content-security-policy"] == client.get("/").headers["content-security-policy"]
    assert "<script" not in r.text


def test_the_homepage_keeps_only_the_short_version(client):
    page = client.get("/").text
    assert all(f"<li>{p}</li>" in page for p in POINTS) and '<a href="/method">Read the full method</a>' in page
    assert '<a href="/method">Method</a>' in page and "How the numbers are made" not in page and "What we don't claim" not in page
    assert "What we're building" not in page and 'id="next"' not in page
    head, pricing = page.split('id="pricing"')
    assert pricing.index("Coming next: backtests, more underlyings and alerts.") < pricing.index("Two plans")


def test_security_policy_is_unchanged(client):
    assert client.get("/").headers["content-security-policy"] == client.get("/login").headers["content-security-policy"]
    assert "script-src" not in client.get("/").headers["content-security-policy"]       # 'self' only, via default-src


# ---------------------------------------------------------------- no real numbers
# ---------------------------------------------------------------- every public file, not only the rendered homepage
PUBLIC_FILES = [*STATIC.glob("*.html"), *(STATIC / "js").rglob("*.js"), *(STATIC / "css").glob("*.css")]
SAMPLE_FILE = (STATIC / "preview" / "sample.json").read_text(encoding="utf-8")
LOGIN_ONLY = r"frozen|findings_v1|findings v1|\bv1\b|Stage \d"
PLOT_CONSTANTS = {"0.02", "0.75", "0.85"}                               # layout values in vol_surface.js; they are not results


def sample_values(node, key=""):
    if isinstance(node, dict):
        for k, v in node.items():
            yield k, k                                                  # keys are text too: "summaries" was keyed by period
            yield from sample_values(v, k)
    elif isinstance(node, list):
        for v in node:
            yield from sample_values(v, key)
    else:
        yield key, node


def test_the_public_file_guards_can_fail():
    assert real_in('const PERIODS = ["both", "2020-01..2023-05", "2023-06..now"];')
    assert real_in('{"period":"2023-06..now"}') and not real_in('{"date":"2023-05-18"}')  # an invented day in a real month
    assert real_in(f'"{config.V1_END}"')
    assert re.search(LOGIN_ONLY, "· v1 frozen to") and re.search(LOGIN_ONLY, "(Stage 5b)")


def test_no_public_file_holds_a_real_figure_or_date():
    for f in PUBLIC_FILES:
        assert set(real_in(f.read_text(encoding="utf-8"))) <= PLOT_CONSTANTS, f


def test_no_text_in_the_sample_file_holds_a_real_figure_or_date():
    # Its 4,700 raw numbers are invented, and some match a figure in the findings by chance (0.02), so those are not
    # compared one by one; every string, every date and every median is.
    assert real_in(" ".join(v for _, v in sample_values(SAMPLE) if isinstance(v, str))) == []


def test_no_median_in_the_sample_file_is_a_real_figure():
    real = set(re.findall(r"\d+\.\d{3}", FINDINGS))
    medians = [(k, v) for k, v in sample_values(SAMPLE) if k.startswith("median_") and isinstance(v, float)]
    assert medians and not [(k, v) for k, v in medians if f"{abs(v):.3f}" in real]


def test_no_public_file_has_login_only_wording():
    for name, text in [(f.name, f.read_text(encoding="utf-8")) for f in PUBLIC_FILES] + [("sample.json", SAMPLE_FILE)]:
        assert not re.search(LOGIN_ONLY, text, re.I), (name, re.findall(rf".{{30}}(?:{LOGIN_ONLY}).{{30}}", text, re.I)[:5])
        for wording in [*settings.PERIOD_IDS.values(), *settings.TERMINAL_WORDING.values()]:
            assert wording not in text, (name, wording)


def test_the_guard_sees_a_real_number():
    # The check below is only worth having if it can fail: each of these is a real v1 figure or date.
    for text in ("median premium +0.264 pp", "baseline 0.493%", f"data to {config.V1_END}", "187 events", "2023-06 to now"):
        assert real_in(text), text
    assert real_figures() and not real_in("S&P 500, 7 tutor questions, £12 a month, 250 questions")


def test_no_real_number_is_in_the_homepage_files(client):
    for name, text in (("/", client.get("/").text), ("/method", client.get("/method").text), ("home.js", JS), ("home_demo.js", DEMO)):
        assert real_in(text) == [], name


def test_the_sample_figures_are_not_real_ones():
    assert real_in(" ".join(sorted(DEMO_FIGURES))) == []


def test_no_median_from_the_api_is_on_the_homepage(client):
    page = client.get("/").text + DEMO
    sign_up(client)
    for period in ("both", EARLY, LATE):
        for s in client.get(f"/api/events?type=all&period={period}").json()["data"]["summaries"]:
            for k in ("median_premium_pp", "median_baseline_pct", "median_implied_move_pct", "median_actual_move_pct"):
                if s[k] is not None:
                    assert f"{s[k]:.3f}" not in page, (period, s["event_type"], k)


def test_no_homepage_file_has_internal_wording(client):
    for name, text in (("/", client.get("/").text), ("/method", client.get("/method").text), ("home_demo.js", DEMO)):
        text = re.sub(r"<!--.*?-->|<[^>]+>", " ", text, flags=re.S)         # the words, not the markup (aria-live)
        assert not re.search(INTERNAL, text, re.I), (name, re.findall(rf".{{20}}(?:{INTERNAL}).{{20}}", text, re.I))


def test_no_public_page_has_the_real_v1_date(client):
    for path in ("/", "/method", "/login", "/preview"):
        assert str(config.V1_END) not in client.get(path).text, path


# ---------------------------------------------------------------- the page's own figures
def test_the_prices_match_the_settings():
    pricing = HTML.split('id="pricing"')[1]
    assert f'<span class="num">{settings.TUTOR_DAILY_FREE}</span> tutor questions a day' in pricing
    assert f'£<span class="num">{settings.TUTOR_PLUS_GBP_MONTH}</span> a month' in pricing
    assert f'<span class="num">{settings.TUTOR_MONTHLY_PLUS}</span> tutor questions a month' in pricing


def test_demo_numbers_come_from_the_sample_file():
    for figure in DEMO_FIGURES:
        assert figure in DEMO, figure
    script = DEMO.split("export const DEMOS")[1]
    for tok in re.findall(NUMBER, script):
        assert tok in ALLOWED, tok


def test_the_page_makes_no_request_by_construction():
    for text in (JS, DEMO):
        assert not re.search(r"\bfetch\b|XMLHttpRequest|import\s*\(|WebSocket|sendBeacon", text)


# ---------------------------------------------------------------- in headless Edge
EXTRACT = r"""(() => {
  const txt = (e) => e.textContent.replace(/\s+/g, ' ').trim();
  const vis = (e) => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; };
  const sections = [...document.querySelectorAll('main > section[id], body > footer[id]')].map((e) => ({ id: e.id, text: txt(e).length, visible: vis(e) }));
  const cut = [...document.querySelectorAll('body *')].filter((e) => vis(e) && !e.closest('svg'))
    .filter((e) => e.getBoundingClientRect().right > innerWidth + 1).map((e) => e.tagName + '.' + e.className);
  const edu = [...document.querySelectorAll('.masthead-line span')][0].getBoundingClientRect();
  const steps = [...document.querySelectorAll('#demo-chat .step')];
  return { text: document.body.innerText, sections, cut: cut.slice(0, 10), scrollX: document.documentElement.scrollWidth > innerWidth,
           edu_visible: edu.top >= 0 && edu.bottom <= innerHeight,
           pricing: { plans_left: document.querySelector('.plans').getBoundingClientRect().left, plan_left: document.querySelector('.plan').getBoundingClientRect().left,
                      heading_left: document.querySelector('#pricing h2').getBoundingClientRect().left },
           chat_left: document.getElementById('demo-chat').getBoundingClientRect().left, controls_left: document.querySelector('.demo-controls').getBoundingClientRect().left,
           section_padding: parseFloat(getComputedStyle(document.getElementById('honest')).paddingTop),
           cards: [...document.querySelectorAll('#findings .card')].map(txt), tag: vis(document.querySelector('#demo .sample-tag')),
           demo: { done: document.getElementById('demo-chat').dataset.done, steps: steps.length, entering: steps.filter((s) => s.classList.contains('enter')).length,
                   transition: steps[0] ? getComputedStyle(steps[0]).transitionDuration : null, text: txt(document.getElementById('demo-chat')),
                   pressed: document.querySelector('.demo-pick[aria-pressed="true"]')?.dataset.demo },
           requests_all: performance.getEntriesByType('resource').map((e) => e.name),
           signed_in_visible: [...document.querySelectorAll('[data-signed-in]')].some(vis),
           signed_out_visible: [...document.querySelectorAll('[data-signed-out]')].some(vis),
           links: Object.fromEntries([...document.querySelectorAll('.actions a, .plan a')].filter(vis).map((a) => [txt(a), a.getAttribute('href')])) };
})()"""
READY = "document.body.dataset.ready === 'true'"
METHOD = r"""(() => ({ text: document.body.innerText, scrollX: document.documentElement.scrollWidth > innerWidth,
  columns: new Set([...document.querySelectorAll('.columns > div')].map((e) => e.getBoundingClientRect().left)).size,
  topics: [...document.querySelectorAll('.columns > div')].map((e) => [...e.querySelectorAll('h3')].map((x) => x.textContent)),
  labels: [...document.querySelectorAll('.kicker')].map((e) => e.textContent),
  heads: [...document.querySelectorAll('h1, h2')].map((e) => e.textContent), current: document.querySelector('nav a[aria-current]').getAttribute('href'),
  nav: [...document.querySelectorAll('nav a')].map((a) => a.getAttribute('href')) }))()"""
REDUCED = [{"name": "prefers-reduced-motion", "value": "reduce"}]


@pytest.fixture(scope="module")
def pages(browser):
    visits = {
        # Desktop, logged out: scroll to the demo, replay and switch questions, then read every request made.
        "desktop": dict(url="/", logged_out=True, wait=READY,
                        wait_after="document.getElementById('demo-chat').dataset.done === 'true'"
                                   " && document.querySelector('.demo-pick[aria-pressed=\"true\"]').dataset.demo === '1'",
                        actions=["document.getElementById('demo').scrollIntoView()",
                                 "document.getElementById('demo-replay').click()",
                                 "document.querySelector('.demo-pick[data-demo=\"1\"]').click()"],
                        extract=EXTRACT, timeout_s=60),
        "phone": dict(url="/", logged_out=True, width=390, height=844, mobile=True, media=REDUCED, wait=READY, extract=EXTRACT),
        "signed_in": dict(url="/", wait=READY, media=REDUCED, extract=EXTRACT),
        "method": dict(url="/method", logged_out=True, extract=METHOD),
        "method_phone": dict(url="/method", logged_out=True, width=390, height=844, mobile=True, extract=METHOD),
        "method_tablet": dict(url="/method", logged_out=True, width=900, height=800, extract=METHOD),
    }
    out = browser.visit(list(visits.values()))
    return {k: r for k, r in zip(visits, out)}


def test_pages_render_without_errors(pages):
    for k, r in pages.items():
        assert r["ready"] and r["error"] is None and r["errors"] == [], (k, r["error"], r["errors"])


def test_the_six_sections_render_in_order(pages):
    for k in ("desktop", "phone"):
        secs = pages[k]["value"]["sections"]
        assert [s["id"] for s in secs] == SECTIONS
        assert all(s["visible"] and s["text"] > 40 for s in secs), secs
    for k in ("phone", "signed_in"):                                  # not scrolled (the desktop visit scrolls to the demo)
        assert pages[k]["value"]["edu_visible"]                       # "Education only, not advice" without scrolling


def test_no_real_number_is_on_the_rendered_page(pages):
    for k in ("desktop", "phone", "signed_in"):
        text = pages[k]["value"]["text"]
        assert real_in(text) == [], k
        assert not re.search(INTERNAL, text, re.I), k
        for tok in re.findall(NUMBER, text):                          # every number on the page is a known, invented or public one
            assert tok.replace("£", "") in ALLOWED, (k, tok)


def test_no_real_number_is_on_the_rendered_method_page(pages):
    for k in ("method", "method_phone"):
        v = pages[k]["value"]
        assert real_in(v["text"]) == [] and not re.search(INTERNAL, v["text"], re.I), k
        assert set(re.findall(NUMBER, v["text"])) == {"500"}, k       # the index's name, and nothing else
        assert not v["scrollX"], k
    v = pages["method"]["value"]
    assert v["heads"] == ["The full method", "How the numbers are made", "What we don't claim"] and v["current"] == "/method"
    assert v["nav"] == ["/#tour", "/method", "/#findings", "/#pricing", "/terminal"]


def test_the_method_topics_are_columns_that_narrow_to_a_stack(pages):
    assert pages["method"]["value"]["topics"] == [["Option chains"], ["Implied and actual move"], ["Baselines"], ["Flagged days"]]
    assert {k: pages[k]["value"]["columns"] for k in ("method", "method_tablet", "method_phone")} == {"method": 4, "method_tablet": 2, "method_phone": 1}
    assert pages["method"]["value"]["labels"] == ["Method", "Data and method", "Limits"]


def test_the_demo_chat_starts_at_the_buttons_left_edge(pages):
    for k in ("desktop", "phone"):
        assert pages[k]["value"]["chat_left"] == pages[k]["value"]["controls_left"], k


def test_the_findings_are_words_with_a_link_to_the_numbers(pages):
    v = pages["desktop"]["value"]
    assert len(v["cards"]) == 2 and "held in both periods" in v["cards"][0]
    assert all(not re.search(r"\d", c) for c in v["cards"]), v["cards"]
    assert v["links"]["See the numbers in the terminal"] == "/terminal"


def test_the_page_calls_nothing_but_its_own_files_and_the_preview(pages):
    v = pages["desktop"]["value"]
    calls = [u for u in v["requests_all"] if "/static/" not in u and not u.endswith("/preview")]
    assert calls == [], calls
    assert sum(u.endswith("/preview") for u in v["requests_all"]) <= 1


def test_the_demo_is_labelled_as_sample_data(pages):
    v = pages["desktop"]["value"]
    assert v["demo"]["done"] == "true" and v["demo"]["pressed"] == "1" and v["demo"]["steps"] == 5 and v["tag"]
    assert "+0.228 pp" in v["demo"]["text"] and "sample data:" in v["demo"]["text"] and "Sample data: invented numbers, not results." in v["demo"]["text"]


def test_reduced_motion_shows_the_demo_at_once(pages):
    d = pages["phone"]["value"]["demo"]
    assert d["done"] == "true" and d["steps"] == 5 and d["entering"] == 0 and d["transition"] == "0s"


def test_phone_width(pages):
    v = pages["phone"]["value"]
    assert not v["scrollX"] and v["cut"] == [], v["cut"]


def test_pricing_lines_up_with_the_headings_and_the_sections_are_tighter(pages):
    for k, most in (("desktop", 75), ("phone", 43)):                  # a third less than the old 112 and 64
        v = pages[k]["value"]
        assert v["pricing"]["plan_left"] == v["pricing"]["heading_left"], (k, v["pricing"])
        assert v["section_padding"] <= most, (k, v["section_padding"])


def test_links_and_signed_in_state(pages):
    out, signed_in = pages["desktop"]["value"], pages["signed_in"]["value"]
    assert out["links"]["Create a free account"] == "/login?mode=signup" and out["links"]["Open the terminal"] == "/terminal"
    assert out["links"]["Sign up"] == "/login?mode=signup" and out["links"]["Open the real terminal"] == "/terminal"
    assert out["signed_out_visible"] and not out["signed_in_visible"]
    assert signed_in["signed_in_visible"] and not signed_in["signed_out_visible"]
