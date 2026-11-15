"""The audit scans built-site and export trees for blocklisted hostnames."""
import re

import audit_catalog
import unsafe

BAD = "bad" + ".example"


def test_audit_flags_unsafe_domain_in_site(tmp_path, monkeypatch):
    monkeypatch.setattr(unsafe, "DOMAINS", [BAD])
    monkeypatch.setattr(unsafe, "HASHES", frozenset())
    monkeypatch.setattr(unsafe, "_PATTERNS", [(BAD, re.compile(r"(?<![a-z0-9.\-])(?:[a-z0-9\-]+\.)*"
                                                              + re.escape(BAD) + r"(?![a-z0-9\-])", re.I))])
    site = tmp_path / "site"
    (site / "d").mkdir(parents=True)
    (site / "d" / "page.html").write_text("<p>ok</p>\n<a href=\"https://www." + BAD + "/x\">x</a>\n", encoding="utf-8")
    (site / "clean.json").write_text('{"a": "fine"}', encoding="utf-8")
    (site / "img.png").write_bytes(b"https://" + BAD.encode())
    hits = audit_catalog.unsafe_domain_hits([site, tmp_path / "absent"])
    assert len(hits) == 1
    assert hits[0].startswith("site/d/page.html:2:")
