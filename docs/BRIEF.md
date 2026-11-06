# Product brief — Allocator Radar (v1, 2026-11-06)

> Transcribed from the owner's brief. Where this file and `docs/AGENDA.md` conflict, this file wins.
> AGENDA.md v0.2 is the research appendix: its source inventory (§2) and legal posture (§7) are approved
> and stay as-is. We are not building its 26-week plan; we are building a product that uses it.

## The product
ALLOCATOR RADAR — a web app that shows which Florida institutional allocators are getting ready to
deploy capital, with a cited reason.

One screen is the whole pitch: a table of allocators, each with a READINESS indicator and the dated,
cited evidence behind it. Click an allocator → its brief: who they are, what they hold (as disclosed),
their incumbents, their consultant, their signal timeline, the documents we watch. Every line links to
the source PDF and page. Nothing is a judgment; every indicator is explainable as "these signals, these
dates, these documents."

Audience: a hedge fund BD manager whose job is the CRM. They must understand it in 30 seconds without a
walkthrough. If it looks like a dashboard of AI output, we failed. It should look like a clean, boring,
trustworthy prospecting list.

## Scope (hard)
- Florida only. Phase A: the ~20 verified South Florida local public pension plans in AGENDA §2.4 plus
  FL SBA via the manual inbox. Phase B: every FL local plan via the DMS list (FL2) and F.S. 112.664 pages.
  Phase C: FL endowments, foundations and hospitals via 990 XML (F1/F3/F4).
- No NYC. Drop every NY source and the Legistar token question.
- No new hardware. Runs on this MacBook.
- LLM: Claude via the Anthropic API for extraction, adjudication and summary lint. ONE provider adapter
  with "anthropic" as the only implemented backend; a stub for "local" (OpenAI-compatible endpoint). No
  model-specific code anywhere else. Public documents only ever go into prompts.
- Hosting: local first (uvicorn). Designed from day one to deploy behind a login on a subdomain (Docker,
  env-configured, Postgres-ready but DuckDB by default). No firm data, ever — this is public-record data
  only and the repo will be made public later. Nothing in the codebase or data should assume or reference
  any employer.

## The Wall (unchanged from AGENDA §1.1 — enforce all four layers)
No contact details anywhere, no outbound capability, scrubber before any LLM/vector/table, nightly wall
tests + canary. Public, login-free sources only; honor robots and ToS literally; never circumvent a bot wall.

## Readiness indicator
Not a score out of 100. A tier with reasons:
- DEPLOYING: a search/RFP is open, finalists named, or a mandate was awarded in the last 90 days.
- PREPARING: target allocation raised, IPS amended, asset-liability or pacing study adopted, consultant
  changed, or incumbent placed on watch / terminated in the last 180 days.
- WATCHING: documents current, no qualifying signal in window.
- STALE: no new documents fetched in 120 days (so we know coverage, not just signals).
Each tier is derived from signal rows by a rules table (signal_type, window, tier) that lives in config and
is shown in the UI's "How this works" page. The indicator must always show the signals that produced it.
No evaluative language anywhere (reuse the §4.7 lint).

## Human touchpoints — exactly these, all inside the app
1. Registry approval (once).
2. Merge review queue (ambiguous entity matches only).
3. Signal review queue: nothing shows as DEPLOYING/PREPARING on the main screen, and nothing exports,
   until a human approves the signal. Unreviewed signals appear in a muted "pending review" state.
4. Manual fact entry for MANUAL-ONLY sources (P&I, Dahab RFPs, FL SBA, NFA BASIC): URL + date + plan +
   fact + signal type → same lint and review queue as machine signals.
5. Public-records request tracker: a human files FL Chapter 119 requests outside the system; the app tracks
   plan, request text, date filed, status, and an inbox/manual/ folder where the response is dropped for
   ingestion with MANUAL provenance. The system never sends anything.

## Config, not questions
- config/firm_posture.yaml: ria | cpo_cta | both | none (default both = strictest). Only affects export
  disclaimers and lint strictness.
- config/readiness_rules.yaml: the tier rules.
- config/sources.yaml: the FL source allowlist from AGENDA §2.3–2.4 with verdicts.
- From AGENDA §10: raw PDFs kept unredacted + encrypted, all derived layers scrubbed; city/state/country
  only; bucket enum accepted; 5-year history; CSV export only; RSS headlines only; reviewer = repo owner.

## Stack
Python 3.12 + uv · FastAPI · DuckDB · Pydantic v2 · Playwright (crawl) · Docling + pdfplumber (parse) ·
LanceDB (retrieval, hybrid) · Anthropic SDK via the adapter · server-rendered HTML (Jinja + HTMX), no SPA,
no build step, one CSS file, readable on a phone. Docker Compose for deploy. Deferred behind feature flags:
vision fallback, Presidio (regex + GLiNER is enough for v0), two-family adjudication.

## Build order — ship a usable screen every step
0. Repo, wall tests + canary + models + provider adapter + empty FastAPI app with an empty allocator table.
1. Registry: discovery agent → registry/florida.yaml for the §2.4 plans → approval screen.
2. Crawl + parse for T-WP and T-CP first. Documents page per allocator: what we fetched, when.
3. Prefilter + extraction → Mandate/Officer/Signal rows with evidence. Allocator brief with cited lines.
4. Readiness rules + main table + signal review queue. First DEPLOYING row with a real cited signal =
   the demo milestone. Stop and show the owner.
5. Manual fact entry + public-records tracker + CSV export.
6. T-LEG / T-AIRO / T-CITY templates, FL SBA inbox, remaining §2.4 plans.
7. Phase B: DMS plan list + 112.664 crawl across all FL local plans.
8. Dockerize + auth + subdomain deploy notes. README for a public repo.

**Definition of done for the demo:** open localhost, see ≥15 Florida plans, ≥2 in DEPLOYING or PREPARING
with approved, cited signals, click one, read the brief, click a citation, land on the page of the PDF.
Wall tests green.

Write DECISIONS.md as you go. Ask the owner only for irreversible choices.
