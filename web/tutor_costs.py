r"""Review the tutor's real cost per message from the cost log (web/var/users.sqlite, table tutor_usage).
Run:  .venv\Scripts\python.exe -m web.tutor_costs [path to users.sqlite]
Prices are in web/settings.py (TUTOR_PRICES, US dollars per million tokens)."""
import sqlite3
import sys
from collections import defaultdict

from . import settings
from .users import TutorStore, UserStore


def summary(rows):
    by = defaultdict(lambda: dict(questions=0, counted=0, cost=0.0, input=0, output=0, cache_read=0, cache_write=0))
    for r in rows:
        for key in ("all", f"route {r['route']}", f"status {r['status']}", f"day {r['at'][:10]}"):
            b = by[key]
            b["questions"] += 1
            b["counted"] += r["counted"]
            b["cost"] += r["cost_usd"]
            for u in r["usage"].values():
                for k in ("input", "output", "cache_read", "cache_write"):
                    b[k] += u[k]
    lines = [f"{'group':<28}{'questions':>10}{'counted':>9}{'cost $':>10}{'$ / question':>14}{'input':>10}{'cache read':>12}{'output':>9}"]
    for k in sorted(by, key=lambda k: (k != "all", k)):
        b = by[k]
        lines.append(f"{k:<28}{b['questions']:>10}{b['counted']:>9}{b['cost']:>10.4f}{b['cost'] / b['questions']:>14.5f}"
                     f"{b['input']:>10}{b['cache_read']:>12}{b['output']:>9}")
    return "\n".join(lines)


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else settings.USERS_DB
    rows = TutorStore(UserStore(type(settings.USERS_DB)(path))).usage_rows()
    print(summary(rows) if rows else "No tutor questions logged yet.")


if __name__ == "__main__":
    try:
        main()
    except sqlite3.Error as e:
        sys.exit(f"Could not read the cost log: {e}")
