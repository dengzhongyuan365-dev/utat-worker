from pathlib import Path
from tempfile import TemporaryDirectory

from utat.node_queue import NodeQueue


def test_submit_claim_and_per_node_serial_queue():
    with TemporaryDirectory() as td:
        q = NodeQueue(Path(td) / "queue.db")
        q.init()
        a = q.submit({"issue_id": "at-mail", "task_type": "AT", "app_name": "deepin-mail", "node_id": "local"})
        b = q.submit({"issue_id": "at-file", "task_type": "AT", "app_name": "dde-file-manager", "node_id": "local"})
        assert a["state"] == "queued"
        assert q.queue_position(a["id"]) == 1
        assert q.queue_position(b["id"]) == 2

        claimed = q.claim_next("local", 100)
        assert claimed and claimed["issue_id"] == "at-mail"
        q.mark_running(claimed["id"], 101, "/tmp/mail.log")
        assert q.get(claimed["id"])["state"] == "running"

        # The queue itself only claims one at a time; the worker lock enforces
        # the same invariant even if multiple worker processes are started.
        q.mark_result_ready(claimed["id"], result_path="/tmp/mail.json", artifact_dir="/tmp/mail", exit_code=0)
        next_task = q.claim_next("local", 100)
        assert next_task and next_task["issue_id"] == "at-file"


def test_submit_is_idempotent_for_same_issue():
    with TemporaryDirectory() as td:
        q = NodeQueue(Path(td) / "queue.db")
        q.init()
        one = q.submit({"issue_id": "same", "task_type": "UT", "app_name": "app", "node_id": "local"})
        two = q.submit({"issue_id": "same", "task_type": "UT", "app_name": "app", "node_id": "local"})
        assert one["id"] == two["id"]
        assert len(q.list()) == 1


def test_finished_or_stale_issue_can_be_submitted_again_as_new_task():
    with TemporaryDirectory() as td:
        q = NodeQueue(Path(td) / "queue.db")
        q.init()
        one = q.submit({"issue_id": "rerun", "task_type": "UT", "app_name": "app", "node_id": "local"})
        q.mark_result_ready(one["id"], result_path="/tmp/old.json", artifact_dir="/tmp/old", exit_code=0, state="result_ready")

        two = q.submit({"issue_id": "rerun", "task_type": "UT", "app_name": "app", "node_id": "local"})

        assert two["id"] != one["id"]
        assert two["state"] == "queued"
        assert len(q.list()) == 1


def test_init_purges_non_active_history_rows():
    with TemporaryDirectory() as td:
        db = Path(td) / "queue.db"
        q = NodeQueue(db)
        q.init()
        active = q.submit({"issue_id": "active", "task_type": "UT", "app_name": "app", "node_id": "local"})
        stale = q.submit({"issue_id": "stale", "task_type": "UT", "app_name": "app", "node_id": "local"})
        q.mark_result_ready(stale["id"], result_path="/tmp/stale.json", artifact_dir="/tmp/stale", exit_code=0, state="result_ready")
        q.close()

        q2 = NodeQueue(db)
        q2.init()
        rows = q2.list()
        assert [r["issue_id"] for r in rows] == ["active"]
        assert rows[0]["id"] == active["id"]
