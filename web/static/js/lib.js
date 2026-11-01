// Shared by the pages: element builder, cached API reads, number formatting, and loading Plotly once.
import { PREVIEW, get } from "./api.js";

export function h(tag, attrs = {}, ...children) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k === "class") e.className = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat(Infinity)) if (c !== null && c !== undefined && c !== false) e.append(c);
  return e;
}

const cache = new Map();
export function cached(path) {
  if (!cache.has(path)) cache.set(path, get(path).catch((e) => { cache.delete(path); throw e; }));
  return cache.get(path);
}

// The preview's plain wording, where the terminal's own would make invented numbers look like real results.
export const plain = (real, sample) => (PREVIEW ? sample : real);

export const num = (v, dp = 3, sign = false) => (v === null || v === undefined || Number.isNaN(v) ? "–"
  : (sign && v > 0 ? "+" : "") + Number(v).toFixed(dp));

export const source = (...parts) => h("p", { class: "source" }, "Source: ", h("code", {}, parts.join(", ")));

let plotly;
export function loadPlotly() {
  plotly ??= new Promise((resolve, reject) => {
    if (window.Plotly) return resolve(window.Plotly);
    const s = document.createElement("script");
    s.src = "/static/vendor/plotly-strict.min.js";
    // The script can load and still fail to start (in 2026-10-15's case, a security policy blocked its styles).
    s.onload = () => (window.Plotly ? resolve(window.Plotly)
      : (plotly = undefined, reject(new Error("The charting library loaded but did not start."))));
    s.onerror = () => { plotly = undefined; reject(new Error("The charting library did not load.")); };
    document.head.append(s);
  });
  return plotly;
}

export function table(headers, rows, opts = {}) {
  return h("div", { class: "table-wrap" }, h("table", { class: "data" + (opts.cls ? " " + opts.cls : "") },
    opts.caption ? h("caption", {}, opts.caption) : null,
    h("thead", {}, h("tr", {}, headers.map((x, i) => h("th", { scope: "col", class: i ? "r" : "" }, x)))),
    h("tbody", {}, rows.map((r) => h("tr", {}, r.map((c, i) => (i ? h("td", { class: "r" }, c) : h("th", { scope: "row" }, c))))))));
}

// A render can be overtaken by a newer one in the same pane (the user changed something meanwhile).
export function renderTicket(container) {
  const mine = String(Date.now() + Math.random());
  container.dataset.renderToken = mine;
  return () => container.dataset.renderToken === mine;
}

// Shown in a chart's box when it cannot be drawn, instead of leaving the box empty.
export function chartFailed(el, err) {
  if (!el) return;
  el.classList.add("plot-failed");
  el.replaceChildren(h("p", { class: "error" }, `This chart could not be drawn: ${err?.message || err}. `
    + "If the code was updated recently, restart both servers. The browser console has the details."));
}
