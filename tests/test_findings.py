"""GET /findings returns findings_v1.md section by section, with the file's checksum."""
import hashlib

import config
from tests.private_files import needs


@needs("findings")
def test_findings(get):
    path = config.PROJECT_DIR / "data" / "findings_v1.md"
    j = get("/findings")
    assert j["data"]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    titles = [s["title"] for s in j["data"]["sections"]]
    assert j["n"] == len(titles) == 7
    assert titles[0] == "Event study: implied vs actual one-day move" and "What this does not show" in titles
    assert j["data"]["sections"][0]["markdown"].strip() in path.read_text(encoding="utf-8")
