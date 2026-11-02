// The homepage's terminal preview: every read the terminal makes is answered from the fixed sample file, so nothing
// reaches the API, the tutor or an account. The file holds one example: CPI in the later period, one event row and one
// pair of dates. The numbers are invented.
let file;
const sample = () => (file ??= fetch("/static/preview/sample.json").then((r) => r.json()));

export async function sampleGet(path) {
  const s = await sample();
  const url = new URL(path, location.origin), p = url.pathname;
  if (p === "/account/me") return s.me;
  if (p === "/status/update") return { configured: false };
  if (p === "/tutor/status") return s.tutor.status;
  if (p === "/tutor/sessions") return { sessions: s.tutor.sessions };
  if (p.startsWith("/tutor/sessions/")) return s.tutor.session;
  if (p === "/api/dates") return s.dates;
  if (p === "/api/findings") return s.findings;
  if (p === "/api/events") return s.events;
  if (p === "/api/events/next") return s.next;
  if (p === "/api/events/index") return s.index;
  const m = p.match(/^\/api\/(?:surface\/([^/]+)|events\/([^/]+)\/skew)$/);
  const found = m && (m[1] ? s.surface[m[1]] : s.skew[m[2]]);
  if (found) return found;
  throw new Error("This is not in the sample.");
}
