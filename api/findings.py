"""GET /findings: the frozen results in data/findings_v1.md, one entry per top-level section."""
import hashlib
import re

import config
from .common import EDU, envelope

PATH = config.PROJECT_DIR / "data" / "findings_v1.md"


def get_findings():
    text = PATH.read_text(encoding="utf-8")
    parts = re.split(r"^## ", text, flags=re.M)
    sections = []
    for p in parts[1:]:
        title, _, body = p.partition("\n")
        m = re.match(r"(\d+)\.\s+(.*)", title.strip())
        sections.append(dict(id=m.group(1) if m else re.sub(r"\W+", "-", title.strip().lower()).strip("-"),
                             title=m.group(2) if m else title.strip(), markdown=body.strip()))
    data = dict(sha256=hashlib.sha256(PATH.read_bytes()).hexdigest(), sections=sections)
    return envelope(["data/findings_v1.md"], len(sections), "2020-01..2026-10", {},
                    f"Frozen results; each section names its own sources and caveats. {EDU}", data)
