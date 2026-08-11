from __future__ import annotations

import re
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
        app_rc, app_log = self.check_application_available(root)
        if app_rc != 0:
            return self._result("failed", "application-not-installed", [source_log, dep_log, *build_logs, app_log], exit_code=app_rc)

        log = self.logs_dir / "at-run.log"
        cmd = self._at_command(root)
        rc = self.run_process(["bash", "-lc", cmd], cwd=root, log_path=log, env=task_env, phase="at-running")
        metrics = parse_at_log(log)
        failed = int(metrics.get("failed", 0) or 0)
        errors = int(metrics.get("errors", 0) or 0)
        status = "done" if rc == 0 and failed == 0 and errors == 0 else "failed"
        reason = "at-finished" if status == "done" else (metrics.get("failure_reason") or "at-failed")
        return self._result(status, reason, [source_log, dep_log, *build_logs, log], exit_code=rc, metrics=metrics)

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


    def check_application_available(self, root: Path) -> tuple[int, Path]:
        log = self.logs_dir / "app-check.log"
        app_cmd = self.app_command()
        quoted = shlex.quote(app_cmd)
        cmd = (
            f"command -v {quoted} && echo 'application command ok: {app_cmd}' || "
            f"(echo '应用未安装或命令不存在: {app_cmd}. full 模式必须先打包并安装应用。' >&2; exit 127)"
        )
        rc = self.run_process(["bash", "-lc", cmd], cwd=root, log_path=log, env=self.task_environment(), phase="app-check")
        return rc, log

    def _write_error(self, message: str) -> Path:
        path = self.logs_dir / "environment-error.log"
        path.write_text(message + "\n", encoding="utf-8")
        return path

    def _result(self, status: str, reason: str, artifacts: List[Path], *, exit_code: int | None = None, metrics: Dict[str, Any] | None = None) -> Dict[str, Any]:
        result = {
            "task_id": self.task.get("id"),
            "issue_id": self.task.get("issue_id"),
            "task_type": "AT",
            "app": self.task.get("app_name"),
            "status": status,
            "reason": reason,
            "exit_code": exit_code,
            "metrics": metrics or {},
            "artifacts": [str(p) for p in artifacts if p and p.exists()],
            "finished_at": now_iso(),
        }
        self.write_json("result.json", result)
        return result


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)


def parse_at_log(path: Path) -> Dict[str, Any]:
    text = _strip_ansi(path.read_text(encoding="utf-8", errors="ignore") if path.exists() else "")
    metrics: Dict[str, Any] = {"passed": None, "failed": 0, "errors": 0, "total": None, "pass_rate": None}
    m = re.search(r"Suites:\s*(\d+)\s+passed,\s*(\d+)\s+failed,\s*(\d+)\s+error\s*\((\d+)\s+total\)", text)
    if m:
        passed, failed, errors, total = map(int, m.groups())
        metrics.update({"passed_suites": passed, "failed_suites": failed, "error_suites": errors, "total_suites": total})
    m = re.search(r"Specs:\s*(\d+)\s+passed,\s*(\d+)\s+failed,\s*(\d+)\s+skipped", text)
    if m:
        passed, failed, skipped = map(int, m.groups())
        total = passed + failed + skipped
        metrics.update({"passed": passed, "failed": failed, "skipped": skipped, "total": total})
        metrics["pass_rate"] = round((passed / total * 100), 2) if total else None
    else:
        metrics["failed"] = int(metrics.get("failed_suites", 0) or 0)
        metrics["passed"] = int(metrics.get("passed_suites", 0) or 0) if metrics.get("passed_suites") is not None else None
        metrics["total"] = int(metrics.get("total_suites", 0) or 0) if metrics.get("total_suites") is not None else None
    metrics["errors"] = int(metrics.get("error_suites", 0) or 0)

    reason = ""
    for pattern in (r"应用程序未启动[^\n]*", r"action '[^']+' failed: ([^\n]+)", r"ERROR[^:]*:\s*([^\n]+)"):
        m = re.search(pattern, text)
        if m:
            reason = (m.group(1) if m.lastindex else m.group(0)).strip()
            break
    if reason:
        metrics["failure_reason"] = reason
    return metrics
