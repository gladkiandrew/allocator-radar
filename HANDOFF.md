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
