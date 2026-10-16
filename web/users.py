"""Local user store (SQLite). Holds what docs/prd.md Page 7 lists per user: login details (dev only;
Supabase holds them later), the plan, the tutor message counters and the layout settings."""
import datetime as dt
import json
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id                INTEGER PRIMARY KEY,
    email             TEXT NOT NULL UNIQUE,
    password_hash     TEXT,                              -- dev provider only
    terms_accepted_at TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    plan              TEXT NOT NULL DEFAULT 'student' CHECK (plan IN ('student', 'tutor_plus')),
    tutor_msgs_today  INTEGER NOT NULL DEFAULT 0,
    tutor_day         TEXT,
    tutor_msgs_month  INTEGER NOT NULL DEFAULT 0,
    tutor_month       TEXT,
    layout            TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS tutor_sessions (
    id         INTEGER PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    title      TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tutor_messages (
    id         INTEGER PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES tutor_sessions (id) ON DELETE CASCADE,
    role       TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    text       TEXT NOT NULL,
    tools      TEXT NOT NULL DEFAULT '[]',          -- the tool calls and results behind an answer (JSON)
    created_at TEXT NOT NULL
);
-- One row per question, for reviewing the real cost per message. No message text. Kept when an account is
-- deleted, without the link to it.
CREATE TABLE IF NOT EXISTS tutor_usage (
    id            INTEGER PRIMARY KEY,
    at            TEXT NOT NULL,
    user_id       INTEGER REFERENCES users (id) ON DELETE SET NULL,
    plan          TEXT,
    route         TEXT NOT NULL,                    -- simple, analysis, or simple->analysis
    status        TEXT NOT NULL,                    -- answered, unverified, error
    counted       INTEGER NOT NULL,                 -- 1 if it counted towards the user's limit
    tool_calls    INTEGER NOT NULL,
    regenerated   INTEGER NOT NULL,
    usage         TEXT NOT NULL,                    -- tokens per model (JSON)
    cost_usd      REAL NOT NULL,
    untraced      TEXT NOT NULL DEFAULT '[]'        -- numbers the check could not trace (JSON); no message text
);
CREATE TABLE IF NOT EXISTS sessions (
    id_hash    TEXT PRIMARY KEY,                         -- sha256 of the cookie value
    user_id    INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL
);
"""
PUBLIC = ("id", "email", "plan", "created_at", "terms_accepted_at", "tutor_msgs_today", "tutor_day",
          "tutor_msgs_month", "tutor_month")


def now():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


class UserStore:
    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = str(path)
        with self._con() as con:
            con.executescript(SCHEMA)
            cols = {r[1] for r in con.execute("PRAGMA table_info(tutor_usage)")}
            if "untraced" not in cols:                 # stores created before this column existed
                con.execute("ALTER TABLE tutor_usage ADD COLUMN untraced TEXT NOT NULL DEFAULT '[]'")

    def _con(self):
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        return con

    def create(self, email, password_hash):
        t = now().isoformat()
        with self._con() as con:
            cur = con.execute("INSERT INTO users (email, password_hash, terms_accepted_at, created_at) VALUES (?, ?, ?, ?)",
                              (email, password_hash, t, t))
            return cur.lastrowid

    def by_email(self, email):
        with self._con() as con:
            r = con.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        return dict(r) if r else None

    def get(self, user_id):
        with self._con() as con:
            r = con.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(r) if r else None

    def delete(self, user_id):
        with self._con() as con:
            con.execute("DELETE FROM users WHERE id = ?", (user_id,))    # sessions go with it (cascade)

    def set_plan(self, user_id, plan):
        with self._con() as con:
            con.execute("UPDATE users SET plan = ? WHERE id = ?", (plan, user_id))

    def layout(self, user_id):
        u = self.get(user_id)
        return json.loads(u["layout"]) if u else {}

    def set_layout(self, user_id, layout):
        with self._con() as con:
            con.execute("UPDATE users SET layout = ? WHERE id = ?", (json.dumps(layout), user_id))

    def count_tutor_message(self, user_id, when=None):
        """Add one tutor message; the day and month counters restart on a new UTC day or month.
        Counting only: the limits (settings.TUTOR_*) are applied in the tutor stage."""
        when = when or now()
        day, month = when.date().isoformat(), when.strftime("%Y-%m")
        with self._con() as con:
            con.execute("""UPDATE users SET
                tutor_msgs_today = CASE WHEN tutor_day = ? THEN tutor_msgs_today + 1 ELSE 1 END, tutor_day = ?,
                tutor_msgs_month = CASE WHEN tutor_month = ? THEN tutor_msgs_month + 1 ELSE 1 END, tutor_month = ?
                WHERE id = ?""", (day, day, month, month, user_id))
        return self.get(user_id)

    # sessions
    def add_session(self, id_hash, user_id, expires_at):
        with self._con() as con:
            con.execute("DELETE FROM sessions WHERE expires_at < ?", (now().isoformat(),))
            con.execute("INSERT INTO sessions VALUES (?, ?, ?)", (id_hash, user_id, expires_at.isoformat()))

    def session_user(self, id_hash):
        with self._con() as con:
            r = con.execute("SELECT user_id FROM sessions WHERE id_hash = ? AND expires_at > ?",
                            (id_hash, now().isoformat())).fetchone()
        return self.get(r["user_id"]) if r else None

    def drop_session(self, id_hash):
        with self._con() as con:
            con.execute("DELETE FROM sessions WHERE id_hash = ?", (id_hash,))


def public(user):
    return {k: user[k] for k in PUBLIC}


# ------------------------------------------------------------ tutor sessions and cost log
class TutorStore:
    """Sessions, messages and the per-question cost log, in the same SQLite file as the users."""

    def __init__(self, users):
        self.users = users

    def _con(self):
        return self.users._con()

    def new_session(self, user_id, title):
        t = now().isoformat()
        with self._con() as con:
            return con.execute("INSERT INTO tutor_sessions (user_id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
                               (user_id, title[:80], t, t)).lastrowid

    def session(self, user_id, session_id):
        with self._con() as con:
            r = con.execute("SELECT * FROM tutor_sessions WHERE id = ? AND user_id = ?", (session_id, user_id)).fetchone()
        return dict(r) if r else None

    def sessions(self, user_id):
        with self._con() as con:
            rows = con.execute("""SELECT s.*, count(m.id) AS messages FROM tutor_sessions s
                                  LEFT JOIN tutor_messages m ON m.session_id = s.id
                                  WHERE s.user_id = ? GROUP BY s.id ORDER BY s.updated_at DESC""", (user_id,)).fetchall()
        return [dict(r) for r in rows]

    def messages(self, session_id):
        with self._con() as con:
            rows = con.execute("SELECT role, text, tools, created_at FROM tutor_messages WHERE session_id = ? ORDER BY id",
                               (session_id,)).fetchall()
        return [dict(r, tools=json.loads(r["tools"])) for r in rows]

    def add_exchange(self, session_id, question, answer, tools):
        t = now().isoformat()
        with self._con() as con:
            con.execute("INSERT INTO tutor_messages (session_id, role, text, created_at) VALUES (?, 'user', ?, ?)", (session_id, question, t))
            con.execute("INSERT INTO tutor_messages (session_id, role, text, tools, created_at) VALUES (?, 'assistant', ?, ?, ?)",
                        (session_id, answer, json.dumps(tools), t))
            con.execute("UPDATE tutor_sessions SET updated_at = ? WHERE id = ?", (t, session_id))

    def rename_session(self, user_id, session_id, title):
        with self._con() as con:
            return con.execute("UPDATE tutor_sessions SET title = ? WHERE id = ? AND user_id = ?",
                               (title, session_id, user_id)).rowcount

    def delete_session(self, user_id, session_id):
        with self._con() as con:
            return con.execute("DELETE FROM tutor_sessions WHERE id = ? AND user_id = ?", (session_id, user_id)).rowcount

    def log_usage(self, **row):
        row = dict(row, at=now().isoformat(), usage=json.dumps(row["usage"]))
        cols = ", ".join(row)
        with self._con() as con:
            con.execute(f"INSERT INTO tutor_usage ({cols}) VALUES ({', '.join('?' * len(row))})", tuple(row.values()))

    def usage_rows(self):
        with self._con() as con:
            return [dict(r, usage=json.loads(r["usage"])) for r in con.execute("SELECT * FROM tutor_usage ORDER BY id")]
