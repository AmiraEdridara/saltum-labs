"""GET /definitions: plain definitions of the terms the site shows (the tutor's get_definition tool, and the
pages' definition lists). The first five are word for word from docs/prd.md (Page 1); the rest are shortened
from the README's definitions (Stages 4, 5 and 5b), which name the exact method."""
import re

from .common import EDU, NotFound, envelope

PRD = "docs/prd.md, Page 1"
S5 = "README.md, Skew and vanna (Stage 5), Definitions"
S5B = "README.md, Stage 5b, Definitions"
TERMS = [
    ("Implied move", "ATM straddle divided by the forward, priced at the 16:15 close the day before", PRD),
    ("Actual move", "Size of the move from that forward to the close on the event day", PRD),
    ("Baseline", "Median implied move on ordinary days within 20 trading days either side", PRD),
    ("Premium", "Implied move minus baseline", PRD),
    ("Variance premium", "Implied move squared minus baseline squared", PRD),
    ("30-day ATM vol", "Implied volatility at the money (call-equivalent delta 0.50), read from the fitted smiles and "
     "interpolated to 30 days in total variance between the Friday expiries either side of 30 days.", S5),
    ("25-delta risk reversal (RR25)", "The implied vol of the 25-delta put minus that of the 25-delta call, in vol "
     "points, at 30 days. Positive means puts are dearer than calls; a fall means the skew flattened.", S5),
    ("Put wing", "The implied vol of the 10-delta put minus the at-the-money vol, in vol points, at 30 days.", S5),
    ("Fixed-strike ATM change", "The change in 30-day implied vol at one fixed strike (the day before's at-the-money "
     "strike), from the day before to the event day. Unlike the 50-delta ATM, it does not move along the skew when "
     "the index moves.", S5B),
    ("Vanna", "How much an option's delta changes for a 1 vol point change in implied vol (delta per vol point). "
     "Shown per option; no dealer or open-interest exposure is computed.", S5),
    ("Flagged day", "A day whose fitted smiles miss the computed options by more than 0.5 vol points somewhere, or "
     "whose total variance falls with longer expiry. Its numbers are less reliable and results are shown with and "
     "without flagged days.", S5),
    ("Ordinary control pair", "Two consecutive ordinary trading days, with no CPI, FOMC or payrolls day on either or "
     "on the day after. 100 pairs, spread evenly in time and drawn with a fixed seed, show how much the measures move "
     "on a normal day.", S5B),
    ("Survives both controls", "For a measure and event type: the 90% interval of event minus ordinary excludes 0, "
     "both with and without flagged days. For ATM it uses the fixed-strike change. It is a screen, not proof.", S5B),
    ("90% interval", "A bootstrap range for event minus ordinary: events and ordinary pairs are resampled with "
     "replacement 5,000 times, and the interval runs from the 5th to the 95th percentile of the difference in "
     "medians.", S5B),
    ("Event minus ordinary", "The median change on event days minus the median change over the ordinary control "
     "pairs, in vol points.", S5B),
]


def _key(s):
    return re.sub(r"[^a-z0-9]+", " ", s.lower()).strip()


def get_definitions(term=None):
    rows = [dict(term=t, definition=d, source=s) for t, d, s in TERMS]
    if term:
        k = _key(term)
        rows = [r for r in rows if k == _key(r["term"]) or k in _key(r["term"]) or _key(r["term"]).startswith(k)]
        if not rows:
            raise NotFound(f"No definition for {term!r}. Terms: " + ", ".join(t for t, _, _ in TERMS))
    return envelope(["docs/prd.md", "README.md"], len(rows), "n/a", dict(term=term) if term else {},
                    f"Plain definitions; the README gives the exact method. {EDU}", dict(terms=rows))
