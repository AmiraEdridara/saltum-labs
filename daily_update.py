r"""Daily update (product stage 7): new trading days, new events and "priced now", appended with the frozen method.
Run from the project folder:
    .venv\Scripts\python.exe daily_update.py --plan        what a run would do now (reads FRED; no ThetaData, no writes)
    .venv\Scripts\python.exe daily_update.py               catch up and update now
    .venv\Scripts\python.exe daily_update.py --scheduled   the same, from Task Scheduler (scripts/register_daily_update.ps1)
Log: data/live/update_log.txt. Status: data/live/update_status.json. See README, "Daily update"."""
import sys

from daily import run as daily


def main(argv):
    if "--plan" in argv:
        daily.print_plan(daily.plan())
        return 0
    return daily.run()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
