# HANDOFF: shared notes between the DATA and PRODUCT sessions

Path: `/Users/drekothapoet/dev/allocator-radar/HANDOFF.md`. Both sessions read and append here, always at this
absolute path in the main checkout. Don't edit this file from a worktree copy or commit it on a branch. PRODUCT commits
it to main when merging.

## Ownership (hard rules)

**DATA** (worktree `../allocator-radar-data`, branch `data`)
- Is the only writer to `data/radar.duckdb` and `data/ef.duckdb` in the main checkout.
- Owns crawlers, parsers, extraction, vocabulary and classification, ingestion, `data/manual_tasks.yaml`, and the
  spot-check accuracy figures.
- Follows D-B23: a dated backup in `data/backups/` and a dry-run count, recorded here, before any database-wide rewrite.
- Posts here when a new data snapshot is ready ("DATA READY <timestamp>: <what changed>") and when DATA is done
  ("DATA DONE <timestamp>").
- Records each spot check as: `SPOT-CHECK <date>: <n> signals checked, <k> correct, accuracy <k/n>%, method <...>`.

**PRODUCT** (worktree `../allocator-radar-product`, branch `product`)
- Never writes to `data/radar.duckdb` or `data/ef.duckdb` in the main checkout. Builds from copies in its own worktree,
  and re-copies after a "DATA READY" line.
- Doesn't edit crawlers, parsers, extraction, vocabulary or ingestion code. Asks for fields and fixes with a "REQUEST"
  entry below.
- Owns git hygiene, ROADMAP.md, the site (templates, display and scoring logic, static export), and the local admin pages.
- Is the only session that merges to main and deploys.

**Both**
- Names and titles as printed are kept (D-B21). No emails, phone numbers, street addresses or individual compensation,
  ever (D-B22). Every figure and signal is cited to a document and page, or to a filing year and line.
- No outbound features. No keys or `.env` files in git.
- Florida only. Nothing from Phase D onward starts without the owner.

## Entry format

Append at the bottom, newest last:

```
### <YYYY-MM-DD HH:MM> <DATA|PRODUCT> <READY|DONE|REQUEST|ANSWER|NOTE|SPOT-CHECK>
<body>
```

## Log

### 2026-11-11 12:20 PRODUCT NOTE
Gate 0 done. main = 5940cd0, tag `stable-2026-11-11`. Backups: `data/backups/radar.2026-11-11.duckdb`,
`data/backups/ef.2026-11-11.duckdb` (checksums match the live files as of 12:12). The pre-restore store is kept as
`data/radar.pre-unredact-2026-11-11.duckdb`. The `names-roles` branch is deleted (tagged `archive/names-roles-2026-11-10`).
Note: `../allocator-radar-B/data/ef_cache/` (183 MB, IRS TEOS index cache) exists only in worktree B. Left in place.

### 2026-11-11 12:45 PRODUCT REQUEST
R1. `data/manual_tasks.yaml` (DATA maintains, PRODUCT renders at /tasks on the local app only). Proposed shape, so
both sides agree; tell me if you need other fields:
```yaml
tasks:
  - id: fl-sba-frs-2026q3-perf        # stable, unique
    priority: 1                       # 1 = highest
    allocator_id: fl-sba-frs          # also the folder name under inbox/manual/
    allocator: Florida SBA (FRS Pension Plan)
    what: Q3 2026 quarterly performance report
    url: https://...                  # exact URL to open in a browser
    file_name: sba-2026q3-performance.pdf   # name to save it as
    drop_folder: inbox/manual/fl-sba-frs/   # where the file goes
    why_manual: site blocks automated GETs (403 behind Akamai)
    status: open                      # open | received | ingested | dropped
```
PRODUCT's upload box saves the file to `<drop_folder><file_name>` and sets `status: received` on that task by
rewriting only that key in the YAML file. DATA owns every other change to the file and moves `received` to `ingested`.
R2. Strategy labels: too many signals show "other (inferred)". Details to follow once I finish the audit.

### 2026-11-11 14:10 PRODUCT REQUEST
Findings from PRODUCT's read-only audit. The display already works around each of these; the fixes belong in the data.
Full signal ids: `/private/tmp/claude-501/-Users-drekothapoet/10540569-f0ec-48f9-8048-a3bd90f79433/scratchpad/audit/q2_proposals.md`.
R2. Strategy buckets. LLM-extracted signals get no `bucket`/`bucket_label` (extractor.py inserts without them;
    `to_bucket` only runs in the SBA parser). 156 of 270 public pension signals were "other (inferred)". Please run
    `to_bucket` on the summary and quote after extraction (or add a bucket field to XSignal), set `bucket_label` from
    the quote wording, and backfill under D-B23 (backup + dry-run count). Proposed: 30 LONG_ONLY_EQUITY,
    11 REAL_ESTATE, 10 LONG_ONLY_FIXED_INCOME, 4 PRIVATE_EQUITY, 3 PRIVATE_CREDIT, 1 DIGITAL_ASSETS,
    1 NATURAL_RESOURCES. 88 are service-provider hires or total-plan events (IPS, ALM, rebalance): please add
    non-strategy values for those.
R3. Vocabulary (buckets.py): accept hyphenated large-/mid-/small-cap. "growth equity" wrongly catches "large cap growth
    equity" (gives PRIVATE_EQUITY). "cash" should match only as an asset class. "fixed income" currently wins over a
    named real estate commitment. Add international / index fund / S&P / passive / SMID → LONG_ONLY_EQUITY;
    multi-sector credit / core plus / short or ultrashort duration / bank loans / TIPS → LONG_ONLY_FIXED_INCOME;
    bitcoin / IBIT / digital asset → DIGITAL_ASSETS; add a long-only convertibles bucket.
R4. SBA consultant_engagements: these rows are per-deal and name the deal's manager or servicer, not a consultant:
    Invesco (0034d712, c25783f2, c40a8d4f, 0f65d7b4), Stockbridge (0e69e05d, d44424c9, 200bae68, 05942491),
    Heitman (7a636739, 527196c6, 11c30798, 2ea21301), MetLife (c5df1cd8, 39a8a628), SitusAMC (76e483bf, 91934beb).
    Please add a role (per-deal consultant vs manager) or drop them. Also add to consultant_aliases.yaml:
    `not_consultant: [Stockbridge, Heitman, Invesco, MetLife, Metlife, SitusAMC]` (PRODUCT reads this list now), and map
    "Cambridge" → "Cambridge Associates". Display today hides the self-manager rows; Stockbridge waits on this list.
R5. SBA hedge fund buckets: 5f35b8f4 Brevan Howard → GLOBAL_MACRO; cdbd33ab Systematica → QUANT_SYSTEMATIC;
    58881978 Pinnacle Natural Resources → a commodities hedge fund bucket. Keep hf_relevant true. (Partly outside
    knowledge; confirm on the page.)
R6. E&F store: (a) `ef_other_investments.fmv` is empty in all 1,191 rows; the 990-PF Part II line 13 statement has an
    EOY FMV column under a different tag than `EndOfYearFMVAmt`. (b) Store the 990-PF line 13 book value
    (`OtherInvestmentsEOYAmt`). (c) Store the Schedule D Part VII valuation method. (d) 990-PF lines 10a–10c: keep 0
    distinct from blank (now `sum(...) or None`). (e) Holland & Knight on the DeLuca TY2024 990-PF is a law firm: set
    investment=False. Mark PNC Bank (BCBS FL Foundation) as bank/custodian.
R7. Spot check: the How it works scorecard reads the latest line in this exact form:
    `SPOT-CHECK <YYYY-MM-DD>: <n> signals checked, <k> correct, accuracy <pct>%, method <...>`
    None is recorded yet, so the scorecard shows "No spot check recorded yet".

### 2026-11-11 14:25 PRODUCT NOTE
1. Decision ids: main already has D-B21 (names kept), D-B22 (no contact details or pay), D-B23 (backup + dry run).
   The `data` commit 2378f7b calls its change D-B22. Please renumber it to D-B24 (or later) in the commit message going
   forward and in DECISIONS.md.
2. Overlap: PRODUCT changed `src/radar/coverage.py` (future-dated documents never make coverage recent; `_newest`) and
   `src/radar/queries.py` (`allocator_table`: newest_doc_date excludes future dates, upcoming_doc_date, consultant
   display via `shown_engagements`). DATA has uncommitted edits to both. I'll resolve the conflicts at merge time, but
   please keep your changes there small and say what they do in your DONE note.
3. PRODUCT is ready to merge (branch `product`, pushed). Waiting for "DATA DONE" before merging data, then product,
   into main and deploying.

### 2026-11-11 14:15 DATA ANSWER
Answers to PRODUCT's requests (branch `data`, head 5e9fcee; decisions D-B24..D-B34 in DECISIONS.md on the branch).
- **Renumbering.** Done. My decisions are D-B24..D-B34. Commit 2378f7b's message still says "D-B22" (already committed, not
  rewritten); the code and DECISIONS.md say D-B24.
- **R1 manual_tasks.yaml.** Your shape is adopted: `id, priority, allocator_id, allocator, what, url, file_name, drop_folder,
  why_manual, note, status` (open | received | ingested | dropped), plus an optional `found_by`. 96 tasks: open P1 12,
  P2 51, P3 21; the rest are dropped (resourcecenters.com per D-B06, login-only, not published). Your upload box may set
  `received`; I'll ingest those and set `ingested`.
- **R2 strategy buckets (D-B30).** The extractor now sets `bucket` and `bucket_label` (`buckets.signal_bucket`). Non-strategy
  values: `SERVICE_PROVIDER` and `TOTAL_PLAN`. `bucket_label` is the source's own matched words, set only when they're in
  the quote, so a NULL label still means "inferred". Backfill (backup `radar.2026-11-11-1242-pre-buckets.duckdb`, dry
  run 246 rows): "other (inferred)" fell from about 194 to 27. Rows a person reviewed were not touched.
- **R3 vocabulary.** All requested changes are in. I also fixed four false positives: "re:" matched real estate, "joint
  venture" matched venture, "trend" matched managed futures, and "board insurance" matched insurance-linked.
  New bucket: `LONG_ONLY_CONVERTIBLES`.
- **R4 SBA consultant rows (D-B31).** New column `consultant_engagements.role` = `consultant` | `per_deal_manager`. All 16
  ids you listed are `per_deal_manager`. `data/consultant_aliases.yaml` now has
  `not_consultant: [Stockbridge, Heitman, Invesco, MetLife, Metlife, SitusAMC]`, and `Cambridge` maps to Cambridge
  Associates (every bare "Cambridge" is the SBA per-deal column).
- **R5 SBA hedge fund buckets.** The cited page prints only "Hedge Fund". Pinnacle Natural Resources → `COMMODITIES`
  (refined by the printed fund name, D-A41; hf_relevant stays true). Brevan Howard and Systematica stay `MULTI_STRATEGY`:
  outside knowledge doesn't set a bucket.
- **R6 E&F store (D-B28/29).**
  - (a) `fmv` is now filled: the 990-PF tag is `EOYFMVAmt`. 1,028 of 1,191 lines have it; Schedule D lines carry book
    value only.
  - (b) New column `ef_filings.investments_other_book`.
  - (c) New column `ef_other_investments.valuation` (Schedule D `MethodValuationCd`, or the 990-PF cost/FMV flag).
  - (d) A printed 0 stays 0. Also fixed: US government obligations FMV (`USGovtObligationsEOYFMVAmt`) was never read.
  - (e) New columns `ef_contractors.contractor_class` (manager | consultant | custodian | bank | law_firm | other) and
    `class_basis`. Holland & Knight = law_firm; PNC Bank = bank. Only managers and consultants feed the added/dropped
    signals.
  - Also new: `ef_officers` (names and titles only), `ef_filings.endowment_eoy` (Schedule D Part V line 1g),
    `ef_exposure` (hedge fund / alternatives $ and % of investments per filing, or `not_disclosed`, cited), and
    `ef_other_investments.value_status` (`not_stated` for $0–$100 placeholders; never rank them), plus
    `ef_orgs.signal_note` (why an organization has no change signal).
- **R7 spot check.** Recorded below in your exact format.
- **Overlap (coverage.py / queries.py).** I reverted my edits to both files; your versions stand. On the data side only:
  a view `document_dates` (meeting_date, retrieved_on, as_of_date, upcoming_when_retrieved), and the extractor dates
  holdings, officers and facts read off an upcoming agenda to the read date (D-B25).
- **Registry.** `registry/florida.yaml` on `data` adds two Pompano pages (`/upcoming-agenda/`, `/rfp-for-legal-services/`).
  The live approval is bound to the branch's file, so until `data` is merged the main checkout's crawler would refuse
  (Touchpoint 1). Merge `data` before running any crawl from main.
- **Config.** New `config/ef_firms.yaml`; `config/sources.yaml` adds EF-UCF2 (theucffoundation.org).
- **New E&F kind.** `HOSPITAL_SYSTEM` (label "Hospital system", D-B32). `KIND_LABEL` is updated in universe.py.

### 2026-11-11 14:15 DATA SPOT-CHECK
SPOT-CHECK 2026-11-11: 20 signals checked, 16 correct, accuracy 80%, method random sample of published signals (15 pension, 5 endowment) from the live stores; an independent subagent checked each summary, date, amount and type against the cited page (stored text, plus the live public PDF for 10 of 15 pension items and both committee PDFs; 990 items against allowed ProPublica pages); 1 wrong (Miramar PNC custodian selection filed under the Police plan but from the Consolidated plan's minutes), 3 doubtful on type (Hollywood 2026-07-28, Pembroke Pines 2024-10-17, FAU IPS item); all four sent back to review with notes; amounts and dates were 20/20

### 2026-11-11 14:15 DATA READY 2026-11-11T14:15
`data/radar.duckdb` and `data/ef.duckdb` are ready to copy.
- **Backups:** `data/backups/*2026-11-11-*` (pre-data, pre-datebackfill, pre-buckets, pre-wallcleanup, ef pre-reparse,
  pre-hospitals).
- **Wall:** `radar wall` passes. Facebook widget URLs are now refused and logged without the URL, link titles are
  scrubbed, and the wall's audit row no longer echoes matched text. Nothing was loosened.
- **Funnel:** `data/funnel/2026-11-11-data-done.json`. Main table 35 → 37; in-scope plans with agenda/minutes ≤90 days
  20 → 26; manager searches ≤90 days 5 → 8; stale 18 → 16.
- **DEPLOYING (3, all refreshed):**
  - Pompano P&F 2026-11-16: agenda, fee discussions with private credit managers Bracco and Constitution Capital, the
    outcome of the Oct 19 interviews.
  - Vero Beach Police 2026-11-04: Cohen & Steers real estate presentation; the same agenda has a large cap value
    analysis (Dana vs DRZ / Nuveen).
  - Hollywood ERF 2026-09-22: "Private Credit: Brightwood Presentation".
- **PREPARING (4, unchanged):**
  - Jacksonville P&F 2026-10-23
  - Sarasota Fire 2026-10-21 (gap plan)
  - FL SBA 2026-06-30
  - South Broward Hospital District 2026-06-24
- **E&F:** 59 organizations (48 + 11 hospital systems). UCF Foundation is DEPLOYING: its board page lists OCIO Selection
  Committee meetings on 2026-11-19 and 2026-12-16 (signal efc-ab875927d72ef2c38bed; please give it a look).

### 2026-11-11 14:15 DATA DONE 2026-11-11T14:15
DATA DONE. Branch `data` at 5e9fcee: tests pass (134), wall passes. It merges main 77d882b. Summary for the owner is in
the session report.
- **Not done:**
  - Harry-Anna's new 990-PF (FYE 2025-03, filed 2026-02) is not yet in the IRS TEOS indexes.
  - (Resolved, for the record) The IRS 2025 index names batch 05A for returns filed after that batch was cut. A
    sibling-batch fallback now loads them; Invisible Hand and Tampa Bay CF TY2023 are in.
  - Audited-financial PDFs are recorded as E&F sources (`ef_documents`) but not read for figures yet.
  - The checker suggests possible missing signals in the Dunedin minutes (Harrison Street commitment, Barings
    redemption).
  - The Pompano symposium minutes date events by the document start date (May 6) rather than the event day (May 8).

### 2026-11-11 15:00 PRODUCT DONE
Merged `data` (5e9fcee), then `product`, into main. One trivial conflict in tests/test_ef.py (same insert; kept DATA's
version). main = 818a520, pushed. Deployed to allocator.amagna.co, Cloudflare version 8f311199-ed5b-4ea9-8b8e-427b235d74dd.
199 pages built from DB copies taken 14:07; 155 tests pass; wall-static ok.
Live check: / /ef /ef-hf /how return 200; /tasks /review /export.csv /registry return 404; hedge fund strip = 3 SBA hedge
fund commitments + FIU "HEDGE FUNDS" line, with 0 law firms, banks or custodians; "Hedge fund buyers" renders; no "[name]";
the footer reads "No emails, phone numbers or addresses are stored."
Wall change (PRODUCT): static check treats table cells and list items as separate text. A pay amount followed by
"Park Place International LLC" had read as a street address. Text within one cell is still checked whole.
