"""radar CLI. `radar --help`."""

from __future__ import annotations

import argparse
import logging
import sys


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="radar", description="Allocator Radar")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="create the database")
    sp = sub.add_parser("serve", help="run the web app")
    sp.add_argument("--host", default="127.0.0.1")
    sp.add_argument("--port", type=int, default=8000)
    sp.add_argument("--reload", action="store_true")
    sub.add_parser("wall", help="run the live wall checks (nightly)")
    xp = sub.add_parser("export-static", help="render the public read-only site to plain HTML (for a static host)")
    xp.add_argument("--out", default="dist", help="output directory (replaced)")
    wp = sub.add_parser("wall-static", help="wall check on a static snapshot before deploy: contact patterns, admin links")
    wp.add_argument("dir", nargs="?", default="dist")
    sub.add_parser("ef-universe", help="Phase C: choose the Florida E&F universe from the IRS BMF -> data/ef_universe.csv")
    sub.add_parser("ef-load", help="Phase C: universe -> ProPublica summaries -> IRS 990 XML -> 990 signals (data/ef.duckdb)")
    sub.add_parser("ef-signals", help="Phase C: rebuild the 990 signals from stored filings")
    rp = sub.add_parser("ef-reparse", help="re-read stored 990s from the IRS batches for new fields (officers, endowment), then rebuild")
    rp.add_argument("--eins", nargs="*", help="only these organizations (default: every organization in ef_orgs)")
    sub.add_parser("ef-committee", help="Phase C: read the foundations' own committee/board documents for dated signals")
    cp = sub.add_parser("crawl", help="crawl approved allocators' listing pages and fetch documents")
    cp.add_argument("--only", nargs="*", help="allocator ids")
    cp.add_argument("--templates", nargs="*", help="e.g. T-WP T-CP")
    cp.add_argument("--max-docs", type=int, default=150, help="cap on depth targets per allocator per run")
    ep = sub.add_parser("extract", help="run the extraction agent over prefiltered pages not yet extracted")
    ep.add_argument("--only", nargs="*", help="allocator ids")
    ep.add_argument("--limit", type=int, default=0, help="max documents this run (0 = all)")
    ep.add_argument("--workers", type=int, default=1, help="parallel extraction calls")
    ep.add_argument("--follow", action="store_true", help="keep going while a crawl is adding documents")
    np_ = sub.add_parser("nightly", help="registry policy -> crawl -> extract -> policy approvals -> index -> wall")
    np_.add_argument("--max-docs", type=int, default=150)
    sub.add_parser("hash-password", help="print the sha256 for RADAR_AUTH_PASSWORD_HASH (reads the password from stdin)")
    sub.add_parser("approve-pending", help="apply config/review_policy.yaml to pending signals (and the registry)")
    rs = sub.add_parser("rescore", help="recompute stored prefilter terms with the current vocabulary; re-extract documents that gained hit pages")
    rs.add_argument("--no-extract", action="store_true", help="only update the stored terms")
    rs.add_argument("--workers", type=int, default=3)
    sub.add_parser("reparse-scanned", help="re-parse documents that have scanned (empty) pages, with local OCR")
    sub.add_parser("resolve", help="entity resolution for firm names (auto-alias only)")
    sub.add_parser("report", help="main table vs coverage gaps, with coverage % per allocator")
    sub.add_parser("sba", help="ingest + parse the FL SBA reports in inbox/manual/fl-sba-frs/")
    sub.add_parser("auto-approve", help="run the tiered auto-approval rules over pending signals")
    ss = sub.add_parser("scan-searches", help="search-focused extraction over the last 12 months of agendas/minutes/RFPs")
    ss.add_argument("--gaps", action="store_true", help="scan gaps-page plans that have documents (default: main table)")
    ss.add_argument("--only", nargs="*", help="scan these plan ids (any bar status)")
    ss.add_argument("--all", action="store_true", help="scan every plan in the registry")
    ss.add_argument("--workers", type=int, default=5)
    pp = sub.add_parser("pipeline", help="discover -> registry policy -> crawl -> extract -> search scan -> auto-approve -> index")
    pp.add_argument("--only", nargs="+")
    pp.add_argument("--only-file", nargs="+", help="files of whitespace-separated plan ids (avoids shell word-splitting)")
    pp.add_argument("--workers", type=int, default=5)
    pp.add_argument("--deep", action="store_true", help="deep discovery (render + second link level)")
    fp = sub.add_parser("reextract", help="force re-extraction of one document, all pages")
    fp.add_argument("sha_prefix"); fp.add_argument("--max-pages", type=int, default=64)
    fp.add_argument("--pages", help="only these pages, e.g. 1,9,150-160")
    fp.add_argument("--search", action="store_true", help="use the search-scan focus and version tag")
    fp.add_argument("--allocator", help="plan id when the document is listed for several plans")
    ip = sub.add_parser("import-seed", help="import a research-pass YAML into the seed list and allowlist")
    ip.add_argument("path"); ip.add_argument("--batch", required=True)
    ap = sub.add_parser("add-page", help="register a listing page for a plan (registry/florida.yaml + live store)")
    ap.add_argument("allocator_id"); ap.add_argument("url"); ap.add_argument("label")
    cg = sub.add_parser("add-signal", help="curated extraction: a signal read off a cited page, held to the extractor's validators")
    cg.add_argument("allocator_id"); cg.add_argument("sha_prefix"); cg.add_argument("page", type=int)
    cg.add_argument("--quote", required=True); cg.add_argument("--type", required=True); cg.add_argument("--qualifier")
    cg.add_argument("--date", required=True); cg.add_argument("--summary", required=True); cg.add_argument("--bucket")
    ig = sub.add_parser("ingest-staging", help="ingest browser-agent downloads from data/staging/<group>/manifest.yaml")
    ig.add_argument("group"); ig.add_argument("--ef", action="store_true", help="E&F group (ids are EINs)")
    sub.add_parser("gaps", help="coverage gaps grouped by primary cause, with the next action for each")
    cs = sub.add_parser("consultant-scan", help="targeted extraction of service providers for gap plans missing a consultant")
    cs.add_argument("--workers", type=int, default=3)
    sub.add_parser("manual-worklist", help="write data/manual_worklist.md: blocked plans, where to look, where to drop files")
    st = sub.add_parser("status", help="one-shot status: registry, main vs gaps by cause, tiers, deploying/preparing, signals, documents")
    st.add_argument("--save", metavar="PATH", help="also write the deploying funnel as JSON to PATH")
    sub.add_parser("index", help="add newly parsed (scrubbed) pages to the LanceDB search index")
    dp = sub.add_parser("discover", help="discovery agent: registry/seed_florida.yaml -> registry/florida.yaml")
    dp.add_argument("--resuggest", action="store_true", help="recompute suggested pages only (no fetching)")
    dp.add_argument("--only", nargs="*", help="re-discover only these plan ids; keep the rest of the registry")
    dp.add_argument("--only-file", nargs="*", help="files of plan ids")
    dp.add_argument("--deep", action="store_true", help="render every page (JS lists) and follow a second link level")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.cmd == "init":
        from radar import db
        db.init_db()
        print("database ready")
    elif args.cmd == "serve":
        import uvicorn
        uvicorn.run("radar.web.app:create_app", factory=True, host=args.host, port=args.port, reload=args.reload)
    elif args.cmd == "discover":
        from radar import db
        from radar.registry import discovery
        db.init_db()
        if args.resuggest:
            path = discovery.registry_path() if hasattr(discovery, "registry_path") else None
            from radar.settings import get_settings
            path = get_settings().registry_dir / "florida.yaml"
            discovery.resuggest(path)
        else:
            from pathlib import Path as _P
            ids = set(args.only or [])
            for f in args.only_file or []:
                ids |= set(_P(f).read_text().split())
            path = discovery.run(only=ids or None, deep=args.deep)
        print(f"wrote {path} (version {discovery.version(path)[:12]})")
    elif args.cmd == "crawl":
        import json
        from radar import db
        from radar.crawl import runner
        db.init_db()
        for st in runner.run(args.only, set(args.templates) if args.templates else None, args.max_docs):
            print(json.dumps(st))
    elif args.cmd == "extract":
        import json
        from radar import db
        from radar.extract import extractor
        db.init_db()
        import time
        from concurrent.futures import ThreadPoolExecutor

        def one(item):
            sha, aid = item
            try:
                st = extractor.extract_document(sha, aid)
            except Exception as e:  # keep the run going; the document stays pending
                st = {"error": f"{type(e).__name__}: {str(e)[:160]}"}
            print(json.dumps({"doc": sha[:12], "allocator": aid, **st}), flush=True)

        idle = 0
        while True:
            with db.connect() as con:
                todo = extractor.pending_documents(con, args.only)
            if args.limit:
                todo = todo[: args.limit]
            print(f"{len(todo)} documents to extract", flush=True)
            if todo:
                idle = 0
                with ThreadPoolExecutor(max_workers=args.workers) as ex:
                    list(ex.map(one, todo))
            if not args.follow or args.limit:
                break
            idle += 1
            if idle > 30:  # ~15 minutes with nothing new: the crawl is done
                break
            time.sleep(30)
    elif args.cmd == "nightly":
        import json
        from radar import db, review, search
        from radar.crawl import runner
        from radar.extract import extractor
        from radar.registry import approve as reg
        from radar.wall import live
        db.init_db()
        reg.auto_approve_if_policy()
        try:
            for st in runner.run(max_docs=args.max_docs):
                print(json.dumps(st))
        except PermissionError as e:
            print(f"crawl skipped: {e}")
        with db.connect() as con:
            todo = extractor.pending_documents(con)
        for sha, aid in todo:
            print(json.dumps({"doc": sha[:12], "allocator": aid, **extractor.extract_document(sha, aid)}))
        print(f"integrity removals: {len(review.integrity_cleanup())}")
        from radar import autoapprove
        print(f"auto-approval: {autoapprove.run_pass()}")
        print(f"indexed: {search.index_new()}")
        from radar import resolve
        print(f"resolver: {resolve.run()}")
        return live.main()
    elif args.cmd == "hash-password":
        import getpass
        import hashlib
        pw = getpass.getpass("password: ") if sys.stdin.isatty() else sys.stdin.readline().rstrip("\n")
        print(hashlib.sha256(pw.encode()).hexdigest())
    elif args.cmd == "approve-pending":
        from radar import db, review
        from radar.registry import approve as reg
        db.init_db()
        print(f"registry auto-approved: {reg.auto_approve_if_policy()}")
        for r in review.integrity_cleanup():
            print(f"removed orphan: {r}")
        print(f"signals approved under policy: {review.approve_pending()}")
    elif args.cmd == "rescore":
        import json
        from concurrent.futures import ThreadPoolExecutor
        from radar import db
        from radar.extract import extractor
        db.init_db()
        with db.connect() as con:
            todo = extractor.rescore_pages(con)
        print(f"{len(todo)} documents gained prefilter hit pages", flush=True)
        if not args.no_extract:
            def one(item):
                st = extractor.extract_document(item[0], item[1], force=True)
                print(json.dumps({"doc": item[0][:12], "allocator": item[1], **st}), flush=True)
            with ThreadPoolExecutor(max_workers=args.workers) as ex:
                list(ex.map(one, todo))
    elif args.cmd == "reparse-scanned":
        from radar import db
        from radar.parse import pdf
        db.init_db()
        with db.connect() as con:
            shas = [r[0] for r in con.execute(
                "SELECT sha256 FROM pages GROUP BY sha256 HAVING sum(CASE WHEN length(text_scrubbed) < 50 THEN 1 ELSE 0 END) > 0").fetchall()]
        print(f"{len(shas)} documents with scanned pages")
        for sha in shas:
            print(sha[:12], pdf.parse_document(sha, force=True), flush=True)
    elif args.cmd == "resolve":
        from radar import db, resolve
        db.init_db()
        print(resolve.run())
    elif args.cmd == "report":
        from radar import coverage, db, queries
        db.init_db()
        with db.connect() as con:
            rows = queries.allocator_table(con, scoped=False)
            main = [r for r in rows if not r["bar_reasons"]]
            gaps = [r for r in rows if r["bar_reasons"]]
            print(f"MAIN TABLE: {len(main)}   COVERAGE GAPS: {len(gaps)}   (registry: {len(rows)})\n")
            print("Main table (coverage % | docs | tier | plan):")
            for r in sorted(main, key=lambda r: -r["coverage_pct"]):
                ck = coverage.checklist(con, r["allocator_id"])
                marks = " ".join({"ok": "✓", "partial": "~", "missing": "✗", "not_published": "·"}[i["status"]] for i in ck["items"])
                print(f"  {r['coverage_pct']:3d}% | {r['n_docs']:3d} | {r['readiness'].tier:9} | {marks} | {r['display_name']}")
            print("\n  checklist order: agendas-18m minutes-18m IPS quarterly actuarial audited-FS 112.664  (✓ held ~ partial ✗ missing · not published)")
            print("\nCoverage gaps:")
            for r in gaps:
                print(f"  {r['coverage_pct']:3d}% | {r['n_docs']:3d} | {r['display_name']}")
                for why in r["bar_reasons"]:
                    print(f"        - {why}")
    elif args.cmd == "sba":
        import json
        from radar import db
        from radar.extract import extractor, sba
        db.init_db()
        files = sba.ingest_inbox("human")
        print(f"ingested {len(files)} files")
        report = {"signals": 0, "hf": 0, "holdings": 0, "failed": [], "llm_docs": []}
        for f in files:
            if f["kind"] == "NEW_INVESTMENTS":
                st = sba.parse_new_investments(f["sha"], f["doc_date"], f["file"])
                sba.mark_extracted(f["sha"])
                report["signals"] += st["signals"]; report["hf"] += st["hf"]; report["failed"] += st["failed"]
                print(json.dumps({"file": f["file"], **{k: v for k, v in st.items() if k != "failed"}}))
            elif f["kind"] == "SI_PERFORMANCE":
                st = sba.parse_si_table(f["sha"], f["doc_date"], f["file"])
                report["holdings"] += st["holdings"]; report["failed"] += st["failed"]
                if st["tabular"]:
                    sba.mark_extracted(f["sha"])
                else:
                    report["llm_docs"].append(f)
                print(json.dumps({"file": f["file"], **{k: v for k, v in st.items() if k != "failed"}}))
            else:
                report["llm_docs"].append(f)
        for f in report["llm_docs"]:
            holdings = f["kind"] in ("SI_PERFORMANCE", "PE_PERFORMANCE")
            st = extractor.extract_document(f["sha"], "fl-sba-frs", all_pages=holdings)
            print(json.dumps({"file": f["file"], "extractor": st}))
        print(json.dumps({k: v for k, v in report.items() if k != "llm_docs"}, default=str, indent=1))
    elif args.cmd == "auto-approve":
        import json
        from radar import autoapprove, db
        db.init_db()
        print(json.dumps(autoapprove.run_pass(), indent=1))
    elif args.cmd == "ingest-staging":
        import json
        from radar import db, staging
        db.init_db()
        print(json.dumps(staging.ingest_ef(args.group) if args.ef else staging.ingest_pensions(args.group), default=str))
    elif args.cmd == "add-signal":
        from datetime import date as _d
        from radar.extract.curated import add_signal
        print(add_signal(args.sha_prefix, args.allocator_id, args.page, args.quote, args.type, args.qualifier,
                         _d.fromisoformat(args.date), args.summary, args.bucket))
    elif args.cmd == "add-page":
        from radar.registry.pages import add_page
        print(add_page(args.allocator_id, args.url, args.label))
    elif args.cmd == "reextract":
        import json
        from radar import db
        from radar.extract import extractor
        db.init_db()
        with db.connect() as con:
            sha, aid = con.execute("SELECT sha256, any_value(allocator_id) FROM document_urls WHERE sha256 LIKE ? GROUP BY 1",
                                   [args.sha_prefix + "%"]).fetchone()
        aid = args.allocator or aid
        pl = None
        if args.pages:
            pl = []
            for part in args.pages.split(","):
                a, _, b = part.partition("-")
                pl += list(range(int(a), int(b or a) + 1))
        print(json.dumps(extractor.extract_document(sha, aid, all_pages=not pl, max_pages=args.max_pages,
                                                    force=not pl, page_list=pl, search_scan=args.search)))
    elif args.cmd == "scan-searches":
        import json
        from concurrent.futures import ThreadPoolExecutor
        from datetime import date, timedelta
        from radar import coverage, db, queries
        from radar.extract import extractor
        db.init_db()
        since = date.today() - timedelta(days=365)
        with db.connect() as con:
            rows = queries.allocator_table(con, scoped=False)
            if args.all:
                main = rows
            elif args.only:
                main = [a for a in rows if a["allocator_id"] in args.only]
            else:
                main = [a for a in rows if (bool(a["bar_reasons"]) and a["n_docs"] > 0) == args.gaps
                        and (args.gaps or not a["bar_reasons"])]
            todo = []
            for a in main:
                for h in coverage.held_documents(con, a["allocator_id"]):
                    if h["kind"] in ("AGENDA", "MINUTES", "RFP") and h["dated"] and h["dated"] >= since:
                        todo.append((h["sha256"], a["allocator_id"]))
        print(f"{len(main)} {'all' if args.all else 'gaps-page' if args.gaps else 'main-table'} plans, {len(todo)} agendas/minutes/RFPs "
              f"from the last 12 months", flush=True)

        def one(item):
            st = extractor.extract_document(item[0], item[1], search_scan=True, max_pages=20)
            print(json.dumps({"doc": item[0][:12], "allocator": item[1], **st}), flush=True)

        with ThreadPoolExecutor(max_workers=5) as ex:
            list(ex.map(one, todo))
    elif args.cmd == "pipeline":
        import subprocess
        from pathlib import Path
        ids = list(args.only or [])
        for f in args.only_file or []:
            ids += Path(f).read_text().split()
        ids = [i for i in ids for i in i.split()]  # a single argument holding many ids is split too
        if not ids:
            raise SystemExit("pipeline: no plan ids")
        steps = [["discover", "--only", *ids] + (["--deep"] if args.deep else []), ["approve-pending"], ["crawl", "--only", *ids],
                 ["extract", "--only", *ids, "--workers", str(args.workers)], ["scan-searches", "--only", *ids],
                 ["auto-approve"], ["index"]]
        for st in steps:
            print(f"== radar {st[0]}", flush=True)
            subprocess.run([sys.executable, "-m", "radar.cli", *st], check=False)
    elif args.cmd == "import-seed":
        import json
        from pathlib import Path
        from radar.registry import importer
        from radar.settings import sources_config
        out = importer.import_entries(importer.load_entries(Path(args.path)), args.batch)
        sources_config.cache_clear()
        print(json.dumps(out, indent=1))
        Path(f"data/phaseb_{args.batch}.txt").write_text(" ".join(out["added"] + out["manual"]))
    elif args.cmd == "gaps":
        from collections import defaultdict
        from radar import coverage, db, queries
        db.init_db()
        with db.connect() as con:
            rows = [r for r in queries.allocator_table(con, scoped=False) if r["bar_reasons"]]
            groups = defaultdict(list)
            for r in rows:
                cat, desc, action = coverage.gap_category(con, r)
                groups[cat].append(r)
        print(f"COVERAGE GAPS: {len(rows)}")
        for cat, desc, action in coverage.GAP_CATEGORIES:
            g = groups.get(cat, [])
            print(f"\n{cat}: {len(g)}  -- {desc}\n   next: {action}")
            for r in sorted(g, key=lambda r: r["display_name"])[:200]:
                print(f"   - {r['display_name'][:70]}  ({r['n_docs']} docs; {r['readiness'].tier})")
    elif args.cmd == "consultant-scan":
        import json
        from concurrent.futures import ThreadPoolExecutor
        from radar import coverage, db, queries
        from radar.extract import extractor
        db.init_db()
        with db.connect() as con:
            todo = []
            for a in queries.allocator_table(con, scoped=False):
                if not any("consultant not identified" in x for x in a["bar_reasons"]):
                    continue
                docs = sorted(coverage.held_documents(con, a["allocator_id"]),
                              key=lambda h: (h["kind"] not in ("PERFORMANCE", "IPS", "DISCLOSURE_112664", "FINANCIAL"),
                                             -(h["dated"].toordinal() if h["dated"] else 0)))
                todo += [(h["sha256"], a["allocator_id"]) for h in docs[:4]]  # the 4 most likely documents per plan
        print(f"{len(todo)} documents across plans missing a consultant", flush=True)

        def one(item):
            st = extractor.extract_document(item[0], item[1], consultant_scan=True, max_pages=6)
            print(json.dumps({"doc": item[0][:12], "allocator": item[1], **st}), flush=True)

        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            list(ex.map(one, todo))
    elif args.cmd == "manual-worklist":
        from pathlib import Path
        from radar import coverage, db, queries
        db.init_db()
        with db.connect() as con:
            rows = [r for r in queries.allocator_table(con, scoped=False) if r["bar_reasons"]
                    and coverage.gap_category(con, r)[0] == "MANUAL_SOURCE"]
        lines = ["# Manual worklist (blocked to automated clients)", "",
                 "Open each page in your own browser. Download recent agendas/minutes, the latest quarterly investment",
                 "report, IPS, actuarial valuation and 112.664 disclosure. Drop the files in the folder shown, then run",
                 "`uv run radar sba` for FL SBA or the Records requests page -> Ingest manual inbox for everything else.", ""]
        for r in sorted(rows, key=lambda r: r["display_name"]):
            why = r.get("access_reason") or "blocked"
            lines += [f"- **{r['display_name']}** ({r['jurisdiction_city']})", f"  - page: {r['home_url']}",
                      f"  - why manual: {why}"]
            if "resourcecenters" in why.lower():
                # Resource Centers' robots.txt disallows Anthropic's agents; we honor that (D-A05), so its documents are
                # never fed to the extractor, even when a person downloads them.
                lines += ["  - **do not drop Resource Centers files in the inbox** (its robots.txt disallows AI agents). "
                          "Use Manual fact entry for specific facts, or file a Chapter 119 request with the city and ingest "
                          "the city's response on the Records requests page."]
            else:
                lines += [f"  - drop files in: `inbox/manual/{r['allocator_id']}/`"]
                (Path("inbox/manual") / r["allocator_id"]).mkdir(parents=True, exist_ok=True)
        Path("data/manual_worklist.md").write_text("\n".join(lines) + "\n")
        print(f"wrote data/manual_worklist.md ({len(rows)} plans)")
    elif args.cmd == "status":
        from collections import Counter
        from radar import coverage, db, queries
        db.init_db()
        with db.connect() as con:
            rows = queries.allocator_table(con, scoped=False)
            main = [r for r in rows if not r["bar_reasons"]]
            gaps = [r for r in rows if r["bar_reasons"]]
            cats = Counter(coverage.gap_category(con, r)[0] for r in gaps)
            docs = con.execute("SELECT count(*), sum(page_count) FROM documents").fetchone()
            sig = dict(con.execute("SELECT review_status, count(*) FROM signals GROUP BY 1").fetchall())
            hf = con.execute("SELECT count(*) FROM signals WHERE hf_relevant AND review_status IN ('approved','auto_approved')").fetchone()[0]
            from radar import funnel
            fun = funnel.compute(con)
        print(f"REGISTRY {len(rows)} plans | MAIN TABLE {len(main)} | GAPS {len(gaps)}")
        print("  tiers (main):", dict(Counter(r["readiness"].tier for r in main)))
        print("  tiers (all): ", dict(Counter(r["readiness"].tier for r in rows)))
        print("  gaps by cause:", {c: cats.get(c, 0) for c, _, _ in coverage.GAP_CATEGORIES})
        print(f"DOCUMENTS {docs[0]} ({docs[1] or 0} pages) | SIGNALS {sig} | hedge-fund-relevant approved {hf}")
        for t in ("DEPLOYING", "PREPARING"):
            hits = [r for r in rows if r["readiness"].tier == t]
            print(f"\n{t} ({len(hits)}):")
            for r in hits:
                s0 = r["readiness"].reasons[0]
                e = (s0["evidence"] or [{}])[0]
                print(f"  {'MAIN' if not r['bar_reasons'] else 'GAP '} {r['display_name'][:58]:58} {s0['event_date']} "
                      f"{s0['signal_type']}/{s0.get('qualifier') or '-'} {s0.get('bucket') or ''}{' HF' if s0.get('hf_relevant') else ''} "
                      f"[{(e.get('doc_title') or '')[:30]} p.{e.get('page')}]")
        print(funnel.render(fun))
        if args.save:
            import json
            from pathlib import Path
            Path(args.save).parent.mkdir(parents=True, exist_ok=True)
            Path(args.save).write_text(json.dumps(fun, indent=2) + "\n")
            print(f"\nwrote {args.save}")
    elif args.cmd == "index":
        from radar import db, search
        db.init_db()
        print(f"indexed {search.index_new()} pages")
    elif args.cmd == "wall":
        from radar.wall import live
        return live.main()
    elif args.cmd == "export-static":
        import json
        from radar import db, static_export
        db.init_db()
        print(json.dumps(static_export.export(args.out)))
    elif args.cmd == "ef-committee":
        import json
        from radar import db
        from radar.sources.endowments import committee
        db.init_db()
        for r in committee.run():
            print(json.dumps(r, default=str), flush=True)
    elif args.cmd in ("ef-universe", "ef-load", "ef-signals", "ef-reparse"):
        import json
        from radar import db
        from radar.net.client import PoliteClient
        from radar.sources.endowments import pipeline
        db.init_db()  # the egress log lives in the main store
        if args.cmd == "ef-universe":
            print(f"{len(pipeline.build_universe(PoliteClient(purpose='ef-bmf')))} organizations -> data/ef_universe.csv")
        elif args.cmd == "ef-load":
            print(json.dumps(pipeline.run()))
        elif args.cmd == "ef-reparse":
            from radar.sources.endowments import store
            store.init()
            with store.connect() as con:
                eins = args.eins or [r[0] for r in con.execute("SELECT ein FROM ef_orgs ORDER BY ein").fetchall()]
            print(json.dumps(pipeline.load_irs(PoliteClient(purpose="ef-990"), eins, reparse=True)))
            print(f"{pipeline.derive_signals()} signals from 990s")
        else:
            print(f"{pipeline.derive_signals()} signals from 990s")
    elif args.cmd == "wall-static":
        from radar.wall import static_site
        return static_site.main(args.dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
