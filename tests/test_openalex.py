import csv
from datetime import date
import gzip
import json
import pathlib

import pytest

import openalex
from citations_common import OpenAlexError, OpenAlexNotFound, title_similarity
from openalex import (WORKS_COLUMNS, WORK_SELECT, accept_candidate, read_works, resolve_row,
                      run_resolve, select_rows, write_works)

FIX = pathlib.Path(__file__).parent / "fixtures"
DOI_WORK = json.loads((FIX / "work_response_doi.json").read_text(encoding="utf-8"))
SEARCH = json.loads((FIX / "search_response.json").read_text(encoding="utf-8"))


class FakeApi:
    def __init__(self, missing=(), raises=None, works=None):
        self.missing = set(missing)
        self.works = {k.lower(): v for k, v in (works or {}).items()}  # doi -> overrides of DOI_WORK
        self.raises = raises
        self.calls = []

    def get(self, path, params=None):
        self.calls.append((path, dict(params or {})))
        if self.raises:
            raise self.raises
        if path.startswith("/works/https://doi.org/"):
            doi = path[len("/works/https://doi.org/"):]
            if doi in self.missing:
                raise OpenAlexNotFound(f"404 for {path}")
            work = dict(DOI_WORK)
            work["doi"] = "https://doi.org/" + doi
            work.update(self.works.get(doi, {}))
            return work
        if path == "/works" and "search" in (params or {}):
            return SEARCH
        raise AssertionError(f"unexpected call {path}")


@pytest.fixture
def fake_api():
    return FakeApi()


def mkrow(**kw):
    base = {"id": "CONF-001", "name": "Some Dataset", "primary_domain": "CONF", "resource_type": "1",
            "status": "active", "doi": "", "citation": ""}
    base.update(kw)
    return base


ART = "10.1177/00220027211068897"
DAT = "10.7910/DVN/OT14Z9"
TITLE_CIT = 'Lopez-Garcia, Maria, and Jane Smith. 2011. "Measuring Democracy With Expert Surveys." APSR 1: 1-2.'


def test_resolve_doi_article_preferred(fake_api):
    row = mkrow(doi=f"{DAT} (data); {ART} (article)")
    rec = resolve_row(row, fake_api, "2026-11-12")
    assert set(rec) == set(WORKS_COLUMNS)
    assert rec["doi_used"] == ART and rec["doi_kind"] == "article" and rec["match_method"] == "doi"
    assert rec["status"] == "ok" and rec["cited_by_count"] == "958"
    assert json.loads(rec["counts_by_year"])[0] == {"year": 2025, "count": 40}
    assert rec["counts_by_year"] == '[{"year":2025,"count":40},{"year":2024,"count":120}]'
    assert rec["openalex_id"] == "W3000000958" and rec["work_year"] == "2022" and rec["work_type"] == "article"
    assert rec["checked_on"] == "2026-11-12" and rec["match_score"] == ""
    assert rec["note"] == ""  # both DOIs reach the same W: counted once
    assert len(fake_api.calls) == 2  # every DOI is looked up
    path, params = fake_api.calls[-1]
    assert path == f"/works/https://doi.org/{ART}" and params == {"select": WORK_SELECT}


def test_resolve_doi_404_skipped_then_data_doi_is_dataset_only():
    api = FakeApi(missing={ART})
    rec = resolve_row(mkrow(doi=f"{ART}; {DAT.lower()} (dataset v2)"), api, "2026-11-12")
    assert rec["status"] == "dataset_only" and rec["doi_used"] == DAT.lower() and rec["doi_kind"] == "data"
    assert rec["openalex_id"] == "W3000000958" and rec["cited_by_count"] == "958"  # information kept
    assert rec["match_method"] == "doi" and rec["note"] == ""
    assert [c[0] for c in api.calls] == [f"/works/https://doi.org/{ART}", f"/works/https://doi.org/{DAT.lower()}"]


def _w(wid, count, typ="article"):
    return {"id": f"https://openalex.org/{wid}", "cited_by_count": count, "type": typ}


def test_resolve_most_cited_article_wins_with_alternatives_in_note():
    a1, a2 = "10.1111/first", "10.1111/second"
    api = FakeApi(works={a1: _w("W1", 61), a2: _w("W2", 400), DAT: _w("W3", 9000, "dataset")})
    rec = resolve_row(mkrow(doi=f"{a1}; {DAT} (dataset); {a2}"), api, "2026-11-12")
    assert rec["status"] == "ok" and rec["doi_used"] == a2 and rec["doi_kind"] == "article"
    assert rec["openalex_id"] == "W2" and rec["cited_by_count"] == "400" and rec["match_method"] == "doi"
    assert rec["note"] == f"also resolved: {a1} (article, 61); {DAT.lower()} (dataset, 9000)"
    assert len(api.calls) == 3


def test_resolve_article_tie_goes_to_earlier_doi():
    a1, a2 = "10.1111/first", "10.1111/second"
    api = FakeApi(works={a1: _w("W1", 50), a2: _w("W2", 50)})
    rec = resolve_row(mkrow(doi=f"{a1}; {a2}"), api, "2026-11-12")
    assert rec["doi_used"] == a1 and rec["openalex_id"] == "W1"


def test_resolve_article_beats_more_cited_data_record():
    art = "10.1111/article"
    api = FakeApi(works={art: _w("W1", 24), DAT: _w("W2", 61, "dataset")})
    rec = resolve_row(mkrow(doi=f"{DAT} (data); {art}"), api, "2026-11-12")
    assert rec["status"] == "ok" and rec["doi_used"] == art and rec["cited_by_count"] == "24"
    assert rec["note"] == f"also resolved: {DAT.lower()} (dataset, 61)"


def test_resolve_data_dois_only_is_dataset_only_from_most_cited():
    d1, d2 = "10.7910/dvn/aaa", "10.7910/dvn/bbb"
    api = FakeApi(works={d1: _w("W1", 5, "dataset"), d2: _w("W2", 12, "dataset")})
    rec = resolve_row(mkrow(doi=f"{d1} (data); {d2} (dataset)"), api, "2026-11-12")
    assert rec["status"] == "dataset_only" and rec["doi_used"] == d2 and rec["doi_kind"] == "data"
    assert rec["cited_by_count"] == "12" and rec["work_type"] == "dataset" and rec["match_method"] == "doi"
    assert rec["note"] == f"also resolved: {d1} (dataset, 5)"


def test_resolve_article_kind_doi_with_dataset_type_is_dataset():
    ipums = "10.18128/d010.v1"  # IPUMS: a data prefix (R18), and OpenAlex types it as a dataset
    api = FakeApi(works={ipums: _w("W1", 300, "dataset")})
    rec = resolve_row(mkrow(doi=ipums), api, "2026-11-12")
    assert rec["status"] == "dataset_only" and rec["doi_kind"] == "data" and rec["cited_by_count"] == "300"
    art = "10.1111/real"
    api = FakeApi(works={ipums: _w("W1", 300, "dataset"), art: _w("W2", 3)})
    rec = resolve_row(mkrow(doi=f"{ipums}; {art}"), api, "2026-11-12")
    assert rec["status"] == "ok" and rec["doi_used"] == art  # the article wins despite fewer citations


def test_resolve_other_error_midway_is_error():
    class Boom(FakeApi):
        def get(self, path, params=None):
            if path.endswith("second"):
                raise OpenAlexError("giving up")
            return super().get(path, params)
    rec = resolve_row(mkrow(doi="10.1111/first; 10.1111/second"), Boom(), "2026-11-12")
    assert rec["status"] == "error" and "giving up" in rec["note"]


def test_resolve_title_match_of_dataset_type_is_dataset_only():
    class Api(FakeApi):
        def get(self, path, params=None):
            return {"results": [SEARCH["results"][0], {**SEARCH["results"][1], "type": "dataset"}]}
    rec = resolve_row(mkrow(citation=TITLE_CIT), Api(), "2026-11-12")
    assert rec["status"] == "dataset_only" and rec["match_method"].startswith("title") and rec["openalex_id"]


def test_resolve_tries_every_doi_and_skips_404():
    a1, a2 = "10.1111/first", "10.1111/second"
    api = FakeApi(missing={a1})
    rec = resolve_row(mkrow(doi=f"{DAT} (dataset); {a1}; {a2}"), api, "2026-11-12")
    assert rec["doi_used"] == a2 and rec["doi_kind"] == "article" and rec["status"] == "ok"
    assert len(api.calls) == 3


def test_resolve_all_dois_404():
    api = FakeApi(missing={ART, DAT.lower()})
    rec = resolve_row(mkrow(doi=f"{ART}; {DAT} (dataset)", citation=TITLE_CIT), api, "2026-11-12")
    assert rec["status"] == "doi_not_in_openalex" and rec["openalex_id"] == ""
    assert rec["match_method"] == "none" and rec["doi_kind"] == "none"
    assert all(c[0].startswith("/works/https://doi.org/") for c in api.calls)  # no title fallback


def test_resolve_no_title_status(fake_api):
    row = mkrow(doi="UNVERIFIED", citation="World Bank. 2025. World Development Indicators.")
    rec = resolve_row(row, fake_api, "2026-11-12")
    assert rec["status"] == "no_title" and rec["openalex_id"] == "" and fake_api.calls == []


def test_resolve_title_no_year_no_search(fake_api):
    rec = resolve_row(mkrow(citation='Smith, Jane. "Measuring Democracy With Expert Surveys." APSR.'),
                      fake_api, "2026-11-12")
    assert rec["status"] == "no_match" and "no year in citation" in rec["note"] and fake_api.calls == []


def test_resolve_title_path_accepts_and_params(fake_api):
    rec = resolve_row(mkrow(doi="N/A", citation=TITLE_CIT), fake_api, "2026-11-12")
    assert rec["status"] == "ok" and rec["openalex_id"] == "W4000000002"
    assert rec["match_method"] == "title+year+author" and float(rec["match_score"]) == 1.0
    assert rec["doi_kind"] == "none" and rec["doi_used"] == ""
    assert rec["cited_by_count"] == "77"
    assert json.loads(rec["counts_by_year"]) == [{"year": 2024, "count": 9}, {"year": 2023, "count": 8}]
    path, params = fake_api.calls[0]
    assert path == "/works"
    assert params == {"search": "Measuring Democracy With Expert Surveys.", "filter": "publication_year:2010-2012",
                      "per-page": 5, "select": WORK_SELECT}


def test_resolve_title_no_match_when_surname_wrong(fake_api):
    cit = 'Nobody, Nina. 2011. "Measuring Democracy With Expert Surveys." APSR.'
    rec = resolve_row(mkrow(citation=cit), fake_api, "2026-11-12")
    assert rec["status"] == "no_match" and rec["openalex_id"] == ""


BASE_TITLE = "base title for the thresholds " + "ab" * 50


def _p(**kw):
    p = {"title": BASE_TITLE, "year": 2011, "surname": "Smith"}
    p.update(kw)
    return p


def _variant(title, k):
    """Replace k characters near the end with a digit to lower similarity."""
    chars = list(title)
    for i in range(k):
        chars[len(chars) - 1 - 3 * i] = "0"
    return "".join(chars)


def _cand(title, year=2011, author="Jane Smith"):
    return {"display_name": title, "publication_year": year,
            "authorships": [{"author": {"display_name": author}}]}


def test_accept_candidate_thresholds():
    p = _p()
    base = p["title"]
    cand_exact = _cand(base)
    cand_sim_096 = _cand(_variant(base, 4))
    cand_sim_093 = _cand(_variant(base, 5))
    cand_sim_090 = _cand(_variant(base, 7))
    assert title_similarity(base, cand_sim_096["display_name"]) >= 0.95
    assert 0.92 <= title_similarity(base, cand_sim_093["display_name"]) < 0.95
    assert title_similarity(base, cand_sim_090["display_name"]) < 0.92
    assert accept_candidate(p, cand_exact) == (True, 1.0, "title+year+author")
    assert accept_candidate(p, _cand(base, year=2013))[0] is False       # year off by two
    assert accept_candidate(p, _cand(base, year=2012))[0] is True        # year off by one
    assert accept_candidate(p, cand_sim_090)[0] is False
    assert accept_candidate(p, _cand(base, author="Jane Jones"))[0] is False
    assert accept_candidate(p, cand_sim_093)[0] is True                  # 0.92 enough with surname
    ns = {**p, "surname": None}
    assert accept_candidate(ns, cand_sim_093)[0] is False                # 0.95 needed without surname
    ok, sim, method = accept_candidate(ns, cand_sim_096)
    assert ok is True and method == "title+year" and sim >= 0.95


def test_accept_candidate_guards_empty_titles():
    assert accept_candidate({"title": "!!!", "year": 2011, "surname": None}, _cand("???"))[0] is False
    assert accept_candidate({"title": "", "year": 2011, "surname": "Smith"}, _cand(""))[0] is False
    assert accept_candidate({"title": "A Real Title", "year": None, "surname": None},
                            _cand("A Real Title"))[0] is False
    assert accept_candidate(_p(), {"display_name": None, "publication_year": None})[0] is False


def test_resolve_error_status():
    api = FakeApi(raises=OpenAlexError("giving up after 6 attempts"))
    rec = resolve_row(mkrow(doi=ART), api, "2026-11-12")
    assert rec["status"] == "error" and "OpenAlexError" in rec["note"] and "giving up" in rec["note"]
    rec2 = resolve_row(mkrow(citation=TITLE_CIT), api, "2026-11-12")
    assert rec2["status"] == "error"


def test_select_rows_filters():
    rows = [mkrow(id="A-1"), mkrow(id="A-2", resource_type="4"), mkrow(id="A-3", status="removed"),
            mkrow(id="B-1", primary_domain="REG", resource_type="3"),
            mkrow(id="B-2", primary_domain="REG", status="superseded by B-1"), mkrow(id="B-3", resource_type="5")]
    assert [r["id"] for r in select_rows(rows, None, None)] == ["A-1", "B-1", "B-2"]
    assert [r["id"] for r in select_rows(rows, ["B-1", "A-2"], None)] == ["B-1"]
    assert [r["id"] for r in select_rows(rows, None, "REG")] == ["B-1", "B-2"]


def _write_catalog(path, rows):
    cols = ["id", "name", "primary_domain", "resource_type", "status", "doi", "citation"]
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def test_works_roundtrip_sorted(tmp_path, monkeypatch):
    monkeypatch.setattr(openalex, "CITATIONS", tmp_path / "citations")
    recs = {i: {**{c: "" for c in WORKS_COLUMNS}, "id": i, "name": 'Ég, "q"'} for i in ("B-2", "A-1")}
    write_works(recs)
    text = (tmp_path / "citations" / "openalex_works.csv").read_text(encoding="utf-8")
    assert text.splitlines()[0] == ",".join(WORKS_COLUMNS)
    assert read_works() == dict(sorted(recs.items()))
    assert list(read_works()) == ["A-1", "B-2"]
    monkeypatch.setattr(openalex, "CITATIONS", tmp_path / "empty")
    assert read_works() == {}


def test_cache_skips_recent_ok(tmp_path, monkeypatch):
    monkeypatch.setattr(openalex, "CITATIONS", tmp_path / "citations")
    cat = tmp_path / "catalog.csv"
    _write_catalog(cat, [mkrow(id="CONF-001", doi=ART), mkrow(id="CONF-002", doi=ART),
                         mkrow(id="CONF-003", doi=ART)])
    old = {**{c: "" for c in WORKS_COLUMNS}, "id": "CONF-001", "status": "ok", "checked_on": "2026-11-02",
           "cited_by_count": "5", "openalex_id": "Wold", "input_hash": openalex.input_hash(mkrow(doi=ART))}
    stale = {**old, "id": "CONF-002", "checked_on": "2026-09-01"}
    err = {**old, "id": "CONF-003", "status": "error", "checked_on": "2026-11-11"}
    write_works({r["id"]: r for r in (old, stale, err)})
    api = FakeApi()
    run_resolve(api, catalog_path=cat, today="2026-11-12")
    assert len(api.calls) == 2  # CONF-002 (stale) and CONF-003 (not ok) re-queried
    works = read_works()
    assert works["CONF-001"]["cited_by_count"] == "5"
    assert works["CONF-002"]["cited_by_count"] == "958" and works["CONF-003"]["status"] == "ok"
    api2 = FakeApi()
    run_resolve(api2, catalog_path=cat, today="2026-11-12", force=True)
    assert len(api2.calls) == 3
    assert read_works()["CONF-001"]["cited_by_count"] == "958"


def test_main_resolve_wiring(tmp_path, monkeypatch):
    monkeypatch.setattr(openalex, "CITATIONS", tmp_path / "citations")
    monkeypatch.setattr(openalex, "CATALOG", tmp_path / "catalog.csv")
    _write_catalog(tmp_path / "catalog.csv",
                   [mkrow(id="CONF-001", doi=ART), mkrow(id="REG-001", primary_domain="REG", doi=ART)])
    made = {}

    class Sess(FakeApi):
        def __init__(self, mailto=None, api_key=None):
            super().__init__()
            made["mailto"] = mailto
            made["api_key"] = api_key

    monkeypatch.setattr(openalex, "OpenAlexSession", Sess)
    assert openalex.main(["resolve", "--domain", "REG"]) == 0
    assert made["mailto"] is None and list(read_works()) == ["REG-001"]
    assert openalex.main(["resolve", "--ids", "CONF-001", "--mailto"]) == 0
    assert made["mailto"] == openalex.AUTHOR["emails"][0]


# ---------------------------------------------------------------- harvest
from openalex import (CITES_SELECT, CITING_COLUMNS, append_citing, drop_partial, harvest_work,  # noqa: E402
                      load_state, read_citing, run_harvest, save_state)

PAGE1 = json.loads((FIX / "cites_page1.json").read_text(encoding="utf-8"))
PAGE2 = json.loads((FIX / "cites_page2.json").read_text(encoding="utf-8"))
EMPTY = {"meta": {"count": 0, "next_cursor": None}, "results": []}


class CiteApi:
    """Serves the two fixture pages for any work; optionally raises on the second request."""

    def __init__(self, empty=False, kill_after_page1=False, fail_works=()):
        self.empty, self.kill, self.fail_works = empty, kill_after_page1, set(fail_works)
        self.calls = []

    def get(self, path, params=None):
        params = dict(params or {})
        self.calls.append((path, params))
        assert path == "/works"
        w = params["filter"].split(":", 1)[1]
        if w in self.fail_works:
            raise OpenAlexError("boom")
        if self.empty:
            return EMPTY
        if params["cursor"] == "*":
            return PAGE1
        if self.kill:
            raise OpenAlexError("connection died")
        assert params["cursor"] == "CUR2"
        return PAGE2


@pytest.fixture
def tmp_citations(tmp_path, monkeypatch):
    monkeypatch.setattr(openalex, "CITATIONS", tmp_path / "citations")
    return tmp_path / "citations"


@pytest.fixture
def cite_api():
    return CiteApi()


def _seed_works(*specs):
    """specs: (catalog_id, W, domain[, status])"""
    recs = {}
    for spec in specs:
        cid, w, dom = spec[:3]
        recs[cid] = {**{c: "" for c in WORKS_COLUMNS}, "id": cid, "primary_domain": dom,
                     "openalex_id": w, "status": spec[3] if len(spec) > 3 else "ok"}
    write_works(recs)


def test_harvest_follows_cursor(cite_api):
    rows, trunc = harvest_work("W1", cite_api)
    assert len(rows) == 3 and trunc is False
    assert rows[0]["subfield"] == "Political Science and International Relations"
    assert rows[0] == {"openalex_id": "W1", "citing_id": "W101", "citing_year": "2020",
                       "citing_type": "article", "field": "Social Sciences",
                       "subfield": "Political Science and International Relations"}
    assert rows[2]["field"] == "" and rows[2]["subfield"] == ""  # no primary_topic
    assert cite_api.calls[0] == ("/works", {"filter": "cites:W1", "per-page": 200, "cursor": "*",
                                            "select": CITES_SELECT})
    assert cite_api.calls[1][1]["cursor"] == "CUR2"


def test_harvest_cap_marks_truncated(cite_api):
    rows, trunc = harvest_work("W1", cite_api, max_citing=2)
    assert len(rows) == 2 and trunc
    assert len(cite_api.calls) == 1  # stopped without fetching page 2
    rows3, trunc3 = harvest_work("W1", CiteApi(), max_citing=3)
    assert len(rows3) == 3 and trunc3 is False  # cap equal to total is not truncation


def test_harvest_zero_citing_marks_done(tmp_citations):
    _seed_works(("CONF-009", "W9", "CONF"))
    api = CiteApi(empty=True)
    assert run_harvest(api, today="2026-11-12") == 0
    st = load_state()
    assert st["W9"] == {"done": True, "n": 0, "checked_on": "2026-11-12", "truncated": False,
                        "ids": ["CONF-009"]}
    assert len(api.calls) == 1
    assert run_harvest(api, today="2026-11-13") == 0
    assert len(api.calls) == 1  # fresh and done: no api call
    run_harvest(api, today="2026-11-13", force=True)
    assert len(api.calls) == 2


def test_resume_drops_partial_rows(tmp_citations):
    _seed_works(("CONF-001", "W1", "CONF"))
    # simulate an append interrupted after the not-done marker was saved
    save_state({"W1": {"done": False, "n": 3, "checked_on": "2026-11-12", "truncated": False,
                       "ids": ["CONF-001"]}})
    append_citing([{"catalog_id": "CONF-001", "openalex_id": "W1", "citing_id": "W101", "citing_year": "2020",
                    "citing_type": "article", "field": "", "subfield": ""}])
    assert run_harvest(CiteApi(kill_after_page1=True), today="2026-11-12") == 1
    assert "W1" not in load_state() or not load_state()["W1"].get("done")
    assert run_harvest(CiteApi(), today="2026-11-12") == 0
    got = list(read_citing())
    assert sorted(r["citing_id"] for r in got) == ["W101", "W102", "W103"]
    assert load_state()["W1"] == {"done": True, "n": 3, "checked_on": "2026-11-12", "truncated": False,
                                  "ids": ["CONF-001"]}


def test_append_and_read_roundtrip_utf8(tmp_citations):
    rows = [{"catalog_id": "REG-001", "openalex_id": "W1", "citing_id": "W5", "citing_year": "2020",
             "citing_type": "article", "field": "Sciences sociales", "subfield": 'Économie, "politique"'},
            {"catalog_id": "REG-001", "openalex_id": "W2", "citing_id": "W6", "citing_year": "",
             "citing_type": "book", "field": "", "subfield": "政治学"}]
    append_citing(rows[:1])
    append_citing(rows[1:])
    with gzip.open(tmp_citations / "citing_works.csv.gz", "rt", encoding="utf-8", newline="") as fh:
        text = fh.read()
    assert text.count(",".join(CITING_COLUMNS)) == 1  # header once
    assert list(read_citing()) == rows
    drop_partial("W1")
    assert list(read_citing()) == rows[1:]
    drop_partial("W2")
    assert list(read_citing()) == []


def test_read_citing_missing_file(tmp_citations):
    assert list(read_citing()) == []
    drop_partial("W1")  # no file: no-op
    assert load_state() == {}


def test_state_atomic_roundtrip(tmp_citations):
    save_state({"W1": {"done": True, "n": 1, "checked_on": "2026-11-12", "truncated": False}})
    assert load_state()["W1"]["n"] == 1
    assert sorted(p.name for p in tmp_citations.iterdir()) == ["harvest_state.json"]  # no temp left over


def test_harvest_filters_and_shared_work(tmp_citations):
    _seed_works(("REG-001", "W1", "REG"), ("REG-002", "W1", "REG"), ("CONF-001", "W2", "CONF"),
                ("CONF-002", "", "CONF", "no_match"), ("CONF-003", "W3", "CONF", "error"))
    api = CiteApi()
    assert run_harvest(api, domain="REG", today="2026-11-12") == 0
    assert len(api.calls) == 2  # W1 harvested once (two pages) for two catalog rows
    got = list(read_citing())
    assert len(got) == 6 and {r["catalog_id"] for r in got} == {"REG-001", "REG-002"}
    assert set(load_state()) == {"W1"} and load_state()["W1"]["ids"] == ["REG-001", "REG-002"]
    run_harvest(CiteApi(), ids=["CONF-001", "CONF-002", "CONF-003"], today="2026-11-12")
    assert set(load_state()) == {"W1", "W2"}  # only status ok rows


def test_harvest_error_continues_and_exit_code(tmp_citations, capsys):
    _seed_works(("A-1", "W1", "A"), ("A-2", "W2", "A"))
    api = CiteApi(fail_works={"W1"})
    assert run_harvest(api, today="2026-11-12") == 1
    assert "W1" in capsys.readouterr().out
    assert "W1" not in load_state() and load_state()["W2"]["done"] is True
    assert {r["openalex_id"] for r in read_citing()} == {"W2"}


def test_harvest_max_citing_records_truncated(tmp_citations):
    _seed_works(("A-1", "W1", "A"))
    run_harvest(CiteApi(), today="2026-11-12", max_citing=2)
    assert load_state()["W1"] == {"done": True, "n": 2, "checked_on": "2026-11-12", "truncated": True,
                                  "ids": ["A-1"]}


def test_state_freshness_window(tmp_citations):
    _seed_works(("A-1", "W1", "A"))
    save_state({"W1": {"done": True, "n": 0, "checked_on": "2026-10-13", "truncated": False, "ids": ["A-1"]}})
    api = CiteApi(empty=True)
    run_harvest(api, today="2026-11-12")  # exactly 30 days: fresh
    assert api.calls == []
    run_harvest(api, today="2026-11-13")  # 31 days: stale
    assert len(api.calls) == 1
    save_state({"W1": {"done": True, "n": 0, "checked_on": "2026-12-01", "truncated": False, "ids": ["A-1"]}})
    run_harvest(api, today="2026-11-13")  # future-dated: not fresh
    assert len(api.calls) == 2


def test_fresh_rejects_future_date():
    rec = {"status": "ok", "checked_on": "2026-11-20"}
    assert openalex._fresh(rec, "2026-11-12") is False
    assert openalex._fresh({**rec, "checked_on": "2026-11-12"}, "2026-11-12") is True
    assert openalex._fresh({**rec, "checked_on": "2026-10-13"}, "2026-11-12") is True
    assert openalex._fresh({**rec, "checked_on": "2026-10-12"}, "2026-11-12") is False


def test_write_works_atomic_and_saves_on_interrupt(tmp_citations, tmp_path):
    cat = tmp_path / "catalog.csv"
    _write_catalog(cat, [mkrow(id="CONF-001", doi=ART), mkrow(id="CONF-002", doi=ART)])

    class Dies(FakeApi):
        def get(self, path, params=None):
            if self.calls:
                raise KeyboardInterrupt
            return super().get(path, params)

    with pytest.raises(KeyboardInterrupt):
        run_resolve(Dies(), catalog_path=cat, today="2026-11-12")
    assert list(read_works()) == ["CONF-001"]  # completed work survived
    assert sorted(p.name for p in tmp_citations.iterdir()) == ["openalex_works.csv"]


def test_main_harvest_wiring(tmp_citations, monkeypatch):
    _seed_works(("A-1", "W1", "A"))
    made = {}

    class Sess(CiteApi):
        def __init__(self, mailto=None, api_key=None):
            super().__init__()
            made["mailto"] = mailto
            made["api_key"] = api_key

    monkeypatch.setattr(openalex, "OpenAlexSession", Sess)
    assert openalex.main(["harvest", "--max-citing", "2"]) == 0
    assert made["mailto"] is None and load_state()["W1"]["truncated"] is True
    monkeypatch.setattr(openalex, "OpenAlexSession", lambda mailto=None, api_key=None: CiteApi(fail_works={"W1"}))
    assert openalex.main(["harvest", "--force", "--ids", "A-1", "--domain", "A"]) == 1


def test_ids_filter_with_shared_work_writes_all_catalog_ids(tmp_citations):
    _seed_works(("REG-001", "W1", "REG"), ("REG-002", "W1", "REG"))
    assert run_harvest(CiteApi(), ids=["REG-001"], today="2026-11-12") == 0
    got = list(read_citing())
    assert len(got) == 6 and {r["catalog_id"] for r in got} == {"REG-001", "REG-002"}
    api = CiteApi()
    run_harvest(api, ids=["REG-002"], today="2026-11-12")
    assert api.calls == []  # already done for both ids
    run_harvest(api, ids=["REG-001"], today="2026-11-12", force=True)
    got = list(read_citing())
    assert len(got) == 6 and {r["catalog_id"] for r in got} == {"REG-001", "REG-002"}


def test_new_catalog_id_on_done_work_triggers_reharvest(tmp_citations):
    _seed_works(("REG-001", "W1", "REG"))
    run_harvest(CiteApi(), today="2026-11-12")
    _seed_works(("REG-001", "W1", "REG"), ("REG-002", "W1", "REG"))
    api = CiteApi()
    run_harvest(api, today="2026-11-12")
    assert len(api.calls) == 2
    got = list(read_citing())
    assert len(got) == 6 and load_state()["W1"]["ids"] == ["REG-001", "REG-002"]


def test_drop_partial_not_called_for_fresh_work(tmp_citations, monkeypatch):
    _seed_works(("A-1", "W1", "A"), ("A-2", "W2", "A"))
    calls = []
    monkeypatch.setattr(openalex, "drop_partial", lambda w, append_rows=None: calls.append(set(w)))
    run_harvest(CiteApi(), today="2026-11-12")
    assert calls == []
    run_harvest(CiteApi(), today="2026-11-12", force=True)
    assert calls == [{"W1", "W2"}]  # refresh of existing rows drops them, in one rewrite


def test_failed_refresh_keeps_old_rows_and_state(tmp_citations):
    _seed_works(("A-1", "W1", "A"))
    run_harvest(CiteApi(), today="2026-11-12")
    before_state, before_rows = load_state(), list(read_citing())
    assert run_harvest(CiteApi(fail_works={"W1"}), today="2026-11-13", force=True) == 1
    assert load_state() == before_state and list(read_citing()) == before_rows
    assert run_harvest(CiteApi(kill_after_page1=True), today="2026-11-13", force=True) == 1
    assert load_state() == before_state and list(read_citing()) == before_rows


def test_truncated_gzip_tolerated_and_repaired(tmp_citations):
    def row(w, c):
        return {"catalog_id": "A-1", "openalex_id": w, "citing_id": c, "citing_year": "2020",
                "citing_type": "article", "field": "", "subfield": ""}
    append_citing([row("W1", "W5")])
    append_citing([row("W2", "W6"), row("W2", "W7")])
    path = tmp_citations / "citing_works.csv.gz"
    path.write_bytes(path.read_bytes()[:-30])  # killed mid-append
    got = list(read_citing())  # must not raise EOFError
    assert got[0] == row("W1", "W5") and len(got) < 4
    assert all(r["citing_id"] in {"W5", "W6", "W7"} for r in got)
    drop_partial("W9")  # rewrite repairs the file
    assert list(read_citing()) == got
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        fh.read()  # no EOFError any more


def test_interrupted_append_repaired_before_fresh_work(tmp_citations):
    def row(w, c):
        return {"catalog_id": "A-1", "openalex_id": w, "citing_id": c, "citing_year": "2020",
                "citing_type": "article", "field": "", "subfield": ""}
    _seed_works(("A-1", "W1", "A"), ("A-2", "W2", "A"), ("A-3", "W7", "A"))  # W7 stays referenced: not pruned
    append_citing([row("W7", "W70")])
    append_citing([row("W2", "W20"), row("W2", "W21")])
    path = tmp_citations / "citing_works.csv.gz"
    path.write_bytes(path.read_bytes()[:-30])  # W2's append was killed
    save_state({"W2": {"done": False, "n": 2, "checked_on": "2026-11-12", "truncated": False, "ids": ["A-2"]},
                "W7": {"done": True, "n": 1, "checked_on": "2026-11-12", "truncated": False, "ids": ["A-1"]}})
    assert run_harvest(CiteApi(), ids=["A-1"], today="2026-11-12") == 0  # only W1 selected, W2 untouched
    got = list(read_citing())
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        fh.read()  # whole file decodes: no corrupt middle
    assert not [r for r in got if r["openalex_id"] == "W2"]
    assert [r["citing_id"] for r in got if r["openalex_id"] == "W1"] == ["W101", "W102", "W103"]
    assert [r["citing_id"] for r in got if r["openalex_id"] == "W7"] == ["W70"]
    assert "W2" not in load_state() and load_state()["W1"]["done"] is True


def test_drop_partial_accepts_set(tmp_citations):
    rows = [{"catalog_id": "A-1", "openalex_id": w, "citing_id": "W5", "citing_year": "", "citing_type": "",
             "field": "", "subfield": ""} for w in ("W1", "W2", "W3")]
    append_citing(rows)
    drop_partial({"W1", "W3"})
    assert [r["openalex_id"] for r in read_citing()] == ["W2"]


# ---------------------------------------------------------------- patch
from citations_common import relabel_count  # noqa: E402
from openalex import build_patch, build_relabel_patch, main  # noqa: E402

TODAY = "2026-11-12"


def _prow(i, name, cc, dom="REG"):
    return {"id": i, "name": name, "primary_domain": dom, "resource_type": "1", "status": "active",
            "citation_count": cc}


def _pwork(i, count, status="ok", dom="REG"):
    return {"id": i, "primary_domain": dom, "status": status, "cited_by_count": str(count)}


def test_relabel_count():
    assert relabel_count("1268 (checked 2026-11)") == "1268 (Google Scholar, checked 2026-11)"
    assert relabel_count("UNVERIFIED") == "UNVERIFIED"


def test_build_patch_fills_unverified_only():
    rows = [_prow("REG-001", "A", "UNVERIFIED"), _prow("REG-002", "B", ""),
            _prow("REG-003", "C", "81 (Google Scholar, checked 2026-11)")]
    works = {"REG-001": _pwork("REG-001", 958), "REG-002": _pwork("REG-002", 5),
             "REG-003": _pwork("REG-003", 99)}
    recs = build_patch(rows, works, TODAY)
    assert [r["name"] for r in recs] == ["A", "B"]
    assert recs[0] == {"name": "A", "primary_domain": "REG", "resource_type": "1",
                       "last_verified_on": TODAY, "citation_count": "958 (OpenAlex, checked 2026-11)"}
    assert all("unverified_notes" not in r for r in recs)


def test_build_patch_skips_non_ok_works():
    rows = [_prow("REG-001", "A", ""), _prow("REG-002", "B", ""), _prow("REG-003", "C", "")]
    works = {"REG-001": _pwork("REG-001", 3, status="no_match"), "REG-002": _pwork("REG-002", 4, "error")}
    assert build_patch(rows, works, TODAY) == []


def test_build_patch_rejects_duplicate_names():
    rows = [_prow("REG-001", "Polity V", ""), _prow("REG-002", "POLITY v", "")]
    with pytest.raises(ValueError, match="REG-001.*REG-002"):
        build_patch(rows, {"REG-001": _pwork("REG-001", 1)}, TODAY)


def test_build_relabel_patch_only_legacy():
    rows = [_prow("REG-001", "A", "10 (checked 2026-11)"), _prow("REG-002", "B", "UNVERIFIED"),
            _prow("REG-003", "C", "4 (OpenAlex, checked 2026-11)"),
            _prow("REG-004", "D", "102 (checked 2026-11) for X 2017; 5 (checked 2026-11) for Z")]
    recs = build_relabel_patch(rows, TODAY)
    assert [r["name"] for r in recs] == ["A", "D"]
    assert recs[0]["citation_count"] == "10 (Google Scholar, checked 2026-11)"
    assert recs[1]["citation_count"].count("Google Scholar") == 2
    assert set(recs[0]) == {"name", "primary_domain", "resource_type", "last_verified_on", "citation_count"}


def _patch_env(tmp_path, monkeypatch, rows, works_specs):
    monkeypatch.setattr(openalex, "RESEARCH_DIR", tmp_path / "research")
    cat = tmp_path / "catalog.csv"
    cols = ["id", "name", "primary_domain", "resource_type", "status", "citation_count"]
    with open(cat, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    monkeypatch.setattr(openalex, "CATALOG", cat)
    recs = {}
    for cid, dom, status, count in works_specs:
        recs[cid] = {**{c: "" for c in WORKS_COLUMNS}, "id": cid, "primary_domain": dom, "status": status,
                     "cited_by_count": str(count), "openalex_id": "W" + cid[-1]}
    write_works(recs)


def test_patch_refuses_on_error_without_allow_partial(tmp_citations, tmp_path, monkeypatch, capsys):
    _patch_env(tmp_path, monkeypatch, [_prow("REG-001", "A", ""), _prow("REG-002", "B", "")],
               [("REG-001", "REG", "ok", 5), ("REG-002", "REG", "error", 0)])
    with pytest.raises(SystemExit) as ei:
        main(["patch"])
    assert ei.value.code == 2
    assert not (tmp_path / "research").exists()
    assert "allow-partial" in capsys.readouterr().err
    # error outside the --ids scope does not block
    assert main(["patch", "--ids", "REG-001"]) == 0


def test_patch_writes_files_with_allow_partial(tmp_citations, tmp_path, monkeypatch, capsys):
    _patch_env(tmp_path, monkeypatch,
               [_prow("REG-001", "Ä", ""), _prow("REG-002", "B", "UNVERIFIED"),
                _prow("REG-003", "C", "9 (checked 2026-11)")],
               [("REG-001", "REG", "ok", 5), ("REG-002", "REG", "error", 0)])
    assert main(["patch", "--allow-partial"]) == 0
    out = capsys.readouterr().out
    assert "python scripts/merge_records.py" in out
    today = date.today().isoformat()
    oa = json.loads((tmp_path / "research" / "zz_patch_openalex.json").read_text(encoding="utf-8"))
    assert [r["name"] for r in oa] == ["Ä"]
    gs = json.loads((tmp_path / "research" / f"zz_patch_gs_relabel_{today}.json").read_text(encoding="utf-8"))
    assert gs[0]["citation_count"] == "9 (Google Scholar, checked 2026-11)"


def test_patch_duplicate_name_out_of_scope_exits_2(tmp_citations, tmp_path, monkeypatch, capsys):
    _patch_env(tmp_path, monkeypatch, [_prow("REG-001", "Polity V", ""), _prow("REG-002", "POLITY v", "")],
               [("REG-001", "REG", "ok", 5)])
    with pytest.raises(SystemExit) as ei:
        main(["patch", "--ids", "REG-001"])
    assert ei.value.code == 2
    err = capsys.readouterr().err
    assert "REG-001" in err and "REG-002" in err and "Traceback" not in err
    assert not (tmp_path / "research").exists()


def test_build_relabel_patch_rejects_duplicate_names():
    rows = [_prow("REG-001", "Polity V", "1 (checked 2026-11)"), _prow("REG-002", "POLITY v", "")]
    with pytest.raises(ValueError, match="REG-001.*REG-002"):
        build_relabel_patch(rows, TODAY)


# ---------------------------------------------------------------- report
from openalex import build_report, note_counts  # noqa: E402


def test_note_counts_parses_both_sources():
    assert note_counts("OpenAlex cited_by_count 1,234 as of 2026; Crossref is-referenced-by-count 99") == {
        "OpenAlex": 1234, "Crossref": 99}
    assert note_counts("OpenAlex cited_by_count is 57; Crossref is-referenced-by-count of 8,001") == {
        "OpenAlex": 57, "Crossref": 8001}
    assert note_counts("OpenAlex cited_by_count 10; OpenAlex cited_by_count 20") == {"OpenAlex": 10}
    for none in ("nothing here", "", None, "OpenAlex lists about 57 citing", "Crossref count: 8,001"):
        assert note_counts(none) == {}


def test_note_counts_ignores_http_status_years_and_doi_digits():
    assert note_counts("OpenAlex returned HTTP 429; Crossref is-referenced-by-count 83 for Hammerschmid et al. 2019") == {
        "Crossref": 83}
    assert note_counts("Crossref is-referenced-by-count for 10.1111/puar.12368 is 116") == {"Crossref": 116}
    assert note_counts("Governance Report 2014 volume not found in Crossref (issued 2026-02-16)") == {}


def test_note_counts_matches_doi_specific_counts():
    n1 = "OpenAlex cited_by_count 1248 for 10.2307/2657374 plus 479 for the duplicate record"
    assert note_counts(n1) == {"OpenAlex": 1248}
    assert note_counts(n1, "10.2307/2657374") == {"OpenAlex": 1248}
    assert note_counts(n1, "10.9999/other") == {}
    n2 = "Crossref is-referenced-by-count 222 for 10.1093/pan/mpm009 and 73 for 10.1086/694846"
    assert note_counts(n2, "10.1093/PAN/mpm009") == {"Crossref": 222}
    assert note_counts(n2, "10.1086/694846") == {"Crossref": 73}
    assert note_counts(n2, "10.1/none") == {}
    assert note_counts(n2) == {"Crossref": 222}
    assert note_counts("Crossref is-referenced-by-count for 10.1111/a.1 is 5", "10.1111/a.2") == {}
    assert note_counts("OpenAlex cited_by_count 9", "10.1/x") == {"OpenAlex": 9}  # undated by DOI: always kept


def _rwork(i, count, status="ok", method="doi", name=None, note="", doi_used=""):
    return {"doi_used": doi_used, "id": i, "name": name or f"Name {i}", "primary_domain": "REG", "openalex_id": "W" + i[-3:],
            "match_method": method, "cited_by_count": "" if count is None else str(count),
            "status": status, "note": note}


def _report(rows=None, works=None, state=None):
    return build_report(rows or [], works or {}, state or {}, TODAY)


def test_report_lists_discrepancies_over_25pct():
    rows = [{"id": "REG-001", "name": "Big gap", "unverified_notes": "OpenAlex cited_by_count 140"},
            {"id": "REG-002", "name": "Small gap", "unverified_notes": "OpenAlex cited_by_count 110"},
            {"id": "REG-003", "name": "Crossref gap", "unverified_notes": "Crossref is-referenced-by-count 40"}]
    works = {"REG-001": _rwork("REG-001", 100), "REG-002": _rwork("REG-002", 100),
             "REG-003": _rwork("REG-003", 100)}
    text = _report(rows, works)
    cons = text.split("## Consistency")[1].split("\n## ")[0]
    assert "REG-001" in cons and "REG-003" in cons and "for a human look" in cons
    look = cons.split("for a human look")[1]
    assert "REG-001" in look and "REG-003" in look and "REG-002" not in look
    assert "REG-002" in cons  # compared, but not flagged


def test_report_consistency_uses_resolved_doi():
    rows = [{"id": "REG-001", "name": "Two DOIs",
             "unverified_notes": "OpenAlex cited_by_count 500 for 10.1000/a plus 100 for 10.1000/b"}]
    works = {"REG-001": _rwork("REG-001", 100, doi_used="10.1000/a")}
    cons = _report(rows, works).split("## Consistency")[1].split("\n## ")[0]
    assert "| OpenAlex | 500 | 100 | 400 |" in cons and "REG-001" in cons.split("for a human look")[1]
    works = {"REG-001": _rwork("REG-001", 100, doi_used="10.1000/c")}
    cons = _report(rows, works).split("## Consistency")[1].split("\n## ")[0]
    assert "REG-001" not in cons


def test_report_sections_present():
    rows = [{"id": "REG-001", "name": "A", "unverified_notes": ""}]
    works = {"REG-001": _rwork("REG-001", 5),
             "REG-002": _rwork("REG-002", None, status="error", method="none", note="OpenAlexError: boom"),
             "REG-003": _rwork("REG-003", None, status="no_match", method="none", name="Lost one"),
             "REG-004": _rwork("REG-004", None, status="doi_not_in_openalex", method="none"),
             "REG-005": _rwork("REG-005", None, status="no_title", method="none")}
    state = {"W001": {"done": True, "n": 7, "truncated": True, "ids": ["REG-001"]},
             "W009": {"done": False, "n": 2, "truncated": False, "ids": ["REG-009"]}}
    text = _report(rows, works, state)
    for head in ("Status", "Match methods", "Top 20", "Consistency", "Harvest", "Errors"):
        assert f"## {head}" in text
    errs = text.split("## Errors")[1]
    assert "REG-002" in errs and "boom" in errs
    assert "Lost one" in text and "REG-004" in text and "REG-005" in text
    harvest = text.split("## Harvest")[1].split("\n## ")[0]
    assert "7" in harvest and "W001" in harvest and "W009" in harvest


def test_report_top20_and_empty():
    works = {f"REG-{n:03d}": _rwork(f"REG-{n:03d}", n) for n in range(1, 26)}
    top = _report(works=works).split("## Top 20")[1].split("\n## ")[0]
    assert "REG-025" in top and "REG-006" in top and "REG-005" not in top
    assert "## Status" in _report()


def test_report_cli_writes_file(tmp_citations, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(openalex, "LOGS", tmp_path / "logs")
    _patch_env(tmp_path, monkeypatch, [_prow("REG-001", "A", "")], [("REG-001", "REG", "ok", 5)])
    assert main(["report"]) == 0
    out = capsys.readouterr().out
    path = tmp_path / "logs" / f"citations_{date.today().isoformat()}.md"
    assert out.startswith(path.read_text(encoding="utf-8").rstrip("\n")) and str(path) in out
    assert "## Status" in out


def test_dump_first_page_writes_only_first_work(tmp_citations):
    _seed_works(("REG-001", "W1", "REG"), ("CONF-001", "W2", "CONF"))
    assert run_harvest(CiteApi(), today="2026-11-12", dump_first_page=True) == 0
    dumps = sorted(p.name for p in tmp_citations.glob("first_page_*.json"))
    assert dumps == ["first_page_W1.json"]
    assert json.loads((tmp_citations / "first_page_W1.json").read_text(encoding="utf-8")) == PAGE1
    run_harvest(CiteApi(), today="2026-11-13", force=True)  # flag off: nothing new
    assert sorted(p.name for p in tmp_citations.glob("first_page_*.json")) == dumps


def test_main_harvest_dump_flag(tmp_citations, monkeypatch):
    _seed_works(("A-1", "W1", "A"))
    monkeypatch.setattr(openalex, "OpenAlexSession", lambda mailto=None, api_key=None: CiteApi())
    assert openalex.main(["harvest", "--dump-first-page"]) == 0
    assert (tmp_citations / "first_page_W1.json").exists()


def _key_wiring(monkeypatch, argv, loaded, capsys):
    _seed_works(("A-1", "W1", "A"))
    made = {}

    class Sess(CiteApi):
        def __init__(self, mailto=None, api_key=None):
            super().__init__()
            made["api_key"] = api_key

    monkeypatch.setattr(openalex, "OpenAlexSession", Sess)
    monkeypatch.setattr(openalex, "load_api_key", lambda: loaded)
    assert openalex.main(argv) == 0
    return made, capsys.readouterr().out


def test_harvest_uses_key_and_prints_source_only(tmp_citations, monkeypatch, capsys):
    made, out = _key_wiring(monkeypatch, ["harvest"], ("sekrit-123", "environment variable OPENALEX_API_KEY"), capsys)
    assert made["api_key"] == "sekrit-123"
    assert "using OpenAlex API key from environment variable OPENALEX_API_KEY" in out
    assert "sekrit-123" not in out


def test_harvest_without_key_prints_notice(tmp_citations, monkeypatch, capsys):
    made, out = _key_wiring(monkeypatch, ["harvest"], (None, "none"), capsys)
    assert made["api_key"] is None
    assert ("no OpenAlex API key found (set OPENALEX_API_KEY or create PRIVATE_openalex_api_key.txt); "
            "cursor paging past the first page will fail") in out


def test_no_api_key_flag_forces_keyless(tmp_citations, monkeypatch, capsys):
    made, out = _key_wiring(monkeypatch, ["harvest", "--no-api-key"], ("sekrit-123", "x"), capsys)
    assert made["api_key"] is None and "sekrit-123" not in out


def test_resolve_key_wiring_and_flag(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(openalex, "CITATIONS", tmp_path / "citations")
    monkeypatch.setattr(openalex, "CATALOG", tmp_path / "catalog.csv")
    _write_catalog(tmp_path / "catalog.csv", [mkrow(id="REG-001", primary_domain="REG", doi=ART)])
    made = {}

    class Sess(FakeApi):
        def __init__(self, mailto=None, api_key=None):
            super().__init__()
            made["api_key"] = api_key

    monkeypatch.setattr(openalex, "OpenAlexSession", Sess)
    monkeypatch.setattr(openalex, "load_api_key", lambda: ("sekrit-123", "PRIVATE_openalex_api_key.txt"))
    assert openalex.main(["resolve", "--force"]) == 0
    out = capsys.readouterr().out
    assert made["api_key"] == "sekrit-123" and "using OpenAlex API key from PRIVATE_openalex_api_key.txt" in out
    assert "sekrit-123" not in out
    assert openalex.main(["resolve", "--force", "--no-api-key"]) == 0
    assert made["api_key"] is None


def test_harvest_401_hint_only_without_key(tmp_citations, monkeypatch, capsys):
    _seed_works(("A-1", "W1", "A"))

    class Fail:
        def get(self, path, params=None):
            raise OpenAlexError("HTTP 401 for /works")

    monkeypatch.setattr(openalex, "OpenAlexSession", lambda mailto=None, api_key=None: Fail())
    monkeypatch.setattr(openalex, "load_api_key", lambda: (None, "none"))
    assert openalex.main(["harvest"]) == 1
    tail = capsys.readouterr().out.split("failed", 1)[1]
    assert "OPENALEX_API_KEY" in tail and "PRIVATE_openalex_api_key.txt" in tail
    monkeypatch.setattr(openalex, "load_api_key", lambda: ("k-1", "env"))
    assert openalex.main(["harvest", "--force"]) == 1
    assert "hint" not in capsys.readouterr().out


@pytest.mark.parametrize("title,cand_title", [
    ("Executive Orders.", "Executive Orders"), ("Open Data.", "Open Data"), ("Polls", "Polls"),
    ("Good Neighbor", "Good Neighbors?:"), ("World Development Indicators.", "World Development Indicators"),
    ("Missing Migrants Project Data.", "Missing Migrants Project Data"),
])
def test_short_title_without_author_rejected(title, cand_title):
    p = {"title": title, "year": 2011, "surname": None}
    assert accept_candidate(p, _cand(cand_title))[0] is False


def test_five_word_title_without_author_accepted_and_two_word_with_author():
    t = "Measuring Democracy With Expert Surveys"
    assert accept_candidate({"title": t, "year": 2011, "surname": None}, _cand(t)) == (True, 1.0, "title+year")
    ok, _, m = accept_candidate({"title": "Missing Tariffs.", "year": 2011, "surname": "Teti"},
                                _cand("Missing Tariffs", author="Andrea Teti"))
    assert ok and m == "title+year+author"


def test_resolve_short_title_no_author_note():
    class Api(FakeApi):
        def get(self, path, params=None):
            self.calls.append((path, params))
            return {"results": [_cand("Open Data") | {"id": "https://openalex.org/W1"}]}
    rec = resolve_row(mkrow(citation='2011. "Open Data."'), Api(), "2026-11-12")
    assert rec["status"] == "no_match" and rec["note"] == "title too short for a match without an author"


def test_build_patch_excludes_dataset_only():
    rows = [_prow("REG-001", "A", "UNVERIFIED"), _prow("REG-002", "B", "UNVERIFIED"),
            _prow("REG-003", "C", "UNVERIFIED")]
    works = {"REG-001": _pwork("REG-001", 0, status="dataset_only"),
             "REG-002": _pwork("REG-002", 700, status="dataset_only"),
             "REG-003": _pwork("REG-003", 0)}
    assert [r["name"] for r in build_patch(rows, works, TODAY)] == ["C"]


def test_report_lists_dataset_only_rows():
    works = {"BUR-043": _rwork("BUR-043", 7, status="dataset_only", name="Mendeley thing", doi_used="10.17632/x"),
             "REG-001": _rwork("REG-001", 0, name="Plain ok"),
             "REG-002": _rwork("REG-002", 5)}
    text = _report(works=works)
    assert "data DOI with 0 citations" not in text
    sec = text.split("### dataset record only (not patched) (1)")[1]
    assert text.index("## No identifiable work") < text.index("### dataset record only")
    assert "BUR-043" in sec and "Mendeley thing" in sec and "10.17632/x" in sec and "| 7 |" in sec
    assert "REG-001" not in sec and "REG-002" not in sec
    assert "dataset_only" in text.split("## Status")[1].split("## Match methods")[0]


# ---------------------------------------------------------------- harvest pruning
def _crow(cid, w, c):
    return {"catalog_id": cid, "openalex_id": w, "citing_id": c, "citing_year": "2020",
            "citing_type": "article", "field": "", "subfield": ""}


def _done(cids, n):
    return {"done": True, "n": n, "checked_on": "2026-11-12", "truncated": False, "ids": cids}


def _prune_setup():
    _seed_works(("A-1", "W1", "A"), ("A-2", "W9", "A", "dataset_only"), ("B-1", "W3", "B"))
    append_citing([_crow("A-1", "W1", "W10"), _crow("A-2", "W2", "W20"), _crow("A-2", "W2", "W21"),
                   _crow("B-1", "W3", "W30")])
    save_state({"W1": _done(["A-1"], 1), "W2": _done(["A-2"], 2), "W3": _done(["B-1"], 1)})


def test_harvest_prunes_unreferenced_work(tmp_citations, capsys):
    _prune_setup()  # A-2 used to map to W2 but now has a different (not ok) status
    assert run_harvest(CiteApi(), today="2026-11-12") == 0
    got = list(read_citing())
    assert {r["openalex_id"] for r in got} >= {"W1", "W3"} and "W2" not in {r["openalex_id"] for r in got}
    assert "W2" not in load_state() and {"W1", "W3"} <= set(load_state())
    out = capsys.readouterr().out
    assert "pruned 1 unreferenced work(s), 2 citing row(s)" in out


def test_harvest_prune_is_global_despite_ids_filter(tmp_citations):
    _prune_setup()
    api = CiteApi()
    assert run_harvest(api, ids=["B-1"], today="2026-11-12") == 0
    assert api.calls == []  # B-1's W3 is cached; nothing harvested
    assert {r["openalex_id"] for r in read_citing()} == {"W1", "W3"}
    assert set(load_state()) == {"W1", "W3"}


def test_harvest_prune_skipped_without_ok_works(tmp_citations, capsys):
    for seed in ((), (("A-1", "", "A", "no_match"),)):  # no works file at all, then no ok row
        if seed:
            _seed_works(*seed)
        (tmp_citations / "citing_works.csv.gz").unlink(missing_ok=True)
        append_citing([_crow("A-1", "W1", "W10")])
        save_state({"W1": _done(["A-1"], 1)})
        assert run_harvest(CiteApi(), today="2026-11-12") == 0
        assert [r["openalex_id"] for r in read_citing()] == ["W1"] and set(load_state()) == {"W1"}
        assert "prune skipped, no ok works" in capsys.readouterr().out


def test_harvest_prune_skipped_when_any_works_row_errored(tmp_citations, capsys):
    _seed_works(("A-1", "W1", "A"), ("A-2", "", "A", "error"))
    append_citing([_crow("A-1", "W1", "W10"), _crow("A-2", "W2", "W20")])
    save_state({"W1": _done(["A-1"], 1), "W2": _done(["A-2"], 1)})
    assert run_harvest(CiteApi(), today="2026-11-12") == 0
    assert {r["openalex_id"] for r in read_citing()} == {"W1", "W2"} and set(load_state()) == {"W1", "W2"}
    assert "prune skipped, 1 works row(s) have status error" in capsys.readouterr().out


def test_harvest_no_prune_when_all_referenced(tmp_citations, capsys, monkeypatch):
    _seed_works(("A-1", "W1", "A"))
    append_citing([_crow("A-1", "W1", "W10")])
    save_state({"W1": _done(["A-1"], 1)})
    monkeypatch.setattr(openalex, "drop_partial", lambda ids: pytest.fail("rewrote the file needlessly"))
    assert run_harvest(CiteApi(), today="2026-11-12") == 0
    assert "pruned" not in capsys.readouterr().out


# ---------------------------------------------------------------- R17: N/A / UNVERIFIED doi fields take the title path
@pytest.mark.parametrize("field", [
    "N/A (no dataset DOI; re3data repository record 10.17616/R3C94T)",
    "n/a for the database working paper; related article 10.1017/S0003055416000654",
    "UNVERIFIED (former B2SHARE record 10.23728/b2share.8b3dd38e listed on B2FIND)",
    "  unverified: maybe 10.1111/abc",
])
def test_resolve_na_or_unverified_doi_field_takes_title_path(fake_api, field):  # R17b: no reference-labelled DOI
    rec = resolve_row(mkrow(doi=field, citation=TITLE_CIT), fake_api, "2026-11-12")
    assert [c[0] for c in fake_api.calls] == ["/works"]  # no DOI lookup at all
    assert rec["status"] == "ok" and rec["match_method"] == "title+year+author"
    assert rec["doi_kind"] == "none" and rec["doi_used"] == ""


def test_resolve_na_doi_field_without_title_is_no_title(fake_api):
    rec = resolve_row(mkrow(doi="N/A (repository record 10.17616/R3C94T)", citation="World Bank. 2025. WDI."),
                      fake_api, "2026-11-12")
    assert rec["status"] == "no_title" and fake_api.calls == []


def test_resolve_na_field_with_reference_labelled_dois_uses_the_doi_path():
    a1, a2 = "10.1017/s0003055403000522", "10.1111/j.1540-5907.2009.00426.x"
    api = FakeApi(works={a1: _w("W1", 300), a2: _w("W2", 900)})
    field = ("N/A for the dataset; reference articles 10.1017/S0003055403000522 (Harff 2003) and "
             "10.1111/j.1540-5907.2009.00426.x (Goldstone et al. 2010)")
    rec = resolve_row(mkrow(doi=field, citation=TITLE_CIT), api, "2026-11-12")
    assert rec["status"] == "ok" and rec["match_method"] == "doi" and rec["doi_used"] == a2  # most-cited
    assert [c[0] for c in api.calls] == [f"/works/https://doi.org/{a1}", f"/works/https://doi.org/{a2}"]
    rec = resolve_row(mkrow(doi="N/A (dataset); 10.1016/0261-3794(91)90004-C (reference article)"), FakeApi(),
                      "2026-11-12")
    assert rec["doi_used"] == "10.1016/0261-3794(91)90004-c" and rec["status"] == "ok"


def test_resolve_labelled_doi_field_still_uses_dois(fake_api):
    rec = resolve_row(mkrow(doi=f"{ART} (reference article); {DAT} (dataset)", citation=TITLE_CIT),
                      fake_api, "2026-11-12")
    assert rec["match_method"] == "doi" and rec["doi_used"] == ART
    assert all(c[0].startswith("/works/https://doi.org/") for c in fake_api.calls)


# ---------------------------------------------------------------- R18: non-article types, data prefixes, title ranking
@pytest.mark.parametrize("typ", ["other", "paratext", "dataset"])
def test_resolve_non_article_openalex_types_are_dataset_only(typ):
    doi = "10.1111/thing"
    rec = resolve_row(mkrow(doi=doi), FakeApi(works={doi: _w("W1", 40, typ)}), "2026-11-12")
    assert rec["status"] == "dataset_only" and rec["doi_kind"] == "article" and rec["work_type"] == typ
    art = "10.1111/real"
    rec = resolve_row(mkrow(doi=f"{doi}; {art}"), FakeApi(works={doi: _w("W1", 400, typ), art: _w("W2", 3)}),
                      "2026-11-12")
    assert rec["status"] == "ok" and rec["doi_used"] == art


def test_non_article_types_constant():
    assert openalex.NON_ARTICLE_TYPES == frozenset({"dataset", "other", "paratext"})


def test_resolve_ipums_prefix_is_data_kind():
    ipums = "10.18128/d010.v1"
    rec = resolve_row(mkrow(doi=ipums), FakeApi(works={ipums: _w("W1", 300, "article")}), "2026-11-12")
    assert rec["doi_kind"] == "data" and rec["status"] == "dataset_only"


def _sr(wid, count, typ="article", title="Measuring Democracy With Expert Surveys", author="Maria Lopez-Garcia"):
    return {"id": f"https://openalex.org/{wid}", "display_name": title, "publication_year": 2011, "type": typ,
            "cited_by_count": count, "counts_by_year": [], "authorships": [{"author": {"display_name": author}}]}


class SearchApi(FakeApi):
    def __init__(self, results):
        super().__init__()
        self.results = results

    def get(self, path, params=None):
        self.calls.append((path, params))
        return {"results": self.results}


def test_title_path_picks_most_cited_non_preprint():
    api = SearchApi([_sr("W1", 10), _sr("W2", 500, "preprint"), _sr("W9", 999, title="Something Else"),
                     _sr("W3", 80), _sr("W4", 80)])
    rec = resolve_row(mkrow(citation=TITLE_CIT), api, "2026-11-12")
    assert rec["status"] == "ok" and rec["openalex_id"] == "W3" and rec["cited_by_count"] == "80"  # tie: search order
    assert "W2" in rec["note"] and "W1" in rec["note"] and "W9" not in rec["note"]


def test_title_path_preprint_only_when_nothing_else():
    rec = resolve_row(mkrow(citation=TITLE_CIT), SearchApi([_sr("W2", 500, "preprint")]), "2026-11-12")
    assert rec["status"] == "ok" and rec["openalex_id"] == "W2"


@pytest.mark.parametrize("typ", ["other", "paratext", "dataset"])
def test_title_path_non_article_winner_is_dataset_only(typ):
    # dataset_only only when no accepted candidate is article-type; the most-cited non-article wins then
    rec = resolve_row(mkrow(citation=TITLE_CIT), SearchApi([_sr("W1", 5, "paratext"), _sr("W2", 50, typ)]),
                      "2026-11-12")
    assert rec["status"] == "dataset_only" and rec["openalex_id"] == "W2"


# ---------------------------------------------------------------- input hash in the resolve cache
def test_input_hash_is_last_works_column_and_written_on_resolve(fake_api):
    assert WORKS_COLUMNS[-1] == "input_hash"
    row = mkrow(doi=ART, citation=TITLE_CIT)
    rec = resolve_row(row, fake_api, "2026-11-12")
    assert rec["input_hash"] == openalex.input_hash(row) and len(rec["input_hash"]) == 12
    import hashlib
    assert rec["input_hash"] == hashlib.sha1(f"{ART}\n{TITLE_CIT}".encode("utf-8")).hexdigest()[:12]


def test_cache_requires_unchanged_doi_and_citation(tmp_path, monkeypatch):
    monkeypatch.setattr(openalex, "CITATIONS", tmp_path / "citations")
    cat = tmp_path / "catalog.csv"
    rows = [mkrow(id="CONF-001", doi=ART), mkrow(id="CONF-002", doi=ART), mkrow(id="CONF-003", doi=ART),
            mkrow(id="CONF-004", doi=ART)]
    _write_catalog(cat, rows)
    base = {**{c: "" for c in WORKS_COLUMNS}, "status": "ok", "checked_on": "2026-11-10", "cited_by_count": "5"}
    same = {**base, "id": "CONF-001", "input_hash": openalex.input_hash(rows[0])}
    doi_changed = {**base, "id": "CONF-002", "input_hash": openalex.input_hash({**rows[1], "doi": "10.1/old"})}
    cit_changed = {**base, "id": "CONF-003", "input_hash": openalex.input_hash({**rows[2], "citation": "old"})}
    no_hash = {**base, "id": "CONF-004"}
    write_works({r["id"]: r for r in (same, doi_changed, cit_changed, no_hash)})
    api = FakeApi()
    run_resolve(api, catalog_path=cat, today="2026-11-12")
    assert len(api.calls) == 3  # only CONF-001 is fresh
    works = read_works()
    assert works["CONF-001"]["cited_by_count"] == "5"
    assert all(works[i]["input_hash"] == openalex.input_hash(r) for i, r in zip(("CONF-002", "CONF-003", "CONF-004"),
                                                                              rows[1:]))


# ---------------------------------------------------------------- R19: rolling patch that refreshes and withdraws
def _pw(i, count, status="ok", checked="2026-11-10"):
    return {"id": i, "primary_domain": "REG", "status": status, "cited_by_count": str(count), "checked_on": checked}


def test_build_patch_refreshes_openalex_counts_and_keeps_current_ones():
    rows = [_prow("REG-001", "A", "5 (OpenAlex, checked 2026-10)"),   # count changed
            _prow("REG-002", "B", "9 (OpenAlex, checked 2026-11)"),   # unchanged: still in the rolling file
            _prow("REG-003", "C", "UNVERIFIED")]                       # plain fill
    works = {"REG-001": _pw("REG-001", 7), "REG-002": _pw("REG-002", 9), "REG-003": _pw("REG-003", 3)}
    recs = build_patch(rows, works, TODAY)
    assert [(r["name"], r["citation_count"]) for r in recs] == [
        ("A", "7 (OpenAlex, checked 2026-11)"), ("B", "9 (OpenAlex, checked 2026-11)"),
        ("C", "3 (OpenAlex, checked 2026-11)")]
    assert all(set(r) == {"name", "primary_domain", "resource_type", "last_verified_on", "citation_count"}
               for r in recs)


def test_build_patch_label_month_comes_from_the_works_check_date():
    rows = [_prow("REG-001", "A", "")]
    recs = build_patch(rows, {"REG-001": _pw("REG-001", 4, checked="2026-10-30")}, TODAY)
    assert recs[0]["citation_count"] == "4 (OpenAlex, checked 2026-10)"
    recs = build_patch(rows, {"REG-001": _pw("REG-001", 4, checked="")}, TODAY)
    assert recs[0]["citation_count"] == "4 (OpenAlex, checked 2026-11)"


def test_build_patch_withdraws_openalex_counts_whose_work_is_gone():
    rows = [_prow("REG-001", "A", "5 (OpenAlex, checked 2026-11)"),
            _prow("REG-002", "B", "6 (OpenAlex, checked 2026-11)"),
            _prow("REG-003", "C", "7 (OpenAlex, checked 2026-11)"),
            _prow("REG-004", "D", "8 (OpenAlex, checked 2026-11)")]
    works = {"REG-001": _pw("REG-001", 5, status="dataset_only"), "REG-002": _pw("REG-002", 0, status="no_match"),
             "REG-004": _pw("REG-004", 0, status="error")}
    recs = build_patch(rows, works, TODAY)
    assert [r["name"] for r in recs] == ["A", "B", "C"]  # error: unknown, left as it is
    assert recs[0]["citation_count"] == "UNVERIFIED (OpenAlex match withdrawn 2026-11-12: dataset_only)"
    assert recs[1]["citation_count"] == "UNVERIFIED (OpenAlex match withdrawn 2026-11-12: no_match)"
    assert recs[2]["citation_count"] == "UNVERIFIED (OpenAlex match withdrawn 2026-11-12: no works row)"
    assert all(r["_replace"] == ["citation_count"] for r in recs)
    assert set(recs[0]) == {"name", "primary_domain", "resource_type", "last_verified_on", "citation_count",
                            "_replace", "_replace_if"}


def test_build_patch_never_touches_google_scholar_na_or_withdrawn_cells():
    rows = [_prow("REG-001", "A", "81 (Google Scholar, checked 2026-11)"),
            _prow("REG-002", "B", "N/A (no reference article)"),
            _prow("REG-003", "C", "5 (OpenAlex, checked 2026-11) for X; 9 (Google Scholar, checked 2026-11) for Y"),
            _prow("REG-004", "D", "UNVERIFIED (OpenAlex match withdrawn 2026-11-01: no_match)")]
    works = {i: _pw(i, 99, status="no_match") for i in ("REG-001", "REG-002", "REG-003", "REG-004")}
    assert build_patch(rows, works, TODAY) == []
    works = {i: _pw(i, 99) for i in ("REG-001", "REG-002", "REG-003", "REG-004")}
    assert [(r["name"], r["citation_count"]) for r in build_patch(rows, works, TODAY)] == [
        ("D", "99 (OpenAlex, checked 2026-11)")]  # a withdrawn cell is UNVERIFIED: filled again when matched


def test_run_patch_writes_one_rolling_file_and_overwrites_it(tmp_citations, tmp_path, monkeypatch, capsys):
    _patch_env(tmp_path, monkeypatch, [_prow("REG-001", "A", ""), _prow("REG-002", "B", "")],
               [("REG-001", "REG", "ok", 5), ("REG-002", "REG", "ok", 6)])
    assert main(["patch"]) == 0
    files = sorted(p.name for p in (tmp_path / "research").iterdir())
    assert files == ["zz_patch_openalex.json", "zz_patch_zz_openalex_prose.json"]  # counts, then prose refresh
    path = tmp_path / "research" / "zz_patch_openalex.json"
    assert [r["name"] for r in json.loads(path.read_text(encoding="utf-8"))] == ["A", "B"]
    _patch_env(tmp_path, monkeypatch, [_prow("REG-001", "A", "5 (OpenAlex, checked 2026-11)"),
                                       _prow("REG-002", "B", "6 (OpenAlex, checked 2026-11)")],
               [("REG-001", "REG", "ok", 5), ("REG-002", "REG", "no_match", 0)])
    assert main(["patch", "--ids", "REG-001"]) == 0  # the rolling file always covers the whole catalog
    recs = json.loads(path.read_text(encoding="utf-8"))
    assert [r["name"] for r in recs] == ["A", "B"] and recs[1]["_replace"] == ["citation_count"]
    assert sorted(p.name for p in (tmp_path / "research").iterdir()) == ["zz_patch_openalex.json",
                                                                         "zz_patch_zz_openalex_prose.json"]


def test_rolling_patch_round_trip_through_merge(tmp_path):
    import merge_records as mr
    from schema import COLUMNS
    row = {**{c: "" for c in COLUMNS}, "id": "REG-001", "name": "A", "primary_domain": "REG",
           "resource_type": "1", "citation_count": "5 (OpenAlex, checked 2026-11)", "last_verified_on": "2026-12-01"}
    rec = build_patch([row], {"REG-001": _pw("REG-001", 0, status="dataset_only")}, TODAY)[0]
    path = tmp_path / "zz_patch_openalex.json"
    path.write_text(json.dumps([rec]), encoding="utf-8")
    loaded = mr.load_records([path])
    mr.merge_into(row, loaded[0])  # older last_verified_on, UNVERIFIED value: replaced all the same
    assert row["citation_count"] == "UNVERIFIED (OpenAlex match withdrawn 2026-11-12: dataset_only)"


# ---------------------------------------------------------------- R19: catalog vs works check
def test_report_catalog_vs_works_section():
    rows = [{"id": "REG-001", "name": "Fine", "citation_count": "5 (OpenAlex, checked 2026-11)"},
            {"id": "REG-002", "name": "Drifted", "citation_count": "5 (OpenAlex, checked 2026-11)"},
            {"id": "REG-003", "name": "Gone", "citation_count": "5 (OpenAlex, checked 2026-11)"},
            {"id": "REG-004", "name": "No row", "citation_count": "5 (OpenAlex, checked 2026-11)"},
            {"id": "REG-005", "name": "Scholar", "citation_count": "5 (Google Scholar, checked 2026-11)"}]
    works = {"REG-001": _rwork("REG-001", 5), "REG-002": _rwork("REG-002", 8),
             "REG-003": _rwork("REG-003", 5, status="dataset_only"), "REG-005": _rwork("REG-005", 1)}
    sec = _report(rows, works).split("## Catalog vs works")[1].split("\n## ")[0]
    assert "REG-002" in sec and "REG-003" in sec and "REG-004" in sec
    assert "REG-001" not in sec and "REG-005" not in sec
    assert "dataset_only" in sec and "no works row" in sec and "OpenAlex 8" in sec
    clean = _report(rows[:1], works).split("## Catalog vs works")[1].split("\n## ")[0]
    assert "None." in clean


# ---------------------------------------------------------------- refresh: one gz rewrite per batch, not per work
def _counting_drop(monkeypatch):
    calls = []
    real = openalex.drop_partial

    def wrapper(ids, append_rows=None):
        calls.append({ids} if isinstance(ids, str) else set(ids))
        return real(ids, append_rows=append_rows)
    monkeypatch.setattr(openalex, "drop_partial", wrapper)
    return calls


def test_refresh_of_many_works_rewrites_the_gz_once(tmp_citations, monkeypatch):
    _seed_works(("A-1", "W1", "A"), ("A-2", "W2", "A"), ("A-3", "W3", "A"))
    assert run_harvest(CiteApi(), today="2026-11-12") == 0
    calls = _counting_drop(monkeypatch)
    assert run_harvest(CiteApi(), today="2026-11-13", force=True) == 0
    assert calls == [{"W1", "W2", "W3"}]
    got = list(read_citing())
    assert len(got) == 9 and sorted({r["openalex_id"] for r in got}) == ["W1", "W2", "W3"]
    assert all(st["done"] and st["checked_on"] == "2026-11-13" for st in load_state().values())


def test_refresh_batches_are_bounded(tmp_citations, monkeypatch):
    _seed_works(("A-1", "W1", "A"), ("A-2", "W2", "A"), ("A-3", "W3", "A"))
    run_harvest(CiteApi(), today="2026-11-12")
    monkeypatch.setattr(openalex, "FLUSH_WORKS", 2)
    calls = _counting_drop(monkeypatch)
    run_harvest(CiteApi(), today="2026-11-13", force=True)
    assert calls == [{"W1", "W2"}, {"W3"}]
    assert len(list(read_citing())) == 9


def test_new_works_are_appended_without_a_rewrite(tmp_citations, monkeypatch):
    _seed_works(("A-1", "W1", "A"), ("A-2", "W2", "A"))
    calls = _counting_drop(monkeypatch)
    assert run_harvest(CiteApi(), today="2026-11-12") == 0
    assert calls == [] and len(list(read_citing())) == 6


def test_refresh_killed_during_rewrite_is_repaired_next_run(tmp_citations, monkeypatch):
    _seed_works(("A-1", "W1", "A"), ("A-2", "W2", "A"))
    run_harvest(CiteApi(), today="2026-11-12")
    real = openalex.drop_partial
    tries = []

    def dies(ids, append_rows=None):
        tries.append(ids)
        raise KeyboardInterrupt
    monkeypatch.setattr(openalex, "drop_partial", dies)
    monkeypatch.setattr(openalex, "FLUSH_WORKS", 1)  # the first batch rewrite dies inside the loop
    with pytest.raises(KeyboardInterrupt):
        run_harvest(CiteApi(), today="2026-11-13", force=True)
    assert len(tries) == 1  # the final flush does not retry the interrupted rewrite
    st = load_state()
    assert st["W1"]["done"] is False and st["W2"]["done"] is True  # W1 marked not-done before its rewrite
    monkeypatch.setattr(openalex, "FLUSH_WORKS", 100)
    monkeypatch.setattr(openalex, "drop_partial", real)
    assert run_harvest(CiteApi(), today="2026-11-13") == 0  # repair pass, then a fresh harvest
    got = list(read_citing())
    assert len(got) == 6 and sorted({r["openalex_id"] for r in got}) == ["W1", "W2"]
    assert all(st["done"] for st in load_state().values())


def test_refresh_interrupted_mid_harvest_keeps_unharvested_works(tmp_citations):
    _seed_works(("A-1", "W1", "A"), ("A-2", "W2", "A"))
    run_harvest(CiteApi(), today="2026-11-12")
    before = list(read_citing())

    class DiesOnW2(CiteApi):
        def get(self, path, params=None):
            if params["filter"] == "cites:W2":
                raise KeyboardInterrupt
            return super().get(path, params)
    with pytest.raises(KeyboardInterrupt):
        run_harvest(DiesOnW2(), today="2026-11-13", force=True)
    st = load_state()
    assert st["W1"]["done"] and st["W1"]["checked_on"] == "2026-11-13"  # completed refresh was written
    assert st["W2"]["done"] and st["W2"]["checked_on"] == "2026-11-12"  # untouched
    assert sorted((r["openalex_id"], r["citing_id"]) for r in read_citing()) == sorted(
        (r["openalex_id"], r["citing_id"]) for r in before)


# ---------------------------------------------------------------- R20: articles first in title matches; quiet patches
def test_title_path_article_beats_more_cited_dataset():
    for typ in ("dataset", "other", "paratext"):
        rec = resolve_row(mkrow(citation=TITLE_CIT), SearchApi([_sr("W1", 50, typ), _sr("W2", 5)]), "2026-11-12")
        assert rec["status"] == "ok" and rec["openalex_id"] == "W2"
        rec = resolve_row(mkrow(citation=TITLE_CIT), SearchApi([_sr("W1", 50, typ), _sr("W2", 5, "preprint")]),
                          "2026-11-12")
        assert rec["status"] == "ok" and rec["openalex_id"] == "W2"  # a preprint is still article-type


def test_withdrawal_record_names_the_value_it_withdraws():
    rows = [_prow("REG-001", "A", "5 (OpenAlex, checked 2026-11)")]
    rec = build_patch(rows, {"REG-001": _pw("REG-001", 0, status="no_match")}, TODAY)[0]
    assert rec["_replace"] == ["citation_count"]
    assert rec["_replace_if"] == {"citation_count": "5 (OpenAlex, checked 2026-11)"}


def test_unchanged_records_keep_the_rows_last_verified_on():
    import merge_records as mr
    row = {**_prow("REG-001", "A", "9 (OpenAlex, checked 2026-11)"), "last_verified_on": "2026-11-12"}
    later = "2026-12-20"
    recs = build_patch([row, _prow("REG-002", "B", "UNVERIFIED")],
                       {"REG-001": _pw("REG-001", 9), "REG-002": _pw("REG-002", 4)}, later)
    assert recs[0]["last_verified_on"] == "2026-11-12"  # unchanged: no bump
    assert recs[1]["last_verified_on"] == later         # fill: today
    from schema import COLUMNS
    full = {**{c: "" for c in COLUMNS}, **row}
    before = dict(full)
    mr.merge_into(full, recs[0])
    assert full == before


# ---------------------------------------------------------------- stale OpenAlex counts in quality justifications
def _qrow(i, name, qj):
    return {"id": i, "name": name, "primary_domain": "REG", "resource_type": "1", "quality_justification": qj}


def test_build_prose_patch_refreshes_only_unambiguous_stale_counts():
    from openalex import build_prose_patch
    rows = [
        _qrow("REG-001", "A", "Validity 4: x. Adoption 4: the reference article has about 1,540 OpenAlex citations, "
                              "but reuse is narrow."),                                          # stale, unambiguous
        _qrow("REG-002", "B", "Adoption 4: about 1,000 OpenAlex citations."),                   # within 10 %
        _qrow("REG-003", "C", "Adoption 4: the 2002 and 2018 articles have about 548 and 161 OpenAlex citations."),
        _qrow("REG-004", "D", "Adoption 2: the reference article has 39 OpenAlex citations."),  # record lower
        _qrow("REG-005", "E", "Adoption 4: the feed paper alone has about 366 OpenAlex citations."),
        _qrow("REG-006", "F", "Adoption 3: about 20 OpenAlex citations."),                      # work not ok
        _qrow("REG-007", "G", "Adoption 3: about 19 OpenAlex citations to the dataset record."),
    ]
    works = {"REG-001": _pwork("REG-001", 1758), "REG-002": _pwork("REG-002", 1050),
             "REG-003": _pwork("REG-003", 900), "REG-004": _pwork("REG-004", 3),
             "REG-005": _pwork("REG-005", 485), "REG-006": _pwork("REG-006", 40, status="no_match"),
             "REG-007": _pwork("REG-007", 39)}
    recs, skipped = build_prose_patch(rows, works)
    assert recs == [{"name": "A", "resource_type": "1",
                     "quality_justification": "Validity 4: x. Adoption 4: the reference article has 1,758 OpenAlex "
                                              "citations, but reuse is narrow.",
                     "_replace": ["quality_justification"],
                     "_replace_if": {"quality_justification": rows[0]["quality_justification"]}}]
    assert sorted(skipped) == ["REG-003", "REG-004", "REG-005", "REG-007"]


def test_prose_patch_file_sorts_after_every_other_patch():
    import openalex
    others = ["zz_patch_openalex.json", "zz_patch_wording_2026-11-15.json", "zz_patch_xprose_2026-11-15_3.json"]
    assert sorted(others + [openalex.PROSE_PATCH_FILE])[-1] == openalex.PROSE_PATCH_FILE
