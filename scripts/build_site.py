"""Static catalog site: data/catalog.csv and its sidecars -> site/ (spec 2026-11-12 sections 5, 6, 12, 14).

Usage: python scripts/build_site.py [--out site] [--root .]

Tasks 4 and 5 render the home page, the 21 subfield pages, one page per live dataset, the comparison index and
one page per comparison group, the network, methods and annex (two) pages and 404.html; the explorer
is added to BUILT_KINDS when it is implemented, and
templates link to a page kind only when it is built, so every internal link resolves at every stage.

Required inputs (FileNotFoundError naming the path): data/catalog.csv, data/comparison_groups.csv, data/author.json
and data/narratives/<CODE>.json for every domain marked complete in data/progress.json. Optional inputs
(data/derived/facets.csv, data/citations/*.csv, annex CSVs, progress.json, site_config.json, _citations.json) are
skipped with a notice. Every free-text value is rendered through site_common.display_html, so UNVERIFIED becomes
the "not confirmed" marker; URLs are linked only when site_common.is_url() holds and the host is not on the
unsafe-domain list (data/unsafe_domains.txt, or the .sha256 hashes when the names are absent).
"""
import argparse
import csv
import datetime
import json
import re
import shutil
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import NamedTuple
from urllib.parse import quote, urlparse
from xml.sax.saxutils import escape as xml_escape

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from markupsafe import Markup, escape

import site_common as sc
import unsafe
from citations_common import extract_dois
from schema import CODE_NAME, CODES, DOMAINS, Q_DIMS, RESOURCE_TYPES

PROJECT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = PROJECT / "templates" / "site"
ASSET_DIR = PROJECT / "site_assets"
BUILD_MARKER = ".site-build"

# Same values as cousage.DOMAIN_COLOURS (checked by a test). Not imported: cousage pulls matplotlib, networkx and
# scipy, which the site build (requirements-site.txt) does not install.
DOMAIN_COLOURS = dict(zip(CODES, [
    "#1f4e9c", "#e8832a", "#2a9d8f", "#c0392b", "#8e6bbf", "#8c5a2b", "#e377c2", "#6b6b6b", "#b5a300",
    "#17a2c4", "#7fb23a", "#9c1f5b", "#f2b134", "#3b3b98", "#d95f5f", "#4f9a5a", "#0b7285", "#a0522d",
    "#5c7cfa", "#a3a3a3", "#2b2d42",
]))

KINDS = ("home", "domain", "dataset", "compare", "explore", "network", "methods", "annex")
BUILT_KINDS = frozenset({"home", "domain", "dataset", "compare", "explore", "network", "methods", "annex"})
EXPLORER_BUDGET_BYTES = int(1.5 * 1024 * 1024)
SUMMARY_LIMIT = 200
PARTNER_LIMIT = 12  # merge partners listed on a dataset page; the rest are reached through the explorer link
REGION_LABELS = {
    "global": "Global", "multi-country": "Multi-country", "europe": "Europe", "north-america": "North America",
    "latin-america": "Latin America", "sub-saharan-africa": "Sub-Saharan Africa", "mena": "MENA",
    "central-asia": "Central Asia", "east-asia": "East Asia", "south-asia": "South Asia",
    "southeast-asia": "Southeast Asia", "oceania": "Oceania", "unclassified": "Unclassified",
}
UNIT_LABELS = {
    "country": "Country", "country-year": "Country-year", "document": "Document", "dyad": "Dyad",
    "election-or-candidate": "Election or candidate", "event": "Event", "geospatial": "Geospatial",
    "grid-cell": "Grid cell", "legislator-or-vote": "Legislator or vote", "organisation": "Organisation",
    "other": "Other", "party": "Party", "person-or-actor": "Person or actor", "respondent": "Respondent",
    "study-or-record": "Study or record", "subnational": "Subnational",
}
EXPLORER_ASSETS = ("minisearch.min.js", "search.js", "explorer.js")
CLIP = 220
COUSAGE_DOWNLOADS = (("cousage.graphml", "GraphML network file"), ("cousage_nodes.csv", "Node table (CSV)"),
                     ("cousage_edges.csv", "Edge table (CSV)"))
COUSAGE_FIGURES = (("cousage_network.svg", "Co-usage map of datasets"),
                   ("cousage_domains.svg", "Co-usage between subfields"))
RUBRIC = (
    ("Validity", "Concept undefined or indicator weakly tied to it", "Defined concept, some known mismatch",
     "Explicit concept, validated against alternatives"),
    ("Reliability", "No reliability information", "Some coder or sampling detail",
     "Intercoder reliability or measurement uncertainty reported; replicable"),
    ("Transparency", "No codebook", "Codebook, partial sources",
     "Full codebook, sources, coding rules, revision history"),
    ("Coverage", "Narrow, many gaps", "Moderate breadth, documented gaps",
     "Broad space and time, missingness documented"),
    ("Comparability", "Undocumented series breaks", "Mostly consistent",
     "Consistent across units and time; breaks documented"),
    ("Maintenance", "Abandoned, no archive", "Irregular updates", "Regular versioned releases"),
    ("Accessibility", "Hard to obtain", "Registration or awkward formats",
     "Open, standard formats, API or package"),
    ("Adoption", "Rarely used", "Used in field journals", "Widely used in top journals"),
)
DATA_LICENCE = ("CC BY 4.0", "https://creativecommons.org/licenses/by/4.0/")
CODE_LICENCE = "MIT"
PROVISIONAL = "AI-assisted, provisional, not yet human-validated"
DIM_LABELS = dict(zip(Q_DIMS, ["Validity", "Reliability", "Transparency", "Coverage", "Comparability",
                               "Maintenance", "Accessibility", "Adoption"]))
LINK_MARKS = {
    "ok": ("✓", "Link checked: working", "ok"),
    "redirect": ("↪", "Link checked: redirects to another address", "redirect"),
    "blocked-by-site": ("⊘", "Link checked: the site blocks automated checks", "blocked by site"),
    "broken": ("✗", "Link checked: broken", "broken"),
}
WITHHELD = "[address withheld: former project domain]"
SPARK_YEARS = 10


# ---------------------------------------------------------------- inputs

@dataclass
class Inputs:
    root: Path
    rows: list
    by_id: dict
    groups: list
    narratives: dict
    facets: dict | None
    works: dict | None
    nodes: dict | None
    annex4: list | None
    annex5: list | None
    author: dict
    cfg: dict
    domains: list
    notices: list = field(default_factory=list)


@dataclass
class BuildReport:
    pages: dict
    notices: list
    sizes: dict = field(default_factory=dict)


class Link(NamedTuple):
    label: str
    url: str | None  # set only when the value is a URL on a safe host
    status: tuple | None  # (symbol, title, word) for the landing page's link_status
    text: str  # the raw value, rendered when it is not linked


def _read_csv(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _require(path):
    if not path.is_file():
        raise FileNotFoundError(f"required input missing: {path}")
    return path


class UnsafeDomains:
    """The unsafe-domain blocklist of one data directory (names, else hashes), without touching unsafe's globals."""

    def __init__(self, data_dir):
        txt = data_dir / "unsafe_domains.txt"
        self.names = unsafe.load(txt)
        self.hashes = frozenset() if txt.exists() else unsafe.load_hashes(data_dir / "unsafe_domains.sha256")
        self.patterns = [re.compile(r"(?<![a-z0-9.\-])(?:[a-z0-9\-]+\.)*" + re.escape(d) + r"(?![a-z0-9\-])", re.I)
                         for d in self.names]

    def _host_bad(self, host):
        host = host.lower().rstrip(".")
        if self.hashes:
            labels = host.split(".")
            return any(unsafe.sha(".".join(labels[i:])) in self.hashes for i in range(len(labels) - 1))
        return any(host == d or host.endswith("." + d) for d in self.names)

    def url_bad(self, url):
        try:
            return self._host_bad(urlparse(url if "://" in url else "http://" + url).hostname or "")
        except ValueError:
            return True

    def redact(self, text):
        """Text with every unsafe domain (and the URL it sits in) replaced by WITHHELD."""
        if not text or not (self.names or self.hashes):
            return text
        if self.hashes:
            def rep(m):
                return WITHHELD if self._host_bad(m.group(1)) else m.group(0)
            return re.sub(r"(?:https?://)?(" + unsafe.HOST_RE.pattern + r")(?:[/:?#][^\s;,)\]]*)?", rep, text)
        for p in self.patterns:
            text = re.sub(r"(?:https?://)?" + p.pattern + r"(?:[/:?#][^\s;,)\]]*)?", WITHHELD, text, flags=re.I)
        return text


def _redact_obj(obj, guard):
    if isinstance(obj, str):
        return guard.redact(obj)
    if isinstance(obj, list):
        return [_redact_obj(x, guard) for x in obj]
    if isinstance(obj, dict):
        return {k: _redact_obj(v, guard) for k, v in obj.items()}
    return obj


def load_inputs(root: Path) -> Inputs:
    root = Path(root)
    data = root / "data"
    notices = []
    guard = UnsafeDomains(data)

    rows_all = _read_csv(_require(data / "catalog.csv"))
    groups_raw = _read_csv(_require(data / "comparison_groups.csv"))
    author = _read_json(_require(data / "author.json"))

    redacted = 0
    rows = []
    for r in rows_all:
        if r.get("status", "").strip() == "removed":
            continue
        if guard.names or guard.hashes:
            joined = "\n".join(str(v) for v in r.values())
            if guard.redact(joined) != joined:
                r = {k: guard.redact(v) for k, v in r.items()}
                redacted += 1
        rows.append(r)
    if redacted:
        notices.append(f"unsafe domains withheld in {redacted} rows")
    by_id = {r["id"]: r for r in rows}

    cfg = {"base_url": "/", "repo_url": ""}
    cfg_path = data / "site_config.json"
    if cfg_path.is_file():
        cfg.update(_read_json(cfg_path))
    else:
        notices.append(f"site_config.json missing: base_url '/' and no repository link ({cfg_path})")
    if not re.match(r"https?://", str(cfg.get("base_url") or "")):
        notices.append(f"base_url {cfg.get('base_url')!r} is not absolute: canonical, Open Graph and JSON-LD URLs "
                       "are relative; set an absolute base_url in data/site_config.json before launch")

    progress = None
    if (data / "progress.json").is_file():
        progress = _read_json(data / "progress.json").get("domains", {})
    else:
        notices.append("progress.json missing: domains with rows are treated as complete")
    counts = Counter(r.get("primary_domain", "") for r in rows)

    narratives = {}
    for code in CODES:
        p = data / "narratives" / f"{code}.json"
        complete = (progress.get(code, {}).get("status") == "complete") if progress is not None else counts[code] > 0
        if p.is_file():
            narratives[code] = _redact_obj(_read_json(p), guard)
        elif complete:
            _require(p)
    cit_p = data / "narratives" / "_citations.json"
    if cit_p.is_file():
        narratives["_citations"] = _redact_obj(_read_json(cit_p), guard)

    facets = None
    if (data / "derived" / "facets.csv").is_file():
        facets = {r["id"]: r for r in _read_csv(data / "derived" / "facets.csv")}
    else:
        notices.append("facets missing (data/derived/facets.csv): temporalCoverage and facet lines omitted")
    works = nodes = None
    cdir = data / "citations"
    if (cdir / "openalex_works.csv").is_file():
        works = {r["id"]: r for r in _read_csv(cdir / "openalex_works.csv")}
    if (cdir / "cousage_nodes.csv").is_file():
        nodes = {r["id"]: r for r in _read_csv(cdir / "cousage_nodes.csv")}
    if works is None or nodes is None:
        notices.append("citations sidecars missing (data/citations/): citation trends and 'used alongside' omitted")
    annex4 = _read_csv(data / "literature_databases.csv") if (data / "literature_databases.csv").is_file() else None
    annex5 = _read_csv(data / "primary_archives.csv") if (data / "primary_archives.csv").is_file() else None
    if annex4 is None or annex5 is None:
        notices.append("annex CSVs missing: annex pages omitted")

    slugs = sc.unique_slugs(g["group"] for g in groups_raw)
    groups = []
    for g, slug in zip(groups_raw, slugs):
        g = _redact_obj(dict(g), guard)
        g["slug"] = slug
        g["id_list"] = [i.strip() for i in g.get("ids", "").split(";") if i.strip()]
        groups.append(g)

    domains = []
    for num, code, name in DOMAINS:
        narr = narratives.get(code)
        core = [c["id"] for c in (narr or {}).get("core", []) if c.get("id") in by_id]
        missing = [c.get("id") for c in (narr or {}).get("core", []) if c.get("id") not in by_id]
        if missing:
            notices.append(f"{code}: core ids not in the catalog skipped: {', '.join(map(str, missing))}")
        status = (progress or {}).get(code, {}).get("status", "")
        domains.append({"num": num, "code": code, "name": name, "colour": DOMAIN_COLOURS[code],
                        "count": counts[code], "complete": status == "complete" or (progress is None and counts[code] > 0),
                        "core": core})

    return Inputs(root=root, rows=rows, by_id=by_id, groups=groups, narratives=narratives, facets=facets,
                  works=works, nodes=nodes, annex4=annex4, annex5=annex5, author=author, cfg=cfg,
                  domains=domains, notices=notices)


# ---------------------------------------------------------------- urls

def _base(cfg):
    b = str(cfg.get("base_url") or "/")
    return b if b.endswith("/") else b + "/"


def url_for(kind: str, key: str, cfg) -> str:
    b = _base(cfg)
    if kind == "home":
        return b
    if kind in ("domain", "dataset", "compare", "annex"):
        sub = {"domain": "domains", "dataset": "datasets", "compare": "compare", "annex": "annex"}[kind]
        return f"{b}{sub}/{key}/" if key else f"{b}{sub}/"
    if kind in ("explore", "network", "methods"):
        return f"{b}{kind}/"
    raise ValueError(f"unknown page kind: {kind}")


def asset_url(name, cfg):
    return f"{_base(cfg)}assets/{name}"


# ---------------------------------------------------------------- text helpers

_URL_RE = re.compile(r"https?://[^\s<>\"']+")


def _unv_html(s):
    return Markup(re.sub("UNVERIFIED", lambda m: sc.UNV_SPAN, str(escape(s)), flags=re.I))


def _trim_url(url):
    while url:
        if url[-1] in ".,;:!?'\"":
            url = url[:-1]
        elif url[-1] in ")]" and url.count("(" if url[-1] == ")" else "[") < url.count(url[-1]):
            url = url[:-1]
        else:
            break
    return url


def make_linkify(guard):
    def linkify(v):
        """display_html with embedded http(s) URLs as links (safe hosts only)."""
        v = "" if v is None else str(v)
        if sc.is_na(v):
            v = v.strip()
            return Markup("n/a") + linkify(v[3:]) if len(v) > 3 else Markup("n/a")
        out, pos = [], 0
        for m in _URL_RE.finditer(v):
            url = _trim_url(m.group(0))
            if not url:
                continue
            out.append(_unv_html(v[pos:m.start()]))
            if sc.is_url(url) and not guard.url_bad(url):
                out.append(Markup('<a href="{0}">{0}</a>').format(url))
            else:
                out.append(_unv_html(url))
            pos = m.start() + len(url)
        out.append(_unv_html(v[pos:]))
        return Markup("").join(out)
    return linkify


def make_doi_links(linkify):
    def doi_links(v):
        """A doi field with every DOI token linked to https://doi.org/<doi>; labels and other text as linkify."""
        v = "" if v is None else str(v)
        if sc.is_na(v):
            v = v.strip()
            return Markup("n/a") + doi_links(v[3:]) if len(v) > 3 else Markup("n/a")
        tokens = sorted({d for d, _ in extract_dois(v)}, key=len, reverse=True)
        if not tokens:
            return linkify(v)
        pat = re.compile(r"(?:https?://(?:dx\.)?doi\.org/|doi:\s*)?(" + "|".join(map(re.escape, tokens)) + ")",
                         re.I)
        out, pos = [], 0
        for m in pat.finditer(v):
            out.append(linkify(v[pos:m.start()]))
            out.append(Markup('<a href="https://doi.org/{0}">{1}</a>').format(m.group(1).lower(), m.group(0)))
            pos = m.end()
        out.append(linkify(v[pos:]))
        return Markup("").join(out)
    return doi_links


def acronym(v):
    """Displayable acronym: "" for empty, N/A or UNVERIFIED values."""
    v = sc.scrub(v)
    return "" if not v or sc.is_na(v) else v


def split_list(v):
    """Top-level ;-separated items (semicolons inside parentheses stay)."""
    parts, depth, cur = [], 0, []
    for ch in str(v or ""):
        if ch == "(":
            depth += 1
        elif ch == ")" and depth:
            depth -= 1
        if ch == ";" and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur).strip())
    return [p for p in parts if p]


def has(v):
    return bool(str(v if v is not None else "").strip())


def one_line(v, limit=180):
    """First sentence or ;-segment of a scrubbed value, cut at a word boundary."""
    v = sc.scrub(v)
    if not v or sc.is_na(v):
        return ""
    v = re.split(r"(?<=[.!?])\s+|;\s*", v)[0].strip()
    if len(v) > limit:
        v = v[:limit].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"
    return v


def status_base(v):
    v = str(v or "").strip()
    if sc.is_unv(v) or not v:
        return ""
    if v.lower().startswith("superseded"):
        return "superseded"
    return v.split()[0].lower()


def _int(v):
    try:
        return int(str(v).strip())
    except ValueError:
        return 0


def guide_institutions(found_via):
    out = []
    for m in re.finditer(r"Guide:\s*([^;(]+)", str(found_via or "")):
        name = m.group(1).strip().rstrip(",")
        if name and name not in out:
            out.append(name)
    return out


class PartnerIndex:
    """site_common.merge_partners for every row in one pass (same result, without the quadratic rescan)."""

    def __init__(self, rows):
        live = [r for r in rows if r.get("status") != "removed"]
        self.systems = {r["id"]: sc.id_systems(r) for r in live}
        self.counts = Counter(label for labels in self.systems.values() for label in labels)
        order = sorted(live, key=lambda r: (-_int(r.get("guide_count") or 0), r["id"]))
        self.members = {label: [r["id"] for r in order if label in self.systems[r["id"]]]
                        for label, _ in sc.ID_SYSTEMS}

    def partners(self, row, limit=None):
        """(system, partner ids): the row's least common identifier system and the other rows keyed on it, at most
        limit of them (PARTNER_LIMIT when limit is None; 0 or less means all)."""
        limit = PARTNER_LIMIT if limit is None else limit
        mine = self.systems.get(row["id"])
        if mine is None:
            mine = sc.id_systems(row)
        if not mine:
            return "", []
        best = min(mine, key=lambda label: self.counts[label])
        others = [i for i in self.members[best] if i != row["id"]]
        return best, (others[:limit] if limit > 0 else others)


def _year(v):
    m = re.match(r"\s*(\d{4})(?!\d)", str(v or ""))
    return int(m.group(1)) if m else None


def _cites(v):
    """The integer N of a citation_count of the form "N (source, checked YYYY-MM)"; None otherwise."""
    m = re.match(r"\s*(\d+)\s*\(", str(v or ""))
    return int(m.group(1)) if m else None


def _cut(v, limit):
    v = " ".join(str(v or "").split())
    if len(v) <= limit:
        return v
    return v[:limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:.") + "…"


def _text(v):
    """A scrubbed free-text value for the JSON layer: "" for empty, N/A and UNVERIFIED-only values."""
    v = sc.scrub(v)
    return "" if not v or sc.is_na(v) else v


def explorer_record(row, facet, work, node, cfg=None):
    """One explorer.json object (spec 7). work and node are accepted for interface stability; counts come from
    the catalog's own citation_count text, so they match the dataset page."""
    facet = facet or {}
    q = [_int(row.get(k, "")) for k in Q_DIMS]
    sd = [c for c in (s.strip() for s in str(row.get("secondary_domains", "")).split(";")) if c in CODE_NAME]
    rtype = str(row.get("resource_type", "")).strip()
    conf = str(row.get("confidence", "")).strip()
    return {
        "id": row["id"], "name": _text(row.get("name")) or row["id"], "acronym": acronym(row.get("acronym")),
        "pd": str(row.get("primary_domain", "")).strip(), "sd": sd,
        "type": int(rtype) if rtype.isdigit() else None,
        "unit_class": str(facet.get("unit_class", "")).strip(),
        "regions": [x.strip() for x in str(facet.get("region_tags", "")).split(";") if x.strip()],
        "start_year": _year(facet.get("start_year")), "end_year": _year(facet.get("end_year")),
        "access": _text(row.get("access_level")).lower(), "status": status_base(row.get("status")),
        "api": 1 if str(facet.get("has_programmatic", "")).strip() == "1" else 0,
        "cites": _cites(row.get("citation_count")), "guides": _int(row.get("guide_count") or 0),
        "conf": conf if conf in ("High", "Medium", "Low") else "",
        "q": q if all(1 <= n <= 5 for n in q) else None,
        "url": url_for("dataset", row["id"], cfg or {}),
        "lu": _year(row.get("last_update")), "summary": _cut(sc.description(row), SUMMARY_LIMIT),
    }


def search_record(row):
    return {"id": row["id"], "name": _text(row.get("name")) or row["id"], "acronym": acronym(row.get("acronym")),
            "key_variables": _text(row.get("key_variables")), "strengths": _text(row.get("strengths")),
            "best_used_for": _text(row.get("best_used_for")), "maintainer": _text(row.get("maintainer"))}


def write_explorer_json(inputs, out):
    """api/explorer.json and api/search.json; returns the byte sizes. A size over budget adds a notice."""
    (out / "api").mkdir(parents=True, exist_ok=True)
    recs = [explorer_record(r, (inputs.facets or {}).get(r["id"]), (inputs.works or {}).get(r["id"]),
                            (inputs.nodes or {}).get(r["id"]), inputs.cfg) for r in inputs.rows]
    sizes = {}
    for name, obj in (("explorer", recs), ("search", [search_record(r) for r in inputs.rows])):
        data = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        (out / "api" / f"{name}.json").write_bytes(data)
        sizes[name] = len(data)
    if sizes["explorer"] > EXPLORER_BUDGET_BYTES:
        inputs.notices.append(f"api/explorer.json is {sizes['explorer']} bytes, over the "
                              f"{EXPLORER_BUDGET_BYTES} byte budget")
    return sizes


def explore_context(inputs):
    cfg = inputs.cfg
    conf = {"explorer_url": f"{_base(cfg)}api/explorer.json", "search_url": f"{_base(cfg)}api/search.json",
            "domains": [[d["code"], d["name"]] for d in inputs.domains],
            "types": {k: v for k, v in RESOURCE_TYPES.items() if k in ("1", "2", "3")},
            "dims": [DIM_LABELS[k] for k in Q_DIMS],
            "labels": {"region": REGION_LABELS, "unit": UNIT_LABELS}}
    return {"conf_json": _json_script(conf), "domains": inputs.domains}


def _json_script(obj):
    s = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=False)
    return Markup(s.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))


# ---------------------------------------------------------------- page contexts

def _named(inputs, ids):
    return [(i, inputs.by_id[i]["name"]) for i in ids if i in inputs.by_id]


def _top5(inputs, rid):
    if not inputs.nodes or rid not in inputs.nodes:
        return []
    return _named(inputs, [i.strip() for i in inputs.nodes[rid].get("top5", "").split(";") if i.strip()])


def dataset_context(inputs, row, pidx, guard):
    rid = row["id"]
    cfg = inputs.cfg
    facet = (inputs.facets or {}).get(rid, {})
    page_url = url_for("dataset", rid, cfg)
    marks = LINK_MARKS.get(str(row.get("link_status", "")).strip())
    links = []
    for label, col in (("Landing page", "landing_url"), ("Download", "download_url"), ("Codebook", "codebook_url")):
        v = str(row.get(col) or "").strip()
        if not v:
            continue
        ok = sc.is_url(v) and not guard.url_bad(v)
        links.append(Link(label, v if ok else None, marks if col == "landing_url" else None, v))
    system, partner_ids = pidx.partners(row)
    all_partners = pidx.partners(row, limit=0)[1]
    groups = [(g["slug"], g["group"]) for g in inputs.groups if rid in g["id_list"]]
    related = []
    for tok in split_list(row.get("related_datasets")):
        if sc.is_unv(tok) or sc.is_na(tok):
            continue
        related.append((tok, inputs.by_id[tok]["name"]) if tok in inputs.by_id else (None, tok))
    successor = None
    m = re.match(r"superseded by\s+([A-Z]+-\d+)", str(row.get("status", "")).strip(), re.I)
    if m and m.group(1) in inputs.by_id:
        successor = (m.group(1), inputs.by_id[m.group(1)]["name"])
    dims = []
    for k in Q_DIMS:
        n = _int(row.get(k, ""))
        dims.append((DIM_LABELS[k], n if 1 <= n <= 5 else None))
    work = (inputs.works or {}).get(rid)
    spark = None
    if work and str(work.get("status", "")).strip() == "ok" and work.get("openalex_id"):
        try:
            cby = json.loads(work.get("counts_by_year") or "[]")
        except ValueError:
            cby = []
        end = _int(str(work.get("checked_on", ""))[:4]) or datetime.date.today().year
        spark = Markup(sc.sparkline_svg(cby, list(range(end - SPARK_YEARS + 1, end + 1))))
    else:
        work = None
    node = (inputs.nodes or {}).get(rid)
    shares = _named(inputs, [i.strip() for i in (node or {}).get("shares_reference_with", "").split(";") if i.strip()])
    report_url = None
    if cfg.get("repo_url"):
        report_url = (f"{str(cfg['repo_url']).rstrip('/')}/issues/new?template=correction.yml"
                      f"&title={quote(rid + ': ')}")
    secondary = [c for c in (s.strip() for s in str(row.get("secondary_domains", "")).split(";")) if c in CODE_NAME]
    return {
        "row": row, "facet": facet, "desc": sc.description(row), "links": links,
        "cite": sc.cite_entry(row, inputs.author, cfg),
        "jsonld": _json_script(sc.jsonld(row, facet, inputs.author, cfg, page_url)),
        "dims": dims, "top5": _top5(inputs, rid),
        "partners": (system, _named(inputs, partner_ids)),
        # every partner id, for the explorer's #ids= link; only when the page lists fewer than all of them
        "partners_all": all_partners if len(all_partners) > len(partner_ids) else [],
        "systems": pidx.systems.get(rid) or sc.id_systems(row),
        "groups": groups, "related": related, "guides": guide_institutions(row.get("found_via")),
        "successor": successor, "report_url": report_url,
        "work": work, "spark": spark, "shares": shares, "secondary": secondary,
        "status_word": status_base(row.get("status")),
        "type_label": RESOURCE_TYPES.get(str(row.get("resource_type", "")).strip(), ""),
        "page_url": page_url,
    }


def domain_context(inputs, d):
    code = d["code"]
    narr = inputs.narratives.get(code)
    texts = {c.get("id"): c.get("text", "") for c in (narr or {}).get("core", [])}
    core = []
    for rid in d["core"]:
        r = inputs.by_id[rid]
        core.append({"row": r, "text": texts.get(rid, ""), "oneline": one_line(r.get("best_used_for")),
                     "alongside": _top5(inputs, rid)})
    rows = sorted((r for r in inputs.rows if r.get("primary_domain") == code),
                  key=lambda r: (str(r.get("name", "")).casefold(), r["id"]))
    groups = [g for g in inputs.groups if g.get("domain") == code]
    n_secondary = sum(1 for r in inputs.rows if code in [s.strip() for s in str(r.get("secondary_domains", "")).split(";")])
    return {"d": d, "narr": narr, "core": core, "rows": rows, "groups": groups, "n_secondary": n_secondary}


def catalog_cite(a):
    cite = f"{a.get('citation_name', '')}. {a.get('year', '')}. {a.get('title', '')}."
    if a.get("version"):
        cite += f" Version {a['version']}."
    if a.get("doi"):
        cite += f" Zenodo. https://doi.org/{a['doi']}."
    return cite


def clip(v, limit=CLIP):
    """A table-cell value cut at a word boundary (UNVERIFIED values pass through for the marker)."""
    v = "" if v is None else str(v).strip()
    if len(v) <= limit or sc.is_unv(v):
        return v
    return v[:limit].rsplit(" ", 1)[0].rstrip(" ,;:") + "…"


def home_context(inputs):
    cards = []
    for d in inputs.domains:
        acr = []
        for rid in d["core"][:3]:
            r = inputs.by_id[rid]
            acr.append(acronym(r.get("acronym")).split(";")[0].strip() or one_line(r.get("name"), 40))
        cards.append({"d": d, "acronyms": acr})
    return {"cards": cards, "n_rows": len(inputs.rows), "catalog_cite": catalog_cite(inputs.author)}


CMP_COLS = (("Unit", "unit_of_analysis"), ("Temporal coverage", "temporal_coverage"),
            ("Spatial coverage", "spatial_coverage"), ("Measurement approach", "measurement_approach"),
            ("Generation method", "generation_method"), ("Access", "access_level"), ("Status", "status"))


def compare_context(inputs, g):
    members = [inputs.by_id[i] for i in g["id_list"] if i in inputs.by_id]
    skipped = [i for i in g["id_list"] if i not in inputs.by_id]
    if skipped:
        inputs.notices.append(f"comparison '{g['group']}' ({g.get('domain', '')}): ids not in the catalog "
                              f"skipped: {', '.join(skipped)}")
    return {"g": g, "members": members, "cols": CMP_COLS, "dom": CODE_NAME.get(g.get("domain", ""), g.get("domain", ""))}


def compare_index_context(inputs):
    by_dom = []
    for d in inputs.domains:
        gs = [g for g in inputs.groups if g.get("domain") == d["code"]]
        if gs:
            by_dom.append((d, gs))
    return {"by_dom": by_dom, "n_groups": len(inputs.groups)}


def network_context(inputs, site_files):
    nodes = list((inputs.nodes or {}).values())

    def beta(n):
        try:
            return float(n.get("betweenness") or 0)
        except ValueError:
            return 0.0
    top = sorted(nodes, key=lambda n: (-beta(n), n["id"]))[:10]
    bridges = [{"n": n, "beta": f"{beta(n):.4f}",
                "shares": _named(inputs, [i.strip() for i in n.get("shares_reference_with", "").split(";") if i.strip()])}
               for n in top]
    c = inputs.narratives.get("_citations") or {}
    return {"bridges": bridges, "figures": site_files["figures"], "downloads": site_files["downloads"],
            "method_p": (c.get("method") or [""])[0], "finding_p": (c.get("findings") or [""])[0]}


def methods_context(inputs):
    c = inputs.narratives.get("_citations") or {}
    cav = c.get("caveats") or []
    return {"rubric": RUBRIC, "oa_method": (c.get("method") or [""])[0], "oa_caveat": cav[0] if cav else "",
            "catalog_cite": catalog_cite(inputs.author), "n_rows": len(inputs.rows)}


def annex_context(inputs, rows, kind):
    rows = [dict(r) for r in rows if str(r.get("status", "")).strip() != "removed"]
    rows.sort(key=lambda r: (str(r.get("name", "")).casefold(), r.get("name", "")))
    for r in rows:
        r["domains"] = [c for c in (x.strip() for x in str(r.get("relevant_domains", "")).split(";")) if c in CODE_NAME]
    if kind == "literature":
        return {"rows": rows, "title": "Literature and reference databases", "slug": "literature",
                "intro": "Databases that index or hold scholarly literature and reference works. They are not datasets, "
                         "so they sit outside the main catalog."}
    return {"rows": rows, "title": "Primary-source and document archives", "slug": "archives",
            "intro": "Archives of documents, proceedings and other primary sources. They are not datasets, so they sit "
                     "outside the main catalog."}


def svg_size(path):
    """(width, height) of an SVG from its viewBox, else its numeric width/height attributes; (None, None) if neither."""
    head = path.read_text(encoding="utf-8", errors="replace")[:4096]
    tag = re.search(r"<svg\s[^>]*>", head)
    if not tag:
        return None, None
    vb = re.search(r'viewBox="\s*[-\d.]+[\s,]+[-\d.]+[\s,]+([\d.]+)[\s,]+([\d.]+)\s*"', tag.group(0))
    if vb:
        return round(float(vb.group(1))), round(float(vb.group(2)))
    w = re.search(r'\swidth="([\d.]+)', tag.group(0))
    h = re.search(r'\sheight="([\d.]+)', tag.group(0))
    if w and h:
        return round(float(w.group(1))), round(float(h.group(1)))
    return None, None


def copy_site_files(inputs, out):
    """Copy the co-usage figures and download files into the site; returns what was copied."""
    figs, downs = [], []
    for name, label in COUSAGE_FIGURES:
        src = inputs.root / "outputs" / "figures" / name
        if src.is_file():
            shutil.copyfile(src, out / "assets" / name)
            figs.append((name, label) + svg_size(src))
    if not figs:
        inputs.notices.append("co-usage figures missing (outputs/figures/*.svg): network figures omitted")
    for name, label in COUSAGE_DOWNLOADS:
        src = inputs.root / "data" / "citations" / name
        if src.is_file():
            (out / "downloads").mkdir(exist_ok=True)
            shutil.copyfile(src, out / "downloads" / name)
            downs.append((name, label, max(1, round(src.stat().st_size / 1024))))
    if len(downs) < len(COUSAGE_DOWNLOADS):
        inputs.notices.append("some co-usage download files missing (data/citations/): their links are omitted")
    return {"figures": figs, "downloads": downs}


# ---------------------------------------------------------------- machine-readable layer

def _dump(path, obj, compact=True):
    path.parent.mkdir(parents=True, exist_ok=True)
    kw = {"separators": (",", ":")} if compact else {"indent": 1}
    data = json.dumps(obj, ensure_ascii=False, **kw).encode("utf-8")
    path.write_bytes(data)
    return len(data)


def _dump_lines(path, items):
    """JSON array, compact, one record per line (keeps single lines short for line-based scanners and diffs)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    body = (","+"\n").join(json.dumps(i, ensure_ascii=False, separators=(",", ":")) for i in items)
    data = ("[" + body + "]" + "\n").encode("utf-8")
    path.write_bytes(data)
    return len(data)


def write_api(inputs, out):
    """api/catalog.json, api/datasets/<id>.json, api/comparisons.json, api/cousage_nodes.json, api/domains.json.
    Rows are the same live rows that get dataset pages (status 'removed' excluded). Values are the raw catalog
    values; returns byte sizes by name."""
    out = Path(out)
    api = out / "api"
    cfg = inputs.cfg
    pidx_facets = inputs.facets or {}

    def facet(rid):
        return {k: v for k, v in pidx_facets.get(rid, {}).items() if k != "id"}

    sizes = {"catalog": _dump_lines(api / "catalog.json", [dict(r, facets=facet(r["id"])) for r in inputs.rows])}
    total = 0
    for r in inputs.rows:
        rid = r["id"]
        obj = {
            "row": dict(r), "facets": facet(rid),
            "top5": [{"id": i, "name": n, "page": url_for("dataset", i, cfg)} for i, n in _top5(inputs, rid)],
            "comparison_groups": [g["slug"] for g in inputs.groups if rid in g["id_list"]],
            "merge_systems": sc.id_systems(r),
            "page": url_for("dataset", rid, cfg),
        }
        total += _dump(api / "datasets" / f"{rid}.json", obj, compact=False)
    sizes["datasets_total"] = total
    sizes["comparisons"] = _dump(api / "comparisons.json", [
        {"domain": g.get("domain", ""), "group": g["group"], "slug": g["slug"], "concept": g.get("concept", ""),
         "ids": [i for i in g["id_list"] if i in inputs.by_id], "how_to_choose": g.get("how_to_choose", ""),
         "page": url_for("compare", g["slug"], cfg)} for g in inputs.groups], compact=False)
    sizes["domains"] = _dump(api / "domains.json", [
        {"code": d["code"], "name": d["name"], "count": d["count"], "core": list(d["core"]),
         "page": url_for("domain", d["code"], cfg)} for d in inputs.domains], compact=False)
    if inputs.nodes is not None:
        sizes["cousage_nodes"] = _dump_lines(api / "cousage_nodes.json", [dict(n) for n in inputs.nodes.values()])
    else:
        inputs.notices.append("co-usage nodes missing (data/citations/cousage_nodes.csv): api/cousage_nodes.json omitted")
    return sizes


def _absolute_note(inputs):
    if not re.match(r"https?://", str(inputs.cfg.get("base_url") or "")):
        inputs.notices.append("sitemap/robots need an absolute base_url: sitemap.xml and robots.txt use relative "
                              "paths until data/site_config.json sets one")


def write_sitemap(pages, out, cfg, build_date=None):
    """pages: URL paths relative to the site root ('' for home). One <url> per page, lastmod = build date."""
    build_date = build_date or datetime.date.today().isoformat()
    b = _base(cfg)
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for p in sorted(pages):
        lines.append(f"  <url><loc>{xml_escape(b + p)}</loc><lastmod>{build_date}</lastmod></url>")
    lines.append("</urlset>")
    (Path(out) / "sitemap.xml").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def write_robots(out, cfg):
    text = "\n".join(["User-agent: *", "Allow: /", "", f"Sitemap: {_base(cfg)}sitemap.xml", ""])
    (Path(out) / "robots.txt").write_text(text, encoding="utf-8", newline="\n")


def write_llms_txt(inputs, out, cfg):
    a = inputs.author
    b = _base(cfg)
    title = a.get("title", "Political Science Data Catalog")
    n = len(inputs.rows)
    doms = [d for d in inputs.domains if d["count"] > 0]
    L = [f"# {title}", "",
         f"> A verified, citable catalog of {n} political science datasets across {len(doms)} subfields, each with "
         f"coverage, access, scholarly record and quality notes. {PROVISIONAL}.", "",
         "## Structure", "",
         f"- [Subfield pages]({b}#subfields): one page per subfield, with its core datasets",
         f"- [Dataset pages]({b}explore/): one page per dataset ({n} in all), reached from the explorer",
         f"- [Comparison pages]({b}compare/): competing measures of the same concept ({len(inputs.groups)} groups)",
         f"- [Explorer]({b}explore/): search and filter the whole catalog",
         f"- [Co-usage network]({b}network/): which datasets are used together in the literature",
         f"- [Methods]({b}methods/): inclusion criteria, quality rubric, citation method", "",
         "## Data and API", "",
         f"- [{b}catalog.csv]({b}catalog.csv): the full catalog as one CSV file",
         f"- [{b}api/catalog.json]({b}api/catalog.json): every dataset row, with its facets, as a JSON array",
         f"- {b}api/datasets/<ID>.json: one dataset by ID (row, facets, top5 = the five datasets whose reference "
         "articles are most often co-cited with this one's in OpenAlex, comparison groups, "
         "merge systems, page URL)",
         f"- [{b}api/comparisons.json]({b}api/comparisons.json): comparison groups with member IDs",
         f"- [{b}api/domains.json]({b}api/domains.json): subfields with counts and core dataset IDs"]
    if inputs.nodes is not None:
        L.append(f"- [{b}api/cousage_nodes.json]({b}api/cousage_nodes.json): co-usage network nodes")
    L += [f"- [{b}api/explorer.json]({b}api/explorer.json) and [{b}api/search.json]({b}api/search.json): "
          "records behind the explorer", "",
          "## Licence", "",
          f"Catalog data: {DATA_LICENCE[0]} ({DATA_LICENCE[1]}). Code: {CODE_LICENCE}. The datasets described keep "
          "their own licences, recorded on each page.", "",
          "## Cite as", "", catalog_cite(a)]
    if a.get("doi"):
        L.append("")
        L.append(f"DOI: https://doi.org/{a['doi']}")
    (Path(out) / "llms.txt").write_text("\n".join(L) + "\n", encoding="utf-8", newline="\n")


# ---------------------------------------------------------------- render

def _env(inputs, guard, build_date):
    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=select_autoescape(["html"]),
                      undefined=StrictUndefined, trim_blocks=True, lstrip_blocks=True, keep_trailing_newline=True)
    cfg = inputs.cfg

    built_kinds = BUILT_KINDS if (inputs.annex4 is not None and inputs.annex5 is not None) else BUILT_KINDS - {"annex"}

    def u(kind, key=""):
        return url_for(kind, key, cfg) if kind in built_kinds else None

    env.filters["t"] = sc.display_html
    env.filters["lk"] = make_linkify(guard)
    env.filters["doi"] = make_doi_links(env.filters["lk"])
    env.filters["acr"] = acronym
    env.filters["one_line"] = one_line
    env.filters["scrub"] = sc.scrub
    env.filters["clip"] = clip
    env.globals.update(
        u=u, dl=lambda n: f"{_base(cfg)}downloads/{n}", has=has, split_list=split_list, asset=lambda n: asset_url(n, cfg), cfg=cfg, author=inputs.author,
        domain_name=CODE_NAME, build_date=build_date, provisional=PROVISIONAL, licence=DATA_LICENCE,
        code_licence=CODE_LICENCE, site_title=inputs.author.get("title", "Political Science Data Catalog"),
        is_unv=sc.is_unv, home_url=url_for("home", "", cfg), csv_url=f"{_base(cfg)}catalog.csv",
        repo_url=str(cfg.get("repo_url") or "").rstrip("/"), all_domains=inputs.domains,
    )
    return env


def _write(out, rel, html):
    p = out / rel / "index.html" if rel else out / "index.html"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(html, encoding="utf-8", newline="\n")


def _prepare_out(out):
    if out.exists():
        if any(out.iterdir()) and not (out / BUILD_MARKER).exists():
            raise ValueError(f"refusing to clear {out}: not empty and not a previous site build")
        shutil.rmtree(out)
    out.mkdir(parents=True)
    (out / BUILD_MARKER).write_text("built by scripts/build_site.py\n", encoding="utf-8")


def render(inputs: Inputs, out: Path, build_date: str | None = None) -> BuildReport:
    out = Path(out)
    build_date = build_date or datetime.date.today().isoformat()
    guard = UnsafeDomains(inputs.root / "data")
    env = _env(inputs, guard, build_date)
    _prepare_out(out)
    pages = Counter()

    _write(out, "", env.get_template("home.html").render(**home_context(inputs)))
    pages["home"] += 1

    for d in inputs.domains:
        _write(out, f"domains/{d['code']}", env.get_template("domain.html").render(**domain_context(inputs, d)))
        pages["domain"] += 1

    pidx = PartnerIndex(inputs.rows)
    tpl = env.get_template("dataset.html")
    for row in inputs.rows:
        _write(out, f"datasets/{row['id']}", tpl.render(**dataset_context(inputs, row, pidx, guard)))
        pages["dataset"] += 1

    _write(out, "compare", env.get_template("compare_index.html").render(**compare_index_context(inputs)))
    pages["compare_index"] += 1
    tpl = env.get_template("compare.html")
    for g in inputs.groups:
        _write(out, f"compare/{g['slug']}", tpl.render(**compare_context(inputs, g)))
        pages["compare"] += 1

    (out / "assets").mkdir(exist_ok=True)
    sizes = write_explorer_json(inputs, out)
    _write(out, "explore", env.get_template("explore.html").render(**explore_context(inputs)))
    pages["explore"] += 1
    site_files = copy_site_files(inputs, out)
    _write(out, "network", env.get_template("network.html").render(**network_context(inputs, site_files)))
    pages["network"] += 1
    _write(out, "methods", env.get_template("methods.html").render(**methods_context(inputs)))
    pages["methods"] += 1
    if inputs.annex4 is not None and inputs.annex5 is not None:
        atpl = env.get_template("annex.html")
        for kind, rows in (("literature", inputs.annex4), ("archives", inputs.annex5)):
            _write(out, f"annex/{kind}", atpl.render(**annex_context(inputs, rows, kind)))
            pages["annex"] += 1
    (out / "404.html").write_text(env.get_template("404.html").render(), encoding="utf-8", newline="\n")
    pages["notfound"] += 1

    for name in ("site.css",) + EXPLORER_ASSETS:
        shutil.copyfile(ASSET_DIR / name, out / "assets" / name)
    sizes.update(write_api(inputs, out))
    shutil.copyfile(inputs.root / "data" / "catalog.csv", out / "catalog.csv")
    paths = sorted(f.parent.relative_to(out).as_posix() + "/" if f.parent != out else ""
                   for f in out.rglob("index.html"))
    _absolute_note(inputs)
    write_sitemap(paths, out, inputs.cfg, build_date)
    write_robots(out, inputs.cfg)
    write_llms_txt(inputs, out, inputs.cfg)
    return BuildReport(pages=dict(pages), notices=list(inputs.notices), sizes=sizes)


def build(root: Path, out: Path) -> BuildReport:
    return render(load_inputs(root), out)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build the static catalog site.")
    ap.add_argument("--out", default="site")
    ap.add_argument("--root", default=".")
    args = ap.parse_args(argv)
    report = build(Path(args.root), Path(args.out))
    for n in report.notices:
        print(f"notice: {n}")
    for k, v in report.pages.items():
        print(f"{k}: {v}")
    for k, v in report.sizes.items():
        print(f"api/{k}: {v} bytes")
    print(f"pages: {sum(report.pages.values())}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
