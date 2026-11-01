// Page 3: the Event index (docs/prd.md). One table of every event, and the Stage 5b summary of which skew
// effects held up against ordinary days. Every number comes from GET /events/index; per-day flags come from
// GET /dates. The browser only formats and sorts.
import { cached, h, num, plain, renderTicket, source } from "../lib.js";

const TYPE_NAME = { CPI: "CPI", NFP: "Payrolls (NFP)", FOMC: "FOMC", "CPI+FOMC": "CPI+FOMC", ordinary: "ordinary" };
const NO_MOVE = "no same-day SPXW expiry, so no implied move";

// The summary's cells in the format of data/stage5b_summary.txt (stage5b_controls.fmt_compare).
const signed = (v) => (v === null || v === undefined ? "–" : `${v > 0 ? "+" : ""}${v.toFixed(3)}`).replace("+-", "-");
const fmt = {
  median: (r) => signed(r.median_volpts),
  share: (r) => `${Math.round(r.share_positive_pct)}%`,
  diff: (r) => (r.event_minus_ordinary_volpts === null ? "–" : signed(r.event_minus_ordinary_volpts)),
  ci: (r) => (r.ci90_volpts[0] === null ? "–" : `[${signed(r.ci90_volpts[0])}, ${signed(r.ci90_volpts[1])}]`),
  survives: (r) => (r.survives_both_controls === null ? "not tested" : r.survives_both_controls ? "Yes" : "No"),
};

// The status file stores dates as Python wrote them, e.g. "[datetime.date(2022, 4, 26)]"; show them as 2022-04-26.
const pad = (n) => String(n).padStart(2, "0");
const readable = (s) => s.replace(/\[?datetime\.date\((\d{4}), (\d{1,2}), (\d{1,2})\)\]?/g, (_, y, m, d) => `${y}-${pad(m)}-${pad(d)}`);

function failed(what, err) {
  return h("p", { class: "error table-failed" }, `The ${what} could not be shown: ${err?.message || err}. `
    + "If the code was updated recently, restart both servers.");
}

// ------------------------------------------------------------ summary (Stage 5b)
function summary(ix) {
  const d = ix.data;
  const box = h("section", { class: "ix-summary", "aria-labelledby": "ix-summary-title" },
    h("h2", { id: "ix-summary-title" }, plain("Summary: event days against ordinary days (Stage 5b)", "Summary: event days against ordinary days")));
  if (!d.summary.length) {
    box.append(h("p", { class: "note summary-note" }, d.summary_note));
    return box;
  }
  const measures = [...new Set(d.summary.map((r) => r.measure))];
  const head = h("tr", {}, ["Measure", "Group", "n", "Median change", "Share > 0", "Event minus ordinary", "90% interval", "Survives both controls"]
    .map((t, i) => h("th", { scope: "col", class: i > 1 ? "r" : "" }, t)));
  const body = [];
  for (const m of measures) {
    d.summary.filter((r) => r.measure === m).forEach((r, i) => body.push(h("tr", { class: i ? "" : "group-start", "data-measure": r.measure, "data-group": r.group },
      i ? h("td", {}, "") : h("th", { scope: "row" }, r.measure), h("td", {}, r.group),
      h("td", { class: "r num" }, String(r.n)), h("td", { class: "r num" }, fmt.median(r)), h("td", { class: "r num" }, fmt.share(r)),
      h("td", { class: "r num" }, fmt.diff(r)), h("td", { class: "r num" }, fmt.ci(r)),
      h("td", { class: "r survives" }, fmt.survives(r)))));
  }
  box.append(h("p", { class: "muted small" }, d.summary_units),
             h("div", { class: "table-wrap" }, h("table", { class: "data summary" }, h("thead", {}, head), h("tbody", {}, body))),
             h("p", { class: "muted small" }, "Survives both controls: the 90% interval of event minus ordinary excludes 0, "
               + "with and without flagged dates. The 50-delta ATM and the ordinary rows are not part of that test."));
  return box;
}

// ------------------------------------------------------------ event table
function flagText(r, byDate) {
  if (r.missing) return "";
  const days = [[r.day_before, "day before"], [r.event_date, r.event_type === "ordinary" ? "day 2" : "event day"]]
    .filter(([d]) => byDate.get(d)?.flagged).map(([, t]) => t);
  if (r.event_type === "ordinary" && days.includes("day before")) days[days.indexOf("day before")] = "day 1";
  return days.length ? `⚑ ${days.join(", ")}` : "";
}

const COLUMNS = [
  { key: "event_date", label: "Date", cell: (r) => (r.live ? [r.event_date, " ", h("span", { class: "live-badge", title: "Added by the daily update; not in findings_v1 or the summary" }, "live")] : r.event_date) },
  { key: "event_type", label: "Type", cell: (r) => TYPE_NAME[r.event_type] || r.event_type },
  { key: "days_drawn", label: "Days drawn", r: true, cell: (r) => `${r.days_drawn} of 2` },
  { key: "flag", label: "Flags", cell: (r) => r.flag },
  { key: "atm30_before", label: "30-day ATM before (%)", r: true, cell: (r) => num(r.atm30_before) },
  { key: "atm30_after", label: "30-day ATM after (%)", r: true, cell: (r) => num(r.atm30_after) },
  { key: "d_atm30_fixed_strike", label: "Fixed-strike ATM change", r: true, cell: (r) => num(r.d_atm30_fixed_strike, 3, true) },
  { key: "d_rr25_30", label: "RR25 change", r: true, cell: (r) => num(r.d_rr25_30, 3, true) },
  { key: "implied_move_pct", label: "Implied move (%)", r: true, cell: (r) => num(r.implied_move_pct) },
  { key: "actual_move_pct", label: "Actual move (%)", r: true, cell: (r) => num(r.actual_move_pct) },
  { key: "premium_pp", label: "Premium (pp)", r: true, cell: (r) => num(r.premium_pp, 3, true) },
];

function compare(key, dir) {
  return (a, b) => {
    const x = a[key], y = b[key];
    if (x === y) return a.event_date < b.event_date ? 1 : -1;                // ties: newest first
    if (x === null || x === undefined || x === "") return 1;                // blanks last, either direction
    if (y === null || y === undefined || y === "") return -1;
    return (x < y ? -1 : 1) * (dir === "asc" ? 1 : -1);
  };
}

function eventTable(rows, sort, ctx) {
  const sorted = [...rows].sort(compare(sort.key, sort.dir));
  const head = h("tr", {}, COLUMNS.map((c) => h("th", {
    scope: "col", class: c.r ? "r" : "", "aria-sort": sort.key === c.key ? (sort.dir === "asc" ? "ascending" : "descending") : "none",
  }, h("button", { type: "button", class: "sort", "data-sort": c.key, onclick: () => ctx.resort(c.key) }, c.label,
       sort.key === c.key ? (sort.dir === "asc" ? " ▲" : " ▼") : ""))),
    h("th", { scope: "col" }, "Vol surface"));
  const body = sorted.map((r) => {
    const open = (e) => { if (e.ctrlKey || e.metaKey || e.shiftKey) return; e.preventDefault(); ctx.openSurface(r.day_before, r.event_date); };
    const cells = r.missing
      ? [h("th", { scope: "row", class: "num" }, r.event_date), h("td", {}, TYPE_NAME[r.event_type] || r.event_type),
         h("td", { class: "r num" }, `${r.days_drawn} of 2`),
         h("td", { colspan: String(COLUMNS.length - 3), class: "missing-reason" }, `Missing day: ${readable(r.reason)}`)]
      : COLUMNS.map((c, i) => {
          const note = !r.missing && r.event_type !== "ordinary" && c.key.endsWith("_pct") && r[c.key] === null ? NO_MOVE
            : c.key === "premium_pp" && r.event_type !== "ordinary" && r.premium_pp === null ? NO_MOVE : null;
          return i ? h("td", { class: (c.r ? "r num" : "") + (c.key === "flag" && r.flag ? " flagcell" : ""), title: note, "data-key": c.key }, c.cell(r))
                   : h("th", { scope: "row", class: "num" }, c.cell(r));
        });
    const link = r.days_drawn === 2
      ? h("td", {}, h("a", { href: ctx.surfaceHref(r.day_before, r.event_date), onclick: open }, "Open"))
      : h("td", { class: "muted" }, "–");
    return h("tr", { class: r.missing ? "missing" : "clickable", "data-date": r.event_date, "data-type": r.event_type,
                     onclick: r.days_drawn === 2 ? (e) => { if (!e.target.closest("a, button")) open(e); } : null }, cells, link);
  });
  return h("div", { class: "table-wrap" }, h("table", { class: "data events index" }, h("thead", {}, head), h("tbody", {}, body)));
}

// ------------------------------------------------------------ the page
const sortState = new WeakMap();

export async function render(container, paneId, params, ctx) {
  const current = renderTicket(container);
  const body = h("div", { class: "event-index" }, h("p", { class: "muted" }, "Loading…"));
  container.append(body);
  const q = new URLSearchParams({ type: params.type, period: params.period, exclude_flagged: params.exclude_flagged,
                                  include_ordinary: params.include_ordinary });
  let ix, dates;
  try {
    [ix, dates] = await Promise.all([cached(`/api/events/index?${q}`), cached("/api/dates")]);
  } catch (err) {
    if (!current()) return;
    body.replaceChildren(
      h("section", { class: "ix-summary" }, h("h2", {}, "Summary: event days against ordinary days (Stage 5b)"), failed("summary", err)),
      h("section", {}, h("h2", {}, "Event table"), failed("event table", err)));
    return;
  }
  if (!current()) return;
  const byDate = new Map(dates.data.dates.map((d) => [d.date, d]));
  const rows = [...ix.data.events.map((r) => ({ ...r })), ...ix.data.missing.map((r) => ({ ...r, missing: true }))];
  rows.forEach((r) => { r.flag = flagText(r, byDate); });
  const sort = () => sortState.get(container) || { key: "event_date", dir: "desc" };
  const slot = h("div", {});
  const pageCtx = {
    ...ctx,
    resort: (key) => {
      const s = sort();
      sortState.set(container, { key, dir: s.key === key && s.dir === "desc" ? "asc" : "desc" });
      slot.replaceChildren(eventTable(rows, sort(), pageCtx));
      slot.querySelector(`button[data-sort="${key}"]`)?.focus();
    },
  };
  slot.append(eventTable(rows, sort(), pageCtx));
  const nEvents = ix.data.events.filter((r) => r.event_type !== "ordinary").length;
  const nOrdinary = ix.n - nEvents;
  body.replaceChildren(
    summary(ix),
    source("event_skew_changes", "event_fixed_strike", "control_pair_changes"),
    h("section", { "aria-labelledby": "ix-table-title" },
      h("h2", { id: "ix-table-title" }, `Event table (n ${ix.n}${nOrdinary ? `: ${nEvents} events, ${nOrdinary} ordinary pairs` : ""})`),
      h("p", { class: "muted small" }, "Click a row, or its Open link, for the two-day Vol surface page. Click a column heading to sort. "
        + "Changes are in vol points, from the day before to the event day."),
      slot,
      h("p", { class: "note" }, ix.data.missing.length
        ? `${ix.data.missing.length} events in this selection have a missing day; the reason is in their row (data/all_dates_status.csv).`
        : "No event in this selection has a missing day."),
      source(...ix.source)),
    h("section", { class: "caveats", "aria-labelledby": "ix-caveats-title" }, h("h2", { id: "ix-caveats-title" }, "Caveats"),
      h("p", {}, ix.caveat)));
}
