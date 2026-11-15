"""Check landing/download/codebook URLs and update link_status in data/catalog.csv.

GET with a browser User-Agent, redirects followed, 20 s timeout, one retry; rate-limiting hosts
(Dataverse, doi.org, Zenodo, GitHub, Wayback, ICPSR, GESIS) are checked one request at a time, 1.5 s apart. Bodies are
streamed and closed unread, so dataset files are never downloaded.
401/403/405/406/429 -> blocked-by-site (verify manually); 404/410/5xx/connection errors -> broken;
Manual checks in data/link_overrides.csv (url,status,verified_on,...) replace any blocked-by-site or broken result seen only by scripts
result when they are less than 90 days old.
a different final host or path -> redirect; otherwise ok.

Usage: python scripts/check_links.py [DOMAIN_CODE ...]   (default: all rows)
Writes logs/link_check_YYYY-MM-DD.csv (one row per URL) and updates the catalog.
"""
import csv
import datetime as dt
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import requests
import urllib3

from schema import CATALOG, COLUMNS, DATA, LOGS
from unsafe import is_unsafe_host

urllib3.disable_warnings()
TODAY = dt.date.today().isoformat()
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
           "Accept-Language": "en-US,en;q=0.9"}
URL_FIELDS = ["landing_url", "download_url", "codebook_url"]
SEVERITY = {"ok": 0, "redirect": 1, "blocked-by-site": 2, "broken": 3}


def extract_urls(text):
    urls = re.findall(r"https?://[^\s;,]+", text or "")
    return [u.rstrip(").,'\"") for u in urls if "<" not in u]


def canon(u):
    p = urlparse(u)
    host = p.netloc.lower().removeprefix("www.")
    return host, p.path.rstrip("/").lower()


# hosts that rate-block bulk checks (Harvard Dataverse blocked this IP on 2026-11-05 after ~600 requests):
# one request at a time per host, at least THROTTLE_GAP seconds apart
THROTTLED = {"dataverse.harvard.edu", "doi.org", "dx.doi.org", "web.archive.org", "zenodo.org", "github.com",
             "hdl.handle.net", "www.icpsr.umich.edu", "search.gesis.org"}
THROTTLE_GAP = 1.5
_host_locks = {h: threading.Lock() for h in THROTTLED}
_host_last = {h: 0.0 for h in THROTTLED}


def fetch(url):
    host = urlparse(url).netloc.lower()
    if host in _host_locks:
        with _host_locks[host]:
            wait = _host_last[host] + THROTTLE_GAP - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            try:
                return _fetch(url)
            finally:
                _host_last[host] = time.monotonic()
    return _fetch(url)


def _fetch(url):
    last = None
    for attempt in range(2):
        try:
            with requests.get(url, headers=HEADERS, timeout=20, allow_redirects=True,
                              stream=True, verify=True) as r:
                return r.status_code, r.url, ""
        except requests.exceptions.SSLError as e:
            # retry without verification once to separate certificate problems from dead hosts
            try:
                with requests.get(url, headers=HEADERS, timeout=20, allow_redirects=True,
                                  stream=True, verify=False) as r:
                    return r.status_code, r.url, "ssl-warning"
            except requests.RequestException as e2:
                last = e2
        except requests.RequestException as e:
            last = e
    return None, "", type(last).__name__


def classify(url, code, final):
    if code is None:
        return "broken"
    if code in (401, 403, 405, 406, 429):
        return "blocked-by-site"
    if code >= 400:
        return "broken"
    return "redirect" if canon(final) != canon(url) else "ok"


def load_overrides():
    path = DATA / "link_overrides.csv"
    if not path.exists():
        return {}
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    cutoff = (dt.date.today() - dt.timedelta(days=90)).isoformat()
    return {r["url"]: r for r in rows if r["verified_on"] >= cutoff}


OVERRIDES = load_overrides()


def check(item):
    rid, field, url = item
    if is_unsafe_host(url):
        # listed in data/unsafe_domains.txt: never fetched; the link must be removed at source
        return {"id": rid, "field": field, "url": url, "status_code": "", "final_url": "",
                "link_status": "broken", "note": "unsafe domain (data/unsafe_domains.txt); not fetched", "checked": TODAY}
    code, final, note = fetch(url)
    # cran.r-project.org intermittently resets connections from this network; the official
    # cloud mirror serves the same paths, so retry there and say so in the note
    host = urlparse(url).netloc.lower()
    if not code and host == "cran.r-project.org":
        mirror = "https://cloud.r-project.org" + url.split(urlparse(url).netloc, 1)[1]
        m_code, m_final, m_note = fetch(mirror)
        if m_code:
            code, final = m_code, m_final
            note = f"cran.r-project.org did not respond; checked on the official mirror {mirror}"
    status = classify(url, code, final)
    ov = OVERRIDES.get(url)
    # a dated manual browser check replaces any blocked or broken result seen only by scripts
    if ov and status in ("blocked-by-site", "broken"):
        status = ov["status"]
        note = f"{ov['method']} {ov['verified_on']}: {ov['note']}"
    return {"id": rid, "field": field, "url": url, "status_code": code or "",
            "final_url": final, "link_status": status, "note": note, "checked": TODAY}


def main():
    domains = {d.upper() for d in sys.argv[1:]}
    with open(CATALOG, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    # arguments may be domain codes (HIST) or single row IDs (LEG-091)
    targets = [r for r in rows if not domains or r["id"].split("-")[0] in domains or r["id"] in domains]
    # fields may hold several URLs plus notes ("url (Stata); url2"): check each URL separately
    items = list(dict.fromkeys((r["id"], fld, u) for r in targets for fld in URL_FIELDS
                               for u in extract_urls(r[fld])))
    with ThreadPoolExecutor(max_workers=24) as ex:
        results = list(ex.map(check, items))
    by_id = {}
    for res in results:
        by_id.setdefault(res["id"], []).append(res)
    for r in targets:
        res = by_id.get(r["id"], [])
        if not res:
            continue
        landing = [x for x in res if x["field"] == "landing_url"][:1]
        worst = max(res, key=lambda x: SEVERITY[x["link_status"]])["link_status"]
        # row status: landing page status, escalated to broken if any other link is broken
        status = landing[0]["link_status"] if landing else worst
        if worst == "broken":
            status = "broken"
        r["link_status"] = status
        r["links_checked"] = TODAY
    LOGS.mkdir(exist_ok=True)
    out = LOGS / f"link_check_{TODAY}.csv"
    prior = []
    if out.exists():
        with open(out, newline="", encoding="utf-8") as f:
            prior = [p for p in csv.DictReader(f) if p["id"] not in by_id]
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["id", "field", "url", "status_code", "final_url",
                                          "link_status", "note", "checked"])
        w.writeheader()
        w.writerows(sorted(prior + results, key=lambda x: (x["id"], x["field"])))
    with open(CATALOG, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows({c: r.get(c, "") for c in COLUMNS} for r in rows)
    counts = {}
    for res in results:
        counts[res["link_status"]] = counts.get(res["link_status"], 0) + 1
    print(f"checked {len(results)} URLs on {len(targets)} rows -> {counts}; log: {out}")
    for res in results:
        if res["link_status"] in ("broken", "blocked-by-site"):
            print(f"  {res['link_status']:16} {res['id']} {res['field']} {res['status_code']} {res['url']}")


if __name__ == "__main__":
    main()
