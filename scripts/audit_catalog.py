"""Self-audit of data/catalog.csv and the annex files (PROTOCOL.md section 8, step 6; `audit` command).

Read-only: reports problems, changes nothing. Checks structure, controlled vocabularies, scores,
cross-references, duplicates, link status and must-include coverage for completed domains.
Usage: python scripts/audit_catalog.py  -> prints a report and writes logs/audit_YYYY-MM-DD.md
"""
import csv
import datetime as dt
import json
import re
from collections import Counter, defaultdict
from urllib.parse import urlparse

import unsafe
from citations_common import count_value_ok, is_legacy_count, openalex_count_mismatches
from schema import ANNEX_COLUMNS, CATALOG, CITATIONS, COLUMNS, DATA, DOMAINS, LOGS, Q_DIMS, ROOT

TODAY = dt.date.today().isoformat()
CODES = {c for _, c, _ in DOMAINS}
ACCESS = {"open", "registration", "restricted", "licensed", "commercial"}
STATUS = re.compile(r"^(active|irregular|frozen|discontinued|superseded by .+|UNVERIFIED.*)$")
LINK = {"ok", "redirect", "blocked-by-site", "broken", ""}
DATE = re.compile(r"^(\d{4}(-\d{2})?|UNVERIFIED.*|N/A.*)$")
URL = re.compile(r"https?://[^\s;,]+")

# PROTOCOL.md section 10: patterns that must match some row (name or acronym) for each completed domain
MUST = {
    "REG": ["V-Dem", "Regimes of the World", "Episodes of Regime Transformation", "Polity5", "Freedom in the World",
            "Nations in Transit", "Freedom on the Net", "Boix", "Autocratic Regimes", "Democracy-Dictatorship|DD ",
            "Bj.rnskov|Rode", "Lexical Index", "Global State of Democracy", "Bertelsmann Transformation",
            "EIU Democracy Index|Democracy Index \\(EIU|Economist Intelligence Unit Democracy", "Unified Democracy",
            "Vanhanen", "Quality of Government", "Worldwide Governance", "Database of Political Institutions",
            "International Country Risk", "Corruption Perceptions", "Rule of Law Index", "Fragile States",
            "Relative Political Capacity", "Hanson"],
    "ELEC": ["Constituency-Level Elections|CLEA", "ParlGov", "Voter Turnout", "Electoral System Design",
             "Democratic Electoral Systems", "NELDA|National Elections Across Democracy", "Perceptions of Electoral Integrity",
             "ElectionGuide", "European Election Database", "MIT Election Data", "Leip", "VEST|Voting and Election Science",
             "GRED|Georeferenced Electoral", "Klarner|State Legislative Election Returns"],
    "PARTY": ["Manifesto Project", "Chapel Hill", "V-Party", "Party Facts", "PopuList", "Global Party Survey", "POPPA",
              "Political Party Database", "DALP|Democratic Accountability and Linkages"],
    "LEG": ["Voteview", "ParlSpeech", "Comparative Legislators", "Parline", "Congressional Bills",
            "Legislative Effectiveness", "Shor|McCarty", "European Parliament.*[Rr]oll|VoteWatch|roll.call.*European Parliament"],
    "EXEC": ["Archigos", "WhoGov", "LEAD|Leader Experience", "REIGN", "CHISOLS", "ParlGov"],
    "CONST": ["Comparative Constitutions Project", "Constitute", "Supreme Court Database", "Linzer|Staton",
              "Regional Authority Index"],
    "OPIN": ["American National Election Stud", "General Social Survey", "Cooperative Election Study", "World Values Survey",
             "European Values Study", "European Social Survey", "International Social Survey Programme|ISSP",
             "Comparative Study of Elect", "Eurobarometer", "Afrobarometer", "Arab Barometer", "Asian Barometer",
             "AmericasBarometer|LAPOP", "Latinobar", "Pew.*Global|Global Attitudes", "American Trends Panel",
             "European Election Stud", "British Election Study", "Nationscape", "Roper"],
    "PE": ["World Development Indicators", "Penn World Table", "Maddison", "World Economic Outlook",
           "International Financial Statistics", "Standardized World Income Inequality", "World Inequality Database",
           "Government Revenue Dataset", "Economic Freedom of the World", "Chinn|KAOPEN", "Comparative Political Data Set",
           "Welfare Entitlements", "SOCX", "Clio Infra", "Cross-National Time-Series"],
    "IR": ["Militarized Interstate Disputes", "National Material Capabilities", "Formal Alliances", "Territorial Change",
           "State System", "Intergovernmental Organizations", "Diplomatic Exchange", "Correlates of War.*Trade|COW.*Trade|Bilateral Trade",
           "Alliance Treaty Obligations|ATOP", "International Crisis Behavior", "Issue Correlates of War|ICOW",
           "General Assembly Voting|UNGA voting|UNGA Ideal", "Design of Trade Agreements|DESTA", "Global Sanctions Data",
           "Threat and Imposition|TIES", "AidData", "SIPRI Military Expenditure", "SIPRI Arms Transfers",
           "Global Diplomacy Index", "Gleditsch", "Militarized Interstate Events|MIE"],
    "CONF": ["UCDP/PRIO Armed Conflict|Armed Conflict Dataset", "Georeferenced Event Dataset|UCDP GED", "Non-State Conflict",
             "One-sided Violence", "ACLED|Armed Conflict Location", "Global Terrorism Database", "ICEWS|Integrated Crisis Early Warning",
             "GDELT", "Ethnic Power Relations", "Minorities at Risk", "Political Instability Task Force|PITF",
             "Major Episodes of Political Violence", "Powell|Global Instances of Coups", "Cline Center Coup",
             "Peace Agreement Dataset", "PA-X", "Pro-Government Militias", "Women in Armed Rebellion|WAAR"],
    "HR": ["CIRIGHTS", "Political Terror Scale", "Fariss|latent human rights", "Human Rights Measurement Initiative|HRMI",
           "Sexual Violence in Armed Conflict|SVAC", "Ill-Treatment and Torture|ITT", "Social Conflict Analysis|SCAD",
           "Mass Mobilization", "NAVCO|Nonviolent and Violent Campaigns"],
    "TEXT": ["ParlSpeech", "General Debate", "Congressional Record", "Media Cloud"],
    "IDENT": ["Ethnic and Cultural Diversity.*Fearon|Fearon.*Diversity", "Fractionalization \\(Alesina", "Religion and State",
              "Association of Religion Data Archives", "DEMIG", "Immigration Policies in Comparison|IMPIC", "GLOBALCIT",
              "International Migrant Stock"],
    "GENDER": ["Women in (National )?Parliaments|IPU.*[Ww]omen", "Gender Quotas Database", "WomanStats"],
    "ENV": ["International Environmental Agreements|IEADB", "Climate Change Laws of the World", "Climate Policy Database",
            "Environmental Performance Index"],
    "GEO": ["PRIO-GRID", "CShapes", "GADM|Global Administrative Areas", "GeoEPR|Geo-referencing Ethnic Power"],
    "AREA": ["FEC campaign finance|OpenFEC", "OpenSecrets", "DIME: Database on Ideology, Money", "American Presidency Project", "Voting and Registration Supplement"],
    "INFRA": ["peacesciencer", "countrycode", "democracyData", "Dataverse.*journal|journal.*Dataverse"],
    "POLICY": ["Comparative Agendas", "Correlates of State Policy", "State Policy Innovation|SPID"],
}


def is_unv(v):
    return str(v).strip().upper().startswith("UNVERIFIED")


OPENALEX_MISMATCH_KEY = "citation_count_openalex_mismatch"


def openalex_mismatch_problems(rows, works_path):
    """Catalog OpenAlex counts that data/citations/openalex_works.csv no longer supports (row missing, status not
    ok, or a different cited_by_count). Skipped silently (empty list) when the works file does not exist."""
    if not works_path.exists():
        return []
    with open(works_path, newline="", encoding="utf-8") as f:
        works = {w["id"]: w for w in csv.DictReader(f)}
    return [f"{i}: {v!r} ({p})" for i, v, p in openalex_count_mismatches(rows, works)]


SITE_TEXT_EXT = {".html", ".json", ".txt", ".xml", ".csv", ".md"}


def unsafe_domain_hits(roots):
    """Blocklisted hostnames in the text files under each existing root: ["path:line: host", ...] (read-only).

    Uses unsafe.find_unsafe, so the hashed blocklist works when the names file is absent. Hits report the host as
    it appears in the text (hash mode) or the listed domain (names mode), never anything else from the line."""
    out = []
    for root in roots:
        if not root.is_dir():
            continue
        for f in sorted(root.rglob("*")):
            if not f.is_file() or f.suffix.lower() not in SITE_TEXT_EXT:
                continue
            try:
                lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            rel = f.relative_to(root).as_posix()
            for n, line in enumerate(lines, 1):
                for host in unsafe.find_unsafe(line):
                    out.append(f"{root.name}/{rel}:{n}: {host}")
    return out


def main():
    with open(CATALOG, newline="", encoding="utf-8") as f:
        raw = list(csv.reader(f))
    header, body = raw[0], raw[1:]
    P = defaultdict(list)  # category -> list of problem strings

    if header != COLUMNS:
        P["structure"].append("header differs from schema COLUMNS")
    for r in body:
        if len(r) != len(COLUMNS):
            P["structure"].append(f"{r[0]}: {len(r)} columns instead of {len(COLUMNS)}")
    rows = [dict(zip(COLUMNS, r)) for r in body if len(r) == len(COLUMNS)]
    ids = {r["id"] for r in rows}
    by_id = {r["id"]: r for r in rows}

    # identifiers
    for i, n in Counter(r["id"] for r in rows).items():
        if n > 1:
            P["structure"].append(f"duplicate id {i}")
    for r in rows:
        if not re.fullmatch(r"[A-Z]+-\d{3}", r["id"]) or r["id"].split("-")[0] not in CODES:
            P["structure"].append(f"bad id {r['id']}")
        if r["id"].split("-")[0] != r["primary_domain"]:
            P["structure"].append(f"{r['id']}: id prefix differs from primary_domain {r['primary_domain']}")

    # vocabularies and formats
    for r in rows:
        i = r["id"]
        if r["primary_domain"] not in CODES:
            P["vocabulary"].append(f"{i}: primary_domain {r['primary_domain']!r}")
        for s in filter(None, (x.strip() for x in r["secondary_domains"].split(";"))):
            if s not in CODES:
                P["vocabulary"].append(f"{i}: secondary domain {s!r}")
        if r["resource_type"] not in {"1", "2", "3"}:
            P["vocabulary"].append(f"{i}: resource_type {r['resource_type']!r}")
        if r["access_level"] not in ACCESS and not is_unv(r["access_level"]):
            P["vocabulary"].append(f"{i}: access_level {r['access_level']!r}")
        if not STATUS.match(r["status"]):
            P["vocabulary"].append(f"{i}: status {r['status']!r}")
        if r["confidence"] not in {"High", "Medium", "Low"}:
            P["vocabulary"].append(f"{i}: confidence {r['confidence']!r}")
        if r["link_status"] not in LINK:
            P["vocabulary"].append(f"{i}: link_status {r['link_status']!r}")
        for c in ("release_date", "last_update"):
            v = r[c].strip()
            if v and not DATE.match(v):
                P["dates"].append(f"{i}: {c} not YYYY or YYYY-MM ({v[:40]!r})")
        for c in ("added_on", "last_verified_on", "links_checked"):
            if r[c] and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", r[c]):
                P["dates"].append(f"{i}: {c} {r[c]!r}")
        cc = r["citation_count"].strip()
        if is_legacy_count(cc):
            P["citation_count_legacy_format"].append(f"{i}: {cc[:60]!r}")
        elif not count_value_ok(cc):
            P["citation_count"].append(f"{i}: {cc[:60]!r}")
        d = r["doi"].strip()
        if d and not (is_unv(d) or d.upper().startswith("N/A") or re.search(r"10\.\d{4,9}/\S+", d)):
            P["doi"].append(f"{i}: {d[:60]!r}")
        if not r["landing_url"].startswith("http"):
            P["urls"].append(f"{i}: landing_url is not a URL ({r['landing_url'][:60]!r})")
        for c in ("landing_url", "download_url", "codebook_url"):
            # a file name after a URL with no opening parenthesis or quote usually means an unencoded space
            for m in re.finditer(r"(https?://[^\s;,()']+) ([^\s;,()']+\.(pdf|xlsx?|csv|zip|docx?|dta))", r[c]):
                P["urls"].append(f"{i}: possible unencoded space in {c}: {m.group(0)[:90]}")
        for c in ("name", "primary_domain", "resource_type", "quality_justification", "found_via"):
            if not r[c].strip() or is_unv(r[c]):
                P["required"].append(f"{i}: {c} empty or UNVERIFIED")

    # OpenAlex counts against the works table (only when data/citations/openalex_works.csv exists)
    P[OPENALEX_MISMATCH_KEY].extend(openalex_mismatch_problems(rows, CITATIONS / "openalex_works.csv"))
    if not P[OPENALEX_MISMATCH_KEY]:
        del P[OPENALEX_MISMATCH_KEY]

    # scores
    ties_down = []
    for r in rows:
        i = r["id"]
        vals = []
        for q in Q_DIMS:
            try:
                v = int(r[q])
                if not 1 <= v <= 5:
                    raise ValueError
                vals.append(v)
            except ValueError:
                P["scores"].append(f"{i}: {q}={r[q]!r}")
        if len(vals) == 8:
            mean = sum(vals) / 8
            try:
                q = float(r["q_overall"])
                if abs(q - mean) > 0.0501:
                    P["scores"].append(f"{i}: q_overall {r['q_overall']} but mean is {mean:.3f}")
                elif abs(mean * 10 - int(mean * 10) - 0.5) < 1e-9 and q < mean:
                    ties_down.append(i)
            except ValueError:
                P["scores"].append(f"{i}: q_overall={r['q_overall']!r}")
        if len(r["quality_justification"]) < 150:
            P["scores"].append(f"{i}: quality_justification shorter than 150 characters")

    if ties_down:
        P["rounding"].append(f"{len(ties_down)} rows whose mean ends in .x5 are rounded down while others round up "
                             f"(float rounding in merge_records.q_overall), e.g. {', '.join(ties_down[:6])}")

    # cross-references
    for r in rows:
        i = r["id"]
        m = re.match(r"superseded by (.+)", r["status"])
        if m:
            t = m.group(1).strip()
            if re.fullmatch(r"[A-Z]+-\d{3}", t):
                if t not in ids:
                    P["cross-references"].append(f"{i}: superseded by unknown id {t}")
            else:
                P["cross-references"].append(f"{i}: successor not in catalog ({t[:70]})")
        for t in filter(None, (x.strip() for x in r["related_datasets"].split(";"))):
            if re.fullmatch(r"[A-Z]+-\d{3}", t) and t not in ids:
                P["cross-references"].append(f"{i}: related id {t} does not exist")
            if t == i:
                P["cross-references"].append(f"{i}: lists itself in related_datasets")

    # provenance
    for r in rows:
        i = r["id"]
        guides = {g.split("(")[0].strip() for g in r["found_via"].split(";") if g.strip().startswith("Guide:")}
        try:
            gc = int(r["guide_count"])
        except ValueError:
            P["provenance"].append(f"{i}: guide_count {r['guide_count']!r}")
            continue
        if gc != len(guides):
            P["provenance"].append(f"{i}: guide_count {gc} but {len(guides)} distinct guide institutions in found_via")

    # duplicates across the catalog
    def norm(s):
        return re.sub(r"[^a-z0-9]", "", s.lower())
    seen = defaultdict(list)
    for r in rows:
        if r["status"] != "removed":
            seen[norm(r["name"])].append(r["id"])
    for k, v in seen.items():
        if len(v) > 1:
            P["duplicates"].append(f"same name: {', '.join(v)}")
    # same acronym AND same landing host within a domain suggests a duplicate (shared acronyms alone are common)
    acr = defaultdict(list)
    for r in rows:
        a = norm(r["acronym"])
        if a and a not in {"na", "none"} and not is_unv(r["acronym"]):
            acr[(a, urlparse(r["landing_url"]).netloc.lower().removeprefix("www."))].append(r["id"])
    for (a, host), v in acr.items():
        if len(v) > 1:
            P["duplicates"].append(f"same acronym {a!r} and landing host {host}: {', '.join(v)}")

    # link status
    for r in rows:
        if r["link_status"] == "broken":
            P["links"].append(f"{r['id']}: broken ({r['acronym'] or r['name'][:40]})")

    # must-includes for completed domains
    prog = json.load(open(DATA / "progress.json", encoding="utf-8"))["domains"]
    done = [c for c, d in prog.items() if d.get("status") == "complete"]
    for c in done:
        for pat in MUST.get(c, []):
            if not any(re.search(pat, r["name"] + " " + r["acronym"], re.I) for r in rows):
                P["must-include"].append(f"{c}: no row matches {pat!r}")
        n = sum(1 for r in rows if r["primary_domain"] == c)
        if n != prog[c].get("rows"):
            P["progress"].append(f"{c}: progress.json rows {prog[c].get('rows')} but catalog has {n}")
        if not (DATA / "narratives" / f"{c}.json").exists():
            P["progress"].append(f"{c}: narrative file missing")

    # built site and public export: no blocklisted hostname
    for h in unsafe_domain_hits([ROOT / "site", ROOT / "public"]):
        P["unsafe-domains-in-site"].append(h)

    # annex files
    for fn in ("literature_databases.csv", "primary_archives.csv"):
        with open(DATA / fn, newline="", encoding="utf-8") as f:
            a = list(csv.reader(f))
        if a[0] != ANNEX_COLUMNS:
            P["annex"].append(f"{fn}: header differs from ANNEX_COLUMNS")
        for r in a[1:]:
            if len(r) != len(ANNEX_COLUMNS):
                P["annex"].append(f"{fn}: {r[0][:40]} has {len(r)} columns")
        names = Counter(norm(r[0]) for r in a[1:])
        for k, n in names.items():
            if n > 1:
                P["annex"].append(f"{fn}: duplicate entry {k}")

    # unsafe domains (data/unsafe_domains.txt) anywhere in catalog-facing files
    from unsafe import find_unsafe
    for r in rows:
        for c, v in r.items():
            for d in find_unsafe(v):
                P["unsafe-domains"].append(f"{r['id']} {c}: {d}")
    for fn in ("literature_databases.csv", "primary_archives.csv", "comparison_groups.csv"):
        text = (DATA / fn).read_text(encoding="utf-8") if (DATA / fn).exists() else ""
        for d in find_unsafe(text):
            P["unsafe-domains"].append(f"{fn}: {d}")
    for p in sorted((DATA / "narratives").glob("*.json")):
        for d in find_unsafe(p.read_text(encoding="utf-8")):
            P["unsafe-domains"].append(f"narratives/{p.name}: {d}")

    # summary statistics
    unv = Counter(c for r in rows for c, v in r.items() if is_unv(v))
    stats = {
        "rows": len(rows), "domains_complete": done,
        "unverified_cells": sum(unv.values()), "unverified_top_fields": unv.most_common(6),
        "confidence": dict(Counter(r["confidence"] for r in rows)),
        "link_status": dict(Counter(r["link_status"] for r in rows)),
        "stale_verification_>180d": sum(1 for r in rows if r["last_verified_on"] and
                                         r["last_verified_on"] < (dt.date.today() - dt.timedelta(days=180)).isoformat()),
    }
    out = [f"# Catalog audit {TODAY}", "", "## Summary", "```", json.dumps(stats, indent=1), "```", ""]
    for cat in sorted(P):
        out += [f"## {cat} ({len(P[cat])})", ""] + [f"- {p}" for p in P[cat]] + [""]
    if not P:
        out.append("No problems found.")
    LOGS.mkdir(exist_ok=True)
    path = LOGS / f"audit_{TODAY}.md"
    path.write_text("\n".join(out), encoding="utf-8")
    print(json.dumps(stats))
    for cat in sorted(P):
        print(f"{cat}: {len(P[cat])}")
    print(f"report: {path}")


if __name__ == "__main__":
    main()
