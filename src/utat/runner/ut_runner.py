from __future__ import annotations

import hashlib
import json
import re
import shlex
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, Iterable, List

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

        workflow_log = self.logs_dir / "ut-workflow.log"
        workflow_log.write_text(
            "UT 执行模式：script-only。\n"
            "UT 不执行 build-dep/build/package/install；构建、覆盖率、报告生成由 UT 脚本自身负责。\n",
            encoding="utf-8",
        )

        script = self._find_script(root)
        if not script:
            msg = self.logs_dir / "ut-run.log"
            msg.write_text("未找到 UT 测试脚本。默认查找 test-prj-running.sh；issue 中 TEST_SCRIPT/test.script 若有特殊指定则使用指定脚本。\n", encoding="utf-8")
            return self._result("failed", "test-script-not-found", [source_log, workflow_log, msg])
        log = self.logs_dir / "ut-run.log"
        explicit = str(self.task.get("test_command") or "").strip()
        cmd = explicit or f"chmod +x {shlex.quote(str(script))} 2>/dev/null || true; bash {shlex.quote(str(script))}"
        rc = self.run_process(["bash", "-lc", cmd], cwd=script.parent, log_path=log, env=task_env, phase="ut-running")
        report_dirs = [root / rel for rel in _common_report_dirs()]
        parsed = parse_ut_log(log, report_dirs=report_dirs)
        artifacts: List[Path] = [source_log, workflow_log, log]
        # Common coverage/report dirs.
        for rel in ["tests/build-qt6/coverage", "build/coverage", "build-ut/coverage", "tests/build-qt6/report"]:
            tar = self.tar_dir(root / rel, rel.replace("/", "-") + ".tar.gz")
            if tar:
                artifacts.append(tar)
        failed = int(parsed.get("failed", 0) or 0)
        failed_cases = int(parsed.get("failed_cases", 0) or 0)
        crashed = int(parsed.get("crashed", 0) or 0)
        status = "done" if rc == 0 and failed == 0 and failed_cases == 0 and crashed == 0 else "failed"
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
        candidates += [root / "test-prj-running.sh", root / "tests" / "test-prj-running.sh"]
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
            "metrics": normalize_ut_metrics(metrics),
            "artifacts": [str(p) for p in artifacts if p and p.exists()],
            "finished_at": now_iso(),
        }
        self.write_json("result.json", result)
        return result


def _common_report_dirs() -> List[str]:
    return [
        "tests/build-qt6/report",
        "tests/build/report",
        "build-qt6/report",
        "build/report",
        "build-ut/report",
        "tests/report",
        "report",
    ]


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)


def _rate(passed: int, total: int) -> float:
    return round((passed / total * 100), 2) if total else 0.0


def normalize_ut_metrics(metrics: Dict[str, Any] | None) -> Dict[str, Any]:
    """Return stable UT metric fields; result.json must never contain null for report fields.

    Some project UT scripts only print coverage and exit code, without gtest/ctest
    case counts.  In that case keep the execution result as done/failed according
    to exit code, but mark case metrics as not_collected instead of writing null.
    """
    src = dict(metrics or {})

    def as_int(key: str, default: int = 0) -> int:
        value = src.get(key, default)
        if value is None or value == "":
            return default
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    def as_float(key: str, default: float = 0.0) -> float:
        value = src.get(key, default)
        if value is None or value == "":
            return default
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    normalized: Dict[str, Any] = {
        "metric_source": str(src.get("metric_source") or "none"),
        "total": as_int("total"),
        "passed": as_int("passed"),
        "failed": as_int("failed"),
        "failed_cases": as_int("failed_cases"),
        "crashed": as_int("crashed"),
        "pass_rate": as_float("pass_rate"),
        "total_suites": as_int("total_suites"),
    }
    # Preserve optional coverage/report fields, but never preserve None.
    for key, value in src.items():
        if key in normalized or value is None:
            continue
        normalized[key] = value
    if normalized["total"] == 0 and (normalized.get("line_coverage") or normalized.get("function_coverage")):
        normalized["case_metric_status"] = "not_collected"
        if normalized["metric_source"] == "none":
            normalized["metric_source"] = "coverage-only"
    else:
        normalized.setdefault("case_metric_status", "collected")
    return normalized


def parse_ut_log(path: Path, *, report_dirs: Iterable[Path] = ()) -> Dict[str, Any]:
    text = _strip_ansi(path.read_text(encoding="utf-8", errors="ignore") if path.exists() else "")
    metrics: Dict[str, Any] = {
        "passed": 0,
        "failed": 0,
        "total": 0,
        "pass_rate": 0.0,
        "total_suites": 0,
        "crashed": 0,
        "failed_cases": 0,
        "metric_source": "none",
    }
    m = re.search(r"执行\s+(\d+)\s+套件,\s*异常退出\s+(\d+)\s+套件", text)
    if m:
        total_suites = int(m.group(1))
        crashed = int(m.group(2))
        metrics["total_suites"] = total_suites
        metrics["crashed"] = crashed
    m = re.search(r"失败用例数\(汇总\):\s*(\d+)", text)
    if m:
        metrics["failed_cases"] = int(m.group(1))

    xml_metrics = parse_ut_xml_reports(report_dirs)
    if xml_metrics.get("total", 0):
        metrics.update(xml_metrics)
        metrics["metric_source"] = "gtest-xml"
    else:
        # Fallback to the current shell-script summary.  This summary is suite scoped:
        # total = executed suites, failed = abnormal/crashed suites.  Failed test-case
        # count remains available as failed_cases and also participates in final status.
        total_suites = int(metrics.get("total_suites", 0) or 0)
        crashed = int(metrics.get("crashed", 0) or 0)
        if total_suites:
            failed_suites = min(crashed, total_suites)
            passed_suites = max(total_suites - failed_suites, 0)
            metrics.update({
                "total": total_suites,
                "failed": failed_suites,
                "passed": passed_suites,
                "pass_rate": _rate(passed_suites, total_suites),
                "metric_source": "suite-summary",
            })
        else:
            gtest_metrics = parse_gtest_summary(text)
            if gtest_metrics.get("total", 0):
                metrics.update(gtest_metrics)
                metrics["metric_source"] = "gtest-log-summary"

    line_matches = list(re.finditer(r"lines\.+:\s*([0-9.]+%)\s*\((\d+) of (\d+) lines\)", text))
    if line_matches:
        line_cov = line_matches[-1]
        metrics["line_coverage"] = line_cov.group(1)
        metrics["line_covered"] = int(line_cov.group(2)); metrics["line_total"] = int(line_cov.group(3))
    fn_matches = list(re.finditer(r"functions\.+:\s*([0-9.]+%)\s*\((\d+) of (\d+) functions\)", text))
    if fn_matches:
        fn_cov = fn_matches[-1]
        metrics["function_coverage"] = fn_cov.group(1)
        metrics["function_covered"] = int(fn_cov.group(2)); metrics["function_total"] = int(fn_cov.group(3))
    return normalize_ut_metrics(metrics)


def parse_gtest_summary(text: str) -> Dict[str, Any]:
    passed = 0
    failed = 0
    m = re.search(r"\[\s*PASSED\s*\]\s*(\d+)\s+tests?", text)
    if m:
        passed = int(m.group(1))
    m = re.search(r"\[\s*FAILED\s*\]\s*(\d+)\s+tests?", text)
    if m:
        failed = int(m.group(1))
    if passed or failed:
        total = passed + failed
        return {"passed": passed, "failed": failed, "total": total, "pass_rate": _rate(passed, total)}

    m = re.search(r"(\d+)%\s+tests\s+passed,\s*(\d+)\s+tests\s+failed\s+out\s+of\s+(\d+)", text, re.I)
    if m:
        failed = int(m.group(2))
        total = int(m.group(3))
        passed = max(total - failed, 0)
        return {"passed": passed, "failed": failed, "total": total, "pass_rate": _rate(passed, total)}
    return {"passed": 0, "failed": 0, "total": 0, "pass_rate": 0.0}


def parse_ut_xml_reports(report_dirs: Iterable[Path]) -> Dict[str, Any]:
    total = 0
    failed = 0
    skipped = 0
    files = 0
    seen_digests: set[str] = set()
    for report_dir in report_dirs:
        if not report_dir.exists() or not report_dir.is_dir():
            continue
        for xml in sorted(report_dir.glob("*.xml")):
            try:
                data = xml.read_bytes()
                digest = hashlib.sha256(data).hexdigest()
                if digest in seen_digests:
                    continue
                root = ET.fromstring(data)
            except (ET.ParseError, OSError):
                continue
            tests, bad, skip = _xml_counts(root)
            if tests == 0 and bad == 0 and skip == 0:
                continue
            seen_digests.add(digest)
            files += 1
            total += tests
            failed += bad
            skipped += skip
    if total <= 0:
        return {"passed": 0, "failed": 0, "total": 0, "pass_rate": 0.0}
    passed = max(total - failed - skipped, 0)
    return {
        "passed": passed,
        "failed": failed,
        "total": total,
        "pass_rate": _rate(passed, total),
        "skipped": skipped,
        "report_xml_files": files,
    }


def _xml_counts(root: ET.Element) -> tuple[int, int, int]:
    # Prefer top-level aggregate attributes.  gtest writes them on <testsuites>
    # or <testsuite>, and per-suite XML files are summed by parse_ut_xml_reports().
    if root.tag in {"testsuites", "testsuite"} and root.get("tests") is not None:
        tests = _to_int(root.get("tests"))
        failures = _to_int(root.get("failures"))
        errors = _to_int(root.get("errors"))
        disabled = _to_int(root.get("disabled"))
        skipped = _to_int(root.get("skipped")) + disabled
        return tests, failures + errors, skipped

    tests = failures = errors = skipped = disabled = 0
    for suite in root.iter("testsuite"):
        tests += _to_int(suite.get("tests"))
        failures += _to_int(suite.get("failures"))
        errors += _to_int(suite.get("errors"))
        skipped += _to_int(suite.get("skipped"))
        disabled += _to_int(suite.get("disabled"))
    return tests, failures + errors, skipped + disabled


def _to_int(value: str | None) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0
