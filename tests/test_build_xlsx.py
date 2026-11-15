import csv
import shutil
from pathlib import Path

import pytest
from openpyxl import load_workbook

import build_xlsx

MINI = Path(__file__).parent / "fixtures" / "mini"


def test_year_columns_missing_years_blank():
    years = build_xlsx.year_columns([], "2026-11-11")
    assert years == list(range(2017, 2027))
    rows = [{"id": "A-1", "name": "A", "primary_domain": "REG", "citation_count": "5 (OpenAlex, checked 2026-11)"}]
    works = [{"id": "A-1", "openalex_id": "W1", "work_year": "2015", "cited_by_count": "230",
              "match_method": "doi", "status": "ok",
              "counts_by_year": '[{"year":2024,"count":200},{"year":2021,"count":30},{"year":2005,"count":9}]'}]
    out = build_xlsx.citations_sheet_rows(rows, works, years)
    assert len(out) == 1
    r = out[0]
    assert r[2024] == 200 and r[2021] == 30
    assert r[2025] == "" and r[2023] == "" and r[2022] == ""
    assert 2005 not in r


def test_citations_sheet_source_column():
    years = [2025, 2026]
    mk = lambda i, cc: {"id": i, "name": i, "primary_domain": "REG", "citation_count": cc}
    rows = [mk("A", "958 (OpenAlex, checked 2026-11)"), mk("B", "UNVERIFIED"), mk("C", "N/A"), mk("D", ""),
            mk("E", "81 (Google Scholar, checked 2026-11)"), mk("F", "12 (checked 2026-11)")]
    works = [{"id": i, "openalex_id": "W1", "counts_by_year": "[]"} for i in "ABCDEF"]
    out = {r["id"]: r["source"] for r in build_xlsx.citations_sheet_rows(rows, works, years)}
    assert out == {"A": "OpenAlex", "B": "", "C": "", "D": "", "E": "Google Scholar", "F": ""}


def _point(tmp_path, monkeypatch, with_sidecars):
    root = tmp_path / "proj"
    shutil.copytree(MINI, root)
    if not with_sidecars:
        shutil.rmtree(root / "data" / "citations")
    d = root / "data"
    for name, val in (("ROOT", root), ("DATA", d), ("CATALOG", d / "catalog.csv"),
                      ("CITATIONS", d / "citations"), ("OUTPUTS", tmp_path / "out")):
        monkeypatch.setattr(build_xlsx, name, val)
    monkeypatch.setattr(build_xlsx, "sheet_password", lambda: "pw")  # never touch the real project root
    return root


def test_build_xlsx_with_and_without_sidecars(tmp_path, monkeypatch, capsys):
    _point(tmp_path, monkeypatch, True)
    build_xlsx.main()
    wb = load_workbook(tmp_path / "out" / "polisci_data_catalog.xlsx")
    assert "Citations" in wb.sheetnames and "Co_usage" in wb.sheetnames
    assert wb.sheetnames.index("Co_usage") < wb.sheetnames.index("Guide_Sweep")
    ws = wb["Citations"]
    hdr = [c.value for c in ws[1]]
    assert hdr[:4] == ["id", "name", "domain", "citation_count"]
    assert ws.max_row == 1 + 6
    oa = hdr.index("openalex_id") + 1
    assert ws.cell(2, oa).hyperlink.target == "https://openalex.org/" + ws.cell(2, oa).value
    assert ws.freeze_panes == "B2" and ws.auto_filter.ref
    co = wb["Co_usage"]
    vals = [[c.value for c in r] for r in co.iter_rows()]
    blank = [i for i, r in enumerate(vals) if not any(v not in (None, "") for v in r)]
    assert len(blank) == 2 and blank[1] == blank[0] + 1
    assert vals[blank[1] + 1][0] == "a"
    assert len(vals) - blank[1] - 2 == 6  # 6 edges in the fixture
    cos = [r[vals[blank[1] + 1].index("cosine")] for r in vals[blank[1] + 2:]]
    assert cos == sorted(cos, reverse=True)
    assert "skipped" not in capsys.readouterr().out


def test_build_xlsx_without_sidecars_skips(tmp_path, monkeypatch, capsys):
    _point(tmp_path, monkeypatch, False)
    build_xlsx.main()
    out = capsys.readouterr().out
    assert out.count("skipped") == 2
    wb = load_workbook(tmp_path / "out" / "polisci_data_catalog.xlsx")
    assert "Citations" not in wb.sheetnames and "Co_usage" not in wb.sheetnames
    assert "Guide_Sweep" in wb.sheetnames


def test_edges_capped_and_names_expanded():
    by_id = {"A": {"name": "Alpha"}, "B": {"name": "Beta"}}
    nodes = [{"id": "A", "name": "Alpha", "top5": "B;Z"}]
    edges = [{"a": "A", "b": "B", "cosine": str(i / 10)} for i in range(5)]
    n, e = build_xlsx.cousage_sheet_rows(nodes, edges, by_id, max_edges=3)
    assert n[0]["top5_names"] == "Beta; Z"
    assert [x["cosine"] for x in e] == [0.4, 0.3, 0.2] and e[0]["a_name"] == "Alpha"
