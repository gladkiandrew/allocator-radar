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
    elif args.cmd == "wall":
        from radar.wall import live
        return live.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
