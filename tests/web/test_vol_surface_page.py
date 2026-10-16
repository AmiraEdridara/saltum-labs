"""Page 2 in headless Edge: the Vol surface page matches the prototype pages (data/figures/events/) field by
field for 4 event pairs, shows flags next to the chart, falls back to a flat contour map without 3D graphics,
uses the stored change only for event and control pairs, fits Split view and phone width, and leaves no
browser processes running."""
import json
import subprocess

import pytest

import config
from api import db, index, surface

PAIRS = [("2020-03-10", "2020-03-11", "2020-03-11_CPI"), ("2025-04-09", "2025-04-10", "2025-04-10_CPI"),
         ("2026-09-10", "2026-09-11", "2026-09-11_CPI"), ("2020-11-04", "2020-11-05", "2020-11-05_FOMC")]
BENIGN = ("willReadFrequently",)          # a browser performance hint logged from inside Plotly's 3D code
READY = ("(() => { const p = document.querySelector('#pane-a .vol-surface'); if (!p) return false;"
         " const s = [...p.querySelectorAll('.plot-surface')]; const t = p.querySelector('.plot-term');"
         " return s.length > 0 && s.every((e) => e.data && e.data.length) && !!(t && t.data && t.data.length)"
         " && [...p.querySelectorAll('[data-caption]')].every((c) => c.textContent.length > 0); })()")
EXTRACT = r"""(() => {
  const pane = document.querySelector('#pane-a');
  const txt = (e) => (e ? e.textContent.replace(/\s+/g, ' ').trim() : null);
  const days = {};
  for (const key of ['d1', 'd2']) {
    const plot = pane.querySelector(`#a-surface-${key}`);
    if (!plot) continue;
    const fit = plot.data.find((t) => t.type === 'surface' || t.type === 'contour');
    const pts = plot.data.find((t) => t.name === 'computed options');
    days[key] = {
      type: fit.type, x: Array.from(fit.x), y: Array.from(fit.y), z: fit.z.map((r) => Array.from(r)),
      strikes: fit.customdata.map((r) => r.map((c) => c[1])), exps: fit.customdata.map((r) => r[0][3]),
      pts: pts ? pts.customdata.map((c, i) => [c[4], pts.z[i], c[1], c[3], c[3 - 1]]) : null,
      caption: txt(pane.querySelector(`[data-caption="${key}"]`)),
      head: txt(pane.querySelector(`.day-head[data-day="${key}"]`)),
      label: txt(pane.querySelector(`.day-head[data-day="${key}"] .day-label`)),
      flag_by_chart: txt(pane.querySelector(`.flag[data-flag="${key}"]`)),
      smiles: pane.querySelector(`#a-smiles-${key}`)?.data?.length || 0,
    };
  }
  const term = pane.querySelector('#a-term').data.map((t) => ({ x: Array.from(t.x), y: Array.from(t.y) }));
  const skew = {};
  pane.querySelectorAll('.skew td[data-key]').forEach((td) => {
    skew[td.dataset.key] = { value: td.dataset.value === '' ? null : Number(td.dataset.value), text: txt(td), computed: !!td.dataset.computed };
  });
  const rect = (sel) => { const e = pane.querySelector(sel); if (!e) return null; const r = e.getBoundingClientRect(); return [r.left, r.top, r.right, r.bottom]; };
  return { url: location.search, days, term, skew, note: txt(pane.querySelector('.skew .note')),
           caveats: txt(pane.querySelector('.caveats')), d1: rect('.vs-day[data-day="d1"]'), d2: rect('.vs-day[data-day="d2"]'),
           page_scroll_x: document.documentElement.scrollWidth > innerWidth };
})()"""


def prototype(name):
    t = (config.PROJECT_DIR / "data" / "figures" / "events" / f"{name}.html").read_text(encoding="utf-8")
    start = t.index("const DATA = ") + len("const DATA = ")
    return json.JSONDecoder().raw_decode(t[start:])[0]["dates"]


def errors(r):
    return [e for e in r["errors"] if not any(b in e for b in BENIGN)]


def url(d1, d2, **kw):
    extra = "".join(f"&a.{k}={v}" for k, v in kw.items())
    return f"/terminal?a=vol-surface&a.d1={d1}&a.d2={d2}{extra}&split=0&tutor=0"


@pytest.fixture(scope="module")
def pages(browser):
    out = browser.visit([dict(url=url(d1, d2, pts="true"), wait=READY, extract=EXTRACT) for d1, d2, _ in PAIRS])
    return {name: r for (_, _, name), r in zip(PAIRS, out)}


def test_pages_rendered_without_errors(pages):
    for name, r in pages.items():
        assert r["ready"] and r["error"] is None, (name, r["error"])
        assert errors(r) == [], (name, errors(r))


@pytest.mark.parametrize("d1, d2, name", PAIRS)
def test_matches_prototype_field_by_field(pages, d1, d2, name):
    proto, page = prototype(name), pages[name]["value"]
    for key, day in (("d1", d1), ("d2", d2)):
        p, v = proto[day], page["days"][key]
        assert v["type"] == "surface"
        assert v["x"] == [round(1 - g, 4) for g in p["grid"]]                       # grid, puts on the left
        assert v["y"] == [e["dte"] for e in p["expiries"]]
        assert v["exps"] == [e["exp"] for e in p["expiries"]]
        assert v["z"] == [e["iv"] for e in p["expiries"]]
        assert v["strikes"] == [e["K"] for e in p["expiries"]]
        assert [[q[0], q[1], q[2]] for q in v["pts"]] == [q for e in p["expiries"] for q in e["pts"]]   # computed options
        assert v["label"] == f"· {p['label']}"
        assert f"Drawn from the {p['range_label']}." in v["head"]
        assert ("Narrower than the usual" in v["head"]) == (not p["full_range"])
        assert ("Half-day session" in v["head"]) == p["half_day"]
        cap = v["caption"]
        assert f"within {p['fit_max']:.2f} vol points of the {p['n_pts']:,} computed options (average gap {p['fit_mean']:.2f})" in cap
        assert p["metrics"] in cap                                                   # the prototype's metrics sentence, verbatim
        assert ("Total variance falls" in cap) == (p["tv_drops"] > 0)
        for exp, dte in p["dropped"]:
            assert exp in json.dumps(p) and str(dte) in json.dumps(p)
        assert len(p["dropped"]) == (page["caveats"].count(f"{_fmt(day)}: left out") if p["dropped"] else 0)
        assert v["smiles"] >= 1
    # Term structure: both days, every expiry, as in the prototype.
    assert [t["x"] for t in page["term"]] == [[q[0] for q in proto[d]["term"] if q[0] >= 1] for d in (d1, d2)]
    assert [t["y"] for t in page["term"]] == [[q[1] for q in proto[d]["term"] if q[0] >= 1] for d in (d1, d2)]


def _fmt(day):
    import datetime as dt
    d = dt.date.fromisoformat(day)
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sept", "Oct", "Nov", "Dec"]
    return f"{d.day} {months[d.month - 1]} {d.year}"


@pytest.mark.parametrize("d1, d2, name", PAIRS)
def test_vanna_and_metrics_from_the_api(pages, d1, d2, name):
    page = pages[name]["value"]
    for key, day in (("d1", d1), ("d2", d2)):
        api = surface.get_surface(db.cursor(), day)["data"]
        assert [q[3] for q in page["days"][key]["pts"]] == [o["vanna"] for e in api["expiries"] for o in e["options"]]
        for m in ("atm30", "rr25_30", "wing10_30"):
            assert page["skew"][f"{key}.{m}"]["value"] == pytest.approx(api["metrics"][m], abs=1e-12)


def test_flagged_day_shows_its_flag_next_to_the_chart(pages):
    p = pages["2020-03-11_CPI"]["value"]["days"]
    assert p["d1"]["flag_by_chart"] == "⚑ Flagged: fit gap above 0.5 vol points (largest 0.96)."
    assert p["d2"]["flag_by_chart"] == "⚑ Flagged: fit gap above 0.5 vol points (largest 0.84)."
    q = pages["2025-04-10_CPI"]["value"]["days"]
    assert q["d1"]["flag_by_chart"].startswith("⚑ Flagged") and q["d2"]["flag_by_chart"] is None
    assert pages["2026-09-11_CPI"]["value"]["days"]["d2"]["flag_by_chart"] is None


def test_event_pair_uses_stored_changes(pages):
    sk = pages["2026-09-11_CPI"]["value"]
    stored = index.get_index(db.cursor(), "CPI", "both", "false", "false")["data"]["events"]
    row = next(r for r in stored if r["event_date"] == "2026-09-11")
    assert sk["skew"]["d_atm30_fixed_strike"]["value"] == pytest.approx(row["d_atm30_fixed_strike"], abs=1e-12)
    assert sk["skew"]["d_atm30"]["value"] == pytest.approx(row["d_atm30"], abs=1e-12)
    assert not sk["skew"]["d_atm30"]["computed"] and sk["note"].startswith("Event pair (CPI)")


def test_control_pair_and_other_pair(browser):
    ctrl = [r for r in index.get_index(db.cursor(), "all", "both", "false", "true")["data"]["events"] if r["event_type"] == "ordinary"][0]
    pairs = {(r["day_before"], r["event_date"]) for r in index.get_index(db.cursor(), "all", "both", "false", "true")["data"]["events"]}
    other = ("2026-09-14", "2026-09-15")
    assert other not in pairs
    c, o = browser.visit([dict(url=url(ctrl["day_before"], ctrl["event_date"]), wait=READY, extract=EXTRACT),
                          dict(url=url(*other), wait=READY, extract=EXTRACT)])
    for r in (c, o):
        assert r["ready"] and errors(r) == [], r["errors"]
    cs = c["value"]
    assert "100 ordinary-day control pairs" in cs["note"]
    assert cs["skew"]["d_atm30_fixed_strike"]["value"] == pytest.approx(ctrl["d_atm30_fixed_strike"], abs=1e-12)
    assert cs["skew"]["d_atm30"]["value"] == pytest.approx(ctrl["d_atm30"], abs=1e-12)
    os_ = o["value"]
    assert os_["skew"]["d_atm30_fixed_strike"] == {"value": None, "text": "only for event and control pairs", "computed": False}
    for m in ("atm30", "rr25_30", "wing10_30"):
        diff = os_["skew"][f"d_{m}"]
        assert diff["computed"] and diff["value"] == pytest.approx(os_["skew"][f"d2.{m}"]["value"] - os_["skew"][f"d1.{m}"]["value"], abs=1e-12)


def test_flat_contour_fallback_without_3d(browser):
    (r,) = browser.visit([dict(url=url("2025-04-09", "2025-04-10"), wait=READY, extract=EXTRACT)],
                         edge_args=["--disable-3d-apis", "--disable-webgl"])
    assert r["ready"] and errors(r) == [], r["errors"]
    for key in ("d1", "d2"):
        v = r["value"]["days"][key]
        assert v["type"] == "contour"
        assert v["caption"].startswith("Shown as a flat contour map because this browser has 3D graphics turned off.")
        assert v["z"] == [e["iv"] for e in prototype("2025-04-10_CPI")[{"d1": "2025-04-09", "d2": "2025-04-10"}[key]]["expiries"]]


def test_pickers_buttons_and_keys(browser):
    pair_ready = READY
    ex = "location.search"
    prev, nxt, key = browser.visit([
        dict(url=url("2026-09-10", "2026-09-11"), wait=pair_ready, extract=ex,
             actions=["document.querySelector('#pane-a [data-action=\"prev-pair\"]').click()"]),
        dict(url=url("2026-09-10", "2026-09-11"), wait=pair_ready, extract=ex,
             actions=["document.querySelector('#pane-a [data-action=\"next-pair\"]').click()"]),
        dict(url=url("2026-09-10", "2026-09-11"), wait=pair_ready, extract=ex, keys=["]"]),
    ])
    assert "a.d1=2026-09-03" in prev["value"] and "a.d2=2026-09-04" in prev["value"]      # NFP before the CPI
    assert "a.d1=2026-09-15" in nxt["value"] and "a.d2=2026-09-16" in nxt["value"]        # the FOMC after it
    assert "a.d1=2026-09-11" in key["value"] and "a.d2=2026-09-14" in key["value"]        # ] moves one drawn date
    (opts,) = browser.visit([dict(url="/terminal?a=vol-surface&split=0&tutor=0", wait=pair_ready,
                                  extract="({url: location.search, options: Object.fromEntries([...document.querySelectorAll('#pane-a select[data-picker=\"d2\"] option')].map((o) => [o.value, o.textContent]))})")])
    v = opts["value"]
    from api import surface as api_surface
    ds = api_surface.get_dates()["data"]["dates"]
    latest = next((ds[i - 1]["date"], ds[i]["date"]) for i in range(len(ds) - 1, 0, -1)
                  if ds[i]["events"] and ds[i - 1]["events_next_day"])
    assert f"a.d1={latest[0]}" in v["url"] and f"a.d2={latest[1]}" in v["url"]       # default: the latest event pair
    o = v["options"]
    assert o["2026-10-02"] == "◆ 2 Oct 2026 · payrolls day"                              # event pairs stand apart
    assert len(o) == len(ds)                                                               # every drawn date, v1 and live
    assert o["2026-10-01"] == "◆ 1 Oct 2026 · day before payrolls"
    assert o["2026-10-09"] == "9 Oct 2026"                                                 # ordinary dates plain
    assert o["2020-03-10"] == "◆ 10 Mar 2020 · day before CPI (flagged)"
    assert "2022-04-12" not in o                                                           # failed dates are not offered


def test_split_view_and_phone_width(browser):
    both = ("(() => { const t = [...document.querySelectorAll('.plot-term')];"
            " return t.length === 2 && t.every((e) => e.data && e.data.length); })()")
    lay = r"""(() => [...document.querySelectorAll('.vol-surface')].map((v) => {
        const r = (s) => v.querySelector(s).getBoundingClientRect();
        const a = r('.vs-day[data-day="d1"]'), b = r('.vs-day[data-day="d2"]');
        return { stacked: b.top >= a.bottom - 1, side: Math.abs(a.top - b.top) < 2 && b.left > a.left };
      }).concat([{ page_scroll_x: document.documentElement.scrollWidth > innerWidth,
                   controls_fit: [...document.querySelectorAll('.vs-picker button, .vs-picker select, .vs-options button')]
                     .every((e) => { const r = e.getBoundingClientRect(); return r.right <= innerWidth + 1 && r.left >= -1; }) }]))()"""
    u = ("/terminal?a=vol-surface&a.d1=2026-09-10&a.d2=2026-09-11&b=vol-surface&b.d1=2025-04-09&b.d2=2025-04-10"
         "&split=1&tutor=0")
    desk, phone, wide = browser.visit([dict(url=u, wait=both, extract=lay),
                                       dict(url=u, width=390, height=844, mobile=True, wait=both, extract=lay),
                                       dict(url=url("2026-09-10", "2026-09-11"), wait=READY, extract=EXTRACT)])
    for r in (desk, phone, wide):
        assert r["ready"] and r["error"] is None and errors(r) == [], (r["error"], r["errors"])
    assert all(p["stacked"] for p in desk["value"][:2])           # no split inside a split: days stack in a half pane
    assert all(p["stacked"] for p in phone["value"][:2]) and not phone["value"][2]["page_scroll_x"]
    assert phone["value"][2]["controls_fit"] and desk["value"][2]["controls_fit"]
    w = wide["value"]                                               # a single wide pane: days side by side
    assert abs(w["d1"][1] - w["d2"][1]) < 2 and w["d2"][0] > w["d1"][0]


def test_no_browser_processes_left(browser):
    ps = ("(Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" | Where-Object { $_.CommandLine -like "
          f"'*{browser.tmp}*' }}).Count")
    out = subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=60)
    assert out.stdout.strip() in ("", "0"), out.stdout


# ------------------------------------------------------------ surfaces must draw (2026-10-15 regression)
DRAWN = r"""(() => [...document.querySelectorAll('.plot-surface')].map((p) => {
  const r = p.getBoundingClientRect();
  return { id: p.id, scene: !!(p._fullLayout && p._fullLayout.scene), canvas: p.querySelectorAll('canvas').length,
           w: Math.round(r.width), h: Math.round(r.height), left: Math.round(r.left), top: Math.round(r.top),
           failed: p.classList.contains('plot-failed') };
}))()"""
ALL_DRAWN = ("(() => { const s = [...document.querySelectorAll('.plot-surface')];"
             " return s.length >= 2 && s.every((p) => (p._fullLayout && p._fullLayout.scene) || p.classList.contains('plot-failed')); })()")


def test_surfaces_draw_in_a_single_pane_and_in_split_view(browser):
    """Fails if any surface is not drawn as a 3D scene with a canvas of real size, in either layout."""
    single, split = browser.visit([
        dict(url="/terminal?a=vol-surface&split=0&tutor=0", wait=ALL_DRAWN, extract=DRAWN),
        dict(url="/terminal?a=event-study&b=vol-surface&b.d1=2020-03-10&b.d2=2020-03-11&split=1&tutor=0&active=b",
             wait=ALL_DRAWN, extract=DRAWN)])
    for r, n in ((single, 2), (split, 2)):
        assert r["ready"] and r["error"] is None, r["error"]
        assert not [e for e in errors(r) if e.startswith("exception")], errors(r)
        plots = r["value"]
        assert len(plots) == n, plots
        for p in plots:
            assert p["scene"] and p["canvas"] >= 1 and not p["failed"], p
            assert p["w"] > 250 and p["h"] > 250, p
    a, b = single["value"]
    assert abs(a["top"] - b["top"]) < 2 and b["left"] > a["left"]              # single pane: side by side
    a, b = split["value"]
    assert b["top"] > a["top"] + a["h"] and abs(a["left"] - b["left"]) < 2     # Split view: stacked


def test_charts_say_so_when_plotly_cannot_start(browser):
    msg = "[...document.querySelectorAll('.plot-failed')].map((p) => p.textContent)"
    (r,) = browser.visit([dict(url=url("2026-09-10", "2026-09-11"), wait=ALL_DRAWN,
                               extract=f"({{plots: {DRAWN}, messages: {msg}}})")], block=["*plotly-strict.min.js"])
    assert r["ready"], r
    plots, messages = r["value"]["plots"], r["value"]["messages"]
    assert len(plots) == 2 and all(p["failed"] for p in plots)                 # every chart box says so, none is blank
    assert len(messages) >= 5 and all(m.startswith("This chart could not be drawn: The charting library did not load.") for m in messages)
