"""Derive normalised facets (unit class, region tags, year span, programmatic flag) from the
free-text catalog columns, for the static site (spec 2026-11-12-catalog-site-design.md, section 4).

Writes data/derived/facets.csv and logs/facets_<date>.md.  Run: python scripts/derive_facets.py

Deviation from the spec table: the legislator-or-vote rule uses a word-bounded "votes?" that excludes
"vote shares", so "voter" and vote-share units are not tagged as legislator votes ("voter" goes to
respondent); "household" units are respondent (survey microdata), not organisation.
"""
import csv
import datetime as dt
import re
import sys
from collections import Counter

import schema

FIELDS = ["id", "unit_class", "region_tags", "start_year", "end_year", "has_programmatic"]

# ---------------------------------------------------------------- unit class
# Ordered: the first matching rule wins (precedence as in the spec).
UNIT_RULES = [
    ("dyad", r"dyad"),
    ("grid-cell", r"grid|cell|raster"),
    ("event", r"event|incident|attack|protest|speech act"),
    ("respondent", r"respondent|individual|interview|panel|wave"),
    ("legislator-or-vote", r"legislator|roll.?call|\bvotes?\b(?!\s*shares?)|bill|member of parliament|mp\b"),
    ("party", r"party|parties|manifesto|candidate list"),
    ("document", r"document|text|speech|article|treaty|law|court decision|case"),
    ("subnational", r"state|province|region|district|municipalit|county|city|constituency|precinct"),
    ("country-year", r"country.?year|state.?year|polity.?year|nation.?year"),
    ("country", r"country|state|nation"),
    # Tuning round 1: appended after the spec table, so they only affect rows that were 'other'.
    ("event", r"conflict|\bwar\b|episode|intervention|transaction|deal\b|airing|encounter|dispute|spell|exercise|decision"),
    ("respondent", r"voter|household"),
    ("election-or-candidate", r"candidate|election|ballot|primary|runoff|campaign"),
    ("person-or-actor", r"leader|officeholder|official|deputy|judge|justice|commissioner|person|politician|"
                        r"senator|rebel|minister|member\b"),
    ("organisation", r"firm|organi[sz]ation|company|facility|agency|institution|\bio\b|ngo|enterprise|"
                     r"supplier|advertiser"),
    ("geospatial", r"polygon|boundar|polyline|segment|map feature|geometry|shapefile|vector"),
    ("study-or-record", r"study|studies|dataset|record|file|repository|entry|table|report|fact-check|"
                        r"agreement|notification|measure"),
]
_UNIT_COMPILED = [(c, re.compile(p)) for c, p in UNIT_RULES]


# Phrases where "state" means a country or a government role, never a subnational unit.
_STATE_MASK = re.compile(r"head of state|member states?|nation-state|state system", re.I)
_BARE_STATE_PERIOD = re.compile(r"\bstate[- ]?(year|month|day|quarter)\b")
# Evidence (in the unit text, parentheses included, or in spatial_coverage) that "state" is subnational.
_SUBNATIONAL_SIGNAL = re.compile(
    r"subnational|sub-national|province|\bland\b|l(ae|ä)nder|count(y|ies)|municipal|governor|"
    r"state legislature|\b50 (u\.s\. )?states|(united states|u\.s\.|\bus\b|american)\b.{0,40}\bstates\b",
    re.I)
_LEAD_SPLIT = re.compile(r";|,| and | plus | with ")
_DYAD_PAREN = re.compile(r"\(\s*(\w+[- ])?dyad")
_STATE_SYSTEM = re.compile(r"\bstate(?=[ ]*(?:\([ ]*)?membership)")
_PARENS = re.compile(r"\([^)]*\)")


def _classify(s, n_rules=None):
    for cls, rx in _UNIT_COMPILED[:n_rules]:
        if rx.search(s):
            return cls
    return "other"


_N_SPEC_RULES = 10  # rules before the "tuning round 1" additions
_COMPANION_WORDS = re.compile(r"plus|file|version|profile|companion")


def _unit_paren_is_dyad(full):
    """True when the parenthetical attached to the leading unit describes it as a dyad."""
    pm = _PARENS.search(full)
    return bool(pm and ";" not in full[:pm.start()] and "dyad" in pm.group()
                and not _COMPANION_WORDS.search(pm.group()))


def unit_class(s, spatial=""):
    """Class of a unit_of_analysis string.  Parenthetical explanations are ignored first (the full
    text is used only if the core text gives 'other'); a bare state-year/-month/-day is a
    country-level unit unless the text or spatial_coverage points to subnational states."""
    full = (s or "").lower()
    if not (_SUBNATIONAL_SIGNAL.search(full) or _SUBNATIONAL_SIGNAL.search(spatial or "")):
        # COW state-system entries ("state membership spell") are country-level units
        full = _STATE_SYSTEM.sub("country", full)
    full = _STATE_MASK.sub(" ", full)
    core = _PARENS.sub(" ", full)
    signal = bool(_SUBNATIONAL_SIGNAL.search(full) or _SUBNATIONAL_SIGNAL.search(spatial or ""))
    if _BARE_STATE_PERIOD.search(core) and not signal:
        core = _BARE_STATE_PERIOD.sub(lambda m: "country-" + m.group(1), core)
        full = _BARE_STATE_PERIOD.sub(lambda m: "country-" + m.group(1), full)
    if "dyad" in full:
        # dyad is the unit only when it leads, or sits in a parenthetical that opens with a dyad phrase
        # or explains a country/other lead; elsewhere it names a companion file and the leading unit wins.
        lead = _LEAD_SPLIT.split(core.strip(), 1)[0]
        if "dyad" in lead:
            return "dyad"
        lead_cls = _classify(lead)
        if _classify(lead, _N_SPEC_RULES) == "other" or _unit_paren_is_dyad(full):
            return "dyad"  # lead is outside the spec table, or its own parenthetical is a dyad
        if "dyad" not in core and (_DYAD_PAREN.search(full) or lead_cls in ("country", "other")):
            return "dyad"
        core, full = lead, full.replace("dyad", " ")
    cls = _classify(core)
    return cls if cls != "other" else _classify(full)


# ---------------------------------------------------------------- regions
REGION_KEYWORDS = {
    "global": [r"global", r"worldwide", r"world", r"all countries", r"all (un|independent) (member )?states",
               r"all sovereign states", r"international system"],
    "europe": [r"europe", r"\bEU\b", r"\bEEC\b", r"nordic", r"scandinavia", r"balkans", r"baltic",
               r"post-communist", r"postcommunist", r"soviet", r"\bOECD\b"],
    "north-america": [r"north america"],
    "latin-america": [r"latin america", r"south america", r"central america", r"caribbean", r"andean",
                      r"americas"],
    "sub-saharan-africa": [r"sub-saharan", r"\bafrica\b", r"african", r"sahel"],
    "mena": [r"middle east", r"\bmena\b", r"north africa", r"northern africa", r"arab", r"maghreb", r"\bgulf\b"],
    "south-asia": [r"south asia"],
    "east-asia": [r"east asia", r"asia-pacific"],
    "southeast-asia": [r"southeast asia", r"south-east asia", r"\basean\b"],
    "central-asia": [r"central asia", r"caucasus"],
    "oceania": [r"oceania", r"pacific island", r"australasia"],
}
# Keywords match case-insensitively except those that carry capitals (\bEU\b, \bOECD\b, ...).
_REGION_COMPILED = {
    r: [re.compile(p, 0 if re.search(r"[A-Z]", p) else re.I) for p in ps] for r, ps in REGION_KEYWORDS.items()
}
_NORTH_AFRICA = re.compile(r"north(ern)? africa", re.I)

# Member countries by region; used (a) as the country-name table and (b) to roll several named
# countries up to their region(s) when no region keyword matches.
_COUNTRIES_BY_REGION = {
    "europe": """Albania|Andorra|Austria|Belarus|Belgium|Bosnia and Herzegovina|Bulgaria|Croatia|Cyprus|Czechia|
        Denmark|Estonia|Finland|France|Germany|Greece|Hungary|Iceland|Ireland|Italy|Kosovo|Latvia|Liechtenstein|
        Lithuania|Luxembourg|Malta|Moldova|Monaco|Montenegro|Netherlands|North Macedonia|Norway|Poland|Portugal|
        Romania|Russia|San Marino|Serbia|Slovakia|Slovenia|Spain|Sweden|Switzerland|Ukraine|United Kingdom""",
    "north-america": "Canada|United States",
    "latin-america": """Argentina|Belize|Bolivia|Brazil|Chile|Colombia|Costa Rica|Cuba|Dominican Republic|Ecuador|
        El Salvador|Guatemala|Guyana|Haiti|Honduras|Jamaica|Mexico|Nicaragua|Panama|Paraguay|Peru|Suriname|
        Trinidad and Tobago|Uruguay|Venezuela|Puerto Rico""",
    "sub-saharan-africa": """Angola|Benin|Botswana|Burkina Faso|Burundi|Cabo Verde|Cameroon|Central African Republic|
        Chad|Comoros|Congo|Democratic Republic of the Congo|Cote d'Ivoire|Djibouti|Equatorial Guinea|Eritrea|Eswatini|
        Ethiopia|Gabon|Gambia|Ghana|Guinea|Guinea-Bissau|Kenya|Lesotho|Liberia|Madagascar|Malawi|Mali|Mauritania|
        Mauritius|Mozambique|Namibia|Niger|Nigeria|Rwanda|Senegal|Seychelles|Sierra Leone|Somalia|South Africa|
        South Sudan|Sudan|Tanzania|Togo|Uganda|Zambia|Zimbabwe""",
    "mena": """Algeria|Bahrain|Egypt|Iran|Iraq|Israel|Jordan|Kuwait|Lebanon|Libya|Morocco|Oman|Palestine|Qatar|
        Saudi Arabia|Syria|Tunisia|Turkey|United Arab Emirates|Yemen""",
    "south-asia": "Afghanistan|Bangladesh|Bhutan|India|Maldives|Nepal|Pakistan|Sri Lanka",
    "east-asia": "China|Hong Kong|Japan|Mongolia|North Korea|South Korea|Taiwan",
    "southeast-asia": "Brunei|Cambodia|Indonesia|Laos|Malaysia|Myanmar|Philippines|Singapore|Thailand|Timor-Leste|Vietnam",
    "central-asia": "Armenia|Azerbaijan|Georgia|Kazakhstan|Kyrgyzstan|Tajikistan|Turkmenistan|Uzbekistan",
    "oceania": """Australia|Fiji|Kiribati|Marshall Islands|Micronesia|Nauru|New Zealand|Palau|Papua New Guinea|
        Samoa|Solomon Islands|Tonga|Tuvalu|Vanuatu""",
}
COUNTRY_REGION = {}
for _r, _names in _COUNTRIES_BY_REGION.items():
    for _n in _names.replace("\n", "").split("|"):
        COUNTRY_REGION[_n.strip()] = _r

# variant -> canonical short name
_VARIANTS = {
    "USA": "United States", "U.S.": "United States", "U.S.A.": "United States", "US": "United States",
    "United States of America": "United States", "American": "United States",
    "UK": "United Kingdom", "U.K.": "United Kingdom", "Britain": "United Kingdom",
    "Great Britain": "United Kingdom", "England": "United Kingdom", "Scotland": "United Kingdom",
    "Wales": "United Kingdom", "Northern Ireland": "United Kingdom", "British": "United Kingdom",
    "Czech Republic": "Czechia", "Holland": "Netherlands", "Dutch": "Netherlands",
    "Russian Federation": "Russia", "USSR": "Russia", "Soviet Union": "Russia", "Russian": "Russia",
    "Macedonia": "North Macedonia", "Burma": "Myanmar", "Swaziland": "Eswatini",
    "Ivory Coast": "Cote d'Ivoire", "Côte d'Ivoire": "Cote d'Ivoire", "Cape Verde": "Cabo Verde",
    "DR Congo": "Democratic Republic of the Congo", "DRC": "Democratic Republic of the Congo",
    "Republic of the Congo": "Congo", "East Timor": "Timor-Leste",
    "Korea": "South Korea", "Republic of Korea": "South Korea", "South Korean": "South Korea",
    "DPRK": "North Korea",
    "PRC": "China", "People's Republic of China": "China", "Chinese": "China", "Mainland China": "China",
    "Iranian": "Iran", "Syrian Arab Republic": "Syria", "Viet Nam": "Vietnam",
    "Turkiye": "Turkey", "Türkiye": "Turkey", "Palestinian Territories": "Palestine",
    "West Bank": "Palestine", "Gaza": "Palestine",
    "German": "Germany", "French": "France", "Italian": "Italy", "Spanish": "Spain", "Swedish": "Sweden",
    "Danish": "Denmark", "Norwegian": "Norway", "Finnish": "Finland", "Irish": "Ireland", "Polish": "Poland",
    "Swiss": "Switzerland", "Austrian": "Austria", "Belgian": "Belgium", "Greek": "Greece",
    "Hungarian": "Hungary", "Czech": "Czechia", "Portuguese": "Portugal", "Ukrainian": "Ukraine",
    "Japanese": "Japan", "Indian": "India", "Canadian": "Canada", "Mexican": "Mexico", "Brazilian": "Brazil",
    "Australian": "Australia", "Israeli": "Israel", "Egyptian": "Egypt", "Turkish": "Turkey",
    "Taiwanese": "Taiwan", "Philippine": "Philippines", "Nigerian": "Nigeria", "Kenyan": "Kenya",
    "Ghanaian": "Ghana", "Chilean": "Chile", "Argentine": "Argentina", "Colombian": "Colombia",
    "Peruvian": "Peru",
}
COUNTRY_NAMES = {n: n for n in COUNTRY_REGION}
COUNTRY_NAMES.update(_VARIANTS)
# longest first so "Guinea-Bissau" / "South Sudan" / "Niger" resolve correctly
_COUNTRY_RX = re.compile(
    r"(?<![\w'-])(" + "|".join(re.escape(n) for n in sorted(COUNTRY_NAMES, key=len, reverse=True)) + r")(?![\w-])"
)
# Tuning round 1: a cross-national sample whose region is not stated ("70 countries", "WTO members").
_MULTI_COUNTRY = re.compile(r"countries|economies|\bstates\b|nations|jurisdictions|members|multilateral|"
                            r"international organi[sz]ations|security council|territories", re.I)
_EMPTY_VALUES = {"", "unverified", "n/a", "na", "none"}


US_STATES = (
    "Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|Delaware|Florida|Hawaii|Idaho|Illinois|"
    "Indiana|Iowa|Kansas|Kentucky|Louisiana|Maine|Maryland|Massachusetts|Michigan|Minnesota|Mississippi|"
    "Missouri|Montana|Nebraska|Nevada|New Hampshire|New Jersey|New Mexico|New York|North Carolina|North Dakota|"
    "Ohio|Oklahoma|Oregon|Pennsylvania|Rhode Island|South Carolina|South Dakota|Tennessee|Texas|Utah|Vermont|"
    "Virginia|Washington|West Virginia|Wisconsin|Wyoming|Georgia").split("|")
_US_STATE_RX = re.compile(r"(?<![\w'-])(" + "|".join(sorted(US_STATES, key=len, reverse=True)) + r")(?![\w-])")
_US_CONTEXT = re.compile(r"united states|u"+r"\.s\."+r"|\bUS\b|american", re.I)


def countries_in(s):
    """Canonical country names found in s (document order, unique)."""
    s = s or ""
    # US state names are masked first ("New Mexico" is not Mexico); "Georgia" is the US state, not the
    # country, when the string has a US context or names at least two other US states.
    states = {m.group(1) for m in _US_STATE_RX.finditer(s)}
    others = states - {"Georgia"}
    us_context = bool(_US_CONTEXT.search(s)) or len(others) >= 2
    masked = _US_STATE_RX.sub(lambda m: "Georgia" if (m.group(1) == "Georgia" and not us_context) else " ", s)
    out = []
    for m in _COUNTRY_RX.finditer(masked):
        c = COUNTRY_NAMES[m.group(1)]
        if c not in out:
            out.append(c)
    if len(others) >= 2 and "United States" not in out:
        out.append("United States")
    return out


def region_tags(s):
    s = s or ""
    if s.strip().lower() in _EMPTY_VALUES:
        return ["unclassified"]
    tags = []
    for region, rxs in _REGION_COMPILED.items():
        # "North Africa" is mena and must not trigger the sub-saharan keywords ("africa")
        text = _NORTH_AFRICA.sub(" ", s) if region == "sub-saharan-africa" else s
        if any(rx.search(text) for rx in rxs):
            tags.append(region)
    if "global" in tags:
        return ["global"]
    if tags:
        return tags
    countries = countries_in(s)
    if not countries and _MULTI_COUNTRY.search(s):
        return ["multi-country"]
    if len(countries) == 1:
        return ["single-country:" + countries[0]]
    regs = []
    for c in countries:
        r = COUNTRY_REGION.get(c)
        if r and r not in regs:
            regs.append(r)
    if regs:
        return regs
    return ["multi-country"] if _MULTI_COUNTRY.search(s) else ["unclassified"]


# ---------------------------------------------------------------- years
_YEAR = re.compile(r"(?<!\d)(1[5-9]\d\d|20\d\d)(?!\d)")
_PRESENT = re.compile(r"(?<!then )present|ongoing|to date|continuing", re.I)
_PARENS = re.compile(r"\([^()]*\)")


# Years that sit in a release/version/update/file context are provenance, not coverage.
_YEAR_CONTEXT = re.compile(
    r"(v"+r"\d|version|release|released|updated?|file|edition|vintage|as of|accessed|published|checked)"
    r"[^"+r"\d]{0,12}$", re.I)
_RANGE_END = re.compile(r"(-|–|—|to\s)\s*$", re.I)


def _coverage_years(s, today_year):
    years = []
    for m in _YEAR.finditer(s):
        before = s[:m.start()]
        y = int(m.group(1))
        if _YEAR_CONTEXT.search(before[-24:]):
            continue
        if y > today_year + 1 and not _RANGE_END.search(before[-4:]):
            continue
        years.append(y)
    return years


def year_span(s, today_year):
    s = s or ""
    if s.strip().lower().startswith("unverified"):
        return ("", "")
    years = _coverage_years(s, today_year)
    outside = s
    while _PARENS.search(outside):
        outside = _PARENS.sub(" ", outside)
    # "present" in a parenthetical note does not extend a range (two or more years) stated outside it (P21)
    scope = outside if len(_coverage_years(outside, today_year)) >= 2 else s
    present = bool(_PRESENT.search(scope))
    if not years and not present:
        return ("", "")
    if not years:
        return ("", str(today_year))
    end = today_year if present else max(years)
    return (str(min(years)), str(end))


_NO_PROGRAMMATIC = re.compile(r"^"+r"\s*(none|no\b|n/a|not\b|unverified)", re.I)


def has_programmatic(s):
    """1 only for official programmatic access (API, package). A value that starts with none/no/n-a/not/
    unverified is 0 by design, even when it goes on to name a third-party package; those packages stay
    visible in the dataset page's programmatic_access text."""
    s = (s or "").strip()
    return "0" if not s or _NO_PROGRAMMATIC.search(s) else "1"


# ---------------------------------------------------------------- driver
def derive(rows, today_year):
    out = []
    for r in rows:
        if r.get("status", "") == "removed":
            continue
        start, end = year_span(r.get("temporal_coverage", ""), today_year)
        out.append({
            "id": r["id"],
            "unit_class": unit_class(r.get("unit_of_analysis", ""), r.get("spatial_coverage", "")),
            "region_tags": ";".join(region_tags(r.get("spatial_coverage", ""))),
            "start_year": start,
            "end_year": end,
            "has_programmatic": has_programmatic(r.get("programmatic_access", "")),
        })
    return out


def write_report(facets, rows, path):
    by_id = {r["id"]: r for r in rows}
    n = len(facets)
    units = Counter(f["unit_class"] for f in facets)
    tags = Counter(t for f in facets for t in f["region_tags"].split(";"))
    blank = sum(1 for f in facets if not f["start_year"] and not f["end_year"])
    lines = [f"# Derived facets report ({dt.date.today().isoformat()})", "",
             f"Rows: {n}", "", "## Unit class", ""]
    for k, v in units.most_common():
        lines.append(f"- {k}: {v} ({100 * v / max(n, 1):.1f} %)")
    lines += ["", "## Region tags (single-country tags pooled)", ""]
    pooled = Counter()
    for t, v in tags.items():
        pooled["single-country:*" if t.startswith("single-country:") else t] += v
    for k, v in pooled.most_common():
        lines.append(f"- {k}: {v}")
    lines += ["", f"Rows with blank years: {blank}", "",
              "## First 50 'other' / 'unclassified' rows", ""]
    shown = 0
    for f in facets:
        if shown >= 50:
            break
        if f["unit_class"] == "other" or f["region_tags"] == "unclassified":
            r = by_id.get(f["id"], {})
            lines.append(f"- {f['id']}: unit={r.get('unit_of_analysis', '')!r} -> {f['unit_class']}; "
                         f"spatial={r.get('spatial_coverage', '')!r} -> {f['region_tags']}")
            shown += 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def main():
    today = dt.date.today()
    with open(schema.CATALOG, encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    live = [r for r in rows if r.get("status", "") != "removed"]
    facets = derive(live, today.year)
    schema.DERIVED.mkdir(parents=True, exist_ok=True)
    with open(schema.DERIVED / "facets.csv", "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(facets)
    write_report(facets, live, schema.LOGS / f"facets_{today.isoformat()}.md")
    n = len(facets)
    o = sum(f["unit_class"] == "other" for f in facets)
    u = sum(f["region_tags"] == "unclassified" for f in facets)
    print(f"{n} rows; other {o} ({100 * o / n:.1f} %); unclassified {u} ({100 * u / n:.1f} %)")


if __name__ == "__main__":
    sys.exit(main())
