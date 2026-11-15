# Political Science Data Catalog

A catalog of datasets, data portals and licensed data products used in political science. Each row records what a
resource contains, how it was built, how to get it, how it is cited and how it can be joined to other data. The
catalog does not contain the datasets themselves, only descriptions and links.

Website: https://ekremkaanafsar.github.io/codebook-sommelier/

## What is in it

- 2,269 catalog rows in 21 subfield domains (regimes, elections, parties, legislatures, conflict, public opinion,
  international relations, political economy and others).
- By resource type: 1,861 research datasets, 331 data portals or aggregators, 77 commercial or licensed products.
- By access level: 1,847 open, 222 registration, 95 restricted, 44 licensed, 47 commercial, 14 not yet determined.
- Two annex tables: 128 literature and reference databases and 196 primary-source and document archives.
- A citation record (OpenAlex counts for the reference works), comparison groups of competing measures, and an
  identifier crosswalk.

Values that could not be confirmed on an official page are marked in the data as UNVERIFIED rather than filled in.

How the catalog is built (domain taxonomy, schema, quality rubric, discovery tiers, verification procedure,
accuracy rules and inclusion criteria) is described in [`PROTOCOL.md`](PROTOCOL.md).

## How to browse

- The website (https://ekremkaanafsar.github.io/codebook-sommelier/) is built from the data in this repository
  (see "How to build"). Its home page lists the domains;
  `explore/` holds the filterable table with text search, and each dataset has its own page under `datasets/`.
  The site address is set in `data/site_config.json` (`base_url`, `repo_url`).
- The raw table is `data/catalog.csv` (UTF-8, one row per resource, header row as the schema).
- Machine-readable copies are written to `api/` in the built site.

## Quality scores

Each row carries eight rubric scores (validity, reliability, transparency, coverage, comparability, maintenance,
accessibility, adoption; rubric in `PROTOCOL.md` section 7) and their mean. The scores were assigned with AI
assistance from the documentation found for each resource. They are provisional and have not yet been validated by human reviewers. Treat them as a
starting point for your own assessment, not as a ranking.

## How to cite

Use `CITATION.cff` (GitHub shows a "Cite this repository" button). The DOI is
https://doi.org/10.5281/zenodo.24137498, the Zenodo concept DOI that resolves to the latest version. Version 1.1,
released 2026-11-11.

## Licence

- Data (`data/` and the built site content): Creative Commons Attribution 4.0 International, see `LICENSE-DATA`.
- Code (`scripts/`, `templates/`, `site_assets/`, `tests/`): MIT, see `LICENSE`.
- The datasets described in the catalog are not covered by these licences; each keeps its own terms.

## How to build

```
pip install -r requirements-site.txt
python scripts/build_site.py --out site
```

After that install, the site tests run with
`python -m pytest -q tests/test_build_site.py tests/test_site_common.py tests/test_facets.py`. The full suite
(`python -m pytest -q`) also needs `pip install -r requirements-citations.txt` (workbook, PDF and co-usage tests).
`scripts/privacy_check.py` scans a directory for private information and is run before every publication.

On a GitHub Pages project site (`https://<user>.github.io/<repo>/`) crawlers never read the `robots.txt` the build
writes, because they look for it only at the host root; submit `sitemap.xml` in Google Search Console instead.

## How to report a correction

[Open an issue](https://github.com/ekremkaanafsar/codebook-sommelier/issues/new/choose) and choose a template:

- Correction: a wrong or outdated value in a row (give the dataset id, the field, the current and proposed values
  and a source URL).
- Suggest a dataset: a resource that should be added (name, landing page, reference article or DOI, and why it
  qualifies).
- Broken link: a link that no longer works (dataset id, URL, what happens).

## Third-party code

- `site_assets/minisearch.min.js`: MiniSearch 7.2.0 (npm package `minisearch`), MIT licence. Vendored unmodified from
  https://cdn.jsdelivr.net/npm/minisearch@7.2.0/dist/umd/index.min.js with a header comment naming the version, source
  and licence. Used by `site_assets/search.js` for the explorer's text search; it is never loaded from a CDN.
