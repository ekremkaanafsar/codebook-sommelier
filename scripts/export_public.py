"""Public export: copy the allowlisted subset of this repository into public/ and privacy-check it (spec section 9).

Usage: export_public.py [--out public] [--push]

Copies the paths listed in public_manifest.txt into the output directory (cleared first, except .git), applying
transforms, then scans the result with privacy_check. The rules load as for a local run, so a missing, empty or
unreadable PRIVATE_identifiers.txt stops the export (exit 2) before anything is written; the scan of the output then
runs in CI mode (PRIVATE_* files are never exempt, email excerpts are withheld). A manifest pattern that matches no
file also stops the export (exit 2). Patterns ending in "/**" select every file below that directory on every
Python version.

Alongside the privacy scan, a terminology check runs over the output when local/marker_terms.txt exists under the
repository root: every line of every text file (and every relative path) is matched against its case-insensitive
regular expressions, and a match counts unless it lies inside one of the exact substrings in local/marker_allow.txt
on the same line. Hits are printed as path:line: matched text and fail the export (exit 1). Without the terms file
a notice is printed and the check is skipped. local/ is local configuration and is never exported.

Exit 0 only when both checks pass. Nothing is pushed unless --push is given and the scan passed. With --push the
script first refuses (exit 2, before copying anything) unless the output directory is a git repository of its own
(its .git exists and "git rev-parse --show-toplevel" names the output directory itself, never a parent repository),
its repo-local config sets user.name and user.email, and it has a remote named origin. The script never creates a
repository, never sets an identity and never adds a remote: the author does that by hand in the output directory.
The push runs "git add -A", "git commit" and "git push -u origin HEAD:main" there, with no URL arguments.
"""
import argparse
import csv
import datetime
import html
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import privacy_check as pc

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = "public_manifest.txt"
FIXTURES = "tests/fixtures"
NEVER_EXPORT = ("tests/fixtures/privacy",)  # deliberate hits for the scanner's own tests
INSTITUTIONAL_RE = re.compile(r"^[^@\s]+@[^@\s]+(\.edu|\.ac\.[a-z]{2}|\.edu\.[a-z]{2})$")
SKIP_NAMES = {".git", "__pycache__", ".pytest_cache"}
MARKER_TERMS = "local/marker_terms.txt"
MARKER_ALLOW = "local/marker_allow.txt"


class ExportError(Exception):
    """The export cannot proceed; the message names the problem."""


def _config_lines(path):
    """Stripped, non-blank lines of a local config file that do not start with "#"."""
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        v = line.strip()
        if v and not v.startswith("#"):
            out.append(v)
    return out


def load_markers(root):
    """(compiled patterns, allowed substrings) from local/ under root, or None when the terms file is absent.
    A pattern that does not compile raises ExportError."""
    terms = Path(root) / MARKER_TERMS
    if not terms.is_file():
        return None
    pats = []
    for t in _config_lines(terms):
        try:
            pats.append(re.compile(t, re.I))
        except re.error as e:
            raise ExportError(f"{MARKER_TERMS}: pattern {t!r} does not compile ({e})")
    allow_p = Path(root) / MARKER_ALLOW
    allow = _config_lines(allow_p) if allow_p.is_file() else []
    return pats, allow


def _marker_matches(line, pats, allow):
    """Matched texts on one line (raw and HTML-unescaped) that no allowlisted substring covers."""
    out = []
    for v in dict.fromkeys((line, html.unescape(line))):
        spans = []
        for a in allow:
            i = v.find(a)
            while i >= 0:
                spans.append((i, i + len(a)))
                i = v.find(a, i + 1)
        for p in pats:
            for m in p.finditer(v):
                if not any(s <= m.start() and m.end() <= e for s, e in spans) and m.group(0) not in out:
                    out.append(m.group(0))
    return out


def marker_hits(out, pats, allow):
    """[(rel, line_no, matched text)] for every non-allowlisted match under out (.git and caches skipped; line 0
    is the file's relative path). Binary files are skipped; .gz files are read decompressed."""
    out = Path(out)
    hits = []
    for f in sorted(out.rglob("*")):
        if not f.is_file():
            continue
        rel = f.relative_to(out).as_posix()
        if any(part in SKIP_NAMES for part in f.relative_to(out).parts):
            continue
        hits.extend((rel, 0, t) for t in _marker_matches(rel, pats, allow))
        text, err = pc._read_text(f)
        if text is None:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            hits.extend((rel, i, t) for t in _marker_matches(line, pats, allow))
    return hits


def read_manifest(path):
    """Patterns from a manifest file; blank lines and lines starting with # are ignored."""
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        v = line.strip()
        if v and not v.startswith("#"):
            out.append(v)
    return out


def _excluded(root, f):
    rel = f.relative_to(root)
    if any(part in SKIP_NAMES or part.upper().startswith("PRIVATE_") for part in rel.parts):
        return True
    if f.suffix == ".pyc":
        return True
    r = rel.as_posix()
    return any(r == n or r.startswith(n + "/") for n in NEVER_EXPORT)


def _expand(root, pat):
    """Files matched by one manifest pattern. "dir/**" is every file below dir (rglob), the same on Python 3.12 and
    3.13+ (a trailing "**" in Path.glob matches directories only up to 3.12, files and directories from 3.13)."""
    if pat.endswith("/**"):
        base = root / pat[:-3]
        cands = base.rglob("*") if base.is_dir() else []
    else:
        cands = root.glob(pat)
    return [f for f in cands if f.is_file() and not _excluded(root, f)]


def collect(root, patterns):
    """Sorted, de-duplicated files under root matched by the glob patterns (PRIVATE_*, caches, .git never match)."""
    root = Path(root)
    found = set()
    for pat in patterns:
        found.update(_expand(root, pat))
    return sorted(found)


def unmatched(root, patterns):
    """Manifest patterns that select no file."""
    root = Path(root)
    return [pat for pat in patterns if not _expand(root, pat)]


def _check_author(src):
    try:
        data = json.loads(src.read_text(encoding="utf-8"))
    except ValueError as e:
        raise ExportError(f"{src.name} is not valid JSON: {e}")
    emails = data.get("emails")
    problem = None
    if not isinstance(emails, list) or len(emails) != 1:
        problem = "an 'emails' field that does not hold exactly one address"
    elif not INSTITUTIONAL_RE.search(str(emails[0]).strip().lower()):
        problem = "an address that is not institutional"
    else:
        rest = {k: v for k, v in data.items() if k != "emails"}
        if any("mail" in k.lower() for k in rest) or "@" in json.dumps(rest, ensure_ascii=False):
            problem = "an address outside the 'emails' field"
    if problem:
        raise ExportError("data/author.json must contain only the institutional email address (found "
                          + problem + "); fix it before exporting")


def transform(src, dst):
    """Copy src to dst; guide_sweep.csv loses its librarian column, everything else is copied byte for byte."""
    src, dst = Path(src), Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if src.name == "author.json" and src.parent.name == "data":
        _check_author(src)
        data = json.loads(src.read_text(encoding="utf-8"))
        data.pop("sheet_password_file", None)  # names a PRIVATE_ file: not for the public copy
        dst.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
        return
    if src.name == "guide_sweep.csv" and src.parent.name == "data":
        raw = src.read_bytes()
        eol = "\r\n" if b"\r\n" in raw else "\n"
        with src.open(encoding="utf-8", newline="") as fh:
            rows = list(csv.reader(fh))
        if rows and "librarian" in rows[0]:
            i = rows[0].index("librarian")
            rows = [r[:i] + r[i + 1:] for r in rows]
        with dst.open("w", encoding="utf-8", newline="") as fh:
            csv.writer(fh, lineterminator=eol).writerows(rows)
        return
    shutil.copyfile(src, dst)


def _clear(out):
    out.mkdir(parents=True, exist_ok=True)
    for child in out.iterdir():
        if child.name == ".git":
            continue
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()


def _clean_fixtures(root, rules, files):
    """Fixture files that are text and pass the scanner; the rest are skipped with a printed notice."""
    bad = set()
    if files:
        try:
            for h in pc.scan_tree([root / FIXTURES], rules, root=root):
                bad.add(h.path)
        except pc.ScanError:
            pass
    keep = []
    for f in files:
        rel = f.relative_to(root).as_posix()
        text, err = pc._read_text(f)
        if rel in bad or err or text is None:
            print(f"skipped fixture (fails scan or not text): {rel}")
        else:
            keep.append(f)
    return keep


def _git(out, *args):
    """Run a git command inside out (-C out and cwd=out; never with a URL)."""
    return subprocess.run(["git", "-C", str(out), *args], cwd=out, capture_output=True, text=True)


def push_preflight(out):
    """None when out is a git repository of its own with a repo-local identity and an origin remote, otherwise the
    reason it is not. Runs only read-only git queries and changes nothing."""
    if not (out / ".git").is_dir():
        return (f"{out} has no .git of its own. Create the public repository by hand in that directory (git init, "
                "a repo-local user.name and user.email, a remote named origin); the script never does this")
    try:
        top = _git(out, "rev-parse", "--show-toplevel")
        if top.returncode != 0 or not (top.stdout or "").strip():
            return f"git does not recognise {out} as a repository"
        if Path((top.stdout or "").strip()).resolve() != out.resolve():
            return (f"git resolves {out} to a different repository; the output directory must be its own "
                    "repository, never part of the parent one")
        for key in ("user.name", "user.email"):
            got = _git(out, "config", "--local", "--get", key)
            if got.returncode != 0 or not (got.stdout or "").strip():
                return (f"{key} is not set in the repo-local config of {out}; set it there by hand "
                        "(a global identity is never used)")
        rem = _git(out, "remote")
        if rem.returncode != 0 or "origin" not in (rem.stdout or "").split():
            return f"{out} has no remote named origin; add it there by hand"
    except OSError as e:
        return f"git cannot run: {e}"
    return None


def export(root, out, push=False):
    root, out = Path(root).resolve(), Path(out).resolve()
    if out == root or out in root.parents:
        print("export: output directory must not be the repository or contain it", file=sys.stderr)
        return 2
    if push:
        problem = push_preflight(out)
        if problem:
            print(f"export: push refused: {problem}. Nothing was copied or pushed.", file=sys.stderr)
            return 2
    patterns = read_manifest(root / MANIFEST)
    missing = unmatched(root, patterns)
    if missing:
        print("export: manifest pattern(s) match no file: " + ", ".join(missing), file=sys.stderr)
        return 2
    files = collect(root, patterns)
    try:
        rules = pc.load_rules(root, ci=False)  # local semantics: the identifiers file is required
    except SystemExit:
        print("export: privacy rules could not be loaded (see above); nothing exported", file=sys.stderr)
        return 2
    rules.ci = True  # scan of out: no PRIVATE_ exemptions, email excerpts withheld
    try:
        markers = load_markers(root)
    except ExportError as e:
        print(f"export: {e}; nothing exported", file=sys.stderr)
        return 2
    fixtures =[f for f in files if f.relative_to(root).as_posix().startswith(FIXTURES + "/")]
    fixtures_set = set(fixtures)
    skip = set(fixtures) - set(_clean_fixtures(root, rules, fixtures))
    _clear(out)
    n = 0
    try:
        for f in files:
            if f in skip:
                continue
            dst = out / f.relative_to(root)
            if f in fixtures_set:  # fixtures keep their own data/ layouts: copied as they are
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(f, dst)
            else:
                transform(f, dst)
            n += 1
    except ExportError as e:
        print(f"export failed: {e}", file=sys.stderr)
        return 1
    try:
        hits = pc.scan_tree([out], rules, root=out)
    except pc.ScanError as e:
        print(f"privacy check: {e} (fail closed)", file=sys.stderr)
        return 2
    if markers is None:
        print(f"terminology check skipped: {MARKER_TERMS} not found")
        mhits = []
    else:
        mhits = marker_hits(out, *markers)
    if mhits:
        for rel, line_no, term in mhits:
            print(f"{rel}:{line_no}: {term}")
        print(f"terminology check FAILED: {len(mhits)} hits; nothing pushed", file=sys.stderr)
    elif markers is not None:
        print(f"terminology check passed: {len(markers[0])} patterns, {len(markers[1])} allowed phrases")
    if hits:
        for h in hits:
            print(f"{h.path}:{h.line_no}: {h.rule}: {h.excerpt}")
        print(f"privacy check FAILED: {len(hits)} hits; nothing pushed", file=sys.stderr)
        return 1
    if mhits:
        return 1
    print(f"privacy check passed: {n} files copied to {out}")
    if push:
        stamp = datetime.date.today().isoformat()
        g = ["git", "-C", str(out)]
        try:
            subprocess.run(g + ["add", "-A"], cwd=out, check=True)
            st = subprocess.run(g + ["status", "--porcelain"], cwd=out, check=True, capture_output=True, text=True)
            if not (st.stdout or "").strip():
                print("nothing to export")
                return 0
            subprocess.run(g + ["commit", "-m", f"export {stamp}"], cwd=out, check=True)
            subprocess.run(g + ["push", "-u", "origin", "HEAD:main"], cwd=out, check=True)
        except (subprocess.CalledProcessError, OSError) as e:
            print(f"push failed: {e}", file=sys.stderr)
            return 1
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Export the allowlisted public subset and privacy-check it.")
    ap.add_argument("--out", default="public")
    ap.add_argument("--push", action="store_true",
                    help="after a passing check: git add, commit, push -u origin HEAD:main inside --out (which must "
                         "already be its own repository with a repo-local identity and an origin remote)")
    a = ap.parse_args(argv)
    out = Path(a.out)
    return export(ROOT, out if out.is_absolute() else ROOT / out, push=a.push)


if __name__ == "__main__":
    sys.exit(main())
