// Pages 1 to 4 and Help are placeholders for now. Each shows its controls (which write to the URL) and
// the state it would load, so the frame, Split view and links can be checked before the pages are built.
import { render as renderEventStudy } from "./pages/event_study.js";
import { render as renderVolSurface } from "./pages/vol_surface.js";
import { render as renderEventIndex } from "./pages/event_index.js";
import { PREVIEW } from "./api.js";
import { PAGES, page } from "./state.js";

const LABELS = { type: "Event type", period: "Period", exclude_flagged: "Exclude flagged days",
                 include_ordinary: "Show ordinary pairs", d1: "Date 1", d2: "Date 2",
                 live: "Include live data (after {v1}) in the medians" };
const OPTION_TEXT = { all: "All (side by side)", NFP: "Payrolls (NFP)", both: "Both periods",
                      "2020-01..2023-05": "2020-01 to 2023-05", "2023-06..now": "2023-06 to now",
                      true: "Yes", false: "No" };

function el(tag, attrs = {}, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v);
  }
  for (const c of children) e.append(c);
  return e;
}

function control(name, allowed, value, onChange, ctx) {
  if (allowed === "date") {
    const input = el("input", { type: "date", value, min: ctx.dates.first || "", max: ctx.dates.last || "",
                                onchange: (e) => onChange({ [name]: e.target.value }) });
    return el("label", {}, LABELS[name], input);
  }
  const select = el("select", { onchange: (e) => onChange({ [name]: e.target.value }) });
  for (const v of allowed) {
    const o = el("option", { value: v }, OPTION_TEXT[v] || v);
    if (v === value) o.selected = true;
    select.append(o);
  }
  return el("label", {}, LABELS[name].replace("{v1}", ctx.dates.v1End || "…"), select);
}

function dayNote(date, ctx) {
  if (!date) return "not set";
  const d = ctx.dates.byDate.get(date);
  if (!d) return ctx.dates.loaded ? "no surface for this date (not a drawn trading day)" : "";
  const what = d.day_type === "ordinary" ? "ordinary day" : d.day_type;
  const ev = [d.events, d.events_next_day && `${d.events_next_day} next day`].filter(Boolean).join(", ");
  return `${what}${ev ? ` (${ev})` : ""}${d.flagged ? ", flagged" : ""}`;
}

export function renderPage(container, paneId, paneState, ctx) {
  const p = page(paneState.page);
  container.replaceChildren();
  container.append(el("div", { class: "pane-head" }, el("h1", {}, p.title),
                      el("span", { class: "tag" }, ctx.split ? `Pane ${paneId.toUpperCase()}` : "")));

  if (p.id === "help") {
    container.append(el("p", {}, "Every page shows the source table, n and the main caveat beside each number. "
                                  + "Saltum Labs is for education only and is not advice."));
    container.append(el("h2", {}, "Keyboard shortcuts"), ctx.keysTable());
    return;
  }

  if (p.id === "vol-surface") {                // has its own two-date picker and options
    renderVolSurface(container, paneId, paneState.params, ctx);
    return;
  }
  const names = Object.keys(p.params).filter((n) => !(PREVIEW && n === "live"));    // the sample has no live rows
  if (names.length) {
    const row = el("div", { class: "controls-row" });
    for (const n of names) row.append(control(n, p.params[n], paneState.params[n], (v) => ctx.setParams(paneId, v), ctx));
    container.append(row);
  }
  if (p.id === "event-study") {
    renderEventStudy(container, paneId, paneState.params, ctx);
    return;
  }
  if (p.id === "event-index") {
    renderEventIndex(container, paneId, paneState.params, ctx);
    return;
  }
  if (p.id === "vol-surface") {
    const dl = el("dl", { class: "params" });
    for (const n of ["d1", "d2"]) dl.append(el("dt", {}, LABELS[n]), el("dd", {}, dayNote(paneState.params[n], ctx)));
    container.append(dl);
  }

  const text = {
    "event-study": "Implied vs actual moves for CPI, FOMC and payrolls days, the event premium, and the event table.",
    "event-index": "Every event in one table, with the Stage 5b summary of which skew effects held up against ordinary days.",
    "vol-surface": "Two days side by side: surface, smiles and term structure, with the skew numbers. Use [ and ] to move between dates.",
  }[p.id];
  container.append(el("div", { class: "placeholder" }, el("p", {}, text),
                      el("p", { class: "muted" }, `Placeholder: built in ${p.stage} of the build order.`)));

  const dl = el("dl", { class: "params" });
  dl.append(el("dt", {}, "page"), el("dd", {}, p.id));
  for (const [k, v] of Object.entries(paneState.params)) dl.append(el("dt", {}, k), el("dd", {}, v || "—"));
  container.append(el("h3", {}, "State held in the URL"), dl);
}

export { PAGES };
