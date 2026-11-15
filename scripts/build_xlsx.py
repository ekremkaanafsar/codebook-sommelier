"""Build outputs/polisci_data_catalog.xlsx from data/catalog.csv and the annex files (PROTOCOL.md section 12).

Inputs: data/catalog.csv, data/progress.json, data/comparison_groups.csv, data/guide_sweep.csv,
data/literature_databases.csv, data/primary_archives.csv, CHANGELOG.md.
Optional sidecars in data/citations/ add the Citations sheet (openalex_works.csv) and the Co_usage sheet
(cousage_nodes.csv + cousage_edges.csv); each is skipped with a printed notice when its file is missing.
Usage: python scripts/build_xlsx.py
"""
import csv
import datetime as dt
import json
import math
import re

from openpyxl import Workbook
from openpyxl.formatting.rule import ColorScaleRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.workbook.protection import WorkbookProtection
from openpyxl.worksheet.protection import SheetProtection

from author import AUTHOR, cite_as, copyright_line, doi_url, orcid_url, sheet_password, version_label
from citations_common import COUNT_PREFIX_RE
from site_common import ID_SYSTEMS
from schema import (ANNEX_COLUMNS, ANNEX_FILES, CATALOG, CITATIONS, CODE_NAME, CODE_NUM, CODES, COLUMNS,
                    DATA, OUTPUTS, Q_DIMS, ROOT)

TODAY = dt.date.today().isoformat()
HEADER_FILL = PatternFill("solid", fgColor="1F3864")
HEADER_FONT = Font(bold=True, color="FFFFFF")
LINK_FONT = Font(color="0563C1", underline="single")
WRAP = Alignment(wrap_text=True, vertical="top")

SCHEMA_DOC = {
    "id": "Stable identifier <CODE>-<3-digit>; never reused or renumbered",
    "name": "Full dataset name", "acronym": "Common acronym",
    "version": "Latest version identified", "release_date": "Release date of that version (YYYY-MM or YYYY)",
    "maintainer": "Principal investigators and host institution",
    "primary_domain": "Primary domain code (taxonomy in README)",
    "secondary_domains": "Secondary domain codes, ;-separated",
    "resource_type": "1 research dataset; 2 portal/aggregator; 3 commercial/licensed product",
    "unit_of_analysis": "Unit of observation", "spatial_coverage": "Countries/regions covered",
    "temporal_coverage": "Start-end years", "temporal_resolution": "Annual, monthly, event, ...",
    "size": "Number of observations / variables", "key_variables": "5-10 key variables",
    "generation_method": "How the data were produced",
    "measurement_approach": "How the concept is measured/aggregated",
    "landing_url": "Official landing page", "download_url": "Download page, or 'registration required' / 'on request'",
    "codebook_url": "Codebook or methodology document", "formats": "File formats",
    "access_level": "open / registration / restricted / licensed / commercial", "license": "Licence terms",
    "programmatic_access": "APIs and R/Python packages", "citation": "Canonical reference (APSA style)",
    "doi": "DOI of the reference article or dataset", "citation_count": "Count of the reference article with source and check date: N (Google Scholar, checked YYYY-MM) or N (OpenAlex, checked YYYY-MM); OpenAlex counts are a lower bound",
    "notable_uses": "2-3 studies using the data", "update_frequency": "Release cadence",
    "last_update": "Most recent update", "status": "active / irregular / frozen / discontinued / superseded by <id>",
    "merge_keys": "Identifier systems usable for merging", "related_datasets": "IDs of competitors/successors",
    "q_validity": "Rubric score 1-5", "q_reliability": "Rubric score 1-5", "q_transparency": "Rubric score 1-5",
    "q_coverage": "Rubric score 1-5", "q_comparability": "Rubric score 1-5", "q_maintenance": "Rubric score 1-5",
    "q_accessibility": "Rubric score 1-5", "q_adoption": "Rubric score 1-5",
    "q_overall": "Unweighted mean of the eight scores (1 decimal)",
    "quality_justification": "Reasons for the scores", "strengths": "Main strengths",
    "limitations": "Limitations and published critiques (author year)", "best_used_for": "Recommended uses",
    "avoid_for": "Uses to avoid", "found_via": "Guides, meta-lists and repositories listing it",
    "guide_count": "Distinct university library guides listing it",
    "institutional_access_note": "Free vs. via university subscription",
    "links_checked": "Date of last automated link check", "link_status": "ok / redirect / blocked-by-site / broken",
    "confidence": "High / Medium / Low confidence in the record", "unverified_notes": "Open questions, conflicts between sources",
    "added_on": "Date row was added", "last_verified_on": "Date row was last verified against sources",
}
RUBRIC = [
    ("Validity", "Concept undefined or indicator weakly tied to it", "Defined concept, some known mismatch",
     "Explicit concept, validated against alternatives"),
    ("Reliability", "No reliability information", "Some coder/sampling detail",
     "Intercoder reliability or measurement uncertainty reported; replicable"),
    ("Transparency", "No codebook", "Codebook, partial sources", "Full codebook, sources, coding rules, revision history"),
    ("Coverage", "Narrow, many gaps", "Moderate breadth, documented gaps", "Broad space and time, missingness documented"),
    ("Comparability", "Undocumented series breaks", "Mostly consistent", "Consistent across units and time; breaks documented"),
    ("Maintenance", "Abandoned, no archive", "Irregular updates", "Regular versioned releases"),
    ("Accessibility", "Hard to obtain", "Registration or awkward formats", "Open, standard formats, API or package"),
    ("Adoption", "Rarely used", "Used in field journals", "Widely used in top journals"),
]
URL_COLS = {"landing_url", "download_url", "codebook_url", "url", "guide_url"}


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


def write_table(ws, columns, rows, widths=None, freeze=True, autofilter=True):
    ws.append(columns)
    for cell in ws[1]:
        cell.fill, cell.font, cell.alignment = HEADER_FILL, HEADER_FONT, WRAP
    for r in rows:
        vals = []
        for c in columns:
            v = r.get(c, "")
            if c in Q_DIMS or c in ("q_overall", "guide_count", "resource_type"):
                n = num(v)
                v = n if n is not None else v
                if isinstance(v, float) and v.is_integer() and c != "q_overall":
                    v = int(v)
            vals.append(v)
        ws.append(vals)
    for i, c in enumerate(columns, 1):
        letter = get_column_letter(i)
        width = (widths or {}).get(c)
        if width is None:
            longest = max([len(str(c))] + [len(str(r.get(c, ""))) for r in rows[:200]])
            width = min(max(10, longest * 0.9), 60)
        ws.column_dimensions[letter].width = width
        is_url = c in URL_COLS
        for row in ws.iter_rows(min_row=2, min_col=i, max_col=i):
            cell = row[0]
            cell.alignment = WRAP
            if is_url and isinstance(cell.value, str):
                m = re.search(r"https?://[^\s;,]+", cell.value)
                if m:
                    cell.hyperlink = m.group(0).rstrip(").,'\"")
                    cell.font = LINK_FONT
    if freeze:
        ws.freeze_panes = "B2"
    if autofilter and rows:
        ws.auto_filter.ref = f"A1:{get_column_letter(len(columns))}{len(rows) + 1}"


def add_color_scale(ws, columns, nrows):
    if "q_overall" in columns and nrows:
        col = get_column_letter(columns.index("q_overall") + 1)
        ws.conditional_formatting.add(
            f"{col}2:{col}{nrows + 1}",
            ColorScaleRule(start_type="num", start_value=1, start_color="F8696B",
                           mid_type="num", mid_value=3, mid_color="FFEB84",
                           end_type="num", end_value=5, end_color="63BE7B"))


def catalog_widths():
    w = {c: 18 for c in COLUMNS}
    w.update({"id": 10, "name": 40, "acronym": 12, "resource_type": 8, "primary_domain": 9,
              "key_variables": 45, "citation": 55, "quality_justification": 55, "strengths": 45,
              "limitations": 55, "unverified_notes": 45, "found_via": 45, "landing_url": 40,
              "download_url": 35, "codebook_url": 35, **{q: 7 for q in Q_DIMS}, "q_overall": 8})
    return w


def _numify(v):
    """Numeric strings become numbers (nan/inf and empty stay as given); ints stay ints."""
    n = num(v)
    if n is None or math.isnan(n) or math.isinf(n):
        return v
    return int(n) if n.is_integer() and "." not in str(v) and "e" not in str(v).lower() else n


def year_columns(works, today):
    """The 10 calendar years ending with today's year, ascending (works is accepted for a stable
    interface; the window does not depend on it)."""
    end = int(str(today)[:4])
    return list(range(end - 9, end + 1))


def count_source(value):
    """'Google Scholar' / 'OpenAlex' label of the first labelled count in citation_count, else ''."""
    m = COUNT_PREFIX_RE.match((value or "").strip())
    return m.group(1) if m else ""


def citations_sheet_rows(rows, works, years):
    """One row per catalog row that has an openalex_works entry (rows without one are omitted).
    Per-year cells are "" for years absent from counts_by_year; years outside `years` are ignored."""
    wby = {w["id"]: w for w in works}
    out = []
    for r in rows:
        w = wby.get(r["id"])
        if not w:
            continue
        try:
            by_year = {int(c["year"]): c["count"] for c in json.loads(w.get("counts_by_year") or "[]")}
        except (ValueError, KeyError, TypeError):
            by_year = {}
        rec = {"id": r["id"], "name": r["name"], "domain": r["primary_domain"],
               "citation_count": r["citation_count"], "source": count_source(r["citation_count"]),
               "openalex_id": w.get("openalex_id", ""), "work_year": _numify(w.get("work_year", "")),
               "cited_by_count": _numify(w.get("cited_by_count", "")),
               "match_method": w.get("match_method", ""), "status": w.get("status", "")}
        for y in years:
            rec[y] = by_year.get(y, "")
        out.append(rec)
    return out


def cousage_sheet_rows(nodes, edges, by_id, max_edges=500):
    """(node rows with top5 expanded to names, the max_edges strongest edges by cosine with names)."""
    def name(i):
        return by_id.get(i, {}).get("name", i)
    node_rows = []
    for n in nodes:
        top = [x.strip() for x in (n.get("top5") or "").split(";") if x.strip()]
        node_rows.append({**{k: _numify(v) for k, v in n.items()}, "top5_names": "; ".join(name(i) for i in top)})
    ranked = sorted(edges, key=lambda e: -(num(e.get("cosine")) if num(e.get("cosine")) is not None else -1))
    edge_rows = []
    for e in ranked[:max_edges]:
        edge_rows.append({**{k: _numify(v) for k, v in e.items()}, "a_name": name(e["a"]), "b_name": name(e["b"])})
    return node_rows, edge_rows


def add_citations_sheets(wb, rows):
    works_path, nodes_path, edges_path = (CITATIONS / "openalex_works.csv", CITATIONS / "cousage_nodes.csv",
                                          CITATIONS / "cousage_edges.csv")
    if works_path.exists():
        works = read_csv(works_path)
        years = year_columns(works, TODAY)
        crow = citations_sheet_rows(rows, works, years)
        cols = ["id", "name", "domain", "citation_count", "source", "openalex_id", "work_year",
                "cited_by_count", "match_method", "status"] + years
        ws = wb.create_sheet("Citations")
        write_table(ws, cols, crow, {"id": 10, "name": 40, "domain": 9, "citation_count": 28, "source": 14,
                                     "openalex_id": 14, **{y: 7 for y in years}})
        oa = cols.index("openalex_id") + 1
        for row in ws.iter_rows(min_row=2, min_col=oa, max_col=oa):
            cell = row[0]
            if cell.value:
                cell.hyperlink, cell.font = f"https://openalex.org/{cell.value}", LINK_FONT
    else:
        print("Citations: skipped (no sidecar)")
    if nodes_path.exists() and edges_path.exists():
        by_id = {r["id"]: r for r in rows}
        nrows, erows = cousage_sheet_rows(read_csv(nodes_path), read_csv(edges_path), by_id)
        ncols = ["id", "name", "primary_domain", "n_citing", "n_citing_polisci", "degree", "weighted_degree",
                 "eigenvector", "betweenness", "community", "top5", "top5_names", "shares_reference_with"]
        ecols = ["a", "a_name", "b", "b_name", "co_count", "cosine", "jaccard", "co_count_polisci",
                 "cosine_polisci"]
        ws = wb.create_sheet("Co_usage")
        write_table(ws, ncols, nrows, {"name": 40, "top5": 40, "top5_names": 60, "shares_reference_with": 30})
        ws.append([])
        ws.append([])
        ws.append(ecols)
        for cell in ws[ws.max_row]:
            cell.fill, cell.font, cell.alignment = HEADER_FILL, HEADER_FONT, WRAP
        for e in erows:
            ws.append([e.get(c, "") for c in ecols])
    else:
        print("Co_usage: skipped (no sidecar)")


def brand_and_protect(wb):
    """Author properties, a header and footer on every sheet, and sheet and structure locks."""
    p = wb.properties
    p.creator = p.lastModifiedBy = AUTHOR["name"]
    p.title = f"{AUTHOR['title']} v{AUTHOR['version']}"
    p.subject = f"{copyright_line()} {AUTHOR['cover_note']}." if AUTHOR["cover_note"] else copyright_line()
    p.description = f"{copyright_line()} {AUTHOR['rights_notice']} Cite as: {cite_as()}"
    p.keywords = "; ".join(["political science", "datasets", "data catalog", AUTHOR["name"]] + AUTHOR["emails"])
    p.category = "Data catalog"
    p.version = AUTHOR["version"]
    p.identifier = doi_url() or None
    pw = sheet_password()
    left = f"{AUTHOR['name']} · {'; '.join(AUTHOR['emails'])}"
    for ws in wb.worksheets:
        for hf in (ws.oddHeader, ws.evenHeader):
            hf.left.text, hf.right.text = left, AUTHOR["cover_note"]
            hf.left.size = hf.right.size = 8
        for hf in (ws.oddFooter, ws.evenFooter):
            hf.left.text, hf.right.text = f"{copyright_line()} {version_label()}", "Page &P of &N"
            hf.left.size = hf.right.size = 8
        # read-only for casual users; filtering, sorting, selecting and column resizing stay allowed
        ws.protection = SheetProtection(sheet=True, objects=True, scenarios=True, selectLockedCells=False,
                                        selectUnlockedCells=False, formatColumns=False, autoFilter=False,
                                        sort=False)
        ws.protection.password = pw
    wb.security = WorkbookProtection(lockStructure=True)
    wb.security.workbookPassword = pw


def main():
    rows = read_csv(CATALOG)
    progress = json.loads((DATA / "progress.json").read_text(encoding="utf-8")) \
        if (DATA / "progress.json").exists() else {"domains": {}}
    completed = [c for c in CODES if progress.get("domains", {}).get(c, {}).get("status") == "complete"]
    wb = Workbook()

    # README
    ws = wb.active
    ws.title = "README"
    lines = [
        (AUTHOR["title"], ""),
        ("Author", AUTHOR["name"]),
        ("Contact", "; ".join(AUTHOR["emails"])),
        ("ORCID", orcid_url()),
        ("DOI" + (" (all versions)" if "concept" in AUTHOR.get("doi_kind", "") else ""), doi_url()),
        ("Version", f"{AUTHOR['version']} ({AUTHOR['release_date']})"),
        ("Copyright", f"{copyright_line()} {AUTHOR['rights_notice']}"),
        ("Cite as", cite_as()),
    ]
    if AUTHOR["cover_note"]:
        lines.append(("Note", AUTHOR["cover_note"]))
    if AUTHOR.get("version_notes"):
        lines.append((f"Changes in v{AUTHOR['version']}", AUTHOR["version_notes"]))
    for pv in AUTHOR.get("previous_versions", []):
        lines.append((f"Previous version {pv['version']}", f"{pv['release_date']}, https://doi.org/{pv['doi']}"))
    lines += [
        ("", ""),
        ("Purpose", "Verified, citable catalog of political science datasets, one row per dataset, "
                    "regenerated from data/catalog.csv. Never edit this workbook by hand."),
        ("Last build", TODAY),
        ("Catalog rows", len(rows)),
        ("Completed domains", ", ".join(completed) or "none"),
    ]
    for c in CODES:
        n = sum(1 for r in rows if r["primary_domain"] == c)
        if n:
            lines.append((f"Rows in {c}", n))
    lines += [("", ""), ("Domain taxonomy", "")]
    lines += [(f"{CODE_NUM[c]:>2} {c}", CODE_NAME[c]) for c in CODES]
    lines += [("", ""), ("Schema dictionary", "")] + [(c, SCHEMA_DOC.get(c, "")) for c in COLUMNS]
    lines += [("", ""), ("Quality rubric", "1 / 3 / 5")]
    lines += [(d, f"1: {a} | 3: {b} | 5: {c}") for d, a, b, c in RUBRIC]
    lines += [("", ""), ("Value conventions", "UNVERIFIED = not confirmed on a page opened in this project; "
                                             "N/A = field does not apply.")]
    for a, b in lines:
        ws.append([a, b])
    ws["A1"].font = Font(bold=True, size=14)
    for row in ws.iter_rows(min_row=2):
        if row[0].value in ("Domain taxonomy", "Schema dictionary", "Quality rubric", "Author", "Copyright"):
            row[0].font = Font(bold=True)
        row[1].alignment = WRAP
    ws.column_dimensions["A"].width = 28
    ws.column_dimensions["B"].width = 110

    # Catalog
    ws = wb.create_sheet("Catalog")
    write_table(ws, COLUMNS, rows, catalog_widths())
    add_color_scale(ws, COLUMNS, len(rows))

    # Per-domain sheets
    for c in completed:
        drows = [r for r in rows if r["primary_domain"] == c]
        drows.sort(key=lambda r: (-(num(r["q_overall"]) or 0), -(num(r["guide_count"]) or 0), r["id"]))
        ws = wb.create_sheet(f"D{CODE_NUM[c]:02d}_{c}")
        write_table(ws, COLUMNS, drows, catalog_widths())
        add_color_scale(ws, COLUMNS, len(drows))

    # Comparisons
    ws = wb.create_sheet("Comparisons")
    groups = read_csv(DATA / "comparison_groups.csv")
    by_id = {r["id"]: r for r in rows}
    comp_cols = ["group", "concept", "id", "name", "unit_of_analysis", "temporal_coverage",
                 "spatial_coverage", "measurement_approach", "generation_method", "access_level",
                 "q_validity", "q_reliability", "q_coverage", "q_overall", "best_used_for", "how_to_choose"]
    comp_rows = []
    for g in groups:
        for i, rid in enumerate(x.strip() for x in g["ids"].split(";") if x.strip()):
            r = by_id.get(rid)
            if not r:
                continue
            comp_rows.append({**{k: r.get(k, "") for k in comp_cols}, "group": g["group"],
                              "concept": g["concept"], "how_to_choose": g["how_to_choose"] if i == 0 else ""})
    write_table(ws, comp_cols, comp_rows, {"group": 26, "concept": 30, "name": 38, "measurement_approach": 40,
                                           "generation_method": 30, "best_used_for": 40, "how_to_choose": 60})
    add_color_scale(ws, comp_cols, len(comp_rows))

    # Crosswalk
    ws = wb.create_sheet("Crosswalk")
    cw_cols = ["identifier_system", "n_datasets", "dataset_ids"]
    cw_rows = []
    for label, pat in ID_SYSTEMS:
        ids = [r["id"] for r in rows if re.search(pat, r["merge_keys"], re.I)]
        if ids:
            cw_rows.append({"identifier_system": label, "n_datasets": len(ids), "dataset_ids": "; ".join(ids)})
    write_table(ws, cw_cols, cw_rows, {"identifier_system": 26, "n_datasets": 12, "dataset_ids": 120})
    ws2 = wb.create_sheet("Crosswalk_by_dataset")
    mat_cols = ["id", "name"] + [label for label, _ in ID_SYSTEMS] + ["merge_keys"]
    mat_rows = []
    for r in rows:
        m = {"id": r["id"], "name": r["name"], "merge_keys": r["merge_keys"]}
        for label, pat in ID_SYSTEMS:
            m[label] = "x" if re.search(pat, r["merge_keys"], re.I) else ""
        mat_rows.append(m)
    write_table(ws2, mat_cols, mat_rows, {"id": 10, "name": 40, "merge_keys": 60,
                                         **{label: 11 for label, _ in ID_SYSTEMS}})

    # Annexes, guide sweep, changelog
    for title, t in (("Literature_DBs", "4"), ("Primary_Archives", "5")):
        ws = wb.create_sheet(title)
        write_table(ws, ANNEX_COLUMNS, read_csv(ANNEX_FILES[t]), {"name": 36, "description": 60, "url": 40})
    add_citations_sheets(wb, rows)
    ws = wb.create_sheet("Guide_Sweep")
    gs = read_csv(DATA / "guide_sweep.csv")
    write_table(ws, list(gs[0].keys()) if gs else ["resource_name"], gs,
                {"resource_name": 40, "guide_url": 45, "guide_institution": 24})
    ws = wb.create_sheet("Changelog")
    entries, current = [], ""
    cl = ROOT / "CHANGELOG.md"
    for line in (cl.read_text(encoding="utf-8").splitlines() if cl.exists() else []):
        if line.startswith("## "):
            current = line[3:].strip()
        elif line.strip().startswith(("-", "*")) and current:
            entries.append({"entry": current, "change": line.strip()[1:].strip()})
    write_table(ws, ["entry", "change"], entries, {"entry": 30, "change": 120})

    brand_and_protect(wb)
    OUTPUTS.mkdir(exist_ok=True)
    out = OUTPUTS / "polisci_data_catalog.xlsx"
    wb.save(out)
    print(f"wrote {out} ({len(rows)} rows; domain sheets: {completed}; comparison rows: {len(comp_rows)})")


if __name__ == "__main__":
    main()
