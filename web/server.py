r"""The terminal web server: login pages, the terminal frame, account routes, and a proxy to the API.
Run on localhost only (the API must be running on 127.0.0.1:8000 too):
    .venv\Scripts\python.exe -m uvicorn web.server:app --host 127.0.0.1 --port 8080

The browser talks only to this server. GET /api/<path> is forwarded to the API when the session is
valid, with the API token added here; the token never reaches the browser.
"""
import re
import urllib.parse

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

import codecheck
from . import settings
from .auth import AuthError, default_provider
from .tutor import service as tutor
from .tutor.tools import ToolRunner
from .users import TutorStore, UserStore, public

# The API's endpoints (docs/prd.md, Shared API). Anything else is not forwarded.
API_PATHS = [re.compile(p) for p in (r"events", r"events/index", r"events/[^/]+", r"events/[^/]+/skew",
                                     r"surface/[^/]+", r"dates", r"findings", r"definitions")]

app = FastAPI(title="Saltum Labs terminal", docs_url=None, redoc_url=None, openapi_url=None)
app.state.store = UserStore(settings.USERS_DB)
app.state.provider = default_provider(app.state.store)
app.state.api_transport = None            # tests can put an in-process transport here
app.state.tutor_client = None             # the Claude API client, created on first use (tests can put a fake here)
if not app.state.provider.token_configured:
    print("No dev token configured (MACRO_WIRE_DEV_TOKEN in .env): the API will refuse proxied requests.")
app.mount("/static", StaticFiles(directory=settings.STATIC_DIR), name="static")


@app.middleware("http")
async def headers(request: Request, call_next):
    # Same-origin JSON only for anything that changes state (with the SameSite=Strict cookie, this blocks CSRF).
    if request.method in ("POST", "PUT", "DELETE"):
        origin = request.headers.get("origin")
        if origin and urllib.parse.urlparse(origin).netloc != request.headers.get("host"):
            return JSONResponse({"error": "cross-origin request refused"}, status_code=403)
        if not request.headers.get("content-type", "").startswith("application/json"):
            return JSONResponse({"error": "send JSON"}, status_code=415)
    response = await call_next(request)
    # style-src allows inline styles only because Plotly sets them (approved 2026-10-15); scripts stay 'self' only.
    # Only the terminal preview may sit in a frame, and only on this site's own homepage.
    ancestors = "'self'" if request.url.path == "/preview" else "'none'"
    response.headers["Content-Security-Policy"] = ("default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                                                   f"frame-ancestors {ancestors}; base-uri 'none'; form-action 'self'")
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    stale = [s for s in response.headers.get(codecheck.HEADER, "").split(",") if s]
    if codecheck.changed_since_start("web", "."):
        stale.append("web")
    if stale:
        response.headers[codecheck.HEADER] = ",".join(sorted(set(stale)))
    return response


@app.exception_handler(AuthError)
def auth_error(request: Request, exc: AuthError):
    return JSONResponse({"error": str(exc)}, status_code=exc.status)


def user_of(request):
    return request.app.state.provider.current_user(request.cookies.get(settings.SESSION_COOKIE))


def require(request):
    user = user_of(request)
    if user is None:
        raise AuthError(401, "Log in first.")
    return user


def with_session(response, value):
    response.set_cookie(settings.SESSION_COOKIE, value, max_age=settings.SESSION_HOURS * 3600, httponly=True,
                        samesite="strict", secure=False, path="/")     # http on localhost; secure=True when deployed
    return response


# ---------------------------------------------------------------- pages
@app.get("/")
def root(request: Request):
    """The homepage, for everyone: logged-out visitors land here, logged-in users can still reach it. It carries no
    data; a logged-in visitor's page is marked so that it offers the terminal in place of sign-up."""
    page = (settings.STATIC_DIR / "home.html").read_text(encoding="utf-8")
    if user_of(request) is not None:
        page = page.replace("<body>", '<body class="signed-in">', 1)
    return HTMLResponse(page, headers={"Cache-Control": "no-store"})


@app.get("/method")
def method_page():
    """The method in words, for everyone. Like the homepage, it holds no real numbers or dates."""
    return FileResponse(settings.STATIC_DIR / "method.html")


@app.get("/login")
def login_page():
    return FileResponse(settings.STATIC_DIR / "login.html")


@app.get("/terminal")
def terminal_page(request: Request):
    if user_of(request) is None:
        target = "/terminal" + (f"?{request.url.query}" if request.url.query else "")
        return RedirectResponse("/login?next=" + urllib.parse.quote(target, safe=""), status_code=303)
    return FileResponse(settings.STATIC_DIR / "terminal.html", headers={"Cache-Control": "no-store"})


@app.get("/preview")
def preview_page():
    """The homepage's terminal preview, for everyone: the real terminal page, which on this path reads only the fixed
    sample file (web/static/js/preview.js) and never the API."""
    return FileResponse(settings.STATIC_DIR / "terminal.html", headers={"Cache-Control": "no-store"})


# ---------------------------------------------------------------- auth and account
@app.post("/auth/signup", status_code=201)
async def sign_up(request: Request):
    body = await request.json()
    user, value = request.app.state.provider.sign_up(body.get("email"), body.get("password"), body.get("accept_terms"))
    return with_session(JSONResponse(public(user), status_code=201), value)


@app.post("/auth/login")
async def log_in(request: Request):
    body = await request.json()
    user, value = request.app.state.provider.log_in(body.get("email"), body.get("password"))
    return with_session(JSONResponse(public(user)), value)


@app.post("/auth/logout")
def log_out(request: Request):
    request.app.state.provider.log_out(request.cookies.get(settings.SESSION_COOKIE))
    response = JSONResponse({"ok": True})
    response.delete_cookie(settings.SESSION_COOKIE, path="/")
    return response


@app.post("/auth/reset")
async def reset(request: Request):
    body = await request.json()
    request.app.state.provider.request_password_reset(body.get("email"))


@app.get("/account/me")
def me(request: Request):
    user = require(request)
    return dict(public(user), layout=request.app.state.store.layout(user["id"]), provider=request.app.state.provider.name,
                footer=settings.TERMINAL_FOOTER, wording=settings.TERMINAL_WORDING,
                limits=dict(student_daily=settings.TUTOR_DAILY_FREE, tutor_plus_monthly=settings.TUTOR_MONTHLY_PLUS,
                            enforced=False))


@app.put("/account/layout")
async def save_layout(request: Request):
    user = require(request)
    body = await request.json()
    store = request.app.state.store
    layout = store.layout(user["id"])                    # merge: saving one setting keeps the others
    layout.update({k: bool(body[k]) for k in ("split", "tutor") if k in body})
    w = body.get("tutor_width")
    if isinstance(w, int) and not isinstance(w, bool) and settings.TUTOR_WIDTH_MIN <= w <= settings.TUTOR_WIDTH_MAX:
        layout["tutor_width"] = w                        # the browser also keeps it within the window
    store.set_layout(user["id"], layout)
    return layout


@app.delete("/account")
async def delete_account(request: Request):
    user = require(request)
    body = await request.json()
    if (body.get("confirm_email") or "").strip().lower() != user["email"]:
        raise AuthError(400, "Type your email to confirm.")
    request.app.state.provider.delete_user(user)
    response = JSONResponse({"deleted": True})
    response.delete_cookie(settings.SESSION_COOKIE, path="/")
    return response


# ---------------------------------------------------------------- API proxy
@app.get("/api/{path:path}")
async def proxy(path: str, request: Request):
    user = require(request)
    if not any(p.fullmatch(path) for p in API_PATHS):
        return JSONResponse({"error": "not found"}, status_code=404)
    token = request.app.state.provider.api_token_for(user)
    params = [(k, settings.PERIOD_IDS.get(v, v) if k == "period" else v) for k, v in request.query_params.multi_items()]
    async with httpx.AsyncClient(base_url=settings.API_BASE, transport=request.app.state.api_transport,
                                 timeout=30) as client:
        try:
            r = await client.get("/" + path, params=params,
                                 headers={"Authorization": f"Bearer {token or ''}"})
        except httpx.HTTPError:
            return JSONResponse({"error": f"The API is not reachable at {settings.API_BASE}. Start it first."},
                                status_code=502)
    if r.status_code == 401:            # the API refused this server's token (not the user's session)
        return JSONResponse({"error": "The API refused the server's token. Check MACRO_WIRE_DEV_TOKEN in .env."},
                            status_code=502)
    passed_on = {codecheck.HEADER: r.headers[codecheck.HEADER]} if codecheck.HEADER in r.headers else None
    content = r.content
    for neutral, real in settings.PERIOD_IDS.items():    # the browser's code knows periods only by their ids
        content = content.replace(f'"{real}"'.encode(), f'"{neutral}"'.encode())
    return Response(content, status_code=r.status_code, media_type="application/json", headers=passed_on)


# ---------------------------------------------------------------- AI tutor (all Claude API calls are made here)
@app.exception_handler(tutor.TutorError)
def tutor_error(request: Request, exc: tutor.TutorError):
    return JSONResponse({"error": str(exc)}, status_code=exc.status)


def tutor_client(request):
    if request.app.state.tutor_client is None:
        import anthropic                       # reads ANTHROPIC_API_KEY from the environment (.env, loaded at start)
        request.app.state.tutor_client = anthropic.AsyncAnthropic()
    return request.app.state.tutor_client


def tool_runner(request, user):
    token = request.app.state.provider.api_token_for(user)

    async def api_get(path):
        async with httpx.AsyncClient(base_url=settings.API_BASE, transport=request.app.state.api_transport, timeout=30) as c:
            r = await c.get(path, headers={"Authorization": f"Bearer {token or ''}"})
        try:
            return r.status_code, r.json()
        except ValueError:
            return r.status_code, {"error": f"API returned {r.status_code}"}
    return ToolRunner(api_get)


@app.get("/status/update")
def update_status(request: Request):
    """The daily update's last result (data/live/update_status.json), for the terminal's top bar."""
    require(request)
    import json
    import config
    try:
        s = json.loads(config.UPDATE_STATUS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"configured": False}
    keep = ("ok", "running", "last_success", "last_failure", "data_to", "event_moves_to", "error")
    return dict({k: s.get(k) for k in keep}, configured=True, v1_end=str(config.V1_END))


@app.get("/tutor/status")
def tutor_status(request: Request):
    user = require(request)
    return dict(configured=tutor.configured(), allowance=tutor.allowance(user), plan=user["plan"],
                saves_sessions=user["plan"] in settings.TUTOR_SAVE_SESSIONS_PLANS)


@app.post("/tutor/ask")
async def tutor_ask(request: Request):
    user = require(request)
    body = await request.json()
    sid = body.get("session_id")
    if sid is not None and not isinstance(sid, int):
        raise tutor.TutorError(400, "session_id must be a number.")
    store = TutorStore(request.app.state.store)
    return await tutor.ask(client=tutor_client(request) if tutor.configured() else None, tools=tool_runner(request, user),
                           store=store, users=request.app.state.store, user=user, question=body.get("question"),
                           ctx_raw=body.get("context"), session_id=sid)


@app.get("/tutor/sessions")
def tutor_sessions(request: Request):
    user = require(request)
    return {"sessions": TutorStore(request.app.state.store).sessions(user["id"])}


@app.get("/tutor/sessions/{session_id}")
def tutor_session(session_id: int, request: Request):
    user = require(request)
    store = TutorStore(request.app.state.store)
    s = store.session(user["id"], session_id)
    if not s:
        raise tutor.TutorError(404, "That tutor session was not found.")
    msgs = [{k: m[k] for k in ("role", "text", "created_at")} for m in store.messages(session_id)]
    return dict(s, messages=msgs)


@app.put("/tutor/sessions/{session_id}")
async def tutor_rename(session_id: int, request: Request):
    user = require(request)
    title = " ".join(str((await request.json()).get("title") or "").split())
    if not title or len(title) > 80:
        raise tutor.TutorError(400, "A session name needs 1 to 80 characters.")
    if not TutorStore(request.app.state.store).rename_session(user["id"], session_id, title):
        raise tutor.TutorError(404, "That tutor session was not found.")
    return {"id": session_id, "title": title}


@app.delete("/tutor/sessions/{session_id}")
def tutor_delete(session_id: int, request: Request):
    user = require(request)
    if not TutorStore(request.app.state.store).delete_session(user["id"], session_id):
        raise tutor.TutorError(404, "That tutor session was not found.")
    return {"deleted": True}
