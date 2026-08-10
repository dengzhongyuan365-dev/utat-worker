from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import __version__
from .api_server import APIServer
from .config import DEFAULT_CONFIG, DEFAULT_DB, load_config, write_default_config
from .multica_client import MulticaClient
from .orchestrator import Orchestrator
from .queue_db import QueueDB
from .worker import run_worker


def cmd_init(args):
    cfg = write_default_config(args.config, workspace_id=args.workspace_id or "")
    db = QueueDB(args.db or DEFAULT_DB)
    db.init()
    print(str(cfg))
    print(str(db.path))


def cmd_scan(args):
    cfg = load_config(args.config)
    db = QueueDB(args.db or DEFAULT_DB)
    db.init()
    mc = MulticaClient(cfg.get("workspace_id", ""), cli=(cfg.get("multica") or {}).get("cli", "multica"), server_url=(cfg.get("multica") or {}).get("server_url", ""), profile=(cfg.get("multica") or {}).get("profile", ""))
    orch = Orchestrator(db, mc, cfg)
    plan = orch.scan_root(args.root_issue, apply=args.apply)
    print(json.dumps(plan, ensure_ascii=False, indent=2))


def cmd_orchestrator(args):
    cfg = load_config(args.config)
    db = QueueDB(args.db or DEFAULT_DB)
    db.init()
    mc = MulticaClient(cfg.get("workspace_id", ""), cli=(cfg.get("multica") or {}).get("cli", "multica"), server_url=(cfg.get("multica") or {}).get("server_url", ""), profile=(cfg.get("multica") or {}).get("profile", ""))
    orch = Orchestrator(db, mc, cfg)
    if args.once:
        task = orch.schedule_once()
        print(json.dumps(task, ensure_ascii=False, indent=2))
    else:
        orch.run_loop(args.interval)


def cmd_worker(args):
    run_worker(args.config, once=args.once)


def cmd_queue(args):
    db = QueueDB(args.db or DEFAULT_DB)
    db.init()
    print(json.dumps(db.list_exec(args.status), ensure_ascii=False, indent=2))


def cmd_task(args):
    db = QueueDB(args.db or DEFAULT_DB)
    db.init()
    print(json.dumps(db.get_exec(args.task_id), ensure_ascii=False, indent=2))


def cmd_server(args):
    cfg = load_config(args.config)
    db = QueueDB(args.db or DEFAULT_DB)
    db.init()
    server = APIServer(db, cfg.get("server", {}).get("host", "127.0.0.1"), int(cfg.get("server", {}).get("port", 8765)), cfg.get("server", {}).get("token_env", "UTAT_SERVER_TOKEN"))
    server.serve_forever()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="utat")
    p.add_argument("--config", default=str(DEFAULT_CONFIG))
    p.add_argument("--db", default=str(DEFAULT_DB))
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", default=argparse.SUPPRESS)
    common.add_argument("--db", default=argparse.SUPPRESS)
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", parents=[common])
    s.add_argument("--workspace-id", default="")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("scan", parents=[common])
    s.add_argument("--root-issue", required=True)
    s.add_argument("--apply", action="store_true")
    s.set_defaults(func=cmd_scan)

    s = sub.add_parser("orchestrator", parents=[common])
    s.add_argument("run", nargs="?")
    s.add_argument("--once", action="store_true")
    s.add_argument("--interval", type=int, default=30)
    s.set_defaults(func=cmd_orchestrator)

    s = sub.add_parser("worker", parents=[common])
    s.add_argument("run", nargs="?")
    s.add_argument("--once", action="store_true")
    s.set_defaults(func=cmd_worker)

    s = sub.add_parser("queue", parents=[common])
    s.add_argument("list", nargs="?")
    s.add_argument("--status", default="")
    s.set_defaults(func=cmd_queue)

    s = sub.add_parser("task", parents=[common])
    s.add_argument("status", nargs="?")
    s.add_argument("task_id")
    s.set_defaults(func=cmd_task)

    s = sub.add_parser("server", parents=[common])
    s.set_defaults(func=cmd_server)

    s = sub.add_parser("version")
    s.set_defaults(func=lambda args: print(__version__))

    return p


def main(argv=None):
    p = build_parser()
    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
