from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, Literal

TaskType = Literal["AT", "UT"]
TaskState = Literal[
    "queued",
    "running",
    "callback_pending",
    "callback_failed",
    "completed",
    "deleted",
    "orphan",
    "failed_to_submit",
]


@dataclass
class TaskPayload:
    schema_version: str
    task_type: TaskType
    issue_id: str
    workspace_id: str
    root_issue_id: str
    app_issue_id: str
    app_name: str
    repo_name: str
    repo_url: str
    repo: str
    branch: str
    node_id: str = "local"
    execution_mode: str = "full"
    no_code_update: bool = False
    project_root: str = ""
    attempt: int = 1
    build_enabled: bool = False
    install_enabled: bool = False
    test_script: str = ""
    callback: Dict[str, Any] = field(default_factory=dict)
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskPayload":
        typ = str(data.get("task_type") or "").upper()
        if typ not in {"AT", "UT"}:
            raise ValueError("task_type must be AT or UT")
        issue_id = str(data.get("issue_id") or "").strip()
        if not issue_id:
            raise ValueError("missing issue_id")
        repo_url = str(data.get("repo_url") or data.get("repo") or "").strip()
        if not repo_url:
            raise ValueError("missing repo_url/repo")
        repo_name = str(data.get("repo_name") or repo_url.rstrip("/").split("/")[-1].removesuffix(".git"))
        build_enabled = bool(data.get("build_enabled")) if "build_enabled" in data else typ == "AT"
        install_enabled = bool(data.get("install_enabled")) if "install_enabled" in data else typ == "AT"
        if typ == "UT":
            build_enabled = False
            install_enabled = False
        return cls(
            schema_version=str(data.get("schema_version") or "v2"),
            task_type=typ,  # type: ignore[arg-type]
            issue_id=issue_id,
            workspace_id=str(data.get("workspace_id") or ""),
            root_issue_id=str(data.get("root_issue_id") or ""),
            app_issue_id=str(data.get("app_issue_id") or ""),
            app_name=str(data.get("app_name") or data.get("app") or ""),
            repo_name=repo_name,
            repo_url=repo_url,
            repo=str(data.get("repo") or repo_url),
            branch=str(data.get("branch") or "master"),
            node_id=str(data.get("node_id") or "local"),
            execution_mode=str(data.get("execution_mode") or data.get("validation_mode") or "full"),
            no_code_update=bool(data.get("no_code_update", False)),
            project_root=str(data.get("project_root") or ""),
            attempt=int(data.get("attempt") or 1),
            build_enabled=build_enabled,
            install_enabled=install_enabled,
            test_script=str(data.get("test_script") or ("test-prj-running.sh" if typ == "UT" else "")),
            callback=dict(data.get("callback") or {}),
            raw=dict(data),
        )

    def to_dict(self) -> Dict[str, Any]:
        d = dict(self.raw)
        d.update({
            "schema_version": self.schema_version,
            "task_type": self.task_type,
            "issue_id": self.issue_id,
            "workspace_id": self.workspace_id,
            "root_issue_id": self.root_issue_id,
            "app_issue_id": self.app_issue_id,
            "app_name": self.app_name,
            "repo_name": self.repo_name,
            "repo_url": self.repo_url,
            "repo": self.repo,
            "branch": self.branch,
            "node_id": self.node_id,
            "execution_mode": self.execution_mode,
            "no_code_update": self.no_code_update,
            "project_root": self.project_root,
            "attempt": self.attempt,
            "build_enabled": self.build_enabled,
            "install_enabled": self.install_enabled,
            "test_script": self.test_script,
            "callback": self.callback,
        })
        return d


def now_ts() -> float:
    return time.time()
