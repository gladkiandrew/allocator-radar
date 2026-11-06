---
type: project-doc
project: Allocator-Radar
title: "AGENDA — Allocator Radar build plan"
status: draft-v0.1
created: 2026-11-05
tags: [project, allocator-radar, plan, hedge-fund-bd]
---

# AGENDA.md — ALLOCATOR RADAR

> **Status: v0.2 (2026-11-06). Source inventory fully verified by eight research passes.**
> **VERIFIED** = loaded and read this session (raw sweep evidence in `verification/`; terms
> quoted by research agents with the URL they read). **PENDING** items are queued in
> `NEXT-SESSION.md`. Nothing here is asserted from memory without a marker. **Not yet approved
> by the owner** — superseded: §10 was answered in docs/BRIEF.md (2026-11-06).
>
> Companion files: `DECISIONS.md` (every assumption made without asking) ·
> `NEXT-SESSION.md` (handoff) · `verification/` (raw evidence).

---

## 1. Vision & scope

**Allocator Radar** is a local, agentic intelligence engine for hedge-fund business
development. It reads the entire *public, login-free* record on institutional allocators and
the managers they hire, and maintains a living, cited graph of:

- **Allocations** — who allocates to which strategy bucket, how much (as disclosed), since when.
- **Incumbents** — which managers hold which mandates, on what *disclosed* terms.
- **Dated signals** — searches opened, incumbents on watch or terminated, bucket targets
  raised or cut, consultant or CIO changes, drawdowns discussed, fee renegotiations, policy
  changes.
- **Manager side** — AUM trajectory (Form ADV), fund vehicles (ADV Schedule D 7.B.1),
  flows proxy (Form D), long books (13F), service providers, regulatory history, personnel
  moves as printed in filings.

**Geography.** Phase A: South Florida (Miami-Dade, Broward, Palm Beach, Monroe) and New York
City (five NYC funds, NYS Common, NYSTRS, MTA, NYC-area endowments/foundations/hospitals/
Taft-Hartley/corporate DB). Design for nationwide (every state and local plan via the
Public Plans Data, NASRA, IRS BMF and Form 5500 spines), then global (UK LGPS, Canadian
and Australian supers, Dutch/Nordic funds, sovereign annual reports).

**Allocator classes in scope.** Public pensions (state, county, municipal, police/fire,
teachers), corporate DB plans and Taft-Hartley plans (Form 5500), endowments (990 + annual
reports), private and community foundations (990-PF / 990), hospital systems (990),
insurers (statutory Schedule BA — public but distributed by NAIC for a fee, so MANUAL),
sovereigns (annual reports), OCIOs and funds of funds (Form ADV), family offices *with
public footprints only* (ADV / ERA / Form D / 13F).

### 1.1 THE ONE WALL (non-negotiable, enforced three ways)

The system **never contacts anyone** and **never collects contact details.**

| Layer | Enforcement |
|---|---|
| **Schema** | No model has an email, phone, street address, URL-to-a-person, social handle, home-location, DOB, or personal-ID field. `Officer` = `name_as_printed`, `title_as_printed`, `role_type`, `org_id`, `as_of`, `evidence_id`. Nothing else. Entities carry only `jurisdiction_city`, `jurisdiction_state`, `jurisdiction_country` (region attributes, never street/ZIP — see D-014). |
| **Extraction** | Every text payload passes a **scrubber** (regex + Presidio/GLiNER PII recognizers) *before* it reaches the LLM prompt, the vector store, or any table. Emails, phone numbers, street addresses, `linkedin.com/in/`, `x.com/`, `instagram.com/`, `facebook.com/` person URLs, and "Contact:" blocks are replaced with `[REDACTED-CONTACT]`. Source datasets that ship address columns (Form D RELATEDPERSONS, Form 5500 Schedule C provider addresses, IRS BMF street fields, Sunbiz officer addresses) are loaded with those **columns dropped at ingest**, never stored. |
| **Tests** | `tests/wall/` runs on every CI build and nightly against the live store: (a) regex + NER sweep over every text column and every vector-store payload must return **zero** hits; (b) a planted canary document (synthetic board packet with fake contact blocks) must yield zero stored contact tokens; (c) schema introspection fails if any column name matches `/(email|phone|mobile|fax|address|street|zip|linkedin|twitter|handle|dob|ssn)/i`; (d) the HTTP egress log must contain **zero** POST/PUT/PATCH to any non-allowlisted host and zero requests to any `mailto:`/form endpoints. Any failure blocks deploy and pages the watchdog. |
| **Outbound** | There is no outbound capability. The crawler tier's proxy allows GET/HEAD only. No SMTP library, no form-submit helper, no LinkedIn client exists in the codebase; a dependency-allowlist test fails the build if one appears. The system reads; humans talk. |

**Note on raw documents (decision needed — see §10 Q3).** Public PDFs often print a
consultant's phone number on a cover page. Recommended posture (D-013): raw bytes are kept
*immutable and encrypted* in the document lake as evidence, but **every derived layer**
(text, tables, embeddings, extractions, briefs) is scrubbed, and the LLM/vision models never
see unscrubbed text. Page images sent to the vision model are the one leak path; the vision
model's *output* is scrubbed before storage. The alternative (burn-in redaction of raw PDFs
at ingest) breaks evidence integrity; a CCO should choose.

### 1.2 Human touchpoints (exactly three)

1. **Registry approval, once per region.** The discovery agent emits
   `registry/<region>.yaml` (allocators + source allowlist + per-source ToS verdict). A human
   approves it. Nothing is crawled before approval.
2. **Merge-review queue.** Entity matches in the ambiguous band only (fuzzy score 0.80–0.95
   without an ID match). Auto-merge on shared ID or fuzzy ≥ 0.95; auto-new below 0.80.
3. **Signal-review queue.** Nothing exports to CRM or pitch material until a human marks the
   signal `approved`. Export jobs refuse unapproved rows.

Everything else — discovery, crawling, parsing, extraction, resolution, graph building,
diffing, briefing — is agentic and unattended.

### 1.3 Hard rejects

Never ingest, link, or scrape: Preqin database, eVestment (Nasdaq), With Intelligence
products (HFM, Hedge Fund Alert, Pageant), HFR, BarclayHedge, PitchBook, FundFire, P&I
data products, Dakota Marketplace, any licensed database, any login-walled page, any
page behind a CAPTCHA, any site whose robots.txt or ToS forbids automation (those become
MANUAL-ONLY, meaning a human may read them in a browser and type a fact in by hand with a
citation). No workarounds, ever: no UA spoofing against bot walls, no residential proxies,
no headless-browser fingerprint evasion. The auto-mode classifier in this very session
refused a headless-Chrome check of bot-walled hosts; the production system inherits that
posture by design. Hosts on the do-not-crawl list from verification: resourcecenters.com,
BoardDocs, PrimeGov tenants, FundFire, LinkedIn, EMMA, BrokerCheck, NFA BASIC, Delaware ICIS,
all South Florida and NY court portals, Sunbiz web search (bulk SFTP is used instead).

---

## 2. Source inventory (verified, tabular)

**How this was verified (2026-11-05/06).** One scripted sweep of ~120 URLs (status, redirects,
bot-wall markers, robots.txt per host; evidence in `verification/`), then eight research agents
that read each page, quoted terms of use verbatim, and marked anything they could not load as
UNVERIFIED. No logins, no form submissions, no CAPTCHA handling, no User-Agent spoofing. One
headless-browser check of bot-walled hosts was refused by the Claude Code safety classifier as
bot-check circumvention; we did not retry it, and the production design inherits that rule.

**Legend.** Access: **BULK** file download · **API** · **RSS** · **CRAWL** (GET-only, robots-honoring
fetch with httpx or Playwright for JS-rendered pages) · **MANUAL** (a human reads it in a browser
and may type in a dated, cited fact) · **DROP** (not worth it or not permitted). Parse 1 = CSV/XML,
5 = scanned mixed-layout PDF. "Decl. UA" = sec.gov and dol.gov return 403 unless the request carries
a declared User-Agent of the form `AppName admin@domain` (VERIFIED: 403 with a browser UA, 200 declared).
**Status:** VERIFIED = page or file loaded and read this session · PARTIAL = exists, some detail
unconfirmed · UNVERIFIED = could not load (reason given) · NOT FOUND = does not exist, dropped.

### 2.1 Federal — SEC

| # | Source | URL | Access | Rate / ToS | Robots | Cadence | Shape | Yields | Parse | Legal note | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|
| S1 | EDGAR access policy | https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data | — | "Current max request rate: 10 requests/second"; declared UA mandatory; "The SEC does not allow botnets or automated tools to crawl the site" outside policy | `*` allows /Archives; disallows /cgi-bin, /search | full/quarterly indexes rebuilt weekly (Sat) | policy | governs S2–S10 | — | US-gov works, public domain (17 USC 105) | VERIFIED |
| S2 | EDGAR daily + full index | https://www.sec.gov/Archives/edgar/daily-index/ · /full-index/ · per-dir `index.json` | BULK | S1 | allowed | daily / weekly rebuild | idx (form, company, CIK, date, path) | every filing pointer | 1 | public domain | VERIFIED |
| S3 | EDGAR full-text search | https://efts.sec.gov/LATEST/search-index | API | S1; no key | (no robots file) | near real-time; **2001→** (2000 returns 0) | JSON; params `q` (phrases, OR, NOT, `*`, NEAR), `forms`, `dateRange`, `startdt`/`enddt`, `ciks`, `entityName`, `from` (page size 100) | keyword discovery of RFP mentions, "placement agent", plan names in any filing; hit fields `adsh, ciks, display_names, file_date, form, biz_states…` | 1 | public domain | VERIFIED (live probes) |
| S4 | data.sec.gov APIs + Atom | https://data.sec.gov/submissions/CIK##########.json · `/api/xbrl/*` · `submissions.zip` nightly · browse-edgar `output=atom` | API/RSS | S1; no key; no CORS | allowed | real-time (<1 s); bulk zips nightly ~3am ET | JSON / Atom | per-filer history + former names (alias source); live Form D stream | 1 | public domain | VERIFIED |
| S5 | Form D data sets | https://www.sec.gov/data-research/sec-markets-data/form-d-data-sets · readme https://www.sec.gov/files/Form_D.pdf | BULK | S1 | allowed | **quarterly, 2008Q1–2026Q3** | zip of UTF-8 TSV: FORMDSUBMISSION, ISSUERS, OFFERING, RECIPIENTS, RELATEDPERSONS, SIGNATURES | fund vehicles (INVESTMENTFUNDTYPE = Hedge/PE/VC), TOTALOFFERINGAMOUNT / TOTALAMOUNTSOLD / TOTALNUMBERALREADYINVESTED (flow proxy), placement agents (RECIPIENTNAME + CRD), related-person names + relationship | 1 | public domain. **DROP at load:** STREET1/2, CITY-level beyond city, ZIPCODE in ISSUERS, RECIPIENTS, RELATEDPERSONS; ISSUERPHONENUMBER | VERIFIED (page + readme) |
| S6 | Form 13F data sets | https://www.sec.gov/data-research/sec-markets-data/form-13f-data-sets · readme https://www.sec.gov/files/form_13f_readme.pdf | BULK | S1 | allowed | quarterly, **2013Q2→**; since 2024 named by 3-month window | zip of TSV: SUBMISSION, COVERPAGE, OTHERMANAGER(2), SIGNATURE, SUMMARYPAGE, INFOTABLE | long books (CUSIP, FIGI, VALUE in whole $ since 2023), sub-manager links (CIK/CRD) | 1 | public domain. **DROP:** COVERPAGE street/zip, SIGNATURE PHONE | VERIFIED |
| S7 | Form ADV historical CSVs + brochures | https://www.sec.gov/foia-services/frequently-requested-documents/form-adv-data | BULK | S1 | allowed | historical **2000-10-19 → 2024-12-31**; brochures monthly zips 2007→Dec 2024 | CSV zips (IA_ADV_Base_A/B, IA_Schedule_D_7B1 + 7B1A23 auditor, A24 prime broker, A25 custodian, A26 administrator, A28 marketer; ERA_*; DRP_*) + Part 2 PDFs with mapping CSV | private-fund list w/ gross asset value, # beneficial owners, min investment, master/feeder; **service-provider edges**; brochure text | 1 / 4 | public domain. Schedule A/B owner tables and DRP are person-level: load names+titles only | VERIFIED (zip directories read) |
| S8 | IA monthly info reports | https://www.sec.gov/data-research/sec-markets-data/information-about-registered-investment-advisers-exempt-reporting-advisers | BULK | S1 | allowed | **monthly**, Jun 2006 → Nov 2026 | zip/XLSX, RIA and ERA files | Items 1–11 only: identity, RAUM, **Item 5.D client-type mix**, affiliations. **No Schedule D** | 1 | public domain | VERIFIED |
| S9 | IAPD daily compilation feeds | https://reports.adviserinfo.sec.gov/reports/CompilationReports/CompilationReports.manifest.json | BULK | S1 | IAPD robots disallows `/firm/brochure/`, `/IAPD/content/viewform/adv*` — never crawled | **daily** (files dated 2026-11-05) | XML.gz: IA_FIRM_SEC (~79 MB), IA_FIRM_STATE (~70 MB), IA_INDVL (not used) | the 2025→ path for ADV data; whether it carries full Schedule D 7.B.1 is **PENDING** (open one file) | 2 | public domain | VERIFIED (manifest); content PENDING |
| S10 | SEC enforcement RSS | https://www.sec.gov/enforcement-litigation/litigation-releases/rss · …/administrative-proceedings/rss | RSS | S1 | allowed | daily | items: respondents, release no., link/PDF | manager regulatory history | 2 | public domain | VERIFIED |
| S11 | Form N-PX | — | — | — | — | — | — | — | — | — | **NOT FOUND as a data set** (6 URL variants 404). Per-filing XML exists via S4 since mid-2024. Low value; dropped from Phase A |
| S12 | Private Fund Statistics (Form PF aggregates) | https://www.sec.gov/data-research/investment-management-data | BULK | S1 | allowed | quarterly, lagged | PDF + supporting XLSX (2023Q4→) | industry context only, no entities | 2 | public domain | VERIFIED |
| S13 | OFR Hedge Fund Monitor | https://www.financialresearch.gov/hedge-fund-monitor/ · API https://data.financialresearch.gov/hf/v1 | API | no token, no stated limit | allowed | monthly/quarterly | JSON (`/series/timeseries`, `/series/full`…) | aggregate size, leverage, liquidity by strategy | 1 | public domain | VERIFIED |

### 2.2 Federal — IRS, DOL, identifiers, cross-state pension data

| # | Source | URL | Access | Rate / ToS | Robots | Cadence | Shape | Yields | Parse | Legal note | Status |
|---|---|---|---|---|---|---|---|---|---|---|---|
| F1 | IRS 990-series e-file XML | https://www.irs.gov/charities-non-profits/form-990-series-downloads (files at `apps.irs.gov/pub/epostcard/990/xml/YYYY/…`) | BULK | none stated | allowed | monthly batch zips; **2019–2026** only | XML + `index_YYYY.csv` (RETURN_ID, EIN, TAX_PERIOD, RETURN_TYPE…); includes **990, 990EZ, 990PF, 990T** | endowment/foundation/hospital spine; **Schedule D Part VII** and 990-PF investment schedules → allocator↔fund edges; Part VII officer names+titles | 2 | IRC §6104 public; public domain | VERIFIED |
| F2 | IRS 990 on AWS Open Data | https://registry.opendata.aws/irs990/ | BULK | license "None" | n/a | **deprecated, frozen Dec 2021** | XML | back-history 2013–2021 only | 2 | — | VERIFIED (deprecated) |
| F3 | IRS EO Business Master File | https://www.irs.gov/charities-non-profits/exempt-organizations-business-master-file-extract-eo-bmf · dictionary https://www.irs.gov/pub/foia/ig/tege/eo-info.pdf | BULK | none | allowed | **monthly** (posted 2026-10-13; 1,975,975 rows) | CSV per state | **registry seed**: EIN, NAME, CITY, STATE, NTEE_CD, FOUNDATION (02/03/04 = private foundation), ASSET_AMT, INCOME_AMT | 1 | public domain. **DROP:** STREET, ICO (in-care-of person), ZIP→ZIP3 or drop | VERIFIED |
| F4 | ProPublica Nonprofit Explorer API v2 | https://projects.propublica.org/nonprofits/api | API | Data Terms (https://projects.propublica.org/datastore/terms/): cite ProPublica when publishing; no redistribution of raw data in whole; PDF links rate-limited | robots disallows website search/download paths; **API path not disallowed** | live | JSON (search; org by EIN; filings with pdf_url + ~40–120 fields) | fast EIN lookup, 990 PDFs pre-2019 | 1 | **DROP:** org `address` | VERIFIED |
| F5 | GivingTuesday 990 Data Infrastructure | https://990data.givingtuesday.org/ | BULK (data lake on AWS) | data **ODbL 1.0 + DbCL**; site T&Cs forbid "systematic or automated data collection" of the *website*, and direct-marketing use | allowed | per IRS release | curated marts (Basic, Grants & Funds, Personnel…) | pre-parsed 990 fields | 1 | ODbL share-alike obligations if we redistribute a derived DB (we don't) | VERIFIED; years/format PENDING |
| F6 | DOL Form 5500 data sets | https://www.dol.gov/agencies/ebsa/about-ebsa/our-activities/public-disclosure/foia/form-5500-datasets (files on `www.askebsa.dol.gov/FOIA Files/YYYY/{Latest,All}/`) | BULK | decl. UA required | allowed | **monthly** refresh; form years **1999–2025** | zipped CSV + layout files; join key **ACK_ID** | corporate DB + Taft-Hartley spine: F_5500 (sponsor, EIN, plan no., participants); **SCH_C_PART1_ITEM2** (providers paid: name, EIN, service codes, direct comp); **SCH_D_PART1** (CCT/PSA/MTIA/103-12 interests w/ EOY $); **SCH_H** (asset totals by class) | 1 | ERISA §104(a). **DROP:** all sponsor/admin/provider address and phone columns | VERIFIED |
| F7 | EFAST2 Form 5500 search | https://www.efast.dol.gov/5500Search/ → https://5500search.dol.gov/ | **MANUAL** | Akamai 403 to a declared non-browser UA (one agent's WebFetch got 200: conflicting) | robots 403 | per filing | web UI; filing PDFs incl. Schedule of Assets attachments | fund-by-fund holdings for one plan at a time | 4 | public | PARTIAL — treat as browser-only; F6 covers the structured data |
| F8 | Public Plans Data (Boston College CRR) | API https://publicplansdata.org/api/ · docs https://publicplansdata.org/public-plans-database/api/ | API | **no key**; no license text found | allowed | quarterly (last update 2026-09-02) | `?q=QDataSet&dataset=…&format=json` (or csv), filters `filterppdid`, `filterfystart` | **national registry seed**: >250 plans, 2001–2025, 34 data sets, 1,106 variables incl. asset-class allocation. FL: FRS, Jacksonville ERS & P&F, **Miami F&P, Miami GESE**. NY: NYCERS, TRS NYC, NYC Police, NYC Fire, BERS, NYSTRS, NYSLRS ERS & P&F | 1 | academic open data; attribute | VERIFIED (live query) |
| F9 | Census Annual Survey of Public Pensions | https://www.census.gov/data/datasets/2025/econ/aspp/aspp-historical-datasets.html · unit file `www2.census.gov/programs-surveys/aspp/datasets/2025/aspp-historical-datasets/ASPP_Unit_File_2025.csv` | BULK (+ API) | public; API needs a free key and is aggregate-only | Crawl-delay 15 | annual | CSV: UNIT_NAME, PID6, STATE, UNIT_TYPE, ITEM_CODE, ITEM_VALUE | **plan-level long tail** of small systems (assets, contributions, holdings by class) | 1 | public domain | VERIFIED |
| F10 | GLEIF LEI Golden Copy | https://www.gleif.org/en/lei-data/gleif-golden-copy/download-the-golden-copy | BULK | GLEIF open data (license wording PENDING quote) | n/a | 3×/day | CSV/XML/JSON; Level 1 + Level 2 relationships | ID ladder rung; fund→manager parent links | 1 | **DROP:** address blocks | VERIFIED (page) |
| F11 | NASRA | https://www.nasra.org/ (state pages e.g. /fl) | CRAWL | cookie policy only | allowed | — | HTML | thin state-system links | 2 | — | VERIFIED (low value) |
| F12 | Fed Z.1 | https://www.federalreserve.gov/releases/z1/ | BULK | — | — | quarterly | CSV | sector context | 1 | public domain | VERIFIED |

### 2.3 Florida — state level

| # | Source | URL | Access | Rate / ToS | Robots | Cadence | Yields | Parse | Legal note | Status |
|---|---|---|---|---|---|---|---|---|---|---|
| FL1 | Florida SBA (FRS Pension Plan) | https://www.sbafla.com/ | **MANUAL** | **Akamai 403 to every non-browser request**, incl. robots.txt and PDFs | 403 | Trustees ≈ quarterly; IAC quarterly; monthly trustee performance report; **quarterly "New Investment" report** (asset class, manager, account, commitment, consultant); quarterly Alternative Asset Status & Performance report (fund-level IRR, multiple); Annual Investment Report; audited FS | largest FL allocator; dated commitments = signals | 3 | Fla. Stat. ch. 119 | UNVERIFIED by load; doc URLs confirmed via search (`/media/<id>/<slug>.pdf`). A human downloads quarterly; see §10 |
| FL2 | FL DMS Local Retirement Section + fact sheets | https://www.dms.myflorida.com/workforce_operations/retirement/local_retirement_plans/local_retirement_section | CRAWL (Playwright render) | state site | allows `/`; disallows `/media/`, `/search` | annual | **complete list of ~490 FL local plans**; per-plan Actuarial Summary Fact Sheets are static PDFs at `https://employer.frs.fl.gov/forms/<PLANCODE>.pdf` (e.g. `MIRAMARPolice.pdf`) | 3 | public record | PARTIAL — page body is client-rendered (Next.js); needs a JS render, which is not a bot wall |
| FL3 | F.S. 112.664 | http://www.leg.state.fl.us/statutes/index.cfm?App_mode=Display_Statute&URL=0100-0199/0112/Sections/0112.664.html | reference | flsenate.gov returned 429 | — | — | **every FL local plan with a website must post** its latest financial statement, actuarial valuation, a link to its DMS fact sheet, and a 5-year table of assumed vs actual return and % in cash/equity/bonds/alternatives → crawler expectations and a free allocation series | — | — | VERIFIED |
| FL4 | Florida Auditor General | https://flauditor.gov/pages/Reports.aspx · e-filed local audits https://flauditor.gov/pages/efile_reports.html | CRAWL | — | — | annual | sponsor audits with pension notes; predictable `mun_efile pages/<entity>.htm` → PDFs | 3 | public record | VERIFIED |
| FL5 | FL CFO LOGERx | https://logerx.myfloridacfo.gov/LogerX/PublicReportsMenu | CRAWL | — | — | annual | local government AFRs (XBRL) | 2 | public record | PARTIAL |
| FL6 | Sunbiz corporate data files | https://dos.fl.gov/sunbiz/other-services/data-downloads/ · layout https://dos.sunbiz.org/data-definitions/cor.html | BULK (SFTP) | free, "as is"; download pages 403 to non-browser clients; SFTP credentials are published on the DOS page | 403 | daily + quarterly | fixed-width: doc number, name, status, filing type, file date, FEI, registered-agent name, **up to 6 officers with title code + name** → FL manager entity formation (ENTITY_FORMED signals) | 2 | public record. **DROP:** principal, mailing, agent and officer addresses | PARTIAL — a human copies the SFTP details once (§10) |
| FL7 | Sunbiz name search | https://search.sunbiz.org/Inquiry/CorporationSearch/ByName | DROP for automation | FL6 is the sanctioned bulk path | no robots | — | — | — | — | VERIFIED |
| FL8 | MyFloridaMarketPlace Vendor Information Portal | https://vendor.myfloridamarketplace.com/search/bids | PENDING | Angular SPA | none | continuous | state solicitations | 2 | public | UNVERIFIED (needs a logged-out JS render) |
| FL9 | FMPTF (Florida League of Cities) | https://retirement.flcities.com/reporting/ | CRAWL | — | Crawl-delay 10 | annual ACFR (2015–2023), quarterly portfolio sheets | pooled trust for ~215 small plans (2023 ACFR) | 3 | public record | VERIFIED |
| FL10 | FPPTA plan data center | https://fppta1984.fppta.org/pension-plan-data-center/ | **DROP** | "exclusive to FPPTA members only" | Crawl-delay 30 | — | — | — | — | VERIFIED |

### 2.4 South Florida plans (registry seed)

Recurring hosting patterns decide crawler templates. **T-WP** self-hosted WordPress (PDFs under
`/wp-content/uploads/`) · **T-CP** CivicPlus (`/DocumentCenter/View/<id>`, `/AgendaCenter/`) ·
**T-LEG** Legistar · **T-GOV** Granicus OpenCities/govaccess · **T-AIRO** "Airo" app-builder sites,
JS-rendered, need Playwright · **T-CITY** city WordPress/Drupal.

| Plan | URL | Template | What is posted (verified) | Status |
|---|---|---|---|---|
| Miami Firefighters' & Police Officers' Retirement Trust (FIPO) | https://www.miamifipo.org/ | custom .NET | quarterly performance reports Dec 2021→Dec 2025 (Meketa named in 2025 files); audits 2012–2025; actuarial valuations; no agendas/minutes | VERIFIED |
| Miami General Employees' & Sanitation (GESE) | https://gese.org/ | T-WP | board meetings by year 2015–2026 (agendas + minutes); investment performance under /publications/; ACFR | VERIFIED |
| Miami Beach Fire & Police Pension | https://www.miamibeachfl.gov/city-hall/fire-police-pension/ | T-CITY | agendas, minutes 2024–2026, quarterly investment reports 2023→6/30/2026 (AndCo then Mariner), IPS eff. 2026-01-01, actuarial, FS | VERIFIED |
| Miami Beach Employees' Retirement Plan | https://www.miamibeachfl.gov/city-hall/mberp/ | T-CITY | agendas 2024–2026, quarterly investment report (Segal), IPS, actuarial, FS, 112.664 page | VERIFIED |
| Coral Gables Retirement System | https://www.coralgables.com/department/coral-gables-retirement-system | T-CITY + T-LEG | agendas/minutes on coralgables.legistar.com; quarterly reviews; actuarial; audits | VERIFIED |
| Hialeah Employees' Retirement System | http://www.hialeahfl.gov/288/Employees-Retirement-System | T-CP | AgendaCenter agendas/minutes 2018–2026; actuarial; FS | VERIFIED |
| Fort Lauderdale Police & Fire | https://ftlaudpfpension.com/ | T-WP | agendas 2007–2026, minutes 2019–2026, ~55 quarterly reviews 2012→3/31/2026, FS, actuarial, 112.664 | VERIFIED (richest archive) |
| Hollywood Employees' Retirement Fund | https://hollywoodpension.com/ | T-WP | minutes, quarterly + monthly flash performance, actuarial | VERIFIED (index) |
| Hollywood Police Officers' Retirement System | https://www.hollywoodpolicepensionfund.com/ | T-AIRO | board meetings, investments sections (client-rendered) | PARTIAL |
| Hollywood Firefighters' Pension | https://www.hollywoodfl.org/164/Firefighters-Pension-Board | T-CP | sparse: 112.664, summaries | VERIFIED (thin) |
| Pompano Beach Police & Fire | https://www.pbpfrs.org/ | T-WP | agendas, minutes, performance reports (latest 6/30/2026), **IPS**, actuarial, audits, **RFP page** | VERIFIED |
| Pembroke Pines Fire & Police | https://www.pinespension.com/ | T-WP | agendas, minutes, performance, 112.664, audit, actuarial | VERIFIED |
| Davie Firefighters | https://fl-davie.civicplus.com/1274/Firefighters-Retirement-Trust-Pension-Bo | T-CP | agendas/minutes 2019–2026, 112.664, actuarial | VERIFIED |
| Coral Springs Police / Fire / General | https://www.coralsprings.gov/Government/Departments/Human-Resources/Pension-Plans | T-GOV | FS, actuarial, return/allocation PDF | VERIFIED (root 403; subpages load) |
| Miramar Police / Fire / General / Management | https://www.miramarfl.gov/Departments/Management-Budget/State-Required-Pension-Disclosures | T-GOV | IPS (Police, 2025-12-11), annual investment report, actuarial, 112.664, audited FS | VERIFIED |
| Boca Raton Police & Fire | https://www.myboca.us/337/Police-Firefighters-Retirement-System | T-CP | IPS, 112.664, actuarial, FS | VERIFIED |
| Boca Raton General Employees | https://www.myboca.us/294/General-Employees-Pension-Plan | T-CP | IPS, actuarial, FS | VERIFIED |
| West Palm Beach Police Pension | https://www.wpbppf.com/ | T-AIRO | board meetings, investments, disclosures (client-rendered) | PARTIAL |
| Town of Palm Beach Retirement System | https://www.townofpalmbeach.com/265/RetirementBenefits | T-CP | DB quarterly reports 3/2021→6/2026, actuarial, audits, 112.664 | VERIFIED |
| Jupiter Police | https://jupiter.fl.us/294/Police-Pension-Board | T-CP | agendas; minutes and financials link out to Resource Centers (blocked, below) | PARTIAL |
| Key West Police & Fire · Key West General | https://cityofkeywest-fl.gov/825/… · /555/… | T-CP + T-LEG | agendas, minutes, IPS (GE), actuarial | VERIFIED |
| North Miami Beach Police & Fire · Riviera Beach | citynmb.com · rivierabch.com | T-CP | thin or stale | VERIFIED (low value) |
| **Blocked to non-browser clients → MANUAL** | Plantation, Delray Beach, Sunrise, West Palm Beach Fire & GE, Fort Lauderdale GERS (Akamai on Granicus city sites); Lake Worth Beach (Cloudflare block) | — | — | UNVERIFIED |
| **Not located yet** | Miami Elected Officers, North Miami, NMB General, Pompano General, Davie Police, Hallandale (3 plans), Palm Beach Gardens, Boynton Beach (3 plans) | — | — | PENDING |
| **The Resource Centers (TPA)** | https://www.resourcecenters.com/ | — | hosts many Palm Beach/Broward plan documents | **DROP / do-not-crawl**: Cloudflare managed challenge on every page, and robots.txt says `Disallow: /` to ClaudeBot, anthropic-ai, GPTBot and others. Get those plans via the sponsoring city's own pages |

**Other South Florida public bodies with investment content:** Memorial Healthcare System /
South Broward Hospital District (board agendas list monthly pension investment flash reports;
mhs.net) · Jackson Health / Public Health Trust (Miami-Dade Legistar) · Miami-Dade County
(https://miamidade.legistar.com/, **Legistar API works without a token**).

**Non-pension South Florida allocators (990 / 990-PF via F1/F4; EINs confirmed on ProPublica):**
University of Miami (audited FS on controller.miami.edu) · FAU Foundation · Nova Southeastern ·
Baptist Health South Florida + Foundation · Knight Foundation (990-PF posted on its site) · The
Miami Foundation · Community Foundation of Broward · Community Foundation for Palm Beach & Martin ·
Greater Miami Jewish Federation (990s + audits posted) · Jewish Federation of Palm Beach County ·
Quantum Foundation · Health Foundation of South Florida · Batchelor Foundation · Braman, Arison,
Frost, Lennar and Ryder family/corporate foundations. PENDING: FIU Foundation, Nicklaus Children's,
Pérez family, Carnival.

### 2.5 New York — state and city

| # | Source | URL | Access | Notes (verified) | Cadence | Status |
|---|---|---|---|---|---|---|
| NY1 | NYC Comptroller, Bureau of Asset Management | https://comptroller.nyc.gov/services/financial-matters/pension/overview/ · library https://comptroller.nyc.gov/bamlibrary/ · per-fund archive `/pension/<fund>/` (fire, police, …) | CRAWL | **No "Common Investment Meeting" page exists any more.** Per-system public Investment Meeting agenda packages, Public Agenda Notices, Monthly Performance Reviews for Fire, Police, BERS, NYCERS, TRS. Predictable names: `/wp-content/uploads/documents/Monthly-Performance-Review-Material_MM-YYYY-<FUND>.pdf`, `<MM.DD.YYYY>-NYC-<System>-Investment-Meeting-Agenda-Package-Public.pdf`. AUM/performance dashboards are JS-only. robots: WordPress paths blocked; the Crawl-delay 10 applies only to named social bots, but we use 10 s anyway | monthly | VERIFIED |
| NY2 | Comptroller manager-search notices | `https://comptroller.nyc.gov/rfp/<slug>` | CRAWL (metadata only) | title, date, PIN, closing date are public; the notice PDF sits behind a registration form with reCAPTCHA, **which we never submit**. Same notices appear in NY10 | ad hoc | VERIFIED |
| NY3 | NYCERS | https://www.nycers.org/about/board-of-trustees/board-meeting-agendas · ACFR page | CRAWL | public agendas 2022→Oct 2026, investment meeting dates, webcasts, ACFR. No minutes page | monthly | VERIFIED |
| NY4 | TRS NYC | https://www.trsnyc.org/ | via NY1 | public site redirects to a member login; its investment packages are on the Comptroller library | — | VERIFIED (redirect) |
| NY5 | NYC Police Pension Fund | https://www.nyc.gov/content/nycppf/pages/meeting-webcasts-and-materials | via NY1 | nyc.gov asset PDFs are Akamai 403 to non-browser clients; packages are on the Comptroller library | monthly | PARTIAL |
| NY6 | NYC Fire Pension Fund | https://www.nycfirepension.org/ | via NY1 | official site, events calendar; ACFRs on NYC Records (a860-gpp.nyc.gov) | monthly | VERIFIED |
| NY7 | BERS | https://www.bers.nyc.gov/assets/bers/downloads/pdf/meetings/ (`public_packet_MM.DD.YYYY.pdf`, `summary_minutes_MM.DD.YYYY.pdf`) · RFPs under `/requests-for-proposals/` | CRAWL | public packets, summary minutes, RFPs (e.g. investment consulting 2024-003) | monthly | VERIFIED (files) |
| NY8 | NYS Common Retirement Fund | **Monthly Transaction Reports** https://www.osc.ny.gov/common-retirement-fund/resources/financial-reporting-and-asset-allocation (235 PDFs Jan 2007–Aug 2026; 2020→ `/files/common-retirement-fund/pdf/<month>-<yyyy>.pdf`) · Asset Listing https://www.osc.ny.gov/files/retirement/resources/pdf/asset-listing-2026.pdf · Emerging Manager page | CRAWL | **richest structured NY feed:** each entry names fund, manager/GP, commitment amount, asset class, new-vs-existing relationship, close date, placement-agent statement, plus manager additions and terminations. Asset Listing (as of 3/31) names every holding incl. PE, fund-of-funds, opportunistic/ARS, credit. Procurement documents are form-gated (skip) | monthly / annual | VERIFIED |
| NY9 | NYSTRS | https://www.nystrs.org/ | **MANUAL** | HTML pages return a Cloudflare "you have been blocked" page; individual PDFs (ACFR, board packets at `/getmedia/<guid>/…`) do load, but discovering their URLs requires the blocked pages. Rule (D-030): a human downloads them in a browser into the manual inbox; the system never requests nystrs.org itself | quarterly | PARTIAL |
| NY10 | NYC Open Data — City Record Online | https://data.cityofnewyork.us/resource/dg92-zbpx.json | API (Socrata) | **daily**, Public Domain. Fields: agency_name, type_of_notice_description, section_name ("Procurement"), short_title, pin, due_date, contract_amount, vendor_name, document_links. **DROP:** contact_name, phone, email, fax, event address fields | daily | VERIFIED |
| NY11 | NYS Open Data — Active Corporations | https://data.ny.gov/resource/n9v6-gdp6.json | API (Socrata) | monthly snapshot; dos_id, entity name, filing date, county, entity type, chairman (CEO) name, registered agent name. **DROP:** all address fields | monthly | VERIFIED |
| NY12 | NYS Contract Reporter | https://www.nyscr.ny.gov/Ads/Search | CRAWL | public listing without login (842 open ads on 2026-11-05), filters, PDF export of results | continuous | VERIFIED |
| NY13 | NYC PASSPort Public | https://a0333-passportpublic.nyc.gov/rfx.html | CRAWL (Playwright) | public "Browse Solicitations" (EPIN, name, agency, status, dates); the main passport host is login + robots `Disallow: /` and is not used | continuous | VERIFIED |
| NY14 | MTA | https://www.mta.info/transparency/board-and-committee-meetings/<month>-<year> · pension FS https://www.mta.info/transparency/financial-information/financial-and-budget-statements | CRAWL | Finance Committee books, MTA DB Pension Plan FS 2018–2025; docs at `/document/<id>` | monthly | VERIFIED |
| NY15 | NYC Council Legistar | https://legistar.council.nyc.gov/ | CRAWL / API | API needs a token requested via a form at council.nyc.gov (name + email). That is a human decision (§10) | — | VERIFIED |
| NY16 | NYS DOS entity search | https://apps.dos.ny.gov/publicInquiry/ | DROP | TLS reset to non-browser clients; NY11 covers it | — | UNVERIFIED |

**NYC-area non-pension allocators — best public mechanism per class (verified 2026-11-06):**

| Class | Best mechanism | Self-posted examples (verified) | 990/990-PF only (via F1/F4) |
|---|---|---|---|
| Universities | audited FS investment footnote (NAV-practical-expedient table splits hedge funds, PE, real assets) + 990 Schedule D | Columbia (endowment report + FS), Cornell investment office (quarterly reports, policy), Fordham, Yeshiva, Barnard, New School, CUNY Research Foundation, **Cooper Union (board minutes 2014–2026 + IPS 2025)** | NYU, Pratt, Juilliard, Rockefeller University |
| Foundations | 990-PF balance sheet + investment schedules; audited FS where posted | Ford, Rockefeller, Mellon, Wallace, Rockefeller Brothers, Surdna, Robin Hood, NY Community Trust, UJA-Federation, Helmsley, Starr, Altman, Simons, Carnegie | Bloomberg Philanthropies, Open Society, JPB (Freedom Together), Robertson, Tiger, Sloan (site blocked) |
| Hospitals | bond continuing-disclosure FS (EMMA, MANUAL) + 990 Schedule D | Northwell (audited + quarterly FS), Hackensack Meridian (audited, quarterly, 990), MSK (annual report financials), NYU Langone (annual report) | Mount Sinai, NewYork-Presbyterian, HSS, Montefiore |
| Cultural | 990 Schedule D | Whitney (audited FS 2015–2026) | Met, MoMA, Lincoln Center, Carnegie Hall, AMNH, Guggenheim, NYPL (JS page) |
| Taft-Hartley | F6 Schedules C/D/H + Annual Funding Notice | NYC District Council of Carpenters (posts full Form 5500s + AFN), NYSNA (annual report + AFN), JIB / IBEW Local 3 (AFN) | 1199SEIU, 32BJ, Hotel Trades, Local 15, Mason Tenders, Local 282, Local 807, UFCW 1500 |
| Insurers | statutory Schedule BA (NAIC copies are paid → MANUAL) or self-posted statements; 10-K aggregates | **Guardian Life posts full NAIC statutory statements free**; Mutual of America annual report | MetLife, AIG, Equitable, Prudential: 10-K "other invested assets" aggregate only (EDGAR) |
| OCIO / fund of funds | Form ADV Item 5.D + Schedule D 7.B.1 (S7/S9) | Cambridge Associates (CRD 104942), Hirtle Callaghan (105403), Blackstone Alternative Asset Mgmt (107580), Aetos (116761), Morgan Stanley AIP (117050), Lighthouse (136591) | — |

### 2.6 Regulators, courts, registries (manager side)

| # | Source | URL | Access | Terms (quoted this session) | Status |
|---|---|---|---|---|---|
| R1 | NFA BASIC | https://www.nfa.futures.org/BASICNet/ | **MANUAL** | robots `User-agent: * Disallow: /basicnet/`; "You may not copy or display for redistribution to third-parties for commercial purposes any portion of the content without the prior written permission of NFA." No public bulk; directories password-protected | VERIFIED |
| R2 | FINRA BrokerCheck | https://brokercheck.finra.org/ | **MANUAL** | ToU §6(g) bans bulk copying and "data mining, scraping or harvesting tools (including robots)"; §6(p) bans use with "any machine learning… or other artificial intelligence" program; §5 carve-out only for "investor protection, academic, compliance or regulatory purposes"; §7 other uses need written FINRA permission. robots.txt is open, but the ToU controls | VERIFIED |
| R3 | CFTC enforcement | https://www.cftc.gov/LawRegulation/EnforcementActions/index.htm · RSS https://www.cftc.gov/RSS/RSSENF/rssenf.xml | CRAWL + RSS | "Government information at the CFTC website is in the public domain." robots allows `*` but blocks named AI-training bots, so we use a plain declared UA. The RSS index says feeds were "temporarily disabled" as of Oct 2026, yet the feed served items on 2026-11-03: build a page-polling fallback | VERIFIED |
| R4 | CourtListener REST v4 | https://wiki.free.law/c/courtlistener/help/api/rest/v4/overview · terms https://wiki.free.law/c/terms/courtlistener/courtlistenercom-terms-of-service-and-policies | API (free token) | default authenticated limits "5 requests per minute, 50 requests per hour, and 125 requests per day"; no credential pooling or multiple accounts; commercial agreements available. RECAP docket entries and parties are API-only. Use alert webhooks rather than polling | VERIFIED |
| R5 | CourtListener bulk | https://com-courtlistener-storage.s3-us-west-2.amazonaws.com/list.html?prefix=bulk-data/ | BULK | "free of known copyright restrictions"; quarterly snapshots (dockets, opinions, courts, citations, judges' financial disclosures); no docket entries/parties | VERIFIED |
| R6 | Miami-Dade · Broward · Palm Beach clerks | www2.miamidadeclerk.gov/ocs/ · browardclerk.org/Web2/ · appsgp.mypalmbeachclerk.com/eCaseView/ | **MANUAL** | reCAPTCHA v3 + Cloudflare Turnstile (Miami-Dade); Turnstile + "not intended or permitted to be used for commercial resale" + paid Commercial Data Service (Broward); reCAPTCHA + MFA (Palm Beach) | VERIFIED |
| R7 | NY WebCivil / NYSCEF | iapps.courts.state.ny.us | **MANUAL** | Cloudflare challenge on every path; official terms (read via search summary) prohibit bots | PARTIAL |
| R8 | Delaware entity search | https://icis.corp.delaware.gov/ecorp/entitysearch/NameSearch.aspx | **MANUAL** | "The Division of Corporations strictly prohibits mining data." "Use of automated tools in any form may result in the suspension of your access" | VERIFIED |
| R9 | Cayman CIMA entity search | https://www.cima.ky/search-entities-cima | **MANUAL (light)** | results need a form submit; robots blocks named AI bots | VERIFIED |
| R10 | MSRB EMMA | https://emma.msrb.org/ | **MANUAL** | ToU bans "data mining, crawling, 'scraping', robot or similar automated" access and OCR of imaged content; robots `Disallow: /*.pdf$`; continuing-disclosure feed is a $45,000/yr subscription. Get ACFRs from issuers' own sites | VERIFIED |
| R11 | LinkedIn (company pages) | — | **DROP** | robots: "The use of robots or other automated means to access LinkedIn without the express permission of LinkedIn is strictly prohibited"; User Agreement §8.2 bans scraping and using its data via third parties | VERIFIED |

### 2.7 Procurement aggregators and agenda platforms

| Source | Verdict | Basis |
|---|---|---|
| Legistar Web API (https://webapi.legistar.com/Help) | **API** | public REST `v1/{client}/Events`, `/EventItems`, `/Matters`, `/Attachments`; token is per client: **Miami-Dade 200 without token**, NYC "Token is required" |
| NovusAgenda (e.g. Miami Beach) | CRAWL | public search pages, no feed, no robots file |
| CivicPlus / CivicClerk | CRAWL | robots allows; CivicClerk OData API unconfirmed (404) |
| Granicus / IQM2 | CRAWL | Crawl-delay 2 |
| eSCRIBE · Municode Meetings | CRAWL | permissive robots (Municode Crawl-delay 15) |
| **PrimeGov** | **MANUAL** | robots `User-agent: * Disallow: /`. A public JSON API exists, but a blanket disallow covers every path, so we do not use it (D-031) |
| **BoardDocs** | **DROP** | robots `Disallow: /`, Crawl-delay 1000 |
| BidNet Direct | CRAWL (titles only), PENDING T&Cs | solicitation titles and dates visible logged-out; robots Crawl-delay 5; T&Cs body is JS-rendered and unread |
| DemandStar | **MANUAL** | terms bar "any robot, spider, data scraping, crawler or other extraction tool" |

### 2.8 Press, conferences, podcasts, consultants

| # | Source | Verdict | Basis | What we keep |
|---|---|---|---|---|
| P1 | Pensions & Investments | MANUAL | 403 bot wall on everything | human reads; enters dated fact + URL |
| P2 | Chief Investment Officer (ai-cio.com) | RSS | summary feeds `/news/feed/`, category feeds (manager-selection, newsmakers); ISS STOXX ToU: "You may not use any system, device or software to extract data from the website" | headline, link, date |
| P3 | Institutional Investor | MANUAL | ToS bans "harvest, scrape… archive, store" | — |
| P4 | FundFire | DROP | login wall, robots `Disallow: /` | — |
| P5 | Hedgeweek | RSS | full-text feed `/feed/`; ToS forbids caching site pages | headline, link, date, own ≤300-char summary; body discarded |
| P6 | Opalesque | RSS | Crawl-delay 90; "Any diffusion… expressly prohibited" | headline, link |
| P7 | Alternatives Watch | MANUAL | paywalled bodies, no feed | headlines read by a human |
| P8 | Top1000Funds | RSS | summary feed; robots blocks named AI crawlers | headline, link |
| P9 | Hedge Fund Alert / With Intelligence | **HARD-REJECT** | paid product | — |
| P10 | Preqin | **HARD-REJECT** (database) / MANUAL (free blog) | ToS bans automated access | — |
| P11 | Markets Group · Opal · II events · SALT · FPPTA conference | MANUAL | 403 or form-gated agendas | — |
| P12 | iConnections | MANUAL | ToS §4.7(u) bans automated access, §4.7(v) bans using its lists | — |
| P13 | Capital Allocators | MANUAL | transcripts premium-only; ToU bans robots | — |
| C1 | NEPC | RSS (10 s) | full-text blog feed; thought leadership only | — |
| C2 | Callan · Meketa · RVK · Verus/Cerity · Wilshire · Segal Marco · Aon · WTW | MANUAL | robots or terms bar automation, or gates; no client data | — |
| C3 | Mariner Institutional (acquired AndCo, announced 2024-02-01; Southeastern Advisory, announced 2025-08-21) | MANUAL | ToS bans robots | its reports appear inside plan packets; read them there |
| C4 | Marquette · Mercer | MANUAL; Mercer HARD-REJECT for automation | Mercer: "MAY NOT BE USED TO CONSTRUCT A DATABASE OF ANY KIND" | — |
| C5 | Dahab Associates | MANUAL (high value) | no ToS; posts live client RFPs | human checks weekly |
| C6 | Burgess Chambers | URL DROP | bcafl.com is a parked domain; firm active (CRD 110164) | via client plan minutes |

**No consultant publishes client lists or client performance reports on its own site.** Consultant
reports reach us inside plan board packets.

**What the sweep proved.** The automatable, high-value layer is (1) federal bulk and API data (SEC,
IRS, DOL, Census, PPD, GLEIF, Socrata) and (2) allocators' own sites: FL plan WordPress/CivicPlus
pages, the NYC Comptroller library, OSC monthly transaction reports, BERS, NYCERS, MTA. Trade press
is headline-only RSS. Bot-walled or terms-restricted sources stay manual.

---

## 3. Agent roster

All agents are workers consuming typed tasks from a `tasks` table (DuckDB → Postgres).
Every agent writes an append-only `audit_log` row per action (actor, task, inputs hash,
outputs hash, model/version, duration, status). Handoff = writing the next task row. No
agent calls another directly; the orchestrator schedules.

| Agent | Inputs | Outputs | Tools | Failure modes → handling | Hands off to |
|---|---|---|---|---|---|
| **Orchestrator / Scheduler** | cron (nightly full, hourly RSS/Atom), task table | task rows, run manifests | DuckDB/Postgres, APScheduler | stuck tasks → lease timeout + retry w/ backoff (3×) → dead-letter + watchdog alert | everyone |
| **Discovery Agent** (per region) | region spec; seeds F3 (BMF by county + asset size), F8 (PPD), F9 (Census unit file), FL2 (DMS plan list + fact-sheet codes), F6 (5500 by sponsor state/city), the §2.4 template list; search-engine-free (uses only the datasets + site maps) | `registry/<region>.yaml`: allocator entities w/ IDs, candidate home URLs, document-page candidates (sitemaps, "Board", "Minutes", "Investments", "RFP" link discovery), per-host robots + ToS verdict, cadence guess | httpx (GET only), robots parser, sitemap parser, Playwright for JS nav menus | wrong site → human fixes at Touchpoint 1; ToS unparseable → MANUAL default | **Touchpoint 1 → Crawler** |
| **Bulk Fetcher** | source IDs S2–S8, F1–F12, NY10–NY11 | files in document lake (sha256-named), load manifests | httpx w/ declared UA, 10 req/s token bucket for sec.gov, resumable downloads, checksum verify | 403/429 → backoff; schema drift → quarantine file + alert | Loader |
| **Loader / Normalizer** | manifests | typed staging tables; **address/contact columns dropped at load**; column allowlist per dataset | DuckDB, Polars, Pydantic | unexpected column → fail closed (never load unknown columns) | Resolver, Differ |
| **Site Crawler** (Playwright) | approved registry entries | fetched HTML/PDF into lake + `crawl_log` (url, ts, status, sha256, robots verdict) | Playwright (Chromium, GET only, honors robots, per-host rate ≥ crawl-delay or 10 s, no form submission, no auth), single egress proxy | bot wall (403/CAPTCHA) → **mark MANUAL, stop** (never retry with evasion); layout change → re-discover links | Parser |
| **Feed Poller** | RSS/Atom list (S4, S10, R3, P2, P5, P6, P8, C1) | headline rows (title, link, date, source) only; bodies discarded after extraction pass | feedparser | feed dead (CFTC "disabled") → page-poll fallback | Extractor (headline mode) |
| **Layout Parser** | lake docs | per-page text, tables (OTSL/HTML → rows), footnote anchors, page images; `parse_quality` score | Docling (standard: layout + TableFormer; VLM pipeline for scans), pdfplumber for born-digital tables, PaddleOCR-VL-1.6 / MinerU for scanned | low quality or 0 text → **Vision fallback** task; tables w/ merged headers → TableFormer path | Prefilter |
| **Vision Fallback** | page images where text extraction failed or table confidence low | DocTags/Markdown + JSON per page | Qwen3-VL / Qwen3.8-27B (natively multimodal) via vLLM; output **scrubbed** | hallucinated cells → cross-check w/ OCR text; disagreement → human queue | Prefilter |
| **Prefilter** | parsed pages | hit pages only (vocabulary: bucket terms, "watch", "terminate", "search", "RFP", "finalist", "IPS", "target", "fee", "redemption"; **manager list from ADV spine**; consultant names; dollar/percent table patterns) | Tantivy/DuckDB FTS, Aho-Corasick over normalized names | missed page → recall measured on gold set; threshold tuned | Extractor |
| **Extraction Agent** | hit pages + table rows + doc metadata; strict Pydantic schemas | `Mandate`, `Signal`, `Officer`, `AllocationSnapshot`, `Evidence` candidates with confidence; **tables → rows, never prose** | vLLM structured outputs (xgrammar/llguidance), 3-sample self-consistency vote, deterministic post-validators (sums, dates, enum) | schema violation impossible (grammar); low agreement → vision re-read or human queue; **evaluative language → lint fail → rewrite** | Resolver |
| **Resolution Agent** | entity mentions + ID ladder | `Alias` rows, merges, `merge_queue` rows | EIN/CIK/CRD/LEI/plan# join → normalized exact → blocked fuzzy (Jaro-Winkler + token-set) → LLM adjudication w/ evidence → queue | false merge → reversible (merges are edges, never destructive) ; alias regression set gate | **Touchpoint 2**, Graph Builder |
| **Graph Builder** | resolved rows | property graph (allocator↔manager↔fund↔consultant↔person-as-role edges, dated) | DuckPGQ (DuckDB 2.0) → Apache AGE on Postgres; NetworkX for analytics | cycle/inconsistency → QA agent | QA |
| **Differ** | nightly snapshots per source | deltas → `Signal` candidates (rules table: status active→terminated, target change, new RFP notice, new Form D amendment w/ higher sold, ADV RAUM change, new enforcement item) | SQL diff on normalized keys | noisy deltas (re-OCR drift) → require ≥2 fields changed or source-native date | Extractor (signal mode) |
| **QA / Consistency Agent** | new rows | flags: amount sums ≠ total, as-of before inception, same manager both active and terminated same date, bucket mapping conflicts, citation faithfulness (fact present on cited page) | rules + LLM judge | flagged rows quarantined, never silently dropped | human queues |
| **Briefing Agent** | approved graph | per-allocator and per-manager briefs (Markdown + JSON), pipeline view by signal recency; **every line cites `[doc-sha:page]`** | templates + LLM (factual verbs only), lint | uncited line → build fails | **Touchpoint 3 → Exporter** |
| **Exporter** | approved signals only | CSV / CRM payload (fields: entity, signal type, date, summary, evidence URL+page) | — | unapproved row → refuse | humans |
| **Watchdog** | logs, metrics, wall tests | alerts (local only: file + desktop notification), crawl health, model drift (eval harness scores), disk/encryption status | Prometheus-style counters in DuckDB, nightly `tests/wall` | — | humans |

---

## 4. Architecture & schema

### 4.1 Storage

- **Relational:** DuckDB 2.0 LTS (single-file, local) → Postgres 18 when multi-writer or
  size (>~200 GB) demands. Same SQL, same Pydantic models, migration via dbt-style
  SQL files. Snapshot tables are append-only with `run_id`.
- **Graph layer:** logical edges live in relational `edge_*` tables (typed, dated,
  evidence-bearing). Query via **DuckPGQ** (SQL/PGQ community extension, rebuilt for
  DuckDB 2.0 on 2026-10-15) in the DuckDB phase, **Apache AGE 1.8** (openCypher) in the
  Postgres phase. NetworkX in-process for centrality/path analytics. (Kùzu was archived
  2025-10-10; its fork **LadybugDB** v0.22 is the embedded fallback if PGQ proves
  insufficient.)
- **Vector store:** LanceDB 0.41 (hybrid FTS + vector in one table, metadata-first
  filtering, reranker API). Payloads are scrubbed text only.
- **Document lake:** `lake/raw/<sha256[:2]>/<sha256>` immutable bytes + `documents` table
  (sha256, source_id, url, fetched_at, content_type, bytes, robots_verdict, ToS_verdict).
  Derived artifacts under `lake/derived/<sha256>/<parser>@<version>/…`. Content-hash
  dedup is automatic; same doc from two URLs = one blob, two `document_urls` rows.

### 4.2 Pydantic models (v2; every model carries `provenance: list[EvidenceRef]`, non-empty)

```python
class EvidenceRef(BaseModel):
    evidence_id: UUID
class Evidence(BaseModel):
    evidence_id: UUID; document_sha256: str; source_id: str
    locator: Locator            # page:int | table_id/row/col | xpath | json_path | csv_row
    quote: constr(max_length=500)   # scrubbed
    extractor: str; extractor_version: str; prompt_hash: str | None
    extracted_at: datetime; confidence: confloat(ge=0, le=1); human_verified: bool = False

class Allocator(BaseModel):
    allocator_id: UUID; legal_name: str; display_name: str
    allocator_type: AllocatorType   # PUBLIC_PENSION_STATE|PUBLIC_PENSION_LOCAL|POLICE_FIRE|TEACHERS|CORPORATE_DB|TAFT_HARTLEY|ENDOWMENT|PRIVATE_FOUNDATION|COMMUNITY_FOUNDATION|HOSPITAL|INSURER|SOVEREIGN|OCIO|FOF|FAMILY_OFFICE_PUBLIC|OTHER
    ids: EntityIDs                  # ein, cik, lei, plan_number+sponsor_ein, ppd_id, fl_dms_id, state_registry_id
    jurisdiction_city: str | None; jurisdiction_state: str | None; jurisdiction_country: str = "US"
    fiscal_year_end_month: int | None
    aum_as_disclosed: Money | None; aum_as_of: date | None
    consultant_ids: list[UUID]; status: EntityStatus; provenance: list[EvidenceRef]

class Officer(BaseModel):           # person-as-role ONLY
    officer_id: UUID; org_id: UUID; name_as_printed: str; title_as_printed: str
    role_type: RoleType             # TRUSTEE|CHAIR|CIO|EXEC_DIRECTOR|STAFF|CONSULTANT_REP|ACTUARY|AUDITOR|COUNSEL|OTHER
    as_of: date; provenance: list[EvidenceRef]
    # NO contact fields. Model config forbids extra fields (extra="forbid").

class Manager(BaseModel):
    manager_id: UUID; legal_name: str; ids: EntityIDs   # crd, sec_file_no, cik, lei, nfa_id, state_registry_id
    registration_status: str | None; raum_as_disclosed: Money | None; raum_as_of: date | None
    client_type_mix: dict[str, float] | None             # ADV Item 5.D percentages
    jurisdiction_city/state/country; provenance

class Fund(BaseModel):
    fund_id: UUID; manager_id: UUID; name_as_disclosed: str; ids: EntityIDs   # adv_private_fund_id, form_d_cik, lei, cima_no
    fund_type_as_disclosed: str | None; gav_as_disclosed: Money | None; gav_as_of: date | None
    service_providers: list[ServiceProviderEdge]          # role: AUDITOR|PRIME_BROKER|CUSTODIAN|ADMINISTRATOR|MARKETER; provider_name; provider_ids
    form_d_offerings: list[FormDOffering]                  # date, total_offered, total_sold, amendment flag
    provenance

class Mandate(BaseModel):
    mandate_id: UUID; allocator_id: UUID; manager_id: UUID; fund_id: UUID | None
    bucket_label_as_disclosed: str                         # verbatim ("Absolute Return", "Diversifying Strategies")
    bucket: StrategyBucket; bucket_map_confidence: float   # crosswalk, see 4.3
    amount_as_disclosed: Money | None; amount_kind: Literal["market_value","commitment","target_pct","actual_pct"] | None
    as_of: date; inception_as_disclosed: date | None
    status: MandateStatus          # ACTIVE|ON_WATCH|TERMINATED|PENDING_FUNDING|REDEEMING|UNKNOWN
    fee_as_disclosed: str | None   # verbatim text only, never computed
    liquidity_as_disclosed: str | None
    consultant_id: UUID | None; provenance

class Consultant(BaseModel): consultant_id; legal_name; ids; clients: list[ConsultantEngagement]; provenance
class Alias(BaseModel): alias_id; entity_id; entity_kind; alias_text; normalized; source_id; match_method; score; provenance
class Signal(BaseModel):
    signal_id: UUID; signal_type: SignalType; subject_ids: list[UUID]; event_date: date; observed_at: datetime
    summary: constr(max_length=300)        # factual, lint-checked
    review_status: Literal["new","approved","rejected"] = "new"; provenance (≥1)
```

`SignalType` enum: `SEARCH_OPENED, RFP_ISSUED, FINALISTS_NAMED, MANDATE_AWARDED, MANDATE_FUNDED,
MANAGER_TERMINATED, MANAGER_ON_WATCH, WATCH_REMOVED, REDEMPTION_APPROVED, COMMITMENT_MADE,
TARGET_ALLOCATION_CHANGED, IPS_AMENDED, ASSET_LIABILITY_STUDY, PACING_PLAN_ADOPTED,
CONSULTANT_CHANGED, CIO_CHANGED, OFFICER_CHANGED, PERFORMANCE_DISCUSSED, DRAWDOWN_DISCUSSED,
FEE_RENEGOTIATED, FEE_DISCLOSED, REBALANCE_APPROVED, MANAGER_RAUM_REPORTED, FORM_D_NEW,
FORM_D_AMENDED, FORM_ADV_AMENDED, FUND_LAUNCHED, SERVICE_PROVIDER_CHANGED, REGULATORY_ACTION,
LITIGATION_FILED, ENTITY_FORMED, ENTITY_DISSOLVED, PERSONNEL_MOVE_AS_FILED, PRESS_HEADLINE`.

### 4.3 Strategy buckets

`StrategyBucket` enum (system-side): `EQUITY_LONG_SHORT, EQUITY_MARKET_NEUTRAL, EVENT_DRIVEN,
MERGER_ARB, DISTRESSED, CREDIT_LONG_SHORT, STRUCTURED_CREDIT, CONVERTIBLE_ARB, FIXED_INCOME_RV,
GLOBAL_MACRO, MANAGED_FUTURES_CTA, MULTI_STRATEGY, FUND_OF_HEDGE_FUNDS, QUANT_SYSTEMATIC,
VOLATILITY, INSURANCE_LINKED, COMMODITIES, DIGITAL_ASSETS, PRIVATE_CREDIT, PRIVATE_EQUITY,
VENTURE, REAL_ESTATE, INFRASTRUCTURE, NATURAL_RESOURCES, LONG_ONLY_EQUITY,
LONG_ONLY_FIXED_INCOME, CASH_OVERLAY, OTHER`. The allocator's own label is **always
preserved verbatim**; the crosswalk table (`bucket_crosswalk`: label, allocator_id, bucket,
confidence, evidence) is itself a reviewable artifact. See §10 Q4.

### 4.4 Parsing pipeline

born-digital PDF → pdfplumber text layer + table finder (MIT) → pages with spanning headers →
Docling standard pipeline (layout + TableFormer → OTSL → rows) → footnotes bound to anchors
(superscript/marker detection; footnote text attached to the cell/row it annotates) →
scrubber → prefilter. Scanned PDF → Docling VLM pipeline (Granite-Docling-258M) or
PaddleOCR-VL-1.6 / MinerU 4.x → same path. HTML → trafilatura + table parser. XML/CSV →
direct typed load. Failure (no text, garbage OCR, table confidence < threshold) → page image
→ Vision Fallback. **Tables become rows, never prose.** The LLM only ever sees hit pages
(prefilter) and gets the table as structured rows plus the ≤2 sentences around it.

### 4.5 Retrieval

Hybrid BM25 (LanceDB FTS / Tantivy) + dense (Qwen3-Embedding-4B, 32K ctx) → metadata-first
filtering (allocator_id, source_id, doc date range, doc type) → top-50 → rerank
(Qwen3-Reranker-4B) → **table rehydration**: any hit inside a table pulls the whole table
(rows + header + footnotes) rather than a text chunk. Chunking: section-aware, ≤1,500
tokens, tables never split.

### 4.6 Resolution ladder

1. **ID match** (any shared: EIN, CIK, CRD, SEC file no., LEI, sponsor-EIN+plan#, PPD id,
   FL DMS id, Sunbiz doc#, NY DOS id, CIMA no., ADV private-fund id) → auto-merge.
2. **Normalized exact** (casefold, Unicode NFKC, strip punctuation and legal suffixes
   `LP|L.P.|LLC|Ltd|Inc|Fund|Master|Offshore|Onshore|Cayman|Series`, collapse whitespace,
   roman↔arabic numerals) within same entity kind → auto-merge.
3. **Blocked fuzzy** (block on first significant token + state + kind; Jaro-Winkler ≥ 0.92
   *and* token-set ratio ≥ 0.90 → score) → ≥0.95 auto-merge; <0.80 auto-new.
4. **LLM adjudication** (0.80–0.95): both entities' evidence snippets → structured verdict
   {same, different, unsure} with reason; `same` ≥0.9 confidence → merge; else →
5. **Merge queue** (Touchpoint 2).

**Regression alias set** (`tests/resolution/aliases.yaml`, ≥300 pairs, grown from queue
decisions): positives (e.g., "Elliott Management" ≡ "Elliott Investment Management L.P.";
"Citadel Advisors" ≡ "Citadel Advisors LLC"), hard negatives ("Millennium Management" ≠
"Millennium Partners"; "Pershing Square" ≠ "Pershing LLC"; "Davidson Kempner" ≠ "Kempner
Capital"), series/feeder cases ("XYZ Fund LP" vs "XYZ Offshore Fund Ltd" → same manager,
different funds). Gate: zero regressions on every change to the ladder.

### 4.7 Diffing & signals

Nightly: snapshot each source's normalized rows with `run_id`; `EXCEPT`/`INTERSECT` on
natural keys → delta table → rules table maps deltas to `Signal` candidates with both
snapshots as evidence. Signals need a **source-native date** (meeting date, filing date,
notice date); "observed_at" is never used as the event date. Language lint: banned
evaluative tokens (`outperform|underperform|strong|weak|poor|impressive|disappoint|top|
best|worst|better|worse|laggard|leader|attractive|concern`), allowed factual verbs
(`reported|disclosed|approved|voted|terminated|placed on watch|removed from watch|issued|
awarded|selected|increased … from X to Y|decreased … from X to Y|committed|redeemed`);
LLM judge second pass; failing summaries are rewritten or dropped, never stored.

### 4.8 Outputs

- **Allocator brief:** header (type, AUM as disclosed + date, consultant, officers as
  printed, FYE, meeting cadence) · allocation table (target vs actual as disclosed, by
  bucket, with label verbatim) · incumbents by bucket (status, as-of, amount kind) · signal
  timeline · open searches · documents watched (last fetch, next expected). Every line
  `[sha:page]`.
- **Manager brief:** ADV facts (RAUM trajectory table), funds (7.B.1), Form D history,
  13F summary (count of positions, total value — numbers only), service providers,
  regulatory/litigation list, publicly disclosed allocators, signals.
- **Pipeline view:** signals by recency with filters (type, region, bucket, allocator
  type, review status).
- **Export:** CSV/CRM, approved signals only, with evidence URLs and page numbers; export
  log is append-only.

---

## 5. Local / open-weight deployment plan

Verified by agent on 2026-11-05 against HF model cards, GitHub releases and PyPI (dates and
file sizes quoted there; anything the agent marked UNVERIFIED is omitted here).

### 5.1 Model choices by task

| Task | 24 GB box (used 3090/4090, 5090) | 48 GB box (A6000 / 6000 Ada) | 96 GB box (RTX PRO 6000 Blackwell) | Dual-GPU (2×48 or 2×96, vLLM TP=2) | Notes |
|---|---|---|---|---|---|
| **Extraction (strict JSON)** | gpt-oss-20b MXFP4 (12.1 GB file; Apache-2.0) as workhorse; Qwen3.5-9B Q8 (9.5 GB) for bulk | **Qwen3.8-27B FP8 (~28 GB)** or Gemma-4-31B FP8 (~31 GB) with 64K+ ctx; Qwen3.6-35B-A3B FP8 (~36 GB) for throughput | **gpt-oss-120b MXFP4 (63.4 GB)** or Qwen3.5-122B-A10B GPTQ-Int4 (~65–70 GB) | 2×48: gpt-oss-120b or 122B-A10B int4; 2×96: 122B-A10B FP8 + a separate VLM | grammar-constrained decoding makes validity ~100% on all; pick by content accuracy on the gold set |
| **Vision (page/table fallback)** | Qwen3-VL-8B int4 (~6 GB) alongside the extractor; PaddleOCR-VL-1.6 (0.9B) for OCR | Qwen3-VL-32B int4 (~19 GB) or reuse Qwen3.8-27B (natively multimodal) | Gemma-4-31B BF16 or Qwen3.5-122B-A10B (multimodal) | dedicated GPU for VLM | Granite-Docling-258M (<2 GB) always on for layout/DocTags |
| **Layout/tables** | Docling 2.148 (TableFormer) + Granite-Docling; PaddleOCR-VL-1.6 or MinerU 4.1 for scans | same | same | same | avoid Marker/Chandra weights (OpenRAIL-M revenue caps) for commercial self-hosting |
| **Embedding** | BGE-M3 (MIT, 8K) or Qwen3-Embedding-0.6B | **Qwen3-Embedding-4B** (Apache, 32K) | Qwen3-Embedding-8B (~16 GB) | — | Harrier-oss-v1 (MIT, Mar 2026) as challenger |
| **Reranking** | bge-reranker-v2-m3 (0.6B) | **Qwen3-Reranker-4B** (~8 GB) | same | — | avoid jina (NC licenses) |
| **Adjudication / QA judge** | same model as extraction, different prompt + temperature 0 | second family for diversity (Gemma-4-31B if extractor is Qwen) | gpt-oss-120b | split families across GPUs | two-family agreement required for TERMINATED / ON_WATCH signals |
| **PII scrubbing** | Presidio 2.2 (now github.com/data-privacy-stack/presidio) + GLiNER `urchade/gliner_multi_pii-v1` | same | same | same | CPU-capable; runs before any LLM call |

**VRAM budget rule of thumb:** weights (file size above) + KV cache (FP8 KV: ≈ 0.5–1 GB per
8K tokens for 27–31B dense; MoE-A3B much less) + 2 GB runtime. A 48 GB card running
Qwen3.8-27B FP8 (28 GB) leaves ~16 GB for KV → ~128K tokens of concurrent context at
FP8 KV, enough for 8–12 concurrent extraction requests at 8–12K each.

### 5.2 Serving stack

- **vLLM 0.33** (Apache-2.0) for all LLM/VLM/embedding/score endpoints: continuous
  batching, prefix caching (our prompts share long system + schema prefixes), **structured
  outputs via xgrammar / llguidance** (JSON-schema from Pydantic; lm-format-enforcer is
  deprecated), FP8 + MXFP4 + NVFP4 KV, TP across GPUs. Requires CUDA 13 drivers as of 0.33.
- **llama.cpp (llama-server)** for Apple-Silicon dev boxes and CPU fallback (JSON-schema →
  GBNF grammar built in).
- **Ollama**: dev convenience only. Its privacy policy (Mar 2026) collects device/usage
  metadata and download metadata with no documented opt-out; not for the air-gapped tier.
- **HF TEI 1.9.4** for embeddings if we want a separate tiny service; Qwen3-Reranker served
  via vLLM score API (TEI doesn't list it).
- **Why vLLM over SGLang:** both fine; vLLM's structured-output backends and MXFP4/gpt-oss
  support are the ones we verified. Revisit at Phase 6.

### 5.3 What breaks on the frontier→30B swap, and the compensation

| Breaks | Compensation |
|---|---|
| Long-context reasoning over whole packets (200+ pages) degrades | **prefilter harder** (vocab + manager list + table detector) so the model sees 3–10 pages, not 200; section-aware chunking; table rehydration |
| Free-form schema adherence | **grammar-constrained decoding** (zero invalid JSON); smaller atomic tasks (one table → one call; one signal candidate → one call) |
| Subtle status inference ("the Board directed staff to begin the process of…") | typed few-shot exemplars retrieved from the gold set per signal type; **two-family agreement** for high-impact types; otherwise → human queue |
| Numeric transcription errors | tables come from the parser as rows (model never retypes numbers); deterministic validators (row sums, % totals ≈ 100, as-of ≤ meeting date) |
| Entity linking recall | resolution runs on the ID ladder + fuzzy *before* the LLM; the LLM only adjudicates the 0.80–0.95 band |
| Hallucinated citations | citation faithfulness check: the quoted span must string-match the cited page after normalization; else reject |
| OCR noise in scanned minutes | self-consistency (3 samples, majority per field) + vision fallback on disagreement |
| Overall precision drop | **raise human-queue thresholds** at launch (e.g., all TERMINATED/ON_WATCH to review), lower them as gold-set precision proves out |

### 5.4 Hardware (US street, early Nov 2026 — agent-verified ranges)

| Option | VRAM | Approx. price | Use |
|---|---|---|---|
| Used RTX A6000 | 48 GB | ~$4.4k | **best value single-GPU prod** (DRAM shortage has pushed 5090 to $6–8k) |
| 2× used RTX 3090 | 2×24 GB | ~$2.5–3k | cheapest dual-GPU TP=2 for 27–31B FP8 |
| RTX 5090 | 32 GB | $6–8k street (MSRP $1,999) | poor value today |
| RTX 6000 Ada | 48 GB | $4.75k used – $9.99k new | prod |
| RTX PRO 6000 Blackwell | 96 GB | ~$16k | gpt-oss-120b / 122B-A10B single-card |
| DGX Spark 128 GB | 128 GB unified | ~$4.7–7k | CUDA/vLLM small-footprint prod |
| Mac Studio M5 Ultra 96–512 GB | unified | $5.5k–$22.3k | dev via llama.cpp/MLX only (no vLLM) |

Recommendation (D-040): start on **one used A6000 48 GB** (or 2×3090) Linux box with
Qwen3.8-27B FP8 + Qwen3-VL-8B + Qwen3-Embedding-4B + Qwen3-Reranker-4B; upgrade to a 96 GB
card only if gold-set accuracy demands gpt-oss-120b.

---

## 6. Security posture

- **Three tiers, physically or VLAN-separated:**
  1. *Crawl tier* — the only tier with internet, and only through **one allowlisted
     forward proxy** (Squid/Envoy) with a domain allowlist generated from the approved
     registry; GET/HEAD only; TLS terminated at the proxy for logging; per-host rate limits
     enforced at the proxy (defense in depth beyond the crawler's own limits).
  2. *Processing tier* — GPU box(es) running vLLM, Docling, Presidio; **no default route**;
     talks only to the data tier and receives tasks from the orchestrator.
  3. *Data tier* — DuckDB/Postgres, LanceDB, document lake; no internet; encrypted volumes.
- **Secrets:** CourtListener token, Socrata app token, declared-UA contact string, DB
  passwords → local vault (**sops + age** files or 1Password CLI; no cloud KMS). Never in
  repo, never in logs, never in LLM prompts.
- **Logging:** every HTTP request/response (url, method, status, bytes, sha256 of body,
  robots verdict) and every model call (model, version, prompt sha256, response sha256,
  tokens, latency) → append-only `audit_log`. Full payloads retained encrypted for 90 days
  (D-039), hashes forever.
- **Disk encryption:** LUKS (Linux) / FileVault (Mac dev); backups encrypted with age;
  keys offline.
- **No telemetry:** `HF_HUB_OFFLINE=1`, `HF_HUB_DISABLE_TELEMETRY=1`, `VLLM_NO_USAGE_STATS=1`,
  `DO_NOT_TRACK=1`, Playwright browsers pre-installed from a staging box, no Ollama in prod,
  pip/uv installs from a vetted local mirror; weights downloaded once on a staging box,
  sha256-verified against the model card, then copied in.
- **Supply chain:** lockfiles (uv), pinned model revisions (HF commit hashes), SBOM per
  release, dependency allowlist test (no SMTP/LinkedIn/form libraries — part of the Wall).
- **Access:** two roles (operator, reviewer); review queues are the only write path for
  humans; all human decisions logged with actor + timestamp.

---

## 7. Legal posture (for the CCO)

**What we ingest and why it is public.** Federal filings (SEC EDGAR, IAPD, IRS 990 series,
DOL Form 5500) are public by statute (Securities/Advisers Acts disclosure regimes, IRC
§6104, ERISA §104(a)) and are US-government works in the public domain (17 USC §105). State
and local plan documents (agendas, minutes, consultant reports, IPS, ACFRs, RFPs) are public
records under **Florida Chapter 119** and the **Sunshine Law (F.S. 286)**, and under New York
**FOIL** and the **Open Meetings Law**; F.S. 112.664 affirmatively requires FL local plans to
publish financial disclosures on their websites. Open-data portals (NYC/NYS Socrata, GLEIF
CC0) carry explicit open licenses. We only read what is reachable without a login.

**ToS compliance.** Each source has a recorded verdict (§2) and the crawler refuses hosts
without an approved verdict. Robots.txt is honored literally (path rules and crawl-delays),
including `Disallow: /basicnet/` (NFA), `/firm/brochure/` (IAPD — we use SEC bulk brochure
zips instead), `/*.pdf$` (EMMA — we don't use EMMA), and `Disallow: /` (LinkedIn,
FundFire, BoardDocs, PrimeGov, the main PASSPort host). Sites whose ToS forbid automation (FINRA BrokerCheck
for commercial use, EMMA, LinkedIn, Preqin, iConnections, Capital Allocators, Mariner,
Marquette, Mercer, P&I) are MANUAL-ONLY: a human may read them in a browser and enter a
dated, cited fact by hand. **No circumvention of bot walls, ever** (this is also the posture
the Claude Code safety classifier enforced during this session).

**CFAA / scraping adjacency.** Reading public, login-free pages at polite rates and
honoring technical and contractual limits is the conservative end of the post–*hiQ v.
LinkedIn* / *Van Buren* landscape. Residual contract-law exposure exists on sites with
browsewrap ToS we never clicked; we mitigate by honoring them anyway.

**Copyright.** Government documents: public domain or public record. Trade press and
consultant thought leadership: we store **headline, URL, date, and our own ≤300-char
factual summary**, never the article body (Hedgeweek's full-text RSS body is discarded after
extraction). Attribution preserved via URL. No redistribution of third-party text.

**The no-contact wall** (§1.1) is a design invariant, tested nightly. It also keeps us
outside the perimeter of anti-spam (CAN-SPAM, TCPA), data-broker and people-search statutes,
and most state privacy laws, because we hold no reachability data on any individual — only
names and titles as printed in public filings.

**No comparative or evaluative claims in the data layer.** Signal summaries and brief
lines are lint-checked for evaluative language (§4.7). Pitch language is **human-written and
compliance-reviewed**; the system exports facts with citations, not prose about competitors.
This keeps the system's output from becoming an "advertisement" under the SEC **Marketing
Rule (206(4)-1)** and from making unsubstantiated statements about other managers; if the
firm is a CPO/CTA, the same applies to **NFA Compliance Rule 2-29** promotional material.

**Pay-to-play adjacency (Advisers Act Rule 206(4)-5; NYC Lobbying Law; NYSCRF placement-
agent ban; Miami-Dade/Broward lobbyist ordinances).** Many trustees are elected officials or
appointees. The system records their names and titles *because the filings print them*, but
stores no reachability, no contributions, no personal data, and generates no outreach.
Candid note: human BD outreach to NYC and some FL plans can itself trigger **lobbyist
registration** requirements; that is a firm-policy matter outside this system, flagged here
so the CCO sees it.

**Record retention & audit trail.** Raw public documents retained indefinitely (they are
evidence); derived tables versioned per run; audit log append-only; model prompts/responses
hashed forever and retained in full 90 days (adjustable). If any export feeds marketing
material, the firm's Rule 204-2 books-and-records retention (5 years, first 2 readily
accessible) applies to the exported file and its evidence set.

**Residual risk, stated plainly.** (1) A trustee could regard a dossier of their public
statements as surveillance even without contact data — mitigated by role-only modeling and
factual-only summaries, not eliminated. (2) Extraction errors could label a manager
"terminated" wrongly — mitigated by two-family agreement, citations, and human review before
export; a false signal that reaches a pitch is a reputational and potentially defamation-
adjacent risk, hence Touchpoint 3 is mandatory. (3) ToS can change; the registry re-checks
robots/ToS monthly and flips verdicts automatically to MANUAL on any new restriction. (4)
Some state-court portals and plan sites sit behind CAPTCHAs; those gaps stay manual. (5) The
raw-document lake contains incidental contact details printed in public PDFs (§1.1 note) —
a CCO decision is required on redaction vs. evidence integrity.

---

## 8. Evaluation plan

**Gold set (50 hand-labeled documents, versioned in `eval/gold/`):**
10 FL local-plan board packets/minutes (mixed born-digital and scanned) · 8 NYC/NYS board
materials (CIM, TRS minutes, NYSCRF monthly transaction reports) · 6 consultant performance
reports inside packets · 5 investment policy statements · 4 ACFRs/financial statements with
investment footnotes · 4 RFP/solicitation notices (City Record, plan sites) · 3 Form 990
Schedule D / 990-PF investment schedules (XML + PDF) · 3 Form 5500 Schedule C/H + schedule
of assets attachments · 2 Form ADV brochures (from bulk zips) · 2 Form D XML · 2 press
headlines (RSS) · 1 agenda HTML page.

**Labels:** entity mentions with spans and resolved IDs; mandates (bucket label verbatim,
mapped bucket, amount, amount kind, as-of, status); signals (type, event date, summary);
officers (name, title, role); table cells for 10 table-heavy docs; citation anchors.

**Metrics & gates (per field):** precision/recall/F1; exact match for amounts and dates;
table cell accuracy (TEDS for structure, cell EM for values); bucket-mapping accuracy;
signal type precision **≥ 0.95** and recall ≥ 0.80; citation faithfulness ≥ 0.98; entity
resolution on the alias regression set: zero regressions; **wall test: zero hits**.
Regression runs on every model swap, prompt change, parser upgrade, or threshold change; a
drop > 1 point absolute on any gated metric blocks the change without sign-off.

**Bootstrapping labels:** frontier API (Claude) drafts labels during build (D-003); a human
corrects; the local model is then scored against the human-corrected set. Inter-annotator
check on 10 docs.

---

## 9. Build phases with definitions of done

| Phase | Scope | Definition of done |
|---|---|---|
| **P0 Foundation** (2 wks) | repo (`uv`, Python 3.12, Pydantic v2), DuckDB, document lake, audit log, declared-UA httpx client w/ token bucket, robots/ToS registry, **wall tests + canary**, dependency allowlist | `make test` green incl. `tests/wall`; canary doc yields 0 stored contact tokens; schema introspection test passes; CI blocks on failure |
| **P1 Federal spine** (3 wks) | S2–S10, F1, F3, F4, F6, F8, F9, F10, NY10, NY11 loaders with column allowlists; Manager/Fund/Allocator seeds; ID-ladder rungs 1–2 | ≥95% of RIAs with FL/NY principal office loaded w/ CRD+RAUM; all 7.B.1 funds linked; 990 Schedule D holdings parsed for 20 sample filers; 5500 Sched C/D/H loaded for FL/NY sponsors; zero address columns present (test) |
| **P2 Registry FL + NYC** (2 wks) | Discovery agent → `registry/south-florida.yaml`, `registry/nyc.yaml` (allocators, URLs, doc pages, robots/ToS verdicts) | human approves both (Touchpoint 1); every host has a verdict; MANUAL hosts listed with reason |
| **P3 Crawl + parse** (4 wks) | crawler templates T-WP, T-CP, T-LEG, T-AIRO first (covers the ~20 verified South FL plans in §2.4) + NYC Comptroller library, NYCERS, BERS, OSC monthly transaction reports, MTA; Docling pipeline; vision fallback; prefilter | nightly crawl completes w/o manual steps; 100% of fetched docs hashed + logged; parse quality ≥ threshold on 90% of pages; `crawl_log` shows zero non-GET requests |
| **P4 Extraction + signals + eval v1** (4 wks) | extraction agent (frontier API during build), gold set labeled, eval harness, differ, signal lint | gold-set gates met w/ frontier model; nightly diff produces dated, cited signals; lint rejects evaluative text in tests |
| **P5 Resolution + graph + briefs** (3 wks) | fuzzy + LLM adjudication, merge queue UI (Touchpoint 2), DuckPGQ graph, briefs, pipeline view | alias regression set green; 20 allocator briefs + 50 manager briefs render with 100% cited lines; merge queue < 5% of mentions |
| **P6 Local model migration** (3 wks) | vLLM stack, model bake-off on gold set, swap, air-gap network posture, no-telemetry checks | gold gates met with local models (or documented delta + queue thresholds raised); processing/data tiers have no default route (test); telemetry env verified |
| **P7 Review + export** (2 wks) | signal-review queue (Touchpoint 3), CSV/CRM export, export log, retention jobs | export refuses unapproved rows (test); audit log complete; CCO walkthrough of §7 done |
| **P8 Nationwide** (ongoing) | registry per state from PPD/NASRA/DMS-equivalents/BMF/5500; crawler templates per agenda platform | one new state per week w/ Touchpoint 1 approval |
| **P9 Global** (later) | UK LGPS (pooled), Canada, Australia (APRA data), Netherlands/Nordics, sovereign annual reports | per-country legal memo before any crawl |

---

## 10. Open questions & decisions needed from you

1. **Hardware budget / box.** Used A6000 48 GB (recommended, ~$4.4k) vs 96 GB RTX PRO 6000
   (~$16k) vs 2×3090? Linux box assumed.
2. **CRM target and export shape.** HubSpot / Salesforce / Affinity / DealCloud / Backstop
   / Dynamo / CSV only? Field mapping depends on it.
3. **Raw-document posture (irreversible-ish).** Keep raw PDFs unredacted + encrypted as
   evidence with all derived layers scrubbed (recommended), or burn-in redact at ingest?
4. **Strategy bucket taxonomy.** Accept the §4.3 enum or supply the firm's own?
5. **Entity location granularity.** City + state + country only (recommended), or allow
   ZIP5 for registry filtering? Street addresses are out regardless.
6. **Frontier API during build.** OK to use Claude for gold-set drafting and P4 extraction
   while local hardware is procured (public documents only)? Or local-only from day one?
7. **History depth.** Back-fill 10 years of minutes/filings (recommended) or 5?
8. **Scope of allocator classes in Phase A.** Include corporate DB + Taft-Hartley (5500) and
   hospitals/foundations (990) alongside public pensions from the start (recommended), or
   pensions first?
9. **Legistar API token for NYC Council.** Miami-Dade's Legistar API works without a token.
   NYC's needs one, requested through a form on council.nyc.gov that asks for a name and
   email. That is a human submitting a form to a government vendor, not contacting an
   allocator. Permitted, or stay with HTML pages?
10. **Trade press.** RSS headlines only (recommended) vs. none.
11. **Who reviews.** Name the reviewer(s) for Touchpoints 2 and 3 and the CCO for §7.
12. **Regulatory status of the firm** (RIA? ERA? CPO/CTA? none yet) — determines which of
    206(4)-1 / NFA 2-29 / 206(4)-5 apply to downstream use.
13. **Manual-source workflow.** Do you want a lightweight "manual fact entry" form for
    MANUAL-ONLY sources (P&I, NFA, BrokerCheck, Dahab RFPs) with mandatory URL + date, or
    exclude them entirely?
14. **Public-records requests.** FL ch. 119 / NY FOIL requests by a human would unlock
    non-posted packets. Out of system scope by the Wall; do you want a human SOP?
15. **Bot-walled public PDFs (FL SBA, NYSTRS).** Their HTML is blocked to non-browser clients,
    but the documents are public records. Recommended (D-030): a human downloads them in a
    browser on a schedule and drops them into an `inbox/manual/` folder that the parser
    ingests with a `MANUAL` provenance tag. The system never fetches those hosts itself. OK?
16. **Sunbiz SFTP.** Florida publishes free SFTP credentials for its corporate data files on
    a page that blocks non-browser clients. A human copies them once into the local secrets
    vault. OK?
17. **Location of the build.** Recommended: a new private repo `~/dev/allocator-radar`
    (code, data, models stay off this vault), with this folder as the plan of record. OK?

---

## 11. Risks

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Plan sites bot-wall or redesign (Resource Centers 403/521 observed; FL SBA, NYSTRS, P&I 403 to non-browsers) | high | medium | registry re-discovery monthly; MANUAL fallback; never evade |
| Scanned/low-quality minutes defeat OCR | high | medium | VLM pipeline; vision fallback; human queue |
| False "terminated/on watch" signal reaches a pitch | low–medium | **high** | two-family agreement, citation faithfulness, mandatory Touchpoint 3 |
| Entity false-merge (manager ↔ similarly named firm) | medium | high | ID ladder first; merges are reversible edges; alias regression set |
| Wall leak via raw PDFs or vision output | low | high | scrub all derived layers; nightly wall test; CCO decision on raw redaction |
| ToS change flips a source to forbidden | medium | low | monthly robots/ToS re-check auto-flips verdict to MANUAL |
| Local 27–35B model under-performs frontier on inference-heavy signals | medium | medium | §5.3 compensation; raise queue thresholds; 96 GB upgrade path |
| Per-plan crawler maintenance burden (hundreds of bespoke sites) | high | medium | platform templates (Legistar, NovusAgenda, CivicPlus, Granicus, WordPress-PDF-list); discovery agent regenerates link maps |
| CFTC/press RSS instability | medium | low | page-poll fallbacks |
| Perception/lobbying-law adjacency when humans act on signals | medium | medium | §7 flags; firm policy; no reachability data |
| Hardware pricing volatility (DRAM shortage) | high | low | buy used 48 GB; design is GPU-agnostic |
| Scope creep into licensed data | medium | high | hard-reject list in code (host blocklist) |
| Coverage gaps from walled sources (FL SBA, NYSTRS, ~8 Akamai-fronted South FL city sites, Resource Centers plans) | certain | medium | manual inbox (Q15); PPD and Census data fill allocation history; sponsor-city pages as fallback |
| CourtListener free tier is 125 requests/day | certain | low | use bulk snapshots + alert webhooks; consider membership |

---

*End of AGENDA.md v0.2. See `DECISIONS.md` and `NEXT-SESSION.md`.*
