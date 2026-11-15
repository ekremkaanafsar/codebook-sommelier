import csv

import pytest

import derive_facets as df
from derive_facets import (derive, has_programmatic, region_tags, unit_class, write_report,
                           year_span)


@pytest.mark.parametrize("s,c", [
    ("country-year", "country-year"), ("directed dyad-year", "dyad"),
    ("PRIO-GRID cell-year", "grid-cell"), ("respondent", "respondent"),
    ("legislator-vote", "legislator-or-vote"), ("party-election", "party"),
    ("speech", "document"), ("municipality-year", "subnational"),
    ("country (cross-section)", "country"), ("treaty-dyad", "dyad"), ("spacecraft", "other")])
def test_unit_class(s, c):
    assert unit_class(s) == c


def test_region_global():
    assert region_tags("Global, all independent states") == ["global"]


def test_region_two():
    assert set(region_tags("Latin America and Sub-Saharan Africa")) == {"latin-america", "sub-saharan-africa"}


def test_region_single_country():
    assert region_tags("United States (all 50 states and DC)") == ["single-country:United States"]


def test_region_unclassified():
    assert region_tags("UNVERIFIED") == ["unclassified"]


def test_years_simple():
    assert year_span("1946-2023", 2026) == ("1946", "2023")


def test_years_present():
    assert year_span("2005-present", 2026) == ("2005", "2026")


def test_years_present_after_range():
    assert year_span("1816-2014 (v4.1); 2015-present in v5", 2026) == ("1816", "2026")


def test_years_about_present():
    assert year_span("about 2018-present (first wave not dated)", 2026) == ("2018", "2026")


def test_years_none():
    assert year_span("UNVERIFIED", 2026) == ("", "")


def test_programmatic():
    assert has_programmatic("R package vdemdata") == "1"
    assert has_programmatic("UNVERIFIED") == "0" and has_programmatic("N/A") == "0" and has_programmatic("") == "0"


def test_country_longest_match():
    assert region_tags("Nigeria") == ["single-country:Nigeria"]
    assert region_tags("Papua New Guinea") == ["single-country:Papua New Guinea"]
    assert region_tags("South Sudan") == ["single-country:South Sudan"]


def test_countries_roll_up_to_region():
    assert region_tags("Argentina, Brazil, Mexico") == ["latin-america"]


def test_derive_and_report(tmp_path):
    rows = [
        {"id": "A-1", "unit_of_analysis": "country-year", "spatial_coverage": "Global",
         "temporal_coverage": "1900-present", "programmatic_access": "R package", "status": "active"},
        {"id": "A-2", "unit_of_analysis": "spacecraft", "spatial_coverage": "Germany",
         "temporal_coverage": "UNVERIFIED", "programmatic_access": "", "status": "active"},
        {"id": "A-3", "unit_of_analysis": "event", "spatial_coverage": "???",
         "temporal_coverage": "1989-2020", "programmatic_access": "API", "status": "removed"},
        {"id": "A-4", "unit_of_analysis": "event", "spatial_coverage": "Africa",
         "temporal_coverage": "1997-2020", "programmatic_access": "API", "status": "active"},
    ]
    f = derive(rows, 2026)
    assert [x["id"] for x in f] == ["A-1", "A-2", "A-4"]
    assert f[0] == {"id": "A-1", "unit_class": "country-year", "region_tags": "global",
                    "start_year": "1900", "end_year": "2026", "has_programmatic": "1"}
    assert f[1]["region_tags"] == "single-country:Germany" and f[1]["start_year"] == ""
    p = tmp_path / "facets.md"
    write_report(f, rows, p)
    txt = p.read_text(encoding="utf-8")
    assert "Rows: 3" in txt and "A-2" in txt and "spacecraft" in txt and "A-1:" not in txt


def test_main_writes_files(tmp_path, monkeypatch):
    cat = tmp_path / "catalog.csv"
    with open(cat, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["id", "unit_of_analysis", "spatial_coverage",
                                           "temporal_coverage", "programmatic_access", "status"])
        w.writeheader()
        w.writerow({"id": "X-1", "unit_of_analysis": "respondent", "spatial_coverage": "Global",
                    "temporal_coverage": "2000-2010", "programmatic_access": "", "status": "active"})
    monkeypatch.setattr(df.schema, "CATALOG", cat)
    monkeypatch.setattr(df.schema, "DERIVED", tmp_path / "derived")
    monkeypatch.setattr(df.schema, "LOGS", tmp_path / "logs")
    df.main()
    with open(tmp_path / "derived" / "facets.csv", encoding="utf-8", newline="") as fh:
        out = list(csv.DictReader(fh))
    assert out[0]["unit_class"] == "respondent" and out[0]["has_programmatic"] == "0"
    assert list((tmp_path / "logs").glob("facets_*.md"))


@pytest.mark.parametrize("s,c", [
    ("conflict-year", "event"), ("leader spell in office", "event"),
    ("candidate-election", "election-or-candidate"), ("ballot measure", "election-or-candidate"),
    ("judge (U.S. federal circuit judge)", "person-or-actor"), ("rebel leader-year", "person-or-actor"),
    ("firm-year questionnaire response", "organisation"), ("pension plan-fiscal year", "other"),
    ("map feature / boundary polygon", "geospatial"),
    ("study (deposited datasets)", "study-or-record"),
])
def test_unit_class_round1_rules(s, c):
    assert unit_class(s) == c


def test_new_rules_do_not_change_spec_precedence():
    # spec-table classes still win over the appended tuning rules
    assert unit_class("country-year election") != "election-or-candidate"
    assert unit_class("directed dyad-year conflict") == "dyad"
    assert unit_class("respondent election study") == "respondent"


@pytest.mark.parametrize("s", ["166 countries", "UN member states that report voluntarily",
                               "WTO members", "About 70 economies"])
def test_region_multi_country_when_region_unstated(s):
    assert region_tags(s) == ["multi-country"]


def test_region_named_region_beats_multi_country():
    assert region_tags("57 OSCE participating States in Europe") == ["europe"]
    assert region_tags("40 countries across Latin America") == ["latin-america"]


def test_region_single_country_beats_multi_country():
    assert region_tags("Germany (16 states)") == ["single-country:Germany"]


@pytest.mark.parametrize("s,sp,c", [
    ("country-year (national parliament of an EU member state)", "National parliaments of EU member states", "country-year"),
    ("country-year (head of state and head of government in office on 31 December)", "183 countries", "country-year"),
    ("state-year; leader spell (two separate files)", "Global; all Correlates of War system members", "country-year"),
    ("state-year", "Global", "country-year"),
    ("state-year", "United States, all 50 states", "subnational"),
    ("state-year", "United States: 50 states and the District of Columbia", "subnational"),
    ("state-year (state legislature at each measurement point)", "50 U.S. state legislatures", "subnational"),
    ("state-year (by chamber: upper and lower)", "All 50 U.S. state legislatures", "subnational"),
    ("state-year (secondary subnational unit: state, province, Land, canton)", "12 federal democracies", "subnational"),
    ("head of state", "", "other"),
])
def test_state_is_country_unless_subnational_signal(s, sp, c):
    assert unit_class(s, sp) == c


def test_parentheses_ignored_before_fallback():
    assert unit_class("respondent (county sample)") == "respondent"
    assert unit_class("(county)") == "subnational"  # empty core falls back to full text


# ---- review round: strings quoted from the real catalog
@pytest.mark.parametrize("s", ["None found", "None (web download only)", "None official", "None public",
                               "None documented", "none (web download of files)", "No API", "Not available",
                               "N/A", "UNVERIFIED", "  none"])
def test_programmatic_none_values_are_zero(s):
    assert has_programmatic(s) == "0"


def test_programmatic_real_values_are_one():
    assert has_programmatic("R package vdemdata; API") == "1"


def test_mena_phrase_is_not_sub_saharan():
    assert region_tags("Middle East and North Africa: 16-17 Arab countries (Algeria, Bahrain, Egypt, Iraq)") == ["mena"]
    assert region_tags("Middle East and North Africa (countries listed on Dataverse: Algeria, Bahrain, Cyprus, Iran)") == ["mena"]
    assert region_tags("Northern Africa") == ["mena"]
    assert region_tags("Africa and North Africa") == ["sub-saharan-africa", "mena"]


def test_us_georgia_is_not_the_country():
    assert region_tags("11 southern states: Alabama, Arkansas, Florida, Georgia, Louisiana, Mississippi, "
                       "North Carolina, South Carolina, Tennessee, Texas, Virginia (counties)") == [
        "single-country:United States"]
    assert region_tags("Selected U.S. states: Florida, North Carolina, Ohio, Georgia, Washington, Pennsylvania, "
                       "District of Columbia, New York, Nevada") == ["single-country:United States"]
    assert region_tags("Georgia") == ["single-country:Georgia"]
    assert region_tags("Armenia, Azerbaijan and Georgia") == ["central-asia"]


def test_new_mexico_is_not_mexico():
    assert region_tags("New Mexico, Arizona") == ["single-country:United States"]


@pytest.mark.parametrize("s,span", [
    ("UNVERIFIED (article version through 2003; later update reportedly through 2005/2007)", ("", "")),
    ("2005-2024 editions in the data file (reports published since 1996; no 2019 edition)", ("2005", "2024")),
    ("2015-2021 (editions 2015, 2017, 2019, 2021)", ("2015", "2021")),
    ("2008 (laws in force as of June 2008)", ("2008", "2008")),
    ("1950-2020 (updated 2023)", ("1950", "2020")),
    ("1990-2020 with projections to 2050", ("1990", "2050")),
    ("1990-2020, next cycle 2030", ("1990", "2020")),
    ("1970s-2000s", ("1970", "2000")),
    ("late 1800s-1914", ("1800", "1914")),
])
def test_year_provenance(s, span):
    assert year_span(s, 2026) == span


def test_vote_vs_voter():
    assert unit_class("registered voter (person)") == "respondent"
    assert unit_class("vote shares by party") != "legislator-or-vote"
    assert unit_class("legislator-vote") == "legislator-or-vote"
    assert unit_class("roll call vote") == "legislator-or-vote"
    assert unit_class("voter-election (vote-history table)") == "respondent"


def test_dyad_seen_through_parentheses():
    assert unit_class("land border (country dyad, listed in both directions with identical values) "
                      "and country (monadic averages)") == "dyad"


def test_household_is_respondent():
    assert unit_class("household") == "respondent"
    assert unit_class("household survey record") == "respondent"
    assert unit_class("country-year by household type") == "country-year"


# ---- P11/P12: strings quoted from the real catalog
@pytest.mark.parametrize("s,c", [
    ("state-year (plus state-dyad network file via the R package)", "country-year"),
    ("country-year (ideal points); dyad-year (agreement scores and ideal-point distances); "
     "country-vote in the legacy roll-call file (V1-V33)", "country-year"),
    ("Portal: country, conflict, actor and dyad profiles; API: records of the underlying datasets "
     "(event, conflict-year, dyad-year, country-year)", "country"),
    ("country (contemporary non-European nation-state; wide file) and country-colonizer dyad (long file)",
     "country"),
    ("land border (country dyad, listed in both directions with identical values) and country "
     "(monadic averages)", "dyad"),
    ("directed dyad-year", "dyad"),
    ("dyad-year (agreement scores); country-year", "dyad"),
])
def test_dyad_only_when_leading(s, c):
    assert unit_class(s) == c


@pytest.mark.parametrize("s", [
    "None official; R package troopdata for the series",
    "No official API. SUNGEO R package (Zhukov et al.) serves CLEA lower-house results",
    "None (direct file downloads only)", "No API", "None (no API or package)",
])
def test_programmatic_none_values_are_zero_by_design(s):
    """The facet means official programmatic access; third-party packages do not flip a leading negation."""
    assert has_programmatic(s) == "0"



@pytest.mark.parametrize("s,c", [
    ("alliance-member; alliance-member-year; dyad; dyad-year; directed dyad; directed-dyad-year", "dyad"),
    ("claim; dyadic claim; claim dyad-year; settlement attempt", "dyad"),
    ("sanctions case (sender-target dyad; one case per sender, target and political issue)", "dyad"),
    ("country-year (Gleditsch-Ward number-year); directed dyad-year in the Dyadic version", "country-year"),
])
def test_dyad_guards_for_non_spec_or_dyad_parenthetical_leads(s, c):
    assert unit_class(s) == c


def test_state_system_membership_units_are_country_level():
    assert unit_class("state membership spell; country-year; non-directed and directed dyad-year (blank frames)") == "country"
    assert unit_class("state (membership spells with start and end dates); annual and dyadic panels") == "country"
    assert unit_class("state membership spell", "United States, all 50 states") == "subnational"


def test_years_present_in_parenthetical_ignored():
    s = "1990-2014 (re3data describes 1990 to the then present)"
    assert year_span(s, 2026) == ("1990", "2014")
    assert year_span("1990-2014 (updates continue to present)", 2026) == ("1990", "2014")
    assert year_span("1990 to the then present", 2026) == ("1990", "1990")
    assert year_span("(ongoing since 2001)", 2026) == ("2001", "2026")


def test_years_open_range_keeps_parenthetical_present():
    assert year_span("1979-UNVERIFIED (described as 1979 to present at publication)", 2026) == ("1979", "2026")
