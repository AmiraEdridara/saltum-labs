"""Event premiums for events after the v1 cut-off: final once the baseline window is complete, provisional until
then. Both use summarize.event_premium unchanged (the frozen method: the median implied move of ordinary days
within 20 trading days either side, by position in the trading-day list), pointed at the live date list."""
import contextlib

import pandas as pd

import config
import summarize

WINDOW_DAYS = 2 * config.BASELINE_WINDOW        # 40 trading days around the event (20 either side)


@contextlib.contextmanager
def live_date_list(path):
    saved = config.DATE_LIST_CSV
    config.DATE_LIST_CSV = path
    try:
        yield
    finally:
        config.DATE_LIST_CSV = saved


def window(all_dates, event_date):
    """(window days available so far of 40, days still to come, complete?) for one event date."""
    pos = all_dates.index(event_date)
    lo, hi = max(0, pos - config.BASELINE_WINDOW), min(len(all_dates) - 1, pos + config.BASELINE_WINDOW)
    available = (hi - lo + 1) - 1
    after_needed = config.BASELINE_WINDOW - (hi - pos)
    return available, after_needed, after_needed == 0


def premiums(moves, date_list_path, events, attempted):
    """For each event date in `events`: ('final' or 'provisional', row). `attempted` is the set of dates whose
    event_moves step has run (measured or skipped); a window counts as complete only when every one of its 20
    following trading days has been attempted, so a late day cannot change a final premium."""
    all_dates = sorted(pd.read_csv(date_list_path, parse_dates=["date"]).date.dt.date.unique())
    with live_date_list(date_list_path):
        table = summarize.event_premium(moves)
    table["d"] = pd.to_datetime(table.date).dt.date
    out = {}
    for ev in events:
        rows = table[table.d == ev]
        if rows.empty:
            continue
        available, still_to_come, complete = window(all_dates, ev)
        pos = all_dates.index(ev)
        after = all_dates[pos + 1: pos + 1 + config.BASELINE_WINDOW]
        complete = complete and all(d in attempted for d in after)
        for r in rows.drop(columns=["d"]).to_dict("records"):
            if complete:
                out.setdefault(ev, []).append(("final", r))
            else:
                r.update(window_days_used=available, window_days=WINDOW_DAYS)
                out.setdefault(ev, []).append(("provisional", r))
    return out
