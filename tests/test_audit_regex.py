from citations_common import COUNT_PREFIX_RE, COUNT_RE, LEGACY_RE, count_value_ok, relabel_count


def test_count_re_accepts_labelled_rejects_legacy():
    assert COUNT_RE.match("1,268 (Google Scholar, checked 2026-11)")
    assert COUNT_RE.match("958 (OpenAlex, checked 2026-11)")
    assert not COUNT_RE.match("12 (checked 2026-11)")
    assert not COUNT_RE.match("12 (OpenAlex)")
    assert not COUNT_RE.match("12 (Google Scholar, checked 2026-11) for X")


def test_legacy_re_matches_only_bare_form():
    assert LEGACY_RE.match("1268 (checked 2026-11)")
    assert not LEGACY_RE.match("958 (OpenAlex, checked 2026-11)")


def test_count_value_ok_multi_reference():
    ok = "102 (Google Scholar, checked 2026-11) for A 2017; 5 (OpenAlex, checked 2026-11) for Z"
    assert count_value_ok(ok)
    assert count_value_ok("958 (OpenAlex, checked 2026-11)")
    assert count_value_ok("UNVERIFIED: none") and count_value_ok("N/A")
    assert not count_value_ok("102 (checked 2026-11) for A; 5 (checked 2026-11) for Z")
    assert count_value_ok("102 (Google Scholar, checked 2026-11) for A; 5 for Z")  # R2b: leading labelled count
    assert COUNT_PREFIX_RE.match("5 (OpenAlex, checked 2026-11) for Z")


def test_relabel_multi_reference_passes_audit():
    legacy = "102 (checked 2026-11) for Strøm et al. 2017; 5 (checked 2026-11) for Z"
    assert not count_value_ok(legacy)
    new = relabel_count(legacy)
    assert new == ("102 (Google Scholar, checked 2026-11) for Strøm et al. 2017; "
                   "5 (Google Scholar, checked 2026-11) for Z")
    assert count_value_ok(new)


def test_relabel_leaves_others_unchanged():
    for v in ("", "UNVERIFIED", "N/A", "958 (OpenAlex, checked 2026-11)",
              "7 (Google Scholar, checked 2026-11)"):
        assert relabel_count(v) == v


def test_count_value_ok_r2b_qualifier_text():
    const010 = ("35 (Google Scholar, checked 2026-11) for Juon and Bochsler 2022; "
                "the requested book citation is forthcoming")
    assert count_value_ok(const010)
    assert not count_value_ok("12 (checked 2026-11)")
    assert not count_value_ok("12 (OpenAlex)")
    assert not count_value_ok("12 for X; 5 (Google Scholar, checked 2026-11) for Z")


def test_audit_openalex_mismatch_problems(tmp_path):
    import csv

    import audit_catalog
    rows = [{"id": "A-1", "citation_count": "5 (OpenAlex, checked 2026-11)"},
            {"id": "A-2", "citation_count": "5 (OpenAlex, checked 2026-11)"}]
    assert audit_catalog.openalex_mismatch_problems(rows, tmp_path / "nope.csv") == []  # skipped silently
    path = tmp_path / "openalex_works.csv"
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["id", "status", "cited_by_count"])
        w.writeheader()
        w.writerows([{"id": "A-1", "status": "ok", "cited_by_count": "5"},
                     {"id": "A-2", "status": "dataset_only", "cited_by_count": "5"}])
    got = audit_catalog.openalex_mismatch_problems(rows, path)
    assert len(got) == 1 and got[0].startswith("A-2:") and "dataset_only" in got[0]
    assert audit_catalog.OPENALEX_MISMATCH_KEY == "citation_count_openalex_mismatch"
