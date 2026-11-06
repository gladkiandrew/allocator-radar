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
