import shutil
from pathlib import Path

import pandas as pd
import pytest
from pypdf import PdfReader

import build_pdf
import cousage

MINI = Path(__file__).parent / "fixtures" / "mini"


def test_alongside_sentence_three_names():
    by_id = {i: {"id": i, "name": f"Name {i}", "acronym": ""} for i in ("A", "B", "C", "D")}
    by_id["B"]["acronym"] = "BB"
    nodes = {"X": {"id": "X", "top5": "A;MISSING;B;C;D"}}
    assert build_pdf.alongside_sentence("X", nodes, {**by_id}) == \
        "Most often used alongside: Name A, Name B (BB), Name C."


def test_alongside_sentence_sibling_uses_node():
    by_id = {i: {"id": i, "name": f"Name {i}", "acronym": ""} for i in ("A", "B", "S")}
    nodes = {"X": {"id": "X", "top5": "A;S;B", "shares_reference_with": "S;T"}}
    assert build_pdf.sibling_map(nodes) == {"S": "X", "T": "X"}
    want = "Most often used alongside: Name A, Name B."
    assert build_pdf.alongside_sentence("S", nodes, by_id) == want
    assert build_pdf.alongside_sentence("S", nodes, by_id, build_pdf.sibling_map(nodes)) == want


def test_alongside_sentence_not_node_empty():
    by_id = {"A": {"id": "A", "name": "Name A", "acronym": ""}}
    assert build_pdf.alongside_sentence("Z", {"X": {"top5": "A"}}, by_id) == ""
    assert build_pdf.alongside_sentence("X", {"X": {"top5": "NOPE;ALSO"}}, by_id) == ""


def _point(tmp_path, monkeypatch):
    root = tmp_path / "proj"
    shutil.copytree(MINI, root)
    d = root / "data"
    for name, val in (("DATA", d), ("CATALOG", d / "catalog.csv"), ("NARR", d / "narratives"),
                      ("CITATIONS", d / "citations"), ("FIGURES", tmp_path / "figs"),
                      ("OUTPUTS", tmp_path / "out")):
        monkeypatch.setattr(build_pdf, name, val)
    return d


def _figures(d, figs):
    nodes = pd.read_csv(d / "citations" / "cousage_nodes.csv")
    edges = pd.read_csv(d / "citations" / "cousage_edges.csv")
    G = cousage.build_graph(edges, dict(zip(nodes["id"], nodes["n_citing"])))
    cousage._mark_backbone(G, 2)
    cousage.draw_network(G, nodes, figs, label_top=3)
    dom = cousage.domain_matrix(edges, dict(zip(nodes["id"], nodes["primary_domain"])))
    cousage.draw_domain_heatmap(dom, figs)


def _r(i, cc="", dom="REG"):
    return {"id": i, "name": i, "acronym": "", "primary_domain": dom, "citation_count": cc}


def _w(i, n, status="ok"):
    return {"id": i, "status": status, "cited_by_count": str(n)}


def test_top_cited_uses_openalex_even_if_catalog_larger():
    rows = [_r("R1", "9999 (Google Scholar, checked 2026-11)"), _r("R2", "UNVERIFIED")]
    out = build_pdf.top_cited_by_domain(rows, [_w("R1", 5), _w("R2", 50)])
    assert [(o[1], o[3], o[4]) for o in out] == [("R2", "50", "OpenAlex"), ("R1", "5", "OpenAlex")]


def test_top_cited_fewer_than_three_resolved():
    rows = [_r("R1"), _r("R2"), _r("R3"), _r("E1", dom="ELEC")]
    out = build_pdf.top_cited_by_domain(rows, [_w("R1", 5), _w("R2", 7, "not_found"), _w("R3", 1)])
    assert [o[1] for o in out] == ["R1", "R3"]


def test_top_cited_ties_deterministic():
    rows = [_r("R3"), _r("R1"), _r("R2"), _r("R4")]
    out = build_pdf.top_cited_by_domain(rows, [_w(i, 10) for i in ("R3", "R1", "R2", "R4")])
    assert [o[1] for o in out] == ["R1", "R2", "R3"]


def test_build_pdf_with_and_without_chapter(tmp_path, monkeypatch, capsys):
    d = _point(tmp_path, monkeypatch)
    build_pdf.main()
    pdf = tmp_path / "out" / "polisci_data_catalog.pdf"
    assert pdf.exists()
    assert "skipped" not in capsys.readouterr().out
    _figures(d, tmp_path / "figs")
    build_pdf.main()
    with_ch = PdfReader(str(pdf))
    text = "\n".join(p.extract_text() for p in with_ch.pages)
    assert "Citation record" in text and "Most often used alongside" in text
    assert "Three most-cited datasets per domain" in text and "Ten bridge datasets by betweenness" in text
    n_with = len(with_ch.pages)

    (d / "narratives" / "_citations.json").unlink()
    build_pdf.main()
    assert "skipped" in capsys.readouterr().out
    without = PdfReader(str(pdf))
    assert len(without.pages) < n_with
    assert "Citation record" not in "\n".join(p.extract_text() for p in without.pages)
