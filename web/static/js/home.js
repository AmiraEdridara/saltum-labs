// The homepage: one request to /home/data (frozen v1 summaries, public), then every number on the page is filled
// from it. Elements with data-src name the field they show; tests/web/test_home_page.py checks each against the API.
// The tutor demo (home_demo.js) is a script: it makes no requests.
import { DEMOS } from "./home_demo.js";

const $ = (id) => document.getElementById(id);
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
};
const pick = (data, path) => path.split(".").reduce((o, k) => (o == null ? o : o[k]), data);

function num(path, fmt) {
  const el = document.createElement("span");
  el.setAttribute("data-src", path);
  el.setAttribute("data-fmt", fmt);
  el.className = "num";
  return el;
}

function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
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
  findings(data);
  fill(data);
  window.__home = data;                                // for the page tests
  document.body.dataset.ready = "true";
}

main();
