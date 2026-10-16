// Page 2: the Vol surface page (docs/prd.md), ported from the prototype (templates/vol_page.html) to the API.
// Two dates; by default the day before an event and the event day. Every number comes from the API; the
// browser only formats, picks which expiries to draw, and takes the difference of two days' metrics for a
// pair that has no stored change.
import { cached, chartFailed, h, loadPlotly, num, renderTicket, source, table } from "../lib.js";

const NAMES = { CPI: "CPI", NFP: "payrolls", FOMC: "FOMC decision" };       // config.VOL_PAGE_EVENT_NAMES
const SMILE_TARGETS = [14, 28, 45, 63, 91, 126, 168];                       // the prototype's smile_targets
const GRID_COLUMNS = [0.9, 0.8, 0.7, 0.6, 0.5, 0.4, 0.3, 0.2, 0.1];         // table columns: grid deltas, no interpolation
const C = { ink: "#1E1B16", muted: "#5E574B", rule: "#D6CCB4", grid: "#E4DCC8", panel: "#FBF8EF", accent: "#1F3A9E",
            lo: "#F2EDDF", mid: "#7F8FC6", hi: "#1F3A9E", deep: "#0E1C4F" };
const DAY_COLOR = { d1: C.ink, d2: C.accent };
const HAS_GL = (() => { try { const k = document.createElement("canvas"); return !!(k.getContext("webgl2") || k.getContext("webgl")); } catch { return false; } })();

// ------------------------------------------------------------ labels and formatting
export function labelFor(info) {
  if (!info) return "";
  const parts = [];
  for (const t of (info.events || "").split("+").filter(Boolean)) parts.push(`${NAMES[t] || t} day`);
  for (const t of (info.events_next_day || "").split("+").filter(Boolean)) parts.push(`day before ${NAMES[t] || t}`);
  return [...new Set(parts)].join(" and ");
}
const utc = (s) => new Date(s + "T00:00:00Z");
const fmtDate = (s) => utc(s).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
const shortDate = (s) => utc(s).toLocaleDateString("en-GB", { day: "numeric", month: "short", timeZone: "UTC" });
const gapDays = (a, b) => Math.round((utc(b) - utc(a)) / 864e5);
const dlabel = (c) => (c > 0.505 ? Math.round((1 - c) * 100) + "Δ put" : c < 0.495 ? Math.round(c * 100) + "Δ call" : "ATM");
const f2 = (v) => (v === null || v === undefined ? "n/a" : Number(v).toFixed(2));
// The prototype's metrics sentence (vol_page.py), from the API's surface_metrics row.
const metricsText = (m) => `Skew metrics (README, Stage 5): at 30 days, ATM vol ${f2(m.atm30)}%, 25-delta risk reversal ${f2(m.rr25_30)} `
  + `and put wing (10-delta put minus ATM) ${f2(m.wing10_30)} vol points; at the shortest expiry (${m.short_dte} days) `
  + `${f2(m.atm_short)}%, ${f2(m.rr25_short)} and ${f2(m.wing10_short)}.`;
function mix(a, b, t) {
  const hx = (s) => [1, 3, 5].map((i) => parseInt(s.slice(i, i + 2), 16));
  const A = hx(a), B = hx(b);
  return "#" + A.map((v, i) => Math.round(v + (B[i] - v) * t).toString(16).padStart(2, "0")).join("");
}

// One day's API response, in the shape the prototype's drawing code used.
function dayView(s) {
  const d = s.data;
  const G = d.grid_call_delta, hi = G[0], lo = G[G.length - 1];
  const tickC = [...new Set([hi, ...[0.9, 0.75, 0.5, 0.25, 0.1].filter((c) => c < hi - 0.02 && c > lo + 0.02), lo])];
  return {
    raw: s, G, X: G.map((c) => +(1 - c).toFixed(4)), XLAB: G.map(dlabel),
    TICKS: { vals: tickC.map((c) => +(1 - c).toFixed(4)), text: tickC.map(dlabel) },
    E: d.expiries, term: d.term, day: d.day, metrics: d.metrics,
  };
}

function flagReasons(day) {
  if (!day.flagged) return [];
  const out = [];
  if (day.fit_max_volpts > 0.5) out.push(`fit gap above 0.5 vol points (largest ${day.fit_max_volpts.toFixed(2)})`);
  if (day.total_variance_drops > 0) out.push(`total variance falls with longer expiry at ${day.total_variance_drops} grid points`);
  return out.length ? out : ["flagged"];
}

// ------------------------------------------------------------ pairs
function pairsFrom(index) {
  const rows = index.data.events;
  const ev = rows.filter((r) => r.event_type !== "ordinary").sort((a, b) => (a.event_date < b.event_date ? 1 : -1));
  const ctrl = rows.filter((r) => r.event_type === "ordinary").sort((a, b) => (a.event_date < b.event_date ? 1 : -1));
  return { ev, ctrl, find: (d1, d2) => rows.find((r) => r.day_before === d1 && r.event_date === d2) };
}

// ------------------------------------------------------------ drawing (ported from templates/vol_page.html)
const base = () => ({ paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
  font: { family: "EB Garamond, Georgia, serif", size: 13, color: C.ink },
  hoverlabel: { bgcolor: C.panel, bordercolor: C.rule, font: { family: "IBM Plex Mono, monospace", size: 12, color: C.ink } } });
const SCALE = [[0, C.lo], [0.5, C.mid], [1, C.deep]];
const hoverFit = "%{customdata[2]} · %{y} days<br>%{customdata[0]} · strike ≈ %{customdata[1]:,}<br><b>IV %{z:.1f}%</b> (fitted)<extra></extra>";
const cdata = (v) => v.E.map((e) => e.strike.map((k, j) => [v.XLAB[j], k, fmtDate(e.expiration), e.expiration]));

function volRange(views) {
  let lo = Infinity, hi = -Infinity;
  views.forEach((v) => v.E.forEach((e) => e.iv_pct.forEach((x) => { if (x < lo) lo = x; if (x > hi) hi = x; })));
  return [Math.max(0, Math.floor(lo - 1)), Math.ceil(hi + 1)];
}

function camera(narrow) {
  const k = narrow ? 1.45 : 1;
  return { eye: { x: 0.95 * k, y: -1.95 * k, z: 0.75 * k }, center: { x: 0, y: 0, z: -0.12 } };
}

async function drawFlat(P, el, v, zr) {
  await P.react(el, [{ type: "contour", x: v.X, y: v.E.map((e) => e.dte), z: v.E.map((e) => e.iv_pct), zmin: zr[0], zmax: zr[1],
    customdata: cdata(v), colorscale: SCALE, line: { width: 0.5, color: mix(C.panel, C.ink, 0.35) },
    contours: { start: zr[0], end: zr[1], size: zr[1] - zr[0] > 30 ? 5 : 1, showlabels: true, labelfont: { size: 10, color: C.ink } },
    colorbar: { title: { text: "IV %", font: { size: 11 } }, thickness: 10, outlinewidth: 0 }, hovertemplate: hoverFit }],
  Object.assign(base(), { margin: { l: 52, r: 16, t: 12, b: 44 },
    xaxis: { tickvals: v.TICKS.vals, ticktext: v.TICKS.text, tickfont: { size: 11 } },
    yaxis: { title: { text: "days to expiry", font: { size: 11 } }, tickfont: { family: "IBM Plex Mono, monospace", size: 11 } } }),
  { displayModeBar: false, responsive: true });
}

async function drawSurface(P, el, v, zr, opts) {
  if (!HAS_GL) return drawFlat(P, el, v, zr);
  const span = zr[1] - zr[0], size = span > 30 ? 5 : span > 14 ? 2 : 1;
  const traces = [{ type: "surface", name: "fitted", x: v.X, y: v.E.map((e) => e.dte), z: v.E.map((e) => e.iv_pct), cmin: zr[0], cmax: zr[1],
    customdata: cdata(v), colorscale: SCALE, showscale: opts.showScale,
    colorbar: { title: { text: "IV %", font: { size: 11 } }, thickness: 10, len: 0.55, x: 0.98, outlinewidth: 0,
                tickfont: { family: "IBM Plex Mono, monospace", size: 11 } },
    contours: { z: { show: true, start: Math.ceil(zr[0] / size) * size, end: zr[1], size, color: mix(C.panel, C.ink, 0.35), width: 1 } },
    lighting: { ambient: 0.9, diffuse: 0.35, specular: 0.05, roughness: 0.9 }, hovertemplate: hoverFit }];
  v.E.forEach((e) => traces.push({ type: "scatter3d", mode: "lines", name: "smile", x: v.X, y: v.X.map(() => e.dte), z: e.iv_pct,
    line: { color: C.ink, width: 2 }, opacity: 0.55, hoverinfo: "skip", showlegend: false }));
  if (opts.pts) {
    const px = [], py = [], pz = [], cd = [];
    v.E.forEach((e) => e.options.forEach((o) => {
      px.push(+(1 - o.call_delta).toFixed(4)); py.push(e.dte); pz.push(o.iv_pct);
      cd.push([dlabel(o.call_delta), o.strike, fmtDate(e.expiration), o.vanna, o.call_delta]);
    }));
    traces.push({ type: "scatter3d", mode: "markers", name: "computed options", x: px, y: py, z: pz, customdata: cd,
      marker: { size: 2, color: C.ink, opacity: 0.8 }, showlegend: false,
      hovertemplate: "%{customdata[2]} · %{y} days<br>%{customdata[0]} · strike %{customdata[1]:,}<br><b>IV %{z:.2f}%</b> (computed)"
        + "<br>vanna %{customdata[3]:.5f} delta per vol point<extra></extra>" });
  }
  const ax = (t) => ({ title: { text: t, font: { size: 12 } }, gridcolor: C.grid, zerolinecolor: C.grid, showbackground: false,
                       tickfont: { family: "IBM Plex Mono, monospace", size: 11 }, color: C.muted });
  const dtes = v.E.map((e) => e.dte);
  await P.react(el, traces, Object.assign(base(), { margin: { l: 0, r: 0, t: 0, b: 0 }, uirevision: "keep",
    scene: { uirevision: "keep", camera: camera(opts.narrow), aspectmode: "manual", aspectratio: { x: 1.35, y: 1.35, z: opts.narrow ? 0.85 : 0.72 },
      xaxis: Object.assign(ax("delta"), { tickvals: v.TICKS.vals, ticktext: v.TICKS.text, range: [v.X[0], v.X[v.X.length - 1]] }),
      yaxis: Object.assign(ax("days to expiry"), { range: [Math.max(0, Math.min(...dtes) - 4), Math.max(...dtes) + 3] }),
      zaxis: Object.assign(ax("implied vol %"), { range: zr }) } }), { displayModeBar: false, responsive: true });
}

function pickSmiles(E) {
  const pick = [];
  SMILE_TARGETS.forEach((w) => {
    const e = E.reduce((a, b) => (Math.abs(b.dte - w) < Math.abs(a.dte - w) ? b : a));
    if (!pick.includes(e)) pick.push(e);
  });
  return pick.sort((a, b) => a.dte - b.dte);
}

async function drawSmiles(P, el, v, zr) {
  const pick = pickSmiles(v.E);
  const tr = pick.map((e, i) => ({ type: "scatter", mode: "lines", x: v.X, y: e.iv_pct, name: e.dte + " days",
    line: { width: 2, color: mix("#B7C1E3", C.deep, pick.length > 1 ? i / (pick.length - 1) : 1) },
    customdata: e.strike.map((k, j) => [v.XLAB[j], k]),
    hovertemplate: e.dte + " days · %{customdata[0]}<br>strike ≈ %{customdata[1]:,}<br><b>IV %{y:.1f}%</b><extra></extra>" }));
  await P.react(el, tr, Object.assign(base(), { margin: { l: 46, r: 16, t: 10, b: 40 }, hovermode: "closest",
    legend: { orientation: "h", y: 1.0, yanchor: "bottom", x: 0, font: { size: 11 } },
    xaxis: { tickvals: v.TICKS.vals, ticktext: v.TICKS.text, range: [v.X[0] - 0.01, v.X[v.X.length - 1] + 0.01], gridcolor: C.grid,
             linecolor: C.rule, zeroline: false, tickfont: { size: 11 } },
    yaxis: { range: zr, ticksuffix: "%", gridcolor: C.grid, zeroline: false, tickfont: { family: "IBM Plex Mono, monospace", size: 11 } } }),
  { displayModeBar: false, responsive: true });
  return pick;
}

async function drawTerm(P, el, days) {
  const tr = days.map((d) => ({ type: "scatter", mode: "lines+markers", name: `${d.key === "d1" ? "Date 1" : "Date 2"}: ${fmtDate(d.date)}`,
    x: d.v.term.filter((q) => q.dte >= 1).map((q) => q.dte), y: d.v.term.filter((q) => q.dte >= 1).map((q) => q.atm_iv_pct),
    customdata: d.v.term.filter((q) => q.dte >= 1).map((q) => fmtDate(q.expiration)),
    line: { width: 2, color: DAY_COLOR[d.key], dash: d.key === "d1" ? "dot" : "solid" },
    marker: { size: 6, color: DAY_COLOR[d.key], line: { width: 1.5, color: C.panel } },
    hovertemplate: fmtDate(d.date) + "<br>expires %{customdata} · %{x} days<br><b>ATM IV %{y:.1f}%</b><extra></extra>" }));
  await P.react(el, tr, Object.assign(base(), { margin: { l: 46, r: 16, t: 10, b: 44 }, hovermode: "closest", showlegend: true,
    legend: { orientation: "h", y: 1.0, yanchor: "bottom", x: 0, font: { size: 12 } },
    xaxis: { type: "log", title: { text: "days to expiry (log scale)", font: { size: 11 } }, tickvals: [1, 2, 5, 10, 20, 50, 100, 180],
             gridcolor: C.grid, linecolor: C.rule, zeroline: false, tickfont: { family: "IBM Plex Mono, monospace", size: 11 } },
    yaxis: { ticksuffix: "%", gridcolor: C.grid, zeroline: false, tickfont: { family: "IBM Plex Mono, monospace", size: 11 } } }),
  { displayModeBar: false, responsive: true });
}

// When rotation is linked, turning one surface turns the other to the same angle (as in the prototype).
function linkCameras(els, isLinked) {
  let syncing = false;
  els.forEach((gd, i) => {
    if (!gd.on) return;
    gd.on("plotly_relayout", (ev) => {
      if (!isLinked() || syncing || !ev || !ev["scene.camera"]) return;
      const other = els[1 - i];
      if (!other?._fullLayout?.scene) return;
      syncing = true;
      const done = () => { syncing = false; };
      window.Plotly.relayout(other, { "scene.camera": ev["scene.camera"] }).then(done, done);
    });
  });
}

// ------------------------------------------------------------ numbers folded under the figures
function surfaceNumbers(v) {
  const cols = GRID_COLUMNS.map((c) => v.G.findIndex((g) => Math.abs(g - c) < 1e-6)).filter((i) => i >= 0);
  const fitted = table(["Expiry", "Days", ...cols.map((i) => v.XLAB[i])],
    v.E.map((e) => [fmtDate(e.expiration), String(e.dte), ...cols.map((i) => h("span", { class: "num" }, num(e.iv_pct[i], 2)))]),
    { caption: "Fitted IV (%) at the grid's own deltas" });
  const select = h("select", { "aria-label": "Expiry for the computed options" },
    v.E.map((e, i) => h("option", { value: String(i) }, `${fmtDate(e.expiration)} (${e.dte} days, ${e.options.length} options)`)));
  const slot = h("div", {});
  const show = () => {
    const e = v.E[+select.value];
    slot.replaceChildren(table(["Strike", "Call delta", "IV (%)", "Vanna (delta per vol point)"],
      e.options.map((o) => [h("span", { class: "num" }, String(o.strike)), h("span", { class: "num" }, num(o.call_delta, 4)),
                            h("span", { class: "num" }, num(o.iv_pct, 2)), h("span", { class: "num" }, num(o.vanna, 5))]),
      { cls: "options", caption: "Computed options behind this expiry's smile" }));
  };
  select.addEventListener("change", show);
  show();
  return h("div", {}, fitted, h("label", { class: "inline" }, "Computed options for ", select), slot,
           source("vol_surface", "surface_expiries"));
}

function termNumbers(days) {
  const near = days.length > 1 && gapDays(days[0].date, days[1].date) < 300;
  const terms = days.map((d) => d.v.term.filter((q) => q.dte >= 1));
  const head = days.map((d) => (near ? shortDate(d.date) : fmtDate(d.date)));
  if (near) {
    const by = terms.map((t) => new Map(t.map((q) => [q.expiration, q])));
    const exps = [...new Set(terms.flatMap((t) => t.map((q) => q.expiration)))].sort();
    return table(["Expiry", ...head], exps.map((x) => [fmtDate(x), ...by.map((m) => h("span", { class: "num" }, m.has(x) ? num(m.get(x).atm_iv_pct, 2) : "–"))]),
                 { caption: "ATM implied vol (%) by expiry" });
  }
  const by = terms.map((t) => new Map(t.map((q) => [q.dte, q])));
  const dtes = [...new Set(terms.flatMap((t) => t.map((q) => q.dte)))].sort((a, b) => a - b);
  return table(["Days to expiry", ...head], dtes.map((x) => [String(x), ...by.map((m) => h("span", { class: "num" }, m.has(x) ? num(m.get(x).atm_iv_pct, 2) : "–"))]),
               { caption: "ATM implied vol (%) by days to expiry (dates far apart share no expiries)" });
}

// ------------------------------------------------------------ sections
function dayHead(key, date, info, s, err) {
  const title = h("h2", {}, `${key === "d1" ? "Date 1" : "Date 2"}: ${fmtDate(date)}`,
                  h("span", { class: "day-label" }, ` · ${labelFor(info) || "ordinary day"}`),
                  info?.live ? [" ", h("span", { class: "live-badge", title: "Added by the daily update; not in findings_v1" }, "live")] : null);
  if (err) return h("div", { class: "day-head", "data-day": key }, title, h("p", { class: "error" }, err));
  const d = s.data.day;
  const notes = [`Close on ${fmtDate(date)}. Drawn from the ${d.range_label}.`];
  if (!d.full_range) notes.push("Narrower than the usual 10-delta put to 10-delta call: fewer than half of the expiries have quotes that reach those ends.");
  if (d.half_day) notes.push("Half-day session: quotes are from the 13:15 close.");
  return h("div", { class: "day-head", "data-day": key }, title, h("p", { class: "day-note" }, notes.join(" ")),
           d.flagged ? h("p", { class: "flag" }, "⚑ Flagged: ", flagReasons(d).join("; "), ".") : null);
}

function skewSection(st, days, pair, skew) {
  const [a, b] = days;
  const m1 = a.v?.metrics, m2 = b.v?.metrics;
  const rows = [["30-day ATM vol (%)", "atm30", "d_atm30"], ["25-delta risk reversal (vol points)", "rr25_30", "d_rr25_30"],
                ["Put wing, 10-delta put minus ATM (vol points)", "wing10_30", "d_wing10_30"]];
  let changeFrom, sources = ["surface_metrics", "control_surface_metrics"], fixed = null, note;
  if (pair && pair.event_type !== "ordinary") {
    changeFrom = skew ? skew.data.change : pair;
    fixed = changeFrom.d_atm30_fixed_strike;
    sources = [...sources, "event_skew_changes", "event_fixed_strike"];
    note = `Event pair (${pair.event_type}): the change and the fixed-strike change are the stored Stage 5 and 5b numbers.`;
  } else if (pair) {
    changeFrom = pair;
    fixed = pair.d_atm30_fixed_strike;
    sources = [...sources, "control_pair_changes"];
    note = "One of the 100 ordinary-day control pairs: the change and the fixed-strike change are the stored Stage 5b numbers.";
  } else {
    note = "Not an event pair or a control pair: the change is Date 2 minus Date 1 of the numbers above. "
         + "The fixed-strike change exists only for event pairs and the 100 control pairs.";
  }
  const cell = (v, k) => h("td", { class: "r num", "data-key": k, "data-value": v ?? "" }, num(v, 3));
  const change = (key, dkey) => {
    if (changeFrom) return h("td", { class: "r num", "data-key": dkey, "data-value": changeFrom[dkey] ?? "" }, num(changeFrom[dkey], 3, true));
    const v = m1 && m2 && m1[key] !== null && m2[key] !== null ? m2[key] - m1[key] : null;      // the one browser calculation
    return h("td", { class: "r num", "data-key": dkey, "data-value": v ?? "", "data-computed": "difference" }, num(v, 3, true));
  };
  const body = rows.map(([label, key, dkey]) => h("tr", {}, h("th", { scope: "row" }, label),
    cell(m1?.[key], `d1.${key}`), cell(m2?.[key], `d2.${key}`), change(key, dkey)));
  body.push(h("tr", { class: "fixed-strike" }, h("th", { scope: "row" }, "30-day ATM at a fixed strike, change (vol points)"),
    h("td", { class: "r" }, "–"), h("td", { class: "r" }, "–"),
    fixed === null ? h("td", { class: "r muted", "data-key": "d_atm30_fixed_strike", "data-value": "" }, "only for event and control pairs")
                   : h("td", { class: "r num", "data-key": "d_atm30_fixed_strike", "data-value": fixed }, num(fixed, 3, true))));
  return h("section", { class: "skew", "aria-labelledby": `${st.pane}-skew` },
    h("h2", { id: `${st.pane}-skew` }, "Skew numbers"),
    h("div", { class: "table-wrap" }, h("table", { class: "data" },
      h("thead", {}, h("tr", {}, h("th", { scope: "col" }, ""), h("th", { scope: "col", class: "r" }, `Date 1 (${a.date})`),
                     h("th", { scope: "col", class: "r" }, `Date 2 (${b.date})`), h("th", { scope: "col", class: "r" }, "Change"))),
      h("tbody", {}, body))),
    h("p", { class: "note" }, note), source(...sources));
}

function caveats(days) {
  const items = ["One event proves nothing on its own."];
  for (const d of days) {
    if (!d.v) continue;
    const day = d.v.day;
    if (day.flagged) items.push(`${fmtDate(d.date)} is flagged: ${flagReasons(day).join("; ")}.`);
    if (!day.full_range) items.push(`${fmtDate(d.date)} is drawn from the ${day.range_label} only.`);
    if (day.dropped.length) items.push(`${fmtDate(d.date)}: left out because their options do not span the delta range drawn: `
                                       + day.dropped.map((x) => fmtDate(x.expiration)).join(", ") + ".");
  }
  items.push("Fitted smiles are cubics through each expiry's computed implied vols, joined between expiries by straight lines and not extrapolated.");
  items.push("Education only, not advice.");
  return h("section", { class: "caveats", "aria-label": "Flags and caveats" }, h("h2", {}, "Flags and caveats"),
           h("ul", {}, items.map((t) => h("li", {}, t))));
}

// ------------------------------------------------------------ controls: the two-date picker
function picker(st, dates, pairs, ctx) {
  const list = dates.data.dates;
  const byYear = new Map();
  for (const d of list) {
    if (!byYear.has(d.date.slice(0, 4))) byYear.set(d.date.slice(0, 4), []);
    byYear.get(d.date.slice(0, 4)).push(d);
  }
  const optText = (d) => {
    const lab = labelFor(d);
    return `${lab ? "◆ " : ""}${fmtDate(d.date)}${lab ? " · " + lab : ""}${d.flagged ? " (flagged)" : ""}`;
  };
  const dateSelect = (key) => {
    const sel = h("select", { "aria-label": key === "d1" ? "Date 1" : "Date 2", "data-picker": key,
                              onchange: (e) => ctx.setParams(st.pane, { [key]: e.target.value }) },
      [...byYear].reverse().map(([y, ds]) => h("optgroup", { label: y },
        [...ds].reverse().map((d) => h("option", { value: d.date, selected: d.date === st.params[key] }, optText(d))))));
    const i = list.findIndex((d) => d.date === st.params[key]);
    const step = (k) => () => { const n = list[i + k]; if (n) ctx.setParams(st.pane, { [key]: n.date }); };
    return h("span", { class: "date-pick" }, h("button", { type: "button", "aria-label": "Earlier date", onclick: step(-1), disabled: i <= 0 }, "‹"),
             sel, h("button", { type: "button", "aria-label": "Later date", onclick: step(1), disabled: i < 0 || i >= list.length - 1 }, "›"));
  };
  const cur = `${st.params.d1}|${st.params.d2}`;
  const pairText = (r) => `${r.event_type}: day before / day of · ${shortDate(r.day_before)} – ${fmtDate(r.event_date)}${r.flagged ? " (flagged)" : ""}`;
  const pairSel = h("select", { "aria-label": "Jump to a pair", "data-picker": "pair",
                                onchange: (e) => { const [d1, d2] = e.target.value.split("|"); if (d2) ctx.setParams(st.pane, { d1, d2 }); } },
    h("option", { value: "" }, "Jump to a pair…"),
    h("optgroup", { label: `Event pairs (${pairs.ev.length})` },
      pairs.ev.map((r) => h("option", { value: `${r.day_before}|${r.event_date}`, selected: cur === `${r.day_before}|${r.event_date}` }, pairText(r)))),
    h("optgroup", { label: `Ordinary control pairs (${pairs.ctrl.length})` },
      pairs.ctrl.map((r) => h("option", { value: `${r.day_before}|${r.event_date}`, selected: cur === `${r.day_before}|${r.event_date}` },
                              `ordinary pair · ${shortDate(r.day_before)} – ${fmtDate(r.event_date)}`))));
  // Previous / next event pair, by event date, from the current Date 2.
  const prevPair = pairs.ev.find((r) => r.event_date < st.params.d2);
  const nextPair = [...pairs.ev].reverse().find((r) => r.event_date > st.params.d2);
  const go = (r) => () => ctx.setParams(st.pane, { d1: r.day_before, d2: r.event_date });
  return h("div", { class: "vs-picker" },
    h("div", { class: "controls-row" }, h("label", {}, "Date 1 ", dateSelect("d1")), h("label", {}, "Date 2 ", dateSelect("d2"))),
    h("div", { class: "controls-row" }, pairSel,
      h("button", { type: "button", onclick: prevPair ? go(prevPair) : null, disabled: !prevPair, "data-action": "prev-pair" }, "‹ Previous event pair"),
      h("button", { type: "button", onclick: nextPair ? go(nextPair) : null, disabled: !nextPair, "data-action": "next-pair" }, "Next event pair ›")),
    h("p", { class: "muted small" }, "◆ marks event days and the days before them. Event pairs and the 100 ordinary control pairs are in the jump list."));
}

function options(st, ctx, onReset) {
  const seg = h("span", { class: "seg", role: "group", "aria-label": "Days shown" },
    [["both", "Both days"], ["d1", "Date 1"], ["d2", "Date 2"]].map(([v, t]) =>
      h("button", { type: "button", "aria-pressed": String(st.params.view === v), onclick: () => ctx.setParams(st.pane, { view: v }) }, t)));
  const chk = (key, text) => h("label", { class: "chk" },
    h("input", { type: "checkbox", checked: st.params[key] === "true", "data-option": key,
                 onchange: (e) => ctx.setParams(st.pane, { [key]: String(e.target.checked) }) }), " ", text);
  return h("div", { class: "controls-row vs-options" }, seg, chk("same", "Same vol scale"), chk("link", "Link rotation"),
           chk("pts", "Show computed options"), h("button", { type: "button", class: "link", onclick: onReset }, "Reset view"));
}

// ------------------------------------------------------------ the page
export async function render(container, paneId, params, ctx) {
  const current = renderTicket(container);
  const body = h("div", { class: "vol-surface" }, h("p", { class: "muted" }, "Loading…"));
  container.append(body);
  if (!params.d1 || !params.d2) { body.replaceChildren(h("p", {}, "Pick two dates.")); return; }
  const st = { pane: paneId, params };
  const get = (path) => cached(path).then((r) => [r, null], (e) => [null, e.message]);
  const [[dates], [index], [s1, e1], [s2, e2]] = await Promise.all([
    get("/api/dates"), get("/api/events/index?include_ordinary=true"),
    get(`/api/surface/${params.d1}`), get(`/api/surface/${params.d2}`)]);
  if (!current()) return;
  if (!dates || !index) { body.replaceChildren(h("p", { class: "error" }, "The date list could not be loaded.")); return; }
  const pairs = pairsFrom(index);
  const pair = pairs.find(params.d1, params.d2);
  let skew = null;
  if (pair && pair.event_type !== "ordinary") [skew] = await get(`/api/events/${params.d2}/skew`);
  if (!current()) return;
  const info = new Map(dates.data.dates.map((d) => [d.date, d]));
  const days = [{ key: "d1", date: params.d1, s: s1, err: e1 }, { key: "d2", date: params.d2, s: s2, err: e2 }]
    .map((d) => ({ ...d, v: d.s ? dayView(d.s) : null, info: info.get(d.date) }));
  const shown = days.filter((d) => params.view === "both" || params.view === d.key);
  const plots = new Map();
  const narrow = container.clientWidth < 700;

  const dayBlocks = shown.map((d) => {
    const fig = (kind, n, caption) => h("figure", { class: `chart vs-${kind}` }, h("figcaption", {}, caption),
      kind === "surface" && d.v?.day.flagged ? h("p", { class: "flag", "data-flag": d.key }, "⚑ Flagged: ", flagReasons(d.v.day).join("; "), ".") : null,
      d.v ? (() => { const el = h("div", { class: `plot plot-${kind}`, id: `${paneId}-${kind}-${d.key}` }); plots.set(`${kind}-${d.key}`, el); return el; })()
          : h("p", { class: "error" }, d.err),
      kind === "surface" && d.v ? h("p", { class: "cap", "data-caption": d.key }) : null,
      kind === "surface" && d.v ? h("details", {}, h("summary", {}, "Numbers behind the surface, with vanna"), surfaceNumbers(d.v)) : null);
    const tag = d.key === "d1" ? "a" : "b";
    return h("div", { class: "vs-day", "data-day": d.key },
      fig("surface", 1, `Figure 1${shown.length > 1 ? tag : ""}. Surface, ${fmtDate(d.date)}${d.v ? ` (${d.v.E.length} expiries)` : ""}`),
      fig("smiles", 2, `Figure 2${shown.length > 1 ? tag : ""}. Smiles against delta, ${fmtDate(d.date)}`));
  });
  const termEl = h("div", { class: "plot plot-term", id: `${paneId}-term` });
  const reset = () => plots.forEach((el, k) => { if (k.startsWith("surface") && el._fullLayout?.scene) window.Plotly.relayout(el, { "scene.camera": camera(narrow) }); });
  body.replaceChildren(
    h("header", { class: "vs-head" }, days.map((d) => dayHead(d.key, d.date, d.info, d.s, d.err))),
    picker(st, dates, pairs, ctx),
    options(st, ctx, reset),
    skewSection(st, days, pair, skew),
    h("div", { class: `vs-days ${shown.length > 1 ? "both" : "one"}` }, dayBlocks),
    h("figure", { class: "chart vs-term" }, h("figcaption", {}, `Figure 3. Term structure: ATM vol by expiry, ${days.map((d) => fmtDate(d.date)).join(" and ")}`),
      days.every((d) => d.v) ? termEl : h("p", { class: "error" }, "Both days are needed for the term structure."),
      days.every((d) => d.v) ? h("details", {}, h("summary", {}, "Numbers behind the term structure"), termNumbers(days)) : null,
      source("surface_expiries")),
    caveats(days),
    source(...new Set(days.flatMap((d) => d.s?.source || [])), "data/all_dates_status.csv"));

  let P;
  try {
    P = await loadPlotly();
  } catch (err) {
    body.querySelectorAll(".plot").forEach((el) => chartFailed(el, err));
    return;
  }
  if (!current()) return;
  const views = days.filter((d) => d.v).map((d) => d.v);
  const shared = volRange(views);
  for (const d of shown) {
    if (!d.v) continue;
    const zr = params.same === "true" ? shared : volRange([d.v]);
    const showScale = !narrow && (shown.length === 1 || params.same !== "true" || d.key === "d2");
    const surf = plots.get(`surface-${d.key}`);
    try {
      try {
        await drawSurface(P, surf, d.v, zr, { pts: params.pts === "true", narrow, showScale });
      } catch {
        P.purge(surf);                                  // 3D failed: fall back to the flat contour map
        await drawFlat(P, surf, d.v, zr);
      }
      const flat = !surf._fullLayout?.scene;
      const day = d.v.day;
      container.querySelector(`[data-caption="${d.key}"]`).textContent =
        `${flat ? "Shown as a flat contour map because this browser has 3D graphics turned off. " : ""}`
        + `Implied volatility at the close on ${fmtDate(d.date)}, across ${d.v.E.length} Friday expiries. Low strikes sit on the left and carry `
        + `higher volatility, which is the put skew. Fitted smiles stay within ${day.fit_max_volpts.toFixed(2)} vol points of the `
        + `${day.fitted_points.toLocaleString("en-GB")} computed options (average gap ${day.fit_mean_volpts.toFixed(2)}). `
        + metricsText(d.v.metrics)
        + (day.total_variance_drops ? ` Total variance falls with longer expiry at ${day.total_variance_drops} grid points, which should not happen and points to unreliable quotes.` : "");
    } catch (err) {
      chartFailed(surf, err);
    }
    try { await drawSmiles(P, plots.get(`smiles-${d.key}`), d.v, zr); } catch (err) { chartFailed(plots.get(`smiles-${d.key}`), err); }
    if (!current()) return;
  }
  const surfaces = shown.filter((d) => d.v && HAS_GL).map((d) => plots.get(`surface-${d.key}`)).filter((el) => el?._fullLayout?.scene);
  if (surfaces.length === 2) linkCameras(surfaces, () => params.link === "true");
  if (days.every((d) => d.v)) {
    try { await drawTerm(P, termEl, days); } catch (err) { chartFailed(termEl, err); }
  }
}
