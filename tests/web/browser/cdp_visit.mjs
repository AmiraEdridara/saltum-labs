// Headless Edge via the DevTools protocol, for the page tests (no downloads, no browser automation packages).
// Usage: node cdp_visit.mjs <job.json> <out.json>
// job: { edge, port, profile, origin, cookie: {name, value}, edge_args, headed, block, visits: [{ url, width, height, mobile, wait, extract, keys, actions, shots, logged_out, media }] }
// wait_after: what to wait for after keys and actions (default: wait again). logged_out: visit without the session cookie. media: CSS media features to emulate, e.g. [{ name: "prefers-reduced-motion", value: "reduce" }].
// For each visit: open the URL, wait until `wait` (a JS expression) is truthy, press `keys`, then return
// the value of `extract` plus any console errors, exceptions and blocked resources.
import { spawn } from "node:child_process";
import fs from "node:fs";

const job = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const edge = spawn(job.edge, [...(job.headed ? [] : ["--headless=new"]), `--remote-debugging-port=${job.port}`, `--user-data-dir=${job.profile}`,
                              "--no-first-run", "--no-default-browser-check", ...(job.edge_args || []), "about:blank"], { stdio: "ignore" });
const results = [];
try {
  let target;
  for (let i = 0; i < 100 && !target; i++) {
    await sleep(200);
    try { target = (await (await fetch(`http://127.0.0.1:${job.port}/json/list`)).json()).find((t) => t.type === "page"); } catch {}
  }
  if (!target) throw new Error("Edge did not start");
  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((r, j) => { ws.addEventListener("open", r); ws.addEventListener("error", j); });
  let id = 0;
  const waiting = new Map();
  let errors = [];
  ws.addEventListener("message", (m) => {
    const d = JSON.parse(m.data);
    if (d.id && waiting.has(d.id)) { waiting.get(d.id)(d); waiting.delete(d.id); }
    if (d.method === "Runtime.exceptionThrown") errors.push("exception: " + (d.params.exceptionDetails.exception?.description || d.params.exceptionDetails.text));
    if (d.method === "Runtime.consoleAPICalled" && ["error", "warning"].includes(d.params.type)) errors.push("console: " + d.params.args.map((a) => a.value ?? a.description).join(" "));
    if (d.method === "Log.entryAdded" && ["error", "warning"].includes(d.params.entry.level)) errors.push("log: " + d.params.entry.text + " " + (d.params.entry.url || ""));
  });
  const send = (method, params = {}) => new Promise((r) => { const i = ++id; waiting.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
  const evaluate = async (expression) => {
    const r = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
    if (r.result.exceptionDetails) throw new Error("evaluate: " + (r.result.exceptionDetails.exception?.description || r.result.exceptionDetails.text));
    return r.result.result.value;
  };
  await send("Runtime.enable"); await send("Log.enable"); await send("Network.enable");
  if (job.block?.length) await send("Network.setBlockedURLs", { urls: job.block });     // e.g. to make Plotly fail
  await send("Network.setCookie", { name: job.cookie.name, value: job.cookie.value, url: job.origin, httpOnly: true });
  for (const v of job.visits) {
    errors = [];
    await send("Emulation.setDeviceMetricsOverride", { width: v.width || 1440, height: v.height || 900, deviceScaleFactor: 1, mobile: !!v.mobile });
    await send("Emulation.setEmulatedMedia", { features: v.media || [] });
    if (v.logged_out) await send("Network.deleteCookies", { name: job.cookie.name, url: job.origin });
    else await send("Network.setCookie", { name: job.cookie.name, value: job.cookie.value, url: job.origin, httpOnly: true });
    await send("Page.navigate", { url: job.origin + v.url });
    let ok = false;
    for (let i = 0; i < (v.timeout_s || 90) * 5 && !ok; i++) {        // wait up to 90 s by default
      await sleep(200);
      try { ok = await evaluate(v.wait || "document.readyState === 'complete'"); } catch { ok = false; }
    }
    const VK = { ArrowLeft: 37, ArrowUp: 38, ArrowRight: 39, ArrowDown: 40, Home: 36, End: 35, Tab: 9, Enter: 13, Escape: 27 };
    for (const k of v.keys || []) {
      // Named keys need their key code (and no text) for the page to see them; single characters are typed.
      const named = k in VK ? { code: k, windowsVirtualKeyCode: VK[k] } : {};
      for (const type of ["keyDown", "keyUp"]) {
        await send("Input.dispatchKeyEvent", { type: type === "keyDown" && k in VK ? "rawKeyDown" : type, key: k, ...named,
                                               text: type === "keyDown" && !(k in VK) ? k : undefined });
      }
      await sleep(400);
    }
    for (const a of v.actions || []) {         // optional: run a JS expression (a click, say), then wait again
      try { await evaluate(a); } catch {}
      await sleep(800);
    }
    if (v.keys?.length || v.actions?.length) {
      for (let i = 0; i < 100; i++) { await sleep(200); try { if (await evaluate(v.wait_after || v.wait)) break; } catch {} }
    }
    for (const s of v.shots || []) {           // optional screenshots: scroll an element into view, capture the viewport
      await evaluate(`document.querySelector(${JSON.stringify(s.selector)})?.scrollIntoView()`);
      await sleep(600);
      const img = await send("Page.captureScreenshot", { format: "png" });
      fs.writeFileSync(s.path, Buffer.from(img.result.data, "base64"));
    }
    let value = null, error = null;
    try { value = await evaluate(v.extract); } catch (e) { error = String(e.message || e); }
    results.push({ url: v.url, ready: ok, value, error, errors: [...errors] });
  }
  ws.close();
} finally {
  edge.kill();             // helper processes are stopped by the Python harness (by profile folder)
  fs.writeFileSync(process.argv[3], JSON.stringify(results, null, 1));
}
