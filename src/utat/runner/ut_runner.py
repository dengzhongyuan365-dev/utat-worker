from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List

from .base import TaskRunner
from ..timeutil import now_iso


class UTRunner(TaskRunner):
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
        script = self._find_script(root)
        if not script:
            msg = self.logs_dir / "ut-run.log"
            msg.write_text("未找到 UT 测试脚本。优先查找 TEST_SCRIPT、tests/local-test.sh、local-test.sh。\n", encoding="utf-8")
            return self._result("failed", "test-script-not-found", [source_log, dep_log, msg])
        log = self.logs_dir / "ut-run.log"
        explicit = str(self.task.get("test_command") or "").strip()
        cmd = explicit or f"chmod +x {__import__('shlex').quote(str(script))} 2>/dev/null || true; bash {__import__('shlex').quote(str(script))}"
        rc = self.run_process(["bash", "-lc", cmd], cwd=script.parent, log_path=log, env=task_env, phase="ut-running")
        parsed = parse_ut_log(log)
        artifacts: List[Path] = [source_log, dep_log, *build_logs, log]
        # Common coverage/report dirs.
        for rel in ["tests/build-qt6/coverage", "build/coverage", "build-ut/coverage", "tests/build-qt6/report"]:
            tar = self.tar_dir(root / rel, rel.replace("/", "-") + ".tar.gz")
            if tar:
                artifacts.append(tar)
        status = "done" if rc == 0 and parsed.get("failed", 0) == 0 and parsed.get("crashed", 0) == 0 else "failed"
        return self._result(status, "ut-finished", artifacts, exit_code=rc, metrics=parsed)

    def _write_error(self, message: str) -> Path:
        path = self.logs_dir / "environment-error.log"
        path.write_text(message + "\n", encoding="utf-8")
        return path

    def _find_script(self, root: Path) -> Path | None:
        script = (self.task.get("test_script") or "").strip()
        candidates = []
        if script:
            p = Path(script).expanduser()
            candidates.append(p if p.is_absolute() else root / script)
            candidates.append(root / "tests" / script)
        candidates += [root / "tests" / "local-test.sh", root / "local-test.sh"]
        for p in candidates:
            if p.exists() and p.is_file():
                return p
        return None

    def _result(self, status: str, reason: str, artifacts: List[Path], *, exit_code: int | None = None, metrics: Dict[str, Any] | None = None) -> Dict[str, Any]:
        result = {
            "task_id": self.task.get("id"),
            "issue_id": self.task.get("issue_id"),
            "task_type": "UT",
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


def parse_ut_log(path: Path) -> Dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="ignore") if path.exists() else ""
    metrics: Dict[str, Any] = {"passed": None, "failed": 0, "total": None, "pass_rate": None}
    m = re.search(r"执行\s+(\d+)\s+套件,\s*异常退出\s+(\d+)\s+套件", text)
    if m:
        metrics["total_suites"] = int(m.group(1)); metrics["crashed"] = int(m.group(2))
    m = re.search(r"失败用例数\(汇总\):\s*(\d+)", text)
    if m:
        metrics["failed"] = int(m.group(1))
    line_cov = re.search(r"lines\.+:\s*([0-9.]+%)\s*\((\d+) of (\d+) lines\)", text)
    if line_cov:
        metrics["line_coverage"] = line_cov.group(1)
        metrics["line_covered"] = int(line_cov.group(2)); metrics["line_total"] = int(line_cov.group(3))
    fn_cov = re.search(r"functions\.+:\s*([0-9.]+%)\s*\((\d+) of (\d+) functions\)", text)
    if fn_cov:
        metrics["function_coverage"] = fn_cov.group(1)
        metrics["function_covered"] = int(fn_cov.group(2)); metrics["function_total"] = int(fn_cov.group(3))
    return metrics
