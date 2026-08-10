from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import tarfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..timeutil import now_iso


class TaskRunner:
    def __init__(self, task: Dict[str, Any], task_dir: Path, heartbeat=None):
        self.task = task
        self.task_dir = task_dir
        self.logs_dir = task_dir / "logs"
        self.artifacts_dir = task_dir / "artifacts"
        self.heartbeat = heartbeat
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

    def run(self) -> Dict[str, Any]:
        raise NotImplementedError

    def write_json(self, name: str, data: Dict[str, Any]) -> None:
        (self.task_dir / name).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def update_state(self, **kw: Any) -> None:
        state = {
            "issue_id": self.task.get("issue_id"),
            "task_id": self.task.get("id"),
            "task_type": self.task.get("task_type"),
            "app": self.task.get("app_name"),
            "updated_at": now_iso(),
        }
        state.update(kw)
        self.write_json("state.json", state)
        if self.heartbeat:
            self.heartbeat(state)

    def run_process(self, cmd: List[str] | str, *, cwd: Path, log_path: Path, env: Optional[Dict[str, str]] = None, shell: bool = False, phase: str = "running") -> int:
        full_env = os.environ.copy()
        if env:
            full_env.update(env)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("ab") as log:
            log.write((f"\n[{now_iso()}] CMD: {cmd}\n").encode())
            p = subprocess.Popen(cmd, cwd=str(cwd), stdout=log, stderr=subprocess.STDOUT, env=full_env, shell=shell, preexec_fn=os.setsid)
            (self.task_dir / "pid").write_text(str(p.pid), encoding="utf-8")
            self.update_state(phase=phase, pid=p.pid, log_path=str(log_path), exit_code=None)
            while True:
                rc = p.poll()
                if rc is not None:
                    self.update_state(phase=f"{phase}-finished", pid=p.pid, log_path=str(log_path), exit_code=rc)
                    log.write((f"\n[{now_iso()}] EXIT: {rc}\n").encode())
                    return rc
                self.update_state(phase=phase, pid=p.pid, log_path=str(log_path), exit_code=None)
                time.sleep(30)

    def project_root(self) -> Path:
        pr = self.task.get("project_root") or ""
        if pr:
            return Path(pr).expanduser()
        repo = self.task.get("repo") or self.task.get("app_name")
        name = repo.rstrip("/").split("/")[-1]
        if name.endswith(".git"):
            name = name[:-4]
        return Path.home() / "tests" / name

    def prepare_source(self) -> tuple[Path, int, Path]:
        root = self.project_root()
        repo = self.task.get("repo") or ""
        branch = self.task.get("branch") or "master"
        log = self.logs_dir / "source-sync.log"
        env = {}
        if "github.com" in repo:
            env["https_proxy"] = "http://proxy02.uniontech.com:3128"
            env["http_proxy"] = "http://proxy02.uniontech.com:3128"
        if root.exists() and (root / ".git").exists():
            cmd = f"git fetch --all --prune && git checkout {branch} && git reset --hard origin/{branch} && git status --short && git rev-parse HEAD"
            rc = self.run_process(["bash", "-lc", cmd], cwd=root, log_path=log, env=env, phase="source-sync")
            return root, rc, log
        if not repo:
            log.write_text(f"[{now_iso()}] repo empty and {root} not git repo\n", encoding="utf-8")
            return root, 2, log
        root.parent.mkdir(parents=True, exist_ok=True)
        cmd = f"git clone --branch {branch} {repo} {root} && git -C {root} rev-parse HEAD"
        rc = self.run_process(["bash", "-lc", cmd], cwd=root.parent, log_path=log, env=env, phase="source-clone")
        return root, rc, log

    def install_build_deps(self, root: Path) -> tuple[int, Path]:
        log = self.logs_dir / "build-dep.log"
        if not (root / "debian" / "control").exists():
            log.write_text(f"[{now_iso()}] debian/control not found, skip apt build-dep .\n", encoding="utf-8")
            return 0, log
        if os.environ.get("INSTALL_PASSWORD"):
            cmd = "printf '%s\\n' \"$INSTALL_PASSWORD\" | sudo -S apt build-dep -y ."
        else:
            cmd = "sudo -n apt build-dep -y ."
        rc = self.run_process(["bash", "-lc", cmd], cwd=root, log_path=log, phase="build-dep")
        return rc, log

    def tar_dir(self, source: Path, target_name: str) -> Optional[Path]:
        if not source.exists():
            return None
        target = self.artifacts_dir / target_name
        with tarfile.open(target, "w:gz") as tar:
            tar.add(source, arcname=source.name)
        return target
