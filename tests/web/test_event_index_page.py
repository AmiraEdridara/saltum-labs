"""Page 3 in headless Edge: the summary matches data/stage5b_summary.txt exactly; filters change the summary
and the table; a missing-day event shows its reason; sorting; rows open the Vol surface page; a visible error
when the API fails; Split view and phone width; no browser processes left."""
from tests.private_files import require

require("database", "stage5b summary")
import re
import subprocess

import pytest

import config

TXT = (config.PROJECT_DIR / "data" / "stage5b_summary.txt").read_text(encoding="utf-8")
ROW = re.compile(r"^\s*(30-day .+?)\s+(ordinary pairs|CPI|NFP|FOMC)\s+(\d+)\s+([+-]\d+\.\d{3})\s+(\d+%)\s+(\S+)\s+(–|\[.+\])$")
SURV = re.compile(r"^\s*(30-day .+?)\s+(CPI|NFP|FOMC)\s+(True|False)\s+(True|False)\s+(True|False)$")
READY = ("(() => { const p = document.querySelector('#pane-a .event-index');"
         " return !!p && (!!p.querySelector('table.index') || !!p.querySelector('.table-failed')); })()")
EXTRACT = r"""(() => {
  const pane = document.querySelector('#pane-a');
  const txt = (e) => (e ? e.textContent.replace(/\s+/g, ' ').trim() : null);
  let measure = null;
  const summary = [...pane.querySelectorAll('table.summary tbody tr')].map((tr) => {
    const c = [...tr.children].map(txt);
    measure = c[0] || measure;
    return [tr.dataset.measure, c[1], c[2], c[3], c[4], c[5], c[6], c[7]];
  });
  const rows = [...pane.querySelectorAll('table.index tbody tr')].map((tr) => ({ date: tr.dataset.date, type: tr.dataset.type,
    cells: [...tr.children].map(txt), missing: tr.classList.contains('missing'),
    link: tr.querySelector('a')?.getAttribute('href') || null }));
  return { url: location.search, summary, rows, note: txt(pane.querySelector('.summary-note')),
           sorted: txt(pane.querySelector('th[aria-sort]:not([aria-sort="none"])')),
           title: txt(pane.querySelector('#ix-table-title')), caveat: txt(pane.querySelector('.caveats p')),
           errors: [...pane.querySelectorAll('.table-failed')].map(txt), page_scroll_x: document.documentElement.scrollWidth > innerWidth };
})()"""
BENIGN = ("willReadFrequently",)


def frozen(label):
    block = TXT.split(f"######## {label} ########")[1].split("Spot-vol")[0]
    return [m.groups() for m in map(ROW.match, block.splitlines()) if m]


SURVIVAL = {(m, g): s for m, g, _, _, s in (x.groups() for x in map(SURV.match, TXT.split("Survives both controls")[1].splitlines()) if x)}


def url(**kw):
    return "/terminal?a=event-index" + "".join(f"&a.{k}={v}" for k, v in kw.items()) + "&split=0&tutor=0"


@pytest.fixture(scope="module")
def pages(browser):
    cases = {"all": url(), "flagged_out": url(exclude_flagged="true"), "cpi": url(type="CPI"),
             "early": url(period="earlier"), "ordinary": url(include_ordinary="true")}
    out = browser.visit([dict(url=u, wait=READY, extract=EXTRACT) for u in cases.values()])
    return dict(zip(cases, out))


def errs(r):
    return [e for e in r["errors"] if not any(b in e for b in BENIGN)]


def test_rendered_without_errors(pages):
    for k, r in pages.items():
        assert r["ready"] and r["error"] is None and errs(r) == [], (k, r["error"], errs(r))
        assert r["value"]["errors"] == [], k


@pytest.mark.parametrize("case, label", [("all", "ALL"), ("flagged_out", "EXCLUDING FLAGGED")])
def test_summary_matches_stage5b_exactly(pages, case, label):
    shown = [tuple(r[:7]) for r in pages[case]["value"]["summary"]]
    assert len(shown) == 16 and shown == frozen(label)


def test_survives_column(pages):
    for m, g, *_, survives in pages["all"]["value"]["summary"]:
        want = SURVIVAL.get((m, g))
        assert survives == ("not tested" if want is None else "Yes" if want == "True" else "No"), (m, g)


def test_filters_change_summary_and_table(pages):
    all_, cpi, out, early, ordinary = (pages[k]["value"] for k in ("all", "cpi", "flagged_out", "early", "ordinary"))
    # Type: summary keeps the ordinary pairs and CPI only; the table only CPI events (CPI+FOMC counts as CPI).
    assert {r[1] for r in cpi["summary"]} == {"ordinary pairs", "CPI"}
    assert [tuple(r[:7]) for r in cpi["summary"]] == [r for r in frozen("ALL") if r[1] in ("ordinary pairs", "CPI")]
    assert cpi["rows"] and all("CPI" in r["type"] for r in cpi["rows"])
    # Flagged: the other frozen table, and no flagged rows.
    assert out["summary"] != all_["summary"]
    assert not any(r["cells"][3] for r in out["rows"] if not r["missing"])
    assert len([r for r in out["rows"] if not r["missing"] and "live" not in r["cells"][0]]) == 143
    # A single period: rows of that period only, summary empty with the API's note.
    assert early["summary"] == [] and "pools both periods" in early["note"]
    assert all(r["date"] < "2023-06-01" for r in early["rows"])
    # Ordinary pairs: 100 more rows, summary unchanged.
    assert ordinary["summary"] == all_["summary"]
    assert len([r for r in ordinary["rows"] if r["type"] == "ordinary"]) == 100
    assert len(ordinary["rows"]) == len(all_["rows"]) + 100
    n = int(all_["title"].split("n ")[1].split(")")[0].split(":")[0])
    assert n >= 205 and ordinary["title"] == f"Event table (n {n + 100}: {n} events, 100 ordinary pairs)"
    assert len([r for r in all_["rows"] if not r["missing"] and "live" not in r["cells"][0]]) == 205   # the frozen rows


def test_missing_day_event_shows_its_reason(pages):
    rows = {(r["date"], r["type"]): r for r in pages["all"]["value"]["rows"]}
    r = rows[("2022-04-12", "CPI")]
    assert r["missing"] and r["cells"][2] == "1 of 2"
    assert r["cells"][3] == ("Missing day: 2022-04-12: fetch failed: SanityCheckFailed: 2022-04-12: expiries within 14 days "
                             "missing from the response: 2022-04-26")
    both = rows[("2022-05-06", "NFP")]
    assert both["cells"][2] == "0 of 2" and "2022-05-05" in both["cells"][3] and "2022-05-06" in both["cells"][3]
    assert r["link"] is None


def test_flags_say_which_day(pages):
    rows = {(r["date"], r["type"]): r for r in pages["all"]["value"]["rows"]}
    assert rows[("2020-03-11", "CPI")]["cells"][3] == "⚑ day before, event day"
    assert rows[("2025-04-10", "CPI")]["cells"][3] == "⚑ day before"
    assert rows[("2026-09-11", "CPI")]["cells"][3] == ""


def test_newest_first_and_numbers(pages):
    from api import db, index
    rows = pages["all"]["value"]["rows"]
    api = {r["event_date"]: r for r in index.get_index(db.cursor(), "all", "both", "false", "false")["data"]["events"]}
    assert rows[0]["date"] == max(api) and "▼" in pages["all"]["value"]["sorted"]
    r = next(x for x in rows if x["date"] == "2026-09-11")
    a = api["2026-09-11"]
    assert r["cells"][4:11] == [f"{a['atm30_before']:.3f}", f"{a['atm30_after']:.3f}", f"{a['d_atm30_fixed_strike']:+.3f}",
                                f"{a['d_rr25_30']:+.3f}", f"{a['implied_move_pct']:.3f}", f"{a['actual_move_pct']:.3f}",
                                f"{a['premium_pp']:+.3f}"]
    assert "a.d1=2026-09-10" in r["link"] and "a.d2=2026-09-11" in r["link"] and "vol-surface" in r["link"]


def test_sorting_and_opening_a_row(browser):
    first = "(() => [...document.querySelectorAll('#pane-a table.index tbody tr')].slice(0, 3).map((t) => t.dataset.date + ' ' + t.children[10].textContent))()"
    one_click, two_clicks, opened, split = browser.visit([
        dict(url=url(), wait=READY, extract=first, actions=["document.querySelector('#pane-a button[data-sort=\"premium_pp\"]').click()"]),
        dict(url=url(), wait=READY, extract=first, actions=["document.querySelector('#pane-a button[data-sort=\"premium_pp\"]').click()",
                                                             "document.querySelector('#pane-a button[data-sort=\"premium_pp\"]').click()"]),
        dict(url=url(), wait=READY, extract="location.search",
             actions=["document.querySelector('#pane-a tr[data-date=\"2026-09-11\"] td[data-key=\"event_type\"]').click()"]),
        dict(url="/terminal?a=event-index&b=event-study&split=1&tutor=0&active=a", wait=READY, extract="location.search",
             actions=["document.querySelector('#pane-a tr[data-date=\"2026-09-11\"] td[data-key=\"event_type\"]').click()"]),
    ])
    hi = [float(x.split()[1]) for x in one_click["value"]]    # first click: largest premium first
    lo = [float(x.split()[1]) for x in two_clicks["value"]]   # second click: smallest first
    assert hi == sorted(hi, reverse=True) and lo == sorted(lo) and hi[0] > 1 and lo[0] < 0
    assert "a=vol-surface" in opened["value"] and "a.d1=2026-09-10" in opened["value"] and "a.d2=2026-09-11" in opened["value"]
    s = split["value"]                                          # Split view: opens in the other pane, the index stays
    assert "a=event-index" in s and "b=vol-surface" in s and "b.d1=2026-09-10" in s and "b.d2=2026-09-11" in s


def test_visible_error_when_the_api_fails(browser):
    (r,) = browser.visit([dict(url=url(), wait=READY, extract=EXTRACT)], block=["*/api/events/index*"])
    assert r["ready"]
    msgs = r["value"]["errors"]
    assert len(msgs) == 2 and msgs[0].startswith("The summary could not be shown") and msgs[1].startswith("The event table could not be shown")


def test_split_view_and_phone_width(browser):
    both = ("(() => document.querySelectorAll('.event-index table.index').length === 2)()")
    lay = r"""(() => ({ page_scroll_x: document.documentElement.scrollWidth > innerWidth,
      panes: [...document.querySelectorAll('.event-index')].map((p) => { const r = p.getBoundingClientRect(); return [Math.round(r.left), Math.round(r.top), Math.round(r.width)]; }),
      filters_fit: [...document.querySelectorAll('.pane:not(.hidden) .controls-row select')].every((e) => e.getBoundingClientRect().right <= innerWidth + 1) }))()"""
    u = "/terminal?a=event-index&a.type=CPI&b=event-index&b.include_ordinary=true&split=1&tutor=0"
    desk, phone = browser.visit([dict(url=u, wait=both, extract=lay), dict(url=u, width=390, height=844, mobile=True, wait=both, extract=lay)])
    for r in (desk, phone):
        assert r["ready"] and errs(r) == [], errs(r)
        assert not r["value"]["page_scroll_x"] and r["value"]["filters_fit"]
    (a, b) = desk["value"]["panes"]
    assert abs(a[1] - b[1]) < 2 and b[0] > a[0]                 # side by side
    (a, b) = phone["value"]["panes"]
    assert b[1] > a[1] and abs(a[0] - b[0]) < 2                 # stacked


def test_no_browser_processes_left(browser):
    ps = ("(Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" | Where-Object { $_.CommandLine -like "
          f"'*{browser.tmp}*' }}).Count")
    out = subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=60)
    assert out.stdout.strip() in ("", "0"), out.stdout
