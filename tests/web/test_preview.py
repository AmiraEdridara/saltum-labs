"""The homepage's terminal preview: /preview is the real terminal page on a fixed sample file. It is public, makes no
call to the API, the tutor or an account, holds no real numbers and no data-provider credit, and works at phone width."""
import json
import re

import pytest

import config
from web import settings
from webutil import sign_up

STATIC = config.PROJECT_DIR / "web" / "static"
SAMPLE = json.loads((STATIC / "preview" / "sample.json").read_text(encoding="utf-8"))
DAY_BEFORE, CPI_DAY = "2026-06-19", "2026-06-22"
PUBLIC_FILES = [STATIC / "terminal.html", STATIC / "home.html", *(STATIC / "js").rglob("*.js"), STATIC / "preview" / "sample.json"]


# ---------------------------------------------------------------- the route
def test_preview_is_public_and_not_cached(client):
    r = client.get("/preview")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store" and 'id="nav-list"' in r.text


def test_only_the_preview_may_be_framed_and_only_by_this_site(client):
    home, preview = (client.get(p).headers["content-security-policy"] for p in ("/", "/preview"))
    assert "frame-ancestors 'none'" in home and preview == home.replace("frame-ancestors 'none'", "frame-ancestors 'self'")
    sign_up(client)
    assert "frame-ancestors 'none'" in client.get("/terminal").headers["content-security-policy"]


def test_no_public_file_names_the_data_provider_or_the_real_v1_date(client):
    for f in PUBLIC_FILES:
        text = f.read_text(encoding="utf-8")
        assert "ThetaData" not in text, f
    for text in (client.get("/preview").text, (STATIC / "preview" / "sample.json").read_text(encoding="utf-8")):
        assert str(config.V1_END) not in text


def test_the_credit_comes_with_the_login(client):
    assert client.get("/account/me").status_code == 401
    sign_up(client)
    me = client.get("/account/me").json()
    assert me["footer"] == list(settings.TERMINAL_FOOTER) and "ThetaData" in settings.TERMINAL_FOOTER[0]
    assert me["wording"] == settings.TERMINAL_WORDING and me["wording"]["later"] == "2023-06 to now"


# ---------------------------------------------------------------- the sample file
def same_keys(sample, real, where=""):
    """Dicts have the same keys, lists are compared on their first rows; a null on either side says nothing."""
    if sample is None or real is None:
        return
    if isinstance(real, dict):
        assert isinstance(sample, dict) and set(sample) == set(real), (where, set(sample) ^ set(real))
        for k in real:
            same_keys(sample[k], real[k], f"{where}.{k}")
    elif isinstance(real, list) and real and sample:
        same_keys(sample[0], real[0], f"{where}[0]")


@pytest.fixture()
def api(client):
    sign_up(client)
    return lambda path: client.get("/api" + path).json()


def test_the_sample_has_the_shape_of_the_api(client, api):
    real_dates = api("/dates")
    same_keys(SAMPLE["dates"], real_dates)
    same_keys(SAMPLE["findings"], api("/findings"))
    next_real = api("/events/next")
    by_type = ("by_type", "in_the_past", "after_the_event")                 # the sample has only CPI in these
    blank = lambda r: {**r, "data": {**r["data"], "soonest": None, **dict.fromkeys(by_type)}}
    same_keys(blank(SAMPLE["next"]), blank(next_real))
    for k in by_type:
        same_keys(SAMPLE["next"]["data"][k]["CPI"], next_real["data"][k]["CPI"], k)
    events = api("/events?type=all&period=both")
    same_keys(SAMPLE["events"], {**events, "data": {**events["data"], "summaries": []}})
    same_keys(SAMPLE["events"]["data"]["summaries"][0], events["data"]["summaries"][0])
    same_keys(SAMPLE["index"], api("/events/index"))
    day = [d for d in real_dates["data"]["dates"] if d["events"]][-1]["date"]
    surface = api(f"/surface/{day}")
    for date in SAMPLE["surface"]:
        same_keys(SAMPLE["surface"][date], surface)
    same_keys(next(iter(SAMPLE["skew"].values())), api(f"/events/{day}/skew"))
    assert set(SAMPLE["me"]) - {"email", "plan", "tutor_day", "tutor_msgs_today", "tutor_msgs_month", "provider", "layout", "limits", "footer"} == set()


def test_the_sample_is_one_example():
    dates = {d["date"]: d for d in SAMPLE["dates"]["data"]["dates"]}
    assert list(dates) == [DAY_BEFORE, CPI_DAY] and SAMPLE["dates"]["data"]["data_to"] == CPI_DAY and SAMPLE["dates"]["v1_end"] < CPI_DAY
    assert dates[DAY_BEFORE]["events_next_day"] == "CPI" and dates[CPI_DAY]["events"] == "CPI"
    rows = SAMPLE["index"]["data"]["events"]
    assert len(rows) == SAMPLE["index"]["n"] == 1 and (rows[0]["event_type"], rows[0]["day_before"], rows[0]["event_date"]) == ("CPI", DAY_BEFORE, CPI_DAY)
    assert set(SAMPLE["surface"]) == {DAY_BEFORE, CPI_DAY} and set(SAMPLE["skew"]) == {CPI_DAY}
    study = SAMPLE["events"]
    assert study["period"] == "later" and [s["event_type"] for s in study["data"]["summaries"]] == ["CPI"]
    assert {r["event_type"] for r in study["data"]["events"]} == {"CPI"} and CPI_DAY in {r["date"] for r in study["data"]["events"]}
    assert [r["period"] for r in SAMPLE["next"]["data"]["in_the_past"]["CPI"]] == ["later"]
    assert len(SAMPLE["tutor"]["session"]["messages"]) == 2
    assert (STATIC / "preview" / "sample.json").stat().st_size < 50_000


def test_the_sample_holds_no_real_event_or_median(api):
    real = api("/events?type=all&period=both")["data"]
    invented = {(r["date"], r["event_type"]) for r in SAMPLE["events"]["data"]["events"]}
    assert not invented & {(r["date"], r["event_type"]) for r in real["events"]}
    summ = {s["event_type"]: s for s in api("/events?type=all&period=later")["data"]["summaries"]}
    for s in SAMPLE["events"]["data"]["summaries"]:
        for k in ("median_implied_move_pct", "median_actual_move_pct", "median_premium_pp", "median_baseline_pct"):
            assert round(s[k], 3) != round(summ[s["event_type"]][k], 3), k


# ---------------------------------------------------------------- in headless Edge, inside the homepage's frame
FRAME = "document.querySelector('.window iframe')"
LOADED = (f"document.body.dataset.ready === 'true' && (document.querySelector('.window').scrollIntoView(), true)"
          f" && !!{FRAME}.contentDocument?.querySelector('.next-event')")
CLICK = f"{FRAME}.contentDocument.querySelectorAll('#nav-list a')[%d].click()"
EXTRACT = f"""(() => {{
  const f = {FRAME}, w = f.contentWindow, d = f.contentDocument, css = (e) => w.getComputedStyle(e);
  const txt = (e) => e.innerText.replace(/\\s+/g, ' ').trim();
  const pane = d.getElementById('pane-a');
  return {{ top: txt(d.querySelector('.topbar')), foot: txt(d.querySelector('.footer')), title: d.title, path: w.location.pathname,
           nav: [...d.querySelectorAll('#nav-list a')].map((a) => a.firstChild.textContent),
           current: d.querySelector('#nav-list a[aria-current]').firstChild.textContent,
           controls: [...d.querySelectorAll('.nav .controls button')].filter((b) => b.getClientRects().length).map((b) => b.firstChild.textContent.trim()),
           selects: [...pane.querySelectorAll('select')].filter((s) => !s.closest('.vs-numbers, details')).map((s) => [s.disabled, s.options.length, s.hasAttribute('data-locked')]),
           picker_buttons: [...pane.querySelectorAll('.vs-picker button')].map((b) => [b.disabled, b.hasAttribute('data-locked')]),
           event_rows: pane.querySelectorAll('table.events tbody tr').length,
           open_links: [...pane.querySelectorAll('table.events a')].length,
           more: (pane.innerText.match(/More in the full terminal/g) || []).length,
           tag_visible: d.getElementById('sample-tag').getClientRects().length > 0,
           account_visible: d.querySelector('.account').getClientRects().length > 0,
           brand_target: d.querySelector('.brand').target,
           pane: txt(pane), errors: [...d.querySelectorAll('.error')].map((e) => e.textContent).filter(Boolean),
           calls: w.performance.getEntriesByType('resource').map((e) => new URL(e.name).pathname).filter((p) => !p.startsWith('/static/')),
           sections: {{ next: !!d.querySelector('.next-event'), headline: !!d.querySelector('.headline'), events: !!d.querySelector('table.events'),
                       summary: !!d.querySelector('.ix-summary'), picker: d.querySelectorAll('.vs-picker select').length, skew: !!d.querySelector('.skew'),
                       surfaces: d.querySelectorAll('.plot-surface').length }},
           tutor: {{ open: !d.getElementById('tutor').classList.contains('hidden'), msgs: d.querySelectorAll('.tutor-msg').length,
                     locked: ['tutor-q', 'tutor-new', 'tutor-sessions-button'].every((id) => d.getElementById(id).hasAttribute('data-locked'))
                             && d.querySelector('#tutor-form button[type=submit]').hasAttribute('data-locked') && d.getElementById('tutor-q').readOnly,
                     note: d.getElementById('tutor-left').textContent }},
           swap: {{ name: css(pane).animationName, duration: css(pane).animationDuration, classed: pane.classList.contains('swap') }},
           nav_flex: css(d.querySelector('#nav-list')).display,
           live_badges: d.querySelectorAll('.live-badge').length,
           next_tables: [...d.querySelectorAll('.next-event .table-wrap')].map((t) => [t.scrollWidth, t.clientWidth]),
           foot_height: d.querySelector('.footer').getBoundingClientRect().height,
           window_share: document.querySelector('.window').getBoundingClientRect().height / innerHeight,
           widths: {{ page: document.documentElement.scrollWidth, inner: innerWidth, frame: d.documentElement.scrollWidth, frame_inner: w.innerWidth }},
           page_url: location.pathname, page_history: history.length,
           homepage: {{ button: document.querySelector('.tour .actions a').getAttribute('href'), text: document.querySelector('.tour .actions a').textContent,
                        sign_up: [...document.querySelectorAll('.tour .actions a')].slice(1).map((a) => [a.textContent, a.getAttribute('href')]),
                        footer: document.getElementById('footer').textContent }} }};
}})()"""
REDUCED = [{"name": "prefers-reduced-motion", "value": "reduce"}]
PAGE_READY = {1: ".ix-summary", 2: ".skew"}


def nav_visit(i, **kw):
    return dict(url="/", logged_out=True, wait=LOADED, actions=[CLICK % i],
                wait_after=f"!!{FRAME}.contentDocument.querySelector('#pane-a {PAGE_READY[i]}')", extract=EXTRACT, timeout_s=60, **kw)


@pytest.fixture(scope="module")
def frame(browser):
    tutor = f"{FRAME}.contentDocument.getElementById('toggle-tutor').click()"
    visits = {
        "study": dict(url="/", logged_out=True, wait=LOADED, extract=EXTRACT, timeout_s=60),
        "index": nav_visit(1),
        "surface": nav_visit(2),
        "tutor": dict(url="/", logged_out=True, wait=LOADED, actions=[tutor],
                      wait_after=f"{FRAME}.contentDocument.querySelectorAll('.tutor-msg').length === 2", extract=EXTRACT, timeout_s=60),
        "reduced": nav_visit(1, media=REDUCED),
        "phone": dict(url="/", logged_out=True, width=390, height=844, mobile=True, wait=LOADED, extract=EXTRACT, timeout_s=60),
        "phone_surface": nav_visit(2, width=390, height=844, mobile=True),
        "phone_360": dict(url="/", logged_out=True, width=360, height=740, mobile=True, wait=LOADED, extract=EXTRACT, timeout_s=60),
    }
    return dict(zip(visits, browser.visit(list(visits.values()))))


def test_the_frame_shows_the_real_terminal(frame):
    for k, r in frame.items():
        errors = [e for e in r["errors"] if "Canvas2D: Multiple readback" not in e]        # Plotly's own performance notice
        assert r["ready"] and r["error"] is None and errors == [], (k, r["error"], errors)
        v = r["value"]
        assert v["errors"] == [] and v["path"] == "/preview" and v["page_url"] == "/", (k, v["errors"])
        assert v["nav"] == ["Event study", "Event index", "Vol surface", "Help"] and v["controls"] == ["Tutor"]
        assert v["top"].startswith(f"Saltum Labs Underlying SPX Data to {CPI_DAY}") and v["tag_visible"] and not v["account_visible"]
        assert v["foot"].startswith("Education only, not advice.") and "sample data" in v["foot"]
        assert "frozen" not in v["top"]
        assert v["brand_target"] == "_top"


def test_the_sidebar_switches_between_the_real_pages(frame):
    s = frame["study"]["value"]["sections"]
    assert frame["study"]["value"]["current"] == "Event study" and s["next"] and s["headline"] and s["events"]
    assert frame["index"]["value"]["current"] == "Event index" and frame["index"]["value"]["sections"]["summary"]
    s = frame["surface"]["value"]["sections"]
    assert frame["surface"]["value"]["current"] == "Vol surface" and s["picker"] == 3 and s["skew"] and s["surfaces"] == 2


def test_each_page_shows_its_one_example_and_nothing_else_can_be_picked(frame):
    study, index, surface = (frame[k]["value"] for k in ("study", "index", "surface"))
    one = [False, 1, True]                                  # enabled-looking, one option, locked
    assert study["selects"] == [one, one] and study["more"] == 1 and study["event_rows"] == 8 and study["open_links"] == 1
    assert "All (side by side)" not in study["pane"] and "Payrolls" not in study["pane"] and "FOMC" not in study["pane"]
    assert index["selects"] == [one] * 4 and index["more"] == 1 and index["event_rows"] == 1 and index["open_links"] == 1
    assert surface["selects"][:3] == [one] * 3, surface["selects"]
    assert surface["picker_buttons"] == [[False, True]] * 6 and surface["more"] == 1
    assert "Keyboard" not in study["pane"]


def test_split_view_shortcuts_and_date_keys_do_nothing_in_the_preview(browser):
    r = browser.visit([dict(url="/preview", logged_out=True, wait="!!document.querySelector('.next-event')", keys=["s", "?", "["],
                            extract="""(() => ({ split: !document.getElementById('pane-b').classList.contains('hidden'),
                                                 keys: document.getElementById('keys-dialog').open,
                                                 note: document.getElementById('status').textContent }))()""",
                            timeout_s=60)])[0]
    assert r["value"] == {"split": False, "keys": False, "note": ""}


def test_preview_help_lists_only_the_shortcuts_that_work_there(browser):
    help_rows = "[...document.querySelectorAll('#pane-a table.keys kbd')].map((k) => k.textContent)"
    preview, real = browser.visit([
        dict(url="/preview", logged_out=True, wait="!!document.querySelector('.next-event')",
             actions=["document.querySelectorAll('#nav-list a')[3].click()"], wait_after="!!document.querySelector('#pane-a table.keys')",
             extract=help_rows, timeout_s=60),
        dict(url="/terminal?a=help&split=0", wait="!!document.querySelector('#pane-a table.keys')", extract=help_rows, timeout_s=60)])
    assert preview["value"] == ["1", "2", "3", "4", "T"]
    assert real["value"] == ["1", "2", "3", "4", "S", "T", "P", "[", "]", "?"]


# ---------------------------------------------------------------- the sign-up pop-up
TRY_ALL = f"""(async () => {{
  const w = {FRAME}.contentWindow, d = {FRAME}.contentDocument, dialog = d.getElementById('locked-dialog');
  const before = w.location.search, tried = [];
  for (const el of d.querySelectorAll('[data-locked]')) {{
    if (!el.getClientRects().length) continue;
    el.dispatchEvent(new w.MouseEvent('mousedown', {{ bubbles: true, cancelable: true }}));
    el.click();
    el.dispatchEvent(new w.KeyboardEvent('keydown', {{ key: 'ArrowDown', bubbles: true, cancelable: true }}));
    const opened = dialog.open;
    d.getElementById('locked-close').click();
    tried.push([el.getAttribute('aria-label') || el.dataset.action || el.id || el.closest('label')?.firstChild.textContent.trim(), opened, dialog.open]);
  }}
  return {{ tried, same_url: w.location.search === before, msgs: d.querySelectorAll('.tutor-msg').length,
           sessions_hidden: d.getElementById('tutor-sessions').classList.contains('hidden'),
           text: dialog.innerText.replace(/\\s+/g, ' ').trim(),
           links: [...dialog.querySelectorAll('a')].map((a) => [a.textContent, a.getAttribute('href'), a.target]) }};
}})()"""


@pytest.mark.parametrize("i, ready, expected", [
    (0, ".headline", {"Event type", "Period", "tutor-q", "tutor-new", "tutor-sessions-button"}),
    (1, ".ix-summary", {"Event type", "Period", "Exclude flagged days", "Show ordinary pairs"}),
    (2, ".skew", {"Date 1", "Date 2", "Earlier date", "Later date", "Jump to a pair", "prev-pair", "next-pair"})])
def test_every_locked_control_opens_the_sign_up_pop_up_and_changes_nothing(browser, i, ready, expected):
    tutor = f"{FRAME}.contentDocument.getElementById('toggle-tutor').click()"
    r = browser.visit([dict(url="/", logged_out=True, wait=LOADED, actions=[tutor, CLICK % i],
                            wait_after=f"!!{FRAME}.contentDocument.querySelector('#pane-a {ready}') && {FRAME}.contentDocument.querySelectorAll('.tutor-msg').length === 2",
                            extract=TRY_ALL, timeout_s=60)])[0]
    v = r["value"]
    assert {name for name, _, _ in v["tried"]} >= expected, v["tried"]
    assert all(opened and not still for _, opened, still in v["tried"]), v["tried"]
    assert v["same_url"] and v["msgs"] == 2 and v["sessions_hidden"]
    assert v["text"].startswith("This is a preview with one example. Create a free account to explore every event and date.")
    assert v["links"] == [["Create a free account", "/login?mode=signup", "_top"], ["Log in", "/login", "_top"]]


POP_UP = """(() => {
  const d = document.getElementById('locked-dialog'), open = () => (d.open || document.querySelector('[data-locked]').click(), d.open);
  const r = (open(), d.getBoundingClientRect());
  const box = { left: r.left, right: r.right, inner: innerWidth, wide: document.documentElement.scrollWidth <= innerWidth,
                buttons: [...d.querySelectorAll('a, button')].map((b) => b.getBoundingClientRect().right <= r.right) };
  d.dispatchEvent(new MouseEvent('click', { bubbles: true, clientX: 2, clientY: 2 }));
  return { ...box, closed_by_outside_tap: !d.open };
})()"""


def test_the_pop_up_fits_at_360_and_closes_with_a_tap_outside_or_escape(browser):
    opened = "!!document.querySelector('[data-locked]') && (document.getElementById('locked-dialog').open || (document.querySelector('[data-locked]').click(), true))"
    tap, esc = browser.visit([
        dict(url="/preview", logged_out=True, width=360, height=740, mobile=True, wait="!!document.querySelector('[data-locked]')", extract=POP_UP, timeout_s=60),
        dict(url="/preview", logged_out=True, width=360, height=740, mobile=True, wait=opened, keys=["Escape"],
             wait_after="!document.getElementById('locked-dialog').open", extract="document.getElementById('locked-dialog').open", timeout_s=60)])
    v = tap["value"]
    assert v["left"] >= 8 and v["right"] <= v["inner"] - 8 and v["wide"] and all(v["buttons"]), v
    assert v["closed_by_outside_tap"]
    assert esc["value"] is False and esc["ready"]


def test_the_frame_always_opens_on_event_study(browser):
    # A browser reload hands the frame back its last URL, here the page the visitor left it on.
    r = browser.visit([dict(url="/preview?a=help&split=1&tutor=1&active=b", logged_out=True, wait="!!document.querySelector('#nav-list a[aria-current]')",
                            extract="""(() => ({ current: document.querySelector('#nav-list a[aria-current]').firstChild.textContent,
                                                 tutor: !document.getElementById('tutor').classList.contains('hidden'),
                                                 split: !document.getElementById('pane-b').classList.contains('hidden') }))()""",
                            timeout_s=60)])[0]
    assert r["value"] == {"current": "Event study", "tutor": False, "split": False}


def test_invented_numbers_never_read_as_real_results(frame):
    for k, r in frame.items():
        v = r["value"]
        assert v["live_badges"] == 0, k
        assert not re.search(r"Stage 5|frozen|findings_v1|findings v1|\blive\b|docs/prd|\b2020-01\b(?!-)|\b2023-0[56]\b(?!-)", v["pane"], re.I), (k, re.findall(r".{30}(?:Stage 5|frozen|findings|live|2020-01|2023-0[56]).{20}", v["pane"]))


def test_the_second_event_study_table_needs_no_sideways_scroll_at_1440(frame):
    assert frame["study"]["value"]["next_tables"] and all(s <= c for s, c in frame["study"]["value"]["next_tables"]), frame["study"]["value"]["next_tables"]


def test_the_preview_calls_nothing(frame):
    for k, r in frame.items():
        assert r["value"]["calls"] == ["/preview"] or r["value"]["calls"] == [], (k, r["value"]["calls"])


def test_the_tutor_is_a_sample_and_cannot_be_asked(frame):
    t = frame["tutor"]["value"]["tutor"]
    assert t["open"] and t["msgs"] == 2 and t["locked"] and "not called" in t["note"]


def test_page_swaps_take_300ms_and_stop_under_reduced_motion(frame):
    s = frame["index"]["value"]["swap"]
    assert s["classed"] and s["name"] == "enter" and s["duration"] == "0.3s"
    assert frame["reduced"]["value"]["swap"]["name"] == "none"


def test_the_terminal_phone_layout_fits_the_frame(frame):
    for k in ("phone", "phone_surface", "phone_360"):
        v = frame[k]["value"]
        assert v["nav_flex"] == "flex", k                               # top tabs, not the sidebar
        assert v["foot_height"] < 30 and 0.72 <= v["window_share"] <= 0.78, (k, v["foot_height"], v["window_share"])
        assert v["widths"]["page"] <= v["widths"]["inner"] and v["widths"]["frame"] <= v["widths"]["frame_inner"], (k, v["widths"])


def test_the_homepage_around_the_frame(frame):
    h = frame["study"]["value"]["homepage"]
    assert h["text"] == "Open the real terminal" and h["button"] == "/terminal"
    assert h["sign_up"] == [["Create a free account to see every event", "/login?mode=signup"]]
    assert "FRED" not in h["footer"] and "ThetaData" not in h["footer"]


# ---------------------------------------------------------------- the phone date lists, in the real terminal
PICKER = r"""(() => {
  const ctx = document.createElement('canvas').getContext('2d');
  const selects = [...document.querySelectorAll('.vs-picker select')].map((s) => {
    const st = getComputedStyle(s);
    ctx.font = `${st.fontSize} ${st.fontFamily}`;
    const pad = parseFloat(st.paddingLeft) + parseFloat(st.paddingRight) + 22;      // and the arrow
    const texts = [...s.options].map((o) => o.text);
    return { key: s.dataset.picker, room: s.clientWidth - pad, longest: Math.max(...texts.map((t) => ctx.measureText(t).width)),
             worst: texts.reduce((a, b) => (ctx.measureText(b).width > ctx.measureText(a).width ? b : a)) };
  });
  const labels = [...document.querySelectorAll('.vs-picker .controls-row > label')].map((l) => {
    const r = document.createRange();
    r.selectNode(l.firstChild);
    return { text: l.firstChild.textContent.trim(), lines: r.getClientRects().length };
  });
  return { selects, labels, wide: document.documentElement.scrollWidth <= innerWidth };
})()"""
URL = "/terminal?a=vol-surface&split=0"


@pytest.mark.parametrize("width", [390, 360])
def test_phone_date_lists_fit_and_their_labels_stay_on_one_line(browser, width):
    r = browser.visit([dict(url=URL, width=width, height=800, mobile=True, wait="!!document.querySelector('.vs-picker select')", extract=PICKER)])[0]
    v = r["value"]
    assert v["wide"] and [x["text"] for x in v["labels"]] == ["Date 1", "Date 2"] and all(x["lines"] == 1 for x in v["labels"]), v["labels"]
    for s in v["selects"]:
        assert s["longest"] <= s["room"], s
