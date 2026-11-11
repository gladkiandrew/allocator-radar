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

### 2026-11-11 16:05 PRODUCT REQUEST (Phase C close run)
PRODUCT is building the access gate, verified-only rendering, contacts (member/admin only), the timeline UI and the
E&F spot-check review. Fields PRODUCT will read; please post "DATA READY" with the real names if they differ:
R8. Verification: `signals.verification` ∈ {verified, unverified, failed} + `signals.verified_method`
    (e.g. quote_on_page, parsed_table, person) + `signals.verified_at`. Same three on `ef_signals`. Until it exists,
    PRODUCT treats as verified: review_status='approved', or auto_approved whose review_note says the quote matches
    the cited page or the row came from a parsed table. Everything else is hidden from public/member pages.
R9. Contacts: table `contacts(contact_id, allocator_id, name_as_printed, title_as_printed, email, phone, as_of,
    source_url, page)` with row_evidence kind 'contact' (or `ef_contacts` keyed by ein for E&F). Business contact
    details exactly as printed in a public document, each cited. PRODUCT renders them only for member/admin audiences,
    never in the ungated public build. Please record the owner's decision on this as a DECISIONS entry (it changes
    D-B22) and say how the wall treats these columns.
R10. Timeline: per signal `stage` (search_opened|rfp_issued|finalists_named|interviews|awarded|funded),
    `next_step` (text as stated), `next_step_date`, `mandate_size_usd`, each cited by the signal's evidence; per plan
    `next_meeting` (plan_facts NEXT_MEETING already works). PRODUCT computes tier-expiry from the rules window and
    labels it "computed". Last deployment comes from MANDATE_AWARDED/MANDATE_FUNDED/COMMITMENT_MADE (manager_name,
    amount_usd, bucket); fill those where missing.
R11. `data/ef_spotcheck_25.md`: one line per signal, `- [ ] <ef_signal_id> | <org> | <summary>`. PRODUCT's /review/ef
    view will NOT write ef.duckdb: decisions go to `data/ef_spotcheck_25.decisions.jsonl`
    ({"id","decision":"approved|rejected|skip","by","at","note"}), which DATA applies.
R12. Owner decisions on Hollywood, Vero Beach and UCF: please state them here (what each row should show), so the
    deploy check can confirm them.

### 2026-11-11 17:35 DATA NOTE
Timeline tables exist (D-B37, `radar timeline` rebuilds them; both live in `data/radar.duckdb`). Documents only; computed
fields say so in their `*_note` column.
- **`timeline_searches`** (one row per manager search, grouped by plan + asset class):
  - `allocator_id, search_no, asset_class` (bucket), `status` (open | stalled | closed; computed),
    `stage_reached` (hint | opened | finalists | interviews | fee_contract | awarded | funded)
  - one date per stage: `hint_date, opened_date, finalists_date, interviews_date, fee_contract_date, awarded_date,
    funded_date`, plus `first_date, last_date`
  - `next_step_text, next_step_date, next_step_quote, next_step_signal_id`: the next step AS STATED. Either an upcoming
    scheduled item, or a later date printed in the latest stage's own quote. NULL when the documents state none.
  - `signal_ids` (comma list), `computed_note`
- **`timeline_allocators`** (one row per plan, including WATCHING):
  - `tier, tier_signal_id, tier_expires, tier_expires_note` (computed: qualifying signal date + tier window)
  - `next_meeting_date, next_meeting_source` (an upcoming agenda on file, or a next-meeting date printed in a document)
  - `last_deploy_date, last_deploy_asset_class, last_deploy_firm, last_deploy_amount, last_deploy_summary,
    last_deploy_signal_id`
  - `typical_search_days, typical_search_note` ("computed: median of N past searches"; NULL when N < 2), `open_searches`
- Only published, not-failed signals feed it: `review_status` in (approved, auto_approved) and
  `verification_status <> 'failed'`.
- **New columns on `signals`:** `verification_status` (verified | verified_after_fix | failed), `verification_reason`,
  `verified_at`; also `ef_signals.verification_status/_reason`. Failed published signals were moved back to review.
- **New signal types (non-qualifying):** `MANAGER_PRESENTATION`, `INCUMBENT_UPDATE`. New origin value: `CURATED`.
- **UCF:** `OCIO_SEARCH` with `source_type = 'meeting_calendar'` (PREPARING at most).

### 2026-11-11 17:55 PRODUCT REQUEST
R13. Contacts precision. `contacts` (reparse-v1, 2,624 rows) isn't fit to show yet, even to members. Sampled problems:
     - names that aren't people ("Disabilities Act", "Address Card", "Military Trail", "The Board")
     - names paired with someone else's email ("Erik Dessalines" with Krodriguez@goldenbeach.us)
     - contacts unrelated to the pension plan, pulled from city agendas: a water district, FloridaDEP, tampabay.com, a
       realty firm, a hospital pharmacy, NCPERS membership
     - garbled emails ("DONNABRITO@MIAMIBEACHFL.GOVFORASSISTANCE")
     - a personal gmail
     PRODUCT now renders a contact only when DATA marks it: `contacts.verification_status` in (verified,
     verified_after_fix), meaning the name, title and detail sit together on the cited page AND the person or office
     belongs to this plan (board, trustee, plan administrator, plan staff, plan attorney/consultant/actuary for this
     plan). Until that column exists, the members-only section shows nothing. Please also drop personal-domain emails
     (gmail etc.) unless the document presents them as the plan's official contact.
R14. `data/ef_spotcheck_25.md` isn't there yet; /review/ef is ready for it.

### 2026-11-11 18:05 DATA SPOT-CHECK
SPOT-CHECK 2026-11-11: 40 signals checked, 39 correct, accuracy 97%, method 40 random published signals after the verification pass, each read by the DATA session against its cited quote and page; the miss is Pompano 2076bb3a (vote to retain the incumbent auditor CBIZ, typed MANDATE_AWARDED/service_provider: facts right, type should be a retention); separate document defect seen: Pembroke Pines "June" minutes carry doc_date 2026-03-06 from the upload path (signal date is right)

### 2026-11-11 18:05 DATA NOTE
Phase C close run (D-B35..D-B37). PRODUCT, please note:
- **Contacts policy (D-B35).** Contacts are now stored, and `pages.text_scrubbed` holds unscrubbed text (social
  profiles still removed).
  - Your static wall (`wall-static`) and the site footer ("No emails, phone numbers or addresses are stored") need
    updating before the next deploy. Otherwise the wall will fail, or the footer will be wrong.
  - New table `contacts`: `contact_id, allocator_id, name, title, email, phone, office_address, role_type,
    document_sha256, page, source_url, context, method, extracted_at`. E&F officers are in `ef.duckdb` `ef_officers`
    (names and titles).
- **Verification.** 370 published signals plus 8 sampled were checked by independent verifiers. 324 verified (86.2% of
  published, including 4 owner-decided rows verified after an evidence fix); 54 failed and went back to review with
  their reasons. The main causes:
  - Miramar Consolidated-plan minutes attributed to the individual Miramar plans (about 9);
  - same-meeting duplicates;
  - IPS "amended" read from a status line;
  - 990 consultant firms tagged manager.
  - Filter the site on `verification_status <> 'failed'` (failed rows are already out of approved status).
- **Tiers.** DEPLOYING is still Pompano (Nov 16), Vero Beach (Nov 4, now the large cap value search) and Hollywood.
  Hollywood falls back to its Aug 20 Emerging Manager Selection signal, so it expires about Nov 18 per owner decision
  1a. PREPARING is unchanged (4).
  - E&F: UCF is now PREPARING (`OCIO_SEARCH`, meeting calendar). USF is PREPARING: its public notice lists a special
    Investment Committee meeting on Oct 9, 2026 "to receive information from prospective investment managers"
    (`meeting_calendar`). No E&F organization is DEPLOYING.
- **Display items that are yours:**
  - The "100% alternatives" share isn't in the data: no filing's `ef_exposure.alt_pct` reaches 99.5. Blank public lines
    now give no share at all.
  - The 990-PF basis label and the "990HF" label are display strings.
  - UM's endowment is `ef_filings.endowment_eoy` ($1.59B, TY2023); total assets are separate.
- **Officers.** Cleanup done: 5 "X X" vote-tally artifacts stripped, 158 rows without a surname dropped, 1,003 duplicate
  rows merged (evidence re-pointed to the kept row, none lost). 934 officer rows remain.

### 2026-11-11 18:05 DATA NOTE: Phase C status (four done-when criteria)
1. **48 organizations + 10 hospital systems parsed: MET.** 48 + 11 hospital systems: all 990/990-PF returns re-read,
   with officers, endowment (Schedule D Part V), exposure and contractor classes.
2. **Hedge fund buyers cited: MET.** 10 organizations have a hedge fund figure (dollars and % of investments, tax year,
   cited to Schedule D Part VII or the 990-PF Part II line 13 statement); 29 have alternatives figures; the rest say
   "not disclosed".
3. **5+ committee docs: MET, with a caveat.** Six organizations have a dated committee or board document read and cited:
   - FAU Foundation (Investment Committee agendas and minutes)
   - UWF Foundation (Executive Committee minutes)
   - USF Foundation (Aug–Nov 2026 meeting notice)
   - UCF Foundation (board meeting calendar)
   - Broward College Foundation (2025-02-26 Finance & Investment agenda)
   - UF Foundation, via the 2026-11-10 UF Board of Trustees packet with the UFICO update.

   Caveat: four of the six produced cited signals (FAU, UWF, USF, UCF); UCF's and USF's are a calendar and a notice.
   Committee packets for UCF and Broward College sit on OnBoard (robots-disallowed) and UF's on Quickbase
   (disallowed). Those, and the UCF OCIO Selection Committee papers, are priority-1 manual tasks.
4. **Owner 25-signal spot check: PENDING.** `data/ef_spotcheck_25.md` lists 25 published E&F signals (org, summary,
   cited form/line, source URL). Nothing is marked reviewed.

**Deferred (not started, per instructions):**
- reading the 21 audited-financials PDFs;
- hunting investment assets at hospital parent entities;
- contact/staff/board web pages: this run used documents on disk only.

**Still running:** the contact recovery is about 55% through the 7,069 pages. It finishes on its own; a chained step
then cleans social URLs and re-runs `radar wall` (log: `data/logs/contacts-post.log`). Contact counts below will rise
when it finishes.

### 2026-11-11 18:05 DATA READY 2026-11-11T18:05
Stores ready to copy (backups: `data/backups/*2026-11-11-17*`).
- Funnel: `data/funnel/2026-11-11-phasec-close.json`.
- `radar wall` passes; 160 tests pass.
- Contacts so far: 132 of 251 plans have a named contact with a title; 125 have an email or phone; 58 of 59 E&F
  organizations have officer names with titles.
- Timelines: 14 plans have a search timeline (8 open searches) and 14 a last deployment; 13 have a next meeting date;
  2 have a typical search length.

### 2026-11-11 18:05 DATA DONE 2026-11-11T18:05
DATA DONE. Branch `data` at 3857134 (merges main 2417818). New skill:
`.claude/skills/allocator-data/SKILL.md`.

### 2026-11-11 18:45 PRODUCT DONE (Phase C close run)
Merged data (3857134), then product, into main = 72790e0 (pushed). There were no conflicts. PRODUCT's display timeline
moved to radar/web/when.py because DATA owns radar/timeline.py. Deployed the public build to allocator.amagna.co,
version ad6dc5ef-8893-4e74-9e8c-12878bb01f90. The access gate is OFF.
Checks: 195 tests; wall-static ok on both the public and the member build.
Live: / /ef /how return 200; /review /review/ef /tasks /export.csv /login return 404. Only "verified against source"
labels show. 24 of 24 main-table rows answer "when". No contacts blocks. UCF reads "OCIO search".
Phase C on How it works: 3 of 4 met; the owner spot check at /review/ef (25 of 25 rows parsed) is the one left.
For DATA: R13 (contacts precision + verification_status on contacts) is still open. The member contacts section shows
nothing until it lands. The contacts recovery job was still running at deploy time; its output isn't in this build.
