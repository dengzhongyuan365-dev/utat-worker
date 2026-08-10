from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from uuid import uuid4

from .timeutil import now_iso


class QueueDB:
    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.path), timeout=30, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")

    def init(self) -> None:
        cur = self.conn.cursor()
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS root_jobs (
              id TEXT PRIMARY KEY,
              root_issue_id TEXT NOT NULL UNIQUE,
              title TEXT,
              status TEXT NOT NULL,
              priority INTEGER DEFAULT 0,
              created_at TEXT,
              updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS app_tasks (
              id TEXT PRIMARY KEY,
              root_job_id TEXT NOT NULL,
              app_name TEXT NOT NULL,
              app_issue_id TEXT,
              repo TEXT,
              branch TEXT,
              validation_mode TEXT,
              route_policy TEXT,
              status TEXT NOT NULL,
              sort_order INTEGER NOT NULL,
              created_at TEXT,
              updated_at TEXT,
              UNIQUE(root_job_id, app_name)
            );
            CREATE TABLE IF NOT EXISTS exec_tasks (
              id TEXT PRIMARY KEY,
              root_issue_id TEXT NOT NULL,
              app_task_id TEXT,
              app_issue_id TEXT,
              issue_id TEXT NOT NULL UNIQUE,
              task_type TEXT NOT NULL,
              app_name TEXT NOT NULL,
              repo TEXT,
              branch TEXT,
              project_root TEXT,
              validation_mode TEXT,
              test_scope TEXT,
              test_script TEXT,
              preferred_nodes TEXT,
              claimed_by TEXT,
              pid INTEGER,
              phase TEXT,
              status TEXT NOT NULL,
              result_json TEXT,
              log_path TEXT,
              started_at TEXT,
              finished_at TEXT,
              updated_at TEXT
            );
            CREATE TABLE IF NOT EXISTS nodes (
              node_id TEXT PRIMARY KEY,
              hostname TEXT,
              capabilities_json TEXT,
              max_parallel INTEGER DEFAULT 1,
              current_running INTEGER DEFAULT 0,
              last_heartbeat TEXT,
              status TEXT
            );
            CREATE TABLE IF NOT EXISTS runtime_locks (
              lock_name TEXT PRIMARY KEY,
              holder_task_id TEXT,
              holder_node_id TEXT,
              acquired_at TEXT,
              heartbeat_at TEXT
            );
            CREATE TABLE IF NOT EXISTS pending_uploads (
              id TEXT PRIMARY KEY,
              issue_id TEXT NOT NULL,
              content_file TEXT,
              attachments_json TEXT,
              status TEXT NOT NULL,
              error TEXT,
              created_at TEXT,
              updated_at TEXT
            );
            """
        )
        cur.close()

    def upsert_root(self, root_issue_id: str, title: str, status: str = "queued") -> str:
        ts = now_iso()
        rid = self.conn.execute("SELECT id FROM root_jobs WHERE root_issue_id=?", (root_issue_id,)).fetchone()
        if rid:
            self.conn.execute(
                "UPDATE root_jobs SET title=?, updated_at=? WHERE root_issue_id=?",
                (title, ts, root_issue_id),
            )
            return rid["id"]
        jid = str(uuid4())
        self.conn.execute(
            "INSERT INTO root_jobs(id, root_issue_id, title, status, created_at, updated_at) VALUES(?,?,?,?,?,?)",
            (jid, root_issue_id, title, status, ts, ts),
        )
        return jid

    def upsert_app(self, root_job_id: str, app_name: str, *, app_issue_id: str = "", repo: str = "", branch: str = "", validation_mode: str = "full", route_policy: str = "", sort_order: int = 0, status: str = "waiting") -> str:
        ts = now_iso()
        row = self.conn.execute("SELECT id FROM app_tasks WHERE root_job_id=? AND app_name=?", (root_job_id, app_name)).fetchone()
        if row:
            self.conn.execute(
                """UPDATE app_tasks SET app_issue_id=?, repo=?, branch=?, validation_mode=?, route_policy=?, sort_order=?, updated_at=? WHERE id=?""",
                (app_issue_id, repo, branch, validation_mode, route_policy, sort_order, ts, row["id"]),
            )
            return row["id"]
        aid = str(uuid4())
        self.conn.execute(
            """INSERT INTO app_tasks(id, root_job_id, app_name, app_issue_id, repo, branch, validation_mode, route_policy, status, sort_order, created_at, updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
            (aid, root_job_id, app_name, app_issue_id, repo, branch, validation_mode, route_policy, status, sort_order, ts, ts),
        )
        return aid

    def upsert_exec(self, *, root_issue_id: str, app_task_id: str, app_issue_id: str, issue_id: str, task_type: str, app_name: str, repo: str = "", branch: str = "", project_root: str = "", validation_mode: str = "full", test_scope: str = "", test_script: str = "", preferred_nodes: Iterable[str] = (), status: str = "waiting") -> str:
        ts = now_iso()
        pref = json.dumps(list(preferred_nodes), ensure_ascii=False)
        row = self.conn.execute("SELECT id,status FROM exec_tasks WHERE issue_id=?", (issue_id,)).fetchone()
        if row:
            self.conn.execute(
                """UPDATE exec_tasks SET app_task_id=?, app_issue_id=?, task_type=?, app_name=?, repo=?, branch=?, project_root=?, validation_mode=?, test_scope=?, test_script=?, preferred_nodes=?, updated_at=? WHERE issue_id=?""",
                (app_task_id, app_issue_id, task_type, app_name, repo, branch, project_root, validation_mode, test_scope, test_script, pref, ts, issue_id),
            )
            return row["id"]
        eid = str(uuid4())
        self.conn.execute(
            """INSERT INTO exec_tasks(id, root_issue_id, app_task_id, app_issue_id, issue_id, task_type, app_name, repo, branch, project_root, validation_mode, test_scope, test_script, preferred_nodes, phase, status, updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (eid, root_issue_id, app_task_id, app_issue_id, issue_id, task_type, app_name, repo, branch, project_root, validation_mode, test_scope, test_script, pref, "waiting", status, ts),
        )
        return eid

    def list_exec(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        if status:
            rows = self.conn.execute("SELECT * FROM exec_tasks WHERE status=? ORDER BY updated_at", (status,)).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM exec_tasks ORDER BY updated_at").fetchall()
        return [dict(r) for r in rows]

    def get_exec(self, task_id: str) -> Optional[Dict[str, Any]]:
        row = self.conn.execute("SELECT * FROM exec_tasks WHERE id=? OR issue_id=?", (task_id, task_id)).fetchone()
        return dict(row) if row else None

    def heartbeat_node(self, node_id: str, *, hostname: str = "", capabilities: Optional[Dict[str, Any]] = None, max_parallel: int = 1) -> None:
        ts = now_iso()
        caps = json.dumps(capabilities or {}, ensure_ascii=False)
        self.conn.execute(
            """INSERT INTO nodes(node_id, hostname, capabilities_json, max_parallel, last_heartbeat, status)
               VALUES(?,?,?,?,?, 'online')
               ON CONFLICT(node_id) DO UPDATE SET hostname=excluded.hostname, capabilities_json=excluded.capabilities_json,
                 max_parallel=excluded.max_parallel, last_heartbeat=excluded.last_heartbeat, status='online'""",
            (node_id, hostname, caps, max_parallel, ts),
        )

    def make_dispatchable_tasks(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Expose every task that is ready for a *different node* to claim.

        Concurrency is deliberately not global.  A worker enforces one active
        AT/UT task per ``node_id`` when it claims a task.  This method only
        enforces workflow dependencies:

        * an application's UT waits for its own AT to become terminal;
        * applications and root issues are independent and may run on
          different nodes at the same time.
        """
        sql = """
            SELECT e.* FROM exec_tasks e
            JOIN app_tasks a ON a.id=e.app_task_id
            JOIN root_jobs r ON r.root_issue_id=e.root_issue_id
            WHERE e.status IN ('waiting','queued')
              AND (
                (
                  e.task_type='AT'
                  AND NOT EXISTS (
                    SELECT 1 FROM exec_tasks active
                    WHERE active.app_task_id=e.app_task_id
                      AND active.status IN ('dispatchable','claimed','running','collecting')
                  )
                )
                OR
                (
                  e.task_type='UT'
                  AND NOT EXISTS (
                    SELECT 1 FROM exec_tasks at_task
                    WHERE at_task.app_task_id=e.app_task_id
                      AND at_task.task_type='AT'
                      AND at_task.status NOT IN ('done','failed','interrupted','cancelled')
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM exec_tasks active
                    WHERE active.app_task_id=e.app_task_id
                      AND active.status IN ('dispatchable','claimed','running','collecting')
                  )
                )
              )
            ORDER BY r.created_at, a.sort_order, CASE e.task_type WHEN 'AT' THEN 0 ELSE 1 END
        """
        params: List[Any] = []
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        rows = self.conn.execute(sql, params).fetchall()
        if not rows:
            return []
        ts = now_iso()
        with self.conn:
            self.conn.executemany(
                "UPDATE exec_tasks SET status='dispatchable', phase='dispatchable', updated_at=? WHERE id=? AND status IN ('waiting','queued')",
                [(ts, row["id"]) for row in rows],
            )
        return [self.get_exec(row["id"]) for row in rows]

    def make_next_dispatchable(self) -> Optional[Dict[str, Any]]:
        """Backward-compatible single-item wrapper around ``make_dispatchable_tasks``."""
        tasks = self.make_dispatchable_tasks(limit=1)
        return tasks[0] if tasks else None

    def claim_task(self, node_id: str, capabilities: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        # AT/UT execution is always serialized per physical node.  A caller
        # cannot raise this limit through a config value or heartbeat payload.
        self.heartbeat_node(node_id, hostname=capabilities.get("hostname", ""), capabilities=capabilities, max_parallel=1)
        apps = set(capabilities.get("apps") or [])
        types = set(capabilities.get("task_types") or ["AT", "UT"])
        rows = self.conn.execute("SELECT * FROM exec_tasks WHERE status='dispatchable' ORDER BY updated_at").fetchall()
        for r in rows:
            task = dict(r)
            preferred = json.loads(task.get("preferred_nodes") or "[]")
            if preferred and node_id not in preferred:
                continue
            if apps and task["app_name"] not in apps:
                continue
            if task["task_type"] not in types:
                continue
            ts = now_iso()
            # The capacity check and the state transition must be one SQLite
            # transaction.  Otherwise two local worker processes using the
            # same node_id could both observe an idle node and run in parallel.
            try:
                self.conn.execute("BEGIN IMMEDIATE")
                running = self.conn.execute(
                    """
                    SELECT COUNT(*) AS c FROM exec_tasks
                    WHERE claimed_by=?
                      AND status IN ('claimed','running','collecting')
                    """,
                    (node_id,),
                ).fetchone()["c"]
                if running >= 1:
                    self.conn.execute("ROLLBACK")
                    return None
                cur = self.conn.execute(
                    "UPDATE exec_tasks SET status='claimed', phase='claimed', claimed_by=?, started_at=COALESCE(started_at, ?), updated_at=? WHERE id=? AND status='dispatchable'",
                    (node_id, ts, ts, task["id"]),
                )
                if cur.rowcount == 1:
                    self.conn.execute(
                        "UPDATE nodes SET current_running=1, status='busy', last_heartbeat=? WHERE node_id=?",
                        (ts, node_id),
                    )
                    self.conn.execute("COMMIT")
                    return self.get_exec(task["id"])
                self.conn.execute("ROLLBACK")
            except Exception:
                if self.conn.in_transaction:
                    self.conn.execute("ROLLBACK")
                raise
        return None

    def update_task(self, task_id: str, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = now_iso()
        sets = ", ".join([f"{k}=?" for k in fields])
        vals = list(fields.values()) + [task_id]
        self.conn.execute(f"UPDATE exec_tasks SET {sets} WHERE id=?", vals)

    def complete_task(self, task_id: str, status: str, result: Dict[str, Any]) -> None:
        ts = now_iso()
        with self.conn:
            row = self.conn.execute("SELECT claimed_by FROM exec_tasks WHERE id=?", (task_id,)).fetchone()
            self.conn.execute(
                "UPDATE exec_tasks SET status=?, phase=?, result_json=?, finished_at=?, updated_at=? WHERE id=?",
                (status, status, json.dumps(result, ensure_ascii=False), ts, ts, task_id),
            )
            if row and row["claimed_by"]:
                self.conn.execute(
                    """
                    UPDATE nodes
                    SET current_running=(
                        SELECT COUNT(*) FROM exec_tasks
                        WHERE claimed_by=? AND status IN ('claimed','running','collecting')
                    ),
                    status=CASE WHEN (
                        SELECT COUNT(*) FROM exec_tasks
                        WHERE claimed_by=? AND status IN ('claimed','running','collecting')
                    ) = 0 THEN 'online' ELSE 'busy' END,
                    last_heartbeat=?
                    WHERE node_id=?
                    """,
                    (row["claimed_by"], row["claimed_by"], ts, row["claimed_by"]),
                )

    def refresh_app_statuses(self) -> None:
        rows = self.conn.execute("SELECT id FROM app_tasks").fetchall()
        for row in rows:
            tasks = self.conn.execute("SELECT status FROM exec_tasks WHERE app_task_id=?", (row["id"],)).fetchall()
            if not tasks:
                continue
            statuses = [t["status"] for t in tasks]
            if all(s in ("done", "failed", "interrupted", "cancelled") for s in statuses):
                app_status = "done" if all(s == "done" for s in statuses) else "failed"
            elif any(s in ("claimed", "running", "collecting", "dispatchable") for s in statuses):
                app_status = "running"
            else:
                app_status = "waiting"
            self.conn.execute("UPDATE app_tasks SET status=?, updated_at=? WHERE id=?", (app_status, now_iso(), row["id"]))
