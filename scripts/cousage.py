"""Co-usage network of catalog datasets: which datasets are cited by the same works (spec section 6).

Two datasets are linked when the same works cite their reference articles. Everything is computed from a
sparse (catalog id x citing work) incidence matrix. Output paths resolve the module-level CITATIONS at call time
so tests can monkeypatch it.
"""
import argparse
import csv
import math
import os
import sys
import tempfile
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # batch script: always render to files, never to a window
import matplotlib.pyplot as plt  # noqa: E402
import networkx as nx  # noqa: E402
from matplotlib.colors import LogNorm, to_rgb  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
import numpy as np
import pandas as pd
from scipy import sparse

import openalex
from schema import CATALOG, CITATIONS, CODES, FIGURES

MIN_CITING = 20
MIN_CO = 3
TOP_K = 5
SEED = 42
POLISCI_SUBFIELDS = ("political science", "international relations")
EDGE_COLUMNS = ["a", "b", "co_count", "cosine", "jaccard"]
NODE_COLUMNS = ["id", "n_citing", "degree", "weighted_degree", "eigenvector", "betweenness", "community", "top5"]
ROUND = 6
DPI = 300

# 21 categories exceed what any qualitative palette keeps separable by hue alone, so colour is only one of the
# channels: the legend names every domain, the most central nodes carry text labels, and the heatmap uses
# its own sequential scale with axis labels. Hues are spread around the wheel in a fixed order (never cycled),
# alternating light/dark so that neighbouring domain numbers differ in lightness as well as hue.
DOMAIN_COLOURS = dict(zip(CODES, [
    "#1f4e9c", "#e8832a", "#2a9d8f", "#c0392b", "#8e6bbf", "#8c5a2b", "#e377c2", "#6b6b6b", "#b5a300",
    "#17a2c4", "#7fb23a", "#9c1f5b", "#f2b134", "#3b3b98", "#d95f5f", "#4f9a5a", "#0b7285", "#a0522d",
    "#5c7cfa", "#a3a3a3", "#2b2d42",
]))


def build_incidence(citing, restrict: str = "all"):
    """Binary CSR matrix (rows = sorted catalog ids, columns = distinct citing ids) and the row ids.

    Rows are always every catalog id seen in `citing`; `restrict="polisci"` only filters which citing works
    become columns (subfield contains "political science" or "international relations", case-insensitive).
    Repeated (catalog_id, citing_id) pairs count once.
    """
    if restrict not in ("all", "polisci"):
        raise ValueError(f"restrict must be 'all' or 'polisci', got {restrict!r}")
    pairs, seen_ids = set(), set()
    for r in citing:
        seen_ids.add(r["catalog_id"])
        if restrict == "polisci":
            sub = (r.get("subfield") or "").lower()
            if not any(s in sub for s in POLISCI_SUBFIELDS):
                continue
        pairs.add((r["catalog_id"], r["citing_id"]))
    ids = sorted(seen_ids)
    row_of = {i: k for k, i in enumerate(ids)}
    cols = {c: k for k, c in enumerate(sorted({c for _, c in pairs}))}
    rows = [row_of[i] for i, _ in pairs]
    cidx = [cols[c] for _, c in pairs]
    M = sparse.csr_matrix((np.ones(len(pairs), dtype=np.int64), (rows, cidx)), shape=(len(ids), len(cols)))
    return M, ids


def pair_metrics(M, ids, min_citing: int = MIN_CITING, min_co: int = MIN_CO) -> pd.DataFrame:
    """Pair table a<b with co_count, cosine = co/sqrt(n_a n_b), jaccard = co/(n_a+n_b-co).

    n_citing is counted on the incidence before any filtering; only nodes with n_citing >= min_citing and
    pairs with co_count >= min_co are kept.
    """
    M = sparse.csr_matrix(M)
    M.data[:] = 1
    n = np.asarray(M.sum(axis=1)).ravel()
    keep = np.flatnonzero(n >= min_citing)
    if len(keep) == 0:
        return pd.DataFrame({c: [] for c in EDGE_COLUMNS})
    Mk = M[keep]
    co = sparse.triu(Mk @ Mk.T, k=1).tocoo()
    mask = co.data >= min_co
    i, j, c = keep[co.row[mask]], keep[co.col[mask]], co.data[mask].astype(np.int64)
    ids_arr = np.asarray(ids)
    a, b = ids_arr[i], ids_arr[j]
    swap = a > b  # ids are sorted, but never rely on it
    a, b = np.where(swap, b, a), np.where(swap, a, b)
    cos = c / np.sqrt(n[i] * n[j])
    jac = c / (n[i] + n[j] - c)
    df = pd.DataFrame({"a": a, "b": b, "co_count": c, "cosine": cos, "jaccard": jac})
    return df.sort_values(["a", "b"], kind="stable").reset_index(drop=True)[EDGE_COLUMNS]


def build_graph(edges: pd.DataFrame, n_citing: dict) -> nx.Graph:
    """Undirected graph on every id in n_citing; edge attributes co_count, cosine, jaccard."""
    G = nx.Graph()
    for node, n in n_citing.items():
        G.add_node(node, n_citing=int(n))
    for r in edges.itertuples(index=False):
        G.add_edge(r.a, r.b, co_count=int(r.co_count), cosine=float(r.cosine), jaccard=float(r.jaccard))
    return G


def _eigenvector(G: nx.Graph) -> dict:
    """Eigenvector centrality on the largest connected component (ties: component with the smallest id).

    Components of fewer than 4 nodes use a dense eigensolve. Other components, isolates and any numerical failure get 0.0, because the centrality of a disconnected
    graph is degenerate.
    """
    out = dict.fromkeys(G.nodes, 0.0)
    comps = [c for c in nx.connected_components(G) if len(c) > 1]
    if not comps:
        return out
    comp = min(comps, key=lambda c: (-len(c), min(c)))
    sub = G.subgraph(comp)
    try:
        if len(comp) < 4:
            # networkx's ARPACK path needs k < N - 1; tiny components use a dense symmetric solve instead
            nodes = sorted(comp)
            vals, vecs = np.linalg.eigh(nx.to_numpy_array(sub, nodelist=nodes, weight="cosine"))
            ev = dict(zip(nodes, vecs[:, -1]))
        else:
            ev = nx.eigenvector_centrality_numpy(sub, weight="cosine")
    except Exception:  # noqa: BLE001 - numerical failure is documented as 0.0
        return out
    out.update({k: abs(float(v)) for k, v in ev.items()})
    return out


def node_metrics(edges: pd.DataFrame, n_citing: dict, seed: int = SEED) -> pd.DataFrame:
    """Node table (one row per id in n_citing) on the cosine-weighted graph.

    - eigenvector: largest connected component only, others 0 (see _eigenvector).
    - betweenness: shortest paths on distance = 1/cosine (cosine is a similarity); k=500 sampled sources
      with `seed` when the graph has more than 1,000 nodes, otherwise exact.
    - community: Louvain (weight cosine, `seed`) over nodes with at least one edge, labelled 1..k by size
      descending then smallest member id; isolates get "" and top5 "".
    - top5: up to five neighbours by cosine descending (ties by id), ';'-joined.
    """
    G = build_graph(edges, n_citing)
    for _, _, d in G.edges(data=True):
        d["dist"] = 1.0 / d["cosine"]
    ev = _eigenvector(G)
    n_nodes = G.number_of_nodes()
    bt = nx.betweenness_centrality(G, k=500 if n_nodes > 1000 else None, weight="dist", seed=seed)
    comm = {}
    core = G.subgraph([v for v in G if G.degree(v) > 0])
    if core.number_of_nodes():
        parts = nx.community.louvain_communities(core, weight="cosine", seed=seed)
        for label, part in enumerate(sorted(parts, key=lambda p: (-len(p), min(p))), start=1):
            comm.update(dict.fromkeys(part, label))
    rows = []
    for v in sorted(G.nodes):
        nbrs = sorted(G[v].items(), key=lambda kv: (-kv[1]["cosine"], kv[0]))
        rows.append({
            "id": v, "n_citing": G.nodes[v]["n_citing"], "degree": G.degree(v),
            "weighted_degree": sum(d["cosine"] for _, d in nbrs),
            "eigenvector": ev[v], "betweenness": bt[v], "community": comm.get(v, ""),
            "top5": ";".join(k for k, _ in nbrs[:5]),
        })
    return pd.DataFrame(rows, columns=NODE_COLUMNS)


def domain_matrix(edges: pd.DataFrame, domain_of: dict) -> pd.DataFrame:
    """All 21x21 ordered domain pairs with summed co_count.

    An edge between domains X and Y adds its co_count to (X, Y) and to (Y, X); an edge inside X adds it once
    to (X, X). share_within_a = co_count(A, A) / sum over B of co_count(A, B), reported on every row of A
    (0 when A's row sum is 0). Edges whose endpoints have no known domain are skipped.
    """
    mat = {(x, y): 0 for x in CODES for y in CODES}
    for r in edges.itertuples(index=False):
        x, y = domain_of.get(r.a), domain_of.get(r.b)
        if x not in CODES or y not in CODES:
            continue
        mat[(x, y)] += int(r.co_count)
        if x != y:
            mat[(y, x)] += int(r.co_count)
    rows = []
    for x in CODES:
        total = sum(mat[(x, y)] for y in CODES)
        share = mat[(x, x)] / total if total else 0.0
        for y in CODES:
            rows.append({"domain_a": x, "domain_b": y, "co_count": mat[(x, y)], "share_within_a": share})
    return pd.DataFrame(rows, columns=["domain_a", "domain_b", "co_count", "share_within_a"])


_NO_ACRONYM = {"", "nan", "none", "n/a", "unverified"}


def _short_label(name, fallback: str, acronym=None, width: int = 22, acro_width: int = 14) -> str:
    """Short node label: the catalog acronym when present and <= acro_width chars, otherwise the name cut at a
    word boundary to <= width chars without trailing punctuation; the id when there is neither."""
    acro = acronym.strip() if isinstance(acronym, str) else ""
    if acro.lower() not in _NO_ACRONYM and len(acro) <= acro_width:
        return acro
    name = name.strip() if isinstance(name, str) else ""
    if not name:
        return fallback
    if len(name) > width:
        head = name[:width + 1]  # one extra char: a space there means the cut falls on a word boundary
        name = head.rsplit(" ", 1)[0] if " " in head else name[:width]
    return name.rstrip(" ,;:-.(/&") or fallback


def _unique_labels(texts: dict) -> dict:
    """Make label texts unique by appending the id to every member of a colliding group."""
    count = {}
    for t in texts.values():
        count[t] = count.get(t, 0) + 1
    return {k: (f"{t} ({k})" if count[t] > 1 else t) for k, t in texts.items()}


_OFFSETS = [(1, 0, 6), (-1, 0, 6), (0, 1, 6), (0, -1, 6), (1, 1, 6), (-1, 1, 6), (1, -1, 6), (-1, -1, 6),
            (1, 0, 22), (-1, 0, 22), (0, 1, 22), (0, -1, 22), (1, 1, 22), (-1, 1, 22), (1, -1, 22), (-1, -1, 22),
            (1, 0, 40), (-1, 0, 40), (0, 1, 40), (0, -1, 40)]
LABEL_FONTSIZE = 7
LABEL_PAD = 2.0  # pixels kept free around every placed label


def _place_labels(fig, ax, pos: dict, texts: dict, fontsize: float = LABEL_FONTSIZE) -> list:
    """Greedy label placement. `texts` maps node -> label in priority order. Each label tries the offsets in
    _OFFSETS (points, relative to the node) and is kept only if its window extent, padded, overlaps no label
    already placed; otherwise it is removed. Returns [(node, text_artist, bbox)] for the labels kept."""
    fig.canvas.draw()  # settle the constrained layout so window extents are final
    renderer = fig.canvas.get_renderer()
    scale = fig.dpi / 72.0
    placed = []
    for v, text in texts.items():
        if v not in pos:
            continue
        for dx, dy, r in _OFFSETS:
            ha = "left" if dx > 0 else "right" if dx < 0 else "center"
            va = "bottom" if dy > 0 else "top" if dy < 0 else "center"
            norm = math.hypot(dx, dy)
            ann = ax.annotate(
                text, pos[v], xytext=(dx / norm * r, dy / norm * r), textcoords="offset points", ha=ha, va=va,
                fontsize=fontsize, color="#222222", zorder=5,
                arrowprops={"arrowstyle": "-", "color": "#777777", "lw": 0.4, "shrinkA": 0, "shrinkB": 0},
                bbox={"boxstyle": "round,pad=0.15", "fc": "white", "ec": "none", "alpha": 0.8})
            bb = ann.get_window_extent(renderer).padded(LABEL_PAD * scale / 2)
            if any(bb.overlaps(o) for *_, o in placed):
                ann.remove()
                continue
            placed.append((v, ann, bb))
            break
    return placed


def _save(fig, out_dir: Path, stem: str) -> list:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for ext in ("png", "svg"):
        path = out_dir / f"{stem}.{ext}"
        fig.savefig(path, dpi=DPI)
        paths.append(path)
    plt.close(fig)
    return paths


def backbone_subgraph(graph: nx.Graph, top_k: int = TOP_K) -> nx.Graph:
    """All nodes of `graph` plus its backbone edges. If no edge carries a `backbone` attribute the
    backbone is marked on a copy first (top_k neighbours by cosine per node), so an unmarked graph still draws."""
    if not any("backbone" in d for *_, d in graph.edges(data=True)):
        graph = graph.copy()
        _mark_backbone(graph, top_k)
    H = nx.Graph()
    H.add_nodes_from(graph.nodes)
    H.add_edges_from((u, v, d) for u, v, d in graph.edges(data=True) if d.get("backbone"))
    return H


def draw_network(graph: nx.Graph, nodes: pd.DataFrame, out_dir: Path, label_top: int = 25,
                 top_k: int = TOP_K) -> list:
    """Backbone network figure: spring layout (seed SEED, weight cosine), colour = primary domain,
    area ~ n_citing (largest capped), short non-overlapping labels on the label_top highest-eigenvector nodes."""
    info = nodes.set_index("id")
    H = backbone_subgraph(graph, top_k)
    pos = (nx.spring_layout(H, weight="cosine", seed=SEED, k=2.5 / math.sqrt(H.number_of_nodes()), iterations=200)
           if H.number_of_nodes() else {})
    order = sorted(H.nodes)
    colour = [DOMAIN_COLOURS.get(info["primary_domain"].get(v, ""), "#bbbbbb") for v in order]
    n = np.array([float(info["n_citing"].get(v, 1) or 1) for v in order])
    size = 12 + 110 * np.minimum(np.sqrt(n / max(n.max(), 1.0)), 0.7) / 0.7 if len(n) else []

    fig, ax = plt.subplots(figsize=(13, 9), layout="constrained")
    ax.set_axis_off()
    nx.draw_networkx_edges(H, pos, ax=ax, edge_color="#9a9a9a", alpha=0.3, width=0.4)
    if order:
        nx.draw_networkx_nodes(H, pos, nodelist=order, ax=ax, node_color=colour, node_size=size,
                               edgecolors="white", linewidths=0.4)
    present = [c for c in CODES if c in set(info["primary_domain"])]
    ax.legend(handles=[Line2D([], [], marker="o", ls="", color=DOMAIN_COLOURS[c], label=c, markersize=7)
                       for c in present], title="Primary domain", loc="upper left",
              bbox_to_anchor=(1.0, 1.0), fontsize=8, frameon=False, ncols=1 if len(present) <= 12 else 2)
    ax.set_title(f"Dataset co-usage network: backbone of each dataset's {top_k} closest neighbours (cosine); "
                 "node area ∝ citing works; labels: most central datasets", fontsize=10)
    rank_cols = [c for c in ("eigenvector", "n_citing") if c in info.columns]  # fall back to size
    top = info.loc[[v for v in order if v in info.index]].sort_values(rank_cols, ascending=False).head(label_top)
    acro = top["acronym"] if "acronym" in top.columns else pd.Series(None, index=top.index, dtype=object)
    texts = _unique_labels({v: _short_label(top["name"].get(v) if "name" in top.columns else None, v, acro.get(v))
                            for v in top.index})
    _place_labels(fig, ax, pos, texts)
    return _save(fig, out_dir, "cousage_network")


def _compact(v: int) -> str:
    return f"{v / 1000:.0f}k" if v >= 10000 else str(v)


def draw_domain_heatmap(domains: pd.DataFrame, out_dir: Path) -> list:
    """21x21 domain co-usage heatmap in CODES order; log colour scale, zero cells blank (masked)."""
    mat = (domains.pivot_table(index="domain_a", columns="domain_b", values="co_count", aggfunc="sum")
           .reindex(index=CODES, columns=CODES).fillna(0).to_numpy())
    masked = np.ma.masked_less_equal(mat, 0)  # LogNorm never sees a zero
    vmax = max(float(mat.max()), 10.0)
    cmap = plt.get_cmap("YlGnBu").with_extremes(bad="#f4f4f4")
    fig, ax = plt.subplots(figsize=(12, 10.5), layout="constrained")
    im = ax.imshow(masked, cmap=cmap, norm=LogNorm(vmin=1, vmax=vmax))
    ax.set_xticks(range(len(CODES)), CODES, rotation=90, fontsize=8)
    ax.set_yticks(range(len(CODES)), CODES, fontsize=8)
    ax.set_xticks(np.arange(-0.5, len(CODES)), minor=True)
    ax.set_yticks(np.arange(-0.5, len(CODES)), minor=True)
    ax.grid(which="minor", color="white", linewidth=1)
    ax.tick_params(which="both", length=0)
    for i in range(len(CODES)):
        for j in range(len(CODES)):
            v = int(mat[i, j])
            if v > 0:
                dark = sum(to_rgb(cmap(im.norm(v)))[k] * w for k, w in enumerate((0.299, 0.587, 0.114))) < 0.5
                ax.text(j, i, _compact(v), ha="center", va="center", fontsize=5.5,
                        color="white" if dark else "#222222")
    fig.colorbar(im, ax=ax, shrink=0.7, label="Summed pairwise co-citations (log scale)")
    ax.set_title("Domain co-usage: co-citations summed over dataset pairs (blank = none)", fontsize=10)
    return _save(fig, out_dir, "cousage_domains")


def _atomic_write(path, writer):
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    os.close(fd)
    try:
        writer(tmp)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _write_csv(path, columns, df):
    def go(tmp):
        out = df.reindex(columns=columns)
        out = out.apply(lambda s: s.round(ROUND) if s.dtype.kind == "f" else s)
        out.to_csv(tmp, index=False, encoding="utf-8", lineterminator="\r\n")
    _atomic_write(path, go)


def _mark_backbone(G: nx.Graph, top_k: int) -> None:
    keep = set()
    for v in G:
        nbrs = sorted(G[v].items(), key=lambda kv: (-kv[1]["cosine"], kv[0]))[:top_k]
        keep.update(frozenset((v, u)) for u, _ in nbrs)
    for u, v, d in G.edges(data=True):
        d["backbone"] = frozenset((u, v)) in keep


def write_outputs(edges_all, edges_pol, nodes, domains, graph, top_k: int = TOP_K) -> None:
    """Write the four co-usage files under CITATIONS (resolved at call time).

    Edges: left join of the polisci edges onto the all-works edges (blank where the pair is absent).
    GraphML: the all-works graph with boolean edge attribute `backbone` (edge is in the top-k by cosine of
    either endpoint).
    """
    out = CITATIONS
    out.mkdir(parents=True, exist_ok=True)
    pol = edges_pol.rename(columns={"co_count": "co_count_polisci", "cosine": "cosine_polisci"})
    merged = edges_all.merge(pol[["a", "b", "co_count_polisci", "cosine_polisci"]], on=["a", "b"], how="left")
    merged["co_count_polisci"] = merged["co_count_polisci"].astype("Int64")
    _write_csv(out / "cousage_edges.csv", ["a", "b", "co_count", "cosine", "jaccard", "co_count_polisci",
                                           "cosine_polisci"], merged)
    _write_csv(out / "cousage_nodes.csv", ["id", "name", "primary_domain", "n_citing", "n_citing_polisci", "degree",
                                           "weighted_degree", "eigenvector", "betweenness", "community", "top5",
                                           "shares_reference_with"], nodes)
    _write_csv(out / "cousage_domains.csv", ["domain_a", "domain_b", "co_count", "share_within_a"], domains)
    G = graph.copy()
    _mark_backbone(G, top_k)
    if "shares_reference_with" in nodes.columns:
        for i, s in zip(nodes["id"], nodes["shares_reference_with"]):
            if i in G:
                G.nodes[i]["shares_reference_with"] = str(s) if isinstance(s, str) else ""
    _atomic_write(out / "cousage.graphml", lambda tmp: nx.write_graphml(G, tmp, encoding="utf-8"))


def _usable_catalog_ids(works: dict, state: dict) -> set:
    """Catalog ids whose reference work resolved and was harvested completely (not truncated, done)."""
    ok = set()
    for cid, w in works.items():
        st = state.get(w.get("openalex_id", ""))
        if w.get("status") == "ok" and st and st.get("done") and not st.get("truncated"):
            ok.add(cid)
    return ok


def reference_groups(works: dict, usable: set):
    """(catalog id -> representative id, representative -> sorted sibling ids).

    Catalog rows whose usable works rows point at the same openalex_id form one group; the representative is
    the lowest id in sorted string order. Rows without a shared reference are their own representative.
    """
    by_work = {}
    for cid in sorted(usable):
        by_work.setdefault(works[cid]["openalex_id"], []).append(cid)
    rep_of, shares = {}, {}
    for group in by_work.values():
        rep = group[0]
        rep_of.update(dict.fromkeys(group, rep))
        shares[rep] = group[1:]
    return rep_of, shares


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Co-usage network from citing works")
    ap.add_argument("--min-citing", type=int, default=MIN_CITING)
    ap.add_argument("--min-co", type=int, default=MIN_CO)
    ap.add_argument("--top-k", type=int, default=TOP_K)
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--no-figures", action="store_true", help="skip the PNG/SVG figures")
    args = ap.parse_args(argv)

    works = openalex.read_works()
    usable = _usable_catalog_ids(works, openalex.load_state())
    rep_of, shares = reference_groups(works, usable)
    # one node per distinct reference work: sibling rows are folded into the representative's citer set
    # (build_incidence counts each (node, citing work) pair once, so nothing is double counted)
    # only rows harvested for the row's currently chosen work count (stale rows of an earlier choice do not)
    citing = [{**r, "catalog_id": rep_of[r["catalog_id"]]} for r in openalex.read_citing()
              if r["catalog_id"] in usable and r["openalex_id"] == works[r["catalog_id"]]["openalex_id"]]
    if not citing:
        print("cousage: no citing rows for complete works; run openalex.py harvest first")
        return 1
    M, ids = build_incidence(citing, "all")
    Mp, _ = build_incidence(citing, "polisci")
    n_all = np.asarray(M.sum(axis=1)).ravel()
    n_pol = np.asarray(Mp.sum(axis=1)).ravel()
    n_citing = {i: int(n) for i, n in zip(ids, n_all) if n >= args.min_citing}
    edges_all = pair_metrics(M, ids, args.min_citing, args.min_co)
    # polisci edges: nodes already qualified on all works, so only the pair threshold applies here
    edges_pol = pair_metrics(Mp, ids, 1, args.min_co)
    nodes = node_metrics(edges_all, n_citing, args.seed)
    cat = {r["id"]: r for r in openalex.read_catalog(CATALOG)}
    pol_of = dict(zip(ids, (int(x) for x in n_pol)))
    nodes["name"] = nodes["id"].map(lambda i: cat.get(i, {}).get("name", ""))
    nodes["primary_domain"] = nodes["id"].map(lambda i: cat.get(i, {}).get("primary_domain", ""))
    nodes["acronym"] = nodes["id"].map(lambda i: cat.get(i, {}).get("acronym", ""))
    nodes["n_citing_polisci"] = nodes["id"].map(pol_of)
    nodes["shares_reference_with"] = nodes["id"].map(lambda i: ";".join(shares.get(i, [])))
    domains = domain_matrix(edges_all, {i: r.get("primary_domain", "") for i, r in cat.items()})
    graph = build_graph(edges_all, n_citing)
    _mark_backbone(graph, args.top_k)  # once, so the GraphML and the figure share the same backbone
    write_outputs(edges_all, edges_pol, nodes, domains, graph, args.top_k)
    print(f"cousage: {len(n_citing)} nodes, {len(edges_all)} edges ({len(edges_pol)} polisci) -> {CITATIONS}")
    if not args.no_figures:
        FIGURES.mkdir(parents=True, exist_ok=True)
        for p in draw_network(graph, nodes, FIGURES, top_k=args.top_k) + draw_domain_heatmap(domains, FIGURES):
            print(f"cousage: wrote {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
