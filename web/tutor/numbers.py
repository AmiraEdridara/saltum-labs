"""The number check: every number in an answer must come from a tool result (this session), the question or the
page context, or be one of a few method constants. Run on every answer before it is shown."""
import json
import re

DATE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
YEAR_RANGE = re.compile(r"\b(20[12]\d)\s*[–-]\s*(\d{2}|20[12]\d)\b")            # 2020–23, 2023-2026
TIME = re.compile(r"\b\d{1,2}:\d{2}\b")                                           # 16:15
NUMBER = re.compile(r"(?<![\w.])[-+−]?\d[\d,]*(?:\.\d+)?")
# Method constants named in the definitions and prompt (deltas, days, 0.5 vol point flag, 90% intervals, ...).
# 500 is the index's name (S&P 500).
CONSTANTS = {0.0, 1.0, 2.0, 3.0, 0.5, 5.0, 9.0, 10.0, 14.0, 20.0, 25.0, 30.0, 50.0, 90.0, 95.0, 100.0, 500.0, 5000.0, 6.0}


def _numbers_in(text):
    text = TIME.sub(" ", YEAR_RANGE.sub(" ", DATE.sub(" ", text)))      # dates first, so 2026-09-12 is not a year range
    out = []
    for m in NUMBER.finditer(text):
        s = m.group(0).replace("−", "-").replace(",", "")
        if s in ("-", "+"):
            continue
        try:
            out.append((m.group(0), float(s), len(s.split(".")[1]) if "." in s else 0))
        except ValueError:
            pass
    return out


def _source_values(texts):
    vals, dates, years = set(), set(), set()
    for t in texts:
        for m in DATE.finditer(t):
            dates.add(m.group(0))
            years.add(m.group(1))
        for m in re.finditer(r"\b20[12]\d\b", t):
            years.add(m.group(0))
        for m in NUMBER.finditer(t.replace("−", "-")):
            try:
                vals.add(float(m.group(0).replace(",", "")))
            except ValueError:
                pass
    return vals, dates, years


def unverified(answer, sources):
    """Numbers and dates in `answer` that none of `sources` (strings or JSON-able objects) contains."""
    # ensure_ascii=False keeps the "−" minus of findings_v1.md as it is (escaped, "−" would read as 2212...).
    texts = [s if isinstance(s, str) else json.dumps(s, ensure_ascii=False) for s in sources]
    vals, dates, years = _source_values(texts)
    bad = [m.group(0) for m in DATE.finditer(answer) if m.group(0) not in dates]
    for m in YEAR_RANGE.finditer(DATE.sub(" ", answer)):                          # year ranges: the years must be ones the data covers
        y1 = m.group(1)
        if y1 not in years and y1 not in {"2020", "2021", "2022", "2023", "2024", "2025", "2026"}:
            bad.append(m.group(0))
    for raw, v, dp in _numbers_in(answer):
        if abs(v) in CONSTANTS or (1990 <= abs(v) <= 2030 and dp == 0 and str(int(abs(v))) in years | {"2020", "2023", "2026"}):
            continue
        tol = 0.5 * 10 ** -dp + 1e-9
        if not any(abs(abs(v) - abs(s)) <= tol for s in vals):
            bad.append(raw)
    return bad
