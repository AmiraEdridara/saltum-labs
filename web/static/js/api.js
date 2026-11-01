// Calls go to this server's /api proxy, which adds the API token server-side. The browser never holds it.
// On the homepage's preview (/preview) nothing leaves the browser: reads come from a fixed sample file (preview.js).
export const PREVIEW = location.pathname === "/preview";

export class ApiError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

// A server started before a code change keeps serving the old code; it marks its responses so we can say so.
function checkCode(r) {
  const which = r.headers.get("X-MW-Code-Changed");
  const banner = document.getElementById("stale-banner");
  if (!which || !banner || !banner.classList.contains("hidden")) return;
  const names = which.split(",").map((s) => (s === "api" ? "the API (port 8000)" : "the web server (port 8080)"));
  banner.textContent = `The code changed after ${names.join(" and ")} started, so ${names.length > 1 ? "they are" : "it is"} `
    + "running an older version and pages may not work. Restart both servers (Ctrl+C in each window, then start them again).";
  banner.classList.remove("hidden");
}

export async function get(path) {
  if (PREVIEW) return (await import("./preview.js")).sampleGet(path);
  const r = await fetch(path, { headers: { Accept: "application/json" }, credentials: "same-origin" });
  checkCode(r);
  if (r.status === 401) {                       // session expired: back to the login page, then here again
    location.assign("/login?next=" + encodeURIComponent(location.pathname + location.search));
    throw new ApiError(401, "Log in first.");
  }
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new ApiError(r.status, data.detail || data.error || `Request failed (${r.status})`);
  return data;
}

export async function send(method, path, body) {
  if (PREVIEW) return body || {};               // look-only: nothing is saved
  const r = await fetch(path, { method, headers: { "Content-Type": "application/json" }, credentials: "same-origin",
                                body: JSON.stringify(body || {}) });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new ApiError(r.status, data.error || `Request failed (${r.status})`);
  return data;
}
