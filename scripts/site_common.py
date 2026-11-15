"""Pure helpers for the static site: description sentence, JSON-LD, slugs, merge partners, sparkline, cite line.

No file or network access; callers pass rows, facets and the author / site-config dicts.
"""
import re

from markupsafe import Markup, escape

from citations_common import extract_dois
from schema import CODE_NAME

ID_SYSTEMS = [
    ("COW country code", r"\bcow\b|correlates of war|\bccode\b"),
    ("Gleditsch-Ward", r"gleditsch|\bgw(no|code)?\b"),
    ("ISO 3166 alpha-3", r"iso.?3|alpha-3|iso3c"),
    ("ISO 3166 alpha-2", r"iso.?2|alpha-2|iso2c"),
    ("ISO 3166 numeric", r"iso.?num|numeric"),
    ("V-Dem country_id", r"v-?dem|country_id"),
    ("Polity / CSP scode", r"polity|scode|\bcsp\b"),
    ("World Bank code", r"world bank|wb code|wbcode"),
    ("QoG ccodeqog", r"qog"),
    ("ParlGov IDs", r"parlgov"),
    ("FIPS", r"\bfips\b"),
    ("Country name + year", r"country name"),
]

TYPE_PHRASE = {"1": "research dataset", "2": "data portal", "3": "licensed data product"}

_CC = "https://creativecommons.org/licenses/"
_OGL = "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/"
_CC0 = "https://creativecommons.org/publicdomain/zero/1.0/"
_ODBL = "https://opendatacommons.org/licenses/odbl/1-0/"
_ODCBY = "https://opendatacommons.org/licenses/by/1-0/"
# Only versioned Creative Commons names map: a bare "CC BY" would need an invented version. CC0, MIT, ODbL and
# ODC-BY have a single version, so their bare names are unambiguous; OGL has three, so only "v3" maps.
LICENSE_URLS = {
    **{f"cc {v} {n}": _CC + f"{v}/{n}/"
       for v in ("by", "by-sa", "by-nc", "by-nc-sa", "by-nc-nd", "by-nd") for n in ("4.0", "3.0")},
    **{f"cc-{v}-{n}": _CC + f"{v}/{n}/"
       for v in ("by", "by-sa", "by-nc", "by-nc-sa", "by-nc-nd", "by-nd") for n in ("4.0", "3.0")},
    **{f"cc {v} 3.0 igo": _CC + f"{v}/3.0/igo/"
       for v in ("by", "by-sa", "by-nc", "by-nc-sa", "by-nc-nd", "by-nd")},
    "cc0": _CC0,
    "cc0 1.0": _CC0,
    "cc0-1.0": _CC0,
    "mit": "https://opensource.org/licenses/MIT",
    "mit license": "https://opensource.org/licenses/MIT",
    "odbl": _ODBL,
    "odbl 1.0": _ODBL,
    "odbl v1.0": _ODBL,
    "odc-by": _ODCBY,
    "odc-by 1.0": _ODCBY,
    "odc-by v1.0": _ODCBY,
    "open government licence v3.0": _OGL,
    "open government licence v3": _OGL,
    "open government licence 3.0": _OGL,
    "ogl v3.0": _OGL,
    "ogl v3": _OGL,
}


def is_unv(v):
    return str(v or "").strip().upper().startswith("UNVERIFIED")


def is_na(v):
    return str(v or "").strip().upper().startswith("N/A")


def is_url(v):
    return bool(re.match(r"https?://\S+$", str(v or "").strip()))


UNV_SPAN = ('<span class="unv" title="Not confirmed on an official page opened during compilation">'
            'not confirmed</span>')
_PAREN_RE = re.compile(r"\s*\([^()]*\)")
_SEG_SPLIT_RE = re.compile(r"(;\s*|(?<=[.!?])\s+)")


def scrub(v):
    """Remove every parenthetical or ;/sentence segment mentioning UNVERIFIED; "" if nothing meaningful is left."""
    v = str(v or "")
    if "unverified" not in v.lower():
        return v.strip()
    prev = None
    while prev != v:  # innermost parentheticals first, so nesting unwinds
        prev = v
        v = _PAREN_RE.sub(lambda m: "" if "unverified" in m.group(0).lower() else m.group(0), v)
    parts = _SEG_SPLIT_RE.split(v)
    keep = []
    for i in range(0, len(parts), 2):
        seg, sep = parts[i], parts[i + 1] if i + 1 < len(parts) else ""
        if "unverified" not in seg.lower():
            keep.append(seg + sep)
    out = "".join(keep)
    out = re.sub(r"\(\s*\)", "", out)
    out = re.sub(r"\s+", " ", out).strip()
    out = re.sub(r"[\s;,:]+$", "", out)
    return out if re.search(r"[A-Za-z0-9]", out) else ""


def display(v):
    """UNVERIFIED -> "", N/A... -> "n/a", else unchanged (templates choose the marker)."""
    v = "" if v is None else str(v)
    if is_unv(v):
        return ""
    if is_na(v):
        return "n/a"
    return v


def display_html(v):
    """HTML-safe value: every UNVERIFIED occurrence becomes the "not confirmed" span; a leading N/A becomes "n/a"
    and keeps its detail ("N/A (portal offline)" -> "n/a (portal offline)", ruling P19)."""
    v = "" if v is None else str(v)
    if is_na(v):
        v = v.strip()
        return Markup("n/a") + display_html(v[3:]) if len(v) > 3 else Markup("n/a")
    return Markup(re.sub("UNVERIFIED", lambda m: UNV_SPAN, str(escape(v)), flags=re.I))


def _clean(v):
    """Usable value or "" (drops UNVERIFIED, N/A and empty)."""
    v = scrub(v)
    return "" if (not v or is_na(v)) else v


def _short(v, limit=0, cuts=()):
    """First ;-segment of a scrubbed value without parentheticals; if longer than `limit`, cut at the first cut."""
    v = _clean(v)
    while True:
        w = re.sub(r"\s*\([^()]*\)", "", v)
        if w == v:
            break
        v = w
    v = re.sub(r"\s+", " ", v.split(";")[0]).strip(" ,:")
    if limit and len(v) > limit:
        pos = [v.find(c) for c in cuts if v.find(c) > 0]
        if pos:
            v = v[:min(pos)].strip(" ,:")
    return v


def description(row):
    name = _clean(row.get("name")) or _clean(row.get("id"))
    acr = _clean(row.get("acronym"))
    dup = acr and re.search(r"(?<![A-Za-z0-9])" + re.escape(acr) + r"(?![A-Za-z0-9])", name, re.I)
    head = f"{name} ({acr})" if acr and not dup else name
    unit = _short(row.get("unit_of_analysis"), 40, (", ", " and "))
    phrase = TYPE_PHRASE.get(str(row.get("resource_type", "")).strip(), "dataset")
    noun = f"{unit}-level {phrase}" if unit else phrase
    art = "an" if noun[:1].lower() in "aeiou" else "a"
    s = f"{head} is {art} {noun}"
    cover = ", ".join(x for x in (_short(row.get("spatial_coverage"), 60, (": ", ", ")), _short(row.get("temporal_coverage"))) if x)
    if cover:
        s += f" covering {cover}"
    s += "."
    kv = [k for k in (_clean(x) for x in str(row.get("key_variables") or "").split(";")) if k]
    if kv:
        s += " Key variables: " + ", ".join(kv[:5]) + "."
    return s


_LIC_KEYS = sorted(LICENSE_URLS, key=len, reverse=True)

# Words that make the first segment unusable wherever they appear in it (name, parenthetical or trailing scope).
_LIC_BLOCK = ("unverified", "conflict", " vs", "assumed", "not stated", "not separately", "may refer",
              "working paper", "report", "pdf", "restricted", "per search", "search results", "site-wide",
              "other files", "some files", "all rights reserved", "additional")  # "CC0 with additional terms"
# Scopes that tie the licence to something other than the data (site text, a footer, an annex, a terms page,
# a README); an explicit "(data ..." scope overrides them, and a README named beside another source (";") is fine.
_LIC_SCOPE_RE = re.compile(r"site text|site content|footer|\bannex\b|terms of use page|\breadme\b|"
                           r"methodology (?:note|document)")
_LIC_ARTICLE_RE = re.compile(r"\((?:the )?(?:[^\s()]+ )?article\b|\barticle(?:'s)? licen|\bon the article\b|"
                             r"\barticle page\b")
_LIC_LONG = (  # long names -> short keys, at the start of the first segment only
    (re.compile(r"^creative commons attribution((?:[- ](?:non-?commercial|share-?alike|no-?deriv(?:ative)?s))*)"),
     lambda m: "cc by" + "".join("-" + ("nc" if p.startswith("non") else "sa" if p.startswith("share") else "nd")
                                 for p in re.split(r"[- ](?=non|share|no-?d)", m.group(1).strip(" -")) if p)),
    (re.compile(r"^(?:odc|open data commons) open database licen[cs]e(?: \(odbl\))?"), lambda m: "odbl"),
    (re.compile(r"^open data commons attribution licen[cs]e(?: \(odc-by\))?"), lambda m: "odc-by"),
    (re.compile(r"^open database licen[cs]e \(odbl( 1\.0)?\)"), lambda m: "odbl" + (m.group(1) or "")),
)
_LIC_TRAIL_RE = re.compile(r"(?:international|unported|universal)\b\s*")
_LIC_FOR_DATA_RE = re.compile(r"for (?:all )?(?:the )?(?!most |some |certain |selected |other )(?:[a-z]+ )?"
                              r"data(?:sets?| files?)?(?: and metadata)?")
_PAREN = r"\([^()]*(?:\([^()]*\)[^()]*)*\)"
_LIC_PER_THE = r",?\s*per the [^();,]+"  # "…, per the World Bank Data Catalog record" names the source
# After the licence name: up to three parts, each a "for … data" scope, a parenthetical or "per the <source>".
_LIC_REST_RE = re.compile(rf"(?:\s*(?:{_LIC_FOR_DATA_RE.pattern}|{_PAREN}|{_LIC_PER_THE})){{1,3}}")
_LIC_TOKEN_RE = re.compile(r"\b(cc0(?:[- ]1\.0)?|cc[- ]by(?:[- ](?:nc|sa|nd))*(?:[- ]\d\.\d)?(?:[- ]igo)?|"
                           r"MIT\b(?! election)|odbl|odc-by|ogl)\b", re.I)
_NON_DATA_SCOPE_RE = re.compile(r"\b(code|package|software|article|paper|github|repo|repository|metadata|codebook|"
                                r"wiki|site text|website|photos?|reports?|research note|documentation)\b", re.I)
_DATA_WORD_RE = re.compile(r"\bdata(?:set)?s?\b", re.I)
_FIRST_RESTRICT_RE = re.compile(r"(?:for|only for) non-?commercial use|non-?commercial (?:use|purposes) only|"
                                r"(?:research|academic) use only|not for commercial")
_RESTRICT_RE = re.compile(r"non-?commercial|\bacademic\b|research use only|research purposes only|commercial use")


def _top_level_split(text, sep=";"):
    parts, depth, cur = [], 0, []
    for ch in text:
        depth += ch == "("
        depth -= ch == ")" and depth > 0
        if ch == sep and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur).strip())
    return parts


def _lic_id(text):
    """(family, version or "") of a licence name or token, e.g. "cc by-nc", "4.0"."""
    t = re.sub(r"\s+", " ", text.lower().replace("cc-by", "cc by")).strip()
    m = re.match(r"(cc0|cc by(?:-(?:nc|sa|nd))*|mit|odbl|odc-by|ogl|open government licence)[- ]?v?(\d\.\d)?", t)
    if not m:
        return t, ""
    fam = m.group(1).replace("open government licence", "ogl")
    ver = m.group(2) or ""
    if fam.startswith("cc by") and re.search(r"\bigo\b", t):
        fam += " igo"
    return fam, ("" if fam == "cc0" else ver)


def _normalise_first(low):
    for pat, repl in _LIC_LONG:
        low = pat.sub(repl, low, count=1)
    m = re.match(r"(cc[- ]by\S*|cc0|odbl|odc-by)( v?\d\.\d(?: igo)?) ", low)
    if m:  # "4.0 International (CC BY 4.0)" -> "4.0"; restated short name in brackets dropped
        head, rest = m.group(0), low[m.end():]
        rest = _LIC_TRAIL_RE.sub("", rest, count=1) if _LIC_TRAIL_RE.match(rest) else rest
        rest = re.sub(r"^\((?:cc[- ]by[^()]*|cc0[^()]*|odbl|odc-by)\)\s*", "", rest)
        low = (head + rest).strip()
    return low


def _first_segment_url(first):
    low = _normalise_first(first.lower())
    for k in _LIC_KEYS:
        if not low.startswith(k):
            continue
        rest = low[len(k):].strip()
        if rest and not _LIC_REST_RE.fullmatch(rest):
            continue
        if any(w in low for w in _LIC_BLOCK) or _LIC_ARTICLE_RE.search(low):
            return ""
        if _FIRST_RESTRICT_RE.search(low) and "-nc" not in k:
            return ""
        paren = re.search(_PAREN, rest)
        data_scoped = bool(paren and re.match(r"\(\s*data\b", paren.group(0)))
        for m in _LIC_SCOPE_RE.finditer(low):
            if data_scoped or (m.group(0) == "readme" and paren and ";" in paren.group(0)):
                continue
            return ""
        return LICENSE_URLS[k]
    return ""


def _tail_blocks(seg, first_id):
    """True when a later segment contradicts or narrows the first segment's data licence."""
    sl = seg.lower()
    if any(w in sl for w in ("conflict", " vs", "differs", "all rights reserved", "unrecognised", "unrecognized",
                             "noassertion")):
        return True
    if "unverified" in sl and "licen" in sl and _DATA_WORD_RE.search(seg):
        return True  # "data file licence UNVERIFIED": the data licence itself is open
    about_data = _DATA_WORD_RE.search(re.sub(
        r"(?:scientific )?data (?:article|paper|descriptor)|scientific data|data in brief|source datasets?|"
        r"data (?:service|archive|catalogu?e?|portal|bank|hub|cent(?:re|er))", "", sl))
    if _NON_DATA_SCOPE_RE.search(seg) and not about_data:
        return False  # notes about code, packages, articles, codebooks, reports, metadata or site text
    other_file = re.search(r"(?<!licence )(?<!license )\bfiles?\b", sl)
    if other_file and re.search(r"no licen[cs]e|not licen[cs]ed|unverified", sl):
        return True  # another file of the dataset carries no (confirmed) licence
    if "-nc" not in first_id[0] and _RESTRICT_RE.search(sl):
        return True  # a non-commercial or academic-only restriction on an otherwise open licence
    for m in _LIC_TOKEN_RE.finditer(seg):
        fam, ver = _lic_id(m.group(1))
        if fam != first_id[0] or (ver and first_id[1] and ver != first_id[1]):
            return True
    return False


def _license_url(lic):
    """Licence URL decided by the first top-level ;-segment: a URL, or a known versioned name alone or followed by
    a parenthetical, a "for ... data" scope or "per the <source>", with no blocking word or non-data scope (site
    text, footer, annex, README, article, ...). Bare "CC BY" style names are omitted (no invented version). Later
    segments block when they conflict, restrict, leave another data file unlicensed, or give another licence to
    the data; notes about code, packages, articles, codebooks, reports or metadata do not."""
    lic = str(lic or "").strip()
    if not lic or is_na(lic):
        return ""
    if is_url(lic):
        return _clean(lic)
    if "all rights reserved" in lic.lower():
        return ""
    first, *tail = _top_level_split(lic)
    url = _first_segment_url(first)
    if not url:
        return ""
    first_id = _lic_id(_normalise_first(first.lower()))
    if any(_tail_blocks(seg, first_id) for seg in tail):
        return ""
    return url


def jsonld(row, facet, author, cfg, page_url):
    domain = CODE_NAME.get(row.get("primary_domain", ""), "political science")
    desc = description(row)
    bu = _clean(row.get("best_used_for"))
    if bu:
        desc += " " + bu
    if len(desc) > 4900:
        desc = desc[:4899].rstrip() + "…"
    if len(desc) < 50:
        desc = f"{desc} Catalog entry in the domain: {domain}."
    out = {
        "@context": "https://schema.org",
        "@type": "Dataset",
        "name": _clean(row.get("name")) or row.get("id", ""),
        "description": desc,
        "url": page_url,
    }
    if _clean(row.get("acronym")):
        out["alternateName"] = _clean(row["acronym"])
    if is_url(_clean(row.get("landing_url"))):
        out["sameAs"] = _clean(row["landing_url"])
    doi_field = str(row.get("doi") or "").strip()
    if doi_field and not (is_na(doi_field) or is_unv(doi_field)):
        data_dois = [d for d, kind in extract_dois(doi_field) if kind == "data"]
        pick = ""
        if len(data_dois) == 1:
            pick = data_dois[0]
        else:  # several dataset DOIs: only one labelled as the concept / all-versions DOI is unambiguous
            for seg in doi_field.lower().split(";"):
                if ("all versions" in seg or "concept" in seg) and any(d in seg for d in data_dois):
                    pick = next(d for d in data_dois if d in seg)
                    break
        if pick:
            out["identifier"] = "https://doi.org/" + pick
    if _clean(row.get("maintainer")):
        out["creator"] = {"@type": "Organization", "name": _clean(row["maintainer"])}
    lic = _license_url(row.get("license"))
    if lic:
        out["license"] = lic
    sy = str((facet or {}).get("start_year", "")).strip()
    ey = str((facet or {}).get("end_year", "")).strip()
    if sy and ey:
        out["temporalCoverage"] = f"{sy}/{ey}"
    if _clean(row.get("spatial_coverage")):
        out["spatialCoverage"] = {"@type": "Place", "name": _clean(row["spatial_coverage"])}
    if is_url(_clean(row.get("download_url"))):
        dist = {"@type": "DataDownload", "contentUrl": _clean(row["download_url"])}
        if _clean(row.get("formats")):
            dist["encodingFormat"] = _clean(row["formats"])
        out["distribution"] = [dist]
    out["isAccessibleForFree"] = str(row.get("access_level", "")).strip() == "open"
    if _clean(row.get("citation")):
        out["citation"] = _clean(row["citation"])
    catalog = {"@type": "DataCatalog", "name": author.get("title", ""), "url": cfg.get("base_url", "/")}
    if author.get("doi"):
        catalog["identifier"] = "https://doi.org/" + author["doi"]
    out["includedInDataCatalog"] = catalog
    if _clean(row.get("last_verified_on")):
        out["dateModified"] = _clean(row["last_verified_on"])
    return out


def slugify(name):
    return re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-") or "item"


def unique_slugs(names):
    seen, out = {}, []
    for n in names:
        base = slugify(n)
        seen[base] = seen.get(base, 0) + 1
        out.append(base if seen[base] == 1 else f"{base}-{seen[base]}")
    return out


_NO_KEYS_RE = re.compile(r"\s*(?:none|no\b|not\b|n/a)", re.I)


def id_systems(row):
    """ID_SYSTEMS labels named in merge_keys; [] when the field is empty, UNVERIFIED or opens with none/no/not/N/A
    (the row declares no standard identifier, so codes named in its explanation are not its keys)."""
    mk = str(row.get("merge_keys") or "")
    if not mk or is_unv(mk) or _NO_KEYS_RE.match(mk):
        return []
    return [label for label, pat in ID_SYSTEMS if re.search(pat, mk, re.I)]


def _guides(r):
    try:
        return int(r.get("guide_count") or 0)
    except ValueError:
        return 0


def merge_partners(row, rows, limit=12):
    """Most specific system (fewest live rows use it; ties by ID_SYSTEMS order) and other rows sharing it."""
    mine = id_systems(row)
    if not mine:
        return "", []
    live = [r for r in rows if r.get("status") != "removed"]
    systems = {r["id"]: id_systems(r) for r in live}
    counts = {label: 0 for label, _ in ID_SYSTEMS}
    for labels in systems.values():
        for label in labels:
            counts[label] += 1
    best = min(mine, key=lambda label: counts[label])
    others = [r for r in live if r["id"] != row["id"] and best in systems[r["id"]]]
    others.sort(key=lambda r: (-_guides(r), r["id"]))
    return best, others[:limit]


def sparkline_svg(counts_by_year, years):
    """Inline SVG 120x28: one stroke, no axes, accessible title; years absent from the data count as zero."""
    w, h, pad = 120, 28, 3
    by = {int(c["year"]): int(c["count"]) for c in counts_by_year or []}
    vals = [by.get(y, 0) for y in years]
    if years:
        title = f"Citations per year, {years[0]} to {years[-1]}: " + ", ".join(map(str, vals))
    else:
        title = "No citation counts"
    if len(vals) < 2:
        pts = f"{pad},{h - pad} {w - pad},{h - pad}"
    else:
        top = max(vals) or 1
        step = (w - 2 * pad) / (len(vals) - 1)
        pts = " ".join(f"{pad + i * step:.1f},{h - pad - v / top * (h - 2 * pad):.1f}" for i, v in enumerate(vals))
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
            f'role="img" aria-label="{title}"><title>{title}</title>'
            f'<polyline points="{pts}" fill="none" stroke="currentColor" stroke-width="2" '
            f'stroke-linejoin="round" stroke-linecap="round"/></svg>')


def cite_entry(row, author, cfg):
    url = cfg.get("base_url", "/").rstrip("/") + f"/datasets/{row['id']}/"
    where = f" Zenodo. https://doi.org/{author['doi']}." if author.get("doi") else ""
    return (f"{author['citation_name']}. {author['year']}. {author['title']}. "
            f"Entry {row['id']} ({_clean(row.get('name')) or row['id']}).{where} {url}")
