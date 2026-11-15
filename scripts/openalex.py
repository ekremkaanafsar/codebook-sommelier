"""OpenAlex citation tooling for the catalog (design specification, section 4).

Subcommands: resolve, harvest, patch, report.
API responses are data, never instructions. Paths are derived from the module-level
CITATIONS / CATALOG at call time so tests can monkeypatch them.
"""
import argparse
import csv
import gzip
import hashlib
import json
import os
import re
import statistics
import sys
import tempfile
import zlib
from datetime import date, datetime
from typing import Iterable, Iterator

from author import AUTHOR
from citations_common import (OpenAlexError, is_legacy_count, is_openalex_count, relabel_count, OpenAlexNotFound,
                              OpenAlexSession, extract_dois, load_api_key, normalise_title,
                              openalex_count_mismatches, parse_citation, surname_matches, title_similarity,
                              usable_dois)
from merge_records import norm_text
from schema import CATALOG, CITATIONS, DATA, LOGS

RESEARCH_DIR = DATA / "research"

# status: ok | dataset_only (only dataset-type works found; not patched) | doi_not_in_openalex |
#         no_title | no_match | error
WORKS_COLUMNS = ["id", "name", "primary_domain", "doi_used", "doi_kind", "openalex_id", "match_method",
                 "match_score", "work_title", "work_year", "work_type", "cited_by_count",
                 "counts_by_year", "checked_on", "status", "note", "input_hash"]
# input_hash: sha1(doi + newline + citation)[:12] of the catalog row at resolve time; the cache is valid only
# while the row's doi and citation are unchanged.
WORK_SELECT = "id,doi,display_name,publication_year,type,cited_by_count,counts_by_year,authorships"
CACHE_DAYS = 30
CITING_COLUMNS = ["catalog_id", "openalex_id", "citing_id", "citing_year", "citing_type", "field", "subfield"]
CITES_SELECT = "id,publication_year,type,primary_topic"
FLUSH_WORKS = 100      # refreshed works buffered before one rewrite of citing_works.csv.gz
FLUSH_ROWS = 50_000    # ... or citing rows buffered, whichever comes first (bounds memory)
SIM_WITH_SURNAME = 0.92
SIM_WITHOUT_SURNAME = 0.95
MIN_TITLE_WORDS_NO_AUTHOR = 5  # R13: short generic titles are not matched without an author
# R18: OpenAlex work types that are never a reference article (a deposit, a record of unknown kind, front matter).
NON_ARTICLE_TYPES = frozenset({"dataset", "other", "paratext"})


def input_hash(row: dict) -> str:
    """Short fingerprint of the inputs resolve depends on (doi and citation fields)."""
    text = (row.get("doi") or "") + "\n" + (row.get("citation") or "")
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def _blank(row: dict, today: str) -> dict:
    rec = {c: "" for c in WORKS_COLUMNS}
    rec.update({"id": row.get("id", ""), "name": row.get("name", ""),
                "primary_domain": row.get("primary_domain", ""), "doi_kind": "none",
                "match_method": "none", "checked_on": today})
    return rec


def accept_candidate(parsed: dict, cand: dict) -> tuple[bool, float, str]:
    """Apply the title-match rule. Returns (accepted, similarity, method)."""
    surname = parsed.get("surname")
    method = "title+year+author" if surname else "title+year"
    title, cand_title = parsed.get("title") or "", cand.get("display_name") or ""
    if not normalise_title(title) or not normalise_title(cand_title):
        return False, 0.0, method
    sim = title_similarity(title, cand_title)
    year, cyear = parsed.get("year"), cand.get("publication_year")
    if year is None or cyear is None or abs(int(cyear) - int(year)) > 1:
        return False, sim, method
    if surname:
        ok = sim >= SIM_WITH_SURNAME and surname_matches(surname, cand.get("authorships") or [])
    else:
        ok = (sim >= SIM_WITHOUT_SURNAME
              and len(normalise_title(title).split()) >= MIN_TITLE_WORDS_NO_AUTHOR)
    return ok, sim, method


def _fill_from_work(rec: dict, work: dict) -> None:
    wid = work.get("id") or ""
    rec["openalex_id"] = wid.rsplit("/", 1)[-1]
    rec["work_title"] = work.get("display_name") or ""
    year = work.get("publication_year")
    rec["work_year"] = "" if year is None else str(year)
    rec["work_type"] = work.get("type") or ""
    cited = work.get("cited_by_count")
    rec["cited_by_count"] = "" if cited is None else str(cited)
    rec["counts_by_year"] = json.dumps(
        [{"year": c.get("year"), "count": c.get("cited_by_count")} for c in work.get("counts_by_year") or []],
        separators=(",", ":"))
    rec["status"] = "ok"


def _err(rec: dict, exc: Exception) -> dict:
    rec["status"] = "error"
    rec["note"] = f"{type(exc).__name__}: {exc}"
    return rec


def _short(work: dict) -> str:
    wid = work.get("id") or ""
    return wid.rsplit("/", 1)[-1]


def _cb(work: dict) -> int:
    try:
        return int(work.get("cited_by_count"))
    except (TypeError, ValueError):
        return 0


def _is_article(kind: str, work: dict) -> bool:
    return kind == "article" and (work.get("type") or "") not in NON_ARTICLE_TYPES


def resolve_row(row: dict, api, today: str) -> dict:
    """Resolve a catalog row to one OpenAlex work.

    DOI path: look up every DOI in the field; the reference ARTICLE with the most citations wins
    (a dataset deposit is not an article). Status ok, dataset_only (only dataset-like works resolved),
    doi_not_in_openalex, or error. In fields starting with N/A or UNVERIFIED only DOIs labelled as reference
    works count (citations_common.usable_dois, R17b); with none, the row takes the title path.
    Title path: every candidate passing the title rule is ranked by (not a preprint, cited_by_count),
    ties in search order; a non-article winner gives dataset_only (R18).
    """
    rec = _blank(row, today)
    rec["input_hash"] = input_hash(row)
    dois = usable_dois(row.get("doi", ""))
    if dois:
        found: list[tuple[str, str, dict, bool]] = []  # (doi, kind, work, article_type), field order
        by_w: dict[str, int] = {}
        for doi, kind in dois:
            try:
                work = api.get(f"/works/https://doi.org/{doi}", {"select": WORK_SELECT})
            except OpenAlexNotFound:
                continue
            except OpenAlexError as exc:
                return _err(rec, exc)
            article = _is_article(kind, work)
            wid = _short(work)
            if wid and wid in by_w:  # same work reached through two DOIs counts once
                i = by_w[wid]
                if article and not found[i][3]:
                    found[i] = (doi, kind, work, article)
                continue
            by_w[wid] = len(found)
            found.append((doi, kind, work, article))
        if not found:
            rec["status"] = "doi_not_in_openalex"
            rec["note"] = "OpenAlex returned 404 for: " + "; ".join(d for d, _ in dois)
            return rec
        pool = [f for f in found if f[3]] or found
        best = max(range(len(pool)), key=lambda i: (_cb(pool[i][2]), -i))
        doi, kind, work, article = pool[best]
        _fill_from_work(rec, work)
        rec.update({"doi_used": doi, "doi_kind": kind, "match_method": "doi"})
        if not article:
            rec["status"] = "dataset_only"
        others = [f for f in found if f[2] is not work]
        if others:
            rec["note"] = "also resolved: " + "; ".join(
                f"{d} ({w.get('type') or 'unknown'}, {_cb(w)})" for d, _, w, _ in others)
        return rec

    parsed = parse_citation(row.get("citation", ""))
    if not parsed["title"]:
        rec["status"] = "no_title"
        rec["note"] = "no quoted title in citation and no usable DOI"
        return rec
    if parsed["year"] is None:
        rec["status"] = "no_match"
        rec["note"] = "no year in citation"
        return rec
    year = parsed["year"]
    try:
        data = api.get("/works", {"search": parsed["title"], "filter": f"publication_year:{year - 1}-{year + 1}",
                                  "per-page": 5, "select": WORK_SELECT})
    except OpenAlexError as exc:
        return _err(rec, exc)
    best = 0.0
    accepted: list[tuple[dict, float, str]] = []  # search order
    for cand in (data or {}).get("results") or []:
        ok, sim, method = accept_candidate(parsed, cand)
        best = max(best, sim)
        if ok:
            accepted.append((cand, sim, method))
    if accepted:
        # R20: article-type first, then non-preprint, then citations; ties in search order
        i = max(range(len(accepted)),
                key=lambda k: ((accepted[k][0].get("type") or "") not in NON_ARTICLE_TYPES,
                               (accepted[k][0].get("type") or "") != "preprint", _cb(accepted[k][0]), -k))
        cand, sim, method = accepted[i]
        _fill_from_work(rec, cand)
        rec.update({"match_method": method, "match_score": f"{sim:.3f}"})
        if (cand.get("type") or "") in NON_ARTICLE_TYPES:
            rec["status"] = "dataset_only"
        others = [c for k, (c, _, _) in enumerate(accepted) if k != i]
        if others:
            rec["note"] = "also matched: " + "; ".join(
                f"{_short(c)} ({c.get('type') or 'unknown'}, {_cb(c)})" for c in others)
        return rec
    rec["status"] = "no_match"
    if not parsed["surname"] and len(normalise_title(parsed["title"]).split()) < MIN_TITLE_WORDS_NO_AUTHOR:
        rec["note"] = "title too short for a match without an author"
    else:
        rec["note"] = f"no candidate passed the title rule (best similarity {best:.2f})"
    return rec


def _works_path():
    return CITATIONS / "openalex_works.csv"


def read_works() -> dict[str, dict]:
    path = _works_path()
    if not path.exists():
        return {}
    with open(path, encoding="utf-8", newline="") as fh:
        return {r["id"]: r for r in csv.DictReader(fh)}


def _atomic_path(path):
    """Create a temp file next to `path`; returns (fd, tmp_name). Caller must os.replace it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    return tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")


def write_works(records: dict[str, dict]) -> None:
    path = _works_path()
    fd, tmp = _atomic_path(path)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=WORKS_COLUMNS, extrasaction="ignore")
            w.writeheader()
            for key in sorted(records):
                w.writerow({c: records[key].get(c, "") for c in WORKS_COLUMNS})
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def read_catalog(path=None) -> list[dict]:
    with open(path or CATALOG, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def select_rows(rows, ids=None, domain=None) -> list[dict]:
    out = []
    for r in rows:
        if r.get("resource_type") not in ("1", "2", "3") or r.get("status") == "removed":
            continue
        if ids and r.get("id") not in ids:
            continue
        if domain and r.get("primary_domain") != domain:
            continue
        out.append(r)
    return out


def _recent(checked_on: str, today: str) -> bool:
    """True when 0 <= age <= CACHE_DAYS; unparseable or future-dated dates are not recent."""
    try:
        age = (datetime.strptime(today, "%Y-%m-%d") - datetime.strptime(checked_on or "", "%Y-%m-%d")).days
    except ValueError:
        return False
    return 0 <= age <= CACHE_DAYS


def _fresh(rec: dict, today: str, row: dict | None = None) -> bool:
    """Cached ok row within CACHE_DAYS; with `row`, also its doi and citation must be unchanged since resolve
    (a missing input_hash is never fresh)."""
    if row is not None and (not rec.get("input_hash") or rec.get("input_hash") != input_hash(row)):
        return False
    return rec.get("status") == "ok" and _recent(rec.get("checked_on", ""), today)


def run_resolve(api, ids=None, domain=None, force=False, today=None, catalog_path=None) -> dict[str, dict]:
    today = today or date.today().isoformat()
    rows = select_rows(read_catalog(catalog_path), ids, domain)
    works = read_works()
    todo = [r for r in rows if force or not _fresh(works.get(r["id"], {}), today, r)]
    print(f"resolve: {len(rows)} rows selected, {len(rows) - len(todo)} cached, {len(todo)} to query")
    try:
        for n, row in enumerate(todo, 1):
            works[row["id"]] = resolve_row(row, api, today)
            if n % 50 == 0:
                print(f"resolve: {n}/{len(todo)}")
                write_works(works)
    finally:
        write_works(works)
    counts: dict[str, int] = {}
    for row in todo:
        s = works[row["id"]]["status"]
        counts[s] = counts.get(s, 0) + 1
    print("resolve: done " + ", ".join(f"{k}={v}" for k, v in sorted(counts.items())))
    return works


# ---------------------------------------------------------------- harvest
def _state_path():
    return CITATIONS / "harvest_state.json"


def _citing_path():
    return CITATIONS / "citing_works.csv.gz"


def load_state() -> dict:
    path = _state_path()
    if not path.exists():
        return {}
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def save_state(state: dict) -> None:
    path = _state_path()
    fd, tmp = _atomic_path(path)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(state, fh, indent=1, sort_keys=True)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def append_citing(rows: list[dict]) -> None:
    path = _citing_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with gzip.open(path, "at", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CITING_COLUMNS, extrasaction="ignore")
        if new:
            w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in CITING_COLUMNS})


def _iter_citing(path) -> Iterator[dict]:
    """Yield complete rows; a truncated final gzip member (killed append) ends the stream quietly."""
    try:
        with gzip.open(path, "rt", encoding="utf-8", newline="") as fh:
            for r in csv.DictReader(fh):
                if None in r.values() or None in r:
                    break  # partial last line
                yield r
    except (EOFError, gzip.BadGzipFile, zlib.error):
        return


def read_citing() -> Iterator[dict]:
    path = _citing_path()
    if path.exists():
        yield from _iter_citing(path)


def drop_partial(openalex_ids: "str | Iterable[str]", append_rows: "list[dict] | None" = None) -> int:
    """Remove every citing row of the given reference work(s), then write `append_rows` (if any); one
    streamed rewrite into a temp file that atomically replaces the old one.

    Always rewrites the file when it exists, which also repairs a truncated final member.
    Returns the number of rows dropped.
    """
    drop = {openalex_ids} if isinstance(openalex_ids, str) else set(openalex_ids)
    path = _citing_path()
    if not path.exists():
        if append_rows:
            append_citing(append_rows)
        return 0
    dropped = 0
    fd, tmp = _atomic_path(path)
    os.close(fd)
    try:
        with gzip.open(tmp, "wt", encoding="utf-8", newline="") as dst:
            w = csv.DictWriter(dst, fieldnames=CITING_COLUMNS, extrasaction="ignore")
            w.writeheader()
            for r in _iter_citing(path):
                if r["openalex_id"] not in drop:
                    w.writerow(r)
                else:
                    dropped += 1
            for r in append_rows or ():
                w.writerow({c: r.get(c, "") for c in CITING_COLUMNS})
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return dropped


def harvest_work(openalex_id: str, api, max_citing: int = 0, on_first_page=None) -> tuple[list[dict], bool]:
    """Follow the cursor over all works citing `openalex_id`. Returns (rows, truncated).

    `on_first_page(data)` is called with the raw JSON of the first page (debug aid).
    """
    rows: list[dict] = []
    cursor = "*"
    while cursor:
        data = api.get("/works", {"filter": f"cites:{openalex_id}", "per-page": 200, "cursor": cursor,
                                  "select": CITES_SELECT})
        if cursor == "*" and on_first_page is not None:
            on_first_page(data)
        for item in (data or {}).get("results") or []:
            topic = item.get("primary_topic") or {}
            year = item.get("publication_year")
            rows.append({"openalex_id": openalex_id,
                         "citing_id": (item.get("id") or "").rsplit("/", 1)[-1],
                         "citing_year": "" if year is None else str(year),
                         "citing_type": item.get("type") or "",
                         "field": (topic.get("field") or {}).get("display_name") or "",
                         "subfield": (topic.get("subfield") or {}).get("display_name") or ""})
        cursor = ((data or {}).get("meta") or {}).get("next_cursor")
        if max_citing and len(rows) >= max_citing:
            return rows[:max_citing], bool(cursor) or len(rows) > max_citing
    return rows, False


def _dump_first_page(openalex_id: str, data) -> None:
    CITATIONS.mkdir(parents=True, exist_ok=True)
    path = CITATIONS / f"first_page_{openalex_id}.json"
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
    print(f"harvest: first page of {openalex_id} written to {path}")


def run_harvest(api, ids=None, domain=None, force=False, today=None, max_citing=0,
                dump_first_page=False) -> int:
    """Harvest citing works for each resolved reference work. Returns the number of failed works.

    Every catalog id mapping to a work gets rows; filters only choose which works are harvested.
    A refresh only touches stored rows after the new harvest has fully succeeded: refreshed works are held
    in memory and swapped in together, one gz rewrite per batch of FLUSH_WORKS works or FLUSH_ROWS rows
    (and once more at the end, also on interruption). A failed refresh keeps its old rows and state; before
    each rewrite the batch is marked not-done, so a kill during the rewrite is repaired on the next run.
    """
    today = today or date.today().isoformat()
    by_work: dict[str, list[str]] = {}
    selected: set[str] = set()
    works = read_works()  # one snapshot for by_work and the prune
    for rec in works.values():
        if rec.get("status") != "ok" or not rec.get("openalex_id"):
            continue
        w = rec["openalex_id"]
        by_work.setdefault(w, []).append(rec["id"])
        if (not ids or rec["id"] in ids) and (not domain or rec.get("primary_domain") == domain):
            selected.add(w)
    state = load_state()
    # Repair pass before any append: a kill mid-append may have left a truncated gzip member for
    # works whose state is not-done; appending after it would corrupt the file.
    interrupted = sorted(w for w, st in state.items() if not st.get("done"))
    if interrupted:
        print(f"harvest: repairing interrupted appends for {len(interrupted)} work(s)")
        drop_partial(interrupted)
        for w in interrupted:
            del state[w]
        save_state(state)
    # Prune: the chosen work of a row may have changed; citing rows of works that no ok row
    # references any more must not survive (global, regardless of --ids/--domain).
    referenced = {w for rec in works.values()
                  if rec.get("status") == "ok" and (w := rec.get("openalex_id"))}
    errored = sum(1 for rec in works.values() if rec.get("status") == "error")
    if not referenced:
        print("harvest: prune skipped, no ok works in openalex_works.csv (missing, empty or unresolved file)")
        stale = []
    elif errored:
        print(f"harvest: prune skipped, {errored} works row(s) have status error "
              "(their work ids are unknown until resolve is re-run)")
        stale = []
    else:
        stale = sorted(w for w in state if w not in referenced)
    if stale:
        rows_dropped = drop_partial(set(stale))
        for w in stale:
            del state[w]
        save_state(state)
        print(f"harvest: pruned {len(stale)} unreferenced work(s), {rows_dropped} citing row(s)")

    def cached(w):
        st = state.get(w) or {}
        return (bool(st.get("done")) and _recent(st.get("checked_on", ""), today)
                and st.get("ids") == sorted(by_work[w]))

    todo = [w for w in sorted(selected) if force or not cached(w)]
    print(f"harvest: {len(selected)} works selected, {len(selected) - len(todo)} cached, {len(todo)} to harvest")
    errors = 0
    dump_pending = dump_first_page  # only the first work harvested in this run
    # Refreshes (works with stored rows) are buffered and swapped in with one gz rewrite per batch instead of
    # one rewrite per work; new works are appended directly.
    pending: list[tuple[str, dict, list[dict]]] = []
    pending_rows = 0

    def flush():
        nonlocal pending_rows
        if not pending:
            return
        batch, n_rows = pending[:], pending_rows
        pending.clear()  # taken off first: an interrupted rewrite is not retried by the final flush
        pending_rows = 0
        for pw, pentry, _ in batch:
            state[pw] = {**pentry, "done": False}
        save_state(state)  # a kill during the rewrite leaves these not-done: the repair pass cleans them up
        drop_partial({pw for pw, _, _ in batch}, append_rows=[r for _, _, prows in batch for r in prows])
        for pw, pentry, _ in batch:
            state[pw] = {**pentry, "done": True}
        save_state(state)
        print(f"harvest: swapped in {len(batch)} refreshed work(s), {n_rows} citing row(s)")

    try:
        for n, w in enumerate(todo, 1):
            cids = sorted(by_work[w])
            on_page = None
            if dump_pending:
                dump_pending = False
                on_page = lambda data, w=w: _dump_first_page(w, data)
            try:
                rows, truncated = harvest_work(w, api, max_citing, on_first_page=on_page)
            except OpenAlexError as exc:
                errors += 1
                hint = (f" [hint: HTTP 401 usually means an API key is required; {KEY_HINT}]"
                        if str(exc).startswith("HTTP 401") and not _HAVE_KEY else "")
                print(f"harvest: {w} failed ({type(exc).__name__}: {exc}){hint}; stored data left untouched")
                continue
            entry = {"done": False, "n": len(rows), "checked_on": today, "truncated": truncated, "ids": cids}
            out = [{**r, "catalog_id": cid} for cid in cids for r in rows]
            if w in state:  # stored rows exist (the repair pass removed every not-done entry): refresh
                pending.append((w, entry, out))
                pending_rows += len(out)
                if len(pending) >= FLUSH_WORKS or pending_rows >= FLUSH_ROWS:
                    flush()
                print(f"harvest: {n}/{len(todo)} {w} (refresh)")
                continue
            state[w] = entry
            try:
                save_state(state)  # mark not-done first: a kill during the append is repaired next run
                append_citing(out)
                state[w] = {**entry, "done": True}
            finally:
                save_state(state)
            print(f"harvest: {n}/{len(todo)} {w}")
    finally:
        flush()  # completed refreshes are kept even when the run is interrupted
    print(f"harvest: done, {errors} errors")
    return errors


# ---------------------------------------------------------------- patch
def _check_unique_names(rows: list[dict]) -> None:
    seen: dict[str, list[str]] = {}
    for r in rows:
        seen.setdefault(norm_text(r.get("name", "")), []).append(r.get("id", ""))
    dup = [v for v in seen.values() if len(v) > 1]
    if dup:
        raise ValueError("duplicate normalised names (merge_records matches patch records by name): "
                         + "; ".join(", ".join(v) for v in dup))


def _record(row: dict, today: str, citation_count: str) -> dict:
    return {"name": row["name"], "primary_domain": row["primary_domain"],
            "resource_type": row["resource_type"], "last_verified_on": today,
            "citation_count": citation_count}


PATCH_FILE = "zz_patch_openalex.json"  # rolling: overwritten by every `patch` run (R19)
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def count_label(work: dict, today: str) -> str:
    """'N (OpenAlex, checked YYYY-MM)'; the month is the works row's check date (today's when it has none),
    so the label states when OpenAlex was actually asked, not when the patch was written."""
    checked = work.get("checked_on") or ""
    month = checked[:7] if _ISO_DATE_RE.match(checked) else today[:7]
    return f"{work['cited_by_count']} (OpenAlex, checked {month})"


def _ok_count(w) -> bool:
    return bool(w) and w.get("status") == "ok" and (w.get("cited_by_count") or "") != ""


def build_patch(rows: list[dict], works: dict[str, dict], today: str, stats: dict | None = None) -> list[dict]:
    """The complete set of OpenAlex-governed citation_count records, regenerated on every run (R19).

    A row is OpenAlex-governed when its cell is exactly one OpenAlex count (`is_openalex_count`) or is empty /
    UNVERIFIED. For each such row:
    - work ok: a record with the current count (a fill, a refresh, or the unchanged value);
    - cell is an OpenAlex count but the works row is missing or not ok (dataset_only, no_match, ...): a WITHDRAW
      record, citation_count "UNVERIFIED (OpenAlex match withdrawn <today>: <status>)" with
      `"_replace": ["citation_count"]` and `"_replace_if": {"citation_count": <the OpenAlex string withdrawn>}`,
      so merge_records overwrites the count only while the cell still holds that string (R20: re-merging the
      rolling file never reverts a value written later, e.g. a Google Scholar count from an update pass);
    - works status error: no record (the work is unknown, the cell stays as it is; `patch` refuses errors
      unless --allow-partial);
    - empty / UNVERIFIED cell without an ok work: no record.
    Google Scholar, N/A, legacy and hand-written multi-part cells are never touched.

    Unchanged counts stay in the file so that it always states the current OpenAlex value of every row it
    governs. Omitting them would be harmless (merge never reverts a cell to an older research file: no other
    file carries OpenAlex counts), but a complete file is self-describing. Unchanged records carry the row's
    current last_verified_on (merging them changes nothing); fills, refreshes and withdrawals carry today.
    A withdrawal appears only while the catalog still shows the OpenAlex count; once merged, the cell is
    UNVERIFIED and the next run either fills it again (work ok) or leaves it out. Until then `_replace_if`
    makes it a no-op on any cell that no longer holds the withdrawn value.
    Refresh records carry no `_replace`: an OpenAlex value replaces an older OpenAlex value through the normal
    newer-record rule.
    """
    _check_unique_names(rows)
    out = []
    counts = {"fill": 0, "refresh": 0, "unchanged": 0, "withdraw": 0}
    for r in rows:
        cc = (r.get("citation_count") or "").strip()
        w = works.get(r["id"])
        if is_openalex_count(cc):
            if _ok_count(w):
                label = count_label(w, today)
                if label == cc:  # unchanged: keep the row's date so a new-day patch does not re-verify it
                    counts["unchanged"] += 1
                    out.append(_record(r, (r.get("last_verified_on") or "").strip() or today, label))
                else:
                    counts["refresh"] += 1
                    out.append(_record(r, today, label))
            elif w and w.get("status") == "error":
                continue
            else:
                status = (w.get("status") or "no status") if w else "no works row"
                if w and w.get("status") == "ok":
                    status = "ok without cited_by_count"
                counts["withdraw"] += 1
                out.append({**_record(r, today, f"UNVERIFIED (OpenAlex match withdrawn {today}: {status})"),
                            "_replace": ["citation_count"], "_replace_if": {"citation_count": cc}})
        elif (not cc or cc.upper().startswith("UNVERIFIED")) and _ok_count(w):
            counts["fill"] += 1
            out.append(_record(r, today, count_label(w, today)))
    if stats is not None:
        stats.update(counts)
    return out


def build_relabel_patch(rows: list[dict], today: str) -> list[dict]:
    """Records relabelling legacy 'N (checked YYYY-MM)' values as Google Scholar counts."""
    _check_unique_names(rows)
    return [_record(r, today, relabel_count(r["citation_count"]))
            for r in rows if is_legacy_count(r.get("citation_count") or "")]


# Quality justifications cite "about N OpenAlex citations" as evidence for the Adoption score. The phrase is
# refreshed from the works record only when it can only mean that record's work; anything that may refer to
# another work (a companion paper, an edition, two articles, a dataset DOI) is reported and left alone.
PROSE_PATCH_FILE = "zz_patch_zz_openalex_prose.json"  # rolling; sorts after every other patch file
_PROSE_RE = re.compile(r"(?:(?:about|around|roughly|approximately|nearly|some)\s+)?(\d[\d,]*) OpenAlex citations")
_OTHER_WORK_RE = re.compile(r"\b(?:alone|companion|edition|overview|respectively|et al|dataset DOI|dataset record|"
                            r"articles|papers|method paper|research note|reports?)\b", re.I)
_CLAUSE_END = ("; ", ". ", ": ")
_DATED_RE = re.compile(r"checked \d{4}-\d{2}")
_PROSE_TOLERANCE = 0.10


def _clause(text: str, start: int, end: int) -> str:
    """The clause around text[start:end], bounded by '; ', '. ' or ': ' (or the ends of the text)."""
    lo = max((text.rfind(b, 0, start) + len(b) for b in _CLAUSE_END if text.rfind(b, 0, start) >= 0), default=0)
    hi = min((i for i in (text.find(b, end) for b in _CLAUSE_END) if i >= 0), default=len(text))
    return text[lo:hi]


def _dated(text: str, end: int) -> bool:
    """True when 'checked YYYY-MM' follows position `end` before the sentence ends."""
    stop = text.find(". ", end)
    return bool(_DATED_RE.search(text[end:stop if stop >= 0 else len(text)]))


def build_prose_patch(rows: list[dict], works: dict[str, dict]) -> tuple[list[dict], list[str]]:
    """Records refreshing a stale 'about N OpenAlex citations' in quality_justification, plus the ids left alone.

    A phrase is refreshed to the record's exact count when it is the cell's only such phrase, its clause names no
    other work and holds no other number, the record's count is at least the stated one (counts only grow, so a
    lower record means a different work) and the two differ by more than 10 %. Records carry `_replace_if`, so a
    cell edited since is never overwritten. Stale phrases that fail a condition are returned as skipped ids.
    A phrase followed by "checked YYYY-MM" in the same sentence is a dated snapshot (usually of another work) and
    is neither refreshed nor reported."""
    out, skipped = [], []
    for r in rows:
        qj = r.get("quality_justification") or ""
        w = works.get(r["id"])
        phrases = [m for m in _PROSE_RE.finditer(qj) if not _dated(qj, m.end())]
        if not phrases or not _ok_count(w):
            continue
        current = int(w["cited_by_count"])
        stale = [m for m in phrases if abs(int(m.group(1).replace(",", "")) - current) > _PROSE_TOLERANCE * current]
        if not stale:
            continue
        m = stale[0]
        stated = int(m.group(1).replace(",", ""))
        clause = _clause(qj, m.start(), m.end())
        rest = clause.replace(m.group(0), "", 1)
        if len(phrases) > 1 or current < stated or _OTHER_WORK_RE.search(clause) or re.search(r"\d", rest):
            skipped.append(r["id"])
            continue
        new = qj[:m.start()] + f"{current:,} OpenAlex citations" + qj[m.end():]
        out.append({"name": r["name"], "resource_type": r["resource_type"], "quality_justification": new,
                    "_replace": ["quality_justification"], "_replace_if": {"quality_justification": qj}})
    return out, skipped


def _write_patch(name: str, records: list[dict]):
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    path = RESEARCH_DIR / name
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(records, fh, ensure_ascii=False, indent=2)
    return path


def run_patch(ids=None, domain=None, allow_partial=False, today=None) -> int:
    today = today or date.today().isoformat()
    catalog = read_catalog()
    try:
        _check_unique_names(catalog)  # merge_records matches patch records against the whole catalog
    except ValueError as exc:
        print(f"patch: refusing, {exc}", file=sys.stderr)
        return 2
    rows = select_rows(catalog, ids, domain)
    works = read_works()
    bad = sorted(r["id"] for r in rows if works.get(r["id"], {}).get("status") == "error")
    if bad and not allow_partial:
        print(f"patch: refusing, {len(bad)} resolved row(s) in scope have status 'error' "
              f"({', '.join(bad[:10])}{'...' if len(bad) > 10 else ''}); re-run resolve or pass --allow-partial",
              file=sys.stderr)
        return 2
    # The OpenAlex file is rolling and must state every governed row, so it always covers the whole catalog;
    # --ids / --domain scope only the error check above and the relabel patch.
    if ids or domain:
        print("patch: the OpenAlex file always covers the whole catalog; --ids/--domain scope only the error "
              "check and the relabel patch")
    stats: dict = {}
    oa = build_patch(select_rows(catalog), works, today, stats)
    gs = build_relabel_patch(rows, today)
    p1 = _write_patch(PATCH_FILE, oa)
    print(f"patch: {len(oa)} OpenAlex count record(s) -> {p1} "
          f"(fill {stats['fill']}, refresh {stats['refresh']}, unchanged {stats['unchanged']}, "
          f"withdraw {stats['withdraw']})")
    prose, prose_skipped = build_prose_patch(select_rows(catalog), works)
    p3 = _write_patch(PROSE_PATCH_FILE, prose)
    print(f"patch: {len(prose)} quality_justification count refresh(es) -> {p3}")
    if prose_skipped:
        print(f"patch: {len(prose_skipped)} stale 'OpenAlex citations' phrase(s) may cite another work and were "
              f"left for review: {', '.join(prose_skipped)}")
    if gs:
        p2 = _write_patch(f"zz_patch_gs_relabel_{today}.json", gs)
        print(f"patch: {len(gs)} Google Scholar relabel record(s) -> {p2}")
    print("patch: next, review the files, then run: python scripts/merge_records.py")
    return 0


# ---------------------------------------------------------------- report
DISCREPANCY = 0.25
_N = r"(\d[\d,]*)"
_DOI_TOK = r"(10\.\d{4,9}/[^\s;,]+)"
_NOTE_PATTERNS = {
    "OpenAlex": [re.compile(r"OpenAlex cited_by_count(?:\s+(?:is|of))?\s+" + _N + r"(?:\s+for\s+" + _DOI_TOK + ")?", re.I)],
    "Crossref": [re.compile(r"Crossref is-referenced-by-count\s+for\s+" + _DOI_TOK + r"\s+is\s+" + _N, re.I),
                 re.compile(r"Crossref is-referenced-by-count(?:\s+(?:is|of))?\s+" + _N
                            + r"(?:\s+for\s+" + _DOI_TOK + ")?", re.I)],
}
_CONTINUATION = re.compile(r"\s*(?:,\s*)?(?:and|plus)\s+" + _N + r"\s+for\s+" + _DOI_TOK, re.I)


def _norm_doi(token):
    if not token:
        return None
    found = extract_dois(token)
    return found[0][0] if found else token.rstrip(".;,:)]'\"").lower()


def note_counts(note, doi=None) -> dict[str, int]:
    """Counts quoted in an unverified_notes string through explicit count phrases only.

    Occurrences are (source, N, DOI or None); with `doi` given, the first occurrence per source
    whose DOI is None or equals it (case-insensitive) is kept, otherwise the first per source.
    """
    text = note or ""
    want = _norm_doi(doi) if doi else None
    out: dict[str, int] = {}
    for source, patterns in _NOTE_PATTERNS.items():
        occ = []  # (position, n, doi)
        for pat in patterns:
            for m in pat.finditer(text):
                g = m.groups()
                n, d = (g[1], g[0]) if g[0] and g[0].startswith("10.") else (g[0], g[1])
                occ.append((m.start(), int(n.replace(",", "")), _norm_doi(d)))
                end = m.end()
                while True:
                    c = _CONTINUATION.match(text, end)
                    if not c:
                        break
                    occ.append((c.start(), int(c.group(1).replace(",", "")), _norm_doi(c.group(2))))
                    end = c.end()
        for _, n, d in sorted(occ):
            if want is None or d is None or d == want:
                out[source] = n
                break
    return out


def _count(w: dict):
    try:
        return int(w.get("cited_by_count", ""))
    except (TypeError, ValueError):
        return None


def _table(header: list[str], body: list[list]) -> list[str]:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(str(c).replace("|", "/") for c in r) + " |" for r in body]
    return lines


def build_report(rows: list[dict], works: dict[str, dict], state: dict, today: str) -> str:
    """Markdown summary of resolve/harvest results (spec 4.6). Pure: no file or network access."""
    L = [f"# OpenAlex citation report ({today})", ""]
    vals = list(works.values())

    L += ["## Status", ""]
    counts: dict[str, int] = {}
    for w in vals:
        counts[w.get("status") or "?"] = counts.get(w.get("status") or "?", 0) + 1
    L += _table(["status", "works"], [[k, v] for k, v in sorted(counts.items())]) + [f"", f"Total rows: {len(vals)}", ""]
    nums = sorted(c for c in (_count(w) for w in vals if w.get("status") == "ok") if c is not None)
    if nums:
        q = statistics.quantiles(nums, n=4, method="inclusive") if len(nums) > 1 else [nums[0]] * 3
        L += [f"cited_by_count (ok rows, n={len(nums)}): min {nums[0]}, Q1 {q[0]:g}, median {q[1]:g}, "
              f"Q3 {q[2]:g}, max {nums[-1]}", ""]
        bins = [("0", 0, 0), ("1-9", 1, 9), ("10-99", 10, 99), ("100-999", 100, 999), ("1000+", 1000, None)]
        L += _table(["cited_by_count", "works"],
                    [[b, sum(1 for n in nums if n >= lo and (hi is None or n <= hi))] for b, lo, hi in bins]) + [""]

    L += ["## Match methods", ""]
    methods: dict[str, int] = {}
    for w in vals:
        methods[w.get("match_method") or "none"] = methods.get(w.get("match_method") or "none", 0) + 1
    L += _table(["match_method", "works"], [[k, v] for k, v in sorted(methods.items())]) + [""]

    L += ["## Top 20", ""]
    ranked = sorted((w for w in vals if w.get("status") == "ok" and _count(w) is not None),
                    key=lambda w: (-_count(w), w.get("id", "")))[:20]
    L += _table(["id", "name", "cited_by_count", "work"],
                [[w["id"], w.get("name", ""), _count(w), w.get("openalex_id", "")] for w in ranked]) + [""]

    L += ["## Consistency", "", "OpenAlex count against counts quoted in the catalog's unverified_notes "
          "(difference relative to the note value).", ""]
    comp = []
    for r in rows:
        w = works.get(r.get("id"))
        if not w or w.get("status") != "ok" or _count(w) is None:
            continue
        for source, noted in note_counts(r.get("unverified_notes"), w.get("doi_used")).items():
            diff = abs(_count(w) - noted)
            pct = diff / noted if noted else (0.0 if diff == 0 else float("inf"))
            comp.append([r["id"], r.get("name", ""), source, noted, _count(w), diff, pct])
    fmt = lambda c: c[:6] + [f"{c[6] * 100:.0f}%" if c[6] != float("inf") else "n/a"]  # noqa: E731
    head = ["id", "name", "note source", "note value", "OpenAlex", "|diff|", "%"]
    if comp:
        L += _table(head, [fmt(c) for c in comp])
    else:
        L += ["No comparable note values."]
    flagged = [c for c in comp if c[6] > DISCREPANCY]
    L += ["", f"Differences above {DISCREPANCY:.0%}, for a human look ({len(flagged)}):", ""]
    L += _table(head, [fmt(c) for c in flagged]) if flagged else ["None."]
    L += [""]

    L += ["## Catalog vs works", "", "Catalog cells holding an OpenAlex count that the works table no longer "
          "supports (expected empty after patch and merge).", ""]
    names = {r.get("id"): r.get("name", "") for r in rows}
    mism = openalex_count_mismatches(rows, works)
    L += _table(["id", "name", "catalog", "problem"],
                [[i, names.get(i, ""), v, p] for i, v, p in mism]) if mism else ["None."]
    L += [""]

    L += ["## Harvest", ""]
    done = {k: v for k, v in state.items() if v.get("done")}
    pending = sorted(k for k, v in state.items() if not v.get("done"))
    trunc = sorted(k for k, v in done.items() if v.get("truncated"))
    L += [f"- works harvested (done): {len(done)}",
          f"- citing rows: {sum(int(v.get('n') or 0) for v in done.values())}",
          f"- truncated works ({len(trunc)}): {', '.join(trunc) or 'none'}",
          f"- not-done works ({len(pending)}): {', '.join(pending) or 'none'}", ""]

    L += ["## Errors", ""]
    errs = [w for w in vals if w.get("status") == "error"]
    L += _table(["id", "name", "note"], [[w["id"], w.get("name", ""), w.get("note", "")] for w in sorted(errs, key=lambda w: w["id"])]) if errs else ["None."]
    L += [""]

    L += ["## No identifiable work", "", "Rows an update pass could fix by adding a DOI.", ""]
    for status in ("doi_not_in_openalex", "no_title", "no_match"):
        sel = sorted((w for w in vals if w.get("status") == status), key=lambda w: w["id"])
        L += [f"### {status} ({len(sel)})", ""]
        L += _table(["id", "name"], [[w["id"], w.get("name", "")] for w in sel]) if sel else ["None."]
        L += [""]
    ds = sorted((w for w in vals if w.get("status") == "dataset_only"), key=lambda w: w["id"])
    L += [f"### dataset record only (not patched) ({len(ds)})", "",
          "Only a dataset record was found in OpenAlex; add the reference article's DOI.", ""]
    L += _table(["id", "name", "DOI used", "count"],
                [[w["id"], w.get("name", ""), w.get("doi_used", ""), w.get("cited_by_count", "")] for w in ds]) if ds else ["None."]
    L += [""]
    return "\n".join(L)


def run_report(today=None) -> str:
    today = today or date.today().isoformat()
    text = build_report(read_catalog(), read_works(), load_state(), today)
    LOGS.mkdir(parents=True, exist_ok=True)
    path = LOGS / f"citations_{today}.md"
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text if text.endswith("\n") else text + "\n")
    print(text)
    print(f"report: written to {path}")
    return text


KEY_HINT = "set OPENALEX_API_KEY or create PRIVATE_openalex_api_key.txt"
_HAVE_KEY = False  # set by _make_session; used only to decide whether to print the 401 hint


def _make_session(args):
    """Build the OpenAlex session; print which key source is used (never the key itself)."""
    global _HAVE_KEY
    key, source = (None, "") if args.no_api_key else load_api_key()
    _HAVE_KEY = bool(key)
    if args.no_api_key:
        print("OpenAlex API key disabled by --no-api-key; requests are keyless")
    elif key:
        print(f"using OpenAlex API key from {source}")
    else:
        print(f"no OpenAlex API key found ({KEY_HINT}); cursor paging past the first page will fail")
    return OpenAlexSession(mailto=AUTHOR["emails"][0] if args.mailto else None, api_key=key)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="OpenAlex citation tooling")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("resolve", help="resolve each catalog row to an OpenAlex work")
    p.add_argument("--ids", nargs="+", metavar="ID")
    p.add_argument("--domain", metavar="CODE")
    p.add_argument("--force", action="store_true", help="ignore the 30-day cache")
    p.add_argument("--mailto", action="store_true", help="send the author's first e-mail as mailto")
    p.add_argument("--no-api-key", action="store_true", help="ignore any OpenAlex API key (keyless requests)")
    h = sub.add_parser("harvest", help="harvest works citing each resolved reference work")
    h.add_argument("--ids", nargs="+", metavar="ID")
    h.add_argument("--domain", metavar="CODE")
    h.add_argument("--force", action="store_true", help="ignore the 30-day cache")
    h.add_argument("--mailto", action="store_true", help="send the author's first e-mail as mailto")
    h.add_argument("--no-api-key", action="store_true", help="ignore any OpenAlex API key (keyless requests)")
    h.add_argument("--max-citing", type=int, default=0, metavar="N", help="cap citing works per reference work")
    h.add_argument("--dump-first-page", action="store_true",
                   help="debug: write the raw first cites page of the first work harvested to first_page_<W>.json")
    t = sub.add_parser("patch", help="write merge patch files for citation_count (never runs the merge)")
    t.add_argument("--ids", nargs="+", metavar="ID")
    t.add_argument("--domain", metavar="CODE")
    t.add_argument("--allow-partial", action="store_true", help="patch even if some works failed to resolve")
    sub.add_parser("report", help="write logs/citations_<date>.md and print it")
    args = parser.parse_args(argv)
    if args.command == "report":
        run_report()
        return 0
    if args.command == "resolve":
        api = _make_session(args)
        run_resolve(api, ids=args.ids, domain=args.domain, force=args.force)
        return 0
    if args.command == "harvest":
        api = _make_session(args)
        errors = run_harvest(api, ids=args.ids, domain=args.domain, force=args.force, max_citing=args.max_citing,
                             dump_first_page=args.dump_first_page)
        return 1 if errors else 0
    if args.command == "patch":
        rc = run_patch(ids=args.ids, domain=args.domain, allow_partial=args.allow_partial)
        if rc:
            sys.exit(rc)
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
