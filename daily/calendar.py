"""Trading calendar and New York time. Task Scheduler runs on UK time; this module decides in New York time, so
the weeks when UK and US clocks change on different dates do not matter.

Which days traded is decided by FRED's S&P 500 closes once they are published (as build_dates.py does). This
calendar (NYSE holidays and early closes, config.py) is only used to know, before FRED has the day, whether today
traded, when its options close, and which day is next (for "priced now")."""
import datetime as dt
from zoneinfo import ZoneInfo

import config

NY = ZoneInfo("America/New_York")
READY_AFTER_CLOSE = dt.timedelta(minutes=30)        # the 16:15 snapshot is fetched from 16:45 New York time


def ny_now():
    return dt.datetime.now(NY)


def holidays():
    return set(getattr(config, "NYSE_HOLIDAYS", ()))


def is_trading_day(d):
    return d.weekday() < 5 and d not in holidays()


def next_trading_day(d):
    d += dt.timedelta(days=1)
    while not is_trading_day(d):
        d += dt.timedelta(days=1)
    return d


def previous_trading_day(d):
    d -= dt.timedelta(days=1)
    while not is_trading_day(d):
        d -= dt.timedelta(days=1)
    return d


def options_close(d):
    """16:15 New York time, or 13:15 on an early-close day (config.HALF_DAYS)."""
    t = dt.time(13, 15) if d in config.HALF_DAYS else dt.time(16, 15)
    return dt.datetime.combine(d, t, tzinfo=NY)


def latest_closed_day(now=None):
    """The latest trading day whose 16:15 (13:15) snapshot can be fetched now."""
    now = (now or ny_now()).astimezone(NY)
    d = now.date()
    if is_trading_day(d) and now >= options_close(d) + READY_AFTER_CLOSE:
        return d
    return previous_trading_day(d)


def trading_days_after(start, end):
    """Calendar trading days d with start < d <= end."""
    out, d = [], start
    while True:
        d = next_trading_day(d)
        if d > end:
            return out
        out.append(d)
