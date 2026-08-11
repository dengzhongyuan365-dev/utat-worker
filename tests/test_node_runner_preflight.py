from pathlib import Path

from utat.multica_client import MulticaError
from utat.node_runner import NodeRunner


class MissingRootClient:
    def issue_get(self, issue_id):
        raise MulticaError("multica issue get failed rc=4: not found")


def make_runner(tmp_path):
    cfg = {
        "workspace_id": "ws-1",
        "node": {"node_id": "local", "home": str(tmp_path / "node"), "queue_db": str(tmp_path / "queue.db"), "work_root": str(tmp_path / "tests")},
        "multica": {"cli": "multica", "server_url": "", "profile": "", "token": "token"},
    }
    return NodeRunner(cfg)


def test_preflight_deletes_task_when_root_issue_missing(tmp_path, monkeypatch):
    runner = make_runner(tmp_path)
    monkeypatch.setattr(runner, "_multica_for_workspace", lambda workspace_id: MissingRootClient())
    task = runner.queue.submit({
        "issue_id": "issue-1",
        "root_issue_id": "root-missing",
        "app_issue_id": "app-1",
        "workspace_id": "ws-1",
        "task_type": "AT",
        "app_name": "app",
        "node_id": "local",
        "payload_json": "{}",
    })

    runner.run_task(task)

    assert runner.queue.get(task["id"]) is None
    tombstone = tmp_path / "node" / "tasks" / task["id"] / "orphaned-root-issue-deleted.json"
    assert tombstone.exists()
    assert "root-issue-not-found-or-deleted" in tombstone.read_text(encoding="utf-8")
