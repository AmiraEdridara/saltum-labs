// The terminal's view state lives in the URL, so a copied link reopens the same view.
// Example: /terminal?a=event-study&a.type=CPI&a.period=both&b=vol-surface&b.d1=2026-09-10&b.d2=2026-09-11&split=1&tutor=1&active=b
// Vol surface also keeps view (both, d1, d2), same (vol scale), link (rotation) and pts (computed options).

const TYPES = ["all", "CPI", "NFP", "FOMC"];
const PERIODS = ["both", "2020-01..2023-05", "2023-06..now"];
const BOOL = ["false", "true"];
const DATE = /^\d{4}-\d{2}-\d{2}$/;

// Navigation order; the key is the shortcut. Params: allowed values (first is the default) or "date".
export const PAGES = [
  { id: "event-study", title: "Event study", key: "1", stage: "page 1", params: { type: TYPES, period: PERIODS, live: BOOL } },
  { id: "event-index", title: "Event index", key: "2", stage: "page 3",
    params: { type: TYPES, period: PERIODS, exclude_flagged: BOOL, include_ordinary: BOOL } },
  { id: "vol-surface", title: "Vol surface", key: "3", stage: "page 2",
    params: { d1: "date", d2: "date", view: ["both", "d1", "d2"], same: ["true", "false"], link: ["true", "false"],
              pts: ["false", "true"] } },
  { id: "help", title: "Help", key: "4", stage: "", params: {} },
  // The tutor is a panel (T), not a page; its saved sessions are listed inside the panel.
];
export const page = (id) => PAGES.find((p) => p.id === id) || PAGES[0];
export const PANES = ["a", "b"];
const DEFAULT_PAGE = { a: "event-study", b: "vol-surface" };

function cleanParams(pageId, raw) {
  const out = {};
  for (const [name, allowed] of Object.entries(page(pageId).params)) {
    const v = raw[name];
    if (allowed === "date") out[name] = v && DATE.test(v) ? v : "";
    else out[name] = allowed.includes(v) ? v : allowed[0];
  }
  return out;
}

export function parse(search, layout = {}) {
  const q = new URLSearchParams(search);
  const state = { panes: {}, split: false, tutor: false, active: "a" };
  for (const p of PANES) {
    const id = page(q.get(p) || DEFAULT_PAGE[p]).id;
    const raw = {};
    for (const [k, v] of q) if (k.startsWith(p + ".")) raw[k.slice(2)] = v;
    state.panes[p] = { page: id, params: cleanParams(id, raw) };
  }
  // The URL wins; without it, the user's saved layout decides whether Split view and the tutor are open.
  state.split = q.has("split") ? q.get("split") === "1" : Boolean(layout.split);
  state.tutor = q.has("tutor") ? q.get("tutor") === "1" : Boolean(layout.tutor);
  state.active = state.split && q.get("active") === "b" ? "b" : "a";
  return state;
}

export function serialize(state) {
  const q = new URLSearchParams();
  for (const p of PANES) {
    q.set(p, state.panes[p].page);
    for (const [k, v] of Object.entries(state.panes[p].params)) if (v !== "") q.set(`${p}.${k}`, v);
  }
  q.set("split", state.split ? "1" : "0");
  q.set("tutor", state.tutor ? "1" : "0");
  q.set("active", state.active);
  return "?" + q.toString();
}

export function withPage(state, pane, id) {
  const next = structuredClone(state);
  // Filters the new page also has (type, period) carry over; the rest start at their defaults.
  next.panes[pane] = { page: id, params: cleanParams(id, state.panes[pane].params) };
  return next;
}

export function withParams(state, pane, params) {
  const next = structuredClone(state);
  const p = next.panes[pane];
  p.params = cleanParams(p.page, { ...p.params, ...params });
  return next;
}
