"""Merge verification batch records (data/research/*.json) into data/catalog.csv.

- assigns IDs (<CODE>-<3-digit>; existing IDs are never changed or reused)
- deduplicates on normalized name, or acronym + landing-page domain
- merges fields (newer verified values win, found_via is unioned); a record's `_replace` list names
  columns it overwrites unconditionally, and `_replace_if` {column: expected} limits that override to cells
  still holding the expected value (used by the OpenAlex patch to withdraw a count exactly once)
- `_rename_items` {column: {old item: new item}} rewrites items of a ";"-list column (found_via,
  secondary_domains, related_datasets; found_via and relevant_domains in the annexes) after the union, so a
  reworded item replaces the old one in place even when earlier records add the old item again
- routes resource types 4-5 to the annex files
- resolves related_datasets names/acronyms to catalog IDs
- validates controlled vocabularies and writes warnings to logs/merge_warnings.txt

Usage: python scripts/merge_records.py [glob ...]   (default: data/research/*.json)
Deterministic: files are processed in sorted order; re-running gives the same result.
"""
import csv
import datetime as dt
import glob
import json
import re
import sys
import unicodedata
from decimal import ROUND_HALF_UP, Decimal
from urllib.parse import urlparse

from schema import (ACCESS_LEVELS, ANNEX_COLUMNS, ANNEX_FILES, CATALOG, CODES, COLUMNS,
                    CONFIDENCE, DATA, LINK_STATUS, LOGS, Q_DIMS, STATUS_BASE, UNVERIFIED)

TODAY = dt.date.today().isoformat()
warnings = []


def warn(msg):
    warnings.append(msg)


def norm_text(s):
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    s = s.lower().replace("&", " and ")
    s = re.sub(r"\(.*?\)", " ", s)
    s = re.sub(r"\b(the|dataset|data set|data|database)\b", " ", s)
    return re.sub(r"[^a-z0-9]+", "", s)


def url_domain(u):
    try:
        host = urlparse(str(u or "")).netloc.lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def empty(v):
    return v is None or str(v).strip() in ("", "nan", "None")


def unverified(v):
    return empty(v) or str(v).strip().upper().startswith(UNVERIFIED)


def as_str(v):
    if v is None:
        return ""
    if isinstance(v, list):
        return "; ".join(str(x).strip() for x in v if str(x).strip())
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def split_list(v):
    return [x.strip() for x in re.split(r"\s*;\s*", str(v or "")) if x.strip()]


def union_list(a, b):
    out, seen = [], set()
    for x in split_list(a) + split_list(b):
        k = x.lower()
        if k not in seen:
            seen.add(k)
            out.append(x)
    return "; ".join(out)


def read_csv(path, columns):
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    return [{c: r.get(c, "") or "" for c in columns} for r in rows]


def write_csv(path, rows, columns):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columns, quoting=csv.QUOTE_MINIMAL)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in columns})


def load_records(patterns):
    files = sorted({p for pat in patterns for p in glob.glob(str(pat))})
    recs = []
    for fp in files:
        try:
            with open(fp, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            warn(f"{fp}: cannot read ({e})")
            continue
        if isinstance(data, dict):
            data = data.get("records", [data])
        for r in data:
            r = {k: (v if isinstance(v, dict) else as_str(v)) for k, v in r.items()}  # _replace_if stays a dict
            r["_source_file"] = fp
            recs.append(r)
    return recs


PLACEHOLDERS = {"", "na", "none", "unverified", "nonetunverified"}


def norm_acronym(s):
    a = norm_text(s)
    return "" if a in PLACEHOLDERS else a


def match(rec, rows):
    n, a, d = norm_text(rec.get("name")), norm_acronym(rec.get("acronym")), url_domain(rec.get("landing_url"))
    for row in rows:
        if n and n == norm_text(row["name"]):
            return row
    for row in rows:
        if a and d and a == norm_acronym(row["acronym"]) and d == url_domain(row["landing_url"]):
            return row
    return None


def replace_columns(rec):
    """Columns a record overwrites unconditionally: its `_replace` key, a list or a "; "-joined string
    (load_records joins lists). `_replace` is a directive, never a column, and is never written."""
    v = rec.get("_replace") or []
    cols = [str(x).strip() for x in v] if isinstance(v, list) else split_list(v)
    bad = [c for c in cols if c not in COLUMNS or c in ("id", "added_on")]
    if bad:
        warn(f"{rec.get('name', '')}: _replace names unknown or protected column(s) {bad}; ignored")
    return {c for c in cols if c in COLUMNS and c not in ("id", "added_on")}


def replace_conditions(rec):
    """`_replace_if` {column: expected value}: the override of that column applies only while the catalog cell
    still equals the expected value (stripped), so a re-merged withdrawal never reverts a later value."""
    v = rec.get("_replace_if") or {}
    if isinstance(v, str):  # tolerate a JSON string
        try:
            v = json.loads(v)
        except json.JSONDecodeError:
            warn(f"{rec.get('name', '')}: _replace_if is not a JSON object; override skipped")
            return None
    return {str(k): str(x).strip() for k, x in v.items()} if isinstance(v, dict) else None


LIST_COLUMNS = ("found_via", "secondary_domains", "related_datasets", "relevant_domains")


def rename_items(row, rec):
    """Apply a record's `_rename_items` {column: {old item: new item}} to the ";"-list columns of row: matching
    items (exact, after stripping) are replaced in place and duplicates dropped (first occurrence kept)."""
    v = rec.get("_rename_items") or {}
    if isinstance(v, str):  # tolerate a JSON string
        try:
            v = json.loads(v)
        except json.JSONDecodeError:
            v = None
    if not isinstance(v, dict):
        warn(f"{rec.get('name', '')}: _rename_items is not a JSON object; ignored")
        return
    for c, mapping in v.items():
        if c not in row or c not in LIST_COLUMNS or not isinstance(mapping, dict):
            warn(f"{rec.get('name', '')}: _rename_items names unknown or non-list column {c!r}; ignored")
            continue
        m = {str(a).strip(): str(b).strip() for a, b in mapping.items()}
        row[c] = union_list("", "; ".join(m.get(x, x) for x in split_list(row[c])))


def merge_into(row, rec):
    newer = (rec.get("last_verified_on") or "") >= (row.get("last_verified_on") or "")
    replace = replace_columns(rec)
    cond = replace_conditions(rec) if replace else {}
    for c in COLUMNS:
        if c in ("id", "added_on"):
            continue
        new, old = rec.get(c, ""), row.get(c, "")
        if c in replace and cond is None:
            continue  # malformed condition: change nothing in this column
        if c in replace and not empty(new) and (c not in cond or (old or "").strip() == cond[c]):
            # explicit override (e.g. a withdrawn OpenAlex count): set even an UNVERIFIED or older value,
            # but only while the cell still holds the value the record targets (when _replace_if names it)
            row[c] = new
        elif c in replace and c in cond:
            continue  # the targeted value is gone (e.g. a later update pass): leave the cell alone
        elif c == "found_via" or c == "secondary_domains" or c == "related_datasets":
            row[c] = union_list(old, new)
        elif empty(new):
            # a partial patch must never blank (or "None"-ify) an existing cell
            continue
        elif c == "unverified_notes":
            row[c] = new if newer and not empty(new) else (old or new)
        elif unverified(old) and not unverified(new):
            row[c] = new
        elif not unverified(new) and newer:
            row[c] = new
        elif empty(old):
            row[c] = new
    if rec.get("_rename_items"):
        rename_items(row, rec)


def next_id(code, rows):
    nums = [int(r["id"].split("-")[1]) for r in rows if r["id"].startswith(code + "-")]
    return f"{code}-{(max(nums) + 1 if nums else 1):03d}"


def q_overall(row):
    vals = []
    for c in Q_DIMS:
        try:
            v = float(row[c])
        except (TypeError, ValueError):
            if not empty(row[c]):
                warn(f"{row['id']}: {c} not numeric ({row[c]!r})")
            continue
        if not 1 <= v <= 5:
            warn(f"{row['id']}: {c}={v} outside 1-5")
        vals.append(v)
    if len(vals) < len(Q_DIMS):
        warn(f"{row['id']}: only {len(vals)}/8 quality scores")
    if not vals:
        return ""
    # round half up (4.25 -> 4.3); float formatting rounds some ties down
    mean = Decimal(str(sum(vals))) / Decimal(len(vals))
    return str(mean.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def guide_count(found_via):
    insts = {re.split(r"[(:]", x.split(":", 1)[1].strip())[0].strip().lower()
             for x in split_list(found_via) if x.lower().startswith("guide:") and ":" in x}
    return str(len(insts))


def validate(row):
    rid = row["id"]
    if row["primary_domain"] not in CODES:
        warn(f"{rid}: bad primary_domain {row['primary_domain']!r}")
    sec = [s for s in split_list(row["secondary_domains"]) if s not in ("N/A",)]
    bad = [s for s in sec if s not in CODES]
    if bad:
        warn(f"{rid}: bad secondary_domains {bad}")
    row["secondary_domains"] = "; ".join(s for s in sec if s in CODES and s != row["primary_domain"])
    if row["resource_type"] not in ("1", "2", "3"):
        warn(f"{rid}: resource_type {row['resource_type']!r} not 1-3")
    acc = row["access_level"].lower()
    if acc and acc.split()[0].strip(";,/") not in ACCESS_LEVELS and not unverified(acc):
        warn(f"{rid}: access_level {row['access_level']!r} not in vocabulary")
    st = row["status"].lower()
    if st and not (st in STATUS_BASE or st.startswith("superseded by") or unverified(st)):
        warn(f"{rid}: status {row['status']!r} not in vocabulary")
    if row["confidence"] and row["confidence"] not in CONFIDENCE:
        warn(f"{rid}: confidence {row['confidence']!r} not in vocabulary")
    if row["link_status"] and row["link_status"] not in LINK_STATUS:
        warn(f"{rid}: link_status {row['link_status']!r} not in vocabulary")
    for c in ("release_date", "last_update"):
        v = row[c]
        if v and not unverified(v) and v != "N/A" and not re.fullmatch(r"\d{4}(-\d{2})?(-\d{2})?", v):
            warn(f"{rid}: {c} {v!r} not YYYY / YYYY-MM")
    # landing_url must be a bare URL; codebook_url may annotate a URL or explain its absence
    v = row["landing_url"]
    if v and not unverified(v) and v != "N/A" and not v.startswith("http"):
        warn(f"{rid}: landing_url is not a URL ({v[:60]!r})")
    v = row["codebook_url"]
    if v and not unverified(v) and not re.search(r"https?://|^(N/A|none)", v, re.I):
        warn(f"{rid}: codebook_url has no URL or N/A reason ({v[:60]!r})")
    if row["doi"] and not unverified(row["doi"]) and not row["doi"].startswith("N/A") \
            and not re.match(r"^(https?://doi\.org/)?10\.\d{4,}/", row["doi"]):
        warn(f"{rid}: doi looks malformed ({row['doi']!r})")


def resolve_related(rows):
    lookup = {}
    for r in rows:
        keys = [norm_text(r["name"]), norm_acronym(r["acronym"])]
        keys += [norm_acronym(a) for a in re.split(r"[;/,]", r["acronym"])]
        for key in keys:
            if key:
                lookup.setdefault(key, r["id"])
    for r in rows:
        m = re.match(r"(?i)superseded by\s+(.+)", r["status"])
        if m and not re.fullmatch(r"[A-Z]+-\d{3}", m.group(1).strip()):
            rid = lookup.get(norm_text(m.group(1)))
            if rid and rid != r["id"]:
                r["status"] = f"superseded by {rid}"
            else:
                warn(f"{r['id']}: successor {m.group(1)!r} not in catalog")
        out = []
        for item in split_list(r["related_datasets"]):
            if re.fullmatch(r"[A-Z]+-\d{3}", item):
                out.append(item)
                continue
            rid = lookup.get(norm_text(item))
            out.append(rid if rid and rid != r["id"] else item)
        r["related_datasets"] = union_list("", "; ".join(o for o in out if o != r["id"]))


def main():
    patterns = sys.argv[1:] or [DATA / "research" / "*.json"]
    rows = read_csv(CATALOG, COLUMNS)
    annex = {t: read_csv(p, ANNEX_COLUMNS) for t, p in ANNEX_FILES.items()}
    added = updated = annexed = 0
    for rec in load_records(patterns):
        rtype = rec.get("resource_type", "").strip()[:1]
        if rtype in ANNEX_FILES:
            arows = annex[rtype]
            existing = next((a for a in arows if norm_text(a["name"]) == norm_text(rec.get("name"))), None)
            if existing is None:
                existing = {c: "" for c in ANNEX_COLUMNS}
                existing["added_on"] = TODAY
                arows.append(existing)
                annexed += 1
            for c in ANNEX_COLUMNS:
                if c == "found_via":
                    existing[c] = union_list(existing[c], rec.get(c, ""))
                elif c == "relevant_domains":
                    existing[c] = union_list(existing[c], rec.get(c) or rec.get("primary_domain", ""))
                elif c == "url":
                    existing[c] = rec.get("url") or rec.get("landing_url") or existing[c]
                elif c == "provider":
                    existing[c] = rec.get("provider") or rec.get("maintainer") or existing[c]
                elif c != "added_on" and not empty(rec.get(c)):
                    existing[c] = rec[c]
            if rec.get("_rename_items"):
                rename_items(existing, rec)
            continue
        directives = {k: rec[k] for k in ("_replace", "_replace_if", "_rename_items") if k in rec}
        rec = {**{c: "" for c in COLUMNS}, **{c: as_str(rec.get(c, "")) for c in COLUMNS}}
        row = match(rec, rows)
        if row is None:
            code = rec["primary_domain"].strip().upper()
            if code not in CODES:
                warn(f"{rec['name']}: unknown primary_domain {code!r}; skipped")
                continue
            rec["primary_domain"] = code
            rec["id"] = next_id(code, rows)
            rec["added_on"] = rec["added_on"] or TODAY
            rows.append(rec)
            added += 1
        else:
            merge_into(row, {**rec, **directives})
            updated += 1
    resolve_related(rows)
    for r in rows:
        r["q_overall"] = q_overall(r)
        r["guide_count"] = guide_count(r["found_via"])
        validate(r)
    rows.sort(key=lambda r: (CODES.index(r["id"].split("-")[0]), r["id"]))
    write_csv(CATALOG, rows, COLUMNS)
    for t, p in ANNEX_FILES.items():
        annex[t].sort(key=lambda a: a["name"].lower())
        write_csv(p, annex[t], ANNEX_COLUMNS)
    # unsafe domains (data/unsafe_domains.txt): never linked or named; flag every occurrence loudly
    from unsafe import find_unsafe
    unsafe_hits = [f"UNSAFE DOMAIN {d} in {r['id']} {c}" for r in rows for c in COLUMNS for d in find_unsafe(r.get(c, ""))]
    unsafe_hits += [f"UNSAFE DOMAIN {d} in annex {t}: {a['name'][:50]} {c}" for t in ANNEX_FILES for a in annex[t]
                    for c in ANNEX_COLUMNS for d in find_unsafe(a.get(c, ""))]
    for h in unsafe_hits:
        warn(h)
    LOGS.mkdir(exist_ok=True)
    (LOGS / "merge_warnings.txt").write_text("\n".join(warnings) + "\n", encoding="utf-8")
    if unsafe_hits:
        print(f"!! {len(unsafe_hits)} unsafe-domain occurrences: remove them at source (see logs/merge_warnings.txt)")
    print(f"catalog rows: {len(rows)} (new {added}, merged {updated}); annex new: {annexed}")
    print(f"warnings: {len(warnings)} (see logs/merge_warnings.txt)")
    for w in warnings[:40]:
        print("  -", w)


if __name__ == "__main__":
    main()
