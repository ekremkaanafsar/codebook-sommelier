import csv
import json
import subprocess
from pathlib import Path

import pytest

import export_public as ep

AT = chr(64)
MANIFEST = """# test manifest
README.md
data/catalog.csv
data/guide_sweep.csv
data/author.json
data/unsafe_domains.sha256
scripts/**
tests/**
"""
HEADER = ["resource_name", "guide_institution", "librarian", "date_seen"]
IDENTS = "fixtureuser\nfixture-box-9\n"


def write(p, text, newline=None):
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8", newline=newline) as fh:
        fh.write(text)


def make_repo(root, author_email="alice" + AT + "example.edu"):
    write(root / "public_manifest.txt", MANIFEST)
    write(root / "README.md", "# Demo\n")
    write(root / "data" / "catalog.csv", "id,name\nREG-001,Demo\n")
    write(root / "data" / "guide_sweep.csv",
          "resource_name,guide_institution,librarian,date_seen\nDemo,Example U,Jane Roe,2026-01-01\n")
    write(root / "data" / "author.json", json.dumps({"name": "Alice Example", "emails": [author_email]}))
    write(root / "data" / "unsafe_domains.sha256", "0" * 64 + "\n")
    write(root / "data" / "unsafe_domains.txt", "bad.example\n")
    write(root / "scripts" / "tool.py", "print('hi')\n")
    write(root / "scripts" / "__pycache__" / "tool.pyc", "x")
    write(root / "tests" / "fixtures" / "ok.json", '{"a": 1}\n')
    write(root / "tests" / "fixtures" / "privacy" / "deliberate.txt", "Draft prepared " + "for someone\n")
    # files that must never be exported
    write(root / "logs" / "x.md", "log\n")
    write(root / "data" / "research" / "y.json", "{}\n")
    write(root / "data" / "candidates" / "D01.md", "c\n")
    write(root / "release" / "r.txt", "r\n")
    write(root / "outputs" / "o.txt", "o\n")
    write(root / "docs" / "a.md", "doc\n")
    write(root / "PROJECT_STATUS.md", "status\n")
    write(root / "PRIVATE_z.txt", "private value here\n")
    write(root / "PRIVATE_identifiers.txt", IDENTS)  # fictional username and device name
    write(root / "site" / "index.html", "<html></html>\n")


def test_read_manifest_skips_comments(tmp_path):
    write(tmp_path / "m.txt", "# c\n\nREADME.md\n  scripts/**  \n")
    assert ep.read_manifest(tmp_path / "m.txt") == ["README.md", "scripts/**"]


def test_manifest_copy_and_exclusions(tmp_path):
    src, out = tmp_path / "src", tmp_path / "out"
    make_repo(src)
    assert ep.export(src, out) == 0
    assert (out / "README.md").read_text(encoding="utf-8") == "# Demo\n"
    assert (out / "scripts" / "tool.py").is_file()
    assert (out / "tests" / "fixtures" / "ok.json").is_file()
    for rel in ("logs/x.md", "data/research/y.json", "data/candidates/D01.md", "release/r.txt", "outputs/o.txt",
                "docs/a.md", "PROJECT_STATUS.md", "PRIVATE_z.txt", "site/index.html",
                "scripts/__pycache__/tool.pyc"):
        assert not (out / rel).exists(), rel


def test_unsafe_domains_txt_listed_by_mistake_fails_scan(tmp_path):
    src, out = tmp_path / "src", tmp_path / "out"
    make_repo(src)
    write(src / "public_manifest.txt", MANIFEST + "data/unsafe_domains.txt\n")
    assert ep.export(src, out) == 1  # the blocklist's own domains are scan hits


def test_unsafe_domains_txt_absent_with_real_manifest():
    root = ep.ROOT
    patterns = ep.read_manifest(root / "public_manifest.txt")
    names = {f.relative_to(root).as_posix() for f in ep.collect(root, patterns)}
    assert "data/unsafe_domains.txt" not in names
    assert "data/unsafe_domains.sha256" in names
    assert not any(n.startswith("tests/fixtures/privacy/") for n in names)


def test_real_manifest_exports_only_the_two_network_figures_from_outputs():
    root = ep.ROOT
    names = {f.relative_to(root).as_posix() for f in ep.collect(root, ep.read_manifest(root / "public_manifest.txt"))}
    assert {n for n in names if n.startswith("outputs/")} == {"outputs/figures/cousage_network.svg",
                                                              "outputs/figures/cousage_domains.svg"}
    assert ep.unmatched(root, ep.read_manifest(root / "public_manifest.txt")) == []


def test_changelog_not_exported_with_real_manifest():
    root = ep.ROOT
    names = {f.relative_to(root).as_posix() for f in ep.collect(root, ep.read_manifest(root / "public_manifest.txt"))}
    assert "CHANGELOG.md" not in names
    assert "templates/site/changelog.html" not in names


def test_gitleaks_config_exported_keeps_default_rules_and_allowlists_only_known_strings():
    import re
    import tomllib
    root = ep.ROOT
    names = {f.relative_to(root).as_posix() for f in ep.collect(root, ep.read_manifest(root / "public_manifest.txt"))}
    assert ".gitleaks.toml" in names
    cfg = tomllib.loads((root / ".gitleaks.toml").read_text(encoding="utf-8"))
    assert cfg["extend"]["useDefault"] is True
    allow = cfg["allowlist"]
    assert "paths" not in allow and "commits" not in allow  # no whole-file or whole-commit exemptions
    assert allow["regexTarget"] == "match"
    rx = [re.compile(r) for r in allow["regexes"]]
    # assignments are split so this file itself never holds a key-shaped line (privacy_check, gitleaks)
    known = ["api" + "_key = 'abcd1234efgh'", "API" + "_KEY=abcd1234efgh5678",
             "OPENALEX_API" + "_KEY = AbCd-1234-EfGh", "rlkey=" + "4rhdk7qmy8jz31t82v6mnueyy", "API, Snowflake/AWS"]
    assert all(any(r.search(k) for r in rx) for k in known)
    assert not any(r.search("API" + "_KEY=" + "Zq81kfLm02xVb7Tp") for r in rx)


def test_privacy_fixtures_not_exported(tmp_path):
    src, out = tmp_path / "src", tmp_path / "out"
    make_repo(src)
    assert ep.export(src, out) == 0
    assert not (out / "tests" / "fixtures" / "privacy").exists()


def test_fixture_failing_scan_is_skipped(tmp_path, capsys):
    src, out = tmp_path / "src", tmp_path / "out"
    make_repo(src)
    write(src / "tests" / "fixtures" / "leaky.txt", "write to bob" + AT + "mailhost.example.org\n")
    assert ep.export(src, out) == 0
    assert not (out / "tests" / "fixtures" / "leaky.txt").exists()
    assert (out / "tests" / "fixtures" / "ok.json").is_file()
    assert "skipped fixture" in capsys.readouterr().out


def test_fixture_with_same_names_copied_untouched(tmp_path):
    src, out = tmp_path / "src", tmp_path / "out"
    make_repo(src)
    fx = "tests/fixtures/mini/data/"
    write(src / fx / "guide_sweep.csv", "a,librarian\nx,y\n")
    write(src / fx / "author.json", "{}")
    assert ep.export(src, out) == 0
    assert (out / fx / "author.json").read_text(encoding="utf-8") == "{}"
    assert "librarian" in (out / fx / "guide_sweep.csv").read_text(encoding="utf-8")


def test_librarian_column_dropped(tmp_path):
    src, out = tmp_path / "src", tmp_path / "out"
    make_repo(src)
    assert ep.export(src, out) == 0
    with (out / "data" / "guide_sweep.csv").open(encoding="utf-8", newline="") as fh:
        rows = list(csv.reader(fh))
    assert rows[0] == ["resource_name", "guide_institution", "date_seen"]
    assert rows[1] == ["Demo", "Example U", "2026-01-01"]
    assert "Jane Roe" not in (out / "data" / "guide_sweep.csv").read_text(encoding="utf-8")


def test_transform_preserves_quoting_and_crlf(tmp_path):
    src, dst = tmp_path / "data" / "guide_sweep.csv", tmp_path / "o" / "data" / "guide_sweep.csv"
    write(src, 'a,librarian,b\r\n"x, y",L,"multi\nline"\r\n', newline="")
    ep.transform(src, dst)
    assert dst.read_bytes() == b'a,b\r\n"x, y","multi\nline"\r\n'


def test_transform_other_files_byte_for_byte(tmp_path):
    src, dst = tmp_path / "a.csv", tmp_path / "o" / "a.csv"
    src.write_bytes(b"x,librarian\r\n1,2\r\n\xff")
    ep.transform(src, dst)
    assert dst.read_bytes() == src.read_bytes()


def test_author_json_with_extra_address_fails(tmp_path, capsys):
    src, out = tmp_path / "src", tmp_path / "out"
    make_repo(src)
    write(src / "data" / "author.json",
          json.dumps({"name": "A", "emails": ["a" + AT + "example.edu", "b" + AT + "mailhost.example.org"]}))
    assert ep.export(src, out) == 1
    assert "institutional" in capsys.readouterr().err


def test_author_json_address_in_other_field_fails(tmp_path):
    src, out = tmp_path / "src", tmp_path / "out"
    make_repo(src)
    write(src / "data" / "author.json",
          json.dumps({"name": "A", "emails": ["a" + AT + "example.edu"], "contact": "z" + AT + "example.edu"}))
    assert ep.export(src, out) == 1


def test_failing_check_aborts_nonzero(tmp_path, monkeypatch):
    src, out = tmp_path / "src", tmp_path / "out"
    make_repo(src)
    write(src / "README.md", "contact carol" + AT + "mailhost.example.org\n")
    _with_git_dir(out)
    calls = []
    monkeypatch.setattr(subprocess, "run", _fake_run(calls, " M README.md\n"))
    assert ep.export(src, out, push=True) == 1
    assert calls and _no_mutation(calls)  # only the read-only preflight queries ran


class _Done:
    def __init__(self, stdout="", returncode=0):
        self.stdout = stdout
        self.returncode = returncode


def _fake_run(calls, porcelain, toplevel=None, email="a" + AT + "example.edu", name="A Example",
              remotes="origin\n"):
    """A stand-in for subprocess.run that answers the push preflight queries; no real git is ever run."""
    def run(*a, **k):
        cmd = a[0]
        calls.append((a, k))
        args = cmd[3:] if cmd[1:2] == ["-C"] else cmd[1:]
        out = Path(cmd[2]) if cmd[1:2] == ["-C"] else Path(k.get("cwd"))
        if args[:2] == ["rev-parse", "--show-toplevel"]:
            return _Done((toplevel if toplevel is not None else out.as_posix()) + "\n")
        if args[:3] == ["config", "--local", "--get"]:
            v = {"user.email": email, "user.name": name}[args[3]]
            return _Done(v + "\n" if v else "", 0 if v else 1)
        if args[:1] == ["remote"]:
            return _Done(remotes)
        if args[:1] == ["status"]:
            return _Done(porcelain)
        return _Done("")
    return run


def _git_args(calls):
    return [c[0][0][3:] if c[0][0][1:2] == ["-C"] else c[0][0][1:] for c in calls]


def _with_git_dir(out):
    (out / ".git").mkdir(parents=True, exist_ok=True)


def test_push_only_with_flag(tmp_path, monkeypatch):
    src, out = tmp_path / "src", tmp_path / "out"
    make_repo(src)
    _with_git_dir(out)
    calls = []
    monkeypatch.setattr(subprocess, "run", _fake_run(calls, " M README.md\n"))
    assert ep.export(src, out) == 0
    assert calls == []
    assert ep.export(src, out, push=True) == 0
    args = _git_args(calls)
    assert args[:5] == [["rev-parse", "--show-toplevel"], ["config", "--local", "--get", "user.name"],
                        ["config", "--local", "--get", "user.email"], ["remote"], ["add", "-A"]]
    assert args[5] == ["status", "--porcelain"]
    assert args[6][:2] == ["commit", "-m"] and args[6][2].startswith("export 20")
    assert args[7] == ["push", "-u", "origin", "HEAD:main"]
    assert len(args) == 8
    for (a, k) in calls:
        assert a[0][:3] == ["git", "-C", str(out.resolve())]
        assert k.get("cwd") == out.resolve()
        assert not any("://" in x or AT in x for x in a[0])  # never a URL


def test_push_with_no_changes_skips_commit_and_push(tmp_path, monkeypatch, capsys):
    src, out = tmp_path / "src", tmp_path / "out"
    make_repo(src)
    _with_git_dir(out)
    calls = []
    monkeypatch.setattr(subprocess, "run", _fake_run(calls, ""))
    assert ep.export(src, out, push=True) == 0
    assert [a[0] for a in _git_args(calls)][-2:] == ["add", "status"]
    assert "nothing to export" in capsys.readouterr().out


def _no_mutation(calls):
    for a in _git_args(calls):
        if a[0] in ("add", "commit", "push", "init", "status"):
            return False
        if a[0] == "remote" and len(a) > 1:
            return False
        if a[0] == "config" and "--get" not in a:
            return False
    return True


def test_push_refused_without_own_git_dir(tmp_path, monkeypatch, capsys):
    src, out = tmp_path / "src", tmp_path / "out"
    make_repo(src)
    calls = []
    monkeypatch.setattr(subprocess, "run", _fake_run(calls, " M README.md\n"))
    assert ep.export(src, out, push=True) == 2
    assert calls == []
    assert not (out / ".git").exists()
    assert ".git" in capsys.readouterr().err


def test_push_refused_when_repository_is_the_parent(tmp_path, monkeypatch, capsys):
    src, out = tmp_path / "src", tmp_path / "parent" / "out"
    make_repo(src)
    _with_git_dir(out)
    calls = []
    monkeypatch.setattr(subprocess, "run",
                        _fake_run(calls, " M README.md\n", toplevel=(tmp_path / "parent").as_posix()))
    assert ep.export(src, out, push=True) == 2
    assert calls and _no_mutation(calls)
    assert "own repository" in capsys.readouterr().err


@pytest.mark.parametrize("field", ["email", "name"])
def test_push_refused_without_repo_local_identity(tmp_path, monkeypatch, capsys, field):
    src, out = tmp_path / "src", tmp_path / "out"
    make_repo(src)
    _with_git_dir(out)
    calls = []
    monkeypatch.setattr(subprocess, "run", _fake_run(calls, " M README.md\n", **{field: ""}))
    assert ep.export(src, out, push=True) == 2
    assert calls and _no_mutation(calls)
    assert "user." + field in capsys.readouterr().err


def test_push_refused_without_origin_remote(tmp_path, monkeypatch, capsys):
    src, out = tmp_path / "src", tmp_path / "out"
    make_repo(src)
    _with_git_dir(out)
    calls = []
    monkeypatch.setattr(subprocess, "run", _fake_run(calls, " M README.md\n", remotes="upstream\n"))
    assert ep.export(src, out, push=True) == 2
    assert calls and _no_mutation(calls)
    assert "origin" in capsys.readouterr().err


def test_push_refused_when_git_cannot_run(tmp_path, monkeypatch, capsys):
    src, out = tmp_path / "src", tmp_path / "out"
    make_repo(src)
    _with_git_dir(out)

    def boom(*a, **k):
        raise OSError("git not found")
    monkeypatch.setattr(subprocess, "run", boom)
    assert ep.export(src, out, push=True) == 2


def test_push_preflight_runs_before_any_copy(tmp_path, monkeypatch):
    src, out = tmp_path / "src", tmp_path / "out"
    make_repo(src)
    _with_git_dir(out)
    write(out / "keep.txt", "untouched\n")
    monkeypatch.setattr(subprocess, "run", _fake_run([], "", remotes=""))
    assert ep.export(src, out, push=True) == 2
    assert (out / "keep.txt").read_text(encoding="utf-8") == "untouched\n"


def test_missing_identifiers_file_fails_export(tmp_path, capsys):
    src, out = tmp_path / "src", tmp_path / "out"
    make_repo(src)
    (src / "PRIVATE_identifiers.txt").unlink()
    assert ep.export(src, out) != 0
    assert not out.exists()
    assert "PRIVATE_identifiers.txt" in capsys.readouterr().err


def test_empty_identifiers_file_fails_export(tmp_path):
    src, out = tmp_path / "src", tmp_path / "out"
    make_repo(src)
    write(src / "PRIVATE_identifiers.txt", "# nothing here\n\n")
    assert ep.export(src, out) != 0
    assert not out.exists()


def test_identifiers_apply_to_export_scan(tmp_path):
    src, out = tmp_path / "src", tmp_path / "out"
    make_repo(src)
    write(src / "scripts" / "note.py", "# built on fixture-box-9\n")
    assert ep.export(src, out) == 1


def test_export_scan_runs_in_ci_mode(tmp_path, monkeypatch):
    src, out = tmp_path / "src", tmp_path / "out"
    make_repo(src)
    seen = []
    real = ep.pc.scan_tree

    def spy(dirs, rules, root=None):
        seen.append(rules.ci)
        return real(dirs, rules, root=root)
    monkeypatch.setattr(ep.pc, "scan_tree", spy)
    assert ep.export(src, out) == 0
    assert seen and all(seen)


def test_collect_double_star_selects_nested_files(tmp_path):
    write(tmp_path / "scripts" / "a.py", "a\n")
    write(tmp_path / "scripts" / "sub" / "b.py", "b\n")
    write(tmp_path / "scripts" / "sub" / "deeper" / "c.txt", "c\n")
    write(tmp_path / ".github" / "workflows" / "site.yml", "on: push\n")
    write(tmp_path / "README.md", "r\n")
    got = [f.relative_to(tmp_path).as_posix()
           for f in ep.collect(tmp_path, ["scripts/**", ".github/**", "README.md"])]
    assert got == [".github/workflows/site.yml", "README.md", "scripts/a.py", "scripts/sub/b.py",
                   "scripts/sub/deeper/c.txt"]


def test_collect_exact_path_selects_file(tmp_path):
    write(tmp_path / "data" / "catalog.csv", "id\n")
    write(tmp_path / "data" / "other.csv", "id\n")
    assert ep.collect(tmp_path, ["data/catalog.csv"]) == [tmp_path / "data" / "catalog.csv"]


def test_manifest_pattern_matching_nothing_fails_export(tmp_path, capsys):
    src, out = tmp_path / "src", tmp_path / "out"
    make_repo(src)
    write(src / "public_manifest.txt", MANIFEST + "templates/**\n")
    assert ep.export(src, out) == 2
    assert "templates/**" in capsys.readouterr().err
    assert not out.exists()


def test_author_json_public_copy_drops_password_file_key(tmp_path):
    src, out = tmp_path / "src", tmp_path / "out"
    make_repo(src)
    data = {"name": "Alice Å", "emails": ["alice" + AT + "example.edu"], "sheet_password_file": "PRIVATE_x.txt",
            "year": 2026}
    original = json.dumps(data)
    write(src / "data" / "author.json", original)
    assert ep.export(src, out) == 0
    text = (out / "data" / "author.json").read_text(encoding="utf-8")
    assert text == json.dumps({k: v for k, v in data.items() if k != "sheet_password_file"}, indent=2,
                              ensure_ascii=False) + "\n"
    assert (src / "data" / "author.json").read_text(encoding="utf-8") == original


@pytest.mark.parametrize("emails,extra", [
    (["a" + AT + "example.edu", "b" + AT + "example.edu"], {}),
    (["a" + AT + "example.com"], {}),
    (["a" + AT + "school.ac"], {}),
    (["a" + AT + "example.edu"], {"contact_email": "x"}),
    ([], {}),
])
def test_author_email_check_strict(tmp_path, emails, extra):
    src, out = tmp_path / "src", tmp_path / "out"
    make_repo(src)
    write(src / "data" / "author.json", json.dumps({"name": "A", "emails": emails, **extra}))
    assert ep.export(src, out) == 1


@pytest.mark.parametrize("addr", ["a" + AT + "example.edu", "a" + AT + "uni.ac.uk", "a" + AT + "uni.edu.au"])
def test_author_email_check_accepts_institutional(tmp_path, addr):
    src, out = tmp_path / "src", tmp_path / "out"
    make_repo(src, author_email=addr)
    assert ep.export(src, out) == 0


def test_clear_keeps_git_dir(tmp_path):
    src, out = tmp_path / "src", tmp_path / "out"
    make_repo(src)
    write(out / ".git" / "HEAD", "ref\n")
    write(out / "stale.txt", "old\n")
    assert ep.export(src, out) == 0
    assert (out / ".git" / "HEAD").is_file()
    assert not (out / "stale.txt").exists()


def test_private_file_in_output_is_a_hit(tmp_path, monkeypatch):
    src, out = tmp_path / "src", tmp_path / "out"
    make_repo(src)
    real = ep.collect

    def leaky(root, patterns):
        return real(root, patterns) + [root / "PRIVATE_z.txt"]
    monkeypatch.setattr(ep, "collect", leaky)
    assert ep.export(src, out) == 1


def test_out_inside_or_equal_root_refused(tmp_path):
    make_repo(tmp_path)
    assert ep.export(tmp_path, tmp_path) == 2
    assert ep.export(tmp_path, tmp_path.parent) == 2


def test_cli_default_out_is_gitignored():
    ignore = (ep.ROOT / ".gitignore").read_text(encoding="utf-8").split()
    assert "public/" in ignore


# ---------------------------------------------------------------- terminology check (local/marker_terms.txt)
# Fictional terms only: the real local lists are never exported.
TERMS = "# fictional\n" + r"\bzorbtool\b" + "\nquux-bot\n"
ALLOW = "# fictional allowlist\nDr Zorbtool Smith\n"


def _with_markers(src, allow=True):
    write(src / "local" / "marker_terms.txt", TERMS)
    if allow:
        write(src / "local" / "marker_allow.txt", ALLOW)


def test_marker_check_passes_when_clean(tmp_path, capsys):
    src, out = tmp_path / "src", tmp_path / "out"
    make_repo(src)
    _with_markers(src)
    assert ep.export(src, out) == 0
    o = capsys.readouterr().out
    assert "terminology check passed: 2 patterns, 1 allowed phrases" in o and "privacy check passed" in o
    assert not (out / "local").exists()  # local configuration is never exported


def test_marker_hit_fails_export_with_path_line_and_term(tmp_path, capsys):
    src, out = tmp_path / "src", tmp_path / "out"
    make_repo(src)
    _with_markers(src)
    write(src / "README.md", "# Demo\nBuilt with ZorbTool and a Quux-Bot.\n")
    assert ep.export(src, out) == 1
    o = capsys.readouterr().out
    assert "README.md:2: ZorbTool" in o and "README.md:2: Quux-Bot" in o
    assert "Built with" not in o  # only the matched term is shown, never the line


def test_marker_allowlist_covers_only_its_own_span(tmp_path):
    pats, allow = ep.load_markers(_markers_root(tmp_path))
    assert ep._marker_matches("cited: Dr Zorbtool Smith (2020)", pats, allow) == []
    assert ep._marker_matches("Dr Zorbtool Smith used zorbtool", pats, allow) == ["zorbtool"]
    assert ep._marker_matches("data &#113;uux-bot", pats, allow) == ["quux-bot"]  # HTML-unescaped variant


def _markers_root(tmp_path):
    _with_markers(tmp_path)
    return tmp_path


def test_marker_check_without_allowlist_and_in_paths(tmp_path):
    root = tmp_path / "r"
    _with_markers(root, allow=False)
    pats, allow = ep.load_markers(root)
    assert allow == []
    out = tmp_path / "o"
    write(out / "docs" / "ZORBTOOL.md", "plain\n")
    write(out / "a.txt", "line one\nDr Zorbtool Smith\n")
    write(out / ".git" / "x.txt", "zorbtool\n")
    assert ep.marker_hits(out, pats, allow) == [("a.txt", 2, "Zorbtool"), ("docs/ZORBTOOL.md", 0, "ZORBTOOL")]


def test_marker_check_skipped_without_terms_file(tmp_path, capsys):
    src, out = tmp_path / "src", tmp_path / "out"
    make_repo(src)
    write(src / "README.md", "# Demo zorbtool\n")
    assert ep.load_markers(src) is None
    assert ep.export(src, out) == 0
    assert "terminology check skipped" in capsys.readouterr().out


def test_bad_marker_pattern_stops_export_before_copying(tmp_path, capsys):
    src, out = tmp_path / "src", tmp_path / "out"
    make_repo(src)
    write(src / "local" / "marker_terms.txt", "(unclosed\n")
    assert ep.export(src, out) == 2
    assert not out.exists()
    assert "does not compile" in capsys.readouterr().err


def test_real_manifest_never_exports_local_config():
    root = ep.ROOT
    names = {f.relative_to(root).as_posix() for f in ep.collect(root, ep.read_manifest(root / "public_manifest.txt"))}
    assert not any(n.startswith("local/") for n in names)
    assert "PROTOCOL.md" in names
