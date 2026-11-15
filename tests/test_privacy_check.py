"""Tests for scripts/privacy_check.py.

Every planted value is fictional and assembled at run time from fragments, so this file itself scans clean
(see test_self_scan_is_clean).
"""
import codecs
import gzip
import json
import random
import string
import time
from dataclasses import replace
from pathlib import Path

import pytest

import privacy_check as pc
from privacy_check import Rules, load_rules, scan_text, scan_tree, learn_contacts

HERE = Path(__file__).resolve()
FIX = HERE.parent / "fixtures" / "privacy"
SCANNER = HERE.parent.parent / "scripts" / "privacy_check.py"
needs_fixtures = pytest.mark.skipif(not FIX.is_dir(),
                                    reason="tests/fixtures/privacy is not exported to the public repository")

# fictional planted values, built from fragments
AT = "@"
BS = "\\"
USERS = "Us" + "ers"
U = "ali" + "ce"                              # OS username (equals a public given name)
DEV = "box" + "-7"                            # device name
PERS = U + AT + "example" + ".net"            # personal email whose local part equals the username
PERS2 = "zz.k" + "ite9" + AT + "mail" + ".example"   # personal email with a distinct local part
LOCAL2 = "zz.k" + "ite9"
INST = "a.author" + AT + "univ.edu"
ORG = "data" + AT + "org.example"
UNL = "x" + AT + "y.example"
BAD = "bad" + ".example"
LIB = "Pat" + " Lee"
PRIV = "zq-sec" + "ret-line-77"
H32 = "0123456789abcdef" * 2
UUID = "123e4567-e89b-12d3" + "-a456-426614174000"
TOOL = ".toolx"                               # fictional local tool directory (local/tool_dirs.txt)


@pytest.fixture
def rules():
    return Rules(allowed_emails={INST, ORG}, identifiers=[DEV], unsafe_domains={BAD}, librarian_names={LIB},
                 personal_emails={PERS, PERS2}, username=U, private_strings={PRIV})


def names(hits):
    return [h.rule for h in hits]


def rules_of(s, r):
    return set(names(scan_text(s, r, "f")))


# ---------------------------------------------------------------- email

def test_institutional_email_passes(rules):
    assert scan_text("contact " + INST, rules, "f") == []


def test_listed_org_email_passes(rules):
    assert scan_text("write to " + ORG + ".", rules, "f") == []


def test_unlisted_email_fails(rules):
    assert names(scan_text("mail " + UNL, rules, "f")) == ["email"]


def test_email_case_insensitive_allow(rules):
    assert scan_text(INST.upper(), rules, "f") == []


def test_personal_email_fails_even_if_listed(rules):
    r = replace(rules, allowed_emails=rules.allowed_emails | {PERS})
    assert "personal_email" in rules_of(PERS, r)


@pytest.mark.parametrize("s", ["npm i pkg" + AT + "1.2.3", "img/logo" + AT + "2x.png", "logo" + AT + "2X.PNG",
                               "git" + AT + "github.com:owner/repo.git", "@scope/pkg" + AT + "^2.0",
                               "react" + AT + "18.3.1/umd"])
def test_non_email_at_signs_pass(rules, s):
    assert scan_text(s, rules, "f") == []


# ---------------------------------------------------------------- decoded variants

@pytest.mark.parametrize("s", [
    "x" + "%40" + "y.example",                                    # url-encoded
    "x" + "%2540" + "y.example",                                  # double url-encoded
    "x" + "&#64;" + "y.example",                                  # html entity
    "x" + "&#x40;" + "y.example",
    "x" + BS + "u0040" + "y.example",                             # json escape
    "x [a" + "t] y.example", "x(a" + "t)y.example", "x {a" + "t} y [d" + "ot] example",
    "x a" + "t y d" + "ot example",                                # plain words, dot obfuscated too
])
def test_obfuscated_email_found(rules, s):
    assert "email" in rules_of(s, rules)


def test_plain_at_without_dot_obfuscation_is_prose(rules):
    assert scan_text("data are available a" + "t www.icpsr.umich.edu today", rules, "f") == []


def test_personal_email_obfuscated(rules):
    s = PERS.replace(AT, " [a" + "t] ").replace(".net", " [d" + "ot] net")
    assert "personal_email" in rules_of(s, rules)


def test_personal_local_part_word(rules):
    assert "personal_email" in rules_of("reach " + LOCAL2 + " on the forum", rules)
    assert "personal_email" not in rules_of("reach x" + LOCAL2 + "x", rules)


def test_local_part_equal_to_username_not_word_matched(rules):
    # ruling P5: the username is a public given name; a bare word is not a leak
    assert scan_text(U.capitalize() + " Alicesen wrote this", rules, "f") == []


def test_username_url_encoded_path(rules):
    s = "file:///C:/" + USERS + "%2F" + U + "%2Fdoc"
    assert {"home_path", "identifier"} <= rules_of(s, rules)


def test_username_html_escaped_path(rules):
    assert "identifier" in rules_of("&#47;home2&#47;" + U + "&#47;x", rules)


def test_username_json_escaped_slash_path(rules):
    assert "identifier" in rules_of('{"p": "' + BS + "/srv" + BS + "/" + U + BS + '/x"}', rules)


def test_hits_deduplicated_per_line_and_rule(rules):
    s = UNL + " " + UNL.replace("x", "w") + " " + "x" + "%40" + "y.example"
    assert names(scan_text(s, rules, "f")) == ["email"]


# ---------------------------------------------------------------- home_path

@pytest.mark.parametrize("s", ["C:/" + USERS + "/x/", "C:" + BS + USERS + BS + "x" + BS,
                               "C:" + BS * 2 + USERS + BS * 2 + "x" + BS * 2, "/" + USERS + "/x/",
                               "/ho" + "me/x/", "see /ho" + "me/x", "/mnt/c/" + USERS + "/x", "/c/" + USERS + "/x",
                               "D:" + BS + "p" + BS + "App" + "Data" + BS + "Local", "~/.x/App" + "Data/Roaming",
                               "C:App" + "Data" + BS + "x"])
def test_home_paths(rules, s):
    assert "home_path" in rules_of(s, rules)


@pytest.mark.parametrize("s", ["see /data/users/x and https://example.org/" + USERS,
                               "https://example.org/" + USERS + "/x", "visit nappdata.org now",
                               "the App" + "Data folder", "https://example.org/ho" + "me/about"])
def test_ordinary_paths_pass(rules, s):
    assert scan_text(s, rules, "f") == []


# ---------------------------------------------------------------- identifiers

def test_username_in_escaped_json_path(rules):
    s = '{"p": "C:' + BS * 2 + USERS + BS * 2 + U + BS * 2 + 'doc"}'
    assert "identifier" in rules_of(s, rules)


@pytest.mark.parametrize("s", ["C:/" + USERS + "/" + U + "/", "/ho" + "me/" + U, "/ho" + "me/" + U + "/x",
                               BS * 2 + "nas" + BS + U + BS + "share", '"/srv/' + U + '"',
                               U + AT + "laptop.local", "ssh " + U + AT + "host", U + "/project/x"])
def test_username_as_path_segment_or_user_at(rules, s):
    assert "identifier" in rules_of(s, rules)


@pytest.mark.parametrize("s", [U.capitalize() + " Alicesen", "author: " + U, "given-names: " + U.capitalize(),
                               "/x/" + U + "-work/", "/x/" + U + "_old", "/x/" + U + "s/", "M" + U + "/x"])
def test_username_as_word_is_not_a_hit(rules, s):
    assert "identifier" not in rules_of(s, rules)


def test_username_before_at_of_allowed_email_passes(rules):
    r = replace(rules, allowed_emails=rules.allowed_emails | {U + AT + "univ.edu"})
    assert scan_text("contact " + U + AT + "univ.edu", r, "f") == []


def test_username_in_backslash_path(rules):
    assert "identifier" in rules_of("D:" + BS + "work" + BS + DEV.capitalize() + BS + "x", rules)


@pytest.mark.parametrize("s", ["host " + DEV + ".local", BS * 2 + DEV.upper() + BS + "share", "on " + DEV + " today",
                               "x_" + DEV + "_y"])
def test_device_whole_word(rules, s):
    assert "identifier" in rules_of(s, rules)


def test_device_not_inside_word(rules):
    assert scan_text("a" + DEV + "1 and " + DEV + "9", rules, "f") == []


# ---------------------------------------------------------------- session_id

def test_session_id_and_uuid(rules):
    assert names(scan_text("/tmp/" + H32 + "/out", rules, "f")) == ["session_id"]
    assert names(scan_text("C:" + BS + "x" + BS + UUID + BS + "y", rules, "f")) == ["session_id"]
    assert names(scan_text("/x/" + UUID, rules, "f")) == ["session_id"]
    assert names(scan_text("/x/sess-" + UUID + ".jsonl", rules, "f")) == ["session_id"]
    assert scan_text("sha256 fingerprint " + H32 + " in prose", rules, "f") == []


@pytest.mark.parametrize("s", ["https://app.example/artifact/" + UUID, "see <https://x.example/" + H32 + "/v>",
                               "doi 10.5281/zenodo." + UUID, "https://doi.org/10.7910/DVN/" + H32,
                               "[link](https://x.example/a/" + UUID + ")",
                               "data.example.eu/dataset/" + UUID, "hdl.example.net/11245.1/" + UUID,
                               "www.example.org/fileuploads/a/" + UUID + "/file.pdf",
                               "osf.example/x/overview?view_only=" + H32])
def test_session_id_in_url_or_doi_passes(rules, s):
    assert scan_text(s, rules, "f") == []


@pytest.mark.parametrize("s", ["file:///tmp/" + UUID + "/x", "~/" + TOOL + "/projects/" + UUID + ".jsonl",
                               "https://x.example/ok," + "/tmp/" + UUID + "/y",
                               "C:/" + "Users/zz/" + UUID + "/y", "/ho" + "me/zz/" + UUID, TOOL + "/" + UUID,
                               "AppData/" + "x/" + UUID])
def test_session_id_in_local_path_fails(rules, s):
    assert "session_id" in rules_of(s, rules)


# ---------------------------------------------------------------- pitch and secret

@pytest.mark.parametrize("s", ["Draft prepared " + "for Dr X", "draft PREPARED " + "FOR x", "pitch" + "_note: x",
                               "the Pitch" + " Note says"])
def test_pitch(rules, s):
    assert names(scan_text(s, rules, "f")) == ["pitch"]


@pytest.mark.parametrize("s", [
    "tok ghp_" + "a" * 36, "github_pat_" + "A1_" * 12, "Authorization: Bearer " + "A" * 24, "key sk-" + "b" * 22,
    "api" + "_key = 'abcd1234efgh'", '"api' + 'Key": "abcd1234efgh"', "API" + "-KEY='abcd1234efgh'",
    "pass" + "word: 'hunter22'", '{"pass' + 'word": "s3cr3t!"}'])
def test_secret_patterns(rules, s):
    assert names(scan_text(s, rules, "f")) == ["secret"]


@pytest.mark.parametrize("s", ["api" + "_key: str", "self.api" + "_key = api_key", "ws.protection.pass" + "word = pw",
                               "a pass" + "word policy page", "api" + "_key = 'short'", "pass" + "word = ''",
                               "def f(api" + "_key=None):"])
def test_secret_names_without_values_pass(rules, s):
    assert scan_text(s, rules, "f") == []


def test_private_string_substring_case_insensitive(rules):
    assert names(scan_text("x" + PRIV.upper() + "y", rules, "f")) == ["secret"]
    assert "secret" in rules_of("x" + PRIV.replace("-", "%2D"), rules)


# ---------------------------------------------------------------- unsafe domain and librarian

def test_unsafe_domain_and_librarian(rules):
    assert names(scan_text("http://www." + BAD + "/x", rules, "f")) == ["unsafe_domain"]
    assert scan_text("not" + BAD, rules, "f") == []
    assert names(scan_text("Guide by " + LIB.lower() + ", 2025", rules, "f")) == ["librarian"]
    assert scan_text(LIB + "son", rules, "f") == []


@pytest.mark.parametrize("s", ["Pat" + "\u00a0Lee", "Pat" + "  Lee", "Lee" + ", Pat", "LEE" + ",PAT",
                               "Pat" + "&nbsp;Lee"])
def test_librarian_spacing_and_order(rules, s):
    assert "librarian" in rules_of(s, rules)


def test_librarian_allowlist(rules):
    r = replace(rules, allowed_names={"pat" + "  LEE"})
    assert scan_text("by " + LIB, r, "f") == []


def test_parse_librarian_cell():
    p = pc.parse_librarian_cell
    assert p("Pat" + " Lee") == [LIB]
    assert p("Doug" + " Mc; Nick Ok / Ann Bo & Cy Di and Ed Fu") == ["Doug Mc", "Nick Ok", "Ann Bo", "Cy Di",
                                                                     "Ed Fu"]
    assert p("not named on page (guide owner Sam" + " Ray)") == ["Sam Ray"]
    assert p("UNVERIFIED (adapted from Kim" + " Doe, Brandeis)") == ["Kim Doe"]
    assert p("Ola" + " Nordmann (contact)") == ["Ola Nordmann"]
    assert p("Lee" + ", Pat") == [LIB]
    assert p("Jordan Q." + " Rivers") == ["Jordan Q. Rivers"]
    assert p("Rachel" + " O'Toole-Smith") == ["Rachel O'Toole-Smith"]
    for v in ["UNVERIFIED", "not listed", "not named", "N/A", "NA", "", "Data Discovery Team (not named)",
              "Data Services Librarian", "Stone Latin American Library (no individual)",
              "Program on Central Asia, Davis Center", "UNVERIFIED (D'Angelo Law Librarians)",
              "not named (" + "dm" + AT + "lib.example)", "Librarian", "Subject Specialist"]:
        assert p(v) == [], v


# ---------------------------------------------------------------- excerpts

def test_excerpt_short(rules):
    h = scan_text("x" * 300 + " mail " + UNL + " " + "y" * 300, rules, "f")[0]
    assert len(h.excerpt) <= 80 and UNL in h.excerpt


def test_excerpt_offset_after_indent(rules):
    h = scan_text(" " * 200 + "a" * 100 + " mail " + UNL + " " + "y" * 300, rules, "f")[0]
    assert len(h.excerpt) <= 80 and UNL in h.excerpt


@pytest.mark.parametrize("s,rule,secret", [
    ("on " + DEV + " now", "identifier", DEV), ("/ho" + "me/" + U + "/x", "identifier", "/" + U + "/"),
    ("mail " + PERS2 + " now", "personal_email", PERS2), ("v " + PRIV + " w", "secret", PRIV)])
def test_excerpt_masks_sensitive_spans(rules, s, rule, secret):
    hits = scan_text(s, rules, "f")
    assert rule in names(hits)
    for h in hits:
        assert secret.lower() not in h.excerpt.lower() and h.excerpt == pc.WITHHELD


def test_excerpt_of_other_rule_masks_secret_on_same_line(rules):
    hits = scan_text(PRIV + " mail " + UNL, rules, "f")
    assert {"email", "secret"} <= set(names(hits))
    assert all(PRIV not in h.excerpt for h in hits)


# ---------------------------------------------------------------- fixture trees

@needs_fixtures
@pytest.mark.parametrize("fname,rule", [
    ("email_unlisted.txt", "email"), ("home_path.md", "home_path"), ("appdata.md", "home_path"),
    ("identifier.txt", "identifier"), ("username.txt", "identifier"), ("session_id.txt", "session_id"),
    ("pitch.txt", "pitch"), ("secret.py", "secret"), ("unsafe_domain.txt", "unsafe_domain"),
    ("librarian.txt", "librarian")])
def test_fixture_trees_hit(rules, fname, rule):
    hits = scan_tree([FIX / fname], rules, root=FIX)
    assert names(hits) == [rule]
    assert hits[0].path == fname and hits[0].line_no == 1


@needs_fixtures
@pytest.mark.parametrize("fname", ["email_allowed.txt", "clean_surname.txt"])
def test_fixture_trees_pass(rules, fname):
    assert scan_tree([FIX / fname], rules, root=FIX) == []


# ---------------------------------------------------------------- file selection

def test_scan_tree_reads_gz_and_skips_binary(tmp_path, rules):
    (tmp_path / "a.txt.gz").write_bytes(gzip.compress(("mail " + UNL + "\n").encode()))
    (tmp_path / "b.bin").write_bytes(b"\x00\x01" + UNL.encode())
    (tmp_path / "c.png").write_bytes(b"\x89PNG\x00\x00" + UNL.encode())
    (tmp_path / "d.gz").write_bytes(gzip.compress(b"\x00\x00" + UNL.encode()))
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "e.json").write_text('{"a": "fine"}', encoding="utf-8")
    hits = scan_tree([tmp_path], rules, root=tmp_path)
    assert [(h.path, h.rule) for h in hits] == [("a.txt.gz", "email")]


@pytest.mark.parametrize("fname", ["LICENSE", ".gitignore", "NOTES.TXT", "x.weird", "Makefile", "data.tsv"])
def test_any_text_file_is_scanned(tmp_path, rules, fname):
    (tmp_path / fname).write_text("mail " + UNL + "\n", encoding="utf-8")
    assert names(scan_tree([tmp_path], rules, root=tmp_path)) == ["email"]


@pytest.mark.parametrize("enc,bom", [("utf-16-le", codecs.BOM_UTF16_LE), ("utf-16-be", codecs.BOM_UTF16_BE),
                                     ("utf-8", codecs.BOM_UTF8), ("utf-16-le", b""),
                                     ("utf-32-le", codecs.BOM_UTF32_LE)])
def test_bom_and_utf16_files(tmp_path, rules, enc, bom):
    (tmp_path / "w.log").write_bytes(bom + ("ok\nmail " + UNL + "\n").encode(enc))
    hits = scan_tree([tmp_path], rules, root=tmp_path)
    assert [(h.rule, h.line_no) for h in hits] == [("email", 2)]


def test_corrupt_gz_and_bad_utf16_are_unreadable(tmp_path, rules):
    (tmp_path / "a.gz").write_bytes(b"\x1f\x8b\x08\x00garbage")
    (tmp_path / "b.txt").write_bytes(codecs.BOM_UTF16_LE + b"a\x00b")
    assert sorted(names(scan_tree([tmp_path], rules, root=tmp_path))) == ["unreadable", "unreadable"]


def test_private_file_in_tree_is_a_hit(tmp_path, rules):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "PRIVATE_notes.txt").write_text("anything", encoding="utf-8")
    hits = scan_tree([tmp_path / "sub"], rules, root=tmp_path)
    assert names(hits) == ["secret"] and "anything" not in hits[0].excerpt


def test_private_files_in_scanned_root_are_expected(tmp_path, rules):
    (tmp_path / "PRIVATE_notes.txt").write_text("anything", encoding="utf-8")
    (tmp_path / "ok.txt").write_text("fine", encoding="utf-8")
    assert scan_tree([tmp_path], rules, root=tmp_path) == []
    other = tmp_path / "pub"
    other.mkdir()
    (other / "PRIVATE_x.txt").write_text("y", encoding="utf-8")
    assert names(scan_tree([tmp_path], rules, root=tmp_path)) == ["secret"]


def test_skip_dirs(tmp_path, rules, monkeypatch):
    d = tmp_path / "tests" / "fixtures" / "privacy"
    d.mkdir(parents=True)
    (d / "x.txt").write_text(UNL, encoding="utf-8")
    for name in (".git", "__pycache__", ".pytest_cache"):
        (tmp_path / "tests" / name).mkdir()
        (tmp_path / "tests" / name / "y.txt").write_text(UNL, encoding="utf-8")
    (tmp_path / "tests" / "ok.txt").write_text("fine", encoding="utf-8")
    (tmp_path / TOOL).mkdir()
    (tmp_path / TOOL / "z.txt").write_text(UNL, encoding="utf-8")
    monkeypatch.setattr(pc, "SKIP_DIRS", ("tests/fixtures/privacy",))
    tooled = replace(rules, tool_dirs=(TOOL,))
    assert scan_tree([tmp_path / "tests"], tooled, root=tmp_path) == []
    assert scan_tree([tmp_path], tooled, root=tmp_path) == []
    # without a tool-directory list the same directory is an ordinary directory and is scanned
    assert [(h.path, h.rule) for h in scan_tree([tmp_path], rules, root=tmp_path)] == [(TOOL + "/z.txt", "email")]


def test_skip_dirs_scanned_under_ci(tmp_path, rules, monkeypatch):
    d = tmp_path / "tests" / "fixtures" / "privacy"
    d.mkdir(parents=True)
    (d / "x.txt").write_text("mail " + UNL + "\n", encoding="utf-8")
    (tmp_path / "tests" / "ok.txt").write_text("fine", encoding="utf-8")
    monkeypatch.setattr(pc, "SKIP_DIRS", ("tests/fixtures/privacy",))
    ci = replace(rules, ci=True)
    for target in (tmp_path, tmp_path / "tests", d, d / "x.txt"):
        hits = scan_tree([target], ci, root=tmp_path)
        assert [(h.path, h.rule) for h in hits] == [("tests/fixtures/privacy/x.txt", "email")], target
    assert scan_tree([tmp_path / "tests"], rules, root=tmp_path) == []  # local runs still skip it
    assert sorted(rel for _, rel in pc.iter_files([tmp_path], root=tmp_path, ci=True)) == [
        "tests/fixtures/privacy/x.txt", "tests/ok.txt"]


def test_tool_dir_inside_tree_is_a_hit(tmp_path, rules):
    pub = tmp_path / "pub"
    (pub / TOOL).mkdir(parents=True)
    (pub / TOOL / "z.txt").write_text("fine", encoding="utf-8")
    (pub / "ok.txt").write_text("fine", encoding="utf-8")
    hits = scan_tree([pub], replace(rules, tool_dirs=(TOOL,)), root=tmp_path)
    assert [(h.path, h.rule) for h in hits] == [("pub/" + TOOL, "secret")]
    assert scan_tree([pub], rules, root=tmp_path) == []  # no list: nothing special about the name


def test_tool_dir_at_root_is_a_hit_under_ci(tmp_path, rules):
    (tmp_path / TOOL).mkdir()
    (tmp_path / TOOL / "z.txt").write_text("fine", encoding="utf-8")
    (tmp_path / "ok.txt").write_text("fine", encoding="utf-8")
    hits = scan_tree([tmp_path], replace(rules, ci=True, tool_dirs=(TOOL,)), root=tmp_path)
    assert [(h.path, h.rule) for h in hits] == [(TOOL, "secret")]
    assert scan_tree([tmp_path], replace(rules, tool_dirs=(TOOL,)), root=tmp_path) == []  # local run: pruned
    assert [rel for _, rel in pc.iter_files([tmp_path], root=tmp_path, tool_dirs=(TOOL,))] == ["ok.txt"]


def test_load_tool_dirs(tmp_path):
    assert pc.load_tool_dirs(tmp_path) == ()
    (tmp_path / "local").mkdir()
    lines = ["# comment", "", TOOL + "/", ".other", TOOL, ""]
    (tmp_path / "local" / "tool_dirs.txt").write_text("\n".join(lines), encoding="utf-8")
    assert pc.load_tool_dirs(tmp_path) == (TOOL, ".other")


def test_tool_dir_name_marks_a_url_span_as_local(rules):
    s = "https://x.example/" + TOOL + "/" + UUID
    assert "session_id" not in rules_of(s, rules)
    assert "session_id" in rules_of(s, replace(rules, tool_dirs=(TOOL,)))


# ---------------------------------------------------------------- load_rules

def _root(tmp_path, ident=None):
    if ident is None:
        ident = U + "\n" + DEV + "\n" + PERS + "\n\nother-id\n"
    (tmp_path / "data").mkdir(exist_ok=True)
    if ident is not False:
        (tmp_path / "PRIVATE_identifiers.txt").write_text(ident, encoding="utf-8")
    (tmp_path / "data" / "author.json").write_text(json.dumps({"emails": [INST]}), encoding="utf-8")
    (tmp_path / "data" / "unsafe_domains.txt").write_text("# none\n", encoding="utf-8")  # a blocklist must exist
    return tmp_path


def test_load_rules(tmp_path):
    root = _root(tmp_path)
    (root / "data" / "unsafe_domains.txt").write_text("# c\n" + BAD + "   # note\n\n", encoding="utf-8")
    (root / "data" / "derived").mkdir()
    (root / "data" / "derived" / "allowed_contacts.txt").write_text(
        "# hdr\n" + ORG + "\n#no" + AT + "org.example\n", encoding="utf-8")
    (root / "data" / "derived" / "allowed_names.txt").write_text("# authors\nKim" + " Doe\n", encoding="utf-8")
    (root / "data" / "guide_sweep.csv").write_text(
        "resource_name,librarian\nA," + LIB + "\nB,UNVERIFIED\nC,Not listed\nD,N/A\nE,\nF,UNVERIFIED (no name)\n"
        "G,not named\nH,na\nI,\"Kim" + " Doe; Bo Wu\"\n", encoding="utf-8")
    (root / "PRIVATE_api_key.txt").write_text("  " + PRIV + "  \nshort\n------------\n", encoding="utf-8")
    r = load_rules(root)
    assert r.username == U and r.identifiers == [DEV, "other-id"] and r.personal_emails == {PERS}
    assert r.allowed_emails == {INST, ORG}
    assert r.unsafe_domains == {BAD} and r.librarian_names == {LIB, "Bo Wu"}
    assert r.private_strings == {PRIV}
    assert r.tool_dirs == ()
    (root / "local").mkdir()
    (root / "local" / "tool_dirs.txt").write_text(TOOL + "\n", encoding="utf-8")
    assert load_rules(root).tool_dirs == (TOOL,)
    assert load_rules(root, ci=True).tool_dirs == (TOOL,)  # under --ci they are reported, never pruned


def test_load_rules_missing_identifiers_exits_2(tmp_path, capsys):
    with pytest.raises(SystemExit) as e:
        load_rules(_root(tmp_path, ident=False))
    assert e.value.code == 2
    err = capsys.readouterr().err
    assert "first" in err and "@" in err and "--ci" in err and "#" in err


def test_load_rules_ci_mode_without_identifiers(tmp_path, capsys):
    r = load_rules(_root(tmp_path, ident=False), ci=True)
    assert r.username == "" and r.identifiers == [] and r.personal_emails == set()
    assert "warning" in capsys.readouterr().err.lower()


# ---------------------------------------------------------------- CLI

def test_cli_exit_codes(tmp_path, capsys):
    root = _root(tmp_path)
    tree = root / "pub"
    tree.mkdir()
    (tree / "ok.txt").write_text("hello " + INST, encoding="utf-8")
    assert pc.main([str(tree), "--root", str(root)]) == 0
    assert "privacy check passed: 1 files" in capsys.readouterr().out
    (tree / "bad.txt").write_text("ok\nmail " + UNL + "\n", encoding="utf-8")
    assert pc.main([str(tree / "bad.txt"), str(tree), "--root", str(root)]) == 1
    out = capsys.readouterr().out
    assert out.count("pub/bad.txt:2: email: mail " + UNL) == 1
    (root / "PRIVATE_identifiers.txt").unlink()
    with pytest.raises(SystemExit) as e:
        pc.main([str(tree), "--root", str(root)])
    assert e.value.code == 2


def test_cli_fail_closed(tmp_path, capsys):
    root = _root(tmp_path)
    assert pc.main(["--root", str(root)]) == 2
    assert pc.main([str(root / "nope"), "--root", str(root)]) == 2
    empty = root / "empty"
    empty.mkdir()
    assert pc.main([str(empty), "--root", str(root)]) == 2
    (empty / "x.bin").write_bytes(b"\x00\x00\x00")
    assert pc.main([str(empty), "--root", str(root)]) == 2
    (empty / "y.gz").write_bytes(b"not gzip")
    assert pc.main([str(empty), "--root", str(root)]) == 1


def test_cli_private_contents_rule(tmp_path, capsys):
    root = _root(tmp_path)
    (root / "PRIVATE_token.txt").write_text(PRIV + "\n", encoding="utf-8")
    tree = root / "pub"
    tree.mkdir()
    (tree / "a.md").write_text("value " + PRIV.upper() + "\n", encoding="utf-8")
    assert pc.main([str(tree), "--root", str(root)]) == 1
    out = capsys.readouterr().out
    assert "secret" in out and PRIV.upper() not in out


def test_cli_ci_mode(tmp_path, capsys):
    root = _root(tmp_path, ident=False)
    tree = root / "pub"
    tree.mkdir()
    (tree / "a.md").write_text("on " + DEV + "\n", encoding="utf-8")
    assert pc.main([str(tree), "--root", str(root), "--ci"]) == 0
    (tree / "b.md").write_text("mail " + UNL + "\n", encoding="utf-8")
    assert pc.main([str(tree), "--root", str(root), "--ci"]) == 1
    with pytest.raises(SystemExit) as e:
        pc.main([str(tree), "--root", str(root)])
    assert e.value.code == 2


# ---------------------------------------------------------------- learn_contacts

def test_learn_contacts_excludes_author(tmp_path):
    root = _root(tmp_path)
    (root / "data" / "catalog.csv").write_text(
        "id,maintainer,unverified_notes,institutional_access_note,programmatic_access,name,landing_url\n"
        "X-001,\"Team <Data" + AT + "Org.example>\",\"ask " + INST + " or help" + AT + "lab.example.\",,,"
        "\"n" + AT + "any.example\",\"q [a" + "t] lab [d" + "ot] example\"\n",
        encoding="utf-8")
    (root / "data" / "primary_archives.csv").write_text(
        "name,institutional_access_note\nA,mail arch" + AT + "lib.example\n", encoding="utf-8")
    want = ["arch" + AT + "lib.example", ORG, "help" + AT + "lab.example", "n" + AT + "any.example",
            "q" + AT + "lab.example"]
    assert learn_contacts(root) == want
    assert not (root / "data" / "derived").exists()
    assert pc.main(["--learn-contacts", "--root", str(root)]) == 0
    p = root / "data" / "derived" / "allowed_contacts.txt"
    text = p.read_text(encoding="utf-8")
    assert text.startswith("#") and ORG + "\n" in text
    # reviewer rejects one address and an old rejection no longer in the catalog survives too
    p.write_text(text.replace("n" + AT + "any.example\n", "#n" + AT + "any.example\n")
                 + "# old" + AT + "gone.example\n", encoding="utf-8")
    assert pc.main(["--learn-contacts", "--root", str(root)]) == 0
    lines = p.read_text(encoding="utf-8").splitlines()
    assert "#n" + AT + "any.example" in lines and "n" + AT + "any.example" not in lines
    assert "#old" + AT + "gone.example" in lines
    assert ORG in lines and lines[0].startswith("# ")
    r = load_rules(root)
    assert "n" + AT + "any.example" not in r.allowed_emails and ORG in r.allowed_emails


# ---------------------------------------------------------------- self-scan and timing

def test_self_scan_is_clean(rules):
    hits = scan_tree([SCANNER, HERE], rules, root=HERE.parent.parent)
    assert hits == [], [(h.path, h.line_no, h.rule, h.excerpt) for h in hits]


def test_timing_330k_lines_100_librarians(tmp_path, rules):
    rnd = random.Random(7)

    def word():
        return "".join(rnd.choice(string.ascii_lowercase) for _ in range(rnd.randint(4, 9))).capitalize()

    libs = {word() + " " + word() for _ in range(100)}
    r = replace(rules, librarian_names=libs)
    row = ('"REG-{i:05d}","Dataset {i}","https://example.org/data/{i}/x","Smith, Jo; Roe, Al",'
           '"country-year","expert coding",ok,2024-01-01,see path/to/file and 50% coverage & more')
    lines = [row.format(i=i) for i in range(330000)]
    lines[1234] += " " + sorted(libs)[0]
    (tmp_path / "big.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    t = time.perf_counter()
    hits = scan_tree([tmp_path], r, root=tmp_path)
    took = time.perf_counter() - t
    assert [(h.rule, h.line_no) for h in hits] == [("librarian", 1235)]
    assert took < 10, took


# ---------------------------------------------------------------- round 2

WH = pc.WITHHELD


@pytest.mark.parametrize("s", [
    "C:/" + USERS + "/bob/ " + U + " (a" + "t) example (d" + "ot) net",
    "mail " + UNL + " and " + PRIV.replace("-", "%2D"),
    UNL + " and " + DEV.replace("-", "%2D"),
])
def test_excerpts_withheld_when_masked_rule_hits_any_variant(rules, s):
    hits = scan_text(s, rules, "f")
    assert set(names(hits)) & {"identifier", "personal_email", "secret"}
    assert hits and all(h.excerpt == WH for h in hits)


def test_ci_withholds_email_excerpts(rules):
    assert scan_text("mail " + UNL, rules, "f")[0].excerpt == "mail " + UNL
    assert scan_text("mail " + UNL, replace(rules, ci=True), "f")[0].excerpt == WH


@pytest.mark.parametrize("s", [
    "API" + "_KEY=abcd1234efgh5678", "pass" + "word: hunter2hunter2", "DB_PASS" + "WORD=Xy9.k/+=abc",
    "  api" + "-key: AbCdEfGh12", "s" + "k-proj-" + "Ab1_" * 6, "s" + "k-ant-api03-" + "x" * 24,
    "OPENALEX_API" + "_KEY = AbCd-1234-EfGh"])
def test_secret_unquoted_values(rules, s):
    assert names(scan_text(s, rules, "f")) == ["secret"]


@pytest.mark.parametrize("s", [
    "api" + "_key: str | None = None", "self.api" + "_key = api_key", "ws.protection.pass" + "word = pw",
    "pass" + "word = os.environ.get('X')", "pass" + "word: Optional[str] = None", "api" + "_key = settings.api_key",
    "pass" + "word = None", "pass" + "word: registration-required", "pass" + "word: required.",
    "api" + "_key=self.something_long", "pass" + "word = get_secret(name)", "pass" + "word: ${{ secrets.X }}",
    "a risk-adjusted-measure-of-something-long", "api" + "_key = api_key_value"])
def test_secret_unquoted_non_values_pass(rules, s):
    assert scan_text(s, rules, "f") == []


def test_ci_root_private_files_are_hits(tmp_path, rules):
    (tmp_path / "PRIVATE_notes.txt").write_text("x", encoding="utf-8")
    (tmp_path / "ok.txt").write_text("fine", encoding="utf-8")
    assert scan_tree([tmp_path], rules, root=tmp_path) == []
    assert names(scan_tree([tmp_path], replace(rules, ci=True), root=tmp_path)) == ["secret"]


def test_private_prefix_case_insensitive(tmp_path, rules):
    (tmp_path / "Private_root.txt").write_text("x", encoding="utf-8")
    (tmp_path / "ok.txt").write_text("fine", encoding="utf-8")
    assert scan_tree([tmp_path], rules, root=tmp_path) == []
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "private_notes.md").write_text("x", encoding="utf-8")
    assert [(h.path, h.rule) for h in scan_tree([tmp_path], rules, root=tmp_path)] == [
        ("sub/private_notes.md", "secret")]


def test_private_contents_case_insensitive_file_name(tmp_path):
    root = _root(tmp_path)
    (root / "private_token.txt").write_text(PRIV + "\n", encoding="utf-8")
    assert load_rules(root).private_strings == {PRIV}


@pytest.mark.parametrize("ident", [b"", b"\n\n", b"# only a comment\n", PERS.encode() + b"\n",
                                   b"\x00\x01\x02\x00\x03\x00"])
def test_identifiers_file_without_username_exits_2(tmp_path, ident):
    root = _root(tmp_path, ident=False)
    (root / "PRIVATE_identifiers.txt").write_bytes(ident)
    with pytest.raises(SystemExit) as e:
        load_rules(root)
    assert e.value.code == 2
    assert load_rules(root, ci=True).username == ""


def test_identifiers_comments_ignored(tmp_path):
    r = load_rules(_root(tmp_path, ident="# my ids\n\n" + U + "\n# device\n" + DEV + "\n"))
    assert r.username == U and r.identifiers == [DEV]


def test_scan_tree_fails_closed(tmp_path, rules):
    with pytest.raises(pc.ScanError):
        scan_tree([], rules, root=tmp_path)
    with pytest.raises(pc.ScanError):
        scan_tree([tmp_path / "nope"], rules, root=tmp_path)
    with pytest.raises(pc.ScanError):
        scan_tree([tmp_path], rules, root=tmp_path)
    (tmp_path / "x.bin").write_bytes(b"\x00" * 10)
    with pytest.raises(pc.ScanError):
        scan_tree([tmp_path], rules, root=tmp_path)


def test_walk_errors_fail_closed(tmp_path, rules, monkeypatch):
    def bad_walk(top, onerror=None, **kw):
        onerror(OSError("permission denied"))
        return iter(())

    (tmp_path / "d").mkdir()
    monkeypatch.setattr(pc.os, "walk", bad_walk)
    with pytest.raises(pc.ScanError):
        scan_tree([tmp_path / "d"], rules, root=tmp_path)


@pytest.mark.parametrize("s", [
    U + " a" + "t example.net", U.upper() + " @ example.net", U + " [a" + "t] example (d" + "ot) net",
    U + " a" + "t example d" + "ot net", U + AT + "example [d" + "ot] net"])
def test_personal_email_spaced_forms(rules, s):
    assert "personal_email" in rules_of(s, rules)


@pytest.mark.parametrize("s", ["available a" + "t www.example.org",
                               "data a" + "t the d" + "ot level, see example.org",
                               "meet a" + "t noon d" + "ot level"])
def test_prose_at_dot_passes(rules, s):
    assert scan_text(s, rules, "f") == []


def test_text_with_few_nuls_is_scanned(tmp_path, rules):
    tail = b"\nmail " + UNL.encode() + b"\n"
    (tmp_path / "a.log").write_bytes(b"x" * 2000 + b"\x00" + tail)
    (tmp_path / "b.pdf").write_bytes(b"%PDF-1.4\n" + b"x" * 2000 + b"\x00" + tail)
    (tmp_path / "c.zip").write_bytes(b"PK\x03\x04" + b"x" * 2000 + b"\x00" + tail)
    assert [(h.path, h.rule, h.line_no) for h in scan_tree([tmp_path], rules, root=tmp_path)] == [
        ("a.log", "email", 2)]


def test_learn_contacts_excludes_personal_emails(tmp_path):
    root = _root(tmp_path)
    (root / "data" / "catalog.csv").write_text(
        "id,maintainer\nX-001,\"" + PERS + "; " + ORG + "; " + INST + "\"\n", encoding="utf-8")
    assert learn_contacts(root) == [ORG]


_BS = chr(92)
_HEX32 = "6121e572cb5e42f39150031ffb19abb4"
_UUID = "123e4567-e89b-12d3-a456-426614174000"


@pytest.mark.parametrize("s", [
    "https://example.org/p/overview?view_only=" + _HEX32 + " (view-only link)",
    "example.org/x?id=" + _UUID,
    "https://example.org/p?view_only=" + _HEX32 + "#frag",
    "https:"+_BS+"/"+_BS+"/example.org"+_BS+"/p?view_only=" + _HEX32,
    "example.org"+_BS+"/x?id=" + _UUID,
])
def test_session_id_exempt_in_url_query(rules, s):
    assert "session_id" not in names(scan_text(s, rules, "a.txt"))


@pytest.mark.parametrize("s", ["C:/tmp/" + _HEX32 + "/x", ".toolx/projects/" + _UUID + ".jsonl"])
def test_session_id_still_hits_local_paths(rules, s):
    assert "session_id" in names(scan_text(s, rules, "a.txt"))


def test_session_id_url_path_with_home_next_to_url(rules):
    s = ("https://lobbying.example.org/ho" + "me/data/,https://example.org/p/overview?view_only=" + _HEX32
         + " (view-only link),x")
    assert "session_id" not in names(scan_text(s, rules, "a.txt"))
    s2 = "https://lobbying.example.org/ho" + "me/data/;example.org/x?id=" + _UUID
    assert "session_id" not in names(scan_text(s2, rules, "a.txt"))


def test_session_id_local_path_next_to_url_still_hits(rules):
    s = "https://example.org/a/,/ho" + "me/zed-user/" + _HEX32 + "/x"
    assert "session_id" in names(scan_text(s, rules, "a.txt"))
    s2 = "/ho" + "me/zed-user/" + _HEX32 + "/x,https://example.org/a/"
    assert "session_id" in names(scan_text(s2, rules, "a.txt"))


@pytest.mark.parametrize("s", ["my.project/sessions/" + _UUID + ".jsonl", "C:/Us"+"ers/zz/x,notes.md/" + _UUID,
                               "notes.md/" + _UUID + "/x"])
def test_session_id_file_extension_is_not_a_host(rules, s):
    assert "session_id" in names(scan_text(s, rules, "a.txt"))
