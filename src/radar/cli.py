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
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.cmd == "init":
        from radar import db
        db.init_db()
        print("database ready")
    elif args.cmd == "serve":
        import uvicorn
        uvicorn.run("radar.web.app:create_app", factory=True, host=args.host, port=args.port, reload=args.reload)
    elif args.cmd == "wall":
        from radar.wall import live
        return live.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
