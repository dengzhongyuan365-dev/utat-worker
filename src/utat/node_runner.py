from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

from .config import expand_path, load_config
from .multica_client import MulticaClient, MulticaError
from .node_queue import NodeQueue
from .runner.at_runner import ATRunner
from .runner.ut_runner import UTRunner
from .timeutil import now_iso
from .task_payload import normalize_payload, validate_payload
from .result_writer import ut_result_metric_lines


class NodeRunner:
    def __init__(self, config: Dict[str, Any], *, db_path: str | Path | None = None):
        self.config = config
        node_cfg = config.get("node") or {}
        worker_cfg = config.get("worker") or {}
        self.node_id = node_cfg.get("node_id") or worker_cfg.get("node_id") or socket.gethostname()
        self.work_root = Path(expand_path(node_cfg.get("work_root") or worker_cfg.get("work_root") or "~/atut-work"))
        self.archive_root = Path(expand_path(node_cfg.get("archive_root") or "~/Documents/ATUT-WORK-Archive"))
        self.idle_exit_sec = int(node_cfg.get("idle_exit_sec") if node_cfg.get("idle_exit_sec") is not None else 300)
        self.home = Path(expand_path(node_cfg.get("home") or "~/.utat-node"))
        self.home.mkdir(parents=True, exist_ok=True)
        self.worker_cwd = Path(expand_path(str(node_cfg.get("worker_cwd") or self.home / "worker-cwd")))
        self.multica_cwd = Path(expand_path(str(node_cfg.get("multica_cwd") or self.home / "multica-cwd")))
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
            token=mc_cfg.get("token", ""),
            safe_cwd=self.multica_cwd,
        )

    def submit(self, payload: Dict[str, Any], *, auto_start: bool = True) -> Dict[str, Any]:
        payload = normalize_payload(payload)
        payload["node_id"] = payload.get("node_id") or self.node_id
        payload["workspace_id"] = payload.get("workspace_id") or self.config.get("workspace_id", "")
        validate_payload(payload)
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
        node_bin = self._node_bin()
        cmd = [node_bin, "worker", "run", "--node-id", self.node_id]
        env = self._worker_process_env()
        worker_cwd = self.worker_cwd
        worker_cwd.mkdir(parents=True, exist_ok=True)
        with log_path.open("ab") as log:
            log.write(f"[{now_iso()}] starting worker: {cmd} cwd={worker_cwd}\n".encode())
            subprocess.Popen(
                cmd,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
                env=env,
                cwd=str(worker_cwd),
            )
        return True

    def _node_bin(self) -> str:
        configured = str(os.environ.get("UTAT_NODE_BIN") or "").strip()
        if configured:
            return configured

        cfg_bin = str((self.config.get("node") or {}).get("node_bin") or "").strip()
        if cfg_bin:
            return str(Path(cfg_bin).expanduser())

        # When running from the installed venv, sys.argv[0] is usually the
        # console script path.  Prefer that over PATH so Multica agent tasks with
        # a minimal PATH can still auto-start the detached worker.
        argv0 = Path(sys.argv[0]).expanduser() if sys.argv and sys.argv[0] else Path()
        if argv0.name == "utat-node" and argv0.exists():
            return str(argv0)

        default_bin = Path.home() / ".utat-worker" / "venv" / "bin" / "utat-node"
        if default_bin.exists():
            return str(default_bin)

        found = shutil.which("utat-node")
        if found:
            return found
        return "utat-node"

    def _worker_process_env(self) -> Dict[str, str]:
        """Return a clean environment for the detached local worker.

        The submit command may run inside a Multica agent task.  The detached
        background worker must not inherit agent task context variables; result
        callbacks use the normal CLI login created from the code-pinned mul_
        token instead of a short-lived mat_ task token.
        """
        keep = (
            "HOME",
            "USER",
            "LOGNAME",
            "SHELL",
            "PATH",
            "LANG",
            "LC_ALL",
            "LC_CTYPE",
            "TERM",
            "DISPLAY",
            "XAUTHORITY",
            "XDG_RUNTIME_DIR",
            "DBUS_SESSION_BUS_ADDRESS",
            "QT_QPA_PLATFORM",
            "http_proxy",
            "https_proxy",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "no_proxy",
            "NO_PROXY",
            "SSL_CERT_FILE",
            "SSL_CERT_DIR",
            "INSTALL_PASSWORD",
            "UTAT_HOME",
            "UTAT_NODE_HOME",
            "UTAT_NODE_BIN",
        )
        env = {k: v for k, v in os.environ.items() if k in keep and v}
        env.setdefault("HOME", str(Path.home()))
        env.setdefault("USER", os.environ.get("USER") or Path.home().name)
        env.setdefault("LOGNAME", env["USER"])
        env.setdefault("SHELL", os.environ.get("SHELL") or "/bin/bash")
        env.setdefault("PATH", os.environ.get("PATH") or "/usr/local/bin:/usr/bin:/bin")
        env.setdefault("LANG", os.environ.get("LANG") or "C.UTF-8")
        env.setdefault("DISPLAY", os.environ.get("DISPLAY") or ":0")
        env.setdefault("XAUTHORITY", os.environ.get("XAUTHORITY") or str(Path.home() / ".Xauthority"))
        env.setdefault("XDG_RUNTIME_DIR", os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}")
        env.setdefault("DBUS_SESSION_BUS_ADDRESS", os.environ.get("DBUS_SESSION_BUS_ADDRESS") or f"unix:path=/run/user/{os.getuid()}/bus")
        env.setdefault("QT_QPA_PLATFORM", os.environ.get("QT_QPA_PLATFORM") or "dxcb;xcb")
        return env

    def worker_loop(self) -> None:
        lock_path = self.locks_dir / "worker.lock"
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            return
        try:
            idle_since = time.monotonic()
            while True:
                task = self.queue.claim_next(self.node_id, os.getpid())
                if not task:
                    # Self-heal callbacks only for active callback_pending /
                    # callback_failed rows. Finished tasks are deleted from the
                    # queue, so worker restart never replays historical results.
                    retried = self.retry_failed_callbacks()
                    if retried:
                        idle_since = time.monotonic()
                    if self.idle_exit_sec > 0:
                        idle_for = time.monotonic() - idle_since
                        if idle_for >= self.idle_exit_sec:
                            print(f"[{now_iso()}] worker idle for {int(idle_for)}s, exit")
                            break
                        sleep_for = min(self.poll_interval, max(1, int(self.idle_exit_sec - idle_for)))
                    else:
                        sleep_for = self.poll_interval
                    time.sleep(sleep_for)
                    continue
                idle_since = time.monotonic()
                self.run_task(task)
                idle_since = time.monotonic()
        finally:
            os.close(fd)


    def retry_failed_callbacks(self) -> int:
        retried = 0
        now = time.monotonic()
        for state in ("callback_pending", "callback_failed"):
            for task in self.queue.list(state):
                retried += self._retry_one_callback(task, now)
        return retried

    def _retry_one_callback(self, task: Dict[str, Any], now: float) -> int:
        task_dir = self.tasks_dir / task["id"]
        callback_error = task_dir / "multica-callback-error.txt"
        if task.get("state") == "callback_failed" and not callback_error.exists():
            callback_error.write_text("callback_failed without error detail; retrying", encoding="utf-8")
        throttle = task_dir / "callback-last-retry.monotonic"
        try:
            last = float(throttle.read_text(encoding="utf-8")) if throttle.exists() else 0.0
        except Exception:
            last = 0.0
        if now - last < 60:
            return 0
        throttle.write_text(str(now), encoding="utf-8")
        result_path = Path(str(task.get("result_path") or task_dir / "result.json"))
        if not result_path.exists():
            self.queue.delete(task["id"])
            return 0
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
            artifact_dir = Path(str(task.get("artifact_dir") or task_dir / "artifacts"))
            self.publish_result_ready(task, result, result_path, artifact_dir)
            return 1
        except Exception as exc:
            callback_error.write_text(str(exc), encoding="utf-8")
            self.queue.update(task["id"], state="callback_failed", phase="callback-failed", error=str(exc))
            return 0

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

        if self.preflight_issue_deleted_cleanup(task, task_dir):
            return

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
        artifact_dir = task_dir / "artifacts"
        archive_path = self._archive_path(task, result)
        result["archive_path"] = str(archive_path)
        # Always rewrite result.json after adding archive_path so Multica metadata,
        # local task files and archive copy all carry the same stable path.
        result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        archive_error = ""
        try:
            self.archive_task(task, result, task_dir, archive_path)
        except Exception as exc:
            archive_error = str(exc)
            result["archive_path"] = ""
            result["archive_error"] = archive_error
            (task_dir / "archive-error.txt").write_text(archive_error, encoding="utf-8")
            result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        self.queue.mark_result_ready(
            task_id,
            result_path=str(result_path),
            artifact_dir=str(artifact_dir),
            archive_path=str(archive_path) if not archive_error else "",
            exit_code=result.get("exit_code"),
            state="callback_pending",
            error=result.get("reason", "") if result.get("status") != "done" else archive_error,
        )
        self._write_local_state(task_dir, task_id, "callback_pending", result)
        self.publish_result_ready(task, result, result_path, artifact_dir)


    def _archive_path(self, task: Dict[str, Any], result: Dict[str, Any]) -> Path:
        task_type = str(task.get("task_type") or result.get("task_type") or "TASK").upper()
        task_id = str(task.get("id") or result.get("task_id") or "task")
        issue_id = str(task.get("issue_id") or result.get("issue_id") or "no-issue")
        root_issue_id = str(task.get("root_issue_id") or "no-root")
        app_issue_id = str(task.get("app_issue_id") or "no-app-issue")
        app_name = str(result.get("app") or task.get("app_name") or "app")
        root_title = str(task.get("root_title") or task.get("root_issue_title") or "")

        root_label = self._safe_path_part(f"{root_title}__{root_issue_id}" if root_title else f"root__{root_issue_id}")
        app_label = self._safe_path_part(f"{app_name}__{app_issue_id}")
        exec_label = self._safe_path_part(f"{task_type}__{issue_id}__{task_id}")
        return self.archive_root / root_label / app_label / exec_label

    def archive_task(self, task: Dict[str, Any], result: Dict[str, Any], task_dir: Path, archive_path: Path) -> Path:
        """Copy this task's durable outputs to the human-readable archive tree.

        The source/build/test workspace under ~/atut-work is intentionally not
        moved.  Only the per-task result directory is copied so later test runs
        can keep reusing the code checkout while finished logs/results remain
        stable for audit and report regeneration.
        """
        archive_path.mkdir(parents=True, exist_ok=True)
        for child in task_dir.iterdir():
            if child.name == "archive-error.txt":
                continue
            dest = archive_path / child.name
            if child.is_dir():
                shutil.copytree(child, dest, dirs_exist_ok=True)
            elif child.is_file():
                shutil.copy2(child, dest)

        manifest = {
            "task_id": task.get("id", ""),
            "issue_id": task.get("issue_id", ""),
            "root_issue_id": task.get("root_issue_id", ""),
            "app_issue_id": task.get("app_issue_id", ""),
            "workspace_id": self._task_workspace_id(task),
            "node_id": self.node_id,
            "task_type": task.get("task_type", ""),
            "app_name": task.get("app_name", ""),
            "status": result.get("status", ""),
            "exit_code": result.get("exit_code"),
            "source_task_dir": str(task_dir),
            "archive_path": str(archive_path),
            "archived_at": now_iso(),
        }
        (archive_path / "archive-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        (archive_path / "README.md").write_text(self._archive_readme(manifest, result), encoding="utf-8")
        return archive_path

    def _archive_readme(self, manifest: Dict[str, Any], result: Dict[str, Any]) -> str:
        metrics = result.get("metrics") if isinstance(result.get("metrics"), dict) else {}
        lines = [
            f"# {manifest.get('task_type')} {manifest.get('app_name')} 执行归档",
            "",
            f"- task_id: {manifest.get('task_id')}",
            f"- issue_id: {manifest.get('issue_id')}",
            f"- root_issue_id: {manifest.get('root_issue_id')}",
            f"- app_issue_id: {manifest.get('app_issue_id')}",
            f"- node_id: {manifest.get('node_id')}",
            f"- status: {manifest.get('status')}",
            f"- exit_code: {manifest.get('exit_code')}",
            f"- archived_at: {manifest.get('archived_at')}",
        ]
        if metrics:
            lines += [
                "",
                "## metrics",
                f"- passed: {metrics.get('passed', '')}",
                f"- total: {metrics.get('total', '')}",
                f"- pass_rate: {metrics.get('pass_rate', '')}",
                f"- line_coverage: {metrics.get('line_coverage', '')}",
                f"- function_coverage: {metrics.get('function_coverage', '')}",
            ]
        if result.get("reason"):
            lines += ["", "## reason", str(result.get("reason"))]
        lines += ["", "说明：源码工作区仍保留在 ~/atut-work，本目录只保存本次任务结果、日志和产物快照。", ""]
        return "\n".join(lines)

    @staticmethod
    def _safe_path_part(value: str, max_len: int = 160) -> str:
        text = re.sub(r"[\\/\0\r\n\t]+", "_", str(value).strip())
        text = re.sub(r"\s+", "_", text)
        text = re.sub(r"[^0-9A-Za-z._@=+\-\u4e00-\u9fff（）()【】\[\]]+", "_", text)
        text = text.strip("._ ") or "unknown"
        return text[:max_len]


    def preflight_issue_deleted_cleanup(self, task: Dict[str, Any], task_dir: Path) -> bool:
        """Return True if the task was orphaned/deleted before execution.

        The worker claims queued tasks before it starts expensive source/build/test
        work.  If the Multica root/current issue was deleted while the task was
        queued, running it is wasteful and may publish stale results.  In that
        case keep an audit tombstone under the task directory and remove the row
        from the local queue DB.
        """
        task_id = task.get("id", "")
        issue_id = task.get("issue_id", "")
        root_issue_id = task.get("root_issue_id") or ""
        workspace_id = self._task_workspace_id(task)
        multica = self._multica_for_workspace(workspace_id)

        def orphan(filename: str, reason: str, error: str) -> bool:
            tombstone = {
                "task_id": task_id,
                "issue_id": issue_id,
                "root_issue_id": root_issue_id,
                "workspace_id": workspace_id,
                "state": "orphaned",
                "reason": reason,
                "error": error,
                "updated_at": now_iso(),
            }
            task_dir.mkdir(parents=True, exist_ok=True)
            (task_dir / filename).write_text(json.dumps(tombstone, ensure_ascii=False, indent=2), encoding="utf-8")
            self.queue.mark_orphaned(task_id, reason)
            self.queue.delete(task_id)
            self._write_local_state(task_dir, task_id, "orphaned", tombstone)
            return True

        try:
            if root_issue_id:
                multica.issue_get(root_issue_id)
        except Exception as exc:
            if MulticaClient.is_not_found_error(exc):
                return orphan("orphaned-root-issue-deleted.json", "root-issue-not-found-or-deleted", str(exc))
            (task_dir / "issue-preflight-warning.txt").write_text(str(exc), encoding="utf-8")
            return False

        try:
            if issue_id:
                multica.issue_get(issue_id)
        except Exception as exc:
            if MulticaClient.is_not_found_error(exc):
                return orphan("orphaned-issue-deleted.json", "issue-not-found-or-deleted", str(exc))
            (task_dir / "issue-preflight-warning.txt").write_text(str(exc), encoding="utf-8")
            return False
        return False

    def publish_result_ready(self, task: Dict[str, Any], result: Dict[str, Any], result_path: Path, artifact_dir: Path) -> None:
        issue_id = task["issue_id"]
        workspace_id = self._task_workspace_id(task)
        multica = self._multica_for_workspace(workspace_id)
        callback_error = self.home / "tasks" / task["id"] / "multica-callback-error.txt"
        try:
            # If the user has deleted the root issue or the current execution issue,
            # remove it from the local active DB. Keep task files on disk as tombstone/audit.
            root_issue_id = task.get("root_issue_id") or ""
            if root_issue_id:
                multica.issue_get(root_issue_id)
            multica.issue_get(issue_id)
            multica.metadata_set(issue_id, "utat.task_state", "result_ready", value_type="string")
            multica.metadata_set(issue_id, "utat.result_json", json.dumps(result, ensure_ascii=False), value_type="string")
            multica.metadata_set(issue_id, "utat.artifact_dir", str(artifact_dir), value_type="string")
            multica.metadata_set(issue_id, "utat.result_path", str(result_path), value_type="string")
            if result.get("archive_path"):
                multica.metadata_set(issue_id, "utat.archive_path", str(result.get("archive_path")), value_type="string")
            multica.metadata_set(issue_id, "utat.node", self.node_id, value_type="string")
            if workspace_id:
                multica.metadata_set(issue_id, "utat.workspace_id", workspace_id, value_type="string")

            # 回调不要依赖 issue assignee/rerun。rerun 要求 issue 已分派给 agent/squad，
            # 一旦子 issue 未 assign 就会卡在 result_ready。固定在当前 AT/UT issue
            # 评论 mention 对应角色，由 Multica mention 触发 agent 回收结果。
            callback_attachments = [p for p in result.get("artifacts") or [] if Path(str(p)).exists()]
            multica.comment_add(
                issue_id,
                self._result_ready_comment(task, result, result_path, artifact_dir),
                attachments=callback_attachments,
                temp_dir=self.tasks_dir / task["id"],
            )
            callback_error.unlink(missing_ok=True)
            self._write_local_state(self.tasks_dir / task["id"], task["id"], "done", {
                "phase": "callback-done",
                "issue_id": issue_id,
                "result_path": str(result_path),
                "artifact_dir": str(artifact_dir),
                "updated_at": now_iso(),
            })
            self.queue.delete(task["id"])
        except Exception as exc:
            if MulticaClient.is_not_found_error(exc):
                tombstone = {
                    "task_id": task.get("id"),
                    "issue_id": issue_id,
                    "workspace_id": workspace_id,
                    "state": "orphaned",
                    "reason": "issue-not-found-or-deleted",
                    "error": str(exc),
                    "result_path": str(result_path),
                    "artifact_dir": str(artifact_dir),
                    "updated_at": now_iso(),
                }
                (self.home / "tasks" / task["id"] / "orphaned-issue-deleted.json").write_text(json.dumps(tombstone, ensure_ascii=False, indent=2), encoding="utf-8")
                self.queue.mark_orphaned(task["id"], "issue-not-found-or-deleted")
                self.queue.delete(task["id"])
                callback_error.unlink(missing_ok=True)
                return
            callback_error.write_text(str(exc), encoding="utf-8")
            self.queue.update(task["id"], state="callback_failed", phase="callback-failed", error=str(exc))

    def _callback_agent(self, task: Dict[str, Any]) -> tuple[str, str]:
        task_type = str(task.get("task_type") or "").upper()
        try:
            payload = json.loads(task.get("payload_json") or "{}")
        except Exception:
            payload = {}
        callback = payload.get("callback") if isinstance(payload.get("callback"), dict) else {}
        by_type = callback.get(task_type) if isinstance(callback.get(task_type), dict) else {}
        cfg = self.config.get("callback") or {}
        cfg_by_type = cfg.get(task_type) if isinstance(cfg.get(task_type), dict) else {}
        agent_id = str(by_type.get("agent_id") or callback.get("agent_id") or cfg_by_type.get("agent_id") or "").strip()
        agent_name = str(by_type.get("agent_name") or callback.get("agent_name") or cfg_by_type.get("agent_name") or "").strip()
        return agent_id, agent_name

    def _result_ready_comment(self, task: Dict[str, Any], result: Dict[str, Any], result_path: Path, artifact_dir: Path) -> str:
        task_type = str(task.get("task_type") or "").upper()
        agent_id, agent_name = self._callback_agent(task)
        mention = f"[@{agent_name}](mention://agent/{agent_id})" if agent_id and agent_name else ""
        metric_lines = ut_result_metric_lines(result) if task_type == "UT" else []
        return "\n".join([
            f"【utat-node 回调】{task_type} 本地执行完成，结果已写入 metadata。",
            "",
            f"- task_id: {task.get('id', '')}",
            f"- task_type: {task_type}",
            f"- app: {result.get('app') or task.get('app_name') or ''}",
            f"- status: {result.get('status', '')}",
            f"- exit_code: {result.get('exit_code', '')}",
            *metric_lines,
            f"- result_path: {result_path}",
            f"- artifact_dir: {artifact_dir}",
            f"- archive_path: {result.get('archive_path', '')}",
            "",
            "请回收 result_ready，上传/登记产物并在本 issue 写最终结果；完成后再 mention 队长推进。",
            mention,
        ]).strip() + "\n"

    def _task_workspace_id(self, task: Dict[str, Any]) -> str:
        try:
            payload = json.loads(task.get("payload_json") or "{}")
        except Exception:
            payload = {}
        return str(task.get("workspace_id") or payload.get("workspace_id") or self.config.get("workspace_id") or "")

    def _multica_for_workspace(self, workspace_id: str) -> MulticaClient:
        mc_cfg = self.config.get("multica") or {}
        return MulticaClient(
            workspace_id or self.config.get("workspace_id", ""),
            cli=mc_cfg.get("cli", "multica"),
            server_url=mc_cfg.get("server_url", ""),
            profile=mc_cfg.get("profile", ""),
            token=mc_cfg.get("token", ""),
            safe_cwd=self.multica_cwd,
        )

    def cleanup_missing_issues(self, *, root_issue_id: str = "") -> Dict[str, Any]:
        deleted = []
        kept = []
        errors = []
        root_missing = False
        root_error = ""
        if root_issue_id:
            multica = self._multica_for_workspace(self.config.get("workspace_id", ""))
            try:
                multica.issue_get(root_issue_id)
            except Exception as exc:
                if MulticaClient.is_not_found_error(exc):
                    root_missing = True
                    root_error = str(exc)
                else:
                    errors.append({"root_issue_id": root_issue_id, "error": str(exc)})
        for task in self.queue.list():
            if root_issue_id and task.get("root_issue_id") != root_issue_id:
                continue
            workspace_id = self._task_workspace_id(task)
            task_dir = self.tasks_dir / task["id"]
            task_dir.mkdir(parents=True, exist_ok=True)
            if root_missing:
                (task_dir / "orphaned-root-issue-deleted.json").write_text(json.dumps({
                    "task_id": task.get("id"),
                    "issue_id": task.get("issue_id"),
                    "root_issue_id": task.get("root_issue_id"),
                    "workspace_id": workspace_id,
                    "state": "orphaned",
                    "reason": "root-issue-not-found-or-deleted",
                    "error": root_error,
                    "updated_at": now_iso(),
                }, ensure_ascii=False, indent=2), encoding="utf-8")
                self.queue.delete(task["id"])
                deleted.append(task["id"])
                continue
            multica = self._multica_for_workspace(workspace_id)
            try:
                if task.get("root_issue_id"):
                    multica.issue_get(task["root_issue_id"])
                multica.issue_get(task["issue_id"])
                kept.append(task["id"])
            except Exception as exc:
                if MulticaClient.is_not_found_error(exc):
                    (task_dir / "orphaned-issue-deleted.json").write_text(json.dumps({
                        "task_id": task.get("id"),
                        "issue_id": task.get("issue_id"),
                        "root_issue_id": task.get("root_issue_id"),
                        "workspace_id": workspace_id,
                        "state": "orphaned",
                        "reason": "issue-not-found-or-deleted",
                        "error": str(exc),
                        "updated_at": now_iso(),
                    }, ensure_ascii=False, indent=2), encoding="utf-8")
                    self.queue.delete(task["id"])
                    deleted.append(task["id"])
                else:
                    errors.append({"task_id": task.get("id"), "issue_id": task.get("issue_id"), "error": str(exc)})
        return {"deleted": deleted, "kept": kept, "errors": errors, "root_missing": root_missing}

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
