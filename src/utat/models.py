from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, Optional


@dataclass
class ExecTask:
    id: str
    root_issue_id: str
    app_issue_id: str
    issue_id: str
    task_type: str
    app_name: str
    repo: str = ""
    branch: str = ""
    project_root: str = ""
    validation_mode: str = "full"
    test_scope: str = ""
    test_script: str = ""
    preferred_nodes: str = "[]"
    claimed_by: str = ""
    pid: Optional[int] = None
    phase: str = "waiting"
    status: str = "waiting"
    result_json: str = ""
    log_path: str = ""
    started_at: str = ""
    finished_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
