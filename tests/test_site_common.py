import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import site_common as sc  # noqa: E402

AUTHOR = {"title": "Example Catalog", "citation_name": "Doe, Jane", "year": "2026", "doi": "10.1234/example"}
CFG = {"base_url": "https://example.org/cat/", "repo_url": "https://example.org/repo"}


def row(**kw):
    r = {"id": "REG-001", "name": "Sample Index", "acronym": "SI", "resource_type": "1",
         "unit_of_analysis": "country-year", "spatial_coverage": "global", "temporal_coverage": "1900-2020",
         "key_variables": "a; b; c; d; e; f", "primary_domain": "REG", "best_used_for": "Studying things.",
         "landing_url": "https://example.org/si", "download_url": "https://example.org/si.csv",
         "formats": "CSV", "access_level": "open", "license": "CC BY 4.0", "maintainer": "Example Lab",
         "citation": "Doe 2020.", "doi": "10.7910/DVN/ABC", "last_verified_on": "2026-01-01",
         "merge_keys": "ISO3", "guide_count": "1", "status": "active"}
    r.update(kw)
    return r


def test_description_full():
    assert sc.description(row()) == (
        "Sample Index (SI) is a country-year-level research dataset covering global, 1900-2020. "
        "Key variables: a, b, c, d, e.")


def test_description_skips_unverified_clauses():
    d = sc.description(row(spatial_coverage="UNVERIFIED (x)", temporal_coverage="N/A", key_variables="",
                           unit_of_analysis="UNVERIFIED", acronym=""))
    assert d == "Sample Index is a research dataset."
    assert "UNVERIFIED" not in d


def test_description_length_bounds():
    r = row(name="X", acronym="", unit_of_analysis="", spatial_coverage="", temporal_coverage="",
            key_variables="", best_used_for="")
    d = sc.jsonld(r, {}, AUTHOR, CFG, "https://example.org/p")["description"]
    assert len(d) >= 50 and "Regimes" in d
    long = sc.jsonld(row(best_used_for="w " * 6000), {}, AUTHOR, CFG, "u")["description"]
    assert len(long) <= 4900


def test_jsonld_required_keys():
    j = sc.jsonld(row(), {}, AUTHOR, CFG, "https://example.org/cat/datasets/REG-001/")
    for k in ("@context", "@type", "name", "description", "url", "includedInDataCatalog"):
        assert k in j
    assert j["@type"] == "Dataset" and j["isAccessibleForFree"] is True
    assert j["identifier"] == "https://doi.org/10.7910/dvn/abc"
    assert j["includedInDataCatalog"]["identifier"] == "https://doi.org/10.1234/example"
    assert j["distribution"][0]["encodingFormat"] == "CSV"


def test_jsonld_non_url_landing():
    j = sc.jsonld(row(landing_url="UNVERIFIED", download_url="on request"), {}, AUTHOR, CFG, "u")
    assert "sameAs" not in j and "distribution" not in j
    assert "UNVERIFIED" not in str(j)


def test_jsonld_temporal_only_with_both_years():
    assert "temporalCoverage" not in sc.jsonld(row(), {"start_year": "1900", "end_year": ""}, AUTHOR, CFG, "u")
    assert sc.jsonld(row(), {"start_year": "1900", "end_year": "2020"}, AUTHOR, CFG, "u")[
        "temporalCoverage"] == "1900/2020"


def test_jsonld_license_url_mapping():
    j = sc.jsonld(row(license="CC-BY-4.0"), {}, AUTHOR, CFG, "u")
    assert j["license"] == "https://creativecommons.org/licenses/by/4.0/"
    assert sc.jsonld(row(license="https://example.org/lic"), {}, AUTHOR, CFG, "u")["license"] == "https://example.org/lic"
    assert "license" not in sc.jsonld(row(license="Custom terms"), {}, AUTHOR, CFG, "u")
    assert "license" not in sc.jsonld(row(license="UNVERIFIED"), {}, AUTHOR, CFG, "u")


def test_slug_collision_suffix():
    assert sc.unique_slugs(["A B", "a-b", "C"]) == ["a-b", "a-b-2", "c"]


def test_merge_partners_most_specific_system():
    rows = [row(id="A-001", merge_keys="ISO3; COW", guide_count="1"),
            row(id="A-002", merge_keys="ISO3", guide_count="5"),
            row(id="A-003", merge_keys="ISO3", guide_count="2"),
            row(id="A-004", merge_keys="COW", guide_count="0"),
            row(id="A-005", merge_keys="ISO3; COW", guide_count="0", status="removed")]
    label, partners = sc.merge_partners(rows[0], rows)
    assert label == "COW country code"
    assert [p["id"] for p in partners] == ["A-004"]
    assert sc.merge_partners(row(merge_keys="UNVERIFIED"), rows) == ("", [])
    label, partners = sc.merge_partners(rows[1], rows, limit=1)
    assert label == "ISO 3166 alpha-3" and [p["id"] for p in partners] == ["A-003"]


def test_sparkline_missing_years_zero():
    svg = sc.sparkline_svg([{"year": 2022, "count": 4}], [2021, 2022, 2023])
    assert 'width="120"' in svg and 'height="28"' in svg and "<title>" in svg
    assert ": 0, 4, 0" in svg
    pts = re.search(r'points="([^"]+)"', svg).group(1).split()
    assert len(pts) == 3 and pts[0].endswith(",25.0") and pts[1].endswith(",3.0")


def test_cite_entry_format():
    assert sc.cite_entry(row(), AUTHOR, CFG) == (
        "Doe, Jane. 2026. Example Catalog. Entry REG-001 (Sample Index). "
        "Zenodo. https://doi.org/10.1234/example. https://example.org/cat/datasets/REG-001/")


def test_display():
    assert sc.display("UNVERIFIED (x)") == "" and sc.display("N/A here") == "n/a" and sc.display("ok") == "ok"


# Real strings quoted from the catalog.
def test_scrub_embedded_notes():
    assert sc.scrub("Global (independent countries); exact country count UNVERIFIED") == "Global (independent countries)"
    assert sc.scrub("1950-2024 (QoG v6.1); later versions UNVERIFIED") == "1950-2024 (QoG v6.1)"
    assert sc.scrub("2006-2025 (UNVERIFIED: early editions 2006 and 2008 were not annual)") == "2006-2025"
    assert sc.scrub("FAIR-EU project consortium (producer and host UNVERIFIED)") == "FAIR-EU project consortium"
    assert sc.scrub("Daniele Caramani (author); published by Macmillan (London); affiliation UNVERIFIED") == \
        "Daniele Caramani (author); published by Macmillan (London)"
    assert sc.scrub("Norwegian Centre for Research Data (NSD). European Election Database. Bergen: NSD. "
                    "(Canonical citation form UNVERIFIED.)") == \
        "Norwegian Centre for Research Data (NSD). European Election Database. Bergen: NSD."
    assert sc.scrub("UNVERIFIED (could not open page)") == ""
    assert sc.scrub("plain") == "plain"


def test_no_unverified_in_description_or_jsonld():
    r = row(spatial_coverage="Global (independent countries); exact country count UNVERIFIED",
            temporal_coverage="1950-2024 (QoG v6.1); later versions UNVERIFIED",
            maintainer="Daniele Caramani (author); affiliation UNVERIFIED",
            citation="Norwegian Centre. Bergen: NSD. (Canonical citation form UNVERIFIED.)",
            unit_of_analysis="precinct-election (UNVERIFIED)")
    assert "UNVERIFIED" not in sc.description(r)
    j = sc.jsonld(r, {}, AUTHOR, CFG, "u")
    assert "UNVERIFIED" not in str(j)
    assert j["creator"]["name"] == "Daniele Caramani (author)"
    assert "a precinct-election-level research dataset covering Global, 1950-2024." in sc.description(r)


def test_display_html():
    h = sc.display_html("UNVERIFIED")
    assert "UNVERIFIED" not in h and 'class="unv"' in h and "not confirmed" in h
    h2 = sc.display_html("Global; exact count UNVERIFIED <b>")
    assert "UNVERIFIED" not in h2 and "&lt;b&gt;" in h2 and 'class="unv"' in h2
    assert sc.display_html("N/A") == "n/a" and sc.display_html("ok") == "ok"
    assert sc.display_html(" n/a ") == "n/a"


def test_display_html_na_keeps_detail():
    assert sc.display_html("N/A (portal offline)") == "n/a (portal offline)"
    assert sc.display_html("N/A here") == "n/a here"
    h = sc.display_html("N/A (see <b> UNVERIFIED)")
    assert h.startswith("n/a (see &lt;b&gt; ") and 'class="unv"' in h and "UNVERIFIED" not in h


def test_identifier_data_doi_only():
    f = "10.23696/vdemds26 (dataset v16); 10.1017/S1537592711000880 (reference article)"
    assert sc.jsonld(row(doi=f), {}, AUTHOR, CFG, "u")["identifier"] == "https://doi.org/10.23696/vdemds26"
    assert "identifier" not in sc.jsonld(row(doi="10.1017/S1537592711000880 (reference article)"), {}, AUTHOR, CFG, "u")
    assert "identifier" not in sc.jsonld(row(doi="N/A"), {}, AUTHOR, CFG, "u")
    assert "identifier" not in sc.jsonld(row(doi="UNVERIFIED"), {}, AUTHOR, CFG, "u")


def test_description_no_duplicate_acronym_and_clean_clauses():
    d = sc.description(row(name="V-Dem (Varieties of Democracy)", acronym="V-Dem",
                           unit_of_analysis="constituency-candidate (UK Parliament general elections); other (UNVERIFIED)"))
    assert d.startswith("V-Dem (Varieties of Democracy) is a constituency-candidate-level research dataset")
    assert "(V-Dem)" not in d


def test_license_leading_token():
    for lic, tail in [("CC0 1.0 (Harvard Dataverse)", "zero/1.0/"), ("CC BY 4.0 (stated on the page)", "by/4.0/"),
                      ("CC BY 4.0 (data); MIT (code); LPPL 1.3c (codebook)", "by/4.0/"),
                      ("CC BY-NC-SA 4.0", "by-nc-sa/4.0/"), ("CC BY-NC-ND 4.0 (see page)", "by-nc-nd/4.0/"),
                      ("CC BY-ND 4.0", "by-nd/4.0/"), ("CC BY-NC 4.0", "by-nc/4.0/")]:
        assert sc.jsonld(row(license=lic), {}, AUTHOR, CFG, "u")["license"].endswith(tail), lic
    for lic in ["No license stated on the page", "Not stated", "Proprietary (subscription)", "CC BY 4.0 per IssueLab"]:
        assert "license" not in sc.jsonld(row(license=lic), {}, AUTHOR, CFG, "u"), lic


def test_key_variables_and_urls_scrubbed():
    r = row(key_variables="a; b; c; names UNVERIFIED; d (variable names UNVERIFIED)",
            download_url="https://example.org/f.zip (listed on the page; terms step UNVERIFIED)")
    assert sc.description(r).endswith("Key variables: a, b, c, d.")
    j = sc.jsonld(r, {}, AUTHOR, CFG, "u")
    assert j["distribution"][0]["contentUrl"] == "https://example.org/f.zip"
    assert "UNVERIFIED" not in str(j)


def test_whole_catalog_has_no_unverified_and_only_url_identifiers():
    import csv
    import json
    root = Path(__file__).resolve().parent.parent
    with open(root / "data" / "catalog.csv", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    author = json.loads((root / "data" / "author.json").read_text(encoding="utf-8"))
    cfg = {"base_url": "/", "repo_url": ""}
    for r in rows:
        j = sc.jsonld(r, {}, author, cfg, "https://example.org/p")
        assert "UNVERIFIED" not in sc.description(r).upper(), r["id"]
        assert "UNVERIFIED" not in json.dumps(j).upper(), r["id"]
        assert "identifier" not in j or j["identifier"].startswith("https://doi.org/10."), r["id"]


AMBIGUOUS_LICENCES = [  # real catalog values (PARTY-033, HIST-052, GENDER-031, IDENT-059, TEXT-051, TEXT-028, ...)
    "CC BY 4.0 (working paper on CurateND); data file license UNVERIFIED",
    "CC BY-NC-ND 4.0 (PNAS article and supplement, per Crossref); separate data licence UNVERIFIED",
    "CC BY-NC 4.0 (Creative Commons Attribution Non-Commercial 4.0, stated on the copyright page of the 2025/26 report PDF)",
    "CC BY-NC 4.0 (article; the data supplement Relac-JCRrep carries CC BY-NC 4.0 in its SAGE-registered DataCite record)",
    "CC BY 4.0 (Zenodo v1.0.0 record) vs. CC BY-NC 4.0, research use only (GitHub README): conflicting",
    "CC BY-NC 4.0 (Zenodo record and article); DataCite record for the figshare deposit lists CC BY 4.0 (see notes)",
    "CC0 1.0 (Dataverse record); the PLOS ONE article states CC BY-NC 4.0 for the data (conflict, see notes)",
    "CC BY-SA 4.0, non-commercial use only per iuropa.org/database (commercial use by arrangement with the authors)",
    "CC BY 4.0 (dataset page also says 'for non-commercial use only')",
    "CC BY 4.0 (report reuse statement; data coding sheets assumed to fall under the same EC reuse policy)",
    "CC BY 4.0 (per search results summarising SEDAC; Earthdata page says openly shared)",
    "CC BY-ND 4.0 (site-wide notice on transparency.org; terms for the data files not separately stated)",
    "CC0 1.0 (Dataverse record), but data files restricted behind an access request",
    "CC0 (Public Domain / No restrictions) for the HDX aggregate files; microdata under Meta research-partner terms",
]


def test_license_ambiguous_omitted():
    for lic in AMBIGUOUS_LICENCES:
        assert "license" not in sc.jsonld(row(license=lic), {}, AUTHOR, CFG, "u"), lic
    assert sc.jsonld(row(license="CC BY 4.0 (data); MIT (code); LPPL 1.3c (codebook)"), {}, AUTHOR, CFG, "u"
                     )["license"].endswith("by/4.0/")


KEEP_LICENCES = {  # real catalog values: first segment decides; tails about code/article/metadata do not block
    "CC0 1.0 (Harvard Dataverse record); no licence stated on the landing page": "zero/1.0/",  # PE-008
    "CC0 1.0 (Dataverse replication package); GitHub licence UNVERIFIED": "zero/1.0/",  # REG-088
    "CC0 1.0 (Dataverse release); author-site update UNVERIFIED": "zero/1.0/",  # BUR-029
    "CC BY-SA 4.0 (V-Dem dataset); reference article CC BY 4.0": "by-sa/4.0/",  # REG-003
    "CC0 1.0 (Dataverse data records); legislatoR package GPL-3": "zero/1.0/",  # LEG-032
    "CC0 1.0 (Harvard Dataverse deposit); code MIT": "zero/1.0/",  # ELEC-035
    "CC BY 4.0 (Gapminder free-material page; GitHub Systema Globalis README); attribute original sources and "
    "Gapminder": "by/4.0/",  # PE-046
    "CC BY-NC-SA 4.0 (both Dataverse datasets); reference article CC BY 4.0": "by-nc-sa/4.0/",  # LEG-088
    "CC BY 4.0 (FSD data); study description metadata CC0 1.0": "by/4.0/",  # REG-031
}
DROP_LICENCES = [  # real values: version splits (ELEC-010, GENDER-060) and competing data licences
    "CC0 1.0 (EMS 3.0 and version 1); CC BY 4.0 (EMS 2.0)",
    "CC0 1.0 (2022 release); CC BY-NC 4.0 (2025 update)",
    "CC BY 4.0 (v1); data CC BY-NC 4.0",
    "CC0 1.0 (Dataverse); dataset licensed CC BY 4.0 on the publisher page",
]


def test_license_first_segment_decides():
    for lic, tail in KEEP_LICENCES.items():
        assert sc.jsonld(row(license=lic), {}, AUTHOR, CFG, "u")["license"].endswith(tail), lic
    for lic in DROP_LICENCES:
        assert "license" not in sc.jsonld(row(license=lic), {}, AUTHOR, CFG, "u"), lic


def test_description_shorter_unit_and_spatial_clauses():
    d = sc.description(row(
        unit_of_analysis="country electoral period (pair of consecutive elections), separately for lower-chamber "
                         "and presidential elections; party-election vote shares in the accompanying Excel files",
        spatial_coverage="Latin America: country notes cover Argentina, Bolivia, Brazil, Chile, Colombia, Costa Rica, "
                         "Cuba, Dominican Republic, Ecuador (exact country count UNVERIFIED)"))
    assert "a country electoral period-level research dataset covering Latin America," in d
    d = sc.description(row(unit_of_analysis="legal document (node) and directed legal link between two documents (edge)"))
    assert "a legal document-level research dataset" in d
    d = sc.description(row(unit_of_analysis="person-position occupancy (politician holding a position with start/end "
                                            "dates); legacy: legislator-term per legislature",
                           spatial_coverage="Current: 262 countries and territories; legacy mySociety data: 233"))
    assert "a person-position occupancy-level" in d and "covering Current: 262 countries and territories," in d


def test_identifier_multiple_data_dois():
    hist = ("10.7910/DVN/TI0KAU (Roman Road Network); other layers: 10.7910/DVN/3KQFUT (ports), "
            "10.7910/DVN/ZIQPDG (shipwrecks)")
    hr = "10.3886/E100707V22 (Return A); 10.3886/E102263V16 (Arrests); 10.3886/E100699V16 (SHR)"
    for f in (hist, hr):
        assert "identifier" not in sc.jsonld(row(doi=f), {}, AUTHOR, CFG, "u")
    lab = "10.5281/zenodo.111 (v2); 10.5281/zenodo.100 (concept DOI, all versions)"
    assert sc.jsonld(row(doi=lab), {}, AUTHOR, CFG, "u")["identifier"] == "https://doi.org/10.5281/zenodo.100"


# Round 4: exact catalog values (data/catalog.csv, 2026-11-14), keyed by row id.
KEEP_EXACT = {  # clear data licence in the first segment; tails about code, articles, codebooks or metadata
    'PE-008': ('CC0 1.0 (Harvard Dataverse record); no licence stated on the landing page', 'zero/1.0/'),
    'REG-088': ('CC0 1.0 (Dataverse replication package); GitHub licence UNVERIFIED', 'zero/1.0/'),
    'BUR-029': ('CC0 1.0 (Dataverse release); author-site update UNVERIFIED', 'zero/1.0/'),
    'REG-003': ('CC BY-SA 4.0 (V-Dem dataset); reference article CC BY 4.0', 'by-sa/4.0/'),
    'LEG-032': ('CC0 1.0 (Dataverse data records); legislatoR package GPL-3', 'zero/1.0/'),
    'ELEC-035': ('CC0 1.0 (Harvard Dataverse deposit); code MIT', 'zero/1.0/'),
    'LEG-088': ('CC BY-NC-SA 4.0 (both Dataverse datasets); reference article CC BY 4.0', 'by-nc-sa/4.0/'),
    'REG-031': ('CC BY 4.0 (FSD data); study description metadata CC0 1.0', 'by/4.0/'),
    'PE-046': ('CC BY 4.0 (Gapminder free-material page; GitHub Systema Globalis README); attribute original sources and Gapminder', 'by/4.0/'),
    'EXEC-036': ('CC BY 4.0 (data); MIT (code); LPPL 1.3c (codebook)', 'by/4.0/'),
    'ELEC-043': ('CC0 1.0 (Dataverse terms ask users to cite the Party Politics article)', 'zero/1.0/'),
    'ENV-027': ('CC BY 4.0 (Figshare record and article)', 'by/4.0/'),
    'INFRA-043': ('CC0 1.0 (public domain dedication) for all structured data; other wiki text CC BY-SA 4.0', 'zero/1.0/'),
    'CONST-017': ('CC BY 4.0 (data); CC BY-SA 4.0 (codebook)', 'by/4.0/'),
}
DROP_EXACT = {  # scoped to site text / footer / annex / one file / an article, conflicting tails, or unversioned
    'LEG-062': 'CC0 1.0 (Dataverse record); readme states CC BY',
    'IDENT-106': 'CC BY 4.0 (Zenodo record metadata for v1.0 and v1.1); the v1.1 readme says the data are free to use for academic, non-commercial purposes (see notes)',
    'PARTY-051': 'CC0 1.0 (splits and mergers files); repeated-entry file has no licence stated',
    'IR-142': 'CC BY 4.0 (World Bank Data Catalog); DTA website footer says all rights reserved',
    'ELEC-068': 'CC BY-NC-SA 4.0 (site text); reuse for scientific and personal purposes with attribution',
    'GEO-020': 'CC BY 4.0 (WorldPop Hub footer); data-specific licence text linked from API metadata; usage note asks users not to use the data to discriminate against, surveil or harm people',
    'IR-140': 'CC BY-NC-ND 4.0 (technical annex); data file licence UNVERIFIED',
    'CONF-152': "CC BY (Dataverse terms field); Horn original listed as 'No License Restrictions' on NETL EDX",
    'IDENT-083': 'CC BY (HDX listing)',
    'ENV-075': 'CC BY (HDX metadata)',
    'TEXT-051': 'CC BY 4.0 (Zenodo v1.0.0 record) vs. CC BY-NC 4.0, research use only (GitHub README): conflicting',
    'TEXT-028': 'CC BY-NC 4.0 (Zenodo record and article); DataCite record for the figshare deposit lists CC BY 4.0 (see notes)',
    'ELEC-010': 'CC0 1.0 (EMS 3.0 and version 1); CC BY 4.0 (EMS 2.0)',
    'GENDER-060': 'CC0 1.0 (2022 release); CC BY-NC 4.0 (2025 update)',
    'GENDER-031': 'CC BY-NC 4.0 (Creative Commons Attribution Non-Commercial 4.0, stated on the copyright page of the 2025/26 report PDF; the landing and download page show a copyright notice only)',
    'IDENT-059': 'CC BY-NC 4.0 (article; the data supplement Relac-JCRrep carries CC BY-NC 4.0 in its SAGE-registered DataCite record 10.25384/sage.7613681)',
    'HIST-052': 'CC BY-NC-ND 4.0 (PNAS article and supplement, per Crossref); separate data licence UNVERIFIED',
    'PARTY-033': 'CC BY 4.0 (working paper on CurateND); data file license UNVERIFIED',
    'GEO-050': 'CC BY 4.0 (stated in the 2024 methodology note)',  # round-4 audit: licence of the note
}


def test_license_round4_exact_catalog_values():
    for rid, (lic, tail) in KEEP_EXACT.items():
        j = sc.jsonld(row(license=lic), {}, AUTHOR, CFG, "u")
        assert j.get("license", "").endswith(tail), rid
    for rid, lic in DROP_EXACT.items():
        assert "license" not in sc.jsonld(row(license=lic), {}, AUTHOR, CFG, "u"), rid


def test_license_unversioned_names_not_given_a_version():
    for lic in ["CC BY", "CC BY-SA", "CC BY-NC", "CC BY (HDX listing)", "CC BY-NC-SA (repository page)"]:
        assert "license" not in sc.jsonld(row(license=lic), {}, AUTHOR, CFG, "u"), lic
    for lic, tail in [("CC0", "zero/1.0/"), ("MIT", "MIT"), ("ODbL", "odbl/1-0/"), ("CC BY 4.0", "by/4.0/")]:
        assert sc.jsonld(row(license=lic), {}, AUTHOR, CFG, "u")["license"].endswith(tail), lic


def test_license_article_blocks_only_when_it_scopes_the_licence():
    for lic in ["CC BY 4.0 (article)", "CC BY 4.0 (PNAS article)", "CC BY 4.0 (article licence on the journal page)",
                "CC BY 4.0 (stated on the article)"]:
        assert "license" not in sc.jsonld(row(license=lic), {}, AUTHOR, CFG, "u"), lic
    for lic in ["CC BY 4.0 (Figshare record and article)", "CC0 1.0 (Dataverse terms ask users to cite the article)"]:
        assert "license" in sc.jsonld(row(license=lic), {}, AUTHOR, CFG, "u"), lic


def test_id_systems_declared_none():
    for mk in ("None (country names only)", "none standard (country names)", "N/A (varies; COW or GW codes)",
               "no state codes (COW-style categories differ)", "UNVERIFIED"):
        assert sc.id_systems({"merge_keys": mk}) == [], mk
    assert sc.id_systems({"merge_keys": "Nonmember ISO3"}) == ["ISO 3166 alpha-3"]
    assert sc.id_systems({"merge_keys": "country name; year"}) == ["Country name + year"]
