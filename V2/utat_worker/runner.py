from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict

from .config import WorkerConfig, load_config
from .db import QueueDB
from .executor import execute
from .models import TaskPayload, now_ts
from .multica import MulticaClient, MulticaError


class Worker:
    def __init__(self, cfg: WorkerConfig | None = None, db: QueueDB | None = None):
        self.cfg = cfg or load_config()
        self.db = db or QueueDB(self.cfg.db_path)
        self.mc = MulticaClient(self.cfg)

    def submit(self, payload_data: Dict[str, Any], *, rerun: bool = False, check_issue: bool = True, auto_start: bool = True) -> Dict[str, Any]:
        base = TaskPayload.from_dict(payload_data)
        if not base.workspace_id:
            base.workspace_id = self.cfg.workspace_id
        if not base.project_root:
            base.project_root = str(self.cfg.work_root / base.repo_name)
        if check_issue and not self.mc.issue_exists(base.issue_id):
            return {"ok": False, "action": "issue_missing", "issue_id": base.issue_id, "state": "deleted"}

        active = self.db.active_for_issue(base.issue_id)
        if active and not rerun:
            started = self.ensure_worker_started() if auto_start and active.get("state") == "queued" else {"started": False, "reason": "not_needed"}
            return {
                "ok": True,
                "action": "already_active",
                "task_id": active.get("task_id"),
                "issue_id": base.issue_id,
                "attempt": active.get("attempt"),
                "state": active.get("state"),
                "queue_position": self.db.queue_position(str(active.get("task_id"))),
                "worker": started,
            }

        current = self.db.current_attempt(base.issue_id)
        if current > 0 and not rerun:
            latest_rows = self.db.list_tasks(issue_id=base.issue_id)
            latest = latest_rows[-1] if latest_rows else {}
            return {
                "ok": True,
                "action": "already_has_history_require_rerun",
                "task_id": latest.get("task_id"),
                "issue_id": base.issue_id,
                "attempt": latest.get("attempt") or current,
                "state": latest.get("state") or "completed",
                "queue_position": 0,
            }

        if active and rerun:
            self.db.set_state(str(active.get("task_id")), "superseded", error="superseded by explicit rerun", finished_at=now_ts())

        next_attempt = current + 1 if rerun else int(base.attempt or 1)
        base.attempt = next_attempt
        task_id = str(uuid.uuid4())
        row = self.db.insert_attempt(task_id, base, state="queued")
        # Best-effort metadata. Failure should not block local queue creation.
        try:
            self.mc.metadata_set(base.issue_id, "utat.current_attempt", str(base.attempt))
            self.mc.metadata_set(base.issue_id, "utat.active_task_id", task_id)
            self.mc.metadata_set(base.issue_id, "utat.task_state", "queued")
        except Exception:
            pass
        started = self.ensure_worker_started() if auto_start else {"started": False, "reason": "auto_start_disabled"}
        return {
            "ok": True,
            "action": "rerun_submitted" if rerun else "submitted",
            "task_id": task_id,
            "issue_id": base.issue_id,
            "attempt": base.attempt,
            "node_id": base.node_id,
            "state": row.get("state"),
            "queue_position": self.db.queue_position(task_id),
            "worker": started,
        }

    def ensure_worker_started(self) -> Dict[str, Any]:
        """Start a background consumer if no live consumer is recorded.

        Submit must return quickly but queued tasks should not require a human to
        manually run `utat-worker-daemon`.  The real single-consumer guarantee is
        enforced by worker_loop's file lock, so starting more than once is safe.
        """
        self.cfg.state_home.mkdir(parents=True, exist_ok=True)
        log = self.cfg.state_home / "worker.log"
        cmd = [sys.executable, "-m", "utat_worker.cli", "worker"]
        env = dict(os.environ)
        cwd = str(Path(self.cfg.work_root).expanduser())
        Path(cwd).mkdir(parents=True, exist_ok=True)
        with log.open("ab") as f:
            proc = subprocess.Popen(cmd, cwd=cwd, env=env, stdout=f, stderr=subprocess.STDOUT, start_new_session=True)
        return {"started": True, "pid": proc.pid, "log": str(log)}

    def run_once(self) -> Dict[str, Any]:
        row = self.db.next_queued(self.cfg.node_id)
        if not row:
            return {"ok": True, "action": "idle"}
        task_id = str(row["task_id"])
        payload = TaskPayload.from_dict(row["payload"] if isinstance(row.get("payload"), dict) else json.loads(row["payload_json"]))

        if not self.mc.issue_exists(payload.issue_id):
            self.db.set_state(task_id, "deleted", current_step="deleted", progress=100, message="线上 issue 不存在，任务已清理", finished_at=now_ts(), error="issue not found before run")
            return {"ok": True, "action": "deleted_skip", "task_id": task_id, "issue_id": payload.issue_id}

        self.db.set_state(task_id, "running", started_at=now_ts())
        try:
            self.mc.metadata_set(payload.issue_id, "utat.task_state", "running")
            self.mc.metadata_set(payload.issue_id, "utat.active_task_id", task_id)
            self.mc.metadata_set(payload.issue_id, "utat.current_attempt", str(payload.attempt))
        except Exception:
            pass

        result = execute(payload, self.cfg, task_id, progress=lambda step, pct, msg: self.db.update_progress(task_id, step, pct, msg))
        self.db.save_result(task_id, result, archive_path=str(result.get("archive_path") or ""))
        cb = self.callback_result(task_id, payload, result)
        return {"ok": True, "action": "ran", "task_id": task_id, "callback": cb, "status": result.get("status")}

    def callback_result(self, task_id: str, payload: TaskPayload, result: Dict[str, Any]) -> Dict[str, Any]:
        if not self.mc.issue_exists(payload.issue_id):
            self.db.set_state(task_id, "deleted", error="issue not found at callback", finished_at=now_ts())
            return {"ok": True, "action": "callback_skipped_deleted"}
        try:
            self.mc.metadata_set(payload.issue_id, "utat.result_json", json.dumps(result, ensure_ascii=False), value_type="json")
            self.mc.metadata_set(payload.issue_id, "utat.result_attempt", str(payload.attempt))
            self.mc.metadata_set(payload.issue_id, "utat.result_task_id", task_id)
            self.mc.metadata_set(payload.issue_id, "utat.task_state", "result_ready")
            self.mc.metadata_set(payload.issue_id, "utat.archive_path", str(result.get("archive_path") or ""))
            mention = self._mention_for(payload)
            content = self._callback_comment(payload, result, mention)
            self.mc.comment_add(payload.issue_id, content)
            self.db.set_state(task_id, "completed", current_step="completed", progress=100, message="回调完成，任务出队", finished_at=now_ts())
            return {"ok": True, "action": "callback_done"}
        except Exception as exc:
            self.db.set_state(task_id, "callback_failed", current_step="callback_failed", progress=95, message="回调失败，等待重试或排查", error=str(exc))
            return {"ok": False, "action": "callback_failed", "reason": str(exc)}

    def _mention_for(self, payload: TaskPayload) -> str:
        cb = payload.callback.get("leader") if isinstance(payload.callback, dict) else None
        if not isinstance(cb, dict):
            # Legacy payload fallback. New flow should always provide callback.leader.
            cb = payload.callback.get(payload.task_type) if isinstance(payload.callback, dict) else None
        if isinstance(cb, dict) and cb.get("agent_id"):
            name = cb.get("agent_name") or "AT/UT-研发自测队长"
            return f"[@{name}](mention://agent/{cb['agent_id']})"
        return ""

    def _callback_comment(self, payload: TaskPayload, result: Dict[str, Any], mention: str) -> str:
        m = result.get("metrics") or {}
        lines = [
            f"{mention} {payload.task_type} 本地执行已完成，metadata.utat.task_state=result_ready。".strip(),
            "",
            f"- task_id: {result.get('task_id')}",
            f"- attempt: {result.get('attempt')}",
            f"- status: {result.get('status')}",
            f"- passed/failed/skipped/crashed/total: {m.get('passed',0)}/{m.get('failed',0)}/{m.get('skipped',0)}/{m.get('crashed',0)}/{m.get('total', m.get('case_total',0))}",
            f"- pass_rate: {m.get('pass_rate','0%')}",
            f"- line_coverage: {m.get('line_coverage','未产出')}",
            f"- function_coverage: {m.get('function_coverage','未产出')}",
            f"- reason: {result.get('reason') or '-'}",
            f"- archive_path: {result.get('archive_path') or '-'}",
            "",
            "请扫描 root 全树并推进汇总。",
        ]
        return "\n".join(lines)

    def worker_loop(self) -> None:
        self.cfg.state_home.mkdir(parents=True, exist_ok=True)
        lock_path = self.cfg.state_home / f"worker-{self.cfg.node_id}.lock"
        with lock_path.open("w") as lock:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return
            lock.write(str(os.getpid()))
            lock.flush()
            idle_start = time.time()
            while True:
                res = self.run_once()
                if res.get("action") == "idle":
                    if self.cfg.idle_exit_sec > 0 and time.time() - idle_start >= self.cfg.idle_exit_sec:
                        break
                    time.sleep(self.cfg.poll_interval_sec)
                else:
                    idle_start = time.time()

    def status(self, issue_id: str = "", task_id: str = "") -> Dict[str, Any]:
        if task_id:
            task = self.db.get_task(task_id)
            rows = [task] if task else []
        else:
            rows = self.db.list_tasks(issue_id=issue_id)
        active = [r for r in rows if r.get("state") in {"queued", "running", "callback_pending", "callback_failed"}]
        return {"ok": True, "db": str(self.cfg.db_path), "node_id": self.cfg.node_id, "active_count": len(active), "tasks": rows}
