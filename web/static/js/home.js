// The homepage: a static page with no data of its own and no requests. The only numbers are the prices and the sample
// figures in the tutor demo (home_demo.js). The server marks the body when the visitor is logged in.
import { DEMOS } from "./home_demo.js";

const $ = (id) => document.getElementById(id);
const reduced = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

function h(tag, attrs = {}, ...kids) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
  for (const k of kids.flat()) if (k != null) el.append(k);
  return el;
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

demo();
document.body.dataset.ready = "true";
