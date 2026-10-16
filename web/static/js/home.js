// The homepage: one request to /home/data (frozen v1 summaries, public), then every number on the page is filled
// from it. Elements with data-src name the field they show; tests/web/test_home_page.py checks each against the API.
// The tutor demo (home_demo.js) is a script: it makes no requests.
import { DEMOS } from "./home_demo.js";

const $ = (id) => document.getElementById(id);
const SVG = "http://www.w3.org/2000/svg";
const reduced = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

// ---------------------------------------------------------------- formatting (mirrored in the test)
const MINUS = "−";
const signed = (x, d) => (x < 0 ? MINUS : "+") + Math.abs(x).toFixed(d);
const FMT = {
  text: (x) => String(x),
  int: (x) => Number(x).toLocaleString("en-GB"),
  pp3: (x) => signed(x, 3),
  vp3: (x) => signed(x, 3),
  pct3: (x) => Number(x).toFixed(3),
  iv1: (x) => Number(x).toFixed(1),
};
const pick = (data, path) => path.split(".").reduce((o, k) => (o == null ? o : o[k]), data);

function num(path, fmt, tag = "span") {
  const el = tag === "tspan" ? document.createElementNS(SVG, "tspan") : document.createElement(tag);
  el.setAttribute("data-src", path);
  el.setAttribute("data-fmt", fmt);
  if (tag === "span") el.className = "num";
  return el;
}

function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  for (const k of kids.flat()) if (k != null) el.append(k);
  return el;
}

function s(tag, attrs = {}, ...kids) {
  const el = document.createElementNS(SVG, tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  for (const k of kids.flat()) if (k != null) el.append(k);
  return el;
}

function fill(data) {
  for (const el of document.querySelectorAll("[data-src]")) {
    const v = pick(data, el.dataset.src);
    el.textContent = v == null ? "n/a" : FMT[el.dataset.fmt](v);
  }
}

// ---------------------------------------------------------------- tour previews
function studyChart(data) {
  const rows = data.event_study;
  const W = 420, row = 64, top = 30, left = 84, right = 64, H = top + rows.length * row + 8;
  const max = Math.max(...rows.flatMap((r) => [r.median_implied_pct, r.median_actual_pct]));
  const x = (v) => left + (v / max) * (W - left - right);
  const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Median implied and actual move by event type" });
  svg.append(s("rect", { x: left, y: 6, width: 12, height: 12, class: "bar-implied" }), s("text", { x: left + 18, y: 17, class: "legend" }, "Implied"),
             s("rect", { x: left + 100, y: 6, width: 12, height: 12, class: "bar-actual" }), s("text", { x: left + 118, y: 17, class: "legend" }, "Actual"));
  rows.forEach((r, i) => {
    const y = top + i * row;
    svg.append(s("text", { x: 0, y: y + 22, class: "row-label" }, r.type),
               s("text", { x: 0, y: y + 42, class: "n" }, "n ", num(`event_study.${i}.n`, "int", "tspan")));
    for (const [j, key, cls] of [[0, "median_implied_pct", "bar-implied"], [1, "median_actual_pct", "bar-actual"]]) {
      const yy = y + 4 + j * 25;
      svg.append(s("rect", { x: left, y: yy, width: x(r[key]) - left, height: 21, class: cls }),
                 s("text", { x: x(r[key]) + 5, y: yy + 16, class: "value" }, num(`event_study.${i}.${key}`, "pct3", "tspan"), "%"));
    }
  });
  $("preview-study").append(svg);
}

function surfaceChart(data) {
  const sf = data.surface;
  const all = [...sf.before, ...sf.after];
  const lx = (d) => Math.log(Math.max(d, 1));
  const [x0, x1] = [Math.min(...all.map((p) => lx(p.dte))), Math.max(...all.map((p) => lx(p.dte)))];
  const [y0, y1] = [Math.floor(Math.min(...all.map((p) => p.atm_iv_pct))), Math.ceil(Math.max(...all.map((p) => p.atm_iv_pct)))];
  const W = 260, H = 170, L = 30, R = 8, T = 10, B = 26;
  const X = (d) => L + ((lx(d) - x0) / (x1 - x0)) * (W - L - R);
  const Y = (v) => T + (1 - (v - y0) / (y1 - y0)) * (H - T - B);
  const line = (pts) => pts.map((p, i) => `${i ? "L" : "M"}${X(p.dte).toFixed(1)},${Y(p.atm_iv_pct).toFixed(1)}`).join("");
  for (const [key, other, title] of [["before", "after", "Day before"], ["after", "before", "CPI day"]]) {
    const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": `At-the-money implied vol by days to expiry, ${title}` });
    for (const v of [y0, Math.round((y0 + y1) / 2), y1]) {
      svg.append(s("line", { x1: L, x2: W - R, y1: Y(v), y2: Y(v), class: "grid" }),
                 s("text", { x: L - 4, y: Y(v) + 4, class: "tick", "text-anchor": "end", "data-tick": "" }, `${v}`));
    }
    for (const d of [7, 30, 90, 365].filter((d) => lx(d) >= x0 && lx(d) <= x1)) {
      svg.append(s("text", { x: X(d), y: H - 8, class: "tick", "text-anchor": "middle", "data-tick": "" }, `${d}d`));
    }
    svg.append(s("path", { d: line(sf[other]), class: "line-other" }), s("path", { d: line(sf[key]), class: "line-main" }));
    const head = h("figcaption", {}, h("span", { class: "panel-title" }, `${title} `), h("span", { class: "num", "data-src": `surface.${key === "before" ? "day_before" : "event_date"}`, "data-fmt": "text" }),
                   " · shortest expiry ", num(`surface.${key}.0.atm_iv_pct`, "iv1"), "%");
    $("preview-surface").append(h("div", { class: "mini" }, head, svg));
  }
  $("surface-cap").append("At-the-money implied vol, %, by days to expiry (log scale). The dashed line is the other day.");
  $("open-surface").href = `/terminal?a=vol-surface&a.d1=${sf.day_before}&a.d2=${sf.event_date}&split=0`;
}

function indexTable(data) {
  const cell = (i, k, f) => h("td", {}, num(`index_rows.${i}.${k}`, f));
  const body = data.index_rows.map((r, i) => h("tr", {}, h("td", {}, num(`index_rows.${i}.event_date`, "text")),
    h("td", {}, num(`index_rows.${i}.event_type`, "text")), cell(i, "implied_move_pct", "pct3"), cell(i, "actual_move_pct", "pct3"),
    cell(i, "d_atm30", "vp3")));
  $("preview-index").append(h("table", { class: "mini-table" },
    h("thead", {}, h("tr", {}, ["Date", "Type", "Implied, %", "Actual, %", "30-day ATM change, vol pts"].map((t) => h("th", { scope: "col" }, t)))),
    h("tbody", {}, body)));
}

function tutorCard() {
  const row = (label, path, fmt, unit) => h("div", {}, h("dt", {}, label), h("dd", {}, num(path, fmt), unit));
  $("preview-tutor").append(h("div", { class: "tool-card" },
    h("p", { class: "tool-call" }, "get_event(date=\"", h("span", { "data-src": "tutor_card.date", "data-fmt": "text" }), "\")"),
    h("dl", {}, row("Type", "tutor_card.event_type", "text", ""), row("Implied move", "tutor_card.implied_move_pct", "pct3", "%"),
      row("Actual move", "tutor_card.actual_move_pct", "pct3", "%"),
      h("div", {}, h("dt", {}, "Baseline"), h("dd", {}, num("tutor_card.baseline_pct", "pct3"), "% (n ", num("tutor_card.baseline_n", "int"), ")")),
      row("Premium", "tutor_card.premium_pp", "pp3", " pp")),
    h("p", { class: "tool-source" }, "Source: event_moves, event_premium. Frozen v1.")));
}

// ---------------------------------------------------------------- findings
const MEASURE = { "30-day ATM (fixed strike)": "Fixed-strike ATM", "30-day RR25": "Risk reversal (RR25)", "30-day put wing": "Put wing" };

function findings(data) {
  const f = data.findings;
  for (const t of ["CPI", "FOMC"]) {
    const c = (k, n) => h("td", {}, num(`findings.premium.${t}.${k}`, "pp3"), " (", num(`findings.premium.${t}.${n}`, "int"), ")");
    $("f12-rows").append(h("tr", {}, h("th", { scope: "row" }, t), c("early", "early_n"), c("late", "late_n"), c("all", "all_n")));
  }
  $("f12-plain").append(h("strong", {}, "In plain words: "),
    "the evening before a CPI or FOMC release, options priced the index to move further the next day than on nearby " +
    "ordinary days, by a median of ", num("findings.premium.CPI.all", "pp3"), " pp for CPI and ", num("findings.premium.FOMC.all", "pp3"),
    " pp for FOMC across both periods, against an ordinary-day priced move of about ", num("findings.premium.CPI.baseline_late_pct", "pct3"),
    "% in 2023–26. That describes what was priced in; it does not say whether buying or selling options paid.");

  const order = ["CPI", "NFP", "FOMC"];
  const rows = f.survivors.map((r, i) => ({ r, i })).sort((a, b) => order.indexOf(a.r.type) - order.indexOf(b.r.type));
  for (const { r, i } of rows) {
    const p = `findings.survivors.${i}`;
    const val = (d, ci, n) => h("td", {}, num(`${p}.${d}`, "vp3"), " [", num(`${p}.${ci}.0`, "vp3"), ", ", num(`${p}.${ci}.1`, "vp3"), "] (",
                                num(`${p}.${n}`, "int"), ")");
    $("f51-rows").append(h("tr", {}, h("th", { scope: "row" }, r.type), h("td", {}, MEASURE[r.measure] || r.measure),
      val("diff", "ci", "n"), val("diff_ex", "ci_ex", "n_ex")));
  }
  const sizes = f.survivors.map((r) => Math.abs(r.diff));
  data.findings.plain_range = [Math.min(...sizes), Math.max(...sizes)];
  $("f51-plain").append(h("strong", {}, "In plain words: "),
    "from the day before a CPI release to the release day, 30-day implied vol at the same strike fell, the skew flattened " +
    "(puts lost some of their extra vol over calls) and the put wing narrowed, each by about ",
    h("span", { class: "num", "data-src": "findings.plain_range.0", "data-fmt": "range2" }), " to ",
    h("span", { class: "num", "data-src": "findings.plain_range.1", "data-fmt": "range2" }),
    " vol points more than from one ordinary day to the next. After payrolls only the vol fall held, and after FOMC only " +
    "the skew flattening. A vol point is one point of implied vol, for example 15% to 16%.");
}
FMT.range2 = (x) => Number(x).toFixed(2);

// ---------------------------------------------------------------- tabs (keyboard: arrows, Home, End)
function tabs() {
  const list = [...document.querySelectorAll('[role="tab"]')];
  const select = (tab, focus = true) => {
    for (const t of list) {
      const on = t === tab;
      t.setAttribute("aria-selected", on ? "true" : "false");
      t.tabIndex = on ? 0 : -1;
      $(t.getAttribute("aria-controls")).classList.toggle("hidden", !on);
    }
    if (focus) tab.focus();
  };
  for (const t of list) {
    t.addEventListener("click", () => select(t));
    t.addEventListener("keydown", (e) => {
      const i = list.indexOf(t);
      const to = { ArrowRight: list[(i + 1) % list.length], ArrowLeft: list[(i - 1 + list.length) % list.length],
                   Home: list[0], End: list[list.length - 1] }[e.key];
      if (to) { e.preventDefault(); select(to); }
    });
  }
}

// ---------------------------------------------------------------- the scripted tutor demo (no requests)
let demoRun = 0;
let current = 0;

function stepNode(step) {
  const kind = { user: "You", tool: "Tool call", answer: "Tutor", caution: "Caution" }[step.kind];
  const body = step.kind === "tool"
    ? [h("code", {}, step.call), h("span", { class: "tool-result" }, step.result)]
    : [step.text];
  return h("li", { class: `step ${step.kind}` }, h("span", { class: "who" }, kind), h("div", { class: "bubble" }, body));
}

async function play(index) {
  const run = ++demoRun;
  current = index;
  for (const b of document.querySelectorAll(".demo-pick")) b.setAttribute("aria-pressed", b.dataset.demo === String(index) ? "true" : "false");
  const chat = $("demo-chat");
  chat.replaceChildren();
  const steps = DEMOS[index].steps;
  if (reduced()) {                                     // reduced motion: the whole conversation at once
    chat.append(...steps.map(stepNode));
    chat.dataset.done = "true";
    return;
  }
  chat.dataset.done = "false";
  for (const st of steps) {
    await new Promise((r) => setTimeout(r, st.kind === "user" ? 300 : 900));
    if (run !== demoRun) return;
    const node = stepNode(st);
    node.classList.add("enter");
    chat.append(node);
    requestAnimationFrame(() => node.classList.remove("enter"));
  }
  chat.dataset.done = "true";
}

function demo() {
  for (const b of document.querySelectorAll(".demo-pick")) b.addEventListener("click", () => play(Number(b.dataset.demo)));
  $("demo-replay").addEventListener("click", () => play(current));
  if (reduced() || !("IntersectionObserver" in window)) { play(0); return; }
  const io = new IntersectionObserver((entries) => {
    if (entries.some((e) => e.isIntersecting)) { io.disconnect(); play(0); }
  }, { threshold: 0.3 });
  io.observe($("demo"));
}

// ---------------------------------------------------------------- start
async function main() {
  tabs();
  demo();
  let data;
  try {
    const r = await fetch("/home/data", { headers: { Accept: "application/json" } });
    data = await r.json();
    if (!r.ok) throw new Error(data.error || `Request failed (${r.status})`);
  } catch (e) {
    $("load-error").textContent = `The numbers could not be loaded: ${e.message}`;
    $("load-error").classList.remove("hidden");
    document.body.dataset.ready = "error";
    return;
  }
  for (const el of document.querySelectorAll("[data-signed-in]")) el.classList.toggle("hidden", !data.signed_in);
  for (const el of document.querySelectorAll("[data-signed-out]")) el.classList.toggle("hidden", !!data.signed_in);
  studyChart(data);
  surfaceChart(data);
  indexTable(data);
  tutorCard();
  findings(data);
  fill(data);
  window.__home = data;                                // for the page tests
  document.body.dataset.ready = "true";
}

main();
