import json
import pathlib
import urllib.parse

import pytest

import citations_common as cc
from citations_common import (
    DATA_DOI_PREFIXES,
    OpenAlexError,
    OpenAlexNotFound,
    OpenAlexSession,
    extract_dois,
    normalise_title,
    parse_citation,
    surname_matches,
    title_similarity,
)

MAILTO = "me" + "@" + "example.org"

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "work_response.json"


def test_extract_dois_prefix_and_trailing():
    assert extract_dois("doi:10.1111/J.1540-5907.2006.00198.X;") == [("10.1111/j.1540-5907.2006.00198.x", "article")]


def test_extract_dois_article_and_data():
    assert extract_dois("10.1177/00220027211068897 (article); 10.7910/DVN/OT14Z9 (data)") == [
        ("10.1177/00220027211068897", "article"), ("10.7910/dvn/ot14z9", "data")]


def test_extract_dois_placeholders():
    assert extract_dois("UNVERIFIED") == [] and extract_dois("N/A") == [] and extract_dois("") == []


def test_extract_dois_url_forms_and_trailing_paren():
    assert extract_dois("https://doi.org/10.1017/S0003055403000534.") == [("10.1017/s0003055403000534", "article")]
    assert extract_dois("http://dx.doi.org/10.5281/zenodo.123)") == [("10.5281/zenodo.123", "data")]


def test_data_prefixes_constant():
    assert "10.7910/dvn" in DATA_DOI_PREFIXES and len(DATA_DOI_PREFIXES) == 17
    assert {"10.17632", "10.17616", "10.18128"} <= set(DATA_DOI_PREFIXES)


def test_normalise_title_drops_article_and_diacritics():
    assert normalise_title("The Autocratic Régimes: A New Dataset!") == "autocratic regimes a new dataset"


def test_title_similarity():
    assert title_similarity("The Regimes!", "regimes") == 1.0
    assert title_similarity("abc", "xyz") == 0.0


def test_parse_citation_quoted():
    p = parse_citation('Kreiman, Guillermo. 2025. "Revolutionary Days: Introducing the Latin American Guerrillas Dataset." Journal of Peace Research 62(2): 497-508.')
    assert p == {"title": "Revolutionary Days: Introducing the Latin American Guerrillas Dataset.", "year": 2025, "surname": "Kreiman"}


def test_parse_citation_curly_quotes_and_particle():
    p = parse_citation("von Partheymüller, Julia. 2019. “A Title Here.” Journal 1: 1-2.")
    assert p["title"] == "A Title Here." and p["year"] == 2019 and p["surname"] == "Partheymüller"


def test_parse_citation_no_title():
    p = parse_citation("World Bank. 2025. World Development Indicators. Washington, DC.")
    assert p["title"] is None and p["year"] == 2025


def test_parse_citation_surname_none_when_comma_after_year():
    assert parse_citation("World Bank. 2025. Data, more.")["surname"] is None


def test_surname_matches_diacritics():
    auth = json.loads(FIXTURE.read_text(encoding="utf-8"))["authorships"]
    assert surname_matches("Partheymuller", auth)
    assert surname_matches("SMITH", auth)
    assert not surname_matches("Jones", auth)


def test_surname_matches_only_first_three():
    auth = [{"author": {"display_name": n}} for n in ("A One", "B Two", "C Three", "D Four")]
    assert surname_matches("Three", auth) and not surname_matches("Four", auth)


# ---- session -------------------------------------------------------------

class FakeResp:
    def __init__(self, status=200, payload=None, headers=None):
        self.status_code = status
        self.headers = headers or {}
        self._payload = payload if payload is not None else {}

    def json(self):
        return self._payload


class FakeHttp:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, params=None, timeout=None, headers=None):
        self.calls.append({"url": url, "params": dict(params or {}), "timeout": timeout, "headers": headers})
        r = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(r, Exception):
            raise r
        return r


@pytest.fixture
def clock_sleep(monkeypatch):
    state = {"t": 1000.0, "sleeps": []}

    def fake_sleep(s):
        state["sleeps"].append(s)
        state["t"] += s

    monkeypatch.setattr(cc, "sleep", fake_sleep)
    monkeypatch.setattr(cc, "clock", lambda: state["t"])
    return state


def test_session_ok_returns_json_and_headers(clock_sleep):
    work = json.loads(FIXTURE.read_text(encoding="utf-8"))
    http = FakeHttp([FakeResp(200, work)])
    s = OpenAlexSession(http=http)
    assert s.get("/works/W1") == work
    call = http.calls[0]
    assert call["url"] == "https://api.openalex.org/works/W1"
    assert call["timeout"] == 30
    assert call["headers"]["User-Agent"].startswith("polisci-catalog/1.")


def test_session_retries_then_raises(clock_sleep):
    http = FakeHttp([FakeResp(503)])
    s = OpenAlexSession(http=http)
    with pytest.raises(OpenAlexError):
        s.get("/works/W1")
    assert len(http.calls) == 6  # first try + 5 retries
    backoffs = [x for x in clock_sleep["sleeps"] if x >= 2]
    assert backoffs[:5] == [2, 4, 8, 16, 32]


def test_session_recovers_after_5xx(clock_sleep):
    http = FakeHttp([FakeResp(500), FakeResp(200, {"ok": 1})])
    assert OpenAlexSession(http=http).get("/x") == {"ok": 1}


def test_session_connection_error_retries(clock_sleep):
    http = FakeHttp([ConnectionError("boom"), FakeResp(200, {"ok": 2})])
    assert OpenAlexSession(http=http).get("/x") == {"ok": 2}


def test_session_429_honours_retry_after(clock_sleep):
    http = FakeHttp([FakeResp(429, headers={"Retry-After": "7"}), FakeResp(200, {"ok": 3})])
    assert OpenAlexSession(http=http).get("/x") == {"ok": 3}
    assert 7 in clock_sleep["sleeps"]


def test_session_429_default_retry_after(clock_sleep):
    http = FakeHttp([FakeResp(429), FakeResp(200, {"ok": 4})])
    OpenAlexSession(http=http).get("/x")
    assert 10 in clock_sleep["sleeps"]


def test_session_404_not_found(clock_sleep):
    http = FakeHttp([FakeResp(404)])
    with pytest.raises(OpenAlexNotFound):
        OpenAlexSession(http=http).get("/works/nope")
    assert len(http.calls) == 1
    assert issubclass(OpenAlexNotFound, OpenAlexError)


def test_session_mailto_only_when_set(clock_sleep):
    http = FakeHttp([FakeResp(200, {})])
    OpenAlexSession(http=http).get("/x", {"select": "id"})
    assert "mailto" not in http.calls[0]["params"] and http.calls[0]["params"]["select"] == "id"
    http2 = FakeHttp([FakeResp(200, {})])
    OpenAlexSession(mailto=MAILTO, http=http2).get("/x")
    assert http2.calls[0]["params"]["mailto"] == MAILTO


def test_session_rate_limit_spacing(clock_sleep):
    http = FakeHttp([FakeResp(200, {})])
    s = OpenAlexSession(rate=8.0, http=http)
    t0 = clock_sleep["t"]
    s.get("/a")
    s.get("/b")
    assert clock_sleep["t"] - t0 >= 0.125 - 1e-9


def test_extract_dois_parenthetical_data_labels():
    got = extract_dois("10.23696/vdemds26 (dataset v16); 10.1017/S1537592711000880 (reference article)")
    assert got == [("10.23696/vdemds26", "data"), ("10.1017/s1537592711000880", "article")]
    assert extract_dois("10.1234/abc (v2.1)")[0][1] == "data"
    assert extract_dois("10.1234/abc (replication data)")[0][1] == "data"
    assert extract_dois("10.1234/abc (Dataverse)")[0][1] == "data"
    assert extract_dois("10.1234/abc (3 data)")[0][1] == "data"


def test_extract_dois_article_labels_stay_article():
    for label in ("data descriptor", "data article", "data paper", "reference article", "article"):
        assert extract_dois(f"10.1234/abc ({label})")[0][1] == "article", label


def test_session_invalid_json_raises_openalex_error(clock_sleep):
    class BadJson(FakeResp):
        def json(self):
            raise ValueError("not json")

    with pytest.raises(OpenAlexError):
        OpenAlexSession(http=FakeHttp([BadJson(200)])).get("/x")


# ---------------------------------------------------------------- API key

KEY = "sekrit-KEY/123+x"


def test_api_key_param_only_when_set(clock_sleep):
    http = FakeHttp([FakeResp(200, {"ok": 1})])
    OpenAlexSession(http=http).get("/x")
    assert "api_key" not in http.calls[0]["params"]
    http2 = FakeHttp([FakeResp(200, {"ok": 1})])
    OpenAlexSession(api_key=KEY, mailto=MAILTO, http=http2).get("/x", {"select": "id"})
    assert http2.calls[0]["params"] == {"select": "id", "mailto": MAILTO, "api_key": KEY}


def test_api_key_empty_string_is_unset(clock_sleep):
    http = FakeHttp([FakeResp(200, {"ok": 1})])
    OpenAlexSession(api_key="", http=http).get("/x")
    assert "api_key" not in http.calls[0]["params"]


def test_api_key_not_in_errors(clock_sleep):
    with pytest.raises(OpenAlexError) as e1:
        OpenAlexSession(api_key=KEY, http=FakeHttp([FakeResp(401, {})])).get("/works")
    assert "401" in str(e1.value) and KEY not in str(e1.value)

    class Boom:  # requests embeds the full URL, including the query string, in connection errors
        def get(self, url, params=None, **kw):
            raise ConnectionError(f"pool: {url}?api_key={KEY}&x=1 failed; {urllib.parse.quote(KEY)}")

    with pytest.raises(OpenAlexError) as e2:
        OpenAlexSession(api_key=KEY, max_retries=1, http=Boom()).get("/works")
    msg = str(e2.value)
    assert "giving up" in msg and KEY not in msg and urllib.parse.quote(KEY) not in msg

    class BadJsonKey(FakeResp):
        def json(self):
            raise ValueError(f"bad body {KEY}")

    with pytest.raises(OpenAlexError) as e3:
        OpenAlexSession(api_key=KEY, http=FakeHttp([BadJsonKey(200)])).get("/x")
    assert KEY not in str(e3.value) and e3.value.__cause__ is None
    with pytest.raises(OpenAlexNotFound) as e4:
        OpenAlexSession(api_key=KEY, http=FakeHttp([FakeResp(404, {})])).get("/works/nope")
    assert KEY not in str(e4.value)


@pytest.fixture
def key_sources(monkeypatch, tmp_path):
    f = tmp_path / "PRIVATE_openalex_api_key.txt"
    monkeypatch.setattr(cc, "API_KEY_FILE", f)
    monkeypatch.delenv("OPENALEX_API_KEY", raising=False)
    return f


def test_load_api_key_missing(key_sources):
    key, src = cc.load_api_key()
    assert key is None and isinstance(src, str)


def test_load_api_key_file_stripped_and_empty_ignored(key_sources):
    key_sources.write_text("  \n", encoding="utf-8")
    assert cc.load_api_key()[0] is None
    key_sources.write_text("  filekey \n", encoding="utf-8")
    key, src = cc.load_api_key()
    assert key == "filekey" and src == "PRIVATE_openalex_api_key.txt"


def test_load_api_key_env_beats_file(key_sources, monkeypatch):
    key_sources.write_text("filekey", encoding="utf-8")
    monkeypatch.setenv("OPENALEX_API_KEY", " envkey ")
    key, src = cc.load_api_key()
    assert key == "envkey" and src == "environment variable OPENALEX_API_KEY"
    monkeypatch.setenv("OPENALEX_API_KEY", "   ")  # blank env falls through to the file
    assert cc.load_api_key()[0] == "filekey"


def test_extract_dois_new_data_prefixes_with_boundary():
    assert extract_dois("10.17632/abc.1; 10.17616/R3C94T; 10.18128/D010.V1") == [
        ("10.17632/abc.1", "data"), ("10.17616/r3c94t", "data"), ("10.18128/d010.v1", "data")]
    assert extract_dois("10.176320/xyz") == [("10.176320/xyz", "article")]  # prefix must end at "/"


def test_session_429_retry_after_capped(clock_sleep):
    http = FakeHttp([FakeResp(429, headers={"Retry-After": "3600"}), FakeResp(200, {"ok": 5})])
    assert OpenAlexSession(http=http).get("/x") == {"ok": 5}
    assert cc.RETRY_AFTER_CAP == 120
    assert 120 in clock_sleep["sleeps"] and 3600 not in clock_sleep["sleeps"]


def test_openalex_count_mismatches():
    rows = [{"id": "A-1", "citation_count": "5 (OpenAlex, checked 2026-11)"},
            {"id": "A-2", "citation_count": "1,005 (OpenAlex, checked 2026-11)"},
            {"id": "A-3", "citation_count": "5 (OpenAlex, checked 2026-11)"},
            {"id": "A-4", "citation_count": "5 (OpenAlex, checked 2026-11)"},
            {"id": "A-5", "citation_count": "5 (Google Scholar, checked 2026-11)"},
            {"id": "A-6", "citation_count": "UNVERIFIED"}]
    works = {"A-1": {"status": "ok", "cited_by_count": "5"}, "A-2": {"status": "ok", "cited_by_count": "1004"},
             "A-3": {"status": "no_match", "cited_by_count": ""}, "A-5": {"status": "no_match"},
             "A-6": {"status": "ok", "cited_by_count": "3"}}
    got = cc.openalex_count_mismatches(rows, works)
    assert [g[0] for g in got] == ["A-2", "A-3", "A-4"]
    assert got[0][2] == "catalog 1005, OpenAlex 1004"
    assert got[1][2] == "works status no_match" and got[2][2] == "no works row"
    assert cc.is_openalex_count("12 (OpenAlex, checked 2026-11)")
    assert not cc.is_openalex_count("12 (OpenAlex, checked 2026-11) for X")


# ---------------------------------------------------------------- R17b: N/A fields keep DOIs labelled as reference works
R17B_USE = {
    "N/A (dataset); 10.1016/0261-3794(91)90004-C (reference article)": ["10.1016/0261-3794(91)90004-c"],
    "N/A for the dataset; reference articles 10.2307/2657374 (ASR 1999; Crossref also holds a duplicate record "
    "10.1177/000312249906400508) and 10.1016/S0047-2727(99)00044-4 (JPubE 2000)": ["10.2307/2657374"],
    "N/A for the datasets (no DOI listed on the download page); reference book 10.1017/CBO9780511817595":
        ["10.1017/cbo9780511817595"],
    "N/A (no dataset DOI); cited references: 10.1177/0022343302039005007; 10.1177/00223433251345636":
        ["10.1177/0022343302039005007", "10.1177/00223433251345636"],
    "N/A for the dataset; reference articles 10.1017/S0003055403000522 (Harff 2003) and "
    "10.1111/j.1540-5907.2009.00426.x (Goldstone et al. 2010)":
        ["10.1017/s0003055403000522", "10.1111/j.1540-5907.2009.00426.x"],
    "N/A (service); 10.21105/joss.00859 (nomisr reference article)": ["10.21105/joss.00859"],
    "N/A (portal); example survey reference article 10.1186/2193-9020-2-8": ["10.1186/2193-9020-2-8"],
    "UNVERIFIED; reference volume 10.1007/978-3-030-51584-3": ["10.1007/978-3-030-51584-3"],
}
R17B_DONT = [
    "N/A (no dataset DOI; re3data repository record 10.17616/R3C94T)",
    "N/A (no DOI for the data set; companion report DOI 10.31752/idea.2024.55)",
    "N/A (no reference article). Per-dataset figshare DOIs, e.g. 10.25438/wes02.28664795.v1 (2022 Political TV Ad "
    "Airing Data), 10.25438/wes02.21936049.v2 (2020 Non-Presidential Airing Data)",
    "N/A (reference paper is an unpublished conference paper); derived Clio Infra file: 10.34894/KEBYOQ",
    "N/A for the database working paper; related article 10.1017/S0003055416000654",
    "UNVERIFIED (former B2SHARE record 10.23728/b2share.8b3dd38e listed on B2FIND)",
    "N/A (no reference) 10.1111/x (reference? no reference)",
]


@pytest.mark.parametrize("field,expected", list(R17B_USE.items()))
def test_usable_dois_keeps_reference_labelled_dois_in_na_fields(field, expected):
    assert [d for d, _ in cc.usable_dois(field)] == expected


@pytest.mark.parametrize("field", R17B_DONT)
def test_usable_dois_drops_unlabelled_dois_in_na_fields(field):
    assert cc.usable_dois(field) == []


def test_usable_dois_ordinary_fields_unchanged():
    for f in ("10.1177/00220027211068897 (article); 10.7910/DVN/OT14Z9 (data)", "doi:10.1111/abc", "", "N/A"):
        assert cc.usable_dois(f) == extract_dois(f)
    assert cc.usable_dois("N/A (dataset); 10.7910/DVN/X1 (reference data deposit)") == [("10.7910/dvn/x1", "data")]


def test_extract_dois_keeps_parentheses_inside_a_doi():
    assert extract_dois("10.1016/S1574-0722(07)00080-7; (see 10.2307/2657374)") == [
        ("10.1016/s1574-0722(07)00080-7", "article"), ("10.2307/2657374", "article")]
    assert extract_dois("record 10.1177/000312249906400508) and more") == [("10.1177/000312249906400508", "article")]


def test_extract_dois_keeps_a_balanced_trailing_parenthesis():
    assert extract_dois("10.1234/endsparen(2)") == [("10.1234/endsparen(2)", "article")]
    assert extract_dois("see 10.1234/endsparen(2).") == [("10.1234/endsparen(2)", "article")]
    assert extract_dois("(10.1234/abc)") == [("10.1234/abc", "article")]
    assert extract_dois("10.1016/0261-3794(91)90004-C (reference article)") == [
        ("10.1016/0261-3794(91)90004-c", "article")]
