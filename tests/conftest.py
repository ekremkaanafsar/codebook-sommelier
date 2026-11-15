import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _no_real_openalex_key(monkeypatch, tmp_path):
    """Keep a developer's real key (env var or PRIVATE_ file) out of every test."""
    import citations_common
    monkeypatch.delenv("OPENALEX_API_KEY", raising=False)
    monkeypatch.setattr(citations_common, "API_KEY_FILE", tmp_path / "no_such_key_file.txt")


@pytest.fixture(autouse=True)
def _guard_real_data_untouched():
    """Fail any test that creates, deletes or modifies a file under data/citations/ or logs/."""
    root = Path(__file__).resolve().parent.parent

    def snap():
        out = {}
        for d in ("data/citations", "logs"):
            for f in (root / d).rglob("*"):
                if f.is_file():
                    st = f.stat()
                    out[str(f)] = (st.st_mtime_ns, st.st_size)
        return out

    before = snap()
    yield
    after = snap()
    changed = sorted(k for k in before.keys() | after.keys() if before.get(k) != after.get(k))
    assert not changed, f"test touched real project files: {changed}"
