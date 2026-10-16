r"""The shared read-only API. Run on localhost only:
    .venv\Scripts\python.exe -m uvicorn api.main:app --host 127.0.0.1 --port 8000
Every endpoint requires `Authorization: Bearer <token>` (api/auth.py). Nothing is written anywhere.
"""
from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

import codecheck
from . import db, definitions, events, findings, index, surface
from . import next_event as next_event_
from .auth import default_authenticator, require_user
from .common import NotFound
from .validation import BadInput

# No /docs, /redoc or /openapi.json: they would be endpoints without auth.
app = FastAPI(title="Macro Wire API", docs_url=None, redoc_url=None, openapi_url=None,
              dependencies=[Depends(require_user)])
app.state.authenticator = default_authenticator()
if not getattr(app.state.authenticator, "configured", True):
    print("No dev token configured (MACRO_WIRE_DEV_TOKEN in .env): every request will get 401.")


@app.middleware("http")
async def code_check(request: Request, call_next):
    token = db.begin_request()                          # one short read-only connection per request
    try:
        response = await call_next(request)
    finally:
        db.end_request(token)
    if codecheck.changed_since_start("api", "."):       # the API's own code or the pipeline modules it imports
        response.headers[codecheck.HEADER] = "api"
    return response


@app.exception_handler(BadInput)
@app.exception_handler(RequestValidationError)
def bad_input(request: Request, exc):
    return JSONResponse(status_code=400, content=dict(error="bad input", detail=str(exc)))


@app.exception_handler(db.DataUpdating)
def data_updating(request: Request, exc):
    return JSONResponse(status_code=503, content=dict(error="data updating", detail=str(exc)), headers={"Retry-After": "5"})


@app.exception_handler(NotFound)
def not_found(request: Request, exc):
    return JSONResponse(status_code=404, content=dict(error="not found", detail=str(exc)))


@app.get("/events")
def list_events(type: str = "all", period: str = "both", data: str = "v1"):
    return events.list_events(db.cursor(), type, period, data)


@app.get("/events/next")                   # before /events/{date}, so "next" is not read as a date
def next_event(type: str = "all"):
    return next_event_.next_event(db.cursor(), type)


@app.get("/events/index")                  # before /events/{date}, so "index" is not read as a date
def event_index(type: str = "all", period: str = "both", exclude_flagged: str = "false", include_ordinary: str = "false"):
    return index.get_index(db.cursor(), type, period, exclude_flagged, include_ordinary)


@app.get("/events/{date}")
def get_event(date: str):
    return events.get_event(db.cursor(), date)


@app.get("/events/{date}/skew")
def get_skew(date: str):
    return events.get_skew(db.cursor(), date)


@app.get("/surface/{date}")
def get_surface(date: str):
    return surface.get_surface(db.cursor(), date)


@app.get("/dates")
def get_dates():
    return surface.get_dates()


@app.get("/definitions")
def get_definitions(term: str | None = None):
    return definitions.get_definitions(term)


@app.get("/findings")
def get_findings():
    return findings.get_findings()
