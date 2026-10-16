"""Is this server process running older code than the files on disk?

A server keeps the code it started with. After a code change it must be restarted, or it keeps serving the
old behaviour (on 2026-10-15 an old web server sent a security policy that blocked Plotly, so the Vol surface
page drew no charts). The API and the web server mark their responses when this happens, and the terminal
shows a banner asking for a restart.
"""
import time

import config

STARTED = time.time()
HEADER = "X-MW-Code-Changed"
_cache = {}                          # folders -> (checked at, result)


def changed_since_start(*folders, started=None):
    """True if any .py file in these project folders ('.' is the project folder) was saved after the start."""
    started = STARTED if started is None else started
    now = time.time()
    if started is STARTED and folders in _cache and now - _cache[folders][0] < 2:    # checked at most every 2 s
        return _cache[folders][1]
    newest = max((p.stat().st_mtime for f in folders for p in (config.PROJECT_DIR / f).glob("*.py")), default=0.0)
    value = newest > started
    if started is STARTED:
        _cache[folders] = (now, value)
    return value
