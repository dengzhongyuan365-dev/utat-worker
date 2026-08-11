from __future__ import annotations

import json
import os
import shutil
import shlex
import signal
import subprocess
import tarfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..timeutil import now_iso
from ..task_payload import resolve_environment


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

    def task_environment(self) -> Dict[str, str]:
        env = resolve_environment(self.task)
        # GUI/Qt applications need a real desktop session on desktop nodes.
        # These defaults match the common UOS desktop login session on the test machines.
        env.setdefault("DISPLAY", __import__("os").environ.get("DISPLAY") or ":0")
        env.setdefault("XAUTHORITY", __import__("os").environ.get("XAUTHORITY") or str(Path.home() / ".Xauthority"))
        env.setdefault("XDG_RUNTIME_DIR", __import__("os").environ.get("XDG_RUNTIME_DIR") or f"/run/user/{__import__('os').getuid()}")
        env.setdefault("DBUS_SESSION_BUS_ADDRESS", __import__("os").environ.get("DBUS_SESSION_BUS_ADDRESS") or f"unix:path=/run/user/{__import__('os').getuid()}/bus")
        env.setdefault("QT_QPA_PLATFORM", __import__("os").environ.get("QT_QPA_PLATFORM") or "dxcb;xcb")
        return env

    def run_command_if_present(self, key: str, root: Path, *, log_name: str, phase: str) -> tuple[int, Optional[Path]]:
        if not self.task.get("build_enabled", True):
            return 0, None
        command = self.build_command_for(key, root)
        if not command:
            return 0, None
        log = self.logs_dir / log_name
        task_env = self.task_environment()
        command = self.with_noninteractive_sudo(command, task_env, allow_sudo=phase in {"build-dep", "install"})
        rc = self.run_process(["bash", "-lc", command], cwd=root, log_path=log, env=task_env, phase=phase)
        return rc, log

    def default_package_command(self) -> str:
        return (
            "set -e; marker=$PWD/.utat-package-start; touch $marker; "
            "dpkg-buildpackage -us -uc -b -j$(nproc); "
            "find .. -maxdepth 1 -type f -name '*.deb' -newer $marker -print > $PWD/.utat-generated-debs; "
            "test -s $PWD/.utat-generated-debs"
        )

    def default_install_command(self) -> str:
        app_cmd = shlex.quote(self.app_command())
        return (
            "set -e; test -s .utat-generated-debs; "
            "debs=$(grep -vE '(-dbgsym_|-dbg_)' .utat-generated-debs | tr '\n' ' '); "
            "test -n \"$debs\"; "
            "printf '%s\\n' \"${INSTALL_PASSWORD:-1}\" | sudo -S -p '' apt install -y $debs; "
            f"command -v {app_cmd}"
        )

    def should_override_full_mode_command(self, key: str, command: str) -> bool:
        if not command or not self.is_full_mode():
            return False
        # Prompt-generated bare dpkg-buildpackage does not record the newly built debs,
        # so the following install step cannot reliably know which files to install.
        if key == "package_command" and "dpkg-buildpackage" in command and ".utat-generated-debs" not in command:
            return True
        # apt install does not expand quoted/payload glob patterns like ./app_*.deb,
        # and deb files are generated in the parent directory.  Use the recorded
        # .utat-generated-debs list instead of trusting fragile glob commands.
        if key == "install_command" and "*.deb" in command:
            return True
        return False

    def build_command_for(self, key: str, root: Path) -> str:
        command = str(self.task.get(key) or "").strip()
        has_debian_control = (root / "debian" / "control").exists()
        if command and not (has_debian_control and self.should_override_full_mode_command(key, command)):
            return command
        if not self.is_full_mode() or not has_debian_control:
            return command if command else ""
        if key == "package_command":
            return self.default_package_command()
        if key == "install_command":
            return self.default_install_command()
        return ""

    def is_full_mode(self) -> bool:
        mode = str(self.task.get("execution_mode") or self.task.get("validation_mode") or "full").lower()
        return mode in {"", "full", "latest", "all"} and not bool(self.task.get("no_code_update"))

    def repo_name(self) -> str:
        repo = str(self.task.get("repo") or "").rstrip("/")
        name = repo.split("/")[-1] if repo else str(self.task.get("app_name") or "")
        return name[:-4] if name.endswith(".git") else name

    def app_command(self) -> str:
        return str(self.task.get("app_command") or self.task.get("binary") or self.repo_name()).strip()

    def with_noninteractive_sudo(self, command: str, env: Dict[str, str], *, allow_sudo: bool) -> str:
        if "sudo" not in command or "sudo -S" in command:
            return command
        if not allow_sudo:
            return command
        install_password = env.get("INSTALL_PASSWORD") or os.environ.get("INSTALL_PASSWORD") or "1"
        env.setdefault("INSTALL_PASSWORD", install_password)
        before, after = command.split("sudo", 1)
        return f"{before}printf '%s\n' \"$INSTALL_PASSWORD\" | sudo -S -p '' {after.lstrip()}"


    def run_build_steps(self, root: Path) -> tuple[int, List[Path]]:
        logs: List[Path] = []
        for key, log_name, phase in (("build_command", "build.log", "build"), ("package_command", "package.log", "package"), ("install_command", "install.log", "install")):
            rc, log = self.run_command_if_present(key, root, log_name=log_name, phase=phase)
            if log:
                logs.append(log)
            if rc != 0:
                return rc, logs
        return 0, logs

    def run_build_pipeline(self, root: Path) -> tuple[int, List[Path]]:
        logs: List[Path] = []
        commands = [
            ("dependency_command", "build-dep.log", "build-dep"),
            ("build_command", "build.log", "build"),
            ("package_command", "package.log", "package"),
            ("install_command", "install.log", "install"),
        ]
        for key, log_name, phase in commands:
            rc, log = self.run_command_if_present(key, root, log_name=log_name, phase=phase)
            if log:
                logs.append(log)
            if rc != 0:
                return rc, logs
        return 0, logs

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
                time.sleep(5)

    def project_root(self) -> Path:
        pr = self.task.get("project_root") or ""
        if pr:
            return Path(pr).expanduser()
        repo = self.task.get("repo") or self.task.get("app_name")
        name = repo.rstrip("/").split("/")[-1]
        if name.endswith(".git"):
            name = name[:-4]
        return Path.home() / "atut-work" / name

    def prepare_source(self) -> tuple[Path, int, Path]:
        root = self.project_root()
        repo = str(self.task.get("repo") or "")
        branch = str(self.task.get("branch") or "master")
        commit = str(self.task.get("commit") or "")
        mode = str(self.task.get("execution_mode") or self.task.get("validation_mode") or "full").lower()
        no_update = bool(self.task.get("no_code_update")) or mode in {"specified", "version", "fixed", "no_update"}
        log = self.logs_dir / "source-sync.log"
        env = {}
        if "github.com" in repo:
            env["https_proxy"] = os.environ.get("https_proxy", "http://proxy02.uniontech.com:3128")
            env["http_proxy"] = os.environ.get("http_proxy", "http://proxy02.uniontech.com:3128")

        if root.exists() and (root / ".git").exists():
            if no_update:
                cmd = "git status --short; git rev-parse HEAD"
                if commit:
                    cmd = (
                        "echo '[utat-node] clean untracked files before source checkout'; "
                        "git clean -ffdx && "
                        f"git checkout --detach {shlex.quote(commit)} && git reset --hard {shlex.quote(commit)} && "
                        "echo '[utat-node] clean untracked files after source checkout'; "
                        "git clean -ffdx && "
                        "git status --short && git rev-parse HEAD"
                    )
                rc = self.run_process(["bash", "-lc", cmd], cwd=root, log_path=log, env=env, phase="source-check")
                return root, rc, log
            target = commit or f"origin/{branch}"
            clean = "git clean -ffdx"
            fetch = "git fetch --all --prune"
            checkout = f"git checkout {shlex.quote(branch)} && git reset --hard {shlex.quote(target)}"
            cmd = (
                "echo '[utat-node] clean untracked files before source update'; "
                f"{clean} && "
                f"{fetch} && {checkout} && "
                "echo '[utat-node] clean untracked files after source update'; "
                f"{clean} && "
                "git status --short && git rev-parse HEAD"
            )
            rc = self.run_process(["bash", "-lc", cmd], cwd=root, log_path=log, env=env, phase="source-sync")
            return root, rc, log
        if no_update:
            log.write_text(f"[{now_iso()}] 指定版本/不更新模式要求已有仓库，但不存在：{root}\n", encoding="utf-8")
            return root, 2, log
        if not repo:
            log.write_text(f"[{now_iso()}] repo 为空，且 {root} 不是 git 仓库\n", encoding="utf-8")
            return root, 2, log
        root.parent.mkdir(parents=True, exist_ok=True)
        cmd = f"git clone --branch {shlex.quote(branch)} {shlex.quote(repo)} {shlex.quote(str(root))} && git -C {shlex.quote(str(root))} rev-parse HEAD"
        rc = self.run_process(["bash", "-lc", cmd], cwd=root.parent, log_path=log, env=env, phase="source-clone")
        return root, rc, log

    def install_build_deps(self, root: Path) -> tuple[int, Path]:
        log = self.logs_dir / "build-dep.log"
        command = str(self.task.get("dependency_command") or "").strip()
        if not command and (root / "debian" / "control").exists():
            command = "sudo apt build-dep -y ."
        if not command:
            log.write_text(f"[{now_iso()}] 未配置依赖安装命令，或项目没有 debian/control，跳过依赖安装。\n", encoding="utf-8")
            return 0, log

        task_env = self.task_environment()
        command = self.with_noninteractive_sudo(command, task_env, allow_sudo=True)
        rc = self.run_process(["bash", "-lc", command], cwd=root, log_path=log, env=task_env, phase="build-dep")
        return rc, log

    def tar_dir(self, source: Path, target_name: str) -> Optional[Path]:
        if not source.exists():
            return None
        target = self.artifacts_dir / target_name
        with tarfile.open(target, "w:gz") as tar:
            tar.add(source, arcname=source.name)
        return target
