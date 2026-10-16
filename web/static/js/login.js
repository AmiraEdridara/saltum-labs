// Log in and sign up against the web server's /auth routes. The session is an HttpOnly cookie.

const $ = (id) => document.getElementById(id);

function nextUrl() {
  const next = new URLSearchParams(location.search).get("next") || "/terminal";
  return next.startsWith("/terminal") ? next : "/terminal";        // only ever go back into the terminal
}

async function post(url, body) {
  const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.error || `Request failed (${r.status})`);
  return data;
}

function show(which) {
  $("login-section").classList.toggle("hidden", which !== "login");
  $("signup-section").classList.toggle("hidden", which !== "signup");
  document.querySelector(`#${which}-form input`).focus();
}

$("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  $("login-error").textContent = "";
  try {
    await post("/auth/login", { email: f.get("email"), password: f.get("password") });
    location.assign(nextUrl());
  } catch (err) {
    $("login-error").textContent = err.message;
  }
});

$("signup-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = new FormData(e.target);
  $("signup-error").textContent = "";
  try {
    await post("/auth/signup", { email: f.get("email"), password: f.get("password"), accept_terms: f.get("accept_terms") === "on" });
    location.assign(nextUrl());
  } catch (err) {
    $("signup-error").textContent = err.message;
  }
});

$("show-signup").addEventListener("click", () => show("signup"));
if (new URLSearchParams(location.search).get("mode") === "signup") show("signup");     // from the homepage
$("show-login").addEventListener("click", () => show("login"));
$("reset").addEventListener("click", async () => {
  try {
    await post("/auth/reset", { email: new FormData($("login-form")).get("email") });
  } catch (err) {
    $("login-error").textContent = err.message;
  }
});
