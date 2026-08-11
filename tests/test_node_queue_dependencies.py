from pathlib import Path
from tempfile import TemporaryDirectory

from utat.node_queue import NodeQueue


def test_ut_does_not_claim_before_same_app_at_finishes():
    with TemporaryDirectory() as td:
        q = NodeQueue(Path(td) / "queue.db")
        q.init()
        ut = q.submit({"issue_id": "ut-app", "root_issue_id": "root", "app_issue_id": "app", "task_type": "UT", "app_name": "app", "node_id": "local"})
        at = q.submit({"issue_id": "at-app", "root_issue_id": "root", "app_issue_id": "app", "task_type": "AT", "app_name": "app", "node_id": "local"})

        claimed = q.claim_next("local", 100)
        assert claimed and claimed["id"] == at["id"]
        q.mark_running(claimed["id"], 101, "/tmp/at.log")
        assert q.claim_next("local", 100) is None
        q.mark_result_ready(claimed["id"], result_path="/tmp/at.json", artifact_dir="/tmp/at", exit_code=0)
        claimed2 = q.claim_next("local", 100)
        assert claimed2 and claimed2["id"] == ut["id"]


def test_ut_only_blocked_by_same_app_at_not_other_app():
    with TemporaryDirectory() as td:
        q = NodeQueue(Path(td) / "queue.db")
        q.init()
        ut1 = q.submit({"issue_id": "ut-app1", "root_issue_id": "root", "app_issue_id": "app1", "task_type": "UT", "app_name": "app1", "node_id": "local"})
        q.submit({"issue_id": "at-app2", "root_issue_id": "root", "app_issue_id": "app2", "task_type": "AT", "app_name": "app2", "node_id": "local"})
        claimed = q.claim_next("local", 100)
        assert claimed and claimed["id"] == ut1["id"]
