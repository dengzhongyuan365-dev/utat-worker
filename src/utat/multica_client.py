from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


class MulticaError(RuntimeError):
    pass


class MulticaClient:
    def __init__(self, workspace_id: str, cli: str = "multica", server_url: str = "", profile: str = ""):
        self.workspace_id = workspace_id
        self.cli = cli
        self.server_url = server_url
        self.profile = profile

    def _base(self) -> List[str]:
        cmd = [self.cli]
        if self.workspace_id:
            cmd += ["--workspace-id", self.workspace_id]
        if self.server_url:
            cmd += ["--server-url", self.server_url]
        if self.profile:
            cmd += ["--profile", self.profile]
        return cmd

    def run(self, args: List[str], *, input_text: str | None = None, timeout: int = 60, check: bool = True) -> subprocess.CompletedProcess:
        p = subprocess.run(self._base() + args, input=input_text, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
        if check and p.returncode != 0:
            raise MulticaError(f"multica {' '.join(args)} failed rc={p.returncode}: {p.stderr.strip()}")
        return p

    def json(self, args: List[str], **kw: Any) -> Any:
        p = self.run(args + ["--output", "json"], **kw)
        if not p.stdout.strip():
            return None
        return json.loads(p.stdout)

    def issue_get(self, issue_id: str) -> Dict[str, Any]:
        return self.json(["issue", "get", issue_id])

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

    def comment_add(self, issue_id: str, content: str, attachments: Iterable[str] = ()) -> Dict[str, Any]:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as f:
            f.write(content)
            content_path = f.name
        try:
            args = ["issue", "comment", "add", issue_id, "--content-file", content_path]
            for a in attachments:
                if a and Path(a).exists():
                    args += ["--attachment", str(a)]
            return self.json(args, timeout=300)
        finally:
            Path(content_path).unlink(missing_ok=True)
