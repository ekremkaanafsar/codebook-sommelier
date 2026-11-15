import csv
import math

import networkx as nx
import pandas as pd
import pytest

import cousage
import openalex
import schema
from cousage import build_incidence, domain_matrix, node_metrics, pair_metrics, write_outputs

# 6 nodes, 10 citing works (c0..c9). A and B share c0-c3; n_A = 5, n_B = 8.
SETS = {
    "A": {0, 1, 2, 3, 4},
    "B": {0, 1, 2, 3, 5, 6, 7, 8},
    "C": {4, 5, 6, 9},
    "D": {0, 1, 2, 9},
    "E": {7, 8, 9},
    "F": {9},
}
POLISCI = {0, 1, 2, 3, 4, 5}  # citing works with a polisci subfield


def citing_rows(sets=SETS):
    rows = []
    for cid, cs in sets.items():
        for c in sorted(cs):
            rows.append({"catalog_id": cid, "openalex_id": "W" + cid, "citing_id": f"c{c}", "citing_year": "2020",
                         "citing_type": "article", "field": "Social Sciences",
                         "subfield": "Political Science and International Relations" if c in POLISCI else "Economics"})
    return rows


@pytest.fixture
def toy():
    return build_incidence(citing_rows())


def test_build_incidence_shape_and_dedupe():
    rows = citing_rows() + citing_rows()[:3]  # duplicated pairs
    M, ids = build_incidence(rows)
    assert ids == ["A", "B", "C", "D", "E", "F"]
    assert M.shape == (6, 10)
    assert M.sum(axis=1).A1.tolist() == [5, 8, 4, 4, 3, 1]


def test_pair_metrics_values(toy):
    M, ids = toy
    e = pair_metrics(M, ids, min_citing=1, min_co=1)
    r = e[(e.a == "A") & (e.b == "B")].iloc[0]
    assert r.co_count == 4 and abs(r.cosine - 4 / math.sqrt(40)) < 1e-9 and abs(r.jaccard - 4 / 9) < 1e-9
    assert (e.a < e.b).all() and list(e.columns) == ["a", "b", "co_count", "cosine", "jaccard"]


def test_pair_metrics_min_citing_excludes_node(toy):
    M, ids = toy
    e = pair_metrics(M, ids, min_citing=5, min_co=1)  # only A (5) and B (8) qualify
    assert set(e.a) | set(e.b) == {"A", "B"} and len(e) == 1


def test_pair_metrics_min_co_excludes_edge(toy):
    M, ids = toy
    e = pair_metrics(M, ids, min_citing=1, min_co=4)
    assert [(r.a, r.b) for r in e.itertuples()] == [("A", "B")]
    assert len(pair_metrics(M, ids, min_citing=1, min_co=1)) > 1


def test_node_metrics_top5_order_and_community_deterministic(toy):
    M, ids = toy
    e = pair_metrics(M, ids, min_citing=1, min_co=1)
    n = {i: int(M[k].sum()) for k, i in enumerate(ids)}
    n1 = node_metrics(e, n, seed=42)
    n2 = node_metrics(e, n, seed=42)
    assert n1["community"].tolist() == n2["community"].tolist()
    assert list(n1.columns) == ["id", "n_citing", "degree", "weighted_degree", "eigenvector", "betweenness",
                                "community", "top5"]
    a = n1.set_index("id").loc["A"]
    nb = []
    for r in e.itertuples():
        if "A" in (r.a, r.b):
            nb.append((-r.cosine, r.b if r.a == "A" else r.a))
    expected = [x for _, x in sorted(nb)][:5]
    assert a.top5.split(";") == expected and len(expected) == len(nb) == 3  # A shares nothing with F
    assert (n1.eigenvector > 0).all()  # connected toy graph
    assert a.degree == len(nb)
    assert abs(a.weighted_degree - sum(-c for c, _ in nb)) < 1e-9
    assert set(n1.id) == set(ids)


def test_node_metrics_isolate_and_disconnected():
    e = pd.DataFrame({"a": ["A", "C"], "b": ["B", "D"], "co_count": [3, 3], "cosine": [0.9, 0.5], "jaccard": [.5, .3]})
    n = node_metrics(e, {"A": 9, "B": 9, "C": 9, "D": 9, "Z": 9}).set_index("id")
    assert n.loc["Z", "degree"] == 0 and n.loc["Z", "top5"] == "" and n.loc["Z", "community"] == ""
    assert n.loc["A", "community"] != n.loc["C", "community"]
    assert n.loc["A", "eigenvector"] > 0 and n.loc["C", "eigenvector"] == 0  # largest component only
    assert not n[["eigenvector", "betweenness"]].isna().any().any()


def test_domain_matrix_sums_and_share(toy):
    M, ids = toy
    e = pair_metrics(M, ids, min_citing=1, min_co=1)
    dom = {"A": "REG", "B": "REG", "C": "IR", "D": "IR", "E": "IR", "F": "IR"}
    d = domain_matrix(e, dom)
    assert len(d) == 21 * 21 and list(d.columns) == ["domain_a", "domain_b", "co_count", "share_within_a"]
    g = d.set_index(["domain_a", "domain_b"])
    assert g.loc[("REG", "REG"), "co_count"] == 4
    reg_row = sum(g.loc[("REG", b), "co_count"] for b in schema.CODES)
    assert reg_row == 4 + g.loc[("REG", "IR"), "co_count"]
    assert abs(g.loc[("REG", "REG"), "share_within_a"] - 4 / reg_row) < 1e-12
    assert g.loc[("REG", "IR"), "co_count"] == g.loc[("IR", "REG"), "co_count"]
    assert g.loc[("PE", "PE"), "share_within_a"] == 0


def test_restrict_polisci_filters_columns():
    M_all, ids = build_incidence(citing_rows(), "all")
    M_pol, ids_pol = build_incidence(citing_rows(), "polisci")
    assert M_all.shape[1] == 10 and M_pol.shape[1] == 6
    assert ids_pol == ids  # rows kept for every catalog id seen
    assert M_pol[ids.index("B")].sum() == 5  # c0-c3, c5


def test_write_outputs_files_and_graphml_backbone(tmp_path, monkeypatch):
    monkeypatch.setattr(cousage, "CITATIONS", tmp_path)
    M, ids = build_incidence(citing_rows())
    Mp, _ = build_incidence(citing_rows(), "polisci")
    ea = pair_metrics(M, ids, min_citing=1, min_co=1)
    ep = pair_metrics(Mp, ids, min_citing=1, min_co=2)
    n = {i: int(M[k].sum()) for k, i in enumerate(ids)}
    nodes = node_metrics(ea, n)
    nodes["name"] = nodes["id"]
    nodes["primary_domain"] = "REG"
    nodes["n_citing_polisci"] = 1
    domains = domain_matrix(ea, dict.fromkeys(ids, "REG"))
    G = cousage.build_graph(ea, n)
    write_outputs(ea, ep, nodes, domains, G, top_k=1)
    with open(tmp_path / "cousage_edges.csv", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    assert list(rows[0]) == ["a", "b", "co_count", "cosine", "jaccard", "co_count_polisci", "cosine_polisci"]
    assert len(rows) == len(ea)
    ab = next(r for r in rows if (r["a"], r["b"]) == ("A", "B"))
    assert ab["co_count"] == "4" and ab["co_count_polisci"] == "4"
    assert any(r["co_count_polisci"] == "" for r in rows)  # left join leaves blanks
    with open(tmp_path / "cousage_nodes.csv", encoding="utf-8", newline="") as fh:
        nr = list(csv.DictReader(fh))
    assert list(nr[0]) == ["id", "name", "primary_domain", "n_citing", "n_citing_polisci", "degree",
                           "weighted_degree", "eigenvector", "betweenness", "community", "top5", "shares_reference_with"]
    with open(tmp_path / "cousage_domains.csv", encoding="utf-8", newline="") as fh:
        assert len(list(csv.DictReader(fh))) == 441
    g = nx.read_graphml(tmp_path / "cousage.graphml")
    assert g.number_of_edges() == len(ea)
    best = {}
    for u, v, d in g.edges(data=True):
        for x in (u, v):
            if x not in best or d["cosine"] > best[x]:
                best[x] = d["cosine"]
    for u, v, d in g.edges(data=True):
        want = d["cosine"] >= best[u] - 1e-12 or d["cosine"] >= best[v] - 1e-12
        assert bool(d["backbone"]) == want
    assert any(d["backbone"] for *_, d in g.edges(data=True))
    assert not all(d["backbone"] for *_, d in g.edges(data=True))


def _setup_main(tmp_path, monkeypatch):
    monkeypatch.setattr(cousage, "CITATIONS", tmp_path)
    monkeypatch.setattr(openalex, "CITATIONS", tmp_path)
    cat = tmp_path / "catalog.csv"
    with open(cat, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=schema.COLUMNS)
        w.writeheader()
        for i in SETS:
            w.writerow({**dict.fromkeys(schema.COLUMNS, ""), "id": i, "name": "N" + i, "primary_domain": "REG"})
    monkeypatch.setattr(cousage, "CATALOG", cat)
    with open(tmp_path / "openalex_works.csv", "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["id", "openalex_id", "status"])
        w.writeheader()
        for i in SETS:
            w.writerow({"id": i, "openalex_id": "W" + i, "status": "ok"})
    state = {"W" + i: {"done": True, "n": len(s), "checked_on": "2026-11-11", "truncated": False}
             for i, s in SETS.items()}
    state["WB"]["truncated"] = True
    state["WC"]["done"] = False
    openalex.save_state(state)
    openalex.append_citing(citing_rows())


def test_main_excludes_truncated_works(tmp_path, monkeypatch):
    _setup_main(tmp_path, monkeypatch)
    assert cousage.main(["--min-citing", "1", "--no-figures"]) == 0
    with open(tmp_path / "cousage_nodes.csv", encoding="utf-8", newline="") as fh:
        ids = {r["id"] for r in csv.DictReader(fh)}
    assert "B" not in ids and "C" not in ids and {"A", "D", "E", "F"} <= ids
    with open(tmp_path / "cousage_edges.csv", encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            assert not {r["a"], r["b"]} & {"B", "C"}


def _toy_graph_nodes(toy):
    M, ids = toy
    e = pair_metrics(M, ids, min_citing=1, min_co=1)
    n_citing = {i: int(n) for i, n in zip(ids, M.sum(axis=1).A1)}
    nodes = node_metrics(e, n_citing)
    nodes["name"] = ["Alpha Dataset", "Beta", "Gamma Index of Things and Stuff", "Delta", "Epsilon", "Zeta"]
    nodes["primary_domain"] = ["REG", "REG", "ELEC", "PARTY", "IR", "CONF"]
    G = cousage.build_graph(e, n_citing)
    cousage._mark_backbone(G, 2)
    return G, nodes, e


def test_domain_colours_cover_all_codes():
    assert set(cousage.DOMAIN_COLOURS) == set(schema.CODES)
    assert len(set(cousage.DOMAIN_COLOURS.values())) == 21


def test_draw_network_writes_png_and_svg(toy, tmp_path):
    G, nodes, _ = _toy_graph_nodes(toy)
    paths = cousage.draw_network(G, nodes, tmp_path, label_top=3)
    assert sorted(p.name for p in paths) == ["cousage_network.png", "cousage_network.svg"]
    for p in paths:
        assert p.exists() and p.stat().st_size > 10_000


def test_backbone_subgraph_marks_unmarked_graph(toy):
    _, _, e = _toy_graph_nodes(toy)
    M, ids = toy
    G = cousage.build_graph(e, {i: int(n) for i, n in zip(ids, M.sum(axis=1).A1)})  # no backbone attribute
    assert not any("backbone" in d for *_, d in G.edges(data=True))
    H = cousage.backbone_subgraph(G, 1)
    assert set(H.nodes) == set(G.nodes) and 0 < H.number_of_edges() < G.number_of_edges()
    assert not any("backbone" in d for *_, d in G.edges(data=True))  # input not mutated


def test_draw_network_without_eigenvector_column(toy, tmp_path):
    G, nodes, _ = _toy_graph_nodes(toy)
    assert cousage.draw_network(G, nodes.drop(columns="eigenvector"), tmp_path, label_top=2)[0].exists()


def test_main_writes_figures(tmp_path, monkeypatch):
    monkeypatch.setattr(cousage, "FIGURES", tmp_path / "figs")
    _setup_main(tmp_path, monkeypatch)
    assert cousage.main(["--min-citing", "1"]) == 0
    for name in ("cousage_network.png", "cousage_network.svg", "cousage_domains.png", "cousage_domains.svg"):
        assert (tmp_path / "figs" / name).stat().st_size > 10_000


def test_draw_domain_heatmap_handles_zeros(toy, tmp_path):
    _, _, e = _toy_graph_nodes(toy)
    dom = domain_matrix(e, {"A": "REG", "B": "REG", "C": "ELEC", "D": "PARTY", "E": "IR", "F": "CONF"})
    assert (dom.co_count == 0).any()
    paths = cousage.draw_domain_heatmap(dom, tmp_path)
    assert sorted(p.name for p in paths) == ["cousage_domains.png", "cousage_domains.svg"]
    for p in paths:
        assert p.exists() and p.stat().st_size > 10_000
    empty = domain_matrix(e.iloc[0:0], {})
    assert not cousage.draw_domain_heatmap(empty, tmp_path / "empty")[0].stat().st_size == 0


def test_short_label_acronym_shortening_and_dedup():
    sl = cousage._short_label
    assert sl("Polity5 Project", "REG-001", "Polity5") == "Polity5"
    assert sl("Some Long Dataset Name", "X-1", "A Very Long Acronym Indeed") == "Some Long Dataset Name"
    assert sl("Correlates of War Militarized Interstate Disputes", "IR-002", float("nan")) == "Correlates of War"
    assert sl("Alpha, Beta, and the Gamma Dataset of Things", "X-1", "UNVERIFIED") == "Alpha, Beta, and the"
    assert sl("Supercalifragilisticexpialidocious Data", "X-1") == "Supercalifragilisticex"
    assert sl(None, "X-9", None) == "X-9"
    out = cousage._unique_labels({"IR-001": "COW MID", "IR-002": "COW MID", "IR-003": "NMC"})
    assert out == {"IR-001": "COW MID (IR-001)", "IR-002": "COW MID (IR-002)", "IR-003": "NMC"}
    assert len(set(out.values())) == 3


def test_place_labels_never_overlap():
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(4, 3), layout="constrained")
    pos = {f"N{i}": (0.5 + 0.01 * (i % 3), 0.5 + 0.01 * (i // 3)) for i in range(12)}  # crowded cluster
    ax.scatter(*zip(*pos.values()))
    texts = {k: f"Dataset label {k}" for k in pos}
    placed = cousage._place_labels(fig, ax, pos, texts)
    plt.close(fig)
    assert len(placed) >= 2
    for i, (_, _, a) in enumerate(placed):
        for _, _, b in placed[i + 1:]:
            assert not a.overlaps(b)


def test_main_groups_rows_sharing_a_reference_work(tmp_path, monkeypatch):
    _setup_main(tmp_path, monkeypatch)
    works = tmp_path / "openalex_works.csv"
    text = works.read_text(encoding="utf-8").replace("WF", "WE")  # F shares E's reference work
    works.write_text(text, encoding="utf-8")
    assert cousage.main(["--min-citing", "1", "--no-figures"]) == 0
    with open(tmp_path / "cousage_nodes.csv", encoding="utf-8", newline="") as fh:
        rows = {r["id"]: r for r in csv.DictReader(fh)}
    assert "F" not in rows and rows["E"]["shares_reference_with"] == "F"
    assert rows["A"]["shares_reference_with"] == ""
    assert int(rows["E"]["n_citing"]) == len(SETS["E"])  # identical citer sets are not doubled
    assert list(rows["E"]).index("shares_reference_with") == list(rows["E"]).index("top5") + 1
    with open(tmp_path / "cousage_edges.csv", encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            assert r["a"] != r["b"] and "F" not in (r["a"], r["b"])
    g = nx.read_graphml(tmp_path / "cousage.graphml")
    assert g.nodes["E"]["shares_reference_with"] == "F"


def test_main_ignores_citing_rows_of_a_work_no_longer_chosen(tmp_path, monkeypatch):
    _setup_main(tmp_path, monkeypatch)
    # rows left under A from an earlier choice of reference work (W-old) must not count for A
    openalex.append_citing([{"catalog_id": "A", "openalex_id": "Wold", "citing_id": f"x{k}", "citing_year": "2020",
                             "citing_type": "article", "field": "", "subfield": ""} for k in range(7)])
    assert cousage.main(["--min-citing", "1", "--no-figures"]) == 0
    with open(tmp_path / "cousage_nodes.csv", encoding="utf-8", newline="") as fh:
        rows = {r["id"]: r for r in csv.DictReader(fh)}
    assert int(rows["A"]["n_citing"]) == len(SETS["A"])
