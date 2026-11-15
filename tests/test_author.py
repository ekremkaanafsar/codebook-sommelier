import author


def test_cover_note_present(tmp_path):
    f = tmp_path / "note.txt"
    f.write_text("  Example note  \n", encoding="utf-8")
    assert author.load_cover_note(f) == "Example note"


def test_cover_note_absent(tmp_path):
    assert author.load_cover_note(tmp_path / "missing.txt") == ""


def test_default_path_is_used(monkeypatch, tmp_path):
    f = tmp_path / "note.txt"
    f.write_text("Another note\n", encoding="utf-8")
    monkeypatch.setattr(author, "COVER_NOTE_FILE", f)
    assert author.load_cover_note() == "Another note"
    monkeypatch.setattr(author, "COVER_NOTE_FILE", tmp_path / "none.txt")
    assert author.load_cover_note() == ""


def test_emails_institutional_first():
    assert author.AUTHOR["emails"][0].endswith(".edu")
