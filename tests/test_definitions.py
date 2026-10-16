"""GET /definitions: the PRD's five terms word for word, the ten extra site terms, lookup by term, and auth."""
import re

import config

PRD = (config.PROJECT_DIR / "docs" / "prd.md").read_text(encoding="utf-8")


def test_all_terms(get):
    j = get("/definitions")
    terms = [t["term"] for t in j["data"]["terms"]]
    assert j["n"] == len(terms) == 15 and "not advice" in j["caveat"]
    assert terms[:5] == ["Implied move", "Actual move", "Baseline", "Premium", "Variance premium"]


def test_prd_terms_word_for_word(get):
    for t in get("/definitions")["data"]["terms"][:5]:
        assert re.search(rf"^\| {re.escape(t['term'])} \| {re.escape(t['definition'])} \|$", PRD, re.M), t["term"]
        assert t["source"] == "docs/prd.md, Page 1"


def test_lookup_and_unknown(get, client):
    assert [t["term"] for t in get("/definitions?term=risk%20reversal")["data"]["terms"]] == ["25-delta risk reversal (RR25)"]
    assert get("/definitions?term=VANNA")["data"]["terms"][0]["term"] == "Vanna"
    assert "No definition" in get("/definitions?term=gamma%20squeeze", status=404)["detail"]
    assert client.get("/definitions").status_code == 401
