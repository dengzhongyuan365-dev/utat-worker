from __future__ import annotations

import json
import os
import socket
import time
from pathlib import Path
from typing import Any, Dict

from .config import expand_path, load_config
from .http_client import APIClient
from .multica_client import MulticaClient
from .queue_db import QueueDB
from .result_writer import upload_result
from .runner.at_runner import ATRunner
from .runner.ut_runner import UTRunner


class Worker:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.node = config.get("worker") or {}
        self.node_id = self.node.get("node_id") or socket.gethostname()
        self.server = APIClient(self.node.get("server_url") or config.get("server_url") or "http://127.0.0.1:8765")
        self.workspace_id = config.get("workspace_id") or ""
        self.multica = MulticaClient(
            self.workspace_id,
            cli=(config.get("multica") or {}).get("cli", "multica"),
            server_url=(config.get("multica") or {}).get("server_url", ""),
            profile=(config.get("multica") or {}).get("profile", ""),
        )
        self.work_root = Path(expand_path(self.node.get("work_root") or "~/atut-work")).expanduser()
        # AT/UT are desktop/application tests.  Keep one active task per
        # physical node regardless of a stale or overly broad config value.
        self.max_parallel = 1
        self.capabilities = self.node.get("capabilities") or {"apps": [], "task_types": ["AT", "UT"]}
        self.poll_interval = int(self.node.get("poll_interval_sec") or 15)

    def once(self) -> bool:
        self.server.post("/api/v1/nodes/heartbeat", {"node_id": self.node_id, "hostname": socket.gethostname(), "capabilities": self.capabilities, "max_parallel": 1})
        claim = self.server.post("/api/v1/tasks/claim", {"node_id": self.node_id, "capabilities": {**self.capabilities, "hostname": socket.gethostname(), "max_parallel": 1}})
        task = claim.get("task")
        if not task:
            return False
        self.run_task(task)
        return True

    def loop(self) -> None:
        while True:
            ran = self.once()
            if not ran:
                time.sleep(self.poll_interval)

    def run_task(self, task: Dict[str, Any]) -> None:
        task_dir = self.work_root / ".utat-worker" / "tasks" / task["issue_id"]
        task_dir.mkdir(parents=True, exist_ok=True)
        runner = ATRunner(task, task_dir, heartbeat=lambda st: self.server.post(f"/api/v1/tasks/{task['id']}/heartbeat", st)) if task["task_type"] == "AT" else UTRunner(task, task_dir, heartbeat=lambda st: self.server.post(f"/api/v1/tasks/{task['id']}/heartbeat", st))
        result = runner.run()
        # local state already written; now report back to Multica and server.
        try:
            upload_result(self.multica, result)
        except Exception as e:
            (task_dir / "multica-upload-error.txt").write_text(str(e), encoding="utf-8")
        self.server.post(f"/api/v1/tasks/{task['id']}/complete", {"status": result.get("status"), "result": result})


def run_worker(config_path: str | None = None, *, once: bool = False) -> None:
    cfg = load_config(config_path)
    worker = Worker(cfg)
    if once:
        worker.once()
    else:
        worker.loop()
