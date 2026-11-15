"""Privacy scanner: blocks publication of private information (spec section 10).

Usage: privacy_check.py PATH [PATH ...] [--root ROOT] [--ci]
       privacy_check.py --learn-contacts [--root ROOT]
Exit 0: "privacy check passed: N files". Exit 1: hits listed as path:line: rule: excerpt.
Exit 2 (fail closed): no paths, a path that does not exist, a directory that cannot be walked, zero text files
scanned (scan_tree raises ScanError for these), or, without --ci, a PRIVATE_identifiers.txt that is missing, cannot
be decoded or has no username line.

Rules (hit names): email, personal_email, home_path, identifier, session_id, pitch, secret, unsafe_domain,
librarian, unreadable.

Decoded variants. Every rule runs on the raw line and on decoded variants of it: backslash runs turned into "/",
JSON unescaping (backslash-u escapes, escaped slash, escaped backslash), URL percent-decoding (repeated until
stable, at most 3 rounds), HTML entity decoding, and email de-obfuscation. The transforms are applied up to three
times in combination. Hits are deduplicated per (line, rule); the first variant that hits supplies the excerpt.
De-obfuscation always rewrites bracketed forms ([at] (at) {at} <at>, [dot] (dot) ...), the encoded at-sign
(HTML decimal entity, %40) and whitespace around "@". The plain word "at" becomes "@" only in a plausible address
"local at label dot ... tld" whose domain uses the plain word "dot" at least once and ends in a known TLD or a
two-letter one, so "available at www.example.org" and "data at the dot level" stay prose. Each personal address
also has a targeted pattern: its local part, "@"/"at"/[at]/(at) with optional spaces, and its domain with plain
or obfuscated dots.

PRIVATE_identifiers.txt (git-ignored, at --root). Lines containing "@" are personal email addresses. The FIRST
other line is the OS username; the remaining lines are device names or other identifiers.
- The username equals the author's public given name (ruling P5), so it is matched only where it acts as a
  username: as a path segment with a slash or backslash on at least one side and a separator, end of token or
  end of line on the other (drive paths, home directories, UNC shares, "name/..." at the start of a token), and
  directly before "@" (user@host or an email local part) unless that full address is allowlisted. "@name"
  (social handles) is deliberately not matched. A bare word, author.json, CITATION.cff and the site footer pass.
- Device names and other identifiers: whole word, case-insensitive (so hostnames and UNC shares are covered).
- Personal emails: anywhere as a case-insensitive substring, and their local part (if at least 4 characters and
  not equal to the username, see P5) as a whole word. Rule personal_email.
- PRIVATE_* contents (spec section 10, rule 6): every other PRIVATE_* file at --root contributes its stripped lines
  of at least 6 characters that contain a letter or digit; any case-insensitive substring occurrence is a secret
  hit. PRIVATE_identifiers.txt is not used this way: substring matching of the username would undo P5.
  The PRIVATE_ prefix is matched case-insensitively. In PRIVATE_identifiers.txt blank lines and "#" lines are
  ignored.
- Excerpts: when an identifier, personal_email or secret rule hits a line in ANY variant, every hit on that line
  shows WITHHELD instead of an excerpt (an encoded copy of the value could sit in the raw text). Under --ci the
  same applies to lines with an email hit, so CI logs never print addresses.
With --ci the identifiers file is optional (a warning is printed and the identifier/personal_email checks are
skipped); other PRIVATE_* files are still used if present, and nothing is exempt: PRIVATE_* files and a
local tool directory directly in --root are hits too, and SKIP_DIRS are scanned like any other directory. Every
other rule always applies. In the public repository's CI the librarian rule has no names to match (the export drops
the guide_sweep.csv librarian column), so the export's own scan, run with the private copy's names, is the
librarian guard.

Other rules.
- email: an address needs a dot-separated domain ending in an alphabetic TLD of 2+ letters that is not an image
  extension; "user@host:path" (scp/ssh remotes) is not an email. Allowed: author.json emails and
  data/derived/allowed_contacts.txt lines not starting with "#".
- home_path: drive-letter user-profile folders, the macOS profile root, /c/ and /mnt/c/ forms of the Windows
  profile root, /home/<name> (followed by "/" or the end of the token), and the Windows application-data folder
  only as a path segment (so a domain that merely contains the word passes). "~/" is out of scope.
- session_id: a 32-hex run or a UUID inside a path token (a token holding "/" or a backslash), except inside an
  http(s)/other-scheme URL, a scheme-less URL that starts with a hostname, or a DOI token; file:// URLs and spans with local markers (drive letter, profile roots,
  home, application data, Temp, /tmp/, a local tool directory name) are never exempt.
- pitch: the pitch-draft heading phrase and the pitch-note name (underscore or space), case-insensitive.
- secret: key/password assignments with a quoted literal value, or with an unquoted value of 8+ characters from
  [A-Za-z0-9_-./+=] that does not look like code (not followed by "(" or "[", not a self./os./settings. reference,
  not None/True/False/a type name, not a lower-case snake/dotted identifier or a letters-only or kebab-case
  lower-case word); bearer tokens, GitHub tokens, sk- tokens (incl. sk-proj-/sk-ant-); PRIVATE_* contents (above);
  any PRIVATE_* file inside a scanned tree, except, without --ci, the PRIVATE_* files directly in --root when --root
  itself is scanned; and a local tool directory anywhere except directly in --root. Contents are never printed.
- unsafe_domain: any domain in data/unsafe_domains.txt, also as a subdomain; when that file is absent, any host
  whose SHA-256 (or whose parent domain's) is in data/unsafe_domains.sha256.
- librarian: names parsed from the guide_sweep.csv librarian column (see parse_librarian_cell), matched as whole
  phrases with any whitespace between tokens, also in "Last, First" order; names listed in
  data/derived/allowed_names.txt (dataset authors/maintainers) are exempt.
- unreadable: a file that cannot be read, a corrupt .gz, or a BOM-marked file that does not decode.

File selection: every file is sniffed. UTF-8/16/32 BOMs are honoured. With NUL bytes in the first 8 KB a file is
BOM-less UTF-16 (recognised by its NUL pattern), binary (a known binary signature or NULs >= 1% of the sample;
skipped), or text with stray NULs (scanned). Text is decoded as UTF-8 with replacement. .gz files are decompressed
and then sniffed. Skipped: directories .git, __pycache__, .pytest_cache anywhere, and, not under --ci, local tool
directories directly in --root and SKIP_DIRS (relative to --root; tests/fixtures/privacy holds planted hits and is
never exported, so under --ci any copy of it is scanned).

Local tool directories: the names listed in local/tool_dirs.txt under --root (one per line; blank lines and "#"
lines ignored), a local file that is never exported. Without that file no directory is treated this way, so under
--ci in the public repository nothing extra is pruned or reported.

Performance: each rule runs only on lines that contain one of its cheap literal triggers (found with str.find on
the lower-cased file) plus every line that has decoded variants; librarian names are located through their
longest token, so a 330k-line file scans in a few seconds.

Regex sources are assembled from fragments so that this file passes its own scan. Writes nothing except with
--learn-contacts.
"""
import argparse
import bisect
import codecs
import csv
import gzip
import hashlib
import html
import json
import os
import re
import sys
import zlib
from dataclasses import dataclass, field
from itertools import accumulate
from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent.parent
SKIP_DIRS = ("tests/fixtures/privacy",)
SKIP_NAMES = {".git", "__pycache__", ".pytest_cache"}
TOOL_DIRS_FILE = "local/tool_dirs.txt"  # local tool directories: pruned directly under --root, a secret hit elsewhere
IDENT_FILE = "PRIVATE_identifiers.txt"
PRIVATE_PREFIX = "PRIVATE_"
RULE_ORDER = ("email", "personal_email", "home_path", "identifier", "session_id", "pitch", "secret",
              "unsafe_domain", "librarian")
MASKED = {"identifier", "personal_email", "secret"}  # a hit withholds every excerpt of its line
EXCERPT_MAX = 80
MAX_VARIANTS = 24
CONTACTS_HEADER = ("# Organisational contact addresses found in the catalog. Review: prefix a line with # to reject "
                   "it.\n# Rejected or removed addresses will fail privacy_check.py. Rejections survive "
                   "regeneration.\n")

AT = "@"
_US = "Use" + "rs"
_AD = "App" + "Data"
IMAGE_TLDS = {"png", "jpg", "jpeg", "gif", "svg", "webp", "bmp", "ico", "tif", "tiff", "avif", "heic"}

_LOCAL_CH = r"A-Za-z0-9._%+\-"
EMAIL_RE = re.compile(r"(?<![" + _LOCAL_CH + r"])[" + _LOCAL_CH + r"]+" + AT
                      + r"((?:[A-Za-z0-9](?:[A-Za-z0-9\-]*[A-Za-z0-9])?\.)+([A-Za-z]{2,}))(?![A-Za-z0-9\-])")
HOME_RES = [re.compile(p, re.I) for p in (
    r"[A-Za-z]:[/\\]+" + _US + r"[/\\]",
    r"(?<![A-Za-z0-9_.\-])(?:/mnt)?/[A-Za-z]/" + _US + "/",
    r"(?<![A-Za-z0-9_.\-])/" + _US + "/",
    r"(?<![A-Za-z0-9_.\-])/ho" + r"me/[^/\s\"'<>`]+(?:/|(?=[\s\"'<>`),;\]]|$))",
    r"(?:[/\\]|(?<![A-Za-z0-9])[A-Za-z]:)" + _AD + r"[/\\]")]
HOME_TRIGGERS = ("use" + "rs", "/ho" + "me/", _AD.lower())
_HEX = r"[0-9a-fA-F]"
SESSION_RE = re.compile(r"(?<![0-9A-Za-z])(?:%s{8}-%s{4}-%s{4}-%s{4}-%s{12}|%s{32})(?![0-9A-Za-z])"
                        % ((_HEX,) * 6))
SESSION_TRIGGER = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}-|[0-9a-f]{24})")
_TOKEN_STOP = "\\s\"'<>`"
URL_RE = re.compile(r"(?<![A-Za-z0-9+.\-])(?!fil" + r"e:)[A-Za-z][A-Za-z0-9+.\-]*://[^" + _TOKEN_STOP + "]+", re.I)
_FILE_EXTS = ("md|txt|csv|json|py|js|html|htm|xml|yml|yaml|log|jsonl|pdf|zip|gz|png|jpg|svg|docx|xlsx|tsv|ipynb|sh|toml|cfg|ini")
HOSTURL_RE = re.compile(r"(?:[a-z0-9-]+\.)+(?!(?:" + _FILE_EXTS + r")(?![a-z0-9-]))[a-z]{2,}(?::\d+)?/[^" + _TOKEN_STOP + "]*", re.I)
DOI_RE = re.compile(r"(?<![0-9A-Za-z.])10\.\d{4,9}/[^" + _TOKEN_STOP + "]+")
_LOCAL_MARK = (r"(?<![A-Za-z0-9])[A-Za-z]:[/\\]|/" + _US + "/|/ho" + "me/|" + _AD
               + r"|[/\\]Te" + r"mp[/\\]|/tm" + "p/")
LOCAL_MARK_RE = re.compile(_LOCAL_MARK, re.I)
PITCH_RES = [re.compile(r"Draft\s+prepared\s+fo[r]", re.I), re.compile(r"pitch[_ ]not[e]", re.I)]
PITCH_TRIGGERS = ("prepared", "pitch")
_Q = "[\"']"
_VAL_CH = r"A-Za-z0-9_\-./+="
# key/password assignments: a quoted literal, or an unquoted value of 8+ characters that is not code (see
# _is_code_value); group "u" holds the unquoted value
SECRET_KV_RES = [re.compile(name + _Q + r"?\s*[:=]\s*(?:" + _Q + r"[^\"'\s]{%d,}" % n + _Q
                            + r"|(?P<u>[" + _VAL_CH + r"]{8,})(?![" + _VAL_CH + r"]))")
                 for name, n in ((r"(?i:api[_-]?key)", 8), (r"(?i:pass" + r"word)", 4))]
SECRET_RES = [re.compile(p) for p in (
    r"Bear" + r"er [A-Za-z0-9._-]{20,}",
    r"gh" + r"p_[A-Za-z0-9]{30,}",
    r"github" + r"_pat_[A-Za-z0-9_]{30,}",
    r"(?<![A-Za-z0-9])s" + r"k-[A-Za-z0-9_\-]{20,}")]
_PY_IDENT = re.compile(r"[a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)*")
_KEBAB = re.compile(r"[a-z]+(?:-[a-z]+)+")
_CODE_WORDS = {"str", "int", "bytes", "bool", "float", "none", "true", "false", "null", "optional", "any",
               "secretstr", "string", "required", "undefined"}
_CODE_PREFIXES = ("self.", "cls.", "os.", "env.", "settings.", "config.", "args.", "kwargs")
SECRET_TRIGGERS = ("key", "pass" + "word", "bear" + "er", "gh" + "p_", "github" + "_pat_", "s" + "k-")

# variants
_AT_BR = re.compile(r"\s*[\[\(\{<]\s*a" + r"t\s*[\]\)\}>]\s*", re.I)
_DOT_BR = re.compile(r"\s*[\[\(\{<]\s*do" + r"t\s*[\]\)\}>]\s*", re.I)
_AT_SPACED = re.compile(r"\s+" + AT + r"\s*|\s*" + AT + r"\s+")
_DOT_WORD_SEP = re.compile(r"\s+do" + r"t\s+", re.I)
_DOMAIN_SEP = re.compile(r"\.|\s+do" + r"t\s+", re.I)
_DOMAIN_PART = r"[A-Za-z0-9][\w\-]*(?:(?:\.|\s+do" + r"t\s+)[A-Za-z0-9][\w\-]*)+"
_PLAIN_ADDR = re.compile(r"(?<![\w.+\-])([A-Za-z0-9][\w.+\-]*)\s+a" + r"t\s+(" + _DOMAIN_PART + ")", re.I)
_AT_DOMAIN = re.compile(AT + "(" + _DOMAIN_PART + ")", re.I)
PLAUSIBLE_TLDS = {"com", "org", "net", "edu", "gov", "mil", "int", "info", "biz", "io", "ai", "app", "dev", "eu",
                  "example", "test", "local", "museum", "coop", "aero", "name", "pro", "online", "email", "global"}
BINARY_SIGNATURES = (b"PK\x03\x04", b"PK\x05\x06", b"%PDF", b"\x89PNG", b"GIF8", b"\xff\xd8\xff", b"\x1f\x8b",
                     b"\x7fELF", b"MZ", b"Rar!", b"7z\xbc\xaf", b"\xd0\xcf\x11\xe0")
WITHHELD = "<line withheld: contains private value>"
_JSON_ESC = re.compile(r"\\(u[0-9a-fA-F]{4}|[\"\\/bfnrt])")
_JSON_SIMPLE = {'"': '"', "\\": "\\", "/": "/", "b": " ", "f": " ", "n": " ", "r": " ", "t": " "}
DEOBF_TRIGGERS = tuple(w + sp + c for w in ("a" + "t", "do" + "t") for sp in ("", " ") for c in "])}>") + (
    " do" + "t ", "&#" + "64;", "%" + "40")
_DEOBF_CLOSE_RE = re.compile(r"(?:a" + r"t|do" + r"t) ?[\]\)\}>]")
_PCT_RE = re.compile(r"%[0-9A-Fa-f]{2}")
# a bare "%" or "&" (50% coverage, A & B) cannot change under unquote / html.unescape: only %XX, &# and &name can
_VARIANT_RE = re.compile(r"%[0-9A-Fa-f]{2}|&[#A-Za-z0-9]")
VARIANT_TRIGGERS = ("\\", " " + AT, AT + " ", "\t" + AT, AT + "\t") + DEOBF_TRIGGERS

# librarian cell parsing
_NAME_TOKEN = re.compile(r"[^\W\d_](?:[^\W\d_]|['\u2019.\-])*")
_PARTICLES = {"de", "la", "le", "van", "von", "der", "den", "da", "di", "du", "del", "dos", "bin", "al", "el", "y"}
_PLACEHOLDER_RE = re.compile(
    r"\b(?:unverified|not\s+listed|not\s+named(?:\s+on\s+(?:the\s+)?page)?|no\s+(?:individual|librarian|name)s?"
    r"(?:\s+(?:listed|named|given))?|none|unknown)\b", re.I)
_ROLE_RE = re.compile(
    r"\b(?:guide\s+(?:owner|author|creator)|subject\s+(?:specialist|librarian)|liaison\s+librarian|librarian"
    r"|liaison|specialist|curator|contact|owner|author|adapted\s+from|(?:maintained|created|compiled)\s+by|by"
    r"|dr|prof|professor|mrs?|ms)\b\.?", re.I)
_ORG_RE = re.compile(
    r"\b(?:librar(?:y|ies|ians)|teams?|cent(?:er|re)s?|programm?e?s?|services?|department|dept|universit(?:y|ies)"
    r"|college|institute|collections?|office|staff|committee|archives?|unit|group|desk|division|school|faculty)\b",
    re.I)
_CELL_SPLIT = re.compile(r"[;/&()\[\]]|\s+and\s+", re.I)


@dataclass
class Rules:
    allowed_emails: set = field(default_factory=set)
    identifiers: list = field(default_factory=list)  # device names and other identifiers (whole words)
    unsafe_domains: set = field(default_factory=set)
    librarian_names: set = field(default_factory=set)
    personal_emails: set = field(default_factory=set)
    username: str = ""  # first non-@ line of PRIVATE_identifiers.txt (path-segment / user@ matching only)
    private_strings: set = field(default_factory=set)  # lines of the other PRIVATE_* files
    allowed_names: set = field(default_factory=set)  # data/derived/allowed_names.txt
    ci: bool = False  # --ci: no root exemptions, email excerpts withheld
    unsafe_hashes: set = field(default_factory=set)  # SHA-256 hex of listed domains (hash mode: no names available)
    tool_dirs: tuple = ()  # local tool directory names (local/tool_dirs.txt); empty when that file is absent


@dataclass
class Hit:
    path: str
    line_no: int
    rule: str
    excerpt: str


# ------------------------------------------------------------------ text helpers

def _lower(s):
    """Lower-case without changing the length (only U+0130 expands under str.lower)."""
    return s.replace("\u0130", "i").lower()


def _decode(data):
    """Return (text, error). text None and error None means binary."""
    try:
        if data.startswith(codecs.BOM_UTF32_LE) or data.startswith(codecs.BOM_UTF32_BE):
            return data.decode("utf-32"), None
        if data.startswith(codecs.BOM_UTF8):
            return data[3:].decode("utf-8", errors="replace"), None
        if data.startswith(codecs.BOM_UTF16_LE) or data.startswith(codecs.BOM_UTF16_BE):
            return data.decode("utf-16"), None
        head = data[:8192]
        if b"\x00" in head:
            ev, od = head[0::2], head[1::2]
            if len(head) >= 4 and od.count(0) >= 0.9 * len(od) and ev.count(0) <= 0.1 * len(ev):
                return data.decode("utf-16-le"), None
            if len(head) >= 4 and ev.count(0) >= 0.9 * len(ev) and od.count(0) <= 0.1 * len(od):
                return data.decode("utf-16-be"), None
            if head.startswith(BINARY_SIGNATURES) or head.count(0) >= 0.01 * len(head):
                return None, None
            # a text file with a few stray NULs: still scanned
    except UnicodeError as e:
        return None, f"undecodable ({e.__class__.__name__})"
    return data.decode("utf-8", errors="replace"), None


def _read_text(f):
    """Return (text, error); (None, None) for binary."""
    try:
        data = f.read_bytes()
    except OSError as e:
        return None, f"unreadable ({e.__class__.__name__})"
    if f.name.lower().endswith(".gz"):
        try:
            data = gzip.decompress(data)
        except (OSError, EOFError, zlib.error) as e:
            return None, f"corrupt gzip ({e.__class__.__name__})"
    return _decode(data)


def _t_slash(s):
    return re.sub(r"\\+", "/", s) if "\\" in s else s


def _t_json(s):
    if "\\" not in s:
        return s

    def rep(m):
        g = m.group(1)
        return chr(int(g[1:], 16)) if g[0] == "u" else _JSON_SIMPLE[g]

    t = _JSON_ESC.sub(rep, s)
    try:
        t = t.encode("utf-16", "surrogatepass").decode("utf-16")
    except UnicodeError:
        pass
    return t


def _t_unquote(s):
    t = s
    for _ in range(3):
        if "%" not in t or not _PCT_RE.search(t):
            break
        u = unquote(t)
        if u == t:
            break
        t = u
    return t


def _t_html(s):
    return html.unescape(s) if "&" in s else s


def _plain_addr(m):
    dom = m.group(2)
    if not _DOT_WORD_SEP.search(dom):
        return m.group(0)  # plain "at" needs an obfuscated dot as well
    labels = _DOMAIN_SEP.split(dom)
    tld = labels[-1].lower()
    if not (tld in PLAUSIBLE_TLDS or (len(tld) == 2 and tld.isalpha())):
        return m.group(0)
    return m.group(1) + AT + ".".join(labels)


def _t_deobf(s):
    ls = s.lower()
    if not ("&#" + "64;" in s or "%" + "40" in s or " do" + "t " in ls or " " + AT in s or AT + " " in s
            or _DEOBF_CLOSE_RE.search(ls)):
        return s
    t = s.replace("&#" + "64;", AT).replace("%" + "40", AT)
    t = _AT_BR.sub(AT, t)
    t = _DOT_BR.sub(".", t)
    t = _AT_SPACED.sub(AT, t)
    if " do" + "t " in t.lower():
        t = _PLAIN_ADDR.sub(_plain_addr, t)
        t = _AT_DOMAIN.sub(lambda m: AT + ".".join(_DOMAIN_SEP.split(m.group(1))), t)
    return t


_TRANSFORMS = (_t_slash, _t_json, _t_unquote, _t_html, _t_deobf)


def _variants(s):
    out, seen, frontier = [s], {s}, [s]
    for _ in range(3):
        new = []
        for v in frontier:
            for f in _TRANSFORMS:
                w = f(v)
                if w not in seen and len(seen) < MAX_VARIANTS:
                    seen.add(w)
                    out.append(w)
                    new.append(w)
        if not new:
            break
        frontier = new
    return out


def _emails_in(s):
    """Yield (match, lower-cased address) for every email-like token that is a real address."""
    for m in EMAIL_RE.finditer(s):
        if m.group(2).lower() in IMAGE_TLDS:
            continue
        nxt = s[m.end():m.end() + 2]
        if len(nxt) == 2 and nxt[0] == ":" and (nxt[1].isalnum() or nxt[1] in "~/._"):
            continue  # scp/ssh remote such as user@host:path
        yield m, m.group(0).lower()


def _is_code_value(v, nxt):
    """True when an unquoted key/password value looks like code (a name, call, type or prose), not a secret."""
    v = v.rstrip(".,;:")
    lv = v.lower()
    if len(v) < 8 or (nxt and nxt in "(["):
        return True
    if lv in _CODE_WORDS or lv.startswith(_CODE_PREFIXES):
        return True
    if _PY_IDENT.fullmatch(v) and ("_" in v or "." in v or not any(c.isdigit() for c in v)):
        return True
    return bool(_KEBAB.fullmatch(v))


def _spaced_email_re(p):
    """A personal address also written with spaces or obfuscated at/dot (local part may equal the username)."""
    local, dom = p.split(AT, 1)
    sep = r"(?:\.|\s+do" + r"t\s+|\s*[\[\(\{]do" + r"t[\]\)\}]\s*)"
    at = r"\s*(?:" + AT + r"|\ba" + r"t\b|[\[\(\{]a" + r"t[\]\)\}])\s*"
    return re.compile(r"(?<![a-z0-9._%+\-])" + re.escape(local) + at
                      + sep.join(re.escape(x) for x in dom.split(".")) + r"(?![a-z0-9\-])")


def _word_re(v):
    return re.compile(r"(?<![a-z0-9])" + re.escape(_lower(v)) + r"(?![a-z0-9])")


def _norm_name(n):
    return " ".join(_lower(n).split())


def _name_patterns(name):
    toks = name.split()
    if len(toks) < 2:
        return []
    ws = r"[^\S\n]+"

    def tok(t):
        return "".join("['\u2019\u2018`]" if c in "'\u2019\u2018`" else re.escape(c) for c in _lower(t))

    phrase = ws.join(tok(t) for t in toks)
    first = ws.join(tok(t) for t in toks[:-1])
    rev = tok(toks[-1]) + r",[^\S\n]*" + first
    return [re.compile(r"(?<!\w)" + p + r"(?!\w)") for p in (phrase, rev)]


def _anchor(name):
    parts = re.findall(r"[^\W\d_]+", _lower(name))
    return max(parts, key=len) if parts else ""


# ------------------------------------------------------------------ compiled rules

_HOST_RE = re.compile(r"(?<![a-z0-9\-])(?:[a-z0-9\-]+\.)+[a-z]{2,}(?![a-z0-9\-])", re.I)


def _starts_url(s, i):
    """True when a scheme URL or a host-prefixed URL begins at s[i]: a ',' or ';' before it separates two tokens."""
    return bool(URL_RE.match(s, i) or HOSTURL_RE.match(s, i))


class _Compiled:
    def __init__(self, rules):
        self.allowed = {e.strip().lower() for e in rules.allowed_emails if e.strip()}
        self.personal = sorted({e.strip().lower() for e in rules.personal_emails if e.strip()})
        self.username = (rules.username or "").strip().lower()
        locs = {p.split(AT)[0] for p in self.personal}
        self.locals = [(lp, _word_re(lp)) for lp in sorted(locs) if len(lp) >= 4 and lp != self.username]
        self.spaced = [(p.split(AT)[0], _spaced_email_re(p)) for p in self.personal if AT in p]
        self.ci = bool(getattr(rules, "ci", False))
        self.tool_dirs = tuple(sorted({d.strip() for d in getattr(rules, "tool_dirs", ()) if d.strip()}))
        self.local_mark = (re.compile(_LOCAL_MARK + "".join("|" + re.escape(d) for d in self.tool_dirs), re.I)
                           if self.tool_dirs else LOCAL_MARK_RE)
        u = re.escape(self.username)
        end = r"(?![\w\-])(?!\.\w)"
        self.user_res = [
            re.compile(r"(?<=[/\\])" + u + end),
            re.compile(r"(?:^|(?<=[\s\"'`(\[<=:,;]))" + u + r"(?=[/\\])"),
        ] if self.username else []
        self.user_at = re.compile(r"(?<![" + _LOCAL_CH + r"])" + u + AT) if self.username else None
        self.devices = [(_lower(d.strip()), _word_re(d.strip())) for d in rules.identifiers if d.strip()]
        self.private = sorted({_lower(s.strip()) for s in rules.private_strings if s.strip()})
        self.domains = [(d, re.compile(r"(?<![a-z0-9\-])" + re.escape(d) + r"(?![a-z0-9\-])"))
                        for d in sorted({_lower(d.strip()) for d in rules.unsafe_domains if d.strip()})]
        self.hashes = frozenset(h.strip().lower() for h in rules.unsafe_hashes if h.strip())
        allowed_names = {_norm_name(n) for n in rules.allowed_names}
        self.names = {}  # anchor -> [regex]
        for n in sorted(rules.librarian_names):
            if _norm_name(n) in allowed_names:
                continue
            a, pats = _anchor(n), _name_patterns(n)
            if a and pats:
                self.names.setdefault(a, []).extend(pats)

    # each finder takes (s, sl) and returns [(start, end)]
    def f_email(self, s, sl):
        out = []
        for m, e in _emails_in(s):
            if e in self.personal or e in self.allowed:
                continue
            out.append(m.span())
        return out

    def f_personal_email(self, s, sl):
        out = []
        for p in self.personal:
            i = sl.find(p)
            while i != -1:
                out.append((i, i + len(p)))
                i = sl.find(p, i + 1)
        for lp, r in self.locals:
            if lp in sl:
                out.extend(m.span() for m in r.finditer(sl))
        for lp, r in self.spaced:
            if lp in sl:
                out.extend(m.span() for m in r.finditer(sl))
        return out

    def f_home_path(self, s, sl):
        for r in HOME_RES:
            m = r.search(s)
            if m:
                return [m.span()]
        return []

    def f_identifier(self, s, sl):
        out = []
        if self.username and self.username in sl:
            for r in self.user_res:
                out.extend(m.span() for m in r.finditer(sl))
            for m in self.user_at.finditer(sl):
                em = EMAIL_RE.match(s, m.start())
                if em and em.group(0).lower() in self.allowed:
                    continue
                out.append((m.start(), m.end() - 1))
        for d, r in self.devices:
            if d in sl:
                out.extend(m.span() for m in r.finditer(sl))
        return out

    def f_session_id(self, s, sl):
        out = []
        for m in SESSION_RE.finditer(s):
            a = m.start()
            while a > 0 and not (s[a - 1].isspace() or s[a - 1] in "\"'<>`"
                                 or (s[a - 1] in ",;" and _starts_url(s, a))):
                a -= 1
            b = m.end()
            while b < len(s) and not (s[b].isspace() or s[b] in "\"'<>`"
                                      or (s[b] in ",;" and _starts_url(s, b + 1))):
                b += 1
            tok = s[a:b]
            if "/" not in tok and "\\" not in tok:
                continue
            ms, me = m.start() - a, m.end() - a
            tok = tok.replace("\\/", "//")  # JSON-escaped slashes: same length, so the URL regexes still see a URL
            exempt = False
            hu = HOSTURL_RE.match(tok)
            if hu and hu.group(0).lower().endswith(".jsonl"):
                hu = None  # a session-transcript file name, not a web page
            for u in list(URL_RE.finditer(tok)) + list(DOI_RE.finditer(tok)) + ([hu] if hu else []):
                if u.start() <= ms and me <= u.end() and not self.local_mark.search(u.group(0)):
                    exempt = True
                    break
            if not exempt:
                out.append(m.span())
        return out

    def f_pitch(self, s, sl):
        return [m.span() for r in PITCH_RES for m in [r.search(s)] if m]

    def f_secret(self, s, sl):
        out = [m.span() for r in SECRET_RES for m in r.finditer(s)]
        for r in SECRET_KV_RES:
            for m in r.finditer(s):
                if m.group("u") is not None and _is_code_value(m.group("u"), s[m.end():m.end() + 1]):
                    continue
                out.append(m.span())
        for p in self.private:
            i = sl.find(p)
            while i != -1:
                out.append((i, i + len(p)))
                i = sl.find(p, i + 1)
        return out

    def _hash_spans(self, sl):
        out = []
        for m in _HOST_RE.finditer(sl):
            labels = m.group(0).split(".")
            if any(hashlib.sha256(".".join(labels[i:]).encode("utf-8")).hexdigest() in self.hashes
                   for i in range(len(labels) - 1)):
                out.append(m.span())
        return out

    def f_unsafe_domain(self, s, sl):
        out = self._hash_spans(sl) if self.hashes else []
        for d, r in self.domains:
            if d in sl:
                out.extend(m.span() for m in r.finditer(sl))
        return out

    def f_librarian(self, s, sl):
        out = []
        for a, pats in self.names.items():
            if a in sl:
                for r in pats:
                    out.extend(m.span() for m in r.finditer(sl))
        return out

    def triggers(self):
        """rule -> literal lower-case needles; a line holding none of them cannot hit the rule (raw form)."""
        ident = ([self.username] if self.username else []) + [d for d, _ in self.devices]
        return {
            "email": [AT],
            "personal_email": self.personal + [lp for lp, _ in self.locals] + [lp for lp, _ in self.spaced],
            "home_path": list(HOME_TRIGGERS),
            "identifier": ident,
            "pitch": list(PITCH_TRIGGERS),
            "secret": list(SECRET_TRIGGERS) + self.private,
            "unsafe_domain": [d for d, _ in self.domains],
            "librarian": list(self.names),
        }


# ------------------------------------------------------------------ scanning

def _lines_with(low, needle, starts, out):
    n = len(starts)
    p = low.find(needle)
    while p != -1:
        i = bisect.bisect_right(starts, p) - 1
        out.add(i)
        if i + 1 >= n:
            break
        p = low.find(needle, starts[i + 1])


def _excerpt(s, start):
    m, p = s, start
    lead = len(m) - len(m.lstrip())
    m = m.strip()
    p = max(0, min(p - lead, len(m)))
    if len(m) <= EXCERPT_MAX:
        return m
    st = max(0, min(p - 20, len(m) - EXCERPT_MAX))
    return m[st:st + EXCERPT_MAX]


_ALPHA_RUN = re.compile(r"[^\W\d_]+")


def _present(needles, low):
    """The needles occurring in low. Alphabetic needles (name anchors) lie inside one alphabetic run, so with many of
    them it is cheaper to test the distinct runs than to search the whole text once per needle."""
    needles = [nd for nd in needles if nd]
    if len(needles) > 8 and all(nd.isalpha() for nd in needles):
        words = set(_ALPHA_RUN.findall(" ".join(set(low.split()))))  # distinct tokens first: far fewer to scan
        if len(words) <= 20000:
            return [nd for nd in needles if any(nd in w for w in words)]
    return [nd for nd in needles if nd in low]


def _scan(text, comp, path):
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    low = _lower(text)
    starts = list(accumulate([0] + [len(x) + 1 for x in lines[:-1]]))
    cand = {r: set() for r in RULE_ORDER}
    for rule, needles in comp.triggers().items():
        present = _present(needles, low) if rule == "librarian" else [nd for nd in needles if nd and nd in low]
        for nd in present:
            _lines_with(low, nd, starts, cand[rule])
    if comp.hashes:
        for a, _ in comp._hash_spans(low):
            cand["unsafe_domain"].add(bisect.bisect_right(starts, a) - 1)
    for m in SESSION_TRIGGER.finditer(low):
        cand["session_id"].add(bisect.bisect_right(starts, m.start()) - 1)
    var_lines = set()
    for nd in VARIANT_TRIGGERS:
        if nd in low:
            _lines_with(low, nd, starts, var_lines)
    for m in _VARIANT_RE.finditer(low):
        var_lines.add(bisect.bisect_right(starts, m.start()) - 1)
    todo = set(var_lines)
    for v in cand.values():
        todo |= v
    finders = {r: getattr(comp, "f_" + r) for r in RULE_ORDER}
    hits = []
    for i in sorted(todo):
        line = lines[i]
        vs = _variants(line) if i in var_lines else [line]
        active = RULE_ORDER if len(vs) > 1 else [r for r in RULE_ORDER if i in cand[r]]
        first = {}  # rule -> (variant index, start) of its first match
        for vi, v in enumerate(vs):
            vl = _lower(v)
            for r in active:
                if r not in first:
                    spans = finders[r](v, vl)
                    if spans:
                        first[r] = (vi, min(spans)[0])
        if not first:
            continue
        # a private value found in ANY variant may also sit, encoded, in the text an excerpt would show
        withhold = bool(MASKED & first.keys()) or (comp.ci and "email" in first)
        for r in RULE_ORDER:
            if r in first:
                vi, a = first[r]
                hits.append(Hit(path, i + 1, r, WITHHELD if withhold else _excerpt(vs[vi], a)))
    return hits


def scan_text(text, rules, path):
    return _scan(text, _Compiled(rules), path)


def _rel(p, root):
    p = Path(p).resolve()
    try:
        return p.relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return p.as_posix()


def _skipped(rel):
    return any(rel == s or rel.startswith(s.rstrip("/") + "/") for s in SKIP_DIRS)


class ScanError(Exception):
    """The scan cannot vouch for the tree (no paths, missing path, walk error, zero text files): fail closed."""


def _is_private(name):
    return name.upper().startswith(PRIVATE_PREFIX)


def _walk_error(e):
    raise ScanError(f"cannot walk directory: {e}")


def _walk(paths, root, ci=False, tool_dirs=()):
    """Yield ("file", path, rel) and ("dir", path, rel) for a local tool directory that must be reported.

    Prunes SKIP_NAMES anywhere; unless --ci, also prunes the tool_dirs directly under --root, skips SKIP_DIRS and
    skips the PRIVATE_* files directly in --root when --root itself is scanned.
    """
    tool_dirs = set(tool_dirs)
    root = Path(root) if root else Path.cwd()
    rroot = root.resolve()
    seen = set()
    for base in paths:
        base = Path(base)
        if not base.exists():
            raise ScanError(f"path does not exist: {base}")
        if base.is_file():
            cands = [base]
        else:
            cands = []
            exempt_root = not ci and base.resolve() == rroot
            for dirpath, dirnames, filenames in os.walk(base, onerror=_walk_error):
                d = Path(dirpath)
                at_root = d.resolve() == rroot
                keep = []
                for x in sorted(dirnames):
                    if x in SKIP_NAMES or (not ci and _skipped(_rel(d / x, root))):
                        continue
                    if x in tool_dirs:
                        if not (at_root and not ci):
                            yield "dir", d / x, _rel(d / x, root)
                        continue
                    keep.append(x)
                dirnames[:] = keep
                for fn in sorted(filenames):
                    if exempt_root and at_root and _is_private(fn):
                        continue
                    cands.append(d / fn)
        for f in cands:
            key = f.resolve()
            rel = _rel(f, root)
            if key in seen or (not ci and _skipped(rel)):
                continue
            seen.add(key)
            yield "file", f, rel


def iter_files(paths, root=None, ci=False, tool_dirs=()):
    """Yield (file, rel) for every candidate file (see _walk)."""
    for kind, f, rel in _walk(paths, root, ci, tool_dirs):
        if kind == "file":
            yield f, rel


def _scan_paths(paths, comp, root):
    if not paths:
        raise ScanError("no paths given")
    hits, n = [], 0
    for kind, f, rel in _walk(paths, root, comp.ci, comp.tool_dirs):
        if kind == "dir":
            hits.append(Hit(rel, 1, "secret", "local tool directory inside scanned tree (not scanned)"))
            continue
        if _is_private(f.name):
            hits.append(Hit(rel, 1, "secret", "PRIVATE_* file inside scanned tree (contents not shown)"))
            continue
        text, err = _read_text(f)
        if err:
            hits.append(Hit(rel, 1, "unreadable", err))
            continue
        if text is None:
            continue
        n += 1
        hits.extend(_scan(text, comp, rel))
    if n == 0 and not hits:
        raise ScanError("zero text files scanned")
    return hits, n


def scan_tree(dirs, rules, root=None):
    """Hits for the given files/directories; raises ScanError when the scan cannot vouch for them."""
    return _scan_paths(list(dirs), _Compiled(rules), root)[0]


# ------------------------------------------------------------------ rules loading

def _read_lines(p):
    text, err = _read_text(p)
    if text is None:
        return []
    return text.splitlines()


def _author_emails(root):
    p = root / "data" / "author.json"
    if not p.exists():
        return set()
    d = json.loads(p.read_text(encoding="utf-8"))
    return {e.strip().lower() for e in d.get("emails", []) if e.strip()}


def _name_from(part):
    if _ORG_RE.search(part):
        return None
    toks = _NAME_TOKEN.findall(part)
    runs, run = [], []
    for t in toks:
        if t[0].isupper() or (run and t.lower() in _PARTICLES):
            run.append(t)
        else:
            runs.append(run)
            run = []
    runs.append(run)
    best = []
    for r in runs:
        while r and not r[-1][0].isupper():
            r = r[:-1]
        if sum(1 for x in r if x[0].isupper()) >= 2 and len(r) > len(best):
            best = r
    if not best and len(toks) >= 2 and not any(t[0].isupper() for t in toks):
        best = toks
    return " ".join(best) if len(best) >= 2 else None


def parse_librarian_cell(cell):
    """Person names in a guide_sweep librarian cell (placeholders, roles and organisations dropped)."""
    out = []
    for piece in _CELL_SPLIT.split(cell or ""):
        if piece.strip().lower() in ("na", "n/a"):
            continue
        piece = EMAIL_RE.sub(" ", piece)
        piece = _PLACEHOLDER_RE.sub(" ", piece)
        piece = _ROLE_RE.sub(" ", piece)
        parts = piece.split(",")
        if (len(parts) == 2 and len(_NAME_TOKEN.findall(parts[0])) == 1
                and 1 <= len(_NAME_TOKEN.findall(parts[1])) <= 3 and not _ORG_RE.search(piece)):
            parts = [parts[1].strip() + " " + parts[0].strip()]
        for part in parts:
            n = _name_from(part)
            if n and n not in out:
                out.append(n)
    return out


def _identifiers_message(problem):
    return (IDENT_FILE + " " + problem + ". Create it in the project root (git-ignored, never commit it), one value "
            "per line; blank lines and lines starting with '#' are ignored: lines containing '@' are personal email "
            "addresses; the first other line is your OS username (matched only as a path segment or before '@'); "
            "any further lines are device names or other identifiers (matched as whole words). In CI, where the "
            "file cannot exist, run with --ci.\n")


def _parse_identifiers(path):
    """Return (username, identifiers, personal_emails, problem); problem is None when the file is usable."""
    text, err = _read_text(path)
    if text is None:
        return "", [], set(), "cannot be decoded as text"
    username, identifiers, personal = "", [], set()
    for line in text.splitlines():
        v = line.strip()
        if not v or v.startswith("#"):
            continue
        if AT in v:
            personal.add(v.lower())
        elif not username:
            username = v
        else:
            identifiers.append(v)
    if not username:
        return "", identifiers, personal, "has no username line"
    return username, identifiers, personal, None


def load_rules(root, ci=False):
    root = Path(root)
    priv = root / IDENT_FILE
    username, identifiers, personal = "", [], set()
    problem = "is missing" if not priv.is_file() else None
    if not problem:
        username, identifiers, personal, problem = _parse_identifiers(priv)
    if problem:
        if not ci:
            sys.stderr.write(_identifiers_message(problem))
            raise SystemExit(2)
        sys.stderr.write("warning: " + IDENT_FILE + " " + problem + " (--ci): identifier and personal_email checks "
                         "are skipped; all other rules apply.\n")
        username, identifiers, personal = "", [], set()
    private = set()
    for p in sorted(root.iterdir()):
        if not p.is_file() or not _is_private(p.name) or p.name.lower() == IDENT_FILE.lower():
            continue
        for line in _read_lines(p):
            v = line.strip()
            if len(v) >= 6 and any(c.isalnum() for c in v):
                private.add(v)
    allowed = _author_emails(root)
    ac = root / "data" / "derived" / "allowed_contacts.txt"
    if ac.exists():
        for line in ac.read_text(encoding="utf-8").splitlines():
            v = line.strip()
            if v and not v.startswith("#"):
                allowed.add(v.lower())
    unsafe = set()
    up = root / "data" / "unsafe_domains.txt"
    if up.exists():
        for line in up.read_text(encoding="utf-8").splitlines():
            d = line.split("#", 1)[0].strip().lower()
            if d:
                unsafe.add(d)
    hashes = set()
    hp = root / "data" / "unsafe_domains.sha256"
    if not up.exists():
        if hp.exists():
            for line in hp.read_text(encoding="utf-8").splitlines():
                h = line.split("#", 1)[0].strip().lower()
                if re.fullmatch(r"[0-9a-f]{64}", h):
                    hashes.add(h)
        if not hashes:
            if not ci:
                sys.stderr.write("privacy check: neither data/unsafe_domains.txt nor a non-empty "
                                 "data/unsafe_domains.sha256 exists under --root (fail closed). In CI, run with "
                                 "--ci.\n")
                raise SystemExit(2)
            sys.stderr.write("warning: no unsafe-domain blocklist (--ci): the unsafe_domain rule is skipped.\n")
    allowed_names = set()
    an = root / "data" / "derived" / "allowed_names.txt"
    if an.exists():
        for line in an.read_text(encoding="utf-8").splitlines():
            v = line.strip()
            if v and not v.startswith("#"):
                allowed_names.add(v)
    norm_allowed = {_norm_name(n) for n in allowed_names}
    names = set()
    gs = root / "data" / "guide_sweep.csv"
    if gs.exists():
        with gs.open(encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                for n in parse_librarian_cell(row.get("librarian") or ""):
                    if _norm_name(n) not in norm_allowed:
                        names.add(n)
    return Rules(allowed, identifiers, unsafe, names, personal, username, private, allowed_names, bool(ci),
                 unsafe_hashes=hashes, tool_dirs=load_tool_dirs(root))


def load_tool_dirs(root):
    """Directory names from local/tool_dirs.txt under root (blank and "#" lines ignored); () when absent."""
    p = Path(root) / TOOL_DIRS_FILE
    if not p.is_file():
        return ()
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        v = line.strip().rstrip("/")
        if v and not v.startswith("#") and v not in out:
            out.append(v)
    return tuple(out)


def learn_contacts(root):
    """Distinct addresses in every text column of the catalog and the annexes, minus the author's and personal ones."""
    root = Path(root)
    author = _author_emails(root)
    priv = root / IDENT_FILE
    if priv.is_file():
        author |= _parse_identifiers(priv)[2]
    found = set()
    for name in ("catalog.csv", "literature_databases.csv", "primary_archives.csv"):
        p = root / "data" / name
        if not p.exists():
            continue
        with p.open(encoding="utf-8", newline="") as fh:
            for row in csv.reader(fh):
                for cell in row:
                    if not cell:
                        continue
                    for v in _variants(cell):
                        for _, e in _emails_in(v):
                            found.add(e)
    return sorted(found - author)


def _write_contacts(root, emails):
    out = root / "data" / "derived" / "allowed_contacts.txt"
    rejected = set()
    if out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if s.startswith("#"):
                v = s.lstrip("#").strip().lower()
                if EMAIL_RE.fullmatch(v):
                    rejected.add(v)
    body = "".join(("#" + e if e in rejected else e) + "\n" for e in sorted(set(emails) | rejected))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(CONTACTS_HEADER + body, encoding="utf-8", newline="\n")
    return out, len(rejected)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Scan files for private information.")
    ap.add_argument("paths", nargs="*")
    ap.add_argument("--learn-contacts", action="store_true")
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--ci", action="store_true",
                    help="PRIVATE_identifiers.txt optional (warning instead of exit 2); no exemptions, SKIP_DIRS scanned")
    a = ap.parse_args(argv)
    root = Path(a.root)
    if a.learn_contacts:
        emails = learn_contacts(root)
        out, nrej = _write_contacts(root, emails)
        print(f"wrote {len(emails)} addresses ({nrej} rejected kept) to {out}")
        return 0
    if not a.paths:
        print("privacy check: no paths given (fail closed)", file=sys.stderr)
        return 2
    paths = [Path(p) if Path(p).is_absolute() else Path.cwd() / p for p in a.paths]
    missing = [str(p) for p in paths if not p.exists()]
    if missing:
        print("privacy check: path(s) do not exist (fail closed): " + ", ".join(missing), file=sys.stderr)
        return 2
    rules = load_rules(root, ci=a.ci)
    try:
        hits, n = _scan_paths(paths, _Compiled(rules), root)
    except ScanError as e:
        print(f"privacy check: {e} (fail closed)", file=sys.stderr)
        return 2
    if hits:
        for h in hits:
            print(f"{h.path}:{h.line_no}: {h.rule}: {h.excerpt}")
        print(f"privacy check FAILED: {len(hits)} hits in {n} files", file=sys.stderr)
        return 1
    print(f"privacy check passed: {n} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
