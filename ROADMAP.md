# Allocator Radar roadmap

Florida only until Phase I. Every phase follows the standing rules: public documents only, every figure and signal
cited to a document and page (or a filing year and line), names and titles as printed (D-B21), no emails, phone
numbers, street addresses or individual compensation (D-B22), no database-wide rewrite without a dated backup and a
dry-run count (D-B23), and no outbound features.

Status key: **CURRENT** = in progress now · **NOT STARTED**

| Phase | Name | Status |
|---|---|---|
| 0 | Stabilize | **CURRENT** |
| C | Florida endowments, foundations, hospitals | **CURRENT** |
| D | All Florida local pension plans (per-platform adapters) | NOT STARTED |
| E | Corporate and union plans (DOL Form 5500) | NOT STARTED |
| F | Managers, consultants, OCIOs (SEC Form ADV, Form D, 13F) | NOT STARTED |
| G | Insurers and family offices (partial, stated honestly) | NOT STARTED |
| H | Product | NOT STARTED |
| I | National | NOT STARTED |
| J | Services | NOT STARTED |

---

## Phase 0: Stabilize (CURRENT)

**Scope.** Bring the live store, the code and the public site back to a known-good, committed state after the
2026-11-10 name-removal run and its reversal. Fix the public-page problems found since: hedge fund strip precision,
approval labels, freshness of future-dated documents, statewide copy, strategy labels, and the SBA consultant list. Set
up the two-session workflow (DATA and PRODUCT) with HANDOFF.md.

**Sources.** None new. Existing stores: `data/radar.duckdb` (pension plans), `data/ef.duckdb` (endowments and
foundations).

**Requirements.**
- `main` tagged `stable-2026-11-11`, with dated backups of both stores in `data/backups/`.
- DECISIONS.md records D-B21 (names kept), D-B22 (no contact details or pay), D-B23 (backup and dry run before rewrites).
- Approval labels on every public row show the real state: "reviewed", "auto-approved (manual source)" or "awaiting review".
- A document dated after the build date shows as "upcoming meeting <date>" and never counts toward "coverage is current".
- The hedge fund strip shows only hedge fund, absolute return, risk-mitigation, managed futures or diversifying-strategy
  rows.
- The How it works page shows a data quality scorecard computed at build time.
- Local admin pages (/tasks, a fast review queue) exist and are absent from the public build.

**Done when.**
- The full test suite and `radar wall-static dist` pass on `main`.
- The live site returns 200 for `/` and `/ef`, shows no `[name]` anywhere, and has 0 law firms, custodians, banks or
  long-only managers in the hedge fund strip (checked by hand against the source of every strip row).
- 0 public rows labeled "auto-approved" without a qualifier.
- Fewer than 10% of public pension signals display "other (inferred)" as their strategy.
- The scorecard shows four real numbers, and DATA has recorded at least one spot check in HANDOFF.md.

## Phase C: Florida endowments, foundations, hospitals (CURRENT)

**Scope.** The largest Florida 501(c)(3) investors: university and college foundations, private universities, community
foundations, private foundations, and (to add) nonprofit hospital systems and public hospital districts with investment
pools. A by-assets view and a "Hedge fund buyers" view ranked by disclosed hedge fund and alternatives exposure.

**Sources.** IRS e-file XML (TEOS batches): 990 Schedule D Part VII/VIII, Part X, Part VII Section B contractors (firm
names only); 990-PF Part II and Part VIII. ProPublica Nonprofit Explorer API for filing summaries. Committee and board
documents the organizations publish themselves (allowlisted pages). Hospital districts: board and investment committee
packets on the district's own site.

**Requirements.**
- Universe chosen by a written rule from the IRS EO BMF, saved with sources (`data/ef_universe.csv`).
- 990 signals capped at WATCHING; DEPLOYING or PREPARING needs a dated committee or board document.
- Hedge fund exposure shown in dollars and as a percent of investments, with tax year and line; "not disclosed" when absent.
- Officers, directors and trustees kept as printed; Part VII compensation never stored.

**Done when.**
- At least 48 organizations, plus the 10 largest Florida nonprofit hospital systems by total assets, each with its
  latest available 990 or 990-PF parsed, or a stated reason why not.
- "Hedge fund buyers" renders with every figure cited to form, tax year, part and line.
- At least 5 organizations have a dated committee or board document read and cited.
- A spot check of 25 E&F signals by a person scores at least 95% correct.

## Phase D: All Florida local pension plans (NOT STARTED)

**Scope.** Every Florida local government defined benefit plan (Chapter 175/185 police and fire plans, general employee
plans, special districts), statewide, not only South Florida. Built as per-platform adapters, one per document-hosting
platform (e.g. CivicPlus/CivicEngage, Granicus/Legistar, BoardDocs, Laserfiche, municipal WordPress, plan-administrator
portals), so each new plan on a known platform needs configuration only.

**Sources.** Board agendas, minutes and packets; investment consultant quarterly reports; Florida Division of
Retirement annual reports and actuarial valuations; plan IPS documents.

**Requirements.** An adapter interface with tests per platform; registry entries record their platform; failing plans go
on Coverage gaps with a cause; the manual task list covers sites that block automated fetches.

**Done when.** At least 90% of the plans in the Division of Retirement's local plan list are in the registry, at least
75% meet the main-table coverage bar, and each adapter has a recorded test against a live sample.

## Phase E: Corporate and union plans, DOL Form 5500 (NOT STARTED)

**Scope.** Florida-sponsored single-employer and multiemployer (Taft-Hartley) defined benefit plans.

**Sources.** DOL EFAST2 Form 5500 bulk datasets: Schedule H (asset breakdown, including partnership/joint venture
interests, real estate, common/collective trusts, master trusts, 103-12 entities), Schedule C (service providers and
fees), Schedule R.

**Requirements.** Plan-year citations to form, schedule and line; service-provider names as filed; no participant data.

**Done when.** Every Florida-sponsor DB plan with at least $100M in assets in the latest complete plan year is loaded,
with Schedule H allocations and Schedule C providers cited, and a 25-row spot check scores at least 95% correct.

## Phase F: Managers, consultants, OCIOs (NOT STARTED)

**Scope.** The counterparties: investment managers, consultants and OCIO providers, linked to the allocators that hire
them.

**Sources.** SEC Form ADV (Parts 1 and 2A, IAPD bulk data), Form D filings, Form 13F holdings.

**Requirements.** Firm-level records only; entity resolution between ADV names and names as printed in board documents,
with a reviewable alias file; each link to an allocator cited to the allocator's own document.

**Done when.** At least 90% of the manager and consultant names on public pages resolve to an ADV CRD number or are
marked unresolved with a reason, and each consultant page lists its Florida clients with citations.

## Phase G: Insurers and family offices (partial, stated honestly) (NOT STARTED)

**Scope.** Florida-domiciled insurers' investment portfolios, and family offices only where they file publicly.

**Sources.** NAIC statutory statements as publicly available (Schedule BA for alternatives), Florida OIR filings, Form
ADV for registered family offices, 13F where filed.

**Requirements.** Every page states plainly what isn't covered: most family offices file nothing public, and insurer
detail is partial. No inferred holdings.

**Done when.** The covered set and the uncovered set are both listed with reasons, and every figure is cited to a filing
and schedule.

## Phase H: Product (NOT STARTED)

**Scope.** Logins, saved searches and alerts, asset-class filters (private equity, real estate, private credit, hedge
funds), CSV/Excel export of approved signals with citations, and deck-to-allocator matching (match a fund's strategy
description to allocators with a cited, relevant signal).

**Requirements.** Auth before any non-public feature; export only person-approved signals; alerts are in-app only (no
outbound email or messaging in this phase unless the owner decides otherwise).

**Done when.** A signed-in user can filter by each of the four asset classes, save a search, see new matches since their
last visit, export with citations, and get a ranked, cited allocator list for an uploaded strategy description.

## Phase I: National (NOT STARTED)

**Scope.** The Phase D, E and C patterns extended beyond Florida, state by state, starting with the largest public
pension systems.

**Done when.** At least 5 additional states each meet the Phase D "done when" bar.

## Phase J: Services (NOT STARTED)

**Scope.** Paid research and data services built on the platform (custom coverage, briefings).

**Done when.** The owner defines it. Nothing is built until then.
