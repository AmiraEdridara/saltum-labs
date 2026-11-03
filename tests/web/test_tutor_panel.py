"""The tutor panel in headless Edge: saved sessions inside the panel (open, new, rename, delete), no Tutor page in
the navigation, the questions-left count, the resizable panel (drag and keyboard, limits, width remembered per
user), full screen at phone width, and no browser processes left. The test servers have a dummy Claude key and no
question is asked, so nothing is spent."""
import subprocess

import pytest

from tests.private_files import require
from web import settings
from web.users import TutorStore, UserStore

require("pricing")              # the panel waits for its questions-left count, which needs the plan limits

PANEL = ("(() => { const l = document.getElementById('tutor-left'); return !!l && /questions left/.test(l.textContent)"
         " && !document.getElementById('tutor').classList.contains('hidden'); })()")
STATE = r"""(() => {
  const t = document.getElementById('tutor'), h = document.getElementById('tutor-resize'), main = document.getElementById('main');
  return {
    url: location.search,
    nav: [...document.querySelectorAll('.nav a')].map((a) => a.firstChild.textContent + ' ' + a.querySelector('kbd').textContent),
    left: document.getElementById('tutor-left').textContent,
    label: document.getElementById('tutor-session').textContent,
    log: [...document.querySelectorAll('#tutor-log .tutor-msg')].map((m) => m.dataset.role + ': '
      + [...m.children].filter((c) => !c.classList.contains('who')).map((c) => c.textContent).join(' ').replace(/\s+/g, ' ').slice(0, 60)),
    sessions_open: !document.getElementById('tutor-sessions').classList.contains('hidden'),
    sessions: [...document.querySelectorAll('#tutor-sessions li[data-session]')].map((li) => ({
      id: Number(li.dataset.session), title: li.querySelector('.session-open')?.textContent || li.textContent,
      current: li.getAttribute('aria-current') === 'true' })),
    width: Math.round(t.getBoundingClientRect().width), page: Math.round(main.getBoundingClientRect().width),
    valuenow: Number(h.getAttribute('aria-valuenow')), valuemin: Number(h.getAttribute('aria-valuemin')),
    valuemax: Number(h.getAttribute('aria-valuemax')), handle_shown: getComputedStyle(h).display !== 'none',
    handle_focusable: h.tabIndex === 0 && h.getAttribute('role') === 'separator',
    full_screen: t.getBoundingClientRect().width >= innerWidth - 1 && t.getBoundingClientRect().height >= innerHeight - 1,
  };
})()"""
# The panel is ready, and the sessions list (when open) has finished loading.
LISTED = ("(() => { const l = document.getElementById('tutor-left'), s = document.getElementById('tutor-sessions');"
          " return !!l && /questions left/.test(l.textContent) && !document.getElementById('tutor').classList.contains('hidden')"
          " && (s.classList.contains('hidden') || !!s.querySelector('.sessions-head, .note, .error')); })()")
KEY = "(() => { const h = document.getElementById('tutor-resize'); h.focus(); h.dispatchEvent(new KeyboardEvent('keydown', {key: '%s', shiftKey: %s, bubbles: true})); })()"
DRAG = ("(() => { const h = document.getElementById('tutor-resize'), r = h.getBoundingClientRect(), x = r.left + 3, y = r.top + 200;"
        " const ev = (type, cx) => h.dispatchEvent(new PointerEvent(type, {clientX: cx, clientY: y, button: 0, pointerId: 7, bubbles: true}));"
        " ev('pointerdown', x); ev('pointermove', x - 60); ev('pointermove', x - %d); ev('pointerup', x - %d); })()")


@pytest.fixture(scope="module")
def sessions(browser):
    """Three saved sessions for the browser's test user, oldest first."""
    users = UserStore(browser.users_db)
    store = TutorStore(users)
    uid = users.by_email("page-test@example.invalid")["id"]
    with users._con() as con:                                   # start clean (the store is shared by the browser tests)
        con.execute("DELETE FROM tutor_sessions WHERE user_id = ?", (uid,))
        con.execute("UPDATE users SET layout = '{}' WHERE id = ?", (uid,))
    ids = []
    for i, title in enumerate(["CPI premium question", "What is vanna", "Payrolls claim"]):
        sid = store.new_session(uid, title)
        store.add_exchange(sid, f"Question {i + 1}", f"Answer {i + 1} from the tutor.", [])
        with users._con() as con:
            con.execute("UPDATE tutor_sessions SET updated_at = ? WHERE id = ?", (f"2026-10-1{i}T12:00:00+00:00", sid))
        ids.append(sid)
    return dict(users=users, store=store, uid=uid, ids=ids)


def click(selector):
    """A click that waits (up to 5 s) for its target, e.g. a list that is still loading."""
    return ("(async () => { const q = () => document.querySelector(%r); for (let i = 0; i < 50 && !q(); i++) "
            "await new Promise((r) => setTimeout(r, 100)); q().click(); await new Promise((r) => setTimeout(r, 300)); })()" % selector)


def until(expr):
    """Wait (up to 5 s) until a JS expression is true, e.g. an opened session has loaded."""
    return f"(async () => {{ for (let i = 0; i < 50 && !({expr}); i++) await new Promise((r) => setTimeout(r, 100)); }})()"


SESSION_SHOWN = "document.querySelectorAll('#tutor-log .tutor-msg').length === 2"


def ok(r):
    assert r["ready"] and r["error"] is None, (r["error"], r["errors"])
    assert not [e for e in r["errors"] if e.startswith("exception")], r["errors"]
    return r["value"]


URL = "/terminal?a=event-study&split=0&tutor=1"


def test_no_tutor_page_and_shortcuts_renumbered(browser, sessions):
    nav, old, key4 = (ok(r) for r in browser.visit([
        dict(url=URL, wait=PANEL, extract=STATE),
        dict(url="/terminal?a=tutor&split=0&tutor=1", wait=PANEL, extract=STATE),
        dict(url=URL, wait=PANEL, extract=STATE, actions=["document.activeElement.blur()",
             "document.dispatchEvent(new KeyboardEvent('keydown', {key: '4', bubbles: true}))"])]))
    assert nav["nav"] == ["Event study 1", "Event index 2", "Vol surface 3", "Help 4"]
    assert "a=event-study" in old["url"]                                  # an old Tutor-page link falls back
    assert "a=help" in key4["url"]


def test_questions_left_shown_in_the_panel(browser, sessions):
    (v,) = (ok(r) for r in browser.visit([dict(url=URL, wait=PANEL, extract=STATE)]))
    n = settings.TUTOR_DAILY_FREE
    assert v["left"] == f"{n} of {n} questions left this day."


def test_sessions_list_open_rename_delete_new(browser, sessions):
    ids = sessions["ids"]
    listed, opened, renamed, deleted, fresh = (ok(r) for r in browser.visit([
        dict(url=URL, wait=LISTED, extract=STATE, actions=[click("#tutor-sessions-button")]),
        dict(url=URL, wait=LISTED, extract=STATE, actions=[
            click("#tutor-sessions-button"), click(f'#tutor-sessions li[data-session="{ids[0]}"] .session-open'),
            until(SESSION_SHOWN), click("#tutor-sessions-button"), click("#tutor-sessions .sessions-head")]),
        dict(url=URL, wait=LISTED, extract=STATE, actions=[
            click("#tutor-sessions-button"), click(f'#tutor-sessions li[data-session="{ids[1]}"] [data-action=rename]'),
            "(async () => { const i = document.querySelector('#tutor-sessions .session-rename input'); i.value = 'Vanna, explained'; "
            "i.form.requestSubmit(); await new Promise((r) => setTimeout(r, 500)); })()"]),
        dict(url=URL, wait=LISTED, extract=STATE, actions=[
            click("#tutor-sessions-button"), click(f'#tutor-sessions li[data-session="{ids[2]}"] [data-action=delete]'),
            click(f'#tutor-sessions li[data-session="{ids[2]}"] [data-action=confirm-delete]')]),
        dict(url=URL, wait=LISTED, extract=STATE, actions=[
            click("#tutor-sessions-button"), click(f'#tutor-sessions li[data-session="{ids[0]}"] .session-open'),
            until(SESSION_SHOWN), click("#tutor-new")]),
    ]))
    # Newest first, inside the panel.
    assert listed["sessions_open"] and [s["title"] for s in listed["sessions"]] == ["Payrolls claim", "What is vanna", "CPI premium question"]
    # Opening one shows it in the panel; it stays the current session while the list is open again.
    assert opened["log"] == ["user: Question 1", "assistant: Answer 1 from the tutor."]
    assert opened["label"] == "Session: CPI premium question"
    assert [s["current"] for s in opened["sessions"]] == [False, False, True]
    # Renamed in the list and in the store.
    assert any(s["title"] == "Vanna, explained" for s in renamed["sessions"])
    assert sessions["store"].session(sessions["uid"], ids[1])["title"] == "Vanna, explained"
    # Deleted from the list and the store.
    assert [s["id"] for s in deleted["sessions"]] == [ids[1], ids[0]]
    assert sessions["store"].session(sessions["uid"], ids[2]) is None
    # New session: an empty panel.
    assert fresh["log"] == [] and fresh["label"] == "New session"


def test_resize_by_keyboard_with_limits(browser, sessions):
    start, wider, maxed, mined = (ok(r) for r in browser.visit([
        dict(url=URL, wait=PANEL, extract=STATE),
        dict(url=URL, wait=PANEL, extract=STATE, actions=[KEY % ("ArrowLeft", "false")] * 3),
        dict(url=URL, wait=PANEL, extract=STATE, actions=[KEY % ("End", "false")]),
        dict(url=URL, wait=PANEL, extract=STATE, actions=[KEY % ("Home", "false")])]))
    assert start["handle_focusable"] and start["handle_shown"]
    assert wider["valuenow"] == start["valuenow"] + 48 and abs(wider["width"] - wider["valuenow"]) <= 1
    assert maxed["valuenow"] == maxed["valuemax"] and maxed["page"] >= 360           # the page stays usable
    assert maxed["valuemax"] <= round(1440 * 0.6)
    assert mined["valuenow"] == mined["valuemin"] == 280 and abs(mined["width"] - 280) <= 1


def test_drag_resize_is_remembered_per_user(browser, sessions):
    dragged, reopened = (ok(r) for r in browser.visit([
        dict(url=URL, wait=PANEL, extract=STATE, actions=[KEY % ("Home", "false"), DRAG % (150, 150),
                                                          "new Promise((r) => setTimeout(r, 900))"]),
        dict(url=URL, wait=PANEL, extract=STATE)]))
    assert dragged["valuenow"] == 280 + 150 and abs(dragged["width"] - 430) <= 1
    assert sessions["users"].layout(sessions["uid"])["tutor_width"] == 430                # saved with the layout
    assert reopened["valuenow"] == 430 and abs(reopened["width"] - 430) <= 1              # and used next time


def test_full_screen_at_phone_width(browser, sessions):
    (v,) = (ok(r) for r in browser.visit([dict(url=URL, width=390, height=844, mobile=True, wait=PANEL, extract=STATE)]))
    assert v["full_screen"] and not v["handle_shown"]


def test_no_browser_processes_left(browser):
    ps = ("(Get-CimInstance Win32_Process -Filter \"Name='msedge.exe'\" | Where-Object { $_.CommandLine -like "
          f"'*{browser.tmp}*' }}).Count")
    out = subprocess.run(["powershell.exe", "-NoProfile", "-Command", ps], capture_output=True, text=True, timeout=60)
    assert out.stdout.strip() in ("", "0"), out.stdout
