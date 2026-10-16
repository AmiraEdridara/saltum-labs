"""Runs the API and the web server on test ports with a test token and a temporary user store, signs up a
test user, and visits pages in headless Edge (tests/web/browser/cdp_visit.mjs). Skips if Edge or Node is
missing. The real .env token and web/var/users.sqlite are never used."""
import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import time

import httpx
import pytest

import config

EDGE = next((p for p in (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                         r"C:\Program Files\Microsoft\Edge\Application\msedge.exe") if os.path.exists(p)), None)
NODE = shutil.which("node")
API_PORT, WEB_PORT, CDP_PORT = 18000, 18080, 19333
TOKEN = "page-test-token-not-the-real-one"
SCRIPT = pathlib.Path(__file__).parent / "browser" / "cdp_visit.mjs"


def _wait_port(port, seconds=30):
    end = time.time() + seconds
    while time.time() < end:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.2)
    raise RuntimeError(f"nothing listening on {port}")


def stop_edge(profile):
    """Headless Edge leaves helper processes behind; stop every msedge.exe started with this test profile
    (and only those: the user's own Edge has a different profile)."""
    # Stop them, then check again until none are left (helpers can start or linger while Edge shuts down).
    find = ("Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" | Where-Object { $_.CommandLine -like "
            f"'*{profile}*' }}")
    ps = (f"for ($i = 0; $i -lt 30; $i++) {{ $p = @({find}); if ($p.Count -eq 0) {{ break }}; "
          "$p | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }; Start-Sleep -Milliseconds 500 }")
    subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps], check=False, timeout=90)


class Browser:
    def __init__(self, tmp):
        self.tmp = tmp
        env = dict(os.environ, MACRO_WIRE_DEV_TOKEN=TOKEN, MW_USERS_DB=str(tmp / "users.sqlite"),
                   MW_API_BASE=f"http://127.0.0.1:{API_PORT}",
                   # A dummy key: the panel counts as configured (so it shows the questions left), but any real call
                   # would be refused by the API, so the page tests can never spend money. They never ask a question.
                   ANTHROPIC_API_KEY="not-a-real-key-page-tests")
        run = [sys.executable, "-m", "uvicorn", "--host", "127.0.0.1", "--log-level", "warning"]
        self.procs = [subprocess.Popen(run + ["api.main:app", "--port", str(API_PORT)], cwd=config.PROJECT_DIR, env=env),
                      subprocess.Popen(run + ["web.server:app", "--port", str(WEB_PORT)], cwd=config.PROJECT_DIR, env=env)]
        _wait_port(API_PORT)
        _wait_port(WEB_PORT)
        r = httpx.post(f"http://127.0.0.1:{WEB_PORT}/auth/signup",
                       json=dict(email="page-test@example.invalid", password="page test password", accept_terms=True))
        r.raise_for_status()
        self.session = r.cookies["mw_session"]
        self.users_db = tmp / "users.sqlite"
        self.runs = 0

    def visit(self, visits, edge_args=(), headed=False, block=()):
        self.runs += 1
        # A fresh DevTools port per run: the previous Edge may still be releasing its port.
        job = dict(edge=EDGE, port=CDP_PORT + self.runs, profile=str(self.tmp / f"edge-{self.runs}"), origin=f"http://127.0.0.1:{WEB_PORT}",
                   cookie=dict(name="mw_session", value=self.session), visits=visits, edge_args=list(edge_args), headed=headed, block=list(block))
        jf, out = self.tmp / f"job-{self.runs}.json", self.tmp / f"out-{self.runs}.json"
        jf.write_text(json.dumps(job), encoding="utf-8")
        try:
            r = subprocess.run([NODE, str(SCRIPT), str(jf), str(out)], capture_output=True, text=True, timeout=600)
            if r.returncode:
                raise RuntimeError(f"cdp_visit.mjs failed (exit {r.returncode}): {r.stderr[-2000:]}")
        finally:
            stop_edge(job["profile"])
        return json.loads(out.read_text(encoding="utf-8"))

    def close(self):
        for p in self.procs:
            p.terminate()
        for p in self.procs:
            p.wait(timeout=20)


@pytest.fixture(scope="session")
def browser(tmp_path_factory):
    if not (EDGE and NODE):
        pytest.skip("headless Edge or Node not available")
    b = Browser(tmp_path_factory.mktemp("browser"))
    yield b
    b.close()
