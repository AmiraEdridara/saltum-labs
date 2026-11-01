// Page 1: the event study (docs/prd.md). Every number comes from the API, shown with n and its source.
// Nothing is calculated here except formatting and sorting. "Held or not" is quoted from findings_v1.md.
import { cached, chartFailed, h, loadPlotly, num, plain, renderTicket, source, table } from "../lib.js";

const TYPE_NAME = { CPI: "CPI", NFP: "Payrolls (NFP)", FOMC: "FOMC" };
const PERIOD_NAME = { both: "both periods", "2020-01..2023-05": plain("2020-01 to 2023-05", "earlier period"),
                      "2023-06..now": plain("2023-06 to now", "later period") };
// Which finding states, in words, whether each type's premium held (findings_v1.md, Section 1).
const FINDING_FOR = { CPI: "1.2", FOMC: "1.2", NFP: "1.3" };
const DEFINITIONS = [
  ["Implied move", "ATM straddle divided by the forward, priced at the 16:15 close the day before"],
  ["Actual move", "Size of the move from that forward to the close on the event day"],
  ["Baseline", "Median implied move on ordinary days within 20 trading days either side"],
  ["Premium", "Implied move minus baseline"],
  ["Variance premium", "Implied move squared minus baseline squared"],
];
const COLORS = { accent: "#1F3A9E", ink: "#000000", muted: "#8A8273", rule: "#D6CCB4" };

const n = (v) => h("span", { class: "num" }, num(v, 3));
function longDate(iso) {
  const d = new Date(iso + "T12:00:00Z");
  return d.toLocaleDateString("en-GB", { weekday: "long", day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });
}
// ------------------------------------------------------------ findings quotes
function findingText(findings, id) {
  const md = findings.data.sections.find((s) => s.id === "1")?.markdown || "";
  const start = md.indexOf(`**Finding ${id}`);
  if (start < 0) return null;
  const block = md.slice(start).split(/\n\s*\n/)[0];
  const clean = (s) => s.replace(/\*\*/g, "").replace(/`/g, "").replace(/\s+/g, " ").trim();
  const [title, ...bullets] = block.split(/\n- /);
  return { title: clean(title), bullets: bullets.map(clean) };
}

// ------------------------------------------------------------ sections
function nextPanel(next) {
  const d = next.data;
  const box = h("section", { class: "next-event", "aria-labelledby": "next-title" });
  if (!d.soonest) {
    box.append(h("h2", { id: "next-title" }, "Next event"), h("p", {}, "No upcoming date in the event calendar."));
    return box;
  }
  const s = d.soonest;
  const when = s.days_away === 0 ? "today" : s.days_away === 1 ? "tomorrow" : `in ${s.days_away} days`;
  box.append(h("h2", { id: "next-title" }, "Next event: what past events show"),
             h("p", { class: "next-head" }, h("strong", {}, s.event_types.map((t) => TYPE_NAME[t]).join(" and ")),
               ` · ${longDate(s.date)} · ${when}`));
  for (const t of s.event_types) {
    const e = d.by_type[t];
    const priced = e.priced_now
      ? h("p", {}, "Priced now: implied move ", n(e.priced_now.implied_move_pct), "% ",
          h("span", { class: "muted" }, `(${e.priced_note}; `), h("code", {}, e.priced_now.source), ")")
      : h("p", {}, "Priced now: ", h("span", { class: "muted" }, e.priced_note), ".");
    const past = d.in_the_past[t];
    const after = d.after_the_event[t];
    box.append(
      s.event_types.length > 1 ? h("h3", {}, TYPE_NAME[t]) : "",
      priced,
      table(["In the past", "n", "Median implied", "Median actual", "Actual smaller than priced"],
            past.map((r) => [PERIOD_NAME[r.period], String(r.n), n(r.median_implied_move_pct), n(r.median_actual_move_pct),
                             h("span", {}, n(r.share_actual_below_implied_pct), "% of days")]),
            { caption: `${TYPE_NAME[t]} days in the past (moves in %)` }),
      table(["After the event, in the past", "Event (n)", "Ordinary pairs (n)", "Event minus ordinary", "90% interval", "Survives both controls"],
            after.map((r) => [r.measure, h("span", {}, n(r.median_volpts), ` (${r.n})`),
                              h("span", {}, n(r.ordinary_median_volpts), ` (${r.ordinary_n})`),
                              h("span", { class: "num" }, num(r.event_minus_ordinary_volpts, 3, true)),
                              h("span", { class: "num" }, `[${num(r.ci90_volpts[0], 3, true)}, ${num(r.ci90_volpts[1], 3, true)}]`),
                              r.survives_both_controls ? "Yes" : "No"]),
            { caption: `${TYPE_NAME[t]}: ${d.after_the_event_note}` }));
  }
  box.append(source(...next.source), h("p", { class: "caveat" }, next.caveat));
  return box;
}

function headline(ev) {
  const sums = ev.data.summaries;
  const rows = [
    ["Median implied move (%)", (s) => n(s.median_implied_move_pct)],
    ["Median baseline, ordinary days nearby (%)", (s) => n(s.median_baseline_pct)],
    ["Median premium (pp)", (s) => h("span", { class: "num" }, num(s.median_premium_pp, 3, true))],
    ["Median variance premium (%²)", (s) => h("span", { class: "num" }, num(s.median_variance_premium_pct2, 3, true))],
    ["n (events)", (s) => h("span", { class: "num" }, String(s.n))],
  ];
  return h("section", { class: "headline", "aria-labelledby": "headline-title" },
    h("h2", { id: "headline-title" }, "Headline numbers"),
    table(["", ...sums.map((s) => TYPE_NAME[s.event_type])], rows.map(([label, f]) => [label, ...sums.map(f)]),
          { caption: `${PERIOD_NAME[ev.period]}; one column per event type, no pooled summary` }),
    h("p", { class: ev.data.summaries_data === "live" ? "data-label live" : "data-label" },
      plain(ev.data.summaries_data === "live"
        ? `Includes live data after ${ev.v1_end} (${ev.data.live_rows} events): not in findings_v1. Premium medians use final premiums only.`
        : `Frozen results: findings v1, data to ${ev.v1_end}.`
          + (ev.data.live_rows ? ` ${ev.data.live_rows} newer events are in the table below, marked live.` : ""),
        "Sample data: invented numbers, not results.")),
    source(...ev.source.filter((x) => x !== "date_list")), h("p", { class: "muted small" }, ev.caveat));
}

function figureBox(id, title, numbers) {
  return h("figure", { class: "chart" }, h("figcaption", {}, title), h("div", { class: "plot", id }),
           h("details", {}, h("summary", {}, "Numbers"), numbers));
}

const layoutBase = (ytitle) => ({
  paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)", margin: { l: 52, r: 12, t: 8, b: 40 },
  font: { family: "Inter, Segoe UI, sans-serif", size: 14, color: COLORS.ink }, height: 320,
  xaxis: { gridcolor: COLORS.rule, linecolor: COLORS.rule, tickfont: { family: "IBM Plex Mono, monospace", size: 11 } },
  yaxis: { title: { text: ytitle }, gridcolor: COLORS.rule, zerolinecolor: COLORS.muted,
           tickfont: { family: "IBM Plex Mono, monospace", size: 11 } },
  legend: { orientation: "h", y: -0.18 }, hoverlabel: { font: { family: "IBM Plex Mono, monospace" } },
});

async function figure1(el, ev) {
  const rows = [...ev.data.events].sort((a, b) => (a.date < b.date ? -1 : 1));
  const P = await loadPlotly();
  const text = rows.map((r) => `${r.date} ${r.event_type}`);
  await P.newPlot(el, [
    { name: "Baseline (ordinary days nearby)", x: rows.map((r) => r.date), y: rows.map((r) => r.baseline_pct), mode: "lines",
      line: { color: COLORS.muted, width: 1.5 }, hovertemplate: "%{text}<br>baseline %{y:.3f}%<extra></extra>", text },
    { name: "Implied move", x: rows.map((r) => r.date), y: rows.map((r) => r.implied_move_pct), mode: "markers",
      marker: { color: COLORS.accent, size: 6 }, hovertemplate: "%{text}<br>implied %{y:.3f}%<extra></extra>", text },
    { name: "Actual move", x: rows.map((r) => r.date), y: rows.map((r) => r.actual_move_pct), mode: "markers",
      marker: { color: "rgba(0,0,0,0)", size: 6, line: { color: COLORS.ink, width: 1 } },
      hovertemplate: "%{text}<br>actual %{y:.3f}%<extra></extra>", text },
  ], layoutBase("Move (%)"), { responsive: true, displayModeBar: false });
}

async function figure2(el, ev) {
  const sums = ev.data.summaries;
  const types = sums.map((s) => s.event_type);
  const P = await loadPlotly();
  const traces = [];
  types.forEach((t, i) => {
    const rows = ev.data.events.filter((r) => r.event_type.split("+").includes(t));
    // Spread points sideways so they do not sit on top of each other (display only).
    const x = rows.map((_, j) => i + ((j * 0.6180339887) % 1 - 0.5) * 0.5);
    traces.push({ name: TYPE_NAME[t], x, y: rows.map((r) => r.premium_pp), mode: "markers", showlegend: false,
                  marker: { color: "rgba(31,58,158,0.55)", size: 6 }, text: rows.map((r) => r.date),
                  hovertemplate: `${t} %{text}<br>premium %{y:+.3f} pp<extra></extra>` });
    const m = sums[i].median_premium_pp;
    traces.push({ name: `${t} median`, x: [i - 0.35, i + 0.35], y: [m, m], mode: "lines", showlegend: false,
                  line: { color: COLORS.ink, width: 3 }, hovertemplate: `${t} median %{y:+.3f} pp (n ${sums[i].n})<extra></extra>` });
  });
  const layout = layoutBase("Premium (pp)");
  layout.xaxis = { ...layout.xaxis, tickmode: "array", tickvals: types.map((_, i) => i),
                   ticktext: sums.map((s) => `${s.event_type} (n ${s.n})`), range: [-0.6, types.length - 0.4], zeroline: false };
  layout.annotations = sums.map((s, i) => ({ x: i + 0.36, y: s.median_premium_pp, xanchor: "left", showarrow: false,
                                             text: `median ${num(s.median_premium_pp, 3, true)}`,
                                             font: { family: "IBM Plex Mono, monospace", size: 11, color: COLORS.ink } }));
  layout.shapes = [{ type: "line", xref: "paper", x0: 0, x1: 1, y0: 0, y1: 0, line: { color: COLORS.muted, width: 1, dash: "dot" } }];
  await P.newPlot(el, traces, layout, { responsive: true, displayModeBar: false });
}

const COLUMNS = [
  ["date", "Date", (r) => (r.live ? [r.date, " ", h("span", { class: "live-badge", title: "Added by the daily update; not in findings_v1" }, "live")] : r.date)],
  ["event_type", "Type", (r) => r.event_type],
  ["implied_move_pct", "Implied (%)", (r) => num(r.implied_move_pct)], ["actual_move_pct", "Actual (%)", (r) => num(r.actual_move_pct)],
  ["ratio", "Ratio", (r) => num(r.ratio)],
  ["premium_pp", "Premium (pp)", (r) => (r.premium_status === "final" ? num(r.premium_pp, 3, true)
    : r.premium_status === "provisional"
      ? h("span", { class: "provisional", title: "Recomputed by each daily update until the 20 trading days after the event are in" },
          `${num(r.provisional_premium_pp, 3, true)} provisional (${r.provisional_window_days_used} of ${r.provisional_window_days} days)`)
      : "pending")],
  ["variance_premium_pct2", "Variance premium (%²)", (r) => num(r.variance_premium_pct2, 3, true)],
];

function eventTable(ev, ctx, sort) {
  const rows = [...ev.data.events].sort((a, b) => {
    const x = a[sort.key], y = b[sort.key];
    return (x < y ? -1 : x > y ? 1 : 0) * (sort.dir === "asc" ? 1 : -1);
  });
  const head = h("tr", {}, COLUMNS.map(([key, label], i) => h("th", {
    scope: "col", class: i > 1 ? "r" : "", "aria-sort": sort.key === key ? (sort.dir === "asc" ? "ascending" : "descending") : "none",
  }, h("button", { type: "button", class: "sort", onclick: () => ctx.resort(key) }, label,
       sort.key === key ? (sort.dir === "asc" ? " ▲" : " ▼") : ""))), h("th", { scope: "col" }, "Vol surface"));
  const body = rows.map((r) => h("tr", {}, COLUMNS.map(([, , f], i) => (i ? h("td", { class: i > 1 ? "r num" : "" }, f(r))
                                                                          : h("th", { scope: "row", class: "num" }, f(r)))),
    h("td", {}, h("a", { href: ctx.surfaceHref(r.trade_date, r.date),
                         onclick: (e) => { if (!e.ctrlKey && !e.metaKey && !e.shiftKey) { e.preventDefault(); ctx.openSurface(r.trade_date, r.date); } } },
                  "Open"))));
  const nm = ev.data.not_measured;
  const left = Object.entries(nm.by_type).filter(([, c]) => c).map(([t, c]) => `${c} ${t}`).join(", ");
  return h("section", { "aria-labelledby": "table-title" },
    h("h2", { id: "table-title" }, `Event table (n ${ev.n})`),
    h("div", { class: "table-wrap" }, h("table", { class: "data events" }, h("thead", {}, head), h("tbody", {}, body))),
    h("p", { class: "note" }, nm.n
      ? `${nm.n} event days in this selection are left out (${left}): ${nm.reason}.`
      : "No event days in this selection are left out."),
    nm.n ? h("details", {}, h("summary", {}, "Dates left out"),
             h("p", { class: "num" }, nm.dates.map((d) => `${d.date} ${d.event_type}`).join(" · "))) : null,
    source(...ev.source.filter((x) => x !== "date_list"), "date_list"));
}

function caveats(ev, findings) {
  const types = ev.data.summaries.map((s) => s.event_type);
  const ids = [...new Set(types.map((t) => FINDING_FOR[t]))];
  return h("section", { class: "caveats", "aria-labelledby": "caveats-title" },
    h("h2", { id: "caveats-title" }, "Caveats"),
    h("p", {}, "Sample size in this selection (", PERIOD_NAME[ev.period], "): ",
      ev.data.summaries.map((s) => `${TYPE_NAME[s.event_type]} n ${s.n}`).join(", "), "."),
    h("p", {}, plain("Did the premium hold in both periods? In the words of the frozen findings:",
                     "Did the premium hold in both periods? The real terminal quotes its findings here. Sample text:")),
    ids.map((id) => {
      const f = findingText(findings, id);
      return f ? h("blockquote", {}, h("p", {}, h("strong", {}, f.title)), f.bullets.map((b) => h("p", {}, b)),
                   h("footer", {}, plain([h("code", {}, "findings_v1.md"), `, Section 1, Finding ${id}`], "Sample text, not a finding"))) : null;
    }),
    h("p", {}, ev.caveat));
}

function definitions() {
  return h("section", { "aria-labelledby": "defs-title" }, h("h2", { id: "defs-title" }, "Definitions"),
    h("dl", { class: "defs" }, DEFINITIONS.map(([t, d]) => [h("dt", {}, t), h("dd", {}, d)])),
    plain(h("p", { class: "muted small" }, "From docs/prd.md; served by GET /definitions from the tutor stage."), null));
}

// ------------------------------------------------------------ the page
const sortState = new WeakMap();

export async function render(container, paneId, params, ctx) {
  const current = renderTicket(container);
  const body = h("div", { class: "event-study" }, h("p", { class: "muted" }, "Loading…"));
  container.append(body);
  let ev, next, findings;
  try {
    [ev, next, findings] = await Promise.all([
      cached(`/api/events?type=${encodeURIComponent(params.type)}&period=${encodeURIComponent(params.period)}`
             + (params.live === "true" ? "&data=live" : "")),
      cached(`/api/events/next?type=${encodeURIComponent(params.type)}`),
      cached("/api/findings"),
    ]);
  } catch (err) {
    body.replaceChildren(h("p", { class: "error" }, err.message));
    return;
  }
  if (!current()) return;                                      // a newer render took over this pane
  const sort = sortState.get(container) || { key: "date", dir: "desc" };
  const pageCtx = {
    ...ctx,
    resort: (key) => {
      const cur = sortState.get(container) || { key: "date", dir: "desc" };
      sortState.set(container, { key, dir: cur.key === key && cur.dir === "desc" ? "asc" : "desc" });
      tableSlot.replaceChildren(eventTable(ev, pageCtx, sortState.get(container)));
      tableSlot.querySelector(`th[aria-sort]:not([aria-sort="none"]) button`)?.focus();
    },
  };
  const f1 = `${paneId}-fig1`, f2 = `${paneId}-fig2`;
  const rows = [...ev.data.events].sort((a, b) => (a.date < b.date ? 1 : -1));
  const tableSlot = h("div", {}, eventTable(ev, pageCtx, sort));
  body.replaceChildren(
    nextPanel(next),
    headline(ev),
    figureBox(f1, `Figure 1. Implied vs actual move over time, with the baseline (n ${ev.n} events)`,
      h("div", {}, table(["Date", "Type", "Implied (%)", "Actual (%)", "Baseline (%)"],
        rows.map((r) => [h("span", { class: "num" }, r.date), r.event_type, n(r.implied_move_pct), n(r.actual_move_pct), n(r.baseline_pct)])),
        source("event_moves", "event_premium"))),
    figureBox(f2, `Figure 2. Event premium per event, median marked (${ev.data.summaries.map((s) => `${s.event_type} n ${s.n}`).join(", ")})`,
      h("div", {}, table(["Type", "n", "Median premium (pp)", "Positive premium"],
        ev.data.summaries.map((s) => [TYPE_NAME[s.event_type], String(s.n), h("span", { class: "num" }, num(s.median_premium_pp, 3, true)),
                                      `${s.positive_premium_n} of ${s.n}`])),
        h("p", { class: "muted small" }, "Each event's premium is in the event table below."), source("event_premium"))),
    tableSlot,
    caveats(ev, findings),
    definitions(),
  );
  if (!ev.n) return;
  for (const [draw, id] of [[figure1, f1], [figure2, f2]]) {
    const el = container.querySelector(`#${id}`);
    try { await draw(el, ev); } catch (err) { chartFailed(el, err); }
  }
}
