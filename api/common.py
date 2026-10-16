"""The response envelope shared by every endpoint, and JSON clean-up (NaN becomes null)."""
import datetime as dt
import math

import numpy as np
import pandas as pd

EDU = "Education only, not advice."
LIVE_NOTE = "Includes data after 2026-10-09 (live, added by the daily update; not in findings_v1)."


class NotFound(Exception):
    pass


def clean(v):
    if isinstance(v, dict):
        return {k: clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [clean(x) for x in v]
    if isinstance(v, (np.bool_, bool)):
        return bool(v)
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (float, np.floating)):
        return None if math.isnan(v) else float(v)
    if isinstance(v, (pd.Timestamp, dt.datetime)):
        return v.date().isoformat()
    if isinstance(v, dt.date):
        return v.isoformat()
    if v is pd.NA or v is pd.NaT:
        return None
    return v


def count_range(ns):
    """'52 to 78', or '78' when every count is the same."""
    return str(min(ns)) if min(ns) == max(ns) else f"{min(ns)} to {max(ns)}"


def envelope(source, n, period, params, caveat, data):
    import config
    return clean(dict(source=list(source), n=n, period=period, params=params, caveat=caveat, data=data,
                      v1_end=config.V1_END))
