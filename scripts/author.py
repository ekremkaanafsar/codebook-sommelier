"""Author, rights and release settings shared by build_pdf.py and build_xlsx.py.

Edit data/author.json (not this file) to change the name, contact, version or DOI.
"""
import json
import secrets

from schema import DATA, ROOT

AUTHOR = json.loads((DATA / "author.json").read_text(encoding="utf-8"))
COVER_NOTE_FILE = ROOT / "PRIVATE_cover_note.txt"


def load_cover_note(path=None):
    """Optional note kept outside version control; empty string when the file is absent."""
    path = COVER_NOTE_FILE if path is None else path
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return ""


AUTHOR["cover_note"] = load_cover_note()


def copyright_line():
    return f"© {AUTHOR['year']} {AUTHOR['name']}. {AUTHOR['rights']}."


def version_label():
    return f"{AUTHOR['title']} v{AUTHOR['version']} ({AUTHOR['release_date']})"


def doi_url():
    return f"https://doi.org/{AUTHOR['doi']}" if AUTHOR.get("doi") else ""


def orcid_url():
    return f"https://orcid.org/{AUTHOR['orcid']}" if AUTHOR.get("orcid") else ""


def cite_as():
    where = f"Zenodo. {doi_url()}" if AUTHOR.get("doi") else "Unpublished data catalog."
    return (f"{AUTHOR['citation_name']}. {AUTHOR['year']}. {AUTHOR['title']}. "
            f"Version {AUTHOR['version']}, {AUTHOR['release_date']}. {where}")


def sheet_password():
    """Password for Excel sheet protection; created once and kept outside outputs/."""
    path = ROOT / AUTHOR["sheet_password_file"]
    if not path.exists():
        path.write_text(secrets.token_urlsafe(12) + "\n", encoding="utf-8")
    return path.read_text(encoding="utf-8").strip()
