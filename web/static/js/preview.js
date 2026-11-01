// The homepage's terminal preview: every read the terminal makes is answered from the fixed sample file, so nothing
// reaches the API, the tutor or an account. The file has one list of events, two template surfaces and each date's
// levels; here they are filtered and shifted the way the API would. The numbers are invented.
let file;
const sample = () => (file ??= fetch("/static/preview/sample.json").then((r) => r.json()));
const typed = (rows, t) => (t === "all" ? rows : rows.filter((r) => r.event_type.split("+").includes(t)));
const inPeriod = (rows, p) => (p === "both" ? rows : rows.filter((r) => r.period === p));
const EARLY_END = "2023-05-31";
const periodOf = (iso) => (iso <= EARLY_END ? "2020-01..2023-05" : "2023-06..now");

function events(s, q) {
  const type = q.get("type") || "all", period = q.get("period") || "both";
  const rows = inPeriod(typed(s.events.data.events, type).map((r) => ({ ...r, period: periodOf(r.date) })), period);
  const summaries = Object.values(s.summaries[period]).filter((x) => type === "all" || x.event_type === type);
  return { ...s.events, n: rows.length, period, data: { ...s.events.data, events: rows, summaries,
    summaries_data: q.get("data") === "live" ? "live" : "v1", live_rows: rows.filter((r) => r.live).length } };
}

function next(s, q) {
  const type = q.get("type") || "all", by = s.next.data.by_type;
  const soon = (type === "all" ? Object.keys(by) : [type]).reduce((a, b) => (by[b].days_away < by[a].days_away ? b : a));
  return { ...s.next, data: { ...s.next.data, soonest: { date: by[soon].next, event_types: [soon], days_away: by[soon].days_away } } };
}

function index(s, q) {
  let rows = s.index.data.events.filter((r) => r.event_type !== "ordinary");
  if (q.get("include_ordinary") === "true") rows = s.index.data.events;
  const type = q.get("type") || "all", period = q.get("period") || "both", ex = q.get("exclude_flagged") === "true";
  rows = inPeriod(type === "all" ? rows : rows.filter((r) => r.event_type === "ordinary" || r.event_type.split("+").includes(type)), period)
    .filter((r) => !(ex && r.flagged));
  return { ...s.index, n: rows.length, data: { ...s.index.data, events: rows, summary: ex ? s.summary_excluding_flagged : s.index.data.summary } };
}

const addDays = (iso, n) => new Date(Date.parse(iso + "T12:00:00Z") + n * 864e5).toISOString().slice(0, 10);
const toFriday = (iso, dte) => dte - (new Date(addDays(iso, dte) + "T12:00:00Z").getUTCDay() + 2) % 7;

// A date's surface: the template for its kind of day, moved to that day's level and with Friday expiries from that date.
function surface(s, date) {
  const row = s.dates.data.dates.find((d) => d.date === date), level = s.levels[date];
  if (!row || !level) throw new Error("This date is not in the sample.");
  const out = structuredClone(row.events ? s.surface.after : s.surface.before), d = out.data;
  const shift = level.atm30 - d.metrics.atm30, up = (v) => Math.round((v + shift) * 100) / 100;
  for (const e of d.expiries) {
    e.dte = toFriday(date, e.dte);
    e.expiration = addDays(date, e.dte);
    e.iv_pct = e.iv_pct.map(up);
    for (const o of e.options) o.iv_pct = up(o.iv_pct);
  }
  for (const t of d.term) { t.atm_iv_pct = up(t.atm_iv_pct); t.expiration = addDays(date, t.dte); }
  Object.assign(d.day, { live: row.live, day_type: row.day_type, events: row.events, events_next_day: row.events_next_day, flagged: row.flagged });
  if (row.flagged) d.day.fit_max_volpts = 0.62;
  d.metrics = { ...d.metrics, ...level, short_expiry: d.expiries[0].expiration, short_dte: d.expiries[0].dte };
  return { ...out, period: periodOf(date), params: { date } };
}

export async function sampleGet(path) {
  const s = await sample();
  const url = new URL(path, location.origin), p = url.pathname, q = url.searchParams;
  if (p === "/account/me") return s.me;
  if (p === "/status/update") return { configured: false };
  if (p === "/tutor/status") return s.tutor.status;
  if (p === "/tutor/sessions") return { sessions: s.tutor.sessions };
  if (p.startsWith("/tutor/sessions/")) return s.tutor.session;
  if (p === "/api/dates") return s.dates;
  if (p === "/api/findings") return s.findings;
  if (p === "/api/events") return events(s, q);
  if (p === "/api/events/next") return next(s, q);
  if (p === "/api/events/index") return index(s, q);
  const m = p.match(/^\/api\/(?:surface\/([^/]+)|events\/([^/]+)\/skew)$/);
  if (m?.[1]) return surface(s, m[1]);
  if (m?.[2] && s.skew[m[2]]) return s.skew[m[2]];
  throw new Error("This is not in the sample.");
}
