// The terminal frame (docs/prd.md, Page 5): top bar, navigation, one or two panes, tutor panel, footer.
import { PREVIEW, get, send } from "./api.js";
import { cached } from "./lib.js";
import { initTutor } from "./tutor.js";
import { renderPage } from "./pages.js";
import { PAGES, PANES, page, parse, serialize, withPage, withParams } from "./state.js";

const $ = (id) => document.getElementById(id);
const KEYS = [
  ...PAGES.map((p) => [p.key, `Open ${p.title} in the active pane`]),
  ["S", "Toggle Split view"], ["T", "Open or close the tutor"], ["P", "Switch the active pane (Split view)"],
  ["[", "Previous date (Vol surface)"], ["]", "Next date (Vol surface)"], ["?", "Show these shortcuts"],
];

const BASE = PREVIEW ? "/preview" : "/terminal";    // the homepage's preview is this same page, on sample data
let state;
let me;
const shownPage = {};
const dates = { loaded: false, list: [], byDate: new Map(), first: "", last: "" };

function status(text) { $("status").textContent = text; }

function go(next, { push = true, panes = true } = {}) {
  const layoutChanged = !state || next.split !== state.split || next.tutor !== state.tutor;
  state = next;
  const url = BASE + serialize(state);
  if (push && !PREVIEW) history.pushState(null, "", url); else history.replaceState(null, "", url);
  render({ panes });
  if (layoutChanged && me) send("PUT", "/account/layout", { split: state.split, tutor: state.tutor }).catch(() => {});
}

function keysTable() {
  const t = document.createElement("table");
  t.className = "keys";
  const body = document.createElement("tbody");
  for (const [k, what] of KEYS) {
    const tr = document.createElement("tr");
    const a = document.createElement("td"), b = document.createElement("td"), kbd = document.createElement("kbd");
    kbd.textContent = k; a.append(kbd); b.textContent = what; tr.append(a, b); body.append(tr);
  }
  t.append(body);
  return t;
}

function render({ panes = true } = {}) {
  // Navigation: links are real URLs (open in a new tab works), clicks stay in the page.
  const list = $("nav-list");
  list.replaceChildren();
  for (const p of PAGES) {
    const a = document.createElement("a");
    a.href = BASE + serialize(withPage(state, state.active, p.id));
    a.textContent = p.title;
    const kbd = document.createElement("kbd");
    kbd.textContent = p.key;
    a.append(kbd);
    if (state.panes[state.active].page === p.id) a.setAttribute("aria-current", "page");
    a.addEventListener("click", (e) => {
      if (e.ctrlKey || e.metaKey || e.shiftKey || e.button !== 0) return;
      e.preventDefault();
      go(openPage(state.active, p.id));
    });
    const li = document.createElement("li");
    li.append(a);
    list.append(li);
  }

  $("main").classList.toggle("split", state.split);
  $("workspace").classList.toggle("with-tutor", state.tutor);
  $("tutor").classList.toggle("hidden", !state.tutor);
  $("toggle-split").setAttribute("aria-pressed", String(state.split));
  $("toggle-tutor").setAttribute("aria-pressed", String(state.tutor));

  const ctx = { dates, split: state.split, keysTable, setParams: (pane, v) => go(withParams(state, pane, v)),
                surfaceHref: (d1, d2) => BASE + serialize(surfaceState(d1, d2)),
                openSurface: (d1, d2) => go(surfaceState(d1, d2)) };
  for (const p of PANES) {
    const node = $(`pane-${p}`);
    const visible = p === "a" || state.split;
    node.classList.toggle("hidden", !visible);
    node.classList.toggle("active", state.active === p);
    if (visible && panes) renderPage(node, p, state.panes[p], ctx);
    if (visible && panes && shownPage[p] !== state.panes[p].page) {     // a page swap, not a filter change
      if (shownPage[p]) { node.classList.remove("swap"); void node.offsetWidth; node.classList.add("swap"); }
      shownPage[p] = state.panes[p].page;
    }
  }

  // The tutor sees the active pane.
  const tc = $("tutor-context");
  tc.replaceChildren();
  const pane = state.panes[state.active];
  const rows = [["pane", state.split ? state.active.toUpperCase() : "A"], ["page", page(pane.page).title],
                ...Object.entries(pane.params).map(([k, v]) => [k, v || "—"])];
  for (const [k, v] of rows) {
    const dt = document.createElement("dt"), dd = document.createElement("dd");
    dt.textContent = k; dd.textContent = v; tc.append(dt, dd);
  }
  document.title = `Saltum Labs · ${page(pane.page).title}`;
}

// Switch a pane to a page; a Vol surface page without dates starts on the latest event pair.
function openPage(pane, id) {
  let next = withPage(state, pane, id);
  if (id === "vol-surface" && !next.panes[pane].params.d2 && dates.latestPair) next = withParams(next, pane, dates.latestPair);
  return next;
}

// An event's two-day vol surface page: in Split view it opens in the other pane, so the study stays in view.
function surfaceState(d1, d2) {
  const target = state.split ? (state.active === "a" ? "b" : "a") : state.active;
  return withParams(withPage(state, target, "vol-surface"), target, { d1, d2 });
}

// ------------------------------------------------------------ dates ([ and ] on the Vol surface page)
function moveDate(step) {
  const pane = state.panes[state.active];
  if (pane.page !== "vol-surface") return status("[ and ] move between dates on the Vol surface page.");
  if (!dates.loaded) return status("Dates are not loaded yet.");
  const list = dates.list;
  const at = list.indexOf(pane.params.d2);
  const i = at < 0 ? list.length - 1 : Math.min(Math.max(at + step, 1), list.length - 1);
  // Date 2 moves one drawn date; date 1 becomes the drawn date before it (a "day before / day of" pair).
  go(withParams(state, state.active, { d2: list[i], d1: list[i - 1] }));
  status(`Dates ${list[i - 1]} and ${list[i]}`);
}

// ------------------------------------------------------------ keyboard
function typing(t) {
  return t instanceof HTMLElement && (t.isContentEditable || ["INPUT", "SELECT", "TEXTAREA"].includes(t.tagName));
}

document.addEventListener("keydown", (e) => {
  if (e.ctrlKey || e.metaKey || e.altKey || typing(e.target) || document.querySelector("dialog[open]")) return;
  const k = e.key;
  const p = PAGES.find((x) => x.key === k);
  if (p) go(openPage(state.active, p.id));
  else if (k === "s" || k === "S") go({ ...state, split: !state.split, active: state.split ? "a" : state.active });
  else if (k === "t" || k === "T") go({ ...state, tutor: !state.tutor });
  else if (k === "p" || k === "P") {
    if (!state.split) return status("Split view is off, so there is one pane.");
    go({ ...state, active: state.active === "a" ? "b" : "a" }, { push: false, panes: false });
    status(`Active pane: ${state.active.toUpperCase()}`);
  } else if (k === "[") moveDate(-1);
  else if (k === "]") moveDate(1);
  else if (k === "?") $("keys-dialog").showModal();
  else return;
  e.preventDefault();
});

// ------------------------------------------------------------ wiring
// ------------------------------------------------------------ tutor panel width (drag the left edge, or arrow keys)
// The panel is at least 280 px and at most 60% of the workspace, and the page keeps at least 360 px beside the
// navigation. The width is remembered per user with the saved layout. At phone width the tutor is full screen.
const TUTOR_MIN = 280, TUTOR_DEFAULT = 336, PAGE_MIN = 360, TUTOR_MAX_SHARE = 0.6;
let tutorWidth = TUTOR_DEFAULT, saveTimer = null, reflowTimer = null;

function tutorMax() {
  const ws = $("workspace").clientWidth, nav = document.querySelector(".nav").offsetWidth;
  return Math.max(TUTOR_MIN, Math.floor(Math.min(ws * TUTOR_MAX_SHARE, ws - nav - PAGE_MIN)));
}

function applyTutorWidth(w, { save = false } = {}) {
  const max = tutorMax();
  tutorWidth = Math.round(Math.min(Math.max(w, TUTOR_MIN), max));
  $("workspace").style.setProperty("--tutor-w", `${tutorWidth}px`);
  const handle = $("tutor-resize");
  handle.setAttribute("aria-valuemin", String(TUTOR_MIN));
  handle.setAttribute("aria-valuemax", String(max));
  handle.setAttribute("aria-valuenow", String(tutorWidth));
  handle.setAttribute("aria-valuetext", `Tutor panel ${tutorWidth} pixels wide`);
  clearTimeout(reflowTimer);                       // let the charts in the panes fit their new width
  reflowTimer = setTimeout(() => window.dispatchEvent(new Event("resize")), 150);
  if (save) {
    clearTimeout(saveTimer);
    saveTimer = setTimeout(() => send("PUT", "/account/layout", { tutor_width: tutorWidth }).catch(() => {}), 400);
  }
}

function wireTutorResize() {
  const handle = $("tutor-resize");
  let start = null;
  handle.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    start = { x: e.clientX, w: tutorWidth };
    try { handle.setPointerCapture(e.pointerId); } catch { /* synthetic events have no capture */ }
    document.body.classList.add("resizing");
    e.preventDefault();
  });
  handle.addEventListener("pointermove", (e) => {
    if (start) applyTutorWidth(start.w + (start.x - e.clientX));    // the panel is on the right: drag left to widen
  });
  const end = () => {
    if (!start) return;
    start = null;
    document.body.classList.remove("resizing");
    applyTutorWidth(tutorWidth, { save: true });
  };
  handle.addEventListener("pointerup", end);
  handle.addEventListener("pointercancel", end);
  handle.addEventListener("keydown", (e) => {
    const step = e.shiftKey ? 64 : 16;
    const to = { ArrowLeft: tutorWidth + step, ArrowRight: tutorWidth - step, Home: TUTOR_MIN, End: tutorMax() }[e.key];
    if (to === undefined) return;
    e.preventDefault();
    e.stopPropagation();
    applyTutorWidth(to, { save: true });
  });
  window.addEventListener("resize", (e) => { if (e.isTrusted) applyTutorWidth(tutorWidth); });   // keep it inside the window
}

function wire() {
  wireTutorResize();
  for (const p of PANES) $(`pane-${p}`).addEventListener("pointerdown", () => {
    // Only the frame is redrawn, so a control clicked in the inactive pane keeps working.
    if (state.split && state.active !== p) go({ ...state, active: p }, { push: false, panes: false });
  });
  $("toggle-split").addEventListener("click", () => go({ ...state, split: !state.split, active: state.split ? "a" : state.active }));
  $("toggle-tutor").addEventListener("click", () => go({ ...state, tutor: !state.tutor }));
  $("tutor-close").addEventListener("click", () => go({ ...state, tutor: false }));
  $("show-keys").addEventListener("click", () => $("keys-dialog").showModal());
  $("keys-body").replaceWith(Object.assign(keysTable().tBodies[0], { id: "keys-body" }));
  window.addEventListener("popstate", () => { state = parse(location.search, me?.layout); render(); });

  const menu = $("account-menu"), button = $("account-button");
  button.addEventListener("click", () => {
    const open = menu.classList.toggle("hidden") === false;
    button.setAttribute("aria-expanded", String(open));
  });
  document.addEventListener("click", (e) => {
    if (!e.target.closest(".account")) { menu.classList.add("hidden"); button.setAttribute("aria-expanded", "false"); }
  });
  $("logout").addEventListener("click", async () => {
    await send("POST", "/auth/logout").catch(() => {});
    location.assign("/login");
  });
  $("delete-open").addEventListener("click", () => $("delete-dialog").showModal());
  $("delete-cancel").addEventListener("click", () => $("delete-dialog").close());
  $("delete-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      await send("DELETE", "/account", { confirm_email: new FormData(e.target).get("confirm_email") });
      location.assign("/login");
    } catch (err) {
      $("delete-error").textContent = err.message;
    }
  });
}

// The daily update's last result: a red banner if it failed or the data is more than 2 trading days behind.
async function checkUpdate(dataTo) {
  let s;
  try { s = await get("/status/update"); } catch { return; }
  if (!s.configured) return;
  const banner = $("update-banner");
  const behind = (() => {                             // weekdays between the latest data and today
    let n = 0;
    for (let d = new Date(dataTo + "T12:00:00Z"); d < new Date(); d.setUTCDate(d.getUTCDate() + 1)) {
      if (d.getUTCDay() % 6 !== 0) n++;
    }
    return n - 1;
  })();
  if (s.ok === false) {
    banner.textContent = `Last daily update failed: ${s.error}. See data/live/update_log.txt.`;
  } else if (behind > 2) {
    banner.textContent = `The data is ${behind} trading days behind (to ${dataTo}): the daily update has not run. `
      + "See data/live/update_log.txt.";
  } else return;
  banner.classList.remove("hidden");
}

// The latest drawn event day whose previous drawn date is its day before.
function latestEventPair(list) {
  for (let i = list.length - 1; i > 0; i--) {
    if (list[i].events && list[i - 1].events_next_day) return { d1: list[i - 1].date, d2: list[i].date };
  }
  return { d1: list[list.length - 2].date, d2: list[list.length - 1].date };
}

function fillFooter(lines) {
  $("footer-notes").replaceChildren(...lines.map((text) => Object.assign(document.createElement("span"), { textContent: text })));
}

async function start() {
  if (PREVIEW) {
    document.querySelector(".brand").target = "_top";
    document.querySelector(".account").classList.add("hidden");
    document.querySelector(".v1-note").classList.add("hidden");
    document.body.classList.add("is-preview");
    $("sample-tag").classList.remove("hidden");
  }
  me = await get("/account/me");
  fillFooter(me.footer);
  $("account-button").textContent = me.email;
  $("acct-email").textContent = me.email;
  $("acct-plan").textContent = me.plan === "tutor_plus" ? "Tutor plus" : "Student (free)";
  const used = me.tutor_day === new Date().toISOString().slice(0, 10) ? me.tutor_msgs_today : 0;
  $("acct-tutor").textContent = me.plan === "tutor_plus"
    ? `${me.tutor_msgs_month} of ${me.limits.tutor_plus_monthly} this month`
    : `${used} of ${me.limits.student_daily}`;
  $("acct-provider").textContent = me.provider === "dev" ? "local dev login" : me.provider;

  state = parse(PREVIEW ? "" : location.search, me.layout);    // a reload hands the frame its last URL; the preview starts fresh
  wire();
  applyTutorWidth(Number.isInteger(me.layout.tutor_width) ? me.layout.tutor_width : TUTOR_DEFAULT);
  // The tutor sees the active pane: its page and the URL parameters (dates and filters).
  initTutor({ context: () => ({ page: state.panes[state.active].page, params: state.panes[state.active].params }) });
  go(state, { push: false });

  try {
    const d = await cached("/api/dates");          // shared with the pages, so it is fetched once
    dates.list = d.data.dates.map((x) => x.date);
    for (const x of d.data.dates) dates.byDate.set(x.date, x);
    dates.first = dates.list[0];
    dates.last = dates.list[dates.list.length - 1];
    dates.v1End = d.v1_end;
    dates.loaded = true;
    $("latest-close").textContent = dates.last;
    $("v1-end").textContent = d.v1_end;
    checkUpdate(dates.last);
    // Vol surface panes without dates start on the latest event pair: the day before and the event day.
    dates.latestPair = latestEventPair(d.data.dates);
    let next = state;
    for (const p of PANES) {
      if (next.panes[p].page === "vol-surface" && !next.panes[p].params.d2) next = withParams(next, p, dates.latestPair);
    }
    go(next, { push: false });
  } catch (err) {
    $("latest-close").textContent = "unavailable";
    status(err.message);
  }
}

start();
