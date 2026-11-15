"""Build outputs/polisci_data_catalog.pdf from data/catalog.csv (PROTOCOL.md section 12).

Inputs: data/catalog.csv, data/progress.json, data/comparison_groups.csv,
data/narratives/<CODE>.json (landscape, core, choosing, gaps per completed domain),
data/narratives/_report.json (cross-domain gaps), annex files.
Usage: python scripts/build_pdf.py
"""
import csv
import datetime as dt
import json
import re
from collections import Counter
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_JUSTIFY
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (BaseDocTemplate, Frame, Image, KeepTogether, NextPageTemplate, PageBreak,
                                PageTemplate, Paragraph, Spacer, Table, TableStyle)
from reportlab.platypus.tableofcontents import TableOfContents

from author import AUTHOR, cite_as, copyright_line, doi_url, orcid_url, version_label
from schema import (ACCESS_LEVELS, CATALOG, CITATIONS, CODE_NAME, CODE_NUM, CODES, DATA, FIGURES, OUTPUTS,
                    RESOURCE_TYPES)

TODAY = dt.date.today().isoformat()
NARR = DATA / "narratives"


def register_fonts():
    candidates = [
        (Path("C:/Windows/Fonts"), ("times.ttf", "timesbd.ttf", "timesi.ttf", "timesbi.ttf")),
        (Path("/usr/share/fonts/truetype/dejavu"),
         ("DejaVuSerif.ttf", "DejaVuSerif-Bold.ttf", "DejaVuSerif-Italic.ttf", "DejaVuSerif-BoldItalic.ttf")),
    ]
    for folder, files in candidates:
        if all((folder / f).exists() for f in files):
            for name, f in zip(("Serif", "Serif-Bold", "Serif-Italic", "Serif-BoldItalic"), files):
                pdfmetrics.registerFont(TTFont(name, str(folder / f)))
            pdfmetrics.registerFontFamily("Serif", normal="Serif", bold="Serif-Bold",
                                          italic="Serif-Italic", boldItalic="Serif-BoldItalic")
            return "Serif", "Serif-Bold", "Serif-Italic"
    return "Times-Roman", "Times-Bold", "Times-Italic"


BODY, BOLD, ITALIC = register_fonts()
ss = getSampleStyleSheet()
S = {
    "body": ParagraphStyle("body", fontName=BODY, fontSize=10.5, leading=14, alignment=TA_JUSTIFY, spaceAfter=6),
    "small": ParagraphStyle("small", fontName=BODY, fontSize=7.5, leading=9),
    "smallb": ParagraphStyle("smallb", fontName=BOLD, fontSize=7.5, leading=9, textColor=colors.white),
    "h1": ParagraphStyle("h1", fontName=BOLD, fontSize=16, leading=20, spaceBefore=6, spaceAfter=10),
    "h2": ParagraphStyle("h2", fontName=BOLD, fontSize=12.5, leading=16, spaceBefore=10, spaceAfter=6),
    "h3": ParagraphStyle("h3", fontName=BOLD, fontSize=11, leading=14, spaceBefore=6, spaceAfter=3),
    "title": ParagraphStyle("title", fontName=BOLD, fontSize=26, leading=32, alignment=TA_CENTER),
    "subtitle": ParagraphStyle("subtitle", fontName=ITALIC, fontSize=13, leading=18, alignment=TA_CENTER),
    "bib": ParagraphStyle("bib", fontName=BODY, fontSize=9.5, leading=12.5, leftIndent=18,
                          firstLineIndent=-18, spaceAfter=4),
    "toc1": ParagraphStyle("toc1", fontName=BOLD, fontSize=10.5, leading=15),
    "toc2": ParagraphStyle("toc2", fontName=BODY, fontSize=9.5, leading=13, leftIndent=16),
}


class Doc(BaseDocTemplate):
    def __init__(self, path):
        super().__init__(str(path), pagesize=A4, title=f"{AUTHOR['title']} v{AUTHOR['version']}",
                         author=AUTHOR["name"], creator=AUTHOR["name"],
                         subject=(f"{copyright_line()} {AUTHOR['cover_note']}." if AUTHOR["cover_note"]
                                  else copyright_line()),
                         keywords=["political science", "datasets", "data catalog", AUTHOR["name"],
                                   ", ".join(AUTHOR["emails"]), copyright_line()],
                         leftMargin=2.2 * cm, rightMargin=2.2 * cm,
                         topMargin=2.2 * cm, bottomMargin=2 * cm)
        pw, ph = A4
        lw, lh = landscape(A4)
        self.addPageTemplates([
            PageTemplate("portrait", [Frame(2.2 * cm, 2 * cm, pw - 4.4 * cm, ph - 4.2 * cm, id="p")],
                         pagesize=A4, onPage=self.footer),
            PageTemplate("landscape", [Frame(1.5 * cm, 1.5 * cm, lw - 3 * cm, lh - 3 * cm, id="l")],
                         pagesize=landscape(A4), onPage=self.footer),
        ])

    @staticmethod
    def footer(canvas, doc):
        w, h = canvas._pagesize
        # light diagonal cover-note watermark, drawn before the page content so text stays readable
        canvas.saveState()
        canvas.setFillColor(colors.Color(0.55, 0.55, 0.55, alpha=0.13))
        canvas.setFont(BOLD, 30 if w < h else 34)
        canvas.translate(w / 2, h / 2)
        canvas.rotate(35)
        if AUTHOR["cover_note"]:
            canvas.drawCentredString(0, 0.5 * cm, AUTHOR["cover_note"])
        canvas.setFont(BODY, 20 if w < h else 24)
        canvas.drawCentredString(0, -0.9 * cm, f"{AUTHOR['name']} · v{AUTHOR['version']}")
        canvas.restoreState()
        # author footer on every page
        canvas.saveState()
        canvas.setFont(BODY, 7.5)
        canvas.setFillColor(colors.Color(0.25, 0.25, 0.25))
        page = f" · page {doc.page}" if doc.page > 1 else ""
        canvas.drawCentredString(w / 2, 1.1 * cm, f"{copyright_line()} {version_label()}{page}")
        dot = " · "
        canvas.drawCentredString(w / 2, 0.75 * cm, dot.join(([AUTHOR["cover_note"]] if AUTHOR["cover_note"] else []) + AUTHOR["emails"]))
        canvas.restoreState()

    def afterFlowable(self, f):
        if isinstance(f, Paragraph) and getattr(f, "toc_level", None) is not None:
            key = f"h{id(f)}"
            self.canv.bookmarkPage(key)
            self.notify("TOCEntry", (f.toc_level, f.getPlainText(), self.page, key))


def P(text, style="body"):
    return Paragraph(text, S[style])


def esc(t):
    return escape(str(t or ""))


def heading(text, level):
    p = Paragraph(esc(text), S["h1" if level == 0 else "h2"])
    p.toc_level = level
    return p


def link(url, label=None):
    if not url or not str(url).startswith("http"):
        return esc(url)
    # fields may carry a note after the URL ("https://... (archived copy; ...)"): link only the first URL token
    href = str(url).split()[0].rstrip(").,;'\"")
    return f'<link href="{escape(href, {chr(34): "&quot;"})}" color="blue">{esc(label or href)}</link>'


def acr(r):
    a = (r.get("acronym") or "").strip()
    return "" if a.upper() in ("", "N/A", "NONE") or a.upper().startswith("UNVERIFIED") else a


def display_name(r):
    """Name plus acronym, unless the name already contains the acronym."""
    name, a = r.get("name", ""), acr(r)
    return f"{name} ({a})" if a and a.lower() not in name.lower() else name


def read_csv(path):
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def grid(data, widths, header=True):
    t = Table(data, colWidths=widths, repeatRows=1 if header else 0)
    style = [("GRID", (0, 0), (-1, -1), 0.25, colors.grey), ("VALIGN", (0, 0), (-1, -1), "TOP"),
             ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3),
             ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F2F2F2")])]
    if header:
        style.append(("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F3864")))
    t.setStyle(TableStyle(style))
    return t


def load_citations_chapter():
    """Sidecar content for the citation chapter, or None (chapter skipped) when it is not available."""
    narr_path, nodes_path = NARR / "_citations.json", CITATIONS / "cousage_nodes.csv"
    if not narr_path.exists() or not nodes_path.exists():
        print("citation chapter skipped: data/narratives/_citations.json or data/citations/cousage_nodes.csv missing")
        return None
    narr = json.loads(narr_path.read_text(encoding="utf-8"))
    return {"method": narr.get("method", []), "findings": narr.get("findings", []),
            "caveats": narr.get("caveats", []), "nodes": read_csv(nodes_path),
            "works": read_csv(CITATIONS / "openalex_works.csv")}


def sibling_map(nodes_by_id):
    """Reverse map: catalog id folded into a node (listed in its shares_reference_with) -> that node's id."""
    return {s.strip(): nid for nid, n in nodes_by_id.items()
            for s in (n.get("shares_reference_with") or "").split(";") if s.strip()}


def alongside_sentence(rid, nodes_by_id, by_id, siblings=None):
    """'Most often used alongside: A, B, C.' from the first three resolvable top5 neighbours of a node.

    A dataset that is not a node but shares its reference work with one (see sibling_map; pass a prebuilt map
    as `siblings` when calling in a loop) uses that node's neighbours; it is never listed itself."""
    node = nodes_by_id.get(rid)
    if not node:
        siblings = sibling_map(nodes_by_id) if siblings is None else siblings
        node = nodes_by_id.get(siblings.get(rid, ""))
    if not node:
        return ""
    ids = [i.strip() for i in (node.get("top5") or "").split(";") if i.strip() in by_id and i.strip() != rid][:3]
    return f"Most often used alongside: {', '.join(display_name(by_id[i]) for i in ids)}." if ids else ""


def top_cited_by_domain(rows, works):
    """Three most-cited datasets per domain as (domain, id, name, count, source).

    Ranked only on openalex_works.csv cited_by_count for works with status ok (source always OpenAlex);
    rows without a resolved work are left out, so a domain may list fewer than three. Ties: count desc, id."""
    oa = {w["id"]: int(num(w["cited_by_count"])) for w in works
          if w.get("status") == "ok" and num(w.get("cited_by_count")) is not None}
    out = []
    for code in CODES:
        ranked = sorted((r for r in rows if r["primary_domain"] == code and r["id"] in oa),
                        key=lambda r: (-oa[r["id"]], r["id"]))
        out += [(code, r["id"], display_name(r), f"{oa[r['id']]:,}", "OpenAlex") for r in ranked[:3]]
    return out


def scaled_image(path, width, max_height=None):
    from reportlab.lib.utils import ImageReader
    w, h = ImageReader(str(path)).getSize()
    height = width * h / w
    if max_height and height > max_height:
        width, height = width * max_height / height, max_height
    return Image(str(path), width=width, height=height)


def citations_story(ch, rows, section, story):
    """Append the citation record and co-usage chapter to story."""
    story += [NextPageTemplate("portrait"), PageBreak()]
    section("Citation record and the co-usage map")
    fw = A4[0] - 4.4 * cm
    story += [P(esc(p)) for p in ch["method"]]
    for name in ("cousage_network.png", "cousage_domains.png"):
        if (FIGURES / name).exists():
            story += [Spacer(1, 4), scaled_image(FIGURES / name, fw, 0.8 * (A4[1] - 4.2 * cm)), Spacer(1, 4)]
    story.append(P("Three most-cited datasets per domain", "h3"))
    story.append(P("Counts are OpenAlex cited-by counts of each dataset's reference work and are lower bounds. "
                   "Datasets without an identifiable reference work in OpenAlex are not ranked, so a domain "
                   "may show fewer than three entries."))
    data = [[P(h, "smallb") for h in ("ID", "Name", "Domain", "Count", "Source")]]
    data += [[P(esc(c), "small") for c in (i, n, d, cnt, src)] for d, i, n, cnt, src in
             top_cited_by_domain(rows, ch["works"])]
    story.append(grid(data, [2.3 * cm, 7.2 * cm, 1.8 * cm, 2 * cm, 3 * cm]))
    story.append(P("Ten bridge datasets by betweenness", "h3"))
    top = sorted(ch["nodes"], key=lambda n: (-(num(n.get("betweenness")) or 0), n["id"]))[:10]
    data = [[P(h, "smallb") for h in ("ID", "Name", "Domain", "Betweenness", "Community")]]
    data += [[P(esc(n["id"]), "small"),
              P(esc(n["name"]) + (f" <font size=6>(shares reference with: {esc(n['shares_reference_with'].replace(';', ', '))})</font>"
                                  if n.get("shares_reference_with") else ""), "small"), P(esc(n["primary_domain"]), "small"),
              P(f"{num(n.get('betweenness')) or 0:.4f}", "small"), P(esc(n.get("community") or ""), "small")]
             for n in top]
    story.append(grid(data, [2.3 * cm, 7.2 * cm, 1.8 * cm, 2.5 * cm, 2.5 * cm]))
    story.append(Spacer(1, 6))
    story += [P(esc(p)) for p in ch["findings"]] + [P(esc(p)) for p in ch["caveats"]]


def count_table(counter, label, order=None):
    keys = order or sorted(counter)
    data = [[P(label, "smallb"), P("Rows", "smallb")]]
    data += [[P(esc(k), "small"), P(str(counter[k]), "small")] for k in keys if counter.get(k)]
    return grid(data, [9 * cm, 2.5 * cm])


def main():
    rows = [r for r in read_csv(CATALOG) if r.get("status", "") != "removed"]
    progress = json.loads((DATA / "progress.json").read_text(encoding="utf-8")) \
        if (DATA / "progress.json").exists() else {"domains": {}}
    completed = [c for c in CODES if progress.get("domains", {}).get(c, {}).get("status") == "complete"]
    version = progress.get("catalog_version", "0.1")
    groups = read_csv(DATA / "comparison_groups.csv")
    by_id = {r["id"]: r for r in rows}
    story = []

    # a. Title page
    story += [Spacer(1, 5 * cm), P(esc(AUTHOR["title"]), "title"), Spacer(1, 0.6 * cm),
              P(esc(AUTHOR["subtitle"]), "subtitle"),
              Spacer(1, 1.8 * cm), P(f"<b>{esc(AUTHOR['name'])}</b>", "subtitle"),
              P(esc(" · ".join(AUTHOR["emails"])), "subtitle")]
    if orcid_url():
        story.append(P(f"ORCID: {link(orcid_url())}", "subtitle"))
    if doi_url():
        kind = " (all versions)" if "concept" in AUTHOR.get("doi_kind", "") else ""
        story.append(P(f"DOI{kind}: {link(doi_url())}", "subtitle"))
    story += [Spacer(1, 0.8 * cm),
              P(f"Version {esc(AUTHOR['version'])} &#183; {esc(AUTHOR['release_date'])} &#183; build {TODAY}", "subtitle"),
              P(f"Domains completed: {len(completed)} of {len(CODES)}", "subtitle"),
              ] + ([P(esc(AUTHOR["cover_note"]), "subtitle")] if AUTHOR["cover_note"] else []) + [
              Spacer(1, 2.2 * cm),
              P(f"<b>{esc(copyright_line())}</b> {esc(AUTHOR['rights_notice'])}", "small"),
              Spacer(1, 0.2 * cm), P(f"<b>Cite as:</b> {esc(cite_as())}", "small")]
    if AUTHOR.get("version_notes"):
        story += [Spacer(1, 0.2 * cm),
                  P(f"<b>Changes in version {esc(AUTHOR['version'])}:</b> {esc(AUTHOR['version_notes'])}", "small")]
    for pv in AUTHOR.get("previous_versions", []):
        story.append(P(f"Previous version {esc(pv['version'])} ({esc(pv['release_date'])}): "
                       f"{link('https://doi.org/' + pv['doi'])}", "small"))
    story.append(PageBreak())
    toc = TableOfContents()
    toc.levelStyles = [S["toc1"], S["toc2"]]
    story += [P("Contents", "h1"), toc, PageBreak()]

    sec = 0

    def section(title):
        nonlocal sec
        sec += 1
        story.append(heading(f"{sec}. {title}", 0))
        return sec

    # b. Executive summary
    section("Executive summary")
    story.append(P(
        f"The catalog currently holds {len(rows)} research datasets, data portals and licensed data products "
        f"across {len(completed)} completed domain(s). Every row records where the resource is hosted, how it "
        "is cited, how it was produced, how it can be merged with other data, and a quality assessment on "
        "eight rubric dimensions. Values that could not be confirmed on an official page opened during "
        "compilation are marked UNVERIFIED rather than guessed."))
    story.append(P("Rows by primary domain", "h3"))
    story.append(count_table(Counter(f"{r['primary_domain']} - {CODE_NAME.get(r['primary_domain'], '')}"
                                     for r in rows), "Domain"))
    story.append(P("Rows by access level", "h3"))
    story.append(count_table(Counter(r["access_level"].split()[0].strip(";,") if r["access_level"] else "unknown"
                                     for r in rows), "Access level"))
    story.append(P("Rows by status", "h3"))
    story.append(count_table(Counter(re.sub(r"superseded by.*", "superseded", r["status"] or "unknown")
                                     for r in rows), "Status"))
    story.append(P("Rows by resource type", "h3"))
    story.append(count_table(Counter(f"{r['resource_type']} - {RESOURCE_TYPES.get(r['resource_type'], '')}"
                                     for r in rows), "Resource type"))
    story.append(PageBreak())

    # c. Methodology
    section("Methodology")
    for title, text in [
        ("Discovery", "Candidates were harvested in five tiers: (A) university library research guides "
         "(LibGuides and equivalents) at leading political science departments in North America and Europe; "
         "(B) curated meta-lists and data hubs such as PolData and R/Python data packages; (C) repositories "
         "including Harvard Dataverse, ICPSR, GESIS and Zenodo; (D) data-feature articles and replication "
         "archives in political science journals over the past decade; and (E) official producers such as "
         "the World Bank, International IDEA and the UN system. Each query and source is logged in "
         "logs/search_log.md, and each guide listing is recorded in data/guide_sweep.csv."),
        ("Inclusion criteria", "Included are systematically collected and documented data (a codebook or "
         "methods paper) that are used or usable in peer-reviewed political science research, whether open, "
         "registration-only, restricted or commercial. One-off replication files without general reuse value "
         "and undocumented scrapes are excluded. Literature databases and primary-source archives are listed "
         "in annexes rather than the main table."),
        ("Verification", "Each record was verified by opening the official landing page, download page and "
         "codebook found through search, never from memory. Coverage, version, citation and DOI were "
         "recorded only when seen on such a page; anything else is marked UNVERIFIED with a note. When "
         "landing page and codebook disagree, both values are reported."),
        ("Link checking", "All landing, download and codebook URLs are requested with a browser User-Agent, "
         "redirects followed, a 20-second timeout and one retry. Responses of 401, 403 or 429 are classed "
         "as blocked-by-site (requiring manual verification) rather than broken."),
        ("Quality rubric", "Each dataset is scored 1-5 on validity, reliability, transparency, coverage, "
         "comparability, maintenance, accessibility and adoption; q_overall is the unweighted mean. Scores "
         "describe fitness for research, not agreement with substantive conclusions, and the same anchors "
         "are applied in every domain."),
    ]:
        story += [P(title, "h3"), P(text)]
    rub = [["Dimension", "1", "3", "5"],
           ["Validity", "Concept undefined or weakly tied", "Defined concept, some mismatch", "Explicit, validated against alternatives"],
           ["Reliability", "No reliability information", "Some coder/sampling detail", "Intercoder reliability or uncertainty reported"],
           ["Transparency", "No codebook", "Codebook, partial sources", "Full codebook, sources, rules, revisions"],
           ["Coverage", "Narrow, many gaps", "Moderate, documented gaps", "Broad, missingness documented"],
           ["Comparability", "Undocumented breaks", "Mostly consistent", "Consistent; breaks documented"],
           ["Maintenance", "Abandoned", "Irregular updates", "Regular versioned releases"],
           ["Accessibility", "Hard to obtain", "Registration or awkward formats", "Open, standard formats, API/package"],
           ["Adoption", "Rarely used", "Field journals", "Widely used in top journals"]]
    data = [[P(esc(c), "smallb") for c in rub[0]]] + [[P(esc(c), "small") for c in r] for r in rub[1:]]
    story += [Spacer(1, 4), grid(data, [2.6 * cm, 4.3 * cm, 4.3 * cm, 5.4 * cm])]

    # c2. Citation record and co-usage map
    chapter = load_citations_chapter()
    nodes_by_id = {n["id"]: n for n in chapter["nodes"]} if chapter else {}
    siblings = sibling_map(nodes_by_id)
    if chapter:
        citations_story(chapter, rows, section, story)

    # d. Domain chapters
    for code in completed:
        narr_path = NARR / f"{code}.json"
        narr = json.loads(narr_path.read_text(encoding="utf-8")) if narr_path.exists() else {}
        drows = sorted([r for r in rows if r["primary_domain"] == code],
                       key=lambda r: (-(num(r["q_overall"]) or 0), r["name"].lower()))
        story += [NextPageTemplate("portrait"), PageBreak()]
        s = section(f"{CODE_NAME[code]} ({code})")
        story.append(heading(f"{s}.1 The data landscape", 1))
        story += [P(esc(p)) for p in narr.get("landscape", [])]
        story.append(heading(f"{s}.2 Core canon", 1))
        for item in narr.get("core", []):
            r = by_id.get(item["id"], {})
            title = display_name(r) if r else item["id"]
            along = alongside_sentence(item["id"], nodes_by_id, by_id, siblings)
            story += [P(esc(title), "h3"), P(esc(item["text"]) + (
                f" Landing page: {link(r.get('landing_url'))}." if r.get("landing_url", "").startswith("http") else "")
                + (f" {esc(along)}" if along else ""))]
        story.append(heading(f"{s}.3 Competing measures and how to choose", 1))
        story += [P(esc(p)) for p in narr.get("choosing", [])]
        for g in [g for g in groups if g.get("domain") == code]:
            ids = [i.strip() for i in g["ids"].split(";") if i.strip() in by_id]
            story.append(P(f"<b>{esc(g['group'])}</b> ({esc(g['concept'])}): {esc(g['how_to_choose'])} "
                           f"Datasets: {esc(', '.join(acr(by_id[i]) or by_id[i]['name'] for i in ids))}."))
        story += [NextPageTemplate("landscape"), PageBreak()]
        story.append(heading(f"{s}.4 Summary table", 1))
        hdr = ["ID", "Name", "Coverage", "Unit", "Access", "Q", "Status", "Link"]
        data = [[P(h, "smallb") for h in hdr]]
        for r in drows:
            data.append([P(esc(r["id"]), "small"),
                         P(esc(display_name(r)), "small"),
                         P(esc(f"{r['temporal_coverage']}; {r['spatial_coverage']}")[:220], "small"),
                         P(esc(r["unit_of_analysis"]), "small"), P(esc(r["access_level"]), "small"),
                         P(esc(r["q_overall"]), "small"), P(esc(r["status"]), "small"),
                         P(link(r["landing_url"], "landing page") if r["landing_url"].startswith("http")
                           else esc(r["landing_url"]), "small")])
        story.append(grid(data, [1.7 * cm, 6.2 * cm, 6.4 * cm, 3.4 * cm, 2.4 * cm, 1 * cm, 2.4 * cm, 2.9 * cm]))
        story += [NextPageTemplate("portrait"), PageBreak()]

    if not completed:
        story += [NextPageTemplate("portrait"), PageBreak()]

    # e. Merge & crosswalk guide
    section("Merge and crosswalk guide")
    story.append(P(
        "Country-level political data use several incompatible identifier systems. The Correlates of War "
        "(COW) codes and the Gleditsch-Ward (GW) list differ in how they treat state births, deaths and "
        "unifications (for example Germany, Yemen, Serbia/Yugoslavia and Vietnam); ISO 3166 codes follow "
        "current states and lack historical entities; V-Dem uses its own country_id that covers historical "
        "polities. Merge on a single system, convert with a documented crosswalk (the R package countrycode "
        "or the peacesciencer state-system panels), and check unmatched cases by hand rather than dropping "
        "them silently. Always merge on the country code and year pair, and verify that the panel you merge "
        "into defines independence dates the same way as the source."))
    systems = [("COW", r"\bcow\b|correlates of war|\bccode\b"), ("Gleditsch-Ward", r"gleditsch|\bgw"),
               ("ISO3", r"iso.?3|alpha-3|iso3c"), ("V-Dem country_id", r"v-?dem|country_id"),
               ("Polity scode", r"polity|scode"), ("World Bank code", r"world bank|wbcode")]
    data = [[P("Identifier system", "smallb"), P("Datasets using it", "smallb")]]
    for label, pat in systems:
        ids = [acr(r) or r["name"] for r in rows if re.search(pat, r["merge_keys"], re.I)]
        if ids:
            data.append([P(label, "small"), P(esc(", ".join(ids)), "small")])
    story.append(grid(data, [3.5 * cm, 13 * cm]))

    # f. Gaps
    section("Gaps and open problems")
    report = json.loads((NARR / "_report.json").read_text(encoding="utf-8")) if (NARR / "_report.json").exists() else {}
    for code in completed:
        narr_path = NARR / f"{code}.json"
        narr = json.loads(narr_path.read_text(encoding="utf-8")) if narr_path.exists() else {}
        if narr.get("gaps"):
            story.append(P(f"{CODE_NAME[code]} ({code})", "h3"))
            story += [P(esc(p)) for p in narr["gaps"]]
    story += [P(esc(p)) for p in report.get("gaps", [])]

    # g. Legacy / discontinued
    section("Legacy and discontinued datasets")
    legacy = [r for r in rows if re.match(r"(frozen|discontinued|superseded)", r["status"] or "", re.I)]
    if legacy:
        story.append(P("These resources are no longer updated but remain widely cited; use them for replication "
                       "and historical comparison, and prefer the named successor for new work."))
        data = [[P(h, "smallb") for h in ("ID", "Name", "Status", "Coverage", "Notes")]]
        for r in legacy:
            data.append([P(esc(r["id"]), "small"), P(esc(r["name"]), "small"), P(esc(r["status"]), "small"),
                         P(esc(r["temporal_coverage"]), "small"), P(esc(r["best_used_for"])[:300], "small")])
        story.append(grid(data, [1.7 * cm, 4.5 * cm, 2.8 * cm, 2.4 * cm, 5.2 * cm]))
    else:
        story.append(P("No discontinued datasets in the completed domains."))

    # h. Bibliography
    story.append(PageBreak())
    section("Bibliography")
    cites = sorted({r["citation"].strip() for r in rows
                    if r["citation"].strip() and not r["citation"].upper().startswith("UNVERIFIED")},
                   key=lambda c: re.sub(r"[^a-z]", "", c.lower()))
    story += [P(esc(c), "bib") for c in cites]

    OUTPUTS.mkdir(exist_ok=True)
    out = OUTPUTS / "polisci_data_catalog.pdf"
    Doc(out).multiBuild(story)
    print(f"wrote {out} ({len(rows)} rows, {len(completed)} domain chapters, {len(cites)} references)")


if __name__ == "__main__":
    main()
