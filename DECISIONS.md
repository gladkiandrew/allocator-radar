# DECISIONS.md — Allocator Radar build log

The build follows `docs/BRIEF.md` (the product brief). `docs/AGENDA.md` v0.2 is the research
appendix. Its source inventory (§2) and legal posture (§7) are approved and unchanged. Where the
two disagree, the brief wins. This file records every choice made without asking. "Ask" items
are the irreversible ones, which went to the repo owner.

## Decisions taken from AGENDA §10 (given in the brief)

| # | Question | Decision |
|---|---|---|
| Q3 | Raw-document posture | Raw PDFs kept **unredacted + encrypted** in the lake; every derived layer scrubbed |
| Q5 | Location granularity | City / state / country only |
| Q4 | Strategy buckets | §4.3 enum accepted |
| Q6 | Frontier API | Claude via the Anthropic API for extraction, adjudication, summary lint. Public documents only |
| Q7 | History depth | 5 years |
| Q2 | Export | CSV only |
| Q10 | Trade press | RSS headlines only |
| Q11 | Reviewer | Repo owner |
| Q12 | Firm posture | `config/firm_posture.yaml`, default `both` (strictest) |
| Q13 | Manual sources | In-app manual fact entry (Touchpoint 4) |
| Q14 | Public-records requests | In-app tracker; a person files outside the system (Touchpoint 5) |
| Q15 | Bot-walled public PDFs | Human downloads into `inbox/manual/`; MANUAL provenance |
| Q17 | Repo | `~/dev/allocator-radar` |
| — | Scope | Florida only. No NY sources. No Legistar-token question (Q9 dropped) |

## Build-time decisions (2026-11-06)

- **D-A01 Repo is local only.** `git init` at `~/dev/allocator-radar`. No GitHub remote has been
  created, because publishing is outward-facing and hard to reverse. When ready:
  `gh repo create allocator-radar --private --source . --push`.
- **D-A02 Package path.** The brief names `src/llm/provider.py`. The code lives at
  `src/radar/llm/provider.py`, because a top-level `llm` package would collide with the PyPI
  `llm` package. It is still the only provider adapter.
- **D-A03 Model and effort live in config.** `RADAR_LLM_MODEL` defaults to `claude-opus-5-5`
  and `RADAR_LLM_EFFORT` to `medium`. Server-side refusal fallback is on (`fallbacks: "default"`),
  so a declined request reruns on a fallback model instead of failing. The provider re-checks
  every prompt with the contact detector and refuses to send anything that matches.
- **D-A04 Lake encryption.** Fernet (AES-128-CBC + HMAC-SHA256). The key file is
  `~/.config/allocator-radar/lake.key` (0600, created on first use) or `RADAR_LAKE_KEY`.
  **Losing the key loses the raw evidence copies.** The documents themselves can be re-fetched.
  Back the key up offline.
- **D-A05 Robots tokens.** Robots rules are honored for our own UA token (`AllocatorRadar`). They
  are also honored for the Anthropic agent tokens (`ClaudeBot`, `anthropic-ai`, `Claude-User`,
  `Claude-SearchBot`), because fetched text is sent to Claude. If any of these tokens is disallowed
  for a path, the path is disallowed. This is stricter than literal robots.
- **D-A06 Declared UA has no personal contact string.** It reads
  `AllocatorRadar/0.1 (public-records research; GET-only; honors robots.txt)`. SEC-style hosts that
  require an email in the UA are out of scope for Phase A.
- **D-A07 Per-host delay.** The delay is the largest of three values: 10 s, the robots
  Crawl-delay, and the source's `crawl_delay`. robots.txt itself is fetched once per origin per run.
  A 401 or 403 on robots.txt means the whole site is disallowed. A 404 means no restrictions. An
  unreachable robots.txt means the host is refused for that run.
- **D-A08 Bot walls.** A 403 response or a challenge page raises `BotWall`. The crawler stops that
  host and the registry marks it MANUAL. There is no retry path that changes identity.
- **D-A09 Egress log never stores a mailto/tel target.** A refused non-http URL is logged as
  `[mailto link refused]`.
- **D-A10 STALE means no new document fetched for 120 days.** "New" means a sha256 we had not
  seen before, measured by `first_seen`. Tiers are checked in order DEPLOYING > PREPARING >
  WATCHING > STALE. An allocator with a qualifying signal keeps its signal tier even if coverage
  is stale; the row then also shows a "coverage stale" note.
- **D-A11 TARGET_ALLOCATION_CHANGED counts toward PREPARING only with qualifier `increase`.**
  The brief says "target allocation raised".
- **D-A12 Future-dated signals never count.** A signal's `event_date` must fall in
  [today − window, today].
- **D-A13 Lint strictness.** The base list is AGENDA §4.7. Any posture other than `none` adds
  predictive and promotional words (likely, poised, opportunity, prospect, pitch, …).
- **D-A14 Scrubber.** Regex always runs, then GLiNER (`urchade/gliner_multi_pii-v1`) with
  contact-only labels: email, phone, fax, street and postal address. **Person names are not
  redacted**, because officers are recorded as printed. Over-redaction is accepted: a street-shaped
  place name can be removed. GLiNER can be turned off with `RADAR_FLAG_GLINER=false`; tests run
  with it off except `make wall`. The model is downloaded once from the Hugging Face Hub, with
  telemetry off.
- **D-A15 DuckDB concurrency.** Connections are short-lived and retry while another process holds
  the lock. The crawler never holds a connection while it waits on the network.
- **D-A16 htmx is vendored** under `src/radar/web/static/`. The page loads no third-party script.
- **D-A17 Deferred behind flags (not built):** vision fallback, Presidio, two-family
  adjudication, Docling tables.
- **D-A18 Wall sweep and URL columns.** In URL columns, a digit run in a filename is not treated
  as a phone number. Email, mailto and social-profile patterns are still checked there.
- **D-A19 LLM runs on the owner's Claude subscription (owner's instruction, 2026-11-06).** The
  default backend is `claude_code`: headless `claude -p` through the one provider adapter.
  Structured output uses `--json-schema` and is validated against the same Pydantic schema. The
  call runs in an empty directory with `--tools ""`, `--strict-mcp-config`, `--setting-sources ""`
  and `--no-session-persistence`. `ANTHROPIC_API_KEY` and `ANTHROPIC_AUTH_TOKEN` are stripped from
  its environment, so it can never fall through to API billing. The `anthropic` (API key) backend
  is kept but is not the default. `--bare` is not used, because it forces API-key auth. Each call
  writes a `POST api.anthropic.com` egress row (purpose `llm`) and an audit row with tokens and the
  list-price estimate the CLI reports. Usage counts against the plan's limits, so extraction runs
  sequentially. The same pattern runs on the VPS (see `deploy/VPS.md`).
- **D-A20 Quote-support validator.** It is not enough for the quote to appear on the cited page.
  The summary's specific names and numbers must also appear in the quote, or the row is dropped.
  The cause was a summary about an audit engagement that cited a quote about a legal-services
  contract. The extractor (v0.2.0) is also told to quote the words that establish the fact.
- **D-A21 Which plan a document belongs to, on shared city sites.** First, a plan named in the
  link text or filename. Second, the plan whose listing page it was found on. Third, every owner
  of that page. A city listing page that names only a different plan kind is never crawled for
  that plan. Example: the Police board's page filed under the Firefighters' plan.
- **D-A22 Renderer (T-AIRO).** The renderer is Playwright Chromium, headless, with our declared UA
  and no stealth. Every browser request is intercepted. Non-GET requests are aborted and logged as
  refused. Images, fonts and media are skipped. Subresource GETs to the site's CDN and app hosts
  are allowed for rendering only and are logged, and nothing they return is stored.
- **D-A23 Retroactive validator cleanup.** When a new validator lands, unreviewed machine rows
  that fail it are deleted, and each deletion is recorded in `audit_log`. Reviewed rows (approved
  or rejected) are never touched by code.
- **D-A24 Search.** LanceDB holds scrubbed page text only. Search fuses BM25 full-text results and
  local `BAAI/bge-small-en-v1.5` embedding results with reciprocal-rank fusion. The vector-store
  sweep is part of the nightly wall and cannot be skipped silently.
- **D-A25 Known limitation.** A document that belongs to several plans (a combined 112.664 report)
  is extracted once and attributed to one of them. The fix is per-(document, plan) extraction,
  planned for Phase B.
- **D-A26 Owner auto-approve policy (owner's instruction, 2026-11-06).** The owner asked for all
  signals to be approved without manual clicks. `config/review_policy.yaml` turns on
  auto-approval for signals, manual facts and registry versions. Every approval is stamped
  `auto-approve (owner policy 2026-11-06)` in `signals.reviewed_by` and in `audit_log`, never as if
  a person clicked. Every machine validator still runs first. The review page stays open for
  after-the-fact reject or correct. Turn it off with `auto_approve: false`. Accepted trade-off: an
  extraction error can reach the main table and exports without a person seeing it first.
- **D-A27 frs.fl.gov / employer.frs.fl.gov are MANUAL.** The Akamai edge stalls requests that
  carry our declared UA (an HTTP/2 stream reset or read timeout), while a browser gets through. That
  is a bot wall in effect, and we never change identity to pass it. The statewide DMS data on those
  hosts (Appendix A plan list, the fact sheets, the annual report) enters only through
  `inbox/manual/statewide/`. The DMS pages themselves (dms.myflorida.com) render fine.
- **D-A28 The Census ASPP unit file is a 67-plan Florida sample, not the full list.** It is
  allowlisted (F9) but does not replace the DMS plan list for Phase B.
- **D-A29 Deploy.** A single Docker image runs both the app and the nightly worker, and includes
  the Claude Code CLI. In containers, subscription auth comes from `CLAUDE_CODE_OAUTH_TOKEN`
  (`claude setup-token`). Compose binds the app to 127.0.0.1 with basic auth on. The lake key is
  mounted read-only from `./secrets/`.
- **D-A30 Public-repo hygiene.** No person's or employer's name appears in the code, config or
  docs. The license is left for the owner to choose before the repo goes public.

## Phase A lock-in (owner's instructions, 2026-11-06)

- **D-A31 No new allocators until lock-in is done.** The 14 plans located by the research pass are
  parked under `parked_plans:` in `registry/seed_florida.yaml`, not deleted. Registry approval now
  removes allocators that are no longer in the registry, along with their derived rows. Raw documents
  stay in the lake.
- **D-A32 Signal review is back with the owner.** Signal auto-approve is off. All 54 signals the
  policy had approved were returned to the queue, and that change is recorded in `audit_log`.
  Registry auto-approval stays on.
- **D-A33 Depth targets (crawler).** Per plan: every agenda, minutes document and RFP from the last
  18 months; the two newest IPS, quarterly report, actuarial valuation, audited financials and 112.664
  disclosure; and up to 8 unclassified documents. Capped at 150 per run. Every document link seen is
  recorded in `document_links`, so coverage can tell "listed but not fetched" from "not published".
- **D-A34 Document kind and date.** A document's link label decides its type. Page text decides only
  when the link says nothing useful (OTHER). Minutes routinely discuss valuations and IPSs, so page
  text alone would mislabel them. The date is the link date, or else the first date on page 1.
- **D-A35 Coverage %** is the number of checklist items held divided by the items the plan publishes.
  Partial items count by fraction. "Not published" means not found on the plan's approved listing
  pages, and is excluded from the denominator.
- **D-A36 Plan facts (extractor v0.3.0).** These cover total assets, target and actual allocation by
  asset class (with ranges), assumed and actual return, actuary, custodian, auditor, investment
  consultant, and next meeting. Each fact needs a verbatim quote that contains its label or number.
  Report-type documents also get their fact pages read, up to 15 pages per document. When a report
  prints the same label twice (summary page vs detail page), the brief shows the more precise figure.
- **D-A37 Main-table bar** (`config/main_table.yaml`): at least 3 documents, at least one dated in the
  last 183 days, and a consultant identified. Plans below the bar, and MANUAL plans, are listed on
  `/gaps` with reasons.
- **D-A38 Gaps that need no further work during lock-in:**
  - Davie Firefighters: robots.txt disallows the CivicPlus host.
  - Jupiter Police: newer agendas are on a CivicClerk portal that robots.txt disallows, and its
    AgendaCenter stops at 2024-08.
  - North Miami Beach P&F: no plan page found on citynmb.com, and finding new sites is paused.
  - FL SBA: no files in the manual inbox yet.

## FL SBA via the manual inbox (owner downloaded 11 PDFs, 2026-11-06)

- **D-A39 Provenance.** Each file is stored as MANUAL with `retrieved_by = human`, retrieved on
  2026-11-06. The URL is recorded as `manual://fl-sba-frs/<file>`, and the listing note says
  "sbafla.com, downloaded by hand". **The exact sbafla.com URLs are not recorded**: SBA uses
  `/media/<id>/<slug>.pdf` paths whose ids cannot be inferred from a file name, and we never fetch
  that host. Citations open our encrypted cached copy at the right page.
- **D-A40 Deterministic table parser (no LLM)** for the ruled New Investments / New Manager tables
  and the Q1 2026 SI table. Every cell is scrubbed, and each row's key cells must appear in that
  page's scrubbed text. A "New Manager? = Yes" row becomes MANDATE_AWARDED (qualifier manager);
  every other row becomes COMMITMENT_MADE. **The event date is the report's quarter-end**, because
  the reports print no per-row dates. The review note says so.
- **D-A41 Strategy buckets.** The printed asset class decides first (PE, RE, active credit, equity).
  Style and fund name only refine Strategic Investments and hedge-fund rows. The hedge-fund flag
  (`signals.hf_relevant`) covers hedge fund, absolute return, diversifying, managed futures and
  multi-strategy, plus the standard hedge-fund strategy buckets. Insurance-linked is **not** flagged.
  Fund names never set the flag ("Opportunities Fund" is not a strategy).
- **D-A42 Lint ignores proper names quoted from the source**, so a fund named "... Opportunities"
  can still be summarized. Our own wording is linted as before.
- **D-A43 Foreign-currency commitments are never converted.** The amount appears in the summary as
  printed (e.g. €165,000,000), and `amount_usd` stays empty.
- **D-A44 Consultant column.** The SBA's per-row "Consultant" column is recorded as printed, as
  cited consultant engagements. For some real-estate separate accounts it names the manager
  itself (Heitman, Invesco, Stockbridge).

## Phase A wrap-up (owner's instructions, 2026-11-06)

- **D-A45 Tiered auto-approval** (`config/auto_approval.yaml`, rule `auto-approve-v1`). A pending
  signal becomes `auto_approved`, a status distinct from a person's `approved`, only if all of these
  hold:
  - (a) its type is not MANAGER_TERMINATED, MANAGER_ON_WATCH, REDEMPTION_APPROVED or WATCH_REMOVED;
  - (b) it came from the deterministic table parser, or a quote matches its cited page after
    normalization;
  - (c) its date basis is source-native, meaning the cited document's own date or a date printed on
    the cited page;
  - (d) its summary passes the lint.

  Every decision, approve or leave pending, is logged in `audit_log` with the rule version and the
  reason. Auto-approved signals set tiers. **Export still requires a person's approval**, because the
  exporter reads `approved` only. The earlier owner policy (D-A26) is superseded for signals.
- **D-A46 A quarter-end fallback does not satisfy rule (c).** It is a reporting period, not a meeting,
  report or filing date, so those signals stay pending. Adding `quarter_end` to
  `qualifying_date_bases` would change that.
- **D-A47 SBA dating.** Rule: use the publication date or the board/IAC meeting date if either is
  printed, otherwise the quarter-end. None of the 5 New Investments / New Manager reports prints a
  publication or meeting date, and the PDFs carry no date metadata. All 115 rows are therefore
  dated by quarter-end and recorded with `date_basis = quarter_end`.
- **D-A48 Readiness rules.** PREPARING now also includes MANDATE_AWARDED (manager hires only, the same
  qualifier as DEPLOYING) and COMMITMENT_MADE within 180 days. Nothing else changed: DEPLOYING
  searches, RFPs and finalists still count only for manager hires.
- **D-A49 Search scan.** This is a separate extraction pass, versioned `search-scan-1`, over each
  main-table plan's agendas, minutes and RFP documents dated in the last 12 months. It reads pages
  matching a search vocabulary (up to 20 per document), and its prompt focuses on manager and
  consultant searches.
- **D-A50 Auto-approval v2 (owner, 2026-11-06).** `quarter_end` qualifies as a date basis only for
  signals extracted deterministically from a parsed table. LLM-extracted signals still need a
  meeting, report or filing date. The re-check now ignores printed table cells when linting (D-A42),
  which fixed 2 SBA rows whose fund names contain "Opportunity" or "Opportunities".
- **D-A51 Strategy filter.** The main table can be filtered to a hedge-fund preset (hedge fund,
  diversifying, managed futures, multi-strategy, quant, absolute return) or to a single bucket.
  For LLM-extracted signals the bucket is **inferred from the summary** and marked "(inferred)".
  Table-parsed signals carry the source's own label.
- **D-A52 Phase A locked 2026-11-06.** Final state: 13 plans on the main table (1 DEPLOYING, 1
  PREPARING, 11 WATCHING) and 17 on gaps; 193 signals auto-approved and 27 pending for the owner.
  Phase B (DMS statewide list, 112.664 crawl, the parked plans) is not started.

## Phase B (started 2026-11-09)

- **D-B01 Plan discovery without the DMS list.** The statewide DMS plan list is on an Akamai-fronted
  host (D-A27) and still needs a manual download. Meanwhile four read-only research passes (one per
  region) located official plan pages. Their results are curated before import: lone PDFs,
  city-wide AgendaCenter roots with no pension section, staging hosts, frozen archives and NOT FOUND
  entries are skipped. They are imported with `radar import-seed`. The importer only adds new hosts
  to the allowlist. Blocked plans, and plans whose documents sit only on resourcecenters.com, enter
  as MANUAL so the gap stays visible.
- **D-B02 City-wide listings.** A document on a city-wide AgendaCenter or Archive page is taken only
  if its section heading or link names a pension or retirement board.
- **D-B03 The gaps page shows readiness.** A plan below the main-table bar can still carry a DEPLOYING
  or PREPARING signal. The gaps page shows that signal so the plan stays visible. The bar itself is
  unchanged.
- **D-B04 Operator note.** In zsh, an unquoted `$VAR` is not word-split. `radar pipeline --only-file`
  reads ids from a file, and one argument holding many ids is split.
- **D-B05 Gap categories.** Every gap plan gets one primary cause, in fix order: MANUAL_SOURCE,
  PROCESSING, NO_LISTING_PAGES, THIN, NOT_POSTING_RECENT, CONSULTANT_UNIDENTIFIED. Each category
  has a next action. The causes are shown on `/gaps` and printed by `radar gaps`.
- **D-B06 Resource Centers files are never ingested, even when a person downloads them.** Its
  robots.txt disallows Anthropic's agents, and we honor that because the text goes to Claude
  (D-A05). For those plans, a person enters facts by hand, or a Chapter 119 response from the city is
  ingested instead.
- **D-B07 Consultant scan (`consultant-scan-1`).** A separately versioned extraction pass for gap
  plans missing a consultant. It reads up to 4 likely documents per plan (report-type documents
  first) and up to 6 pages per document that name providers. Facts need a quote containing the firm
  name, as always.
- **D-B08 Deep discovery.** `--deep` renders every page in the browser and follows a second level of
  links, for NO_LISTING_PAGES and THIN plans. It is GET-only and checks robots.txt like normal
  discovery.
