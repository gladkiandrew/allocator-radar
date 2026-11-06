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
    cp = sub.add_parser("crawl", help="crawl approved allocators' listing pages and fetch documents")
    cp.add_argument("--only", nargs="*", help="allocator ids")
    cp.add_argument("--templates", nargs="*", help="e.g. T-WP T-CP")
    cp.add_argument("--max-docs", type=int, default=60, help="per allocator per run")
    ep = sub.add_parser("extract", help="run the extraction agent over prefiltered pages not yet extracted")
    ep.add_argument("--only", nargs="*", help="allocator ids")
    ep.add_argument("--limit", type=int, default=0, help="max documents this run (0 = all)")
    np_ = sub.add_parser("nightly", help="registry policy -> crawl -> extract -> policy approvals -> index -> wall")
    np_.add_argument("--max-docs", type=int, default=40)
    sub.add_parser("hash-password", help="print the sha256 for RADAR_AUTH_PASSWORD_HASH (reads the password from stdin)")
    sub.add_parser("approve-pending", help="apply config/review_policy.yaml to pending signals (and the registry)")
    sub.add_parser("index", help="add newly parsed (scrubbed) pages to the LanceDB search index")
    dp = sub.add_parser("discover", help="discovery agent: registry/seed_florida.yaml -> registry/florida.yaml")
    dp.add_argument("--resuggest", action="store_true", help="recompute suggested pages only (no fetching)")
    dp.add_argument("--only", nargs="*", help="re-discover only these plan ids; keep the rest of the registry")
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
            path = discovery.run(only=set(args.only) if args.only else None)
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
        with db.connect() as con:
            todo = extractor.pending_documents(con, args.only)
        if args.limit:
            todo = todo[: args.limit]
        print(f"{len(todo)} documents to extract")
        for sha, aid in todo:
            st = extractor.extract_document(sha, aid)
            print(json.dumps({"doc": sha[:12], "allocator": aid, **st}))
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
        print(f"policy approvals: {review.approve_pending()}")
        print(f"indexed: {search.index_new()}")
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
        print(f"signals approved under policy: {review.approve_pending()}")
    elif args.cmd == "index":
        from radar import db, search
        db.init_db()
        print(f"indexed {search.index_new()} pages")
    elif args.cmd == "wall":
        from radar.wall import live
        return live.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
