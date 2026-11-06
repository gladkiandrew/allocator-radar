# Allocator Radar

A prospecting list of Florida public pension plans. It shows which ones are getting ready to put
money to work, and the dated public document behind each one.

The main screen is one table. Each row is a plan with a readiness tier: **DEPLOYING**,
**PREPARING**, **WATCHING** or **STALE**. Next to the tier is the signal that set it, the date it
happened, and a link to the exact page of the source PDF. Click a plan to see its brief:

- what it holds, as disclosed
- its incumbent managers and its consultant
- officers and trustees, as printed
- a signal timeline
- every document we watch

Every line cites a public document and page. Nothing is an opinion.

## How a tier is set

The rules live in `config/readiness_rules.yaml` and are shown on the app's **How this works** page.

| Tier | Rule |
|---|---|
| DEPLOYING | An investment-manager search or RFP is open, finalists were named, or a mandate was awarded in the last 90 days |
| PREPARING | In the last 180 days: target allocation raised, IPS amended, asset-liability or pacing study adopted, consultant changed, or an incumbent placed on watch or terminated |
| WATCHING | A new document was fetched in the last 120 days, but no qualifying signal is in the window |
| STALE | No new document fetched in 120 days. This describes our coverage, not the plan |

Only **approved** signals count toward a tier. Dates are the ones printed in the source (the
meeting, notice or filing date), never the date we fetched the document.

## The Wall

These rules hold in every configuration.

1. **No contact details, anywhere.** No data model has an email, phone, address or social-profile
   field. People appear only as name and title, exactly as a public document prints them. Every
   piece of text passes through a scrubber (regex plus a GLiNER PII model) before it reaches the
   language model, the search index or any table. Contact-shaped text becomes `[REDACTED-CONTACT]`.
2. **No outbound capability.** The crawler sends only GET and HEAD requests, and only to hosts on
   the allowlist in `config/sources.yaml`. It honors robots.txt, including rules aimed at AI agents.
   It never submits forms or logs in. It stops at any bot wall and never works around one. There is
   no email, messaging or form library in the codebase, and a test fails the build if one appears.
3. **Public, login-free sources only.** Sources whose terms forbid automation are *manual-only*. A
   person reads them in a browser and enters a dated, cited fact by hand.
4. **Tested every night.** `radar wall` checks four things: the schema, a sweep of every stored
   text value and search-index payload, the egress log, and a canary packet of fake contact details
   run through the real pipeline. Any hit fails the run.

## Legal posture, in plain English

- **The data is public by law.** Florida plan documents (agendas, minutes, investment reports,
  investment policy statements, actuarial valuations) are public records under Florida Chapter 119
  and the Sunshine Law. F.S. 112.664 requires local plans to post financial disclosures on their
  websites.
- **We read only what anyone can read without logging in**, at polite rates (at least 10 seconds
  between requests to a host). We follow each site's robots.txt and terms literally.
- **We store facts, not reachability.** There are no contact details. Trustees and staff appear
  only by name and title, as the documents print them.
- **No evaluative language.** A lint rejects words like "strong", "underperform", "attractive" or
  "likely" in every stored summary. The output is a list of cited facts, not marketing material.
  Anything you send to a prospect is yours to write and to have compliance review.
- **Raw PDFs are evidence.** They are kept unredacted but encrypted at rest. Every derived layer
  is scrubbed.
- **Your own regulatory status** sets the export disclaimers (`config/firm_posture.yaml`:
  `ria | cpo_cta | both | none`). Outreach to public plans can trigger lobbyist-registration and
  pay-to-play rules. That is a matter for your own policy, outside this tool.

The full source inventory and legal analysis are in `docs/AGENDA.md` (§2 and §7). The product brief
is in `docs/BRIEF.md`. Every build decision is logged in `DECISIONS.md`.

## Run it locally

You need macOS or Linux, [uv](https://docs.astral.sh/uv/), and the
[Claude Code](https://claude.com/claude-code) CLI logged in to your Claude subscription
(`claude auth status`).

```bash
uv sync
uv run playwright install chromium     # only for JavaScript-rendered plan sites
uv run radar init
uv run radar discover                  # discovery agent -> registry/florida.yaml
uv run radar serve                     # http://localhost:8000 -> Registry -> Approve
uv run radar nightly                   # crawl -> extract -> approvals policy -> index -> wall
make test                              # unit + wall tests (make wall adds the slow model-based ones)
```

## The people in the loop

| Touchpoint | Where |
|---|---|
| Registry approval | **Registry**: approve the plan list and the pages to crawl |
| Merge review | **Merge review**: ambiguous entity matches only |
| Signal review | **Signal review**: approve, correct or reject each signal. See the review policy below |
| Manual facts | **Manual fact**: URL + date + plan + fact, for sources the system never fetches |
| Public-records requests | **Records requests**: track Chapter 119 requests a person files. Drop the responses in the request's folder and ingest them. The system never sends anything |

**Review policy.** `config/review_policy.yaml` can auto-approve signals and registry versions that
pass every machine check. Each auto-approved row is stamped with the policy's name, never a
person's, and can still be rejected on the review page. Set `auto_approve: false` to review
everything by hand.

## LLM backends

There is one adapter (`src/radar/llm/provider.py`). Model names live in config, never in code.

- `claude_code` (default): headless `claude -p` on the owner's Claude subscription. It runs with
  tools, MCP and settings disabled, in an empty working directory, with API keys stripped from its
  environment.
- `anthropic`: the Anthropic API with `ANTHROPIC_API_KEY`.
- `local`: a stub for an OpenAI-compatible endpoint.

Only scrubbed public-document text is ever sent to a model.

## Deploy

`docker compose up -d` runs the app (login required) and a nightly worker. Put it behind your own
tunnel or reverse proxy on a subdomain. Server notes are in `deploy/VPS.md`. DuckDB is the default
store; the SQL is kept portable so it can move to Postgres.

## Status

Phase A covers South Florida local plans, plus the State Board of Administration via the manual
inbox. Phase B covers every Florida local plan. Phase C covers Florida endowments, foundations and
hospitals, from IRS 990 data.

License: to be chosen before the repository is made public.
