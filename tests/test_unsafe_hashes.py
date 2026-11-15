"""Hash mode of the unsafe-domain blocklist (fictional domains only)."""
import hashlib

import pytest

import privacy_check as pc
import unsafe

BAD = "bad" + ".example"


def h(d):
    return hashlib.sha256(d.lower().encode()).hexdigest()


@pytest.fixture
def hash_mode(tmp_path):
    f = tmp_path / "u.sha256"
    f.write_text("# header\n" + h(BAD) + "\n", encoding="utf-8")
    unsafe.configure(tmp_path / "missing.txt", f)
    yield
    unsafe.configure()


def test_hash_mode_find(hash_mode):
    assert unsafe.find_unsafe("see http://" + BAD + "/x") == [BAD]
    assert unsafe.find_unsafe("see WWW.Sub." + BAD.upper() + ".") == ["WWW.Sub." + BAD.upper()]
    assert unsafe.find_unsafe("mail me at a@" + BAD) == [BAD]


@pytest.mark.parametrize("text", ["x-" + BAD, "n" + BAD, BAD + "-x", BAD + ".other.org", "good.example", ""])
def test_hash_mode_find_negatives(hash_mode, text):
    assert unsafe.find_unsafe(text) == []


def test_hash_mode_is_unsafe_host(hash_mode):
    assert unsafe.is_unsafe_host("https://" + BAD + "/p")
    assert unsafe.is_unsafe_host("https://a.b." + BAD + ":8080/p")
    assert unsafe.is_unsafe_host(BAD)
    assert not unsafe.is_unsafe_host("https://x-" + BAD)
    assert not unsafe.is_unsafe_host("https://good.example")


def test_write_hashes_format(tmp_path):
    src = tmp_path / "u.txt"
    src.write_text("# c\n" + BAD + "  # note\nZed.example\n" + BAD + "\n", encoding="utf-8")
    dst = tmp_path / "u.sha256"
    assert unsafe.write_hashes(src, dst) == 2
    lines = dst.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("#") and "--write-hashes" in "\n".join(lines[:3])
    body = [x for x in lines if not x.startswith("#")]
    assert body == sorted([h(BAD), h("zed.example")])
    assert "\r" not in dst.read_text(encoding="utf-8")
    assert BAD not in dst.read_text(encoding="utf-8")


def _root(tmp_path, with_hashes=None):
    (tmp_path / "PRIVATE_identifiers.txt").write_text("tester-user\n", encoding="utf-8")
    (tmp_path / "data").mkdir()
    if with_hashes is not None:
        (tmp_path / "data" / "unsafe_domains.sha256").write_text("# x\n" + "\n".join(with_hashes) + "\n")
    (tmp_path / "pub").mkdir()
    return tmp_path


def _scan(root, text, *extra):
    f = root / "pub" / "a.txt"
    f.write_text(text, encoding="utf-8")
    return pc.main([str(f), "--root", str(root), *extra])


def test_privacy_hash_hit_and_pass(tmp_path, capsys):
    root = _root(tmp_path, [h(BAD)])
    assert _scan(root, "link: https://www." + BAD + "/a\n") == 1
    assert "unsafe_domain" in capsys.readouterr().out
    assert _scan(root, "link: https://x-" + BAD + " and good.example\n") == 0


def test_privacy_hash_hit_in_decoded_variant(tmp_path):
    root = _root(tmp_path, [h(BAD)])
    assert _scan(root, "u=https%3A%2F%2F" + BAD.replace(".", "%2E") + "%2Fa\n") == 1


def test_privacy_neither_file(tmp_path, capsys):
    root = _root(tmp_path)
    with pytest.raises(SystemExit) as e:
        _scan(root, "clean\n")
    assert e.value.code == 2
    assert "fail closed" in capsys.readouterr().err
    assert _scan(root, "clean\n", "--ci") == 0
    assert "skipped" in capsys.readouterr().err


@pytest.mark.parametrize("text", ["*." + BAD, "." + BAD, ".." + BAD, "a." + BAD + "/x"])
def test_hash_mode_hosts_after_dots_hit(hash_mode, text):
    assert unsafe.find_unsafe(text)


@pytest.mark.parametrize("url", ["http://u@" + BAD + "/", "http://" + BAD + "./", "http://u:p@a." + BAD + ":80/x"])
def test_is_unsafe_host_userinfo_and_trailing_dot(tmp_path, url):
    f = tmp_path / "u.txt"
    f.write_text(BAD + "\n", encoding="utf-8")
    h = tmp_path / "u.sha256"
    h.write_text(sha_line(), encoding="utf-8")
    try:
        unsafe.configure(f, tmp_path / "none.sha256")
        assert unsafe.is_unsafe_host(url)
        unsafe.configure(tmp_path / "none.txt", h)
        assert unsafe.is_unsafe_host(url)
    finally:
        unsafe.configure()


def sha_line():
    return h(BAD) + "\n"


def test_privacy_hash_hosts_after_dots(tmp_path):
    root = _root(tmp_path, [h(BAD)])
    for t in ("*." + BAD, "." + BAD, "x-" + BAD, "n" + BAD, BAD + "-x", BAD + ".other.org"):
        assert _scan(root, t + "\n") == (1 if t in ("*." + BAD, "." + BAD) else 0), t


def test_warning_when_no_blocklist(tmp_path, capsys):
    try:
        unsafe.configure(tmp_path / "n.txt", tmp_path / "n.sha256")
        assert "no unsafe-domain blocklist" in capsys.readouterr().err
        assert unsafe.find_unsafe("a." + BAD) == [] and not unsafe.is_unsafe_host(BAD)
    finally:
        unsafe.configure()
