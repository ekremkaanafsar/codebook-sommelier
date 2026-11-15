"""PROTOCOL.md documents the whole schema and taxonomy and carries no local terminology marks."""
import re
from pathlib import Path

import pytest

from schema import CODES, COLUMNS

ROOT = Path(__file__).resolve().parent.parent
PROTOCOL = ROOT / "PROTOCOL.md"
TERMS = ROOT / "local" / "marker_terms.txt"


def _text():
    return PROTOCOL.read_text(encoding="utf-8")


def test_protocol_lists_every_schema_column():
    text = _text()
    missing = [c for c in COLUMNS if "`" + c + "`" not in text]
    assert missing == []


def test_protocol_lists_every_domain_code():
    text = _text()
    missing = [c for c in CODES if not re.search(r"\|\s*" + re.escape(c) + r"\s*\|", text)]
    assert missing == []


@pytest.mark.skipif(not TERMS.is_file(), reason="local/marker_terms.txt is local configuration, not exported")
def test_protocol_has_no_marker_terms():
    pats = [re.compile(t.strip(), re.I) for t in TERMS.read_text(encoding="utf-8").splitlines()
            if t.strip() and not t.strip().startswith("#")]
    hits = [(i, p.pattern) for i, line in enumerate(_text().splitlines(), 1) for p in pats if p.search(line)]
    assert hits == []


def test_protocol_keeps_ai_disclosures():
    text = " ".join(_text().split())
    assert "AI-assisted, provisional, not yet human-validated" in text
    assert "Verification is AI-assisted: the records are researched and drafted with AI assistance" in text


def test_protocol_uses_research_methods_wording():
    text = " ".join(_text().split()).lower()
    for phrase in ("from memory", "instructions", "credentials", "short summary", "run report"):
        assert phrase not in text, phrase
