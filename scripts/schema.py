"""Shared schema, vocabularies and paths for the catalog scripts (PROTOCOL.md sections 4-7)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
LOGS = ROOT / "logs"
OUTPUTS = ROOT / "outputs"
CATALOG = DATA / "catalog.csv"
CITATIONS = DATA / "citations"
FIGURES = OUTPUTS / "figures"
CITATION_SOURCES = ("Google Scholar", "OpenAlex")

COLUMNS = [
    # A. Identification
    "id", "name", "acronym", "version", "release_date", "maintainer",
    "primary_domain", "secondary_domains", "resource_type",
    # B. Content & design
    "unit_of_analysis", "spatial_coverage", "temporal_coverage", "temporal_resolution", "size",
    "key_variables", "generation_method", "measurement_approach",
    # C. Access
    "landing_url", "download_url", "codebook_url", "formats", "access_level", "license",
    "programmatic_access",
    # D. Scholarly record
    "citation", "doi", "citation_count", "notable_uses",
    # E. Maintenance
    "update_frequency", "last_update", "status",
    # F. Interoperability
    "merge_keys", "related_datasets",
    # G. Quality
    "q_validity", "q_reliability", "q_transparency", "q_coverage", "q_comparability",
    "q_maintenance", "q_accessibility", "q_adoption", "q_overall",
    "quality_justification", "strengths", "limitations", "best_used_for", "avoid_for",
    # H. Provenance & verification
    "found_via", "guide_count", "institutional_access_note", "links_checked", "link_status",
    "confidence", "unverified_notes", "added_on", "last_verified_on",
]

Q_DIMS = ["q_validity", "q_reliability", "q_transparency", "q_coverage", "q_comparability",
          "q_maintenance", "q_accessibility", "q_adoption"]

DOMAINS = [
    (1, "REG", "Regimes, democracy & governance"),
    (2, "ELEC", "Elections & electoral systems"),
    (3, "PARTY", "Political parties & party systems"),
    (4, "LEG", "Legislatures & legislative behavior"),
    (5, "EXEC", "Executives, cabinets & leaders"),
    (6, "CONST", "Constitutions, courts, law & territorial institutions"),
    (7, "BUR", "Bureaucracy & public administration"),
    (8, "POLICY", "Public policy & policy agendas"),
    (9, "OPIN", "Political behavior & public opinion"),
    (10, "PE", "Political economy"),
    (11, "IR", "International relations"),
    (12, "CONF", "Conflict & political violence"),
    (13, "HR", "Human rights, repression & contentious politics"),
    (14, "TEXT", "Political communication, media & text-as-data"),
    (15, "IDENT", "Identity, ethnicity, religion & migration"),
    (16, "GENDER", "Gender & politics"),
    (17, "ENV", "Environmental & climate politics"),
    (18, "GEO", "Subnational, local & geospatial politics"),
    (19, "HIST", "Historical political data"),
    (20, "AREA", "Regional and country-specific collections"),
    (21, "INFRA", "Data infrastructure"),
]
CODES = [c for _, c, _ in DOMAINS]
CODE_NAME = {c: n for _, c, n in DOMAINS}
CODE_NUM = {c: i for i, c, _ in DOMAINS}

RESOURCE_TYPES = {"1": "Research dataset", "2": "Data portal / aggregator",
                  "3": "Commercial / licensed data product",
                  "4": "Literature & reference database", "5": "Primary-source & document archive"}
ACCESS_LEVELS = ["open", "registration", "restricted", "licensed", "commercial"]
STATUS_BASE = ["active", "irregular", "frozen", "discontinued"]
LINK_STATUS = ["ok", "redirect", "blocked-by-site", "broken"]
CONFIDENCE = ["High", "Medium", "Low"]

ANNEX_COLUMNS = ["name", "provider", "resource_type", "url", "description", "access_level",
                 "institutional_access_note", "relevant_domains", "found_via", "status",
                 "unverified_notes", "added_on", "last_verified_on"]
ANNEX_FILES = {"4": DATA / "literature_databases.csv", "5": DATA / "primary_archives.csv"}

UNVERIFIED = "UNVERIFIED"

# Static-site paths
DERIVED = DATA / "derived"
SITE = ROOT / "site"
PUBLIC = ROOT / "public"
TEMPLATES = ROOT / "templates" / "site"
SITE_ASSETS = ROOT / "site_assets"
SITE_CONFIG = DATA / "site_config.json"
