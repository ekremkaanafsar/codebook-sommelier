import csv
import json
import re
import shutil
from pathlib import Path

import html5lib
import pytest

import build_site as bs
import site_common as sc

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "site"
BASE = "/"
PENDING_TARGETS = set()


def _html_files(out):
    return sorted(out.rglob("*.html"))


def _parse(path):
    return html5lib.parse(path.read_text(encoding="utf-8"), namespaceHTMLElements=False)


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("site") / "site"
    report = bs.build(FIXTURE, out)
    return report, out


@pytest.fixture
def site_fixture(tmp_path):
    root = tmp_path / "root"
    shutil.copytree(FIXTURE, root)
    return root


@pytest.fixture
def site_fixture_minimal(site_fixture):
    shutil.rmtree(site_fixture / "data" / "citations")
    (site_fixture / "data" / "derived" / "facets.csv").unlink()
    return site_fixture


def _page(out, *parts):
    return (out.joinpath(*parts) / "index.html").read_text(encoding="utf-8")


def test_build_counts(built):
    r, out = built
    assert r.pages["dataset"] == 12
    assert r.pages["domain"] == 21
    assert r.pages["home"] == 1
    assert (out / "index.html").exists()
    assert (out / "assets" / "site.css").exists()
    assert len(list((out / "datasets").iterdir())) == 12


def test_every_internal_link_resolves(built):
    _, out = built
    checked = 0
    for f in _html_files(out):
        doc = _parse(f)
        for el in doc.iter():
            for attr in ("href", "src", "action"):
                v = el.get(attr)
                if not v or not v.startswith(BASE) or v.startswith("//"):
                    continue
                path = v[len(BASE):].split("#", 1)[0].split("?", 1)[0]
                if path in PENDING_TARGETS:
                    continue
                target = out / path
                ok = target.is_file() or (target / "index.html").is_file()
                assert ok, f"{f.relative_to(out)}: {attr}={v} does not resolve"
                checked += 1
    assert checked > 100


def test_dataset_page_has_one_jsonld(built):
    _, out = built
    for d in (out / "datasets").iterdir():
        doc = _parse(d / "index.html")
        blocks = [s for s in doc.iter("script") if s.get("type") == "application/ld+json"]
        assert len(blocks) == 1, d.name
        j = json.loads(blocks[0].text)
        assert j["@type"] == "Dataset"
        assert j["url"] == f"/datasets/{d.name}/"


def test_no_unverified_text_in_html(built):
    _, out = built
    for f in _html_files(out):
        assert "UNVERIFIED" not in f.read_text(encoding="utf-8").upper(), f


def test_unverified_values_render_as_marker(built):
    _, out = built
    html = _page(out, "datasets", "CONF-002")
    assert 'class="unv"' in html and "not confirmed" in html


def test_dataset_page_non_url_landing_not_linked(built):
    _, out = built
    doc = _parse(out / "datasets" / "ELEC-003" / "index.html")
    hrefs = [a.get("href") or "" for a in doc.iter("a")]
    assert not any("registration" in h for h in hrefs)
    assert "registration required" in _page(out, "datasets", "ELEC-003")
    j = json.loads(next(s for s in doc.iter("script") if s.get("type") == "application/ld+json").text)
    assert "sameAs" not in j
    for h in hrefs:
        assert h.startswith(("/", "#", "https://", "http://")), h


def test_scores_block_provisional_label_and_no_rank(built):
    _, out = built
    for d in (out / "datasets").iterdir():
        html = (d / "index.html").read_text(encoding="utf-8")
        assert "AI-assisted, provisional, not yet human-validated" in html
        doc = _parse(d / "index.html")
        for el in doc.iter():
            for k, v in el.attrib.items():
                assert "rank" not in k.lower(), (d.name, k)
                if k in ("class", "id") or k.startswith(("data-", "aria-")):
                    assert "rank" not in v.lower() and "q_overall" not in v.lower(), (d.name, k, v)
                assert not k.lower().startswith("data-sort"), (d.name, k)
    html = _page(out, "datasets", "REG-001")
    assert 'href="/methods/#rubric"' not in html or (out / "methods").exists()
    assert "4.9" in html  # q_overall shown as text in the quality block


def test_q_overall_not_used_for_order_on_domain_page(built):
    _, out = built
    html = _page(out, "domains", "REG")
    names = re.findall(r'<li class="ds-item"><a href="/datasets/(REG-\d+)/"', html)
    assert names, "domain page lists its datasets"
    assert "q_overall" not in html


def test_domain_page_core_and_alongside(built):
    _, out = built
    html = _page(out, "domains", "REG")
    doc = _parse(out / "domains" / "REG" / "index.html")
    cards = [el for el in doc.iter("article") if "core" in (el.get("class") or "")]
    assert [c.get("id") for c in cards] == ["core-REG-001", "core-REG-003", "core-REG-004", "core-REG-002"]
    first = cards[0]
    para = next(p for p in first.iter("p") if "alongside" in (p.get("class") or ""))
    along = [a.get("href") for a in para.iter("a")]
    # REG-001's top5 is REG-003;REG-002;CONF-001;ZZZ-001;IR-001 (ZZZ-001 is not a catalog row)
    assert along == ["/datasets/REG-003/", "/datasets/REG-002/", "/datasets/CONF-001/", "/datasets/IR-001/"]
    assert "Fixture landscape paragraph one for REG." in html
    assert "Fixture gaps paragraph for REG." in html
    assert "Graded democracy indices" in html
    # a domain with no rows and no narrative still gets a page
    assert "not been compiled" in _page(out, "domains", "BUR")


def test_home_page(built):
    _, out = built
    html = (out / "index.html").read_text(encoding="utf-8")
    assert len(html.encode("utf-8")) <= 40_000
    doc = _parse(out / "index.html")
    cards = [el for el in doc.iter("li") if "subfield" in (el.get("class") or "").split()]
    assert len(cards) == 21
    assert "V-Dem" in html and "RoW" in html  # core acronyms of REG
    assert "10.5281/zenodo.1234567" in html  # how to cite
    assert "CC BY 4.0" in html


def test_google_site_verification_only_on_home_page(site_fixture, tmp_path):
    cfg = site_fixture / "data" / "site_config.json"
    data = json.loads(cfg.read_text(encoding="utf-8"))
    data["google_site_verification"] = "Tok_en-123"
    cfg.write_text(json.dumps(data), encoding="utf-8")
    out = tmp_path / "out"
    bs.build(site_fixture, out)
    tag = '<meta name="google-site-verification" content="Tok_en-123">'
    assert tag in (out / "index.html").read_text(encoding="utf-8")
    assert "google-site-verification" not in _page(out, "datasets", "REG-001")


def test_no_google_site_verification_without_config(built):
    _, out = built
    assert "google-site-verification" not in (out / "index.html").read_text(encoding="utf-8")


def test_build_without_sidecars_succeeds(site_fixture_minimal, tmp_path):
    r = bs.build(site_fixture_minimal, tmp_path / "out")
    assert r.pages["dataset"] == 12
    text = " ".join(r.notices).lower()
    assert "citations" in text and "facets" in text
    html = _page(tmp_path / "out", "datasets", "REG-001")
    assert "Used alongside" not in html


def test_missing_required_input_fails_with_path(site_fixture, tmp_path):
    p = site_fixture / "data" / "comparison_groups.csv"
    p.unlink()
    with pytest.raises(FileNotFoundError) as e:
        bs.build(site_fixture, tmp_path / "out")
    assert str(p) in str(e.value)


def test_missing_narrative_for_complete_domain_fails(site_fixture, tmp_path):
    p = site_fixture / "data" / "narratives" / "IR.json"
    p.unlink()
    with pytest.raises(FileNotFoundError) as e:
        bs.build(site_fixture, tmp_path / "out")
    assert str(p) in str(e.value)


def test_dataset_page_size_budget(built):
    _, out = built
    for d in (out / "datasets").iterdir():
        assert (d / "index.html").stat().st_size <= 60_000, d.name


def test_html_parses(built):
    _, out = built
    parser = html5lib.HTMLParser(strict=True, namespaceHTMLElements=False)
    for f in _html_files(out):
        parser.parse(f.read_text(encoding="utf-8"))


def test_unsafe_domain_never_rendered(built):
    _, out = built
    for f in _html_files(out):
        assert "defunct-data.example" not in f.read_text(encoding="utf-8"), f
    assert "withheld" in _page(out, "datasets", "IR-002")


def test_dataset_page_sections(built):
    _, out = built
    html = _page(out, "datasets", "REG-002")
    # successor link from "superseded by REG-001"
    assert re.search(r'Superseded by <a href="/datasets/REG-001/">', html)
    html = _page(out, "datasets", "IR-001")
    assert 'href="/datasets/IR-002/"' in html and "Some Unlisted Dataset" in html
    assert "ZZZ-999" in html and 'href="/datasets/ZZZ-999/"' not in html
    html = _page(out, "datasets", "REG-001")
    assert "Used alongside" in html and "<svg" in html and "openalex.org/W" in html
    assert "Princeton University" in html  # guide institution from found_via
    assert "libguides.princeton.edu" not in html.split('id="provenance"')[1].split("</section>")[0]
    assert "issues/new?template=correction.yml&amp;title=REG-001%3A%20" in html


def test_report_link_omitted_without_repo_url(site_fixture, tmp_path):
    cfg = site_fixture / "data" / "site_config.json"
    cfg.write_text(json.dumps({"base_url": "/", "repo_url": ""}), encoding="utf-8")
    bs.build(site_fixture, tmp_path / "out")
    html = _page(tmp_path / "out", "datasets", "REG-001")
    assert "Report a problem" not in html and "issues/new" not in html


def test_base_url_prefix(site_fixture, tmp_path):
    cfg = site_fixture / "data" / "site_config.json"
    cfg.write_text(json.dumps({"base_url": "/cat/", "repo_url": ""}), encoding="utf-8")
    bs.build(site_fixture, tmp_path / "out")
    html = _page(tmp_path / "out", "datasets", "REG-001")
    assert 'href="/cat/assets/site.css"' in html and 'href="/cat/domains/REG/"' in html


def test_url_for():
    cfg = {"base_url": "https://example.org/cat"}
    assert bs.url_for("home", "", cfg) == "https://example.org/cat/"
    assert bs.url_for("domain", "REG", cfg) == "https://example.org/cat/domains/REG/"
    assert bs.url_for("dataset", "REG-001", cfg) == "https://example.org/cat/datasets/REG-001/"
    assert bs.url_for("compare", "event-data-2", cfg) == "https://example.org/cat/compare/event-data-2/"
    assert bs.url_for("explore", "", cfg) == "https://example.org/cat/explore/"
    assert bs.url_for("annex", "literature", cfg) == "https://example.org/cat/annex/literature/"
    for k in ("network", "methods"):
        assert bs.url_for(k, "", cfg) == f"https://example.org/cat/{k}/"
    for k in ("nope", "changelog"):
        with pytest.raises(ValueError):
            bs.url_for(k, "", cfg)


def test_load_inputs_shapes():
    inp = bs.load_inputs(FIXTURE)
    assert len(inp.rows) == 12 and set(inp.by_id) == {r["id"] for r in inp.rows}
    assert [g["slug"] for g in inp.groups] == ["graded-democracy-indices", "event-data", "event-data-2"]
    assert len(inp.domains) == 21 and inp.facets and inp.works and inp.nodes
    assert inp.annex4 and inp.annex5 and inp.cfg["repo_url"]


def test_merge_partners_index_matches_site_common():
    inp = bs.load_inputs(FIXTURE)
    idx = bs.PartnerIndex(inp.rows)
    for r in inp.rows:
        system, others = sc.merge_partners(r, inp.rows)
        assert idx.partners(r) == (system, [o["id"] for o in others])


def test_domain_colours_match_cousage():
    pytest.importorskip("matplotlib")
    pytest.importorskip("networkx")
    pytest.importorskip("scipy")
    import cousage
    assert bs.DOMAIN_COLOURS == cousage.DOMAIN_COLOURS


def test_css_has_print_and_domain_colours():
    css = (Path(bs.__file__).resolve().parent.parent / "site_assets" / "site.css").read_text(encoding="utf-8")
    assert "@media print" in css and "prefers-color-scheme: dark" in css
    for code, colour in bs.DOMAIN_COLOURS.items():
        assert f"--d-{code}: {colour}" in css


def test_no_external_scripts_or_fonts(built):
    _, out = built
    for f in _html_files(out):
        html = f.read_text(encoding="utf-8")
        assert '<script src="http' not in html and "fonts.googleapis" not in html


# ---------------------------------------------------------------- review fixes

def _edit_rows(root, edits):
    import csv
    p = root / "data" / "catalog.csv"
    with open(p, encoding="utf-8", newline="") as f:
        rd = csv.DictReader(f)
        cols, rows = rd.fieldnames, list(rd)
    for r in rows:
        r.update(edits.get(r["id"], {}))
    with open(p, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def _jsonld(html):
    doc = html5lib.parse(html, namespaceHTMLElements=False)
    return json.loads(next(s for s in doc.iter("script") if s.get("type") == "application/ld+json").text)


def test_built_kinds_pinned():
    assert bs.BUILT_KINDS == frozenset({"home", "domain", "dataset", "compare", "explore", "network", "methods",
                                        "annex"})


def test_injection_is_escaped(site_fixture, tmp_path):
    evil = 'Evil </script><script>alert(1)</script> & "q"'
    _edit_rows(site_fixture, {"REG-004": {"name": evil, "acronym": "<b>X</b>", "landing_url": "javascript:alert(1)",
                                          "citation": "<script>alert(2)</script>",
                                          "download_url": "see javascript:alert(3) or https://example.org/d.csv"}})
    bs.build(site_fixture, tmp_path / "out")
    html = _page(tmp_path / "out", "datasets", "REG-004")
    assert "<script>alert" not in html and "</script><script>" not in html
    assert "<b>X</b>" not in html
    doc = _parse(tmp_path / "out" / "datasets" / "REG-004" / "index.html")
    hrefs = [a.get("href") or "" for a in doc.iter("a")]
    assert not any(h.lower().startswith("javascript") for h in hrefs)
    assert "https://example.org/d.csv" in hrefs
    assert _jsonld(html)["name"] == evil
    for f in _html_files(tmp_path / "out"):
        assert "<script>alert" not in f.read_text(encoding="utf-8"), f


def test_na_acronym_not_shown(site_fixture, tmp_path):
    _edit_rows(site_fixture, {"ELEC-002": {"acronym": "N/A"}, "REG-001": {"acronym": "V-Dem; VDEM"},
                              "ELEC-001": {"acronym": "UNVERIFIED"}})
    bs.build(site_fixture, tmp_path / "out")
    out = tmp_path / "out"
    html = _page(out, "datasets", "ELEC-002")
    title = re.search(r"<title>(.*?)</title>", html).group(1)
    assert "N/A" not in title and "n/a" not in title.lower()
    assert 'og:title" content="Parliaments and Governments Database (ParlGov) |' in html
    assert 'class="acr-line"' not in html
    dom = _page(out, "domains", "ELEC")
    assert ">N/A<" not in dom and ">n/a<" not in dom
    home = (out / "index.html").read_text(encoding="utf-8")
    assert "N/A" not in home and "n/a" not in home
    assert "V-Dem," in home and "VDEM" not in home  # first ;-segment only


def test_doi_tokens_linked(site_fixture, tmp_path):
    _edit_rows(site_fixture, {
        "REG-004": {"doi": "10.7910/DVN/ABC123 (dataset); 10.1017/S0003055400001234 (reference article)"},
        "REG-003": {"doi": "N/A (no DOI; reference article 10.1093/xyz/abc01 per publisher)"}})
    bs.build(site_fixture, tmp_path / "out")
    html = _page(tmp_path / "out", "datasets", "REG-004")
    assert '<a href="https://doi.org/10.7910/dvn/abc123">10.7910/DVN/ABC123</a> (dataset)' in html
    assert '<a href="https://doi.org/10.1017/s0003055400001234">' in html
    html = _page(tmp_path / "out", "datasets", "REG-003")
    assert "n/a (no DOI; reference article " in html
    assert '<a href="https://doi.org/10.1093/xyz/abc01">10.1093/xyz/abc01</a> per publisher' in html


def test_na_detail_rendered(site_fixture, tmp_path):
    _edit_rows(site_fixture, {"REG-004": {"programmatic_access": "N/A (portal offline)"}})
    bs.build(site_fixture, tmp_path / "out")
    assert "n/a (portal offline)" in _page(tmp_path / "out", "datasets", "REG-004")


def test_merge_block_uses_system_labels(site_fixture, tmp_path):
    _edit_rows(site_fixture, {"REG-004": {"merge_keys": "None (country names only in a free-text column)"},
                              "REG-003": {"merge_keys": "V-Dem country_id; COW code (ccode); year"}})
    bs.build(site_fixture, tmp_path / "out")
    out = tmp_path / "out"
    html = _page(out, "datasets", "REG-004")
    assert 'id="merge"' not in html and "Identifier systems" not in html
    html = _page(out, "datasets", "REG-003")
    sec = html.split('id="merge"')[1].split("</section>")[0]
    assert "COW country code" in sec and "V-Dem country_id" in sec and "ccode" not in sec


def test_no_derived_facets_row(built):
    _, out = built
    for d in (out / "datasets").iterdir():
        assert "Derived facets" not in (d / "index.html").read_text(encoding="utf-8")


def test_link_status_visually_hidden_text(built):
    _, out = built
    html = _page(out, "datasets", "REG-001")
    assert '<span class="vh">link status: ok</span>' in html
    assert 'class="lstat" aria-label' not in html and 'aria-label="Link checked' not in html


def test_relative_base_url_notice(built, site_fixture, tmp_path):
    r, _ = built
    assert any("base_url" in n and "absolute" in n for n in r.notices)
    cfg = site_fixture / "data" / "site_config.json"
    cfg.write_text(json.dumps({"base_url": "https://example.org/cat/", "repo_url": ""}), encoding="utf-8")
    r2 = bs.build(site_fixture, tmp_path / "out")
    assert not any("base_url" in n for n in r2.notices)


def test_domain_description_without_core(built):
    _, out = built
    bur = _page(out, "domains", "BUR")
    assert "core canon" not in re.search(r'<meta name="description" content="([^"]*)"', bur).group(1)
    reg = _page(out, "domains", "REG")
    assert "core canon" in re.search(r'<meta name="description" content="([^"]*)"', reg).group(1)


def _css():
    return (Path(bs.__file__).resolve().parent.parent / "site_assets" / "site.css").read_text(encoding="utf-8")


def test_css_narrow_screens_do_not_overflow():
    css = _css()
    assert re.search(r"\.facts \{[^}]*grid-template-columns: minmax\(8rem, 11rem\) minmax\(0, 1fr\)", css)
    assert re.search(r"\.facts dd \{[^}]*min-width: 0;[^}]*overflow-wrap: anywhere", css)
    narrow = css.split("@media (max-width: 40rem)")[1].split("@media print")[0]
    assert "grid-template-columns: minmax(0, 1fr)" in narrow


def _lum(hexc):
    c = [int(hexc[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
    return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]


def _ratio(a, b):
    la, lb = sorted((_lum(a), _lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def test_css_contrast():
    css = _css()
    light = css.split(":root {")[1].split("}")[0]
    dark = css.split("@media (prefers-color-scheme: dark)")[1].split("}")[0]
    for block in (light, dark):
        v = dict(re.findall(r"--([a-z-]+): (#[0-9a-f]{6});", block))
        for fg in ("dot-off", "control"):
            for bg in ("bg", "surface"):
                assert _ratio(v[fg], v[bg]) >= 3, (fg, bg, v[fg], v[bg])
        for fg, bg in (("fg", "bg"), ("muted", "bg"), ("muted", "surface"), ("link", "bg"), ("unv", "bg")):
            assert _ratio(v[fg], v[bg]) >= 4.5, (fg, bg)
    assert re.search(r"\.search input[^{]*\{[^}]*border: 1px solid var\(--control\)", css)


def test_print_shows_closed_details(built):
    pr = "".join(_css().split("@media print")[1:])
    assert "::details-content" in pr
    _, out = built
    assert "beforeprint" in _page(out, "datasets", "REG-001")


def test_comparison_pages_unique(built):
    r, out = built
    assert r.pages["compare"] == 3
    dirs = sorted(p.name for p in (out / "compare").iterdir() if p.is_dir())
    assert dirs == ["event-data", "event-data-2", "graded-democracy-indices"]
    assert "Violent events" in _page(out, "compare", "event-data")
    assert "Interstate incidents" in _page(out, "compare", "event-data-2")
    idx = _page(out, "compare")
    assert len(re.findall(r'href="/compare/[a-z]', idx)) == 3


def test_comparison_cells_link_to_datasets(built):
    _, out = built
    html = _page(out, "compare", "graded-democracy-indices")
    doc = html5lib.parse(html, namespaceHTMLElements=False)
    rows = [tr for tr in doc.iter("tr") if tr.find("th") is not None and tr.find("td") is not None]
    assert len(rows) == 3
    for tr in rows:
        links = [a.get("href") for a in tr.iter("a")]
        assert links[0].startswith("/datasets/REG-00")
        assert all(h == links[0] for h in links)
        assert len(tr.findall("td")) == 7
    for label in ("Unit", "Temporal coverage", "Spatial coverage", "Measurement approach", "Generation method",
                  "Access", "Status"):
        assert f">{label}</th>" in html
    assert "UNVERIFIED" not in html


def test_comparison_skips_unknown_ids(site_fixture, tmp_path):
    p = site_fixture / "data" / "comparison_groups.csv"
    p.write_text(p.read_text(encoding="utf-8").replace("REG-003,Prefer", "REG-003; REG-999,Prefer"),
                 encoding="utf-8", newline="")
    r = bs.build(site_fixture, tmp_path / "o")
    assert any("REG-999" in n for n in r.notices)
    assert "REG-999" not in _page(tmp_path / "o", "compare", "graded-democracy-indices")


def test_network_page_lists_bridges_and_downloads(built):
    _, out = built
    html = _page(out, "network")
    assert "Fixture method paragraph." in html and "Fixture findings paragraph." in html
    doc = html5lib.parse(html, namespaceHTMLElements=False)
    rows = [tr for tr in doc.iter("tr") if tr.find("td") is not None]
    assert 1 <= len(rows) <= 10
    assert "/datasets/CONF-003/" in html  # highest betweenness first
    assert html.index("/datasets/CONF-003/") < html.index("/datasets/REG-003/")
    assert "Shares its reference article with" in html
    for name in ("cousage.graphml", "cousage_nodes.csv", "cousage_edges.csv"):
        assert f'href="/downloads/{name}"' in html
        assert (out / "downloads" / name).is_file()
    for name in ("cousage_network.svg", "cousage_domains.svg"):
        assert f'src="/assets/{name}"' in html
        assert (out / "assets" / name).is_file()
    # intrinsic size on each figure, so a lazy-loaded image is never a 0x0 box that never loads
    imgs = [i for i in doc.iter("img") if "cousage" in (i.get("src") or "")]
    assert len(imgs) == 2 and all(i.get("width") == "10" and i.get("height") == "10" for i in imgs)


def test_svg_size_reads_viewbox_then_width_height(tmp_path):
    p = tmp_path / "a.svg"
    p.write_text('<?xml version="1.0"?>\n<svg width="936pt" height="648pt" viewBox="0 0 936 648"></svg>', encoding="utf-8")
    assert bs.svg_size(p) == (936, 648)
    p.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="10" height="12"></svg>', encoding="utf-8")
    assert bs.svg_size(p) == (10, 12)
    p.write_text('<svg xmlns="http://www.w3.org/2000/svg"></svg>', encoding="utf-8")
    assert bs.svg_size(p) == (None, None)


def test_network_without_figures_or_sidecars(site_fixture_minimal, tmp_path):
    shutil.rmtree(site_fixture_minimal / "outputs")
    r = bs.build(site_fixture_minimal, tmp_path / "o")
    html = _page(tmp_path / "o", "network")
    assert "<img" not in html and "Downloads" not in html and "Ten bridge" not in html
    assert any("figures" in n for n in r.notices)


def test_methods_has_rubric_anchor_and_license(built):
    _, out = built
    html = _page(out, "methods")
    assert 'id="rubric"' in html
    assert bs.PROVISIONAL in html
    for dim in ("Validity", "Reliability", "Transparency", "Coverage", "Comparability", "Maintenance",
                "Accessibility", "Adoption"):
        assert f"<th scope=\"row\">{dim}</th>" in html
    assert "not a ranking" in html and "batch effects" in html and "Validation by human reviewers is pending" in html
    assert "CC BY 4.0" in html and "MIT" in html
    assert "Fixture caveat paragraph." in html and "Fixture method paragraph." in html
    assert "10.5281/zenodo.1234567" in html and "Example Political Data Catalog" in html
    assert "changelog" not in html.lower() and 'id="openalex"' in html
    assert 'href="/methods/#rubric"' in _page(out, "datasets", "REG-001")


def test_annex_pages_rows(built):
    r, out = built
    assert r.pages["annex"] == 2
    for kind, csvname in (("literature", "literature_databases.csv"), ("archives", "primary_archives.csv")):
        with open(FIXTURE / "data" / csvname, encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        html = _page(out, "annex", kind)
        doc = html5lib.parse(html, namespaceHTMLElements=False)
        body_rows = [tr for tr in doc.iter("tr") if tr.find("td") is not None]
        assert len(body_rows) == len(rows) >= 1
        assert "UNVERIFIED" not in html
        assert "Guide:" not in html and "librarian" not in html.lower()
    assert 'href="https://about.proquest.com/' in _page(out, "annex", "literature")
    assert 'href="/annex/literature/"' in _page(out, "methods")


def test_no_changelog_page_even_when_changelog_md_exists(built):
    r, out = built
    assert (FIXTURE / "CHANGELOG.md").is_file()  # the fixture keeps one, and it is still not published
    assert not (out / "changelog").exists()
    assert "changelog" not in r.pages
    for page in (_page(out), _page(out, "methods"), _page(out, "datasets", "REG-001")):
        assert "changelog" not in page.lower()
    assert "changelog" not in (out / "sitemap.xml").read_text(encoding="utf-8")
    assert "Earlier fixture entry" not in (out / "llms.txt").read_text(encoding="utf-8")


def test_404_exists(built):
    _, out = built
    html = (out / "404.html").read_text(encoding="utf-8")
    assert 'href="/"' in html and "/domains/REG/" in html
    assert "Page not found" in html
    assert 'href="/explore/"' in html


def test_header_nav_enabled_kinds(built):
    _, out = built
    html = _page(out, "datasets", "REG-001")
    for href in ("/explore/", "/compare/", "/network/", "/methods/"):
        assert f'<a href="{href}">' in html
    assert 'action="/explore/"' in html and 'name="q"' in html


def test_page_counts_include_new_kinds(built):
    r, _ = built
    assert r.pages["compare_index"] == r.pages["network"] == r.pages["methods"] == 1
    assert r.pages["notfound"] == 1


# ---------------------------------------------------------------- explorer and search (Task 6)

EXPLORER_KEYS = {"id", "name", "acronym", "pd", "sd", "type", "unit_class", "regions", "start_year", "end_year",
                 "access", "status", "api", "cites", "guides", "conf", "q", "url", "lu", "summary"}


def _api(out, name):
    return json.loads((out / "api" / f"{name}.json").read_text(encoding="utf-8"))


def test_explorer_json_keys_and_types(built):
    _, out = built
    recs = _api(out, "explorer")
    assert len(recs) == 12
    by = {r["id"]: r for r in recs}
    for r in recs:
        assert set(r) == EXPLORER_KEYS
        assert isinstance(r["sd"], list) and isinstance(r["regions"], list)
        assert r["api"] in (0, 1) and isinstance(r["guides"], int)
        assert r["cites"] is None or isinstance(r["cites"], int)
        assert r["q"] is None or (len(r["q"]) == 8 and all(isinstance(n, int) and 1 <= n <= 5 for n in r["q"]))
        assert r["start_year"] is None or isinstance(r["start_year"], int)
        assert r["lu"] is None or isinstance(r["lu"], int)
        assert r["url"] == f"/datasets/{r['id']}/" and len(r["summary"]) <= 200
        assert r["status"] != "removed" and "UNVERIFIED" not in json.dumps(r)
    assert by["REG-001"]["cites"] == 1268 and by["REG-001"]["lu"] == 2026 and by["REG-001"]["status"] == "active"
    assert by["ELEC-001"]["cites"] is None  # UNVERIFIED citation count
    assert by["ELEC-003"]["lu"] is None  # UNVERIFIED last update
    assert by["REG-002"]["status"] == "superseded"


def test_explorer_json_size_budget(site_fixture, tmp_path, monkeypatch):
    report = bs.build(site_fixture, tmp_path / "a")
    assert report.sizes["explorer"] < bs.EXPLORER_BUDGET_BYTES
    assert not any("explorer.json" in n for n in report.notices)
    monkeypatch.setattr(bs, "EXPLORER_BUDGET_BYTES", 100)
    report = bs.build(site_fixture, tmp_path / "b")
    assert any("api/explorer.json" in n and "over" in n for n in report.notices)


def test_search_json_fields(built):
    _, out = built
    recs = _api(out, "search")
    assert len(recs) == 12
    for r in recs:
        assert set(r) == {"id", "name", "acronym", "key_variables", "strengths", "best_used_for", "maintainer"}
        assert "unverified" not in json.dumps(r).lower()
    assert next(r for r in recs if r["id"] == "REG-001")["key_variables"]


def test_explore_page_has_facet_groups(built):
    _, out = built
    doc = _parse(out / "explore" / "index.html")
    facets = [e.get("data-facet") for e in doc.iter("fieldset")]
    assert facets == ["sub", "type", "unit", "region", "access", "status", "api", "conf", "period"]
    legends = " ".join("".join(e.itertext()) for e in doc.iter("legend"))
    for name in ("Subfield", "Resource type", "Unit class", "Region", "Access level", "Status",
                 "Programmatic access", "Confidence", "Period covered"):
        assert name in legends
    html = (out / "explore" / "index.html").read_text(encoding="utf-8")
    assert "/catalog.csv" in html and "<noscript>" in html and "/domains/REG/" in html
    for js in ("minisearch.min.js", "search.js", "explorer.js"):
        assert (out / "assets" / js).is_file() and f'src="/assets/{js}"' in html


def test_no_cdn_script_tags(built):
    _, out = built
    for f in _html_files(out):
        assert not re.search(r"<script[^>]+src=[\"']?(https?:)?//", f.read_text(encoding="utf-8"))


def test_vendored_minisearch_header():
    head = (Path(bs.__file__).resolve().parent.parent / "site_assets" / "minisearch.min.js").read_text(
        encoding="utf-8")[:400]
    assert "minisearch 7." in head and "MIT" in head and "jsdelivr" in head


def test_explorer_js_never_inserts_markup():
    js = (Path(bs.__file__).resolve().parent.parent / "site_assets" / "explorer.js").read_text(encoding="utf-8")
    assert "innerHTML" not in js and "insertAdjacentHTML" not in js and "document.write" not in js


def test_domain_page_links_into_explorer(built):
    _, out = built
    assert 'href="/explore/#sub=REG"' in _page(out, "domains", "REG")


def test_json_layer_scrubs_unverified_and_keeps_text_raw(site_fixture, tmp_path):
    _edit_rows(site_fixture, {"REG-004": {"name": "Evil <b>x</b> & more; UNVERIFIED tail"}})
    out = tmp_path / "o"
    bs.build(site_fixture, out)
    for name in ("explorer", "search"):
        raw = (out / "api" / f"{name}.json").read_text(encoding="utf-8")
        assert "unverified" not in raw.lower()
        assert next(r for r in json.loads(raw) if r["id"] == "REG-004")["name"] == "Evil <b>x</b> & more"
    assert "<b>x</b>" not in (out / "explore" / "index.html").read_text(encoding="utf-8")


# ---------------------------------------------------------------- explorer fix round 1

def _exp_conf(out):
    html = (out / "explore" / "index.html").read_text(encoding="utf-8")
    return json.loads(re.search(r'id="explorer-config">(.*?)</script>', html, re.S).group(1)), html


def test_explore_page_sort_best_match_and_a11y(built):
    _, out = built
    conf, html = _exp_conf(out)
    assert re.search(r'<option value="best" hidden disabled>Best match</option>', html)
    assert html.index('value="best"') < html.index('value="cites"')
    assert 'id="ex-count" class="ex-count" role="status"' in html and '<h2 id="ex-heading"' in html
    assert 'id="ex-note" role="status"' in html
    assert "Datasets without known years are hidden while a period is set." in html
    assert 'aria-label="Filter and search datasets"' in html
    assert 'aria-label="Search the catalog"' in _page(out, "datasets", "REG-001")
    assert conf["labels"]["region"]["sub-saharan-africa"] == "Sub-Saharan Africa"
    assert conf["labels"]["unit"]["legislator-or-vote"] == "Legislator or vote"


def test_explorer_js_relevance_default_and_hash_rules():
    js = (Path(bs.__file__).resolve().parent.parent / "site_assets" / "explorer.js").read_text(encoding="utf-8")
    assert 'return state.sort || (state.q ? "best" : "cites")' in js  # relevance is the default with a query
    assert 'sort: ""' in js and '!(s.sort === "best" && !s.q)' in js  # hash carries only an explicit sort
    assert "(?!" in js and "^https?" in js  # safeHref rejects protocol-relative URLs
    sj = (Path(bs.__file__).resolve().parent.parent / "site_assets" / "search.js").read_text(encoding="utf-8")
    assert "new Map()" in sj and "fuzzy: 0.2" in sj


def test_facet_labels_cover_real_values():
    import csv as _csv
    p = Path(bs.__file__).resolve().parent.parent / "data" / "derived" / "facets.csv"
    if not p.is_file():
        pytest.skip("no real facets")
    with open(p, encoding="utf-8", newline="") as f:
        rows = list(_csv.DictReader(f))
    regions = {t for r in rows for t in r["region_tags"].split(";") if t and not t.startswith("single-country:")}
    assert regions <= set(bs.REGION_LABELS)
    assert {r["unit_class"] for r in rows if r["unit_class"]} <= set(bs.UNIT_LABELS)


# ---------------------------------------------------------------- machine-readable layer (Task 7)

def test_api_dataset_json_matches_row(built):
    r, out = built
    with (FIXTURE / "data" / "catalog.csv").open(encoding="utf-8", newline="") as fh:
        rows = {x["id"]: x for x in csv.DictReader(fh)}
    files = sorted((out / "api" / "datasets").glob("*.json"))
    assert len(files) == r.pages["dataset"]
    for f in files:
        obj = json.loads(f.read_text(encoding="utf-8"))
        assert len(obj["row"]) == 56
        if bs.WITHHELD in json.dumps(obj, ensure_ascii=False):
            assert "defunct-data.example" not in json.dumps(obj)  # unsafe domains are withheld in the API too
        else:
            assert obj["row"] == rows[f.stem]
        assert set(obj) == {"row", "facets", "top5", "comparison_groups", "merge_systems", "page"}
        assert obj["page"] == f"/datasets/{f.stem}/"
    cat = _api(out, "catalog")
    assert [x["id"] for x in cat] == [x["id"] for x in cat if x["status"] != "removed"]
    assert len(cat) == r.pages["dataset"]
    doms = _api(out, "domains")
    assert len(doms) == 21 and sum(d["count"] for d in doms) == len(cat)
    for g in _api(out, "comparisons"):
        assert all((out / "datasets" / i / "index.html").is_file() for i in g["ids"])
        assert (out / "compare" / g["slug"] / "index.html").is_file()


def test_sitemap_entries_equal_html_pages(built):
    _, out = built
    xml = (out / "sitemap.xml").read_text(encoding="utf-8")
    locs = set(re.findall(r"<loc>([^<]*)</loc>", xml))
    pages = set()
    for f in _html_files(out):
        rel = f.relative_to(out).as_posix()
        if rel == "404.html":
            continue
        pages.add("/" + rel[: -len("index.html")])
    assert locs == pages
    assert xml.count("<lastmod>") == len(pages)


def test_robots_has_sitemap_line(built):
    _, out = built
    t = (out / "robots.txt").read_text(encoding="utf-8")
    assert "User-agent: *" in t and "Allow: /" in t
    assert "Sitemap: /sitemap.xml" in t
    assert any("absolute base_url" in n for n in built[0].notices)


def test_llms_txt_mentions_api_and_license(built):
    _, out = built
    t = (out / "llms.txt").read_text(encoding="utf-8")
    assert t.startswith("# ") and "\n> " in t
    assert "/api/catalog.json" in t and "CC BY 4.0" in t and "MIT" in t
    assert "Cite as" in t and "@" not in t and "PRIVATE" not in t


def test_catalog_csv_copied(built):
    _, out = built
    assert (out / "catalog.csv").read_bytes() == (FIXTURE / "data" / "catalog.csv").read_bytes()


def test_api_catalog_records_have_facets_equal_to_dataset_files(built):
    _, out = built
    cat = _api(out, "catalog")
    assert cat
    for rec in cat:
        per = json.loads((out / "api" / "datasets" / f"{rec['id']}.json").read_text(encoding="utf-8"))
        assert isinstance(rec["facets"], dict) and rec["facets"] == per["facets"]
        assert {k: v for k, v in rec.items() if k != "facets"} == per["row"] and len(per["row"]) == 56


# ---------------------------------------------------------------- explorer hash links (final review I3, M1)

def _explorer_hash_keys():
    js = (bs.ASSET_DIR / "explorer.js").read_text(encoding="utf-8")
    groups = set(re.findall(r'\{\s*key:\s*"(\w+)"', js))
    singles = set(re.findall(r'p\.get\("(\w+)"\)', js))
    return groups | singles


def test_explorer_parses_ids_and_via():
    keys = _explorer_hash_keys()
    assert {"sub", "ids", "via", "q", "from", "to", "sort"} <= keys


def test_every_template_explorer_hash_key_is_known():
    keys = _explorer_hash_keys()
    used = set()
    for t in bs.TEMPLATE_DIR.glob("*.html"):
        for m in re.finditer(r"u\('explore'\) \}\}#([^\"]*)\"", t.read_text(encoding="utf-8")):
            used |= set(re.findall(r"(?:^|&amp;|&)(\w+)=", m.group(1)))
    assert {"sub", "ids", "via"} <= used
    assert used <= keys, used - keys


def test_no_ids_link_when_every_partner_is_listed(built):
    _, out = built
    for d in (out / "datasets").iterdir():
        assert "#ids=" not in (d / "index.html").read_text(encoding="utf-8"), d.name


def test_merge_partner_link_lists_all_partner_ids(site_fixture, tmp_path, monkeypatch):
    monkeypatch.setattr(bs, "PARTNER_LIMIT", 2)
    out = tmp_path / "site"
    bs.build(site_fixture, out)
    doc = _parse(out / "datasets" / "REG-002" / "index.html")
    links = [a.get("href") for a in doc.iter("a") if "#ids=" in (a.get("href") or "")]
    assert len(links) == 1
    path, frag = links[0].split("#", 1)
    assert path == "/explore/"
    from urllib.parse import parse_qs
    q = parse_qs(frag)
    assert q["ids"] == ["CONF-003,REG-001,REG-003,REG-004"]  # every partner, not only the two listed
    assert q["via"] == ["keyed on V-Dem country_id (merge partners of REG-002)"]
    sec = next(s for s in doc.iter("section") if s.get("id") == "merge")
    assert "all 4" in "".join(sec.itertext())
    # pages whose partners all fit in the list carry no link
    assert "#ids=" not in _page(out, "datasets", "ELEC-001")


def test_llms_txt_links_only_existing_pages(built):
    _, out = built
    t = (out / "llms.txt").read_text(encoding="utf-8")
    for target in re.findall(r"\]\((/[^)\s]*)\)", t):
        path = target.split("#", 1)[0]
        f = out / path.lstrip("/")
        assert (f / "index.html").is_file() if path.endswith("/") else f.is_file(), target
    assert "api/datasets/<ID>.json" in t


def test_verification_notes_say_their_figures_are_dated(built):
    _, out = built
    html = _page(out, "datasets", "REG-001")
    seg = html[html.index("<summary>Verification notes</summary>"):]
    seg = seg[:seg.index("</details>")]
    assert "as of the check date" in seg and 'href="#citations"' in seg
