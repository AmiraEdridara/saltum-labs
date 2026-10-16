"""Input checks. Bad input raises BadInput, which the app turns into a 400 with a clear message."""
import datetime as dt

SPLIT = dt.date(2023, 6, 1)          # first date of the 2023-06..now period (stage3_periods.py)
EARLY, LATE = "2020-01..2023-05", "2023-06..now"
PERIODS = (EARLY, LATE, "both")
TYPES = ("CPI", "NFP", "FOMC")
_TYPE_NAMES = {"CPI": "CPI", "NFP": "NFP", "PAYROLLS": "NFP", "FOMC": "FOMC", "ALL": "all"}


class BadInput(Exception):
    pass


DATASETS = ("v1", "live")


def dataset(value):
    d = (value or "v1").strip().lower()
    if d not in DATASETS:
        raise BadInput(f"data must be v1 (the frozen results, the default) or live; got {value!r}")
    return d


def is_live(day):
    """Dated after the frozen v1 cut-off (config.V1_END): added by the daily update."""
    import config
    return day > config.V1_END


def event_type(value):
    t = _TYPE_NAMES.get((value or "all").strip().upper())
    if t is None:
        raise BadInput(f"type must be one of CPI, NFP (or payrolls), FOMC, all; got {value!r}")
    return t


def period(value):
    p = (value or "both").strip()
    if p not in PERIODS:
        raise BadInput(f"period must be one of {', '.join(PERIODS)}; got {value!r}")
    return p


def date(value):
    try:
        if len(value) != 10:
            raise ValueError
        return dt.date.fromisoformat(value)
    except (TypeError, ValueError):
        raise BadInput(f"date must be YYYY-MM-DD; got {value!r}") from None


def flag(value, name):
    v = (value or "false").strip().lower()
    if v in ("true", "1", "yes"):
        return True
    if v in ("false", "0", "no"):
        return False
    raise BadInput(f"{name} must be true or false; got {value!r}")


def period_of(day):
    return EARLY if day < SPLIT else LATE


def types_of(t):
    """Event types selected by the `type` parameter (a CPI+FOMC day counts under both)."""
    return TYPES if t == "all" else (t,)
