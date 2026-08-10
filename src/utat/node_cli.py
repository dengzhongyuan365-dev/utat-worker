from __future__ import annotations

import argparse
import json
import os
import signal
import sys
from pathlib import Path
from typing import Any, Dict

from .config import DEFAULT_NODE_DB, DEFAULT_NODE_HOME, load_config
from .node_queue import NodeQueue
from .node_runner import NodeRunner, run_node_worker


def _runner(args: argparse.Namespace) -> NodeRunner:
    cfg = load_config(args.config)
    node_cfg = cfg.setdefault("node", {})
    if args.node_id:
        node_cfg["node_id"] = args.node_id
    if args.node_home:
        node_cfg["home"] = args.node_home
    db_path = args.db or node_cfg.get("queue_db") or DEFAULT_NODE_DB
    return NodeRunner(cfg, db_path=db_path)


def cmd_init(args: argparse.Namespace) -> None:
    runner = _runner(args)
    print(json.dumps({"node_id": runner.node_id, "home": str(runner.home), "db": str(runner.queue.path)}, ensure_ascii=False, indent=2))


def cmd_submit(args: argparse.Namespace) -> None:
    runner = _runner(args)
    payload: Dict[str, Any] = {
        "issue_id": args.issue_id,
        "root_issue_id": args.root_issue_id,
        "app_issue_id": args.app_issue_id,
        "task_type": args.task_type,
        "app_name": args.app_name,
        "node_id": args.node or args.node_id or runner.node_id,
        "repo": args.repo,
        "branch": args.branch,
        "project_root": args.project_root,
        "validation_mode": args.validation_mode,
        "test_scope": args.test_scope,
        "test_script": args.test_script,
        "no_code_update": args.no_code_update,
        "env": dict(item.split("=", 1) for item in args.env if "=" in item),
    }
    task = runner.submit(payload, auto_start=not args.no_auto_start)
    task["queue_position"] = runner.queue.queue_position(task["id"])
    print(json.dumps({
        "task_id": task["id"],
        "issue_id": task["issue_id"],
        "node_id": task["node_id"],
        "state": task["state"],
        "queue_position": task["queue_position"],
        "worker_started": not args.no_auto_start,
    }, ensure_ascii=False, indent=2))


def cmd_worker(args: argparse.Namespace) -> None:
    run_node_worker(args.config, node_id=args.node_id, db_path=args.db)


def cmd_status(args: argparse.Namespace) -> None:
    runner = _runner(args)
    if args.task_id:
        print(json.dumps(runner.queue.get(args.task_id), ensure_ascii=False, indent=2))
    else:
        print(json.dumps(runner.queue.list(args.state), ensure_ascii=False, indent=2))


def cmd_recover(args: argparse.Namespace) -> None:
    runner = _runner(args)
    rows = runner.queue.recover(node_id=runner.node_id)
    print(json.dumps(rows, ensure_ascii=False, indent=2))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="utat-node")
    p.add_argument("--config", default=None)
    p.add_argument("--db", default=None)
    p.add_argument("--node-id", default="")
    p.add_argument("--node-home", default="")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("init")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("submit")
    s.add_argument("--issue-id", required=True)
    s.add_argument("--root-issue-id", default="")
    s.add_argument("--app-issue-id", default="")
    s.add_argument("--task-type", choices=["AT", "UT"], required=True)
    s.add_argument("--app-name", required=True)
    s.add_argument("--repo", default="")
    s.add_argument("--branch", default="master")
    s.add_argument("--project-root", default="")
    s.add_argument("--validation-mode", default="full")
    s.add_argument("--test-scope", default="全部 suite")
    s.add_argument("--test-script", default="")
    s.add_argument("--no-code-update", action="store_true")
    s.add_argument("--env", action="append", default=[])
    s.add_argument("--node", default="")
    s.add_argument("--no-auto-start", action="store_true")
    s.set_defaults(func=cmd_submit)

    s = sub.add_parser("worker")
    s.add_argument("run", nargs="?")
    s.add_argument("--node-id", default="")
    s.set_defaults(func=cmd_worker)

    s = sub.add_parser("status")
    s.add_argument("--node-id", default="")
    s.add_argument("--state", default="")
    s.add_argument("--task-id", default="")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("recover")
    s.add_argument("--node-id", default="")
    s.set_defaults(func=cmd_recover)

    return p


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
