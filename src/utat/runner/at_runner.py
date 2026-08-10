from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from .base import TaskRunner
from ..timeutil import now_iso


class ATRunner(TaskRunner):
    def run(self) -> Dict[str, Any]:
        self.write_json("task.json", self.task)
        root, rc, source_log = self.prepare_source()
        if rc != 0:
            return self._result("failed", "source-sync-failed", [source_log])
        dep_rc, dep_log = self.install_build_deps(root)
        if dep_rc != 0:
            return self._result("failed", "dependency-install-failed", [source_log, dep_log])
        log = self.logs_dir / "at-run.log"
        # Conservative default: run youqu from project root. Specific suite support can be added from task.test_scope.
        cmd = "command -v youqu && youqu --version && youqu doctor || true; youqu at run"
        rc = self.run_process(["bash", "-lc", cmd], cwd=root, log_path=log, phase="at-running")
        status = "done" if rc == 0 else "failed"
        return self._result(status, "at-finished", [source_log, dep_log, log], exit_code=rc)

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
