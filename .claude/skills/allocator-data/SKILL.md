---
name: allocator-data
description: Rules for Allocator Radar's DATA work (crawl, parse, extract, classify, verify, write the databases). Covers the contact policy, the signal definitions, the verification step and the single-writer and backup rules. Load before changing signals, contacts, timelines or E&F data in ~/dev/allocator-radar.
---

# allocator-data: how the DATA session works

Read `DECISIONS.md` and the HANDOFF.md log first (main checkout, absolute path; append only). Work in the
`../allocator-radar-data` worktree on branch `data`, with `RADAR_DATA_DIR=~/dev/allocator-radar/data`. For browser
collection, load the `data-scrape-ar` skill.

## Writing the stores
- The DATA session is the only writer to `data/radar.duckdb` and `data/ef.duckdb`. Subagents never open them; they write
  to `data/staging/` and the DATA session ingests.
- Before any multi-row change: a dated backup in `data/backups/` taken through a DuckDB connection (`COPY FROM DATABASE`),
  a dry-run count, then the change, then an `audit_log` row. Record both in HANDOFF.md.
- Never run `radar ef-signals` casually: it deletes and rebuilds every 990 signal, which resets review and verification
  state. Recompute derived tables (e.g. `ef_exposure`) directly instead.

## Contact policy (D-B35, owner 2026-11-11)
- Store names, titles, emails, phones and office addresses of officers, trustees, staff, consultants and managers
  wherever a source we can lawfully reach shows them. Cite document and page for each (the `contacts` table).
- Never store individual compensation. Lines next to a pay amount are skipped.
- Person social-profile URLs stay removed.
- Sources: anything reachable without breaking in (a login we own is fine). Never defeat a CAPTCHA or anti-bot wall;
  blocked items go to `data/manual_tasks.yaml`.
- Recover contacts from documents on disk (`radar.contacts.recover()`); don't re-crawl for them.

## Signal definitions (D-B36, enforced in `radar.extract.definitions`)
1. A presentation or agenda item is a search only if its own words say search, candidate, finalist, interview, RFP,
   replacement, selection, new manager, or fee/contract negotiation for named candidates. Otherwise it is
   `MANAGER_PRESENTATION` (no tier).
2. Look the firm up in the plan's holdings dated before the signal. Already held: a single-firm search →
   `INCUMBENT_UPDATE`; an award that is a retention or re-up → `COMMITMENT_MADE`/`re_up`.
3. Service providers (auditor, attorney, actuary, custodian, investment consultant, OCIO) never drive a manager tier.
4. Hand-entered (curated) signals carry origin `CURATED` and pass the extractor's validators (`radar add-signal`).
5. Meeting calendars and public notices count as PREPARING at most until an agenda or minutes is read.
6. 990-only E&F signals stay WATCHING at most; DEPLOYING or PREPARING needs a dated board or committee document.

## Verification step (every run that publishes)
- Every published or qualifying signal is checked by verifier subagents that did not extract it. They see only the signal
  plus its cited page (or the cited 990 lines, including the prior year's for added/dropped claims) and its same-day
  siblings.
- The six checks: quote on page; summary within the quote; type fits its definition; right allocator; date, amount and
  firm match; not a duplicate of the same meeting.
- Store `verification_status` (verified | verified_after_fix | failed) and the reason. Failed published signals go back to
  review (`review_status = 'new'`).
- Duplicate pairs: keep one, not zero.
- Then spot-check 40 yourself and record "N of 40" in HANDOFF.md. Target 38 or more.
- Brief: `data/staging/verify/VERIFIER_BRIEF.md`. Use a cheaper model for verifiers.

## Known traps
- Shared city or consolidated-plan documents (Miramar's Consolidated Retirement Plan, North Palm Beach P&F vs General)
  get attributed to the wrong plan. Check the plan named on the page.
- Wait on jobs by PID (`kill -0`), never with `pgrep -f <pattern>` in a loop: the loop matches itself.
- Dates parsed from upload paths can be wrong (e.g. `/2026/03/06.Minutes-June-2026.pdf` dated 2026-03-06).
