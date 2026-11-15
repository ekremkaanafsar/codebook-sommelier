# Political Science Data Catalog: build protocol

This document describes how the catalog is built and maintained: its scope, taxonomy, schema, quality rubric,
discovery and verification procedure, accuracy rules and inclusion criteria. Section numbers are stable; the scripts
and the catalog notes refer to them (for example "PROTOCOL.md section 13").

## 1. Purpose

The project maintains a verified, citable and continuously updatable catalog of political science datasets covering
every subfield. The intended standard is that of a peer-reviewed data feature article (Journal of Peace Research,
Research & Politics, Political Science Research and Methods, Scientific Data).

The catalog has three outputs, all regenerated from one master data file:

1. `outputs/polisci_data_catalog.xlsx`: the master table, one row per dataset, filterable.
2. `outputs/polisci_data_catalog.pdf`: an academic report (methods, domain overviews, comparison tables,
   bibliography).
3. `data/catalog.csv`: the single source of truth. The workbook, the report and the static site are never edited by
   hand; they are always rebuilt from the CSV.

## 2. Maintenance cycle

Work proceeds one domain at a time; a run never covers the whole discipline at once. A domain run discovers,
verifies and merges the datasets of one domain (section 8). An update pass re-verifies the existing rows of a domain,
adds new releases and datasets, and logs the changes. An audit re-runs the link checks and the self-audit across the
whole catalog and reports problems only. A build regenerates the workbook, the report and the static site from
`data/catalog.csv`. The OpenAlex citation record (`scripts/openalex.py`) and the dataset co-usage network
(`scripts/cousage.py`) are refreshed separately, and a privacy check runs before anything is published.

## 3. Repository layout

- `data/catalog.csv`: master table (UTF-8, comma-separated, header row = the schema in section 6).
- `data/literature_databases.csv` and `data/primary_archives.csv`: annex tables for resource types 4 and 5.
- `data/guide_sweep.csv`: one row per resource listed in a library guide (resource name, institution, guide URL,
  page or tab, date seen).
- `data/comparison_groups.csv`: groups of competing measures of the same concept.
- `data/citations/`: the OpenAlex citation record (resolved reference work and counts per catalog id, citing works)
  and the co-usage network (nodes, edges, domain matrix).
- `data/derived/`: facets derived from the catalog (unit class, regions, start and end years).
- `data/narratives/`: domain narratives used by the report and the site.
- `data/progress.json`: per-domain status.
- `scripts/`: the build, merge, link-check, audit, citation, site and privacy scripts (Python).
- `templates/site/`, `site_assets/`: templates and assets of the static site.
- `tests/`: the test suite.
- `outputs/figures/`: co-usage network and domain heatmap figures.

Candidate lists, raw verification records and search logs are kept in the working repository for provenance and are
not part of the public copy.

## 4. Domain taxonomy

Every dataset has exactly one primary domain and any number of secondary domains.

| N | Code | Domain |
|---|---|---|
| 1 | REG | Regimes, democracy & governance (democracy indices, regime type, state capacity, rule of law, corruption) |
| 2 | ELEC | Elections & electoral systems (results, rules, turnout, electoral integrity, district-level data) |
| 3 | PARTY | Political parties & party systems (positions, manifestos, organization, populism) |
| 4 | LEG | Legislatures & legislative behavior (roll calls, bills, committees, speeches) |
| 5 | EXEC | Executives, cabinets & leaders (government composition, coalitions, leader biographies) |
| 6 | CONST | Constitutions, courts, law & territorial institutions (constitutional texts, judicial independence, federalism) |
| 7 | BUR | Bureaucracy & public administration |
| 8 | POLICY | Public policy & policy agendas (welfare state, diffusion, budgets, regulation) |
| 9 | OPIN | Political behavior & public opinion (cross-national surveys, election studies, panels) |
| 10 | PE | Political economy (development, inequality, taxation, trade, finance, aid) |
| 11 | IR | International relations (disputes, alliances, IOs, treaties, diplomacy, UN voting, sanctions) |
| 12 | CONF | Conflict & political violence (civil war, armed actors, terrorism, coups, peace agreements) |
| 13 | HR | Human rights, repression & contentious politics (physical integrity, protest, social movements) |
| 14 | TEXT | Political communication, media & text-as-data |
| 15 | IDENT | Identity, ethnicity, religion & migration |
| 16 | GENDER | Gender & politics |
| 17 | ENV | Environmental & climate politics |
| 18 | GEO | Subnational, local & geospatial politics |
| 19 | HIST | Historical political data (pre-1945, long-run series) |
| 20 | AREA | Regional and country-specific collections (US, EU, Africa, Latin America, Asia, MENA, post-communist) |
| 21 | INFRA | Data infrastructure (merged datasets, crosswalks, country-code systems, R/Python data packages) |

## 5. Resource types

Every resource found is classified. Only types 1-3 enter `data/catalog.csv`; types 4-5 go to the annex tables.

1. **Research dataset**: structured, documented, downloadable or requestable.
2. **Data portal / aggregator**: for example QoG, UNdata, Data Planet, Social Explorer.
3. **Commercial / licensed data product**: for example Roper iPoll, the Cross-National Time-Series Data Archive,
   Global Financial Data, ICRG, EIU. The row notes which institutions typically subscribe.
4. **Literature & reference database**: for example Political Science Complete, PAIS, Worldwide Political Science
   Abstracts, JSTOR, CQ Researcher.
5. **Primary-source & document archive**: for example ProQuest Congressional, UN document systems,
   declassified-document collections, historical newspapers.

## 6. Schema of `data/catalog.csv`

The column order is fixed. `UNVERIFIED` marks any value not confirmed on a page actually opened for the project;
`N/A` is used only when a field truly does not apply.

**A. Identification**
- `id`: `<CODE>-<3-digit>` (for example `REG-007`). IDs are never reused or renumbered; retired rows keep their ID
  with `status = removed`.
- `name`, `acronym`, `version`, `release_date` (YYYY-MM or YYYY), `maintainer` (PIs and host institution).
- `primary_domain` (code), `secondary_domains` (codes, `;`-separated), `resource_type` (1-3).

**B. Content & design**
- `unit_of_analysis` (country-year, dyad-year, event, respondent, legislator-vote, party-election, municipality,
  document, grid-cell-year, ...).
- `spatial_coverage`, `temporal_coverage` (start-end), `temporal_resolution`, `size` (N observations / N variables).
- `key_variables` (5-10, `;`-separated).
- `generation_method` (expert coding; survey; event data from news; administrative records; text/NLP; remote
  sensing; aggregation).
- `measurement_approach` (for example Bayesian IRT latent scores; additive index; dichotomous coding; raw counts).

**C. Access**
- `landing_url`, `download_url` (or `registration required` / `on request`), `codebook_url`.
- `formats`, `access_level` (open / registration / restricted / licensed / commercial), `license`.
- `programmatic_access` (APIs, R/Python packages).

**D. Scholarly record**
- `citation` (full canonical reference, APSA style), `doi`.
- `citation_count`: count for the reference article with source and check date, as "N (Google Scholar, checked
  YYYY-MM)" or "N (OpenAlex, checked YYYY-MM)"; OpenAlex counts are a lower bound.
- `notable_uses` (2-3 studies, author and year).

**E. Maintenance**
- `update_frequency`, `last_update`, `status` (active / irregular / frozen / discontinued / superseded by `<id>`).

**F. Interoperability**
- `merge_keys` (COW code, Gleditsch-Ward, ISO3, V-Dem country_id, ParlGov IDs, FIPS, ...).
- `related_datasets` (IDs of competitors and successors, `;`-separated).

**G. Quality** (rubric in section 7)
- `q_validity`, `q_reliability`, `q_transparency`, `q_coverage`, `q_comparability`, `q_maintenance`,
  `q_accessibility`, `q_adoption`, and `q_overall` (unweighted mean, one decimal).
- `quality_justification`, `strengths`, `limitations` (including published critiques with author and year),
  `best_used_for`, `avoid_for`.

**H. Provenance & verification**
- `found_via` (every guide, meta-list and repository listing the resource, `;`-separated).
- `guide_count` (number of distinct university library guides listing it).
- `institutional_access_note` (free, or usually available through a university subscription).
- `links_checked` (YYYY-MM-DD), `link_status` (ok / redirect / blocked-by-site / broken).
- `confidence` (High / Medium / Low), `unverified_notes`.
- `added_on`, `last_verified_on`.

## 7. Quality rubric

Each dimension is scored from 1 to 5 and justified in `quality_justification`.

| Dimension | 1 | 3 | 5 |
|---|---|---|---|
| Validity | Concept undefined or indicator weakly tied to it | Defined concept, some known mismatch | Explicit concept, validated against alternatives |
| Reliability | No reliability information | Some coder/sampling detail | Intercoder reliability or measurement uncertainty reported; replicable |
| Transparency | No codebook | Codebook, partial sources | Full codebook, sources, coding rules, revision history |
| Coverage | Narrow, many gaps | Moderate breadth, documented gaps | Broad space and time, missingness documented |
| Comparability | Undocumented series breaks | Mostly consistent | Consistent across units and time; breaks documented |
| Maintenance | Abandoned, no archive | Irregular updates | Regular versioned releases |
| Accessibility | Hard to obtain | Registration or awkward formats | Open, standard formats, API or package |
| Adoption | Rarely used | Used in field journals | Widely used in top journals |

Scores reflect a dataset's fitness for research, not a view of its conclusions, and the rubric is applied in the
same way across domains. The scores were assigned with AI assistance from the documentation found for each resource
and are published with the label "AI-assisted, provisional, not yet human-validated".

## 8. Procedure for a domain run

1. **Plan.** `data/progress.json` and `data/catalog.csv` are reviewed to note which datasets already exist, so that
   nothing is duplicated.
2. **Discover candidates.** Every tier in section 9 is searched for the domain, starting with the must-include list
   in section 10. Every candidate is listed with its resource type and where it was found, and every query and source
   checked is logged. The aim is completeness: the core canon, major regional datasets, newer and niche projects,
   and discontinued datasets still in wide use.
3. **Verify.** Candidates are verified in groups of 6-8. For every dataset the official landing page, download page
   and codebook are located through web search and consulted directly, and one record per dataset is written with
   the schema of section 6, the rubric of section 7 and the accuracy rules of section 11. Each group's records are
   saved as `data/research/Dxx_batchK.json` (a JSON array keyed by schema column names) and kept for provenance,
   together with the list of fields left UNVERIFIED.
   Verification is AI-assisted: the records are researched and drafted with AI assistance from the pages opened,
   and the merge validation and the self-audit below check them before release.
4. **Merge.** `scripts/merge_records.py` assigns IDs, deduplicates (normalised name, acronym and landing-page
   domain; on a match it merges fields, keeps the most recent verified values and unions `found_via`), validates
   column types and controlled vocabularies, and writes `data/catalog.csv`. Type 4-5 resources go to the annex
   tables.
5. **Check links.** `scripts/check_links.py` requests every link of the domain's rows with GET and a normal browser
   User-Agent, follows redirects, waits up to 20 seconds and retries once. It records the final URL and status. An
   HTTP 401, 403 or 429 is recorded as `blocked-by-site`, not broken, and marked for manual verification. Results go
   to a dated link-check log and to `link_status`.
6. **Self-audit.** Every row is checked against this list and fixed or flagged:
   - every URL, DOI and citation came from a page opened for the project;
   - coverage years and version agree between landing page and codebook; conflicts are reported with both values;
   - the version most cited in the literature is flagged when it differs from the latest version;
   - scores follow the rubric and are justified;
   - no text is copied from source websites; all descriptions are original wording;
   - every must-include dataset for the domain is present, or its absence is explained.
7. **Build.** The workbook and the report are rebuilt (section 12).
8. **Record.** `data/progress.json` is updated, and the date, the domain, the rows added, updated and removed, the
   open UNVERIFIED items, the 3-5 core datasets of the domain and any borderline exclusions are logged.

## 9. Source discovery tiers

**Tier A: university library research guides (LibGuides and equivalents).** Every dataset listed in political
science, statistics/data, public opinion, elections, IR/conflict, public policy, area-studies, and topic or event
guides is harvested. The sweep starts with these guides:

- Binghamton: libraryguides.binghamton.edu/polisci, /poli_stats, /opinion, /elections (incl. Roll Call Votes and
  Campaign Finance tabs), /issues, /tiananman; index at libraryguides.binghamton.edu/?b=s
- Princeton: libguides.princeton.edu/politics/data, /politics/conflict; topic guides listed at
  jdarring.scholar.princeton.edu/research-guides
- Harvard: guides.library.harvard.edu/gov/ir, /gov/amgov, /hks/international_relations/statistics
- MIT: libguides.mit.edu/polisci/data
- NYU: guides.nyu.edu/polisci/political-data-and-statistics, /comparative-politics
- UC Berkeley: guides.lib.berkeley.edu/PoliSciSubfields
- University of Washington: guides.lib.uw.edu/research/polisci/primarysources
- Penn State: guides.libraries.psu.edu/political-science/stats
- Pittsburgh: pitt.libguides.com/conflictstudies/datasets
- USC: libguides.usc.edu/IR/War, /IR/Statistics
- William & Mary: guides.libraries.wm.edu/IRdata
- Emory: guides.libraries.emory.edu/main/Data_Services/intlinks
- Cornell: guides.library.cornell.edu/peace/home
- UNC: guides.lib.unc.edu/poli350/data-reports
- Kentucky: libguides.uky.edu/datasources/poli
- Houston: guides.lib.uh.edu/political_science
- UCL: library-guides.ucl.ac.uk/data-statistics/conflict-political-risk
- Bristol: bristol.libguides.com/politics/researchdata
- Mannheim: bib.uni-mannheim.de/en/subject-specific-research/political-science

The sweep then expands to guides at the top-50 U.S. political science departments and leading non-U.S.
institutions (Oxford, LSE, Essex, EUI, Sciences Po, Gothenburg, ETH Zurich, Toronto, ANU, Columbia, Yale, Stanford,
Michigan, Chicago, Duke, UCLA, Penn, Texas, Wisconsin and others), using search patterns such as
`site:libguides.*.edu`, `site:guides.*.edu` and `site:*.libguides.com` combined with "political science" and data,
datasets, statistics, public opinion, elections, conflict or a region. Area-studies and event-specific guides are
included because they surface primary-source archives that standard lists miss.

For each guide the institution, URL, page or tab, librarian and the guide's "last updated" date are recorded in
`data/guide_sweep.csv` (the librarian column is not published). Each guide listing a dataset adds to that dataset's
`found_via` and `guide_count`. Swept guides form a cache shared across runs; a guide swept within the previous 90
days is not fetched again.

**Tier B: curated meta-lists and data hubs.** PolData (github.com/erikgahner/PolData; already structured with
coverage, access, licence, codebook and CSV links, used as a seed with every field verified independently);
awesome-democracy-data (github.com/andrewcstewart/awesome-democracy-data); Paul Hensel's International Relations
Data Site; MacroDataGuide; Party Facts; Data is Plural; R and Python packages (peacesciencer, vdemdata,
democracyData, manifestoR, countrycode, WDI and equivalents).

**Tier C: repositories.** Harvard Dataverse (including journal dataverses such as ISQ and International
Interactions), ICPSR, Roper Center, GESIS, UK Data Service, CESSDA, Qualitative Data Repository, OSF, Zenodo,
re3data.org.

**Tier D: journals.** Replication archives and data-feature articles in APSR, AJPS, JOP, BJPolS, CPS, IO, ISQ, JPR,
JCR, Electoral Studies, PSRM, Research & Politics and Scientific Data, searched for "introducing [X] dataset" and
"new data on" over the past ten years.

**Tier E: official producers.** The UN system, World Bank, IMF, OECD, International IDEA, IPU, IFES, Eurostat,
national statistical offices and election commissions.

## 10. Must-include canonical datasets

A domain run is incomplete if any dataset below that belongs to the domain is missing. The list is a floor, not a
ceiling.

- **REG**: V-Dem (incl. Regimes of the World, Episodes of Regime Transformation), Polity5, Freedom House (Freedom in
  the World, Nations in Transit, Freedom on the Net), Boix-Miller-Rosato, Geddes-Wright-Frantz Autocratic Regimes,
  Cheibub-Gandhi-Vreeland Democracy-Dictatorship (and Bjørnskov-Rode extension), Lexical Index of Electoral
  Democracy, Global State of Democracy (IDEA), Bertelsmann Transformation Index, EIU Democracy Index, Unified
  Democracy Scores, Vanhanen Index, Quality of Government, Worldwide Governance Indicators, Database of Political
  Institutions, ICRG, Transparency International CPI, World Justice Project Rule of Law Index, Fragile States Index,
  Relative Political Capacity, Hanson-Sigman state capacity
- **ELEC**: CLEA, ParlGov elections, IDEA Voter Turnout and Electoral System Design databases, Bormann-Golder
  Democratic Electoral Systems, NELDA, Perceptions of Electoral Integrity, IFES ElectionGuide, European Election
  Database, MIT Election Data and Science Lab, Dave Leip's Atlas, VEST precinct data, GRED, Klarner state
  legislative returns
- **PARTY**: Manifesto Project (MARPOR), Chapel Hill Expert Survey, V-Party, Party Facts, PopuList, Global Party
  Survey, POPPA, Political Party Database, DALP
- **LEG**: Voteview, ParlSpeech, Comparative Legislators Database, IPU Parline, Congressional Bills Project,
  Legislative Effectiveness Scores, Shor-McCarty state ideology, European Parliament roll calls
- **EXEC**: Archigos, WhoGov, LEAD, REIGN, CHISOLS, ParlGov cabinets
- **CONST**: Comparative Constitutions Project / Constitute, U.S. Supreme Court Database, Linzer-Staton judicial
  independence, Regional Authority Index
- **OPIN**: ANES, GSS, Cooperative Election Study, World Values Survey / European Values Study, European Social
  Survey, ISSP, CSES, Eurobarometer, Afrobarometer, Arab Barometer, Asian Barometer, AmericasBarometer (LAPOP),
  Latinobarómetro, Pew Global Attitudes and American Trends Panel, European Election Studies, British Election Study
  and other national election studies, Nationscape, Roper iPoll
- **IR**: Correlates of War (MID, NMC, Alliances, IGO, Diplomatic Exchange, Territorial Change, Trade, State
  System), ATOP, ICB, ICOW, UN General Assembly voting (Voeten), DESTA, Global Sanctions Database, TIES, AidData,
  SIPRI Military Expenditure and Arms Transfers, Lowy Global Diplomacy Index, Gleditsch-Ward state list, MIE
- **CONF**: UCDP/PRIO Armed Conflict, UCDP GED, UCDP Non-State and One-Sided Violence, ACLED, Global Terrorism
  Database, ICEWS, GDELT, Ethnic Power Relations / GROW, Minorities at Risk, PITF, Major Episodes of Political
  Violence (INSCR), Powell-Thyne coups, Cline Center coups, UCDP Peace Agreements, PA-X, Pro-Government Militias,
  Women in Armed Rebellion
- **HR**: CIRIGHTS (successor to CIRI), Political Terror Scale, Fariss latent human rights scores, Human Rights
  Measurement Initiative, SVAC, Ill-Treatment and Torture data, SCAD, Mass Mobilization, NAVCO
- **PE**: World Development Indicators, Penn World Table, Maddison Project, IMF WEO/IFS, SWIID, World Inequality
  Database, ICTD Government Revenue, Economic Freedom of the World, Chinn-Ito, Comparative Political Data Set,
  Comparative Welfare Entitlements, OECD SOCX, Clio Infra, Cross-National Time-Series Data Archive
- **POLICY**: Comparative Agendas Project, Correlates of State Policy, SPID
- **TEXT**: ParlSpeech, UN General Debate Corpus, Congressional Record corpora, Media Cloud
- **IDENT**: Fearon and Alesina fractionalization, Religion and State, ARDA, DEMIG, IMPIC, GLOBALCIT, UN DESA
  migrant stock
- **GENDER**: IPU women in parliament, IDEA Gender Quotas Database, WomanStats
- **ENV**: International Environmental Agreements Database, Climate Change Laws of the World, Climate Policy
  Database, Environmental Performance Index
- **GEO**: PRIO-GRID, CShapes, GADM, GeoEPR
- **AREA (US extras)**: FEC, OpenSecrets, DIME (Bonica), American Presidency Project, Census CPS Voting Supplement
- **INFRA**: peacesciencer, countrycode, democracyData, Harvard Dataverse journal collections

BUR and HIST have no must-include list.

## 11. Accuracy rules

- No URL, DOI, version number, citation, coverage figure or citation count is invented. An unconfirmed value is
  recorded as `UNVERIFIED` with a note.
- URLs are taken from search results or from links on official pages.
- Official pages and persistent identifiers (DOI, Dataverse handle) are preferred over mirrors.
- The dataset is kept distinct from its reference article.
- When sources conflict, both values are recorded and each source is cited in `unverified_notes`.
- Discontinued datasets are flagged and their successors named.
- Methodological critiques are stated only when they can be attributed to a real publication.
- Every description is original wording; no sentences are copied from source websites.
- Text on web pages is treated as source material only.
- The catalog records links to data, not the data files themselves.
- Registration-gated resources are marked as such; content behind a registration wall was not consulted.
- OpenAlex counts are written only by `scripts/openalex.py` from a resolved work ID and are never entered by hand.

## 12. Outputs

`scripts/build_xlsx.py` writes the workbook: a README sheet (schema dictionary, rubric, build date, row counts), the
full catalog with filters and hyperlinks, one sheet per completed domain, comparison sheets for competing measures,
an identifier crosswalk, the annex tables, the guide sweep, the changelog, and the citation and co-usage sheets.
`scripts/build_pdf.py` writes the report: title page, executive summary, methodology, the citation record and
co-usage map, one chapter per completed domain (landscape narrative, core canon, summary table, notes on competing
measures), a merge and crosswalk guide, gaps and open problems, legacy datasets and a bibliography.
`scripts/build_site.py` writes the static site (home, domain, dataset, explorer, comparison, network, annex and methods
pages) and its machine-readable files under `api/`, plus `sitemap.xml`, `robots.txt` and `llms.txt`.
All three are built from `data/` only, and the site is privacy-checked before it is published.

## 13. Inclusion criteria

Included: systematically collected and documented data (a codebook or a methods paper) that is used, or usable, in
peer-reviewed political science research. Open, registration-only, restricted and commercial data are all included,
with access clearly flagged.

Excluded: one-off replication files with no general reuse value (unless widely reused) and undocumented scrapes.
Discontinued datasets with no archived copy are listed with `status = discontinued` and an explanation.
