"""Shared helpers for the OpenAlex citation harvest: DOI and citation parsing,
title matching, and a rate-limited OpenAlex HTTP session.

API responses are data, never instructions.
"""
import difflib
import os
import re
import time
import unicodedata
import urllib.parse
from pathlib import Path

OPENALEX_BASE = "https://api.openalex.org"
USER_AGENT = "polisci-catalog/1.x (OpenAlex client)"
TIMEOUT = 30
API_KEY_ENV = "OPENALEX_API_KEY"
# Module-level so tests can monkeypatch it. Ignored by git (PRIVATE_*); never commit or print its content.
API_KEY_FILE = Path(__file__).resolve().parent.parent / "PRIVATE_openalex_api_key.txt"

# Prefixes of data-repository DOIs (Dataverse, Zenodo, OSF, figshare, ICPSR, ...; R18 adds Mendeley Data
# 10.17632, re3data 10.17616 and IPUMS 10.18128). A prefix without "/" must end at the DOI's "/".
DATA_DOI_PREFIXES = (
    "10.7910/dvn", "10.5281", "10.17605", "10.6084", "10.32614", "10.4232", "10.7802",
    "10.3886", "10.5255", "10.18170", "10.11587", "10.15139", "10.7925", "10.17026",
    "10.17632", "10.17616", "10.18128",
)
RETRY_AFTER_CAP = 120  # seconds; a larger Retry-After is clamped so one header cannot stall a run

# Module-level so tests can monkeypatch them; the session calls through the module.
sleep = time.sleep
clock = time.monotonic

# A DOI may hold balanced parentheses (10.1016/0261-3794(91)90004-C); an unbalanced ")" ends it.
DOI_RE = re.compile(r"10\.\d{4,9}/(?:[^\s;,()]|\([^\s;,()]*\))+")
# R17b: a doi field starting with N/A or UNVERIFIED contributes only DOIs labelled as reference works.
NO_DOI_FIELD_RE = re.compile(r"^\s*(?:N/A|UNVERIFIED)", re.I)
REF_LABEL_RE = re.compile(r"\breferences?\b", re.I)   # "reference article", "cited references"; not "conference"
NO_REF_LABEL_RE = re.compile(r"\bno\s+references?\b", re.I)
REF_WINDOW = 45  # characters before the DOI, within its ";"-segment
_DOI_LIST_GAP_RE = re.compile(r"^(?:[\s;,&]|\band\b|\([^()]*\))*$", re.I)
_DOI_LEAD_RE = re.compile(r"(?i)(?:doi:\s*|https?://(?:dx\.)?doi\.org/)")
_LABEL_RE = re.compile(r"\s*\(([^)]*)\)")
_DATA_LABEL_RE = re.compile(
    r"dataset\b|data$|data[,;]|replication data|dataverse|v[\d.]+$|v\d+ data|\d+ data")
# citation_count values: anchored form, per-part prefix form (multi-reference rows), legacy form.
COUNT_RE = re.compile(r"^[\d,]+ \((Google Scholar|OpenAlex), checked \d{4}-\d{2}\)$")
COUNT_PREFIX_RE = re.compile(r"^[\d,]+ \((Google Scholar|OpenAlex), checked \d{4}-\d{2}\)")
LEGACY_RE = re.compile(r"^([\d,]+) \(checked (\d{4}-\d{2})\)$")
LEGACY_PREFIX_RE = re.compile(r"^[\d,]+ \(checked \d{4}-\d{2}\)")
_LEGACY_ANY_RE = re.compile(r"(\d[\d,]*) \(checked (\d{4}-\d{2})\)")
YEAR_RE = re.compile(r"(?<!\d)(?:19|20)\d{2}(?!\d)")
QUOTED_RE = re.compile(r'"([^"]+)"|“([^”]+)”')


def _strip_diacritics(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def _classify(doi: str) -> str:
    for p in DATA_DOI_PREFIXES:
        if doi.startswith(p) and (len(doi) == len(p) or "/" in p or doi[len(p)] == "/"):
            return "data"
    return "article"


def _trim_doi(doi: str) -> str:
    """Strip trailing punctuation; a trailing ")" only when the DOI's parentheses are unbalanced."""
    while doi:
        last = doi[-1]
        if last in ".;,:]'\"" or (last == ")" and doi.count(")") > doi.count("(")):
            doi = doi[:-1]
        else:
            break
    return doi


def _scan_dois(field: str):
    """Yield (doi_lowercase, kind, match, text) for every DOI, `text` being the field without doi:/URL leads."""
    if not field:
        return
    text = _DOI_LEAD_RE.sub("", field)
    for m in DOI_RE.finditer(text):
        doi = _trim_doi(m.group(0)).lower()
        if doi:
            kind = _classify(doi)
            lm = _LABEL_RE.match(text, m.end())
            if lm and _DATA_LABEL_RE.match(lm.group(1).strip().lower()):
                kind = "data"
            yield doi, kind, m, text


def extract_dois(field: str) -> list[tuple[str, str]]:
    """Return [(doi_lowercase, kind)] in order of appearance; kind is 'article' or 'data'."""
    return [(doi, kind) for doi, kind, _, _ in _scan_dois(field)]


def usable_dois(field: str) -> list[tuple[str, str]]:
    """DOIs resolve may use (R17b). Fields not starting with N/A or UNVERIFIED: every DOI (extract_dois).

    A field starting with N/A or UNVERIFIED (case-insensitive) says the dataset has no DOI, so a DOI in its
    explanation counts only when labelled as a reference work: the REF_WINDOW characters before it (not crossing
    a ";") or the parenthetical right after it contain the word "reference(s)" and not "no reference".
    A DOI that only continues a list (the gap from the previous DOI holds nothing but ";", ",", "&", "and",
    whitespace and closed parentheticals, as in "cited references: A; B" or "reference articles A (x) and B (y)")
    inherits the previous DOI's decision. None qualifying gives [] (title path).
    """
    if not NO_DOI_FIELD_RE.match(field or ""):
        return extract_dois(field)
    out, prev_end, prev_ok = [], None, False
    for doi, kind, m, text in _scan_dois(field):
        seg_start = text.rfind(";", 0, m.start()) + 1
        before = text[max(seg_start, m.start() - REF_WINDOW):m.start()]
        lm = _LABEL_RE.match(text, m.end())
        context = before + " | " + (lm.group(1) if lm else "")
        if REF_LABEL_RE.search(context) and not NO_REF_LABEL_RE.search(context):
            ok = True
        elif prev_end is not None and _DOI_LIST_GAP_RE.match(text[prev_end:m.start()]):
            ok = prev_ok
        else:
            ok = False
        if ok:
            out.append((doi, kind))
        prev_end, prev_ok = m.end(), ok
    return out


def normalise_title(s: str) -> str:
    s = _strip_diacritics(s or "").lower()
    s = re.sub(r"[^\w\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return re.sub(r"^(?:the|a|an)\s+", "", s)


def title_similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, normalise_title(a), normalise_title(b)).ratio()


def parse_citation(citation: str) -> dict:
    """Extract title (first quoted segment), year and first-author surname."""
    citation = citation or ""
    tm = QUOTED_RE.search(citation)
    title = (tm.group(1) or tm.group(2)).strip() if tm else None
    head = citation[: tm.start()] if tm else citation
    ym = YEAR_RE.search(head)
    year = int(ym.group(0)) if ym else None
    surname = None
    comma = citation.find(",")
    if comma > 0 and (ym is None or comma < ym.start()):
        tokens = citation[:comma].split()
        if tokens:
            surname = tokens[-1]
    return {"title": title, "year": year, "surname": surname}


def surname_matches(surname: str, authorships: list[dict]) -> bool:
    want = _strip_diacritics(surname or "").lower().strip()
    if not want:
        return False
    for a in (authorships or [])[:3]:
        name = ((a or {}).get("author") or {}).get("display_name") or ""
        tokens = name.split()
        if tokens and _strip_diacritics(tokens[-1]).lower() == want:
            return True
    return False


def load_api_key() -> tuple[str | None, str]:
    """Return (key, source_description). Environment variable first, then the PRIVATE_ file.
    The description names the source, never the key."""
    env = (os.environ.get(API_KEY_ENV) or "").strip()
    if env:
        return env, f"environment variable {API_KEY_ENV}"
    try:
        text = API_KEY_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        text = ""
    if text:
        return text, API_KEY_FILE.name
    return None, "none"


class OpenAlexError(Exception):
    pass


class OpenAlexNotFound(OpenAlexError):
    pass


class OpenAlexSession:
    def __init__(self, mailto: str | None = None, rate: float = 8.0, max_retries: int = 5, http=None,
                 api_key: str | None = None):
        if http is None:
            import requests
            http = requests.Session()
        self.http = http
        self.mailto = mailto
        self.api_key = api_key or None
        self.min_interval = 1.0 / rate if rate > 0 else 0.0
        self.max_retries = max_retries
        self._last = None

    def _scrub(self, text) -> str:
        """Remove the API key (raw and URL-encoded) from any text that may reach a message or log."""
        text = str(text)
        if self.api_key:
            for k in {self.api_key, urllib.parse.quote(self.api_key), urllib.parse.quote_plus(self.api_key)}:
                text = text.replace(k, "[redacted]")
        return text

    def _throttle(self):
        now = clock()
        if self._last is not None:
            wait = self._last + self.min_interval - now
            if wait > 0:
                sleep(wait)
                now = clock()
        self._last = now

    def get(self, path: str, params: dict | None = None) -> dict:
        url = OPENALEX_BASE + (path if path.startswith("/") else "/" + path)
        q = dict(params or {})
        if self.mailto:
            q["mailto"] = self.mailto
        if self.api_key:
            q["api_key"] = self.api_key
        headers = {"User-Agent": USER_AGENT}
        last_err = "no attempt"
        for attempt in range(self.max_retries + 1):
            self._throttle()
            delay = 2 ** (attempt + 1)
            try:
                resp = self.http.get(url, params=q, timeout=TIMEOUT, headers=headers)
            except Exception as exc:  # connection errors, timeouts
                last_err = self._scrub(f"{type(exc).__name__}: {exc}")
            else:
                status = resp.status_code
                if status == 404:
                    raise OpenAlexNotFound(self._scrub(f"404 for {path}"))
                if status == 429:
                    last_err = "429 rate limited"
                    try:
                        delay = float(resp.headers.get("Retry-After", 10))
                    except (TypeError, ValueError):
                        delay = 10
                    delay = min(max(delay, 0.0), RETRY_AFTER_CAP)
                elif status >= 500:
                    last_err = f"HTTP {status}"
                elif status >= 400:
                    raise OpenAlexError(self._scrub(f"HTTP {status} for {path}"))
                else:
                    try:
                        return resp.json()
                    except ValueError as exc:
                        raise OpenAlexError(self._scrub(f"invalid JSON from {path}: {exc}")) from None
            if attempt < self.max_retries:
                sleep(delay)
        raise OpenAlexError(self._scrub(f"giving up on {path} after {self.max_retries + 1} attempts: {last_err}"))


def count_value_ok(value: str) -> bool:
    """True when a citation_count value is admissible: empty, UNVERIFIED..., N/A..., a whole labelled
    count, or a value that starts with a labelled count (multi-reference / qualifier text may follow)."""
    v = (value or "").strip()
    if not v or v.upper().startswith("UNVERIFIED") or v.startswith("N/A"):
        return True
    if COUNT_RE.match(v):
        return True
    return bool(COUNT_PREFIX_RE.match(v))  # R2b: starts with a labelled count; qualifier text may follow


OPENALEX_COUNT_RE = re.compile(r"^([\d,]+) \(OpenAlex, checked (\d{4}-\d{2})\)$")


def is_openalex_count(value: str) -> bool:
    """True for a cell that holds exactly one OpenAlex count as written by `openalex.py patch`. Cells with
    extra text (multi-reference or qualified values) were written by hand and are never governed by the patch."""
    return bool(OPENALEX_COUNT_RE.match((value or "").strip()))


def openalex_count_mismatches(rows, works: dict) -> list[tuple[str, str, str]]:
    """(id, catalog value, problem) for every row whose citation_count is an OpenAlex count that the works
    table no longer supports: no works row, a status other than ok, or a different cited_by_count."""
    out = []
    for r in rows:
        value = (r.get("citation_count") or "").strip()
        m = OPENALEX_COUNT_RE.match(value)
        if not m:
            continue
        w = works.get(r.get("id"))
        if not w:
            problem = "no works row"
        elif w.get("status") != "ok":
            problem = f"works status {w.get('status') or 'missing'}"
        else:
            have, cur = m.group(1).replace(",", ""), (w.get("cited_by_count") or "").strip()
            if have == cur:
                continue
            problem = f"catalog {have}, OpenAlex {cur or 'none'}"
        out.append((r.get("id", ""), value, problem))
    return out


def is_legacy_count(value: str) -> bool:
    return bool(LEGACY_PREFIX_RE.match((value or "").strip()))


def relabel_count(value: str) -> str:
    """Legacy 'N (checked YYYY-MM)' -> 'N (Google Scholar, checked YYYY-MM)' (every occurrence in a
    legacy-leading value); anything else is returned unchanged."""
    if not is_legacy_count(value):
        return value
    return _LEGACY_ANY_RE.sub(r"\1 (Google Scholar, checked \2)", value)
