from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .models import TaskPayload, now_ts

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS attempts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL UNIQUE,
  issue_id TEXT NOT NULL,
  attempt INTEGER NOT NULL,
  node_id TEXT NOT NULL,
  task_type TEXT NOT NULL,
  root_issue_id TEXT,
  app_issue_id TEXT,
  app_name TEXT,
  repo_name TEXT,
  state TEXT NOT NULL,
  current_step TEXT,
  progress INTEGER DEFAULT 0,
  message TEXT,
  payload_json TEXT NOT NULL,
  result_json TEXT,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  started_at REAL,
  finished_at REAL,
  error TEXT,
  archive_path TEXT,
  UNIQUE(issue_id, attempt)
);
CREATE INDEX IF NOT EXISTS idx_attempts_state ON attempts(state, node_id, created_at);
CREATE INDEX IF NOT EXISTS idx_attempts_issue ON attempts(issue_id, attempt);
"""

MIGRATIONS = [
    "ALTER TABLE attempts ADD COLUMN current_step TEXT",
    "ALTER TABLE attempts ADD COLUMN progress INTEGER DEFAULT 0",
    "ALTER TABLE attempts ADD COLUMN message TEXT",
]

ACTIVE_STATES = {"queued", "running", "callback_pending", "callback_failed"}
TERMINAL_STATES = {"completed", "deleted", "orphan", "failed_to_submit", "superseded"}


class QueueDB:
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        for sql in MIGRATIONS:
            try:
                self.conn.execute(sql)
            except sqlite3.OperationalError:
                pass
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def row_to_dict(self, row: sqlite3.Row | None) -> Optional[Dict[str, Any]]:
        if row is None:
            return None
        d = dict(row)
        for key in ("payload_json", "result_json"):
            if d.get(key):
                try:
                    d[key[:-5] if key.endswith("_json") else key] = json.loads(d[key])
                except Exception:
                    pass
        return d

    def current_attempt(self, issue_id: str) -> int:
        row = self.conn.execute("SELECT MAX(attempt) AS n FROM attempts WHERE issue_id=?", (issue_id,)).fetchone()
        return int(row["n"] or 0)

    def active_for_issue(self, issue_id: str) -> Optional[Dict[str, Any]]:
        row = self.conn.execute(
            "SELECT * FROM attempts WHERE issue_id=? AND state IN ('queued','running','callback_pending','callback_failed') ORDER BY attempt DESC LIMIT 1",
            (issue_id,),
        ).fetchone()
        return self.row_to_dict(row)

    def insert_attempt(self, task_id: str, payload: TaskPayload, state: str = "queued") -> Dict[str, Any]:
        ts = now_ts()
        self.conn.execute(
            """
            INSERT INTO attempts(task_id, issue_id, attempt, node_id, task_type, root_issue_id, app_issue_id, app_name, repo_name, state, current_step, progress, message, payload_json, created_at, updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                task_id,
                payload.issue_id,
                payload.attempt,
                payload.node_id,
                payload.task_type,
                payload.root_issue_id,
                payload.app_issue_id,
                payload.app_name,
                payload.repo_name,
                state,
                "queued",
                0,
                "等待本地 worker 执行",
                json.dumps(payload.to_dict(), ensure_ascii=False),
                ts,
                ts,
            ),
        )
        self.conn.commit()
        return self.get_task(task_id) or {}

    def get_task(self, task_id: str) -> Optional[Dict[str, Any]]:
        row = self.conn.execute("SELECT * FROM attempts WHERE task_id=?", (task_id,)).fetchone()
        return self.row_to_dict(row)

    def list_tasks(self, states: Iterable[str] | None = None, issue_id: str = "", limit: int = 200) -> List[Dict[str, Any]]:
        sql = "SELECT * FROM attempts"
        args: List[Any] = []
        where: List[str] = []
        if states:
            st = list(states)
            where.append("state IN (%s)" % ",".join("?" for _ in st))
            args.extend(st)
        if issue_id:
            where.append("issue_id=?")
            args.append(issue_id)
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        rows = [self.row_to_dict(r) or {} for r in self.conn.execute(sql, args).fetchall()]
        rows.reverse()
        return rows

    def next_queued(self, node_id: str) -> Optional[Dict[str, Any]]:
        row = self.conn.execute(
            "SELECT * FROM attempts WHERE node_id=? AND state='queued' ORDER BY created_at ASC LIMIT 1",
            (node_id,),
        ).fetchone()
        return self.row_to_dict(row)

    def set_state(self, task_id: str, state: str, **fields: Any) -> None:
        fields["state"] = state
        fields["updated_at"] = now_ts()
        keys = list(fields)
        vals = [fields[k] for k in keys]
        sets = ", ".join(f"{k}=?" for k in keys)
        self.conn.execute(f"UPDATE attempts SET {sets} WHERE task_id=?", [*vals, task_id])
        self.conn.commit()

    def update_progress(self, task_id: str, step: str, progress: int, message: str = "") -> None:
        self.set_state(task_id, "running", current_step=step, progress=max(0, min(100, int(progress))), message=message)

    def save_result(self, task_id: str, result: Dict[str, Any], archive_path: str = "") -> None:
        self.set_state(
            task_id,
            "callback_pending",
            current_step="callback_pending",
            progress=95,
            message="执行完成，等待回调 Multica",
            result_json=json.dumps(result, ensure_ascii=False),
            archive_path=archive_path,
            finished_at=now_ts(),
        )

    def queue_position(self, task_id: str) -> int:
        task = self.get_task(task_id)
        if not task or task.get("state") != "queued":
            return 0
        rows = self.list_tasks(["queued"], limit=10000)
        for i, row in enumerate(rows, 1):
            if row.get("task_id") == task_id:
                return i
        return 0
