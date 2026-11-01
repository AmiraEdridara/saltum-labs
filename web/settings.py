"""Settings for the terminal web server (separate from the pipeline's config.py).
MW_USERS_DB and MW_API_BASE override two of them; the page tests use this to run on test ports."""
import os
import pathlib

import config

WEB_DIR = config.PROJECT_DIR / "web"
STATIC_DIR = WEB_DIR / "static"
USERS_DB = pathlib.Path(os.getenv("MW_USERS_DB", WEB_DIR / "var" / "users.sqlite"))   # local dev users; git-ignored
API_BASE = os.getenv("MW_API_BASE", "http://127.0.0.1:8000")     # the read-only API (api/), local only
SESSION_HOURS = 12
SESSION_COOKIE = "mw_session"
MIN_PASSWORD = 10

PLANS = ("student", "tutor_plus")               # docs/prd.md, Page 4 decisions
TUTOR_DAILY_FREE = 7                            # placeholder: the real plan limits and prices are private
TUTOR_MONTHLY_PLUS = 250                        # placeholder
TUTOR_PLUS_GBP_MONTH = 12                       # placeholder
# The terminal's data credit: served with the logged-in account, so it is never in a public file.
TERMINAL_FOOTER = ("Data: SPX and SPXW options from ThetaData; Treasury bills and VIX from FRED.",
                   "16:15 ET snapshots, 2020-01 to the latest close.")

# ------------------------------------------------------------ AI tutor (docs/prd.md, Page 4)
TUTOR_MODEL_SIMPLE = "claude-haiku-5-5"          # definitions
TUTOR_MODEL_ANALYSIS = "claude-sonnet-5-5"       # everything else
# US dollars per million tokens, from https://platform.claude.com/docs/en/about-claude/pricing (read 2026-10-15).
# Haiku 5.5 prices are for prompts up to 100,000 tokens; the tutor's prompts stay far below that.
TUTOR_PRICES = {
    "claude-sonnet-5-5": dict(input=2.00, cache_write=2.50, cache_read=0.10, output=10.00),
    "claude-haiku-5-5": dict(input=0.10, cache_write=0.125, cache_read=0.01, output=0.50),
}
TUTOR_SAVE_SESSIONS_PLANS = ("student", "tutor_plus")   # set to ("tutor_plus",) to keep saved sessions for Tutor plus only
TUTOR_MAX_TOOL_ROUNDS = 6
TUTOR_HISTORY_TURNS = 10                          # earlier question-and-answer pairs sent with a new question
TUTOR_MAX_QUESTION = 2000                         # characters
TUTOR_MAX_TOKENS = 1200                           # per answer
TUTOR_WIDTH_MIN, TUTOR_WIDTH_MAX = 280, 1600      # saved panel width in px (the browser also caps it to the window)
