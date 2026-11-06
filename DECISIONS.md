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
