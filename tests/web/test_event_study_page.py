"""Page 1 in headless Edge: the numbers the page displays match stage3_periods.txt and findings_v1.md for
every type and period; every figure and headline shows n and a source; the payrolls premium is shown as
not holding only in the findings' own words; the next-event panel; Split view and phone width."""
from tests.private_files import require

require("database", "stage3 summary", "findings")
import json
import re
import sys

import pytest

import config
from web import settings

sys.path.insert(0, str(config.PROJECT_DIR / "tests"))
from test_events import PERIODS, S3  # noqa: E402

TYPES = ["CPI", "NFP", "FOMC", "all"]
PERIOD_ID = {real: neutral for neutral, real in settings.PERIOD_IDS.items()}
NAMES = {"CPI": "CPI", "Payrolls (NFP)": "NFP", "FOMC": "FOMC"}
READY = ("(() => { const p = document.querySelector('#pane-a .event-study');"
         " return !!p && !!p.querySelector('.headline') && p.querySelectorAll('.plot .main-svg').length >= 2; })()")
EXTRACT = r"""(() => {
  const pane = document.querySelector('#pane-a');
  const txt = (e) => (e ? e.textContent.replace(/\s+/g, ' ').trim() : null);
  const head = [...pane.querySelectorAll('.headline thead th')].map(txt);
  const rows = [...pane.querySelectorAll('.headline tbody tr')].map((tr) => [...tr.children].map(txt));
  return {
    head, rows,
    headline_source: txt(pane.querySelector('.headline .source')),
    figcaptions: [...pane.querySelectorAll('figure.chart figcaption')].map(txt),
    figure_sources: [...pane.querySelectorAll('figure.chart .source')].map(txt),
    plots: pane.querySelectorAll('.plot .main-svg').length,
    table_title: txt(pane.querySelector('#table-title')),
    table_rows: pane.querySelectorAll('table.events tbody tr').length,
    table_source: txt(pane.querySelector('table.events')?.closest('section').querySelector('.source')),
    first_link: pane.querySelector('table.events tbody tr a')?.getAttribute('href'),
    first_date: txt(pane.querySelector('table.events tbody tr th')),
    note: txt(pane.querySelector('section .note')),
    quotes: [...pane.querySelectorAll('.caveats blockquote')].map((q) => [...q.querySelectorAll('p')].map(txt)),
    quote_refs: [...pane.querySelectorAll('.caveats blockquote footer')].map(txt),
    caveats: txt(pane.querySelector('.caveats')),
    next: txt(pane.querySelector('.next-event')),
    next_source: txt(pane.querySelector('.next-event .source')),
    definitions: [...pane.querySelectorAll('dl.defs dt')].map(txt),
    page_text: txt(pane),
  };
})()"""


def num(s):
    return float(s.replace("+", "").replace("−", "-"))


@pytest.fixture(scope="module")
def pages(browser):
    visits = [dict(url=f"/terminal?a=event-study&a.type={t}&a.period={PERIOD_ID.get(p, p)}&split=0&tutor=0", wait=READY, extract=EXTRACT)
              for t in TYPES for p in PERIODS]
    out = browser.visit(visits)
    return {(t, p): r for (t, p), r in zip([(t, p) for t in TYPES for p in PERIODS], out)}


def test_every_page_rendered_without_errors(pages):
    for key, r in pages.items():
        assert r["ready"] and r["error"] is None, (key, r["error"])
        assert r["errors"] == [], (key, r["errors"])


@pytest.mark.parametrize("t", TYPES)
@pytest.mark.parametrize("p", PERIODS)
def test_headline_numbers_match_stage3(pages, t, p):
    v = pages[(t, p)]["value"]
    cols = [NAMES[h] for h in v["head"][1:]]
    assert cols == (["CPI", "NFP", "FOMC"] if t == "all" else [t])       # side by side, no pooled column
    labels = [r[0] for r in v["rows"]]
    want = {"Median implied move (%)": "median_implied_move_pct", "Median baseline, ordinary days nearby (%)": "median_baseline_pct",
            "Median premium (pp)": "median_premium_pp", "Median variance premium (%²)": "median_variance_premium_pct2",
            "n (events)": "n"}
    assert labels == list(want)
    for row in v["rows"]:
        for col, cell in zip(cols, row[1:]):
            assert num(cell) == pytest.approx(S3[col][want[row[0]]][PERIODS[p]], abs=1e-9), (t, p, row[0], col)


def test_premiums_and_counts_match_findings(pages):
    text = (config.PROJECT_DIR / "data" / "findings_v1.md").read_text(encoding="utf-8")
    for t in ("CPI", "NFP", "FOMC"):
        m = re.search(rf"^\| {t} \| (\d+) / (\d+) / (\d+) \| ([+-][\d.]+) / ([+-][\d.]+) / ([+-][\d.]+) \|", text, re.M)
        for p, i in PERIODS.items():
            rows = {r[0]: r[1] for r in pages[(t, p)]["value"]["rows"]}
            assert rows["Median premium (pp)"] == m.group(4 + i), (t, p)        # same text, sign included
            assert int(rows["n (events)"]) == int(m.group(1 + i))


def test_every_figure_and_headline_shows_n_and_source(pages):
    for key, r in pages.items():
        v = r["value"]
        assert "event_moves" in v["headline_source"] and "event_premium" in v["headline_source"]
        assert len(v["figcaptions"]) == 2 and all(re.search(r"\bn \d+", c) for c in v["figcaptions"]), key
        assert len(v["figure_sources"]) == 2 and v["plots"] >= 2
        assert re.search(r"\(n \d+\)", v["table_title"]) and "event_moves" in v["table_source"]
        assert int(re.search(r"n (\d+)", v["table_title"]).group(1)) == v["table_rows"]
        assert "event_calendar.csv" in v["next_source"]


def test_table_newest_first_and_links_to_vol_surface(pages):
    from api import db, events
    newest = events.list_events(db.cursor(), "CPI", "both")["data"]["events"][0]        # v1 or live, the latest CPI
    v = pages[("CPI", "both")]["value"]
    assert v["first_date"].split()[0] == newest["date"]
    assert v["first_link"].startswith("/terminal?") and "vol-surface" in v["first_link"]
    assert f"d1={newest['trade_date']}" in v["first_link"] and f"d2={newest['date']}" in v["first_link"]


def test_left_out_days_note(pages):
    assert pages[("all", "both")]["value"]["note"].startswith("15 event days in this selection are left out (14 CPI, 1 FOMC)")
    assert pages[("NFP", "both")]["value"]["note"] == "No event days in this selection are left out."


def test_held_or_not_only_in_the_findings_words(pages):
    nfp = pages[("NFP", "both")]["value"]
    assert "Survived the controls? No. The premium does not hold in the out-of-sample period." in sum(nfp["quotes"], [])
    assert nfp["quote_refs"] == ["findings_v1.md, Section 1, Finding 1.3"]
    for t in ("CPI", "FOMC"):
        v = pages[(t, "both")]["value"]
        assert "Survived the controls? Yes, in both periods and with the crash excluded." in sum(v["quotes"], [])
        assert v["quote_refs"] == ["findings_v1.md, Section 1, Finding 1.2"]
        assert "does not hold" not in v["page_text"]
    # Every quoted paragraph is word for word in findings_v1.md (markdown marks and line breaks aside).
    findings = re.sub(r"\s+", " ", (config.PROJECT_DIR / "data" / "findings_v1.md").read_text(encoding="utf-8")
                      .replace("**", "").replace("`", ""))
    quotes = sum(pages[("all", "both")]["value"]["quotes"], [])
    assert len(quotes) == 6
    for paragraph in quotes:
        assert paragraph in findings, paragraph


def test_next_event_panel(pages):
    from api import db, next_event
    v = pages[("all", "both")]["value"]["next"]
    api = next_event.next_event(db.cursor(), "all")["data"]
    s = api["soonest"]
    assert s and s["date"] in json.dumps(api, default=str)
    assert all(t in v for t in s["event_types"])
    assert "History, not a forecast. Education only, not advice." in v
    assert "Priced now" in v and ("not priced yet" in v or "implied move" in v)
    assert " will " not in f" {v} ".lower() and "expect" not in v.lower()
    assert "In the past" in v and "After the event, in the past" in v and "Survives both controls" in v


def test_definitions_shown(pages):
    assert pages[("CPI", "both")]["value"]["definitions"] == ["Implied move", "Actual move", "Baseline", "Premium", "Variance premium"]


def test_split_view_and_phone_width(browser):
    both = ("(() => [...document.querySelectorAll('.event-study')].length === 2 && "
            "document.querySelectorAll('.event-study .plot .main-svg').length >= 4)()")
    layout = r"""(() => {
      const a = document.querySelector('#pane-a').getBoundingClientRect(), b = document.querySelector('#pane-b').getBoundingClientRect();
      const plots = [...document.querySelectorAll('.event-study .plot')].map((p) => p.getBoundingClientRect().width);
      const panes = [...document.querySelectorAll('.pane')].filter((p) => !p.classList.contains('hidden'));
      return { side: Math.abs(a.top - b.top) < 2 && b.left > a.left, stacked: b.top >= a.bottom - 1,
               page_scroll_x: document.documentElement.scrollWidth > innerWidth,
               plots_fit: plots.every((w) => w > 150 && w <= innerWidth),
               content_fits: panes.every((p) => p.scrollWidth <= p.clientWidth + 1 || getComputedStyle(p).overflowX !== 'visible'),
               heads: [...document.querySelectorAll('.headline thead th')].map((t) => t.textContent.trim()) };
    })()"""
    url = "/terminal?a=event-study&a.type=CPI&a.period=both&b=event-study&b.type=all&b.period=earlier&split=1&tutor=0"
    desk, phone = browser.visit([dict(url=url, wait=both, extract=layout),
                                 dict(url=url, width=390, height=844, mobile=True, wait=both, extract=layout)])
    for r in (desk, phone):
        assert r["ready"] and r["error"] is None and r["errors"] == [], r
    assert desk["value"]["side"] and not desk["value"]["page_scroll_x"] and desk["value"]["plots_fit"]
    assert phone["value"]["stacked"] and not phone["value"]["page_scroll_x"] and phone["value"]["plots_fit"]
    assert desk["value"]["heads"] == ["", "CPI", "", "CPI", "Payrolls (NFP)", "FOMC"]     # each pane keeps its own filters
