from __future__ import annotations

import argparse
import json
from pathlib import Path

from .runner import Worker
from .web import StatusServer


def load_payload(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser(prog="utat-worker")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("submit")
    p.add_argument("--payload-file", required=True)
    p.add_argument("--rerun", action="store_true")
    p.add_argument("--no-check-issue", action="store_true")

    p = sub.add_parser("status")
    p.add_argument("--issue-id", default="")
    p.add_argument("--task-id", default="")

    sub.add_parser("run-once")
    sub.add_parser("worker")
    p = sub.add_parser("serve")
    p.add_argument("--host", default="")
    p.add_argument("--port", type=int, default=0)

    args = ap.parse_args()
    worker = Worker()
    if args.cmd == "submit":
        out = worker.submit(load_payload(args.payload_file), rerun=args.rerun, check_issue=not args.no_check_issue)
    elif args.cmd == "status":
        out = worker.status(issue_id=args.issue_id, task_id=args.task_id)
    elif args.cmd == "run-once":
        out = worker.run_once()
    elif args.cmd == "worker":
        worker.worker_loop()
        out = {"ok": True, "action": "worker_exit"}
    elif args.cmd == "serve":
        StatusServer(worker.cfg, worker.db).serve(args.host or worker.cfg.web_host, args.port or worker.cfg.web_port)
        out = {"ok": True, "action": "serve_exit"}
    else:
        raise AssertionError(args.cmd)
    print(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    return 0 if out.get("ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
