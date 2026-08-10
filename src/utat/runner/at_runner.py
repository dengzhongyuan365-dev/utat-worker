from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any, Dict, List

from .base import TaskRunner
from ..timeutil import now_iso


class ATRunner(TaskRunner):
    def run(self) -> Dict[str, Any]:
        self.write_json("task.json", self.task)
        try:
            task_env = self.task_environment()
        except ValueError as exc:
            return self._result("blocked", "environment-invalid", [self._write_error(str(exc))])

        root, rc, source_log = self.prepare_source()
        if rc != 0:
            return self._result("failed", "source-sync-failed", [source_log])
        dep_rc, dep_log = self.install_build_deps(root)
        if dep_rc != 0:
            return self._result("failed", "dependency-install-failed", [source_log, dep_log])
        build_rc, build_logs = self.run_build_steps(root)
        if build_rc != 0:
            return self._result("failed", "build-or-install-failed", [source_log, dep_log, *build_logs])

        log = self.logs_dir / "at-run.log"
        cmd = self._at_command(root)
        rc = self.run_process(["bash", "-lc", cmd], cwd=root, log_path=log, env=task_env, phase="at-running")
        status = "done" if rc == 0 else "failed"
        return self._result(status, "at-finished", [source_log, dep_log, *build_logs, log], exit_code=rc)

    def _at_command(self, root: Path) -> str:
        explicit = str(self.task.get("at_command") or "").strip()
        if explicit:
            return explicit
        testdir = str(self.task.get("at_path") or root)
        suite = str(self.task.get("suite") or "").strip()
        spec_ids = self.task.get("spec_ids") or []
        if isinstance(spec_ids, (list, tuple)):
            spec_value = ",".join(str(x) for x in spec_ids)
        else:
            spec_value = str(spec_ids).strip()
        args = ["youqu", "at", "run", "--testdir", testdir]
        if suite:
            args += ["--suite", suite]
        if spec_value:
            args += ["--spec-ids", spec_value]
        return "command -v youqu && youqu --version && youqu doctor || true; " + " ".join(shlex.quote(x) for x in args)

    def _write_error(self, message: str) -> Path:
        path = self.logs_dir / "environment-error.log"
        path.write_text(message + "\n", encoding="utf-8")
        return path

    def _result(self, status: str, reason: str, artifacts: List[Path], *, exit_code: int | None = None) -> Dict[str, Any]:
        result = {
            "task_id": self.task.get("id"),
            "issue_id": self.task.get("issue_id"),
            "task_type": "AT",
            "app": self.task.get("app_name"),
            "status": status,
            "reason": reason,
            "exit_code": exit_code,
            "metrics": {},
            "artifacts": [str(p) for p in artifacts if p and p.exists()],
            "finished_at": now_iso(),
        }
        self.write_json("result.json", result)
        return result
