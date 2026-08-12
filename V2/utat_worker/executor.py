from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

from .config import WorkerConfig, expand
from .models import TaskPayload


def safe_name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._@\-\u4e00-\u9fff]+", "_", s or "unknown")[:120]


def run_cmd(cmd: List[str], cwd: Path, log: Path, timeout: int = 7200, env: Dict[str, str] | None = None) -> Tuple[int, str]:
    merged = dict(os.environ)
    if env:
        merged.update(env)
    with log.open("a", encoding="utf-8") as f:
        f.write(f"$ {' '.join(cmd)}\n")
        f.flush()
        cp = subprocess.run(cmd, cwd=str(cwd), text=True, stdout=f, stderr=subprocess.STDOUT, timeout=timeout, env=merged)
        f.write(f"\nexit_code={cp.returncode}\n")
    return cp.returncode, str(log)


def ensure_source(payload: TaskPayload, cfg: WorkerConfig, logs: Path, progress: Callable[[str, int, str], None] | None = None) -> Tuple[Path, List[Dict[str, Any]]]:
    if progress:
        progress("source_sync", 10, f"同步源码 {payload.repo_url} {payload.branch}")
    work_root = expand(payload.project_root) if payload.project_root else cfg.work_root / payload.repo_name
    work_root.parent.mkdir(parents=True, exist_ok=True)
    artifacts: List[Dict[str, Any]] = []
    log = logs / "source-sync.log"
    if work_root.exists() and (work_root / ".git").exists():
        if payload.no_code_update:
            rc = 0
            with log.open("a", encoding="utf-8") as f:
                f.write("NO_CODE_UPDATE=true; skip git update\n")
        else:
            rc, _ = run_cmd(["git", "fetch", "--all", "--prune"], work_root, log, timeout=1800)
            if rc == 0:
                # Works for GitHub and normal Gerrit branch refs. If exact branch is a ref, git checkout handles it.
                rc, _ = run_cmd(["git", "checkout", payload.branch], work_root, log, timeout=600)
            if rc == 0:
                rc, _ = run_cmd(["git", "pull", "--ff-only", "origin", payload.branch], work_root, log, timeout=1800)
    else:
        if work_root.exists():
            shutil.rmtree(work_root)
        rc, _ = run_cmd(["git", "clone", "--branch", payload.branch, payload.repo_url, str(work_root)], work_root.parent, log, timeout=3600)
    artifacts.append({"name": "source-sync.log", "path": str(log)})
    if rc != 0:
        raise RuntimeError(f"源码同步失败，详见 {log}")
    return work_root, artifacts


def parse_common_metrics(text: str) -> Dict[str, Any]:
    metrics: Dict[str, Any] = {}
    patterns = {
        "passed": [r"passed\D+(\d+)", r"通过(?:用例)?数\D+(\d+)", r"(\d+)\s+passed"],
        "failed": [r"failed\D+(\d+)", r"失败(?:用例)?数\D+(\d+)", r"(\d+)\s+failed"],
        "skipped": [r"skipped\D+(\d+)", r"跳过(?:用例)?数\D+(\d+)", r"(\d+)\s+skipped"],
        "crashed": [r"crashed\D+(\d+)", r"崩溃(?:用例)?数\D+(\d+)", r"(\d+)\s+crashed"],
        "total": [r"total\D+(\d+)", r"总(?:用例)?数\D+(\d+)"],
        "line_coverage": [r"line[_ ]coverage\D+([0-9.]+%)", r"行覆盖率\D+([0-9.]+%)"],
        "function_coverage": [r"function[_ ]coverage\D+([0-9.]+%)", r"函数覆盖率\D+([0-9.]+%)"],
    }
    low = text.lower()
    for key, pats in patterns.items():
        source = low if key not in {"line_coverage", "function_coverage"} else text
        for pat in pats:
            m = re.search(pat, source, re.I)
            if m:
                metrics[key] = m.group(1)
                break
    for key in ("passed", "failed", "skipped", "crashed", "total"):
        if key in metrics:
            try:
                metrics[key] = int(metrics[key])
            except Exception:
                metrics[key] = 0
    passed = int(metrics.get("passed") or 0)
    failed = int(metrics.get("failed") or 0)
    skipped = int(metrics.get("skipped") or 0)
    crashed = int(metrics.get("crashed") or 0)
    if "total" not in metrics:
        metrics["total"] = passed + failed + skipped + crashed
    total = int(metrics.get("total") or 0)
    metrics.setdefault("case_total", total)
    metrics.setdefault("executed_total", passed + failed)
    metrics.setdefault("pass_rate", f"{round(passed / total * 100, 2)}%" if total else "0%")
    metrics.setdefault("line_coverage", "未产出")
    metrics.setdefault("function_coverage", "未产出")
    metrics["metric_source"] = "log_regex"
    return metrics


def find_ut_script(root: Path, script: str) -> Path:
    candidates = [root / script, root / "tests" / script, root / "test" / script]
    for c in candidates:
        if c.exists():
            return c
    matches = list(root.rglob(script))
    if matches:
        return matches[0]
    raise FileNotFoundError(f"未找到 UT 测试脚本: {script}")


def run_at(payload: TaskPayload, root: Path, logs: Path, progress: Callable[[str, int, str], None] | None = None) -> Tuple[str, Dict[str, Any], List[Dict[str, Any]]]:
    artifacts: List[Dict[str, Any]] = []
    if progress:
        progress("build_deps", 25, "安装构建依赖：sudo apt build-dep -y .")
    build_dep_log = logs / "build-dep.log"
    rc, _ = run_cmd(["sudo", "apt", "build-dep", "-y", "."], root, build_dep_log, timeout=3600)
    artifacts.append({"name": "build-dep.log", "path": str(build_dep_log)})
    if rc != 0:
        return "failed", {"passed": 0, "failed": 0, "skipped": 0, "crashed": 0, "total": 0, "pass_rate": "0%", "line_coverage": "未产出", "function_coverage": "未产出", "metric_source": "build_dep_failed"}, artifacts

    if progress:
        progress("build", 45, "打包：dpkg-buildpackage -us -uc -b -j8")
    build_log = logs / "build.log"
    rc, _ = run_cmd(["dpkg-buildpackage", "-us", "-uc", "-b", "-j8"], root, build_log, timeout=7200)
    artifacts.append({"name": "build.log", "path": str(build_log)})
    if rc != 0:
        return "failed", {"passed": 0, "failed": 0, "skipped": 0, "crashed": 0, "total": 0, "pass_rate": "0%", "line_coverage": "未产出", "function_coverage": "未产出", "metric_source": "build_failed"}, artifacts

    debs = sorted(root.parent.glob("*.deb"), key=lambda p: p.stat().st_mtime, reverse=True)
    deb_record = logs / ".utat-generated-debs"
    deb_record.write_text("\n".join(str(p) for p in debs), encoding="utf-8")
    artifacts.append({"name": ".utat-generated-debs", "path": str(deb_record)})
    if not debs:
        return "failed", {"passed": 0, "failed": 0, "skipped": 0, "crashed": 0, "total": 0, "pass_rate": "0%", "line_coverage": "未产出", "function_coverage": "未产出", "metric_source": "no_deb"}, artifacts

    if progress:
        progress("install", 65, "安装真实生成的 deb：sudo dpkg -i")
    install_log = logs / "install.log"
    rc, _ = run_cmd(["sudo", "dpkg", "-i", *[str(p) for p in debs]], root, install_log, timeout=3600)
    artifacts.append({"name": "install.log", "path": str(install_log)})
    if rc != 0:
        return "failed", {"passed": 0, "failed": 0, "skipped": 0, "crashed": 0, "total": 0, "pass_rate": "0%", "line_coverage": "未产出", "function_coverage": "未产出", "metric_source": "install_failed"}, artifacts

    if progress:
        progress("at_test", 80, "执行 AT：youqu")
    at_log = logs / "at.log"
    env = {"DISPLAY": os.environ.get("DISPLAY", ":0"), "XAUTHORITY": os.environ.get("XAUTHORITY", str(Path.home() / ".Xauthority"))}
    rc, _ = run_cmd(["youqu", "run", root.name], root, at_log, timeout=7200, env=env)
    artifacts.append({"name": "at.log", "path": str(at_log)})
    text = at_log.read_text(encoding="utf-8", errors="ignore") if at_log.exists() else ""
    metrics = parse_common_metrics(text)
    status = "done" if rc == 0 and int(metrics.get("failed") or 0) == 0 and int(metrics.get("crashed") or 0) == 0 else "failed"
    return status, metrics, artifacts


def run_ut(payload: TaskPayload, root: Path, logs: Path, progress: Callable[[str, int, str], None] | None = None) -> Tuple[str, Dict[str, Any], List[Dict[str, Any]]]:
    artifacts: List[Dict[str, Any]] = []
    if progress:
        progress("ut_find_script", 35, f"查找 UT 脚本：{payload.test_script or 'test-prj-running.sh'}")
    script = find_ut_script(root, payload.test_script or "test-prj-running.sh")
    script.chmod(script.stat().st_mode | 0o111)
    if progress:
        progress("ut_test", 60, f"执行 UT 脚本：{script}")
    ut_log = logs / "ut.log"
    rc, _ = run_cmd([str(script)], root, ut_log, timeout=7200)
    artifacts.append({"name": "ut.log", "path": str(ut_log)})
    text = ut_log.read_text(encoding="utf-8", errors="ignore") if ut_log.exists() else ""
    metrics = parse_common_metrics(text)
    metrics["test_script"] = str(script)
    status = "done" if rc == 0 and int(metrics.get("failed") or 0) == 0 and int(metrics.get("crashed") or 0) == 0 else "failed"
    return status, metrics, artifacts


def archive_task(payload: TaskPayload, cfg: WorkerConfig, task_id: str) -> Path:
    path = cfg.archive_root / safe_name(payload.root_issue_id or "root") / safe_name(payload.app_issue_id or payload.app_name) / safe_name(payload.issue_id) / f"attempt-{payload.attempt}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def execute(payload: TaskPayload, cfg: WorkerConfig, task_id: str, progress: Callable[[str, int, str], None] | None = None) -> Dict[str, Any]:
    if progress:
        progress("prepare", 5, "准备工作目录和归档目录")
    archive = archive_task(payload, cfg, task_id)
    logs = archive / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    artifacts: List[Dict[str, Any]] = []
    reason = ""
    try:
        source_root, source_artifacts = ensure_source(payload, cfg, logs, progress)
        artifacts.extend(source_artifacts)
        if payload.task_type == "AT":
            status, metrics, more = run_at(payload, source_root, logs, progress)
        else:
            status, metrics, more = run_ut(payload, source_root, logs, progress)
        artifacts.extend(more)
        if status != "done":
            reason = str(metrics.get("metric_source") or "execution_failed")
    except Exception as exc:
        status = "failed"
        reason = str(exc)
        metrics = {"passed": 0, "failed": 0, "skipped": 0, "crashed": 0, "total": 0, "case_total": 0, "executed_total": 0, "pass_rate": "0%", "line_coverage": "未产出", "function_coverage": "未产出", "metric_source": "exception"}
    if progress:
        progress("archive", 90, "写入 result.json 和归档信息")
    result = {
        "schema_version": "v2",
        "task_id": task_id,
        "task_type": payload.task_type,
        "issue_id": payload.issue_id,
        "root_issue_id": payload.root_issue_id,
        "app_issue_id": payload.app_issue_id,
        "app": payload.app_name,
        "repo_name": payload.repo_name,
        "repo_url": payload.repo_url,
        "branch": payload.branch,
        "attempt": payload.attempt,
        "status": status,
        "reason": reason or ("ut-finished" if payload.task_type == "UT" else "at-finished"),
        "metrics": metrics,
        "archive_path": str(archive),
        "artifacts": artifacts,
    }
    (archive / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
