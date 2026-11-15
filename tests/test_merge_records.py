import pytest

import merge_records as mr
from schema import COLUMNS


def make_row(**kw):
    row = {c: "" for c in COLUMNS}
    row.update(id="REG-001", name="Test Data", primary_domain="REG", resource_type="1",
               last_verified_on="2026-01-01")
    row.update(kw)
    return row


def make_rec(**kw):
    rec = {c: "" for c in COLUMNS}
    rec.update(name="Test Data", primary_domain="REG", resource_type="1",
               last_verified_on="2026-11-11")
    rec.update(kw)
    return rec


def test_partial_patch_keeps_literal_none():
    row = make_row(programmatic_access="None", update_frequency="None")
    mr.merge_into(row, make_rec(citation_count="10 (checked 2026-11)"))
    assert row["programmatic_access"] == "None"
    assert row["update_frequency"] == "None"


def test_partial_patch_keeps_normal_value():
    row = make_row(programmatic_access="R package vdemdata", license="CC BY")
    mr.merge_into(row, make_rec(citation_count="10 (checked 2026-11)"))
    assert row["programmatic_access"] == "R package vdemdata"
    assert row["license"] == "CC BY"


def test_fills_unverified_citation_count():
    row = make_row(citation_count="UNVERIFIED")
    mr.merge_into(row, make_rec(citation_count="123 (checked 2026-11)"))
    assert row["citation_count"] == "123 (checked 2026-11)"


def test_newer_nonempty_replaces_older_value():
    row = make_row(version="v1", last_verified_on="2026-01-01")
    mr.merge_into(row, make_rec(version="v2"))
    assert row["version"] == "v2"


@pytest.mark.parametrize("old", ["x", "None", "UNVERIFIED", "", "nan"])
@pytest.mark.parametrize("newer_date", ["2026-11-11", "2025-01-01"])
def test_never_writes_empty_over_anything(old, newer_date):
    row = make_row(license=old, unverified_notes=old, version=old)
    mr.merge_into(row, make_rec(last_verified_on=newer_date, license="", unverified_notes="", version="None"))
    for c in ("license", "unverified_notes", "version"):
        assert row[c] == old


def test_replace_overrides_with_unverified_even_when_older():
    row = make_row(citation_count="5 (OpenAlex, checked 2026-11)", version="v2", last_verified_on="2026-12-01")
    rec = make_rec(citation_count="UNVERIFIED (OpenAlex match withdrawn 2026-11-12: no_match)", version="v1",
                   last_verified_on="2026-11-12")
    rec["_replace"] = ["citation_count"]
    mr.merge_into(row, rec)
    assert row["citation_count"] == "UNVERIFIED (OpenAlex match withdrawn 2026-11-12: no_match)"
    assert row["version"] == "v2"  # other columns: normal rules (older record does not win)
    assert "_replace" not in row


def test_replace_as_loaded_string_and_empty_value_ignored():
    row = make_row(citation_count="5 (OpenAlex, checked 2026-11)", license="CC BY")
    rec = make_rec(citation_count="UNVERIFIED (x)", license="")
    rec["_replace"] = "citation_count; license"  # load_records turns lists into "; "-joined strings
    mr.merge_into(row, rec)
    assert row["citation_count"] == "UNVERIFIED (x)" and row["license"] == "CC BY"


def test_without_replace_unverified_never_overwrites():
    row = make_row(citation_count="5 (OpenAlex, checked 2026-11)")
    mr.merge_into(row, make_rec(citation_count="UNVERIFIED (x)"))
    assert row["citation_count"] == "5 (OpenAlex, checked 2026-11)"


def test_main_honours_replace_and_never_writes_it(tmp_path, monkeypatch):
    import csv
    import json
    import sys
    cat = tmp_path / "catalog.csv"
    row = make_row(citation_count="5 (OpenAlex, checked 2026-11)", last_verified_on="2026-12-01")
    mr.write_csv(cat, [row], COLUMNS)
    annex = {t: tmp_path / f"annex{t}.csv" for t in mr.ANNEX_FILES}
    monkeypatch.setattr(mr, "CATALOG", cat)
    monkeypatch.setattr(mr, "ANNEX_FILES", annex)
    monkeypatch.setattr(mr, "LOGS", tmp_path / "logs")
    patch = tmp_path / "zz_patch_openalex.json"
    patch.write_text(json.dumps([{"name": "Test Data", "primary_domain": "REG", "resource_type": "1",
                                  "last_verified_on": "2026-11-12", "_replace": ["citation_count"],
                                  "citation_count": "UNVERIFIED (OpenAlex match withdrawn 2026-11-12: no_match)"}]),
                     encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["merge_records.py", str(patch)])
    mr.main()
    with open(cat, encoding="utf-8", newline="") as fh:
        got = list(csv.DictReader(fh))
    assert list(got[0]) == COLUMNS and len(got) == 1
    assert got[0]["citation_count"] == "UNVERIFIED (OpenAlex match withdrawn 2026-11-12: no_match)"


# ---------------------------------------------------------------- R20: a withdrawal applies only to the value it targets
OA = "5 (OpenAlex, checked 2026-11)"
WD = "UNVERIFIED (OpenAlex match withdrawn 2026-11-12: no_match)"
GS = "1500 (Google Scholar, checked 2026-12)"


def _withdrawal():
    rec = make_rec(citation_count=WD, last_verified_on="2026-11-12")
    rec["_replace"] = ["citation_count"]
    rec["_replace_if"] = {"citation_count": OA}
    return rec


def test_replace_if_applies_only_while_the_cell_holds_the_expected_value():
    row = make_row(citation_count=OA, last_verified_on="2026-11-12")
    mr.merge_into(row, _withdrawal())
    assert row["citation_count"] == WD
    before = dict(row)
    mr.merge_into(row, _withdrawal())  # second merge after the withdrawal: no-op
    assert row == before
    mr.merge_into(row, make_rec(citation_count=GS, last_verified_on="2026-12-01"))  # later update pass
    assert row["citation_count"] == GS
    mr.merge_into(row, _withdrawal())  # the rolling file is merged again: the GS value stays
    assert row["citation_count"] == GS


def test_main_replace_if_survives_load_and_never_reverts_a_later_value(tmp_path, monkeypatch):
    import csv
    import json
    import sys
    cat = tmp_path / "catalog.csv"
    mr.write_csv(cat, [make_row(citation_count=OA, last_verified_on="2026-11-12")], COLUMNS)
    monkeypatch.setattr(mr, "CATALOG", cat)
    monkeypatch.setattr(mr, "ANNEX_FILES", {t: tmp_path / f"annex{t}.csv" for t in mr.ANNEX_FILES})
    monkeypatch.setattr(mr, "LOGS", tmp_path / "logs")
    wd = {"name": "Test Data", "primary_domain": "REG", "resource_type": "1", "last_verified_on": "2026-11-12",
          "citation_count": WD, "_replace": ["citation_count"], "_replace_if": {"citation_count": OA}}
    (tmp_path / "zz_patch_openalex.json").write_text(json.dumps([wd]), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["merge_records.py", str(tmp_path / "*.json")])

    def cell():
        with open(cat, encoding="utf-8", newline="") as fh:
            return next(csv.DictReader(fh))["citation_count"]
    mr.main()
    assert cell() == WD
    mr.main()
    assert cell() == WD
    gs = {"name": "Test Data", "primary_domain": "REG", "resource_type": "1", "last_verified_on": "2026-12-01",
          "citation_count": GS}
    (tmp_path / "D01_zpatch_upd.json").write_text(json.dumps([gs]), encoding="utf-8")
    mr.main()
    assert cell() == GS
    mr.main()
    assert cell() == GS


# ---------------------------------------------------------------- _rename_items: reword list items in place
def _renamer():
    rec = make_rec(last_verified_on="")
    rec["_rename_items"] = {"found_via": {"Old sweep (Tier B)": "New sweep (Tier B)"}}
    return rec


def test_rename_items_rewrites_in_place_and_survives_a_re_added_old_item():
    row = make_row(found_via="Guide: A (u); Old sweep (Tier B); PolData")
    mr.merge_into(row, _renamer())
    assert row["found_via"] == "Guide: A (u); New sweep (Tier B); PolData"
    mr.merge_into(row, make_rec(found_via="Old sweep (Tier B); Zenodo"))  # an earlier record unions it back
    mr.merge_into(row, _renamer())
    assert row["found_via"] == "Guide: A (u); New sweep (Tier B); PolData; Zenodo"
    assert "_rename_items" not in row


def test_rename_items_drops_duplicates_and_ignores_bad_columns():
    row = make_row(found_via="Old sweep (Tier B); New sweep (Tier B)", license="CC BY")
    rec = _renamer()
    rec["_rename_items"]["license"] = {"CC BY": "x"}
    mr.merge_into(row, rec)
    assert row["found_via"] == "New sweep (Tier B)"
    assert row["license"] == "CC BY"


def test_main_rename_items_is_stable_across_merges(tmp_path, monkeypatch):
    import csv
    import json
    import sys
    cat = tmp_path / "catalog.csv"
    mr.write_csv(cat, [make_row(found_via="Old sweep (Tier B); PolData")], COLUMNS)
    annex = {t: tmp_path / f"annex{t}.csv" for t in mr.ANNEX_FILES}
    mr.write_csv(annex["5"], [{**{c: "" for c in mr.ANNEX_COLUMNS}, "name": "Archive X", "resource_type": "5",
                               "found_via": "Old sweep (Tier B)"}], mr.ANNEX_COLUMNS)
    monkeypatch.setattr(mr, "CATALOG", cat)
    monkeypatch.setattr(mr, "ANNEX_FILES", annex)
    monkeypatch.setattr(mr, "LOGS", tmp_path / "logs")
    (tmp_path / "D01_batch01.json").write_text(json.dumps([
        {"name": "Test Data", "primary_domain": "REG", "resource_type": "1", "found_via": "Old sweep (Tier B)"},
        {"name": "Archive X", "resource_type": "5", "found_via": "Old sweep (Tier B)"}]), encoding="utf-8")
    ren = {"found_via": {"Old sweep (Tier B)": "New sweep (Tier B)"}}
    (tmp_path / "zz_patch_wording.json").write_text(json.dumps([
        {"name": "Test Data", "resource_type": "1", "_rename_items": ren},
        {"name": "Archive X", "resource_type": "5", "_rename_items": ren}]), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["merge_records.py", str(tmp_path / "*.json")])

    def cells():
        with open(cat, encoding="utf-8", newline="") as fh:
            c = next(csv.DictReader(fh))["found_via"]
        with open(annex["5"], encoding="utf-8", newline="") as fh:
            return c, next(csv.DictReader(fh))["found_via"]
    mr.main()
    assert cells() == ("New sweep (Tier B); PolData", "New sweep (Tier B)")
    mr.main()
    assert cells() == ("New sweep (Tier B); PolData", "New sweep (Tier B)")
