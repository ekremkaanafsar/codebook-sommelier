"""README.md and CITATION.cff, the first files a visitor to the public repository reads."""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CFG = json.loads((ROOT / "data" / "site_config.json").read_text(encoding="utf-8"))

# top-level keys of the Citation File Format 1.2.0 schema (additionalProperties: false)
CFF_KEYS = {"abstract", "authors", "cff-version", "commit", "contact", "date-released", "doi", "identifiers",
            "keywords", "license", "license-url", "message", "preferred-citation", "references", "repository",
            "repository-artifact", "repository-code", "title", "type", "url", "version"}


def test_readme_links_the_live_site_and_the_issue_templates():
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert CFG["base_url"] in text
    assert CFG["repo_url"].rstrip("/") + "/issues/new/choose" in text


def test_citation_cff_uses_only_schema_keys_and_points_to_site_and_code():
    text = (ROOT / "CITATION.cff").read_text(encoding="utf-8")
    keys = set(re.findall(r"^([a-z][a-z-]*):", text, re.M))
    assert keys <= CFF_KEYS, keys - CFF_KEYS
    assert re.search(r'^url: "' + re.escape(CFG["base_url"]) + '"$', text, re.M)
    assert re.search(r'^repository-code: "' + re.escape(CFG["repo_url"]) + '"$', text, re.M)
