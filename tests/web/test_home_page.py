"""Page 6, the homepage: a public page with no real numbers. GET / for everyone, no data route and no data request, the
nine sections, a tutor demo on sample figures, findings in words, no internal wording, phone width, reduced motion. The
terminal preview in section 2 is tested in test_preview.py."""
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
SECTIONS = ["hero", "tour", "demo", "method", "honest", "findings", "next", "pricing", "footer"]
INTERNAL = r"frozen|findings_v1|findings v1|\bv1\b|\blive\b|Stage \d|local version|local edition|on this computer|ThetaData|FRED|provisional"
NUMBER = r"(?<![\w.:])[+−-]?\d[\d,]*(?:[.:]\d+)?%?"


def sample_premium(period, t):
    s = SAMPLE["summaries"][period][t]
    return f"+{s['median_premium_pp']:.3f}", s["n"]


EARLY, LATE = "2020-01..2023-05", "2023-06..now"
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
    return sorted(f for f in real_figures() if re.search(rf"(?<![\d.]){re.escape(f)}(?![\d])", text))


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


def test_security_policy_is_unchanged(client):
    assert client.get("/").headers["content-security-policy"] == client.get("/login").headers["content-security-policy"]
    assert "script-src" not in client.get("/").headers["content-security-policy"]       # 'self' only, via default-src


# ---------------------------------------------------------------- no real numbers
def test_the_guard_sees_a_real_number():
    # The check below is only worth having if it can fail: each of these is a real v1 figure or date.
    for text in ("median premium +0.264 pp", "baseline 0.493%", f"data to {config.V1_END}", "187 events", "2023-06 to now"):
        assert real_in(text), text
    assert real_figures() and not real_in("S&P 500, 7 tutor questions, £12 a month, 250 questions")


def test_no_real_number_is_in_the_homepage_files(client):
    for name, text in (("/", client.get("/").text), ("home.js", JS), ("home_demo.js", DEMO)):
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
    for name, text in (("/", client.get("/").text), ("home_demo.js", DEMO)):
        text = re.sub(r"<!--.*?-->|<[^>]+>", " ", text, flags=re.S)         # the words, not the markup (aria-live)
        assert not re.search(INTERNAL, text, re.I), (name, re.findall(rf".{{20}}(?:{INTERNAL}).{{20}}", text, re.I))


def test_no_public_page_has_the_real_v1_date(client):
    for path in ("/", "/login", "/preview"):
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
    }
    out = browser.visit(list(visits.values()))
    return {k: r for k, r in zip(visits, out)}


def test_pages_render_without_errors(pages):
    for k, r in pages.items():
        assert r["ready"] and r["error"] is None and r["errors"] == [], (k, r["error"], r["errors"])


def test_the_nine_sections_render_in_order(pages):
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


def test_links_and_signed_in_state(pages):
    out, signed_in = pages["desktop"]["value"], pages["signed_in"]["value"]
    assert out["links"]["Create a free account"] == "/login?mode=signup" and out["links"]["Open the terminal"] == "/terminal"
    assert out["links"]["Sign up"] == "/login?mode=signup" and out["links"]["Open the real terminal"] == "/terminal"
    assert out["signed_out_visible"] and not out["signed_in_visible"]
    assert signed_in["signed_in_visible"] and not signed_in["signed_out_visible"]
