from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .config import HARDCODED_MULTICA_TOKEN


class MulticaError(RuntimeError):
    pass


class MulticaClient:
    def __init__(self, workspace_id: str, cli: str = "multica", server_url: str = "", profile: str = "", token: str = "", safe_cwd: str | Path | None = None):
        self.workspace_id = workspace_id
        self.cli = cli
        self.server_url = server_url
        self.profile = profile
        # Background callbacks must always use the code-pinned token.
        # Do not trust environment variables, config token overrides, or stale CLI login state.
        self.token = HARDCODED_MULTICA_TOKEN
        self.safe_cwd = Path(safe_cwd).expanduser() if safe_cwd else Path(os.environ.get("UTAT_NODE_HOME", str(Path.home() / ".utat-node"))).expanduser() / "multica-cwd"

    def _base(self) -> List[str]:
        cmd = [self.cli]
        if self.workspace_id:
            cmd += ["--workspace-id", self.workspace_id]
        if self.server_url:
            cmd += ["--server-url", self.server_url]
        if self.profile:
            cmd += ["--profile", self.profile]
        return cmd

    def run(self, args: List[str], *, input_text: str | None = None, timeout: int = 60, check: bool = True, cwd: str | Path | None = None) -> subprocess.CompletedProcess:
        env = self._env()
        safe_cwd = self._safe_cwd(cwd)
        p = subprocess.run(self._base() + args, input=input_text, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, env=env, cwd=str(safe_cwd))
        if self.token and self._is_auth_error(p):
            self._login_with_token(env=env, cwd=safe_cwd)
            p = subprocess.run(self._base() + args, input=input_text, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, env=env, cwd=str(safe_cwd))
        if check and p.returncode != 0:
            raise MulticaError(f"multica {' '.join(args)} failed rc={p.returncode}: {p.stderr.strip()}")
        return p

    def _safe_cwd(self, cwd: str | Path | None = None) -> Path:
        # Never let background callbacks run from a Multica agent workdir.  The
        # CLI detects .multica/daemon_task_context.json in cwd/parents and then
        # rejects the normal pinned mul_ token as non task-scoped.
        p = Path(cwd).expanduser() if cwd is not None else self.safe_cwd
        p.mkdir(parents=True, exist_ok=True)
        return p

    def _env(self) -> Dict[str, str]:
        # Build a clean non-agent environment.  Workers are usually started by a
        # Multica agent process; inheriting its task-context variables makes the
        # CLI enter "agent execution context" and reject the pinned mul_ token
        # with: "requires MULTICA_TOKEN to be a task-scoped mat_ token".
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
            "http_proxy",
            "https_proxy",
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "no_proxy",
            "NO_PROXY",
            "SSL_CERT_FILE",
            "SSL_CERT_DIR",
        )
        env = {k: v for k, v in os.environ.items() if k in keep and v}
        env.setdefault("HOME", str(Path.home()))
        env.setdefault("USER", os.environ.get("USER") or Path.home().name)
        env.setdefault("LOGNAME", env["USER"])
        env.setdefault("SHELL", os.environ.get("SHELL") or "/bin/bash")
        env.setdefault("PATH", os.environ.get("PATH") or "/usr/local/bin:/usr/bin:/bin")
        env.setdefault("LANG", os.environ.get("LANG") or "C.UTF-8")
        if self.workspace_id:
            env["MULTICA_WORKSPACE_ID"] = self.workspace_id
        if self.server_url:
            env["MULTICA_SERVER_URL"] = self.server_url
        # Intentionally do not export MULTICA_TOKEN. Authentication is refreshed
        # through `multica login --token <code-pinned-token>` when needed.
        return env

    @staticmethod
    def _is_auth_error(p: subprocess.CompletedProcess) -> bool:
        if p.returncode == 0:
            return False
        text = f"{p.stdout or ''}\n{p.stderr or ''}"
        return "登录已过期" in text or "尚未登录" in text or "Authenticate" in text or "not logged" in text.lower()

    def _login_with_token(self, *, env: Dict[str, str], cwd: str | Path | None = None) -> None:
        # Refresh the local CLI session with the code-pinned token, then retry.
        login_cmd = [self.cli]
        if self.server_url:
            login_cmd += ["--server-url", self.server_url]
        if self.profile:
            login_cmd += ["--profile", self.profile]
        login_cmd += ["login", "--token", self.token]
        p = subprocess.run(login_cmd, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60, env=env, cwd=str(self._safe_cwd(cwd)))
        if p.returncode != 0:
            raise MulticaError(f"multica login --token failed rc={p.returncode}: {p.stderr.strip()}")

    def json(self, args: List[str], **kw: Any) -> Any:
        p = self.run(args + ["--output", "json"], **kw)
        if not p.stdout.strip():
            return None
        return json.loads(p.stdout)

    def issue_get(self, issue_id: str) -> Dict[str, Any]:
        return self.json(["issue", "get", issue_id])

    @staticmethod
    def is_not_found_error(exc: Exception) -> bool:
        text = str(exc)
        return "rc=4" in text or "未找到请求的资源" in text or "not found" in text.lower()

    def issue_children(self, issue_id: str) -> Dict[str, Any]:
        return self.json(["issue", "children", issue_id])

    def issue_create(self, *, title: str, description: str = "", parent: str = "", status: str = "backlog") -> Dict[str, Any]:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as f:
            f.write(description)
            desc_path = f.name
        try:
            args = ["issue", "create", "--title", title, "--description-file", desc_path, "--status", status]
            if parent:
                args += ["--parent", parent]
            return self.json(args, timeout=60)
        finally:
            Path(desc_path).unlink(missing_ok=True)

    def issue_update(self, issue_id: str, *, title: str | None = None, description: str | None = None, status: str | None = None) -> Dict[str, Any]:
        args = ["issue", "update", issue_id]
        tmp = None
        if title is not None:
            args += ["--title", title]
        if status is not None:
            args += ["--status", status]
        if description is not None:
            f = tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False)
            f.write(description)
            f.close()
            tmp = f.name
            args += ["--description-file", tmp]
        try:
            return self.json(args, timeout=60)
        finally:
            if tmp:
                Path(tmp).unlink(missing_ok=True)

    def issue_status(self, issue_id: str, status: str) -> Any:
        return self.run(["issue", "status", issue_id, status, "--output", "json"], timeout=60, check=True).stdout

    def metadata_set(self, issue_id: str, key: str, value: str, *, value_type: str = "string") -> Any:
        args = ["issue", "metadata", "set", issue_id, "--key", key, "--value", value]
        if value_type:
            args += ["--type", value_type]
        return self.json(args, timeout=60)

    def metadata_get(self, issue_id: str, key: str) -> Any:
        return self.json(["issue", "metadata", "get", issue_id, "--key", key], timeout=60)

    def metadata_list(self, issue_id: str) -> Any:
        return self.json(["issue", "metadata", "list", issue_id], timeout=60)

    def issue_rerun(self, issue_id: str) -> Any:
        return self.json(["issue", "rerun", issue_id], timeout=60)

    def comment_add(self, issue_id: str, content: str, attachments: Iterable[str] = (), *, temp_dir: str | Path | None = None) -> Dict[str, Any]:
        tmp_dir = Path(temp_dir).expanduser() if temp_dir else Path.cwd()
        tmp_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False, dir=str(tmp_dir)) as f:
            f.write(content)
            content_path = Path(f.name)
        try:
            args = ["issue", "comment", "add", issue_id, "--content-file", content_path.name]
            for a in attachments:
                if not a:
                    continue
                ap = Path(a)
                if not ap.exists():
                    continue
                try:
                    rel = ap.resolve().relative_to(tmp_dir.resolve())
                    args += ["--attachment", str(rel)]
                except Exception:
                    args += ["--attachment", str(ap)]
            return self.json(args, timeout=300, cwd=tmp_dir)
        finally:
            content_path.unlink(missing_ok=True)
