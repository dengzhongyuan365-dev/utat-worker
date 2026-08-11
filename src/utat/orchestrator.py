from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List

from .issue_parser import AppSpec, extract_timestamp_from_root_title, flatten_children, parse_root_description, child_by_title, resolve_run_types
from .multica_client import MulticaClient, MulticaError
from .queue_db import QueueDB


class Orchestrator:
    def __init__(self, db: QueueDB, multica: MulticaClient, config: Dict[str, Any]):
        self.db = db
        self.multica = multica
        self.config = config

    def scan_root(self, root_issue_id: str, *, apply: bool = False) -> Dict[str, Any]:
        root = self.multica.issue_get(root_issue_id)
        title = root.get("title") or ""
        desc = root.get("description") or ""
        timestamp = extract_timestamp_from_root_title(title)
        root_run_types = resolve_run_types(title, desc)
        root_job_id = self.db.upsert_root(root_issue_id, title, status="queued")

        root_children = flatten_children(self.multica.issue_children(root_issue_id))
        specs = parse_root_description(desc)
        # Fallback: use existing app children if description is loose.
        if not specs and root_children:
            for c in root_children:
                m = re.match(r"(?:AT-UT|AT|UT)-\d{12}-(.+?)(?:-应用)?$", c.get("title", ""))
                if m:
                    specs.append(AppSpec(app_name=m.group(1), repo="", branch="master"))

        created: List[Dict[str, Any]] = []
        plan: List[Dict[str, Any]] = []
        routing = self.config.get("routing") or {}

        for idx, spec in enumerate(specs):
            run_types = spec.run_types or root_run_types
            app_prefix = "AT-UT" if run_types == ["AT", "UT"] else run_types[0]
            app_title = f"AT-UT-{timestamp}-{spec.app_name}" if app_prefix == "AT-UT" else f"{app_prefix}-{timestamp}-{spec.app_name}-应用"
            app_issue = child_by_title(root_children, app_title)
            if not app_issue and apply:
                app_issue = self.multica.issue_create(title=app_title, parent=root_issue_id, status="backlog", description=self._app_desc(spec))
                created.append({"type": "app", "id": app_issue.get("id"), "title": app_title})
            app_issue_id = app_issue.get("id") if app_issue else ""
            app_task_id = self.db.upsert_app(root_job_id, spec.app_name, app_issue_id=app_issue_id, repo=spec.repo, branch=spec.branch, validation_mode=spec.validation_mode, route_policy=spec.route, sort_order=idx)

            exec_children: List[Dict[str, Any]] = []
            if app_issue_id:
                exec_children = flatten_children(self.multica.issue_children(app_issue_id))
            preferred = routing.get(spec.app_name, {}).get("preferred_nodes") or ([spec.route] if spec.route else [])
            for typ in run_types:
                exec_title = f"{typ}-{timestamp}-{spec.app_name}"
                ex = child_by_title(exec_children, exec_title)
                if not ex and apply and app_issue_id:
                    ex = self.multica.issue_create(title=exec_title, parent=app_issue_id, status="backlog", description=self._exec_desc(spec, typ, app_issue_id, root_issue_id))
                    created.append({"type": typ, "id": ex.get("id"), "title": exec_title})
                issue_id = ex.get("id") if ex else ""
                if issue_id:
                    project_root = f"~/atut-work/{self._repo_name(spec.repo) or spec.app_name}"
                    script = "test-prj-running.sh" if typ == "UT" else ""
                    self.db.upsert_exec(root_issue_id=root_issue_id, app_task_id=app_task_id, app_issue_id=app_issue_id, issue_id=issue_id, task_type=typ, app_name=spec.app_name, repo=spec.repo, branch=spec.branch, project_root=project_root, validation_mode=spec.validation_mode, test_scope=spec.test_scope, test_script=script, preferred_nodes=preferred, status="waiting")
                plan.append({"app": spec.app_name, "type": typ, "title": exec_title, "issue_id": issue_id, "preferred_nodes": preferred, "run_types": run_types})
        return {"root_issue_id": root_issue_id, "title": title, "timestamp": timestamp, "run_types": root_run_types, "apps": [s.__dict__ for s in specs], "created": created, "plan": plan}

    def schedule_once(self) -> List[Dict[str, Any]]:
        self.db.refresh_app_statuses()
        return self.db.make_dispatchable_tasks()

    def run_loop(self, interval: int = 30) -> None:
        print(f"orchestrator loop started interval={interval}s")
        while True:
            tasks = self.schedule_once()
            for task in tasks:
                print(f"dispatchable: {task['task_type']} {task['app_name']} issue={task['issue_id']}")
            time.sleep(interval)

    def _repo_name(self, repo: str) -> str:
        if not repo:
            return ""
        name = repo.rstrip("/").split("/")[-1]
        return name[:-4] if name.endswith(".git") else name

    def _app_desc(self, spec: AppSpec) -> str:
        types = ",".join(spec.run_types or []) if spec.run_types else "继承总入口"
        return f"应用：{spec.app_name}\n仓库：{spec.repo}\n分支：{spec.branch}\n验证模式：{spec.validation_mode}\n测试范围：{spec.test_scope}\n执行类型：{types}\n"

    def _exec_desc(self, spec: AppSpec, typ: str, app_issue_id: str, root_issue_id: str) -> str:
        lines = [
            f"应用：{spec.app_name}",
            f"REPO：{spec.repo}",
            f"BRANCH：{spec.branch}",
            f"PROJECT_ROOT：~/atut-work/{self._repo_name(spec.repo) or spec.app_name}",
            f"VALIDATION_MODE：{spec.validation_mode}",
            "VERSION_SOURCE：latest",
            "NO_CODE_UPDATE：false",
            f"TEST_SCOPE：{spec.test_scope}",
            f"TASK_TYPE：{typ}",
            f"父应用 issue：https://agent-dev.uniontech.com/v25/issues/{app_issue_id}",
            f"总入口 issue：https://agent-dev.uniontech.com/v25/issues/{root_issue_id}",
            "",
            "本 issue 由 utat-worker 执行，不依赖 Multica Agent 长会话。",
        ]
        if typ == "UT":
            lines.insert(7, "TEST_SCRIPT：test-prj-running.sh")
        return "\n".join(lines)
