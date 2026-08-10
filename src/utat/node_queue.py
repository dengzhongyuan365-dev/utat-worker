from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

from .timeutil import now_iso


TERMINAL_STATES = {"result_ready", "finalized", "failed", "blocked", "interrupted"}
ACTIVE_STATES = {"starting", "running"}


class NodeQueue:
    """Durable single-node queue used by ``utat-node``.

    The queue is intentionally local to one physical execution node.  Cross-node
    assignment is done by the Multica/leader workflow; this database only
    guarantees ordering and single-task execution on the current node.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), timeout=30, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=30000")

    def init(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS node_tasks (
              id TEXT PRIMARY KEY,
              issue_id TEXT NOT NULL UNIQUE,
              root_issue_id TEXT,
              app_issue_id TEXT,
              task_type TEXT NOT NULL,
              app_name TEXT NOT NULL,
              node_id TEXT NOT NULL,
              payload_json TEXT NOT NULL,
              state TEXT NOT NULL,
              phase TEXT,
              queue_position INTEGER,
              worker_pid INTEGER,
              test_pid INTEGER,
              exit_code INTEGER,
              log_path TEXT,
              result_path TEXT,
              artifact_dir TEXT,
              error TEXT,
              submitted_at TEXT NOT NULL,
              started_at TEXT,
              finished_at TEXT,
              heartbeat_at TEXT,
              updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_node_tasks_state ON node_tasks(state, submitted_at);
            CREATE INDEX IF NOT EXISTS idx_node_tasks_node ON node_tasks(node_id, state);
            """
        )

    def close(self) -> None:
        self.conn.close()

    def get(self, task_id: str) -> Optional[Dict[str, Any]]:
        row = self.conn.execute(
            "SELECT * FROM node_tasks WHERE id=? OR issue_id=?",
            (task_id, task_id),
        ).fetchone()
        return dict(row) if row else None

    def list(self, state: str = "") -> List[Dict[str, Any]]:
        if state:
            rows = self.conn.execute(
                "SELECT * FROM node_tasks WHERE state=? ORDER BY submitted_at, rowid",
                (state,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM node_tasks ORDER BY submitted_at, rowid",
            ).fetchall()
        return [dict(row) for row in rows]

    def submit(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        issue_id = str(payload["issue_id"])
        existing = self.get(issue_id)
        if existing and existing["state"] not in TERMINAL_STATES:
            return existing
        if existing and existing["state"] in TERMINAL_STATES:
            # A new submission for a finished issue is almost always an
            # accidental duplicate callback.  Keep it idempotent unless the
            # caller explicitly creates a new issue/attempt.
            return existing

        task_id = str(payload.get("task_id") or uuid4())
        ts = now_iso()
        data = dict(payload)
        data["task_id"] = task_id
        data["submitted_at"] = ts
        with self.conn:
            self.conn.execute(
                """INSERT INTO node_tasks(
                    id, issue_id, root_issue_id, app_issue_id, task_type,
                    app_name, node_id, payload_json, state, phase, submitted_at,
                    updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    task_id,
                    issue_id,
                    payload.get("root_issue_id", ""),
                    payload.get("app_issue_id", ""),
                    payload.get("task_type", ""),
                    payload.get("app_name", ""),
                    payload.get("node_id", ""),
                    json.dumps(data, ensure_ascii=False),
                    "queued",
                    "queued",
                    ts,
                    ts,
                ),
            )
        return self.get(task_id) or {}

    def queue_position(self, task_id: str) -> int:
        row = self.get(task_id)
        if not row:
            return 0
        result = self.conn.execute(
            """SELECT COUNT(*) AS c FROM node_tasks AS q
               WHERE q.node_id=? AND q.state='queued'
                 AND (q.submitted_at < ? OR
                      (q.submitted_at = ? AND q.rowid <=
                       (SELECT rowid FROM node_tasks WHERE id=?)))""",
            (row["node_id"], row["submitted_at"], row["submitted_at"], row["id"]),
        ).fetchone()
        return int(result["c"] or 0)

    def claim_next(self, node_id: str, worker_pid: int) -> Optional[Dict[str, Any]]:
        ts = now_iso()
        try:
            self.conn.execute("BEGIN IMMEDIATE")
            row = self.conn.execute(
                """SELECT * FROM node_tasks
                   WHERE node_id=? AND state='queued'
                   ORDER BY submitted_at, rowid LIMIT 1""",
                (node_id,),
            ).fetchone()
            if not row:
                self.conn.execute("ROLLBACK")
                return None
            self.conn.execute(
                """UPDATE node_tasks
                   SET state='starting', worker_pid=?, started_at=?,
                       heartbeat_at=?, updated_at=?
                   WHERE id=? AND state='queued'""",
                (worker_pid, ts, ts, ts, row["id"]),
            )
            self.conn.execute("COMMIT")
            return self.get(row["id"])
        except Exception:
            if self.conn.in_transaction:
                self.conn.execute("ROLLBACK")
            raise

    def mark_running(self, task_id: str, test_pid: int, log_path: str = "") -> None:
        self.update(
            task_id,
            state="running",
            test_pid=test_pid,
            log_path=log_path,
            heartbeat_at=now_iso(),
        )

    def heartbeat(self, task_id: str, *, test_pid: int | None = None, phase: str = "running") -> None:
        fields: Dict[str, Any] = {"heartbeat_at": now_iso(), "phase": phase}
        if test_pid is not None:
            fields["test_pid"] = test_pid
        self.update(task_id, **fields)

    def mark_result_ready(
        self,
        task_id: str,
        *,
        result_path: str,
        artifact_dir: str,
        exit_code: int | None,
        state: str = "result_ready",
        error: str = "",
    ) -> None:
        self.update(
            task_id,
            state=state,
            result_path=result_path,
            artifact_dir=artifact_dir,
            exit_code=exit_code,
            error=error,
            finished_at=now_iso(),
            heartbeat_at=now_iso(),
        )

    def update(self, task_id: str, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = now_iso()
        sets = ", ".join(f"{key}=?" for key in fields)
        values = list(fields.values()) + [task_id]
        with self.conn:
            self.conn.execute(f"UPDATE node_tasks SET {sets} WHERE id=?", values)

    def recover(self, *, node_id: str = "", stale_before: str = "") -> List[Dict[str, Any]]:
        """Return active tasks for inspection without killing processes.

        Recovery is deliberately conservative.  The caller must inspect the
        recorded test_pid before deciding whether a task is interrupted.
        """
        query = "SELECT * FROM node_tasks WHERE state IN ('starting','running')"
        params: List[Any] = []
        if node_id:
            query += " AND node_id=?"
            params.append(node_id)
        if stale_before:
            query += " AND (heartbeat_at IS NULL OR heartbeat_at < ?)"
            params.append(stale_before)
        query += " ORDER BY submitted_at"
        return [dict(row) for row in self.conn.execute(query, params).fetchall()]
