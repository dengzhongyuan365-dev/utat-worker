from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List

from .config import WorkerConfig


class MulticaError(RuntimeError):
    pass


class MulticaClient:
    def __init__(self, cfg: WorkerConfig):
        self.cfg = cfg

    def _cmd(self, args: List[str]) -> List[str]:
        cmd = [self.cfg.multica_cli]
        if self.cfg.workspace_id:
            cmd += ["--workspace-id", self.cfg.workspace_id]
        return cmd + args

    def _env(self) -> Dict[str, str]:
        env = dict(os.environ)
        # Avoid leaking a short-lived task token into background callbacks.
        env.pop("MULTICA_TOKEN", None)
        env.pop("MULTICA_TASK_TOKEN", None)
        return env

    def run(self, args: List[str], *, timeout: int = 120, cwd: str | Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
        cp = subprocess.run(self._cmd(args), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, env=self._env(), cwd=str(cwd) if cwd else None)
        if cp.returncode != 0 and self._is_auth_error(cp) and self.cfg.pinned_token:
            self.login(cwd=cwd)
            cp = subprocess.run(self._cmd(args), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, env=self._env(), cwd=str(cwd) if cwd else None)
        if check and cp.returncode != 0:
            raise MulticaError(cp.stderr.strip() or cp.stdout.strip() or f"multica rc={cp.returncode}")
        return cp

    @staticmethod
    def _is_auth_error(cp: subprocess.CompletedProcess[str]) -> bool:
        text = f"{cp.stdout}\n{cp.stderr}".lower()
        return "登录" in text or "auth" in text or "token" in text or "not logged" in text

    def login(self, cwd: str | Path | None = None) -> None:
        if not self.cfg.pinned_token:
            raise MulticaError("missing code-pinned multica token")
        cmd = [self.cfg.multica_cli]
        if self.cfg.server_url:
            cmd += ["--server-url", self.cfg.server_url]
        cmd += ["login", "--token", self.cfg.pinned_token]
        cp = subprocess.run(cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60, env=self._env(), cwd=str(cwd) if cwd else None)
        if cp.returncode != 0:
            raise MulticaError(cp.stderr.strip() or cp.stdout.strip() or "multica login failed")

    def json(self, args: List[str], **kw: Any) -> Any:
        cp = self.run(args + ["--output", "json"], **kw)
        return json.loads(cp.stdout) if cp.stdout.strip() else None

    def issue_exists(self, issue_id: str) -> bool:
        try:
            self.json(["issue", "get", issue_id], timeout=60)
            return True
        except Exception as exc:
            return not self.is_not_found_error(exc)

    @staticmethod
    def is_not_found_error(exc: Exception) -> bool:
        text = str(exc).lower()
        return "not found" in text or "未找到" in text or "404" in text or "不存在" in text

    def metadata_set(self, issue_id: str, key: str, value: Any, value_type: str = "string") -> None:
        if not isinstance(value, str):
            value = json.dumps(value, ensure_ascii=False)
            value_type = "json"
        self.json(["issue", "metadata", "set", issue_id, "--key", key, "--value", value, "--type", value_type], timeout=60)

    def comment_add(self, issue_id: str, content: str, attachments: Iterable[str] = (), cwd: str | Path | None = None) -> None:
        tmpdir = Path(cwd) if cwd else Path(tempfile.mkdtemp(prefix="utat-v2-comment-"))
        tmpdir.mkdir(parents=True, exist_ok=True)
        content_path = tmpdir / "comment.md"
        content_path.write_text(content, encoding="utf-8")
        args = ["issue", "comment", "add", issue_id, "--content-file", str(content_path)]
        for a in attachments:
            if a and Path(a).exists():
                args += ["--attachment", str(Path(a))]
        self.json(args, timeout=300, cwd=tmpdir)
