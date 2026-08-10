from __future__ import annotations

import fcntl
import json
import os
import signal
import socket
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional

from .config import expand_path, load_config
from .multica_client import MulticaClient, MulticaError
from .node_queue import NodeQueue
from .runner.at_runner import ATRunner
from .runner.ut_runner import UTRunner
from .timeutil import now_iso


class NodeRunner:
    def __init__(self, config: Dict[str, Any], *, db_path: str | Path | None = None):
        self.config = config
        node_cfg = config.get("node") or {}
        worker_cfg = config.get("worker") or {}
        self.node_id = node_cfg.get("node_id") or worker_cfg.get("node_id") or socket.gethostname()
        self.work_root = Path(expand_path(node_cfg.get("work_root") or worker_cfg.get("work_root") or "~/tests"))
        self.home = Path(expand_path(node_cfg.get("home") or "~/.utat-node"))
        self.home.mkdir(parents=True, exist_ok=True)
        self.queue = NodeQueue(db_path or node_cfg.get("queue_db") or self.home / "queue.db")
        self.queue.init()
        self.poll_interval = int(node_cfg.get("poll_interval_sec") or 5)
        self.locks_dir = self.home / "locks"
        self.locks_dir.mkdir(parents=True, exist_ok=True)
        self.tasks_dir = self.home / "tasks"
        self.tasks_dir.mkdir(parents=True, exist_ok=True)
        mc_cfg = config.get("multica") or {}
        self.multica = MulticaClient(
            config.get("workspace_id", ""),
            cli=mc_cfg.get("cli", "multica"),
            server_url=mc_cfg.get("server_url", ""),
            profile=mc_cfg.get("profile", ""),
        )

    def submit(self, payload: Dict[str, Any], *, auto_start: bool = True) -> Dict[str, Any]:
        payload = dict(payload)
        payload["node_id"] = payload.get("node_id") or self.node_id
        task = self.queue.submit(payload)
        task["queue_position"] = self.queue.queue_position(task["id"])
        if auto_start:
            self.ensure_worker()
        return task

    def ensure_worker(self) -> bool:
        lock_path = self.locks_dir / "worker.lock"
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            return False
        # We only use the lock for a quick ownership probe.  The actual worker
        # process reacquires it and keeps it for its entire lifetime.
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
        log_path = self.home / "logs" / "worker.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [os.environ.get("UTAT_NODE_BIN", "utat-node"), "worker", "run", "--node-id", self.node_id]
        with log_path.open("ab") as log:
            log.write(f"[{now_iso()}] starting worker: {cmd}\n".encode())
            subprocess.Popen(
                cmd,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
            )
        return True

    def worker_loop(self) -> None:
        lock_path = self.locks_dir / "worker.lock"
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            return
        try:
            while True:
                task = self.queue.claim_next(self.node_id, os.getpid())
                if not task:
                    time.sleep(self.poll_interval)
                    continue
                self.run_task(task)
        finally:
            os.close(fd)

    def run_task(self, task: Dict[str, Any]) -> None:
        task_id = task["id"]
        task_dir = self.tasks_dir / task_id
        task_dir.mkdir(parents=True, exist_ok=True)
        task_data = dict(task)
        try:
            payload = json.loads(task.get("payload_json") or "{}")
            task_data.update(payload)
        except json.JSONDecodeError:
            pass
        task_data["id"] = task_id
        task_data["issue_id"] = task["issue_id"]
        task_data["task_type"] = task["task_type"]
        task_data["app_name"] = task["app_name"]
        task_data["project_root"] = task_data.get("project_root") or str(self.work_root / self._repo_name(task_data.get("repo", ""), task["app_name"]))

        self.queue.update(task_id, state="starting")
        self._write_local_state(task_dir, task_id, "starting", task)
        heartbeat = lambda state: self.queue.heartbeat(task_id, test_pid=state.get("pid"), phase=state.get("phase", "running"))
        runner = ATRunner(task_data, task_dir, heartbeat=heartbeat) if task["task_type"] == "AT" else UTRunner(task_data, task_dir, heartbeat=heartbeat)
        result: Dict[str, Any]
        try:
            result = runner.run()
        except Exception as exc:
            result = {
                "task_id": task_id,
                "issue_id": task["issue_id"],
                "task_type": task["task_type"],
                "app": task["app_name"],
                "status": "failed",
                "reason": f"runner-exception: {exc}",
                "exit_code": None,
                "artifacts": [],
                "finished_at": now_iso(),
            }
            (task_dir / "runner-exception.txt").write_text(str(exc), encoding="utf-8")
        result_path = task_dir / "result.json"
        if not result_path.exists():
            result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        artifact_dir = task_dir / "artifacts"
        self.queue.mark_result_ready(
            task_id,
            result_path=str(result_path),
            artifact_dir=str(artifact_dir),
            exit_code=result.get("exit_code"),
            state="result_ready" if result.get("status") in {"done", "failed", "blocked"} else "failed",
            error=result.get("reason", "") if result.get("status") != "done" else "",
        )
        self._write_local_state(task_dir, task_id, "result_ready", result)
        self.publish_result_ready(task, result, result_path, artifact_dir)

    def publish_result_ready(self, task: Dict[str, Any], result: Dict[str, Any], result_path: Path, artifact_dir: Path) -> None:
        issue_id = task["issue_id"]
        try:
            self.multica.metadata_set(issue_id, "utat.task_state", "result_ready", value_type="string")
            self.multica.metadata_set(issue_id, "utat.result_json", json.dumps(result, ensure_ascii=False), value_type="string")
            self.multica.metadata_set(issue_id, "utat.artifact_dir", str(artifact_dir), value_type="string")
            self.multica.metadata_set(issue_id, "utat.result_path", str(result_path), value_type="string")
            self.multica.metadata_set(issue_id, "utat.node", self.node_id, value_type="string")
            self.multica.issue_rerun(issue_id)
        except Exception as exc:
            callback_error = self.home / "tasks" / task["id"] / "multica-callback-error.txt"
            callback_error.write_text(str(exc), encoding="utf-8")

    def _write_local_state(self, task_dir: Path, task_id: str, state: str, data: Dict[str, Any]) -> None:
        payload = {"task_id": task_id, "node_id": self.node_id, "state": state, "updated_at": now_iso()}
        payload.update({k: v for k, v in data.items() if k in {"issue_id", "task_type", "app_name", "status", "reason", "exit_code", "pid", "phase"}})
        (task_dir / "node-state.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _repo_name(repo: str, fallback: str) -> str:
        name = (repo or "").rstrip("/").split("/")[-1]
        if name.endswith(".git"):
            name = name[:-4]
        return name or fallback


def run_node_worker(config_path: str | None = None, *, node_id: str = "", db_path: str | None = None) -> None:
    cfg = load_config(config_path)
    if node_id:
        cfg.setdefault("node", {})["node_id"] = node_id
    runner = NodeRunner(cfg, db_path=db_path)
    runner.worker_loop()
