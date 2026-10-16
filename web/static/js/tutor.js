// The tutor panel. Questions go to this server (POST /tutor/ask), which calls the Claude API; the browser never
// holds an API key. Answers are shown as plain text (never as HTML). Saved sessions are listed inside the panel
// (Sessions): open, start a new one, rename, delete. The current session stays open while the list is shown.
import { get, send } from "./api.js";
import { h } from "./lib.js";

let sessionId = null;
let saves = true;
let getContext = () => null;
const $ = (id) => document.getElementById(id);
const when = (s) => new Date(s).toLocaleString("en-GB", { day: "numeric", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });

function paragraphs(text) {
  // Plain text with paragraph breaks and simple "- " lists; markdown emphasis marks are dropped.
  const out = [];
  for (const block of text.replace(/\*\*/g, "").split(/\n\s*\n/)) {
    const lines = block.split("\n").filter((l) => l.trim());
    if (lines.length && lines.every((l) => /^\s*[-•*]\s+/.test(l))) out.push(h("ul", {}, lines.map((l) => h("li", {}, l.replace(/^\s*[-•*]\s+/, "")))));
    else if (lines.length) out.push(h("p", {}, lines.join(" ")));
  }
  return out;
}

function addMessage(role, text, extra = {}) {
  const el = h("div", { class: `tutor-msg ${role}${extra.status === "unverified" ? " unverified" : ""}`, "data-role": role,
                        "data-status": extra.status || null },
    h("p", { class: "who" }, role === "user" ? "You" : "Tutor"),
    role === "user" ? h("p", {}, text) : paragraphs(text),
    extra.sources?.length ? h("p", { class: "source" }, "Tools read: ", h("code", {}, extra.sources.join(", "))) : null);
  $("tutor-log").append(el);
  el.scrollIntoView({ block: "end" });
  return el;
}

function showAllowance(a) {
  $("tutor-left").textContent = a ? `${a.left} of ${a.limit} questions left this ${a.per}.` : "";
}

function label(text) {
  $("tutor-session").textContent = text;
}

// ------------------------------------------------------------ sessions
export async function openSession(id) {
  const s = await get(`/tutor/sessions/${id}`);
  sessionId = s.id;
  $("tutor-log").replaceChildren();
  for (const m of s.messages) addMessage(m.role, m.text);
  label(`Session: ${s.title}`);
  markCurrent();
}

export function newSession() {
  sessionId = null;
  $("tutor-log").replaceChildren();
  label("New session");
  markCurrent();
  $("tutor-q").focus();
}

function markCurrent() {
  document.querySelectorAll("#tutor-sessions li[data-session]").forEach((li) => {
    if (Number(li.dataset.session) === sessionId) li.setAttribute("aria-current", "true");
    else li.removeAttribute("aria-current");
  });
}

function sessionRow(s) {
  const li = h("li", { "data-session": String(s.id) });
  const show = () => {
    li.replaceChildren(
      h("button", { type: "button", class: "link session-open", title: "Open this session",
                    onclick: async () => { await openSession(s.id); toggleSessions(false); } }, s.title),
      h("span", { class: "muted small session-meta" }, ` ${when(s.updated_at)} · ${s.messages / 2} question${s.messages === 2 ? "" : "s"}`),
      h("span", { class: "session-actions" },
        h("button", { type: "button", class: "link", "data-action": "rename", onclick: rename }, "Rename"),
        h("button", { type: "button", class: "link", "data-action": "delete", onclick: askDelete }, "Delete")));
    if (s.id === sessionId) li.setAttribute("aria-current", "true");   // the row may not be on the page yet
    else li.removeAttribute("aria-current");
  };
  const rename = () => {
    const input = h("input", { type: "text", value: s.title, maxlength: "80", "aria-label": "Session name" });
    const err = h("span", { class: "error small" });
    const save = async (e) => {
      e.preventDefault();
      try {
        const r = await send("PUT", `/tutor/sessions/${s.id}`, { title: input.value });
        s.title = r.title;
        if (sessionId === s.id) label(`Session: ${s.title}`);
        show();
      } catch (ex) { err.textContent = ex.message; }
    };
    li.replaceChildren(h("form", { class: "session-rename", onsubmit: save }, input,
      h("button", { type: "submit" }, "Save"), h("button", { type: "button", class: "link", onclick: show }, "Cancel"), err));
    input.addEventListener("keydown", (e) => { if (e.key === "Escape") { e.stopPropagation(); show(); } });
    input.focus();
    input.select();
  };
  const askDelete = () => {
    li.replaceChildren(h("span", {}, `Delete "${s.title}"? `),
      h("button", { type: "button", "data-action": "confirm-delete", onclick: async () => {
        await send("DELETE", `/tutor/sessions/${s.id}`, {});
        if (sessionId === s.id) newSession();
        li.remove();
        if (!document.querySelector("#tutor-sessions li[data-session]")) renderSessions();
      } }, "Delete"),
      h("button", { type: "button", class: "link", onclick: show }, "Cancel"));
  };
  show();
  return li;
}

async function renderSessions() {
  const box = $("tutor-sessions");
  if (!saves) {
    box.replaceChildren(h("p", { class: "note" }, "Sessions are not saved on your plan."));
    return;
  }
  box.replaceChildren(h("p", { class: "muted small" }, "Loading…"));
  try {
    const { sessions } = await get("/tutor/sessions");               // newest first
    box.replaceChildren(
      h("div", { class: "sessions-head" }, h("h3", {}, `Saved sessions (${sessions.length})`),
        h("button", { type: "button", onclick: () => { newSession(); toggleSessions(false); } }, "New session")),
      sessions.length ? h("ul", { class: "session-list" }, sessions.map(sessionRow))
                      : h("p", { class: "note" }, "No saved sessions yet. Ask a question to start one."));
  } catch (err) {
    box.replaceChildren(h("p", { class: "error" }, `Your sessions could not be loaded: ${err.message}`));
  }
}

function toggleSessions(open) {
  const box = $("tutor-sessions"), button = $("tutor-sessions-button");
  const show = open ?? box.classList.contains("hidden");
  box.classList.toggle("hidden", !show);
  button.setAttribute("aria-expanded", String(show));
  if (show) renderSessions();
}

// ------------------------------------------------------------ asking
export async function initTutor({ context }) {
  getContext = context;
  const form = $("tutor-form"), q = $("tutor-q"), button = form.querySelector("button[type=submit]");
  $("tutor-new").addEventListener("click", newSession);
  $("tutor-sessions-button").addEventListener("click", () => toggleSessions());
  try {
    const st = await get("/tutor/status");
    showAllowance(st.allowance);
    saves = st.saves_sessions;
    if (!st.configured) {
      $("tutor-left").textContent = "The tutor is not configured on this server yet (no Claude API key).";
      button.disabled = true;
    }
    if (!saves) label("Sessions are not saved on your plan.");
  } catch { /* the panel still works; errors show when asking */ }
  const submit = async (e) => {
    e?.preventDefault();
    const question = q.value.trim();
    if (!question || button.disabled) return;
    addMessage("user", question);
    q.value = "";
    button.disabled = true;
    const wait = h("p", { class: "muted tutor-wait" }, "Looking it up…");
    $("tutor-log").append(wait);
    try {
      const r = await send("POST", "/tutor/ask", { question, context: getContext(), session_id: sessionId });
      if (r.session_id) {
        if (sessionId !== r.session_id) label("Session saved");
        sessionId = r.session_id;
      }
      addMessage("assistant", r.answer, { status: r.status, sources: r.sources });
      showAllowance(r.allowance);
      if (!$("tutor-sessions").classList.contains("hidden")) renderSessions();
    } catch (err) {
      addMessage("assistant", err.message, { status: "error" });
    } finally {
      wait.remove();
      button.disabled = false;
      q.focus();
    }
  };
  form.addEventListener("submit", submit);
  q.addEventListener("keydown", (e) => { if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) submit(e); });
}
