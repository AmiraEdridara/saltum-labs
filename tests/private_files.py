"""The private files some tests need. They stay on the owner's PC and out of git: the ThetaData-derived database and
results are kept private under ThetaData's personal-use terms, and the plan prices are private too. In a clone without
them, the tests that need them are skipped with the reason, and everything else runs."""
import pytest

import config

PRIVATE = {
    "database": config.PROJECT_DIR / "data" / config.DUCKDB_PATH.name,   # the real file, not the per-run test copy
    "date list": config.ALL_DATES_STATUS_CSV,
    "findings": config.PROJECT_DIR / "data" / "findings_v1.md",
    "stage3 summary": config.PROJECT_DIR / "data" / "stage3_periods.txt",
    "stage5b summary": config.PROJECT_DIR / "data" / "stage5b_summary.txt",
    "event pages": config.PROJECT_DIR / "data" / "figures" / "events",
    "v1 checksums": config.PROJECT_DIR / "data" / "live" / "v1_checksums.csv",
    "pricing": config.PROJECT_DIR / "web" / "pricing.json",
    "PRD": config.PROJECT_DIR / "docs" / "prd.md",
}
HAVE = {name: path.exists() for name, path in PRIVATE.items()}


def needs(*names):
    """Skip unless every named private file is on this PC."""
    missing = [n for n in names if not HAVE[n]]
    reason = ("needs private files kept out of git (ThetaData data and results, personal-use terms; or the plan prices): "
              + ", ".join(f"{n} ({PRIVATE[n].relative_to(config.PROJECT_DIR).as_posix()})" for n in missing))
    return pytest.mark.skipif(bool(missing), reason=reason)


def require(*names):
    """Skip the whole module, with the reason, unless every named private file is on this PC. Call it before the
    module reads any of them."""
    mark = needs(*names)
    if mark.args[0]:
        pytest.skip(mark.kwargs["reason"], allow_module_level=True)
