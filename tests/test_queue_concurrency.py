from pathlib import Path
from tempfile import TemporaryDirectory

from utat.queue_db import QueueDB


def make_db():
    td = TemporaryDirectory()
    db = QueueDB(Path(td.name) / "queue.db")
    db.init()
    return td, db


def seed(db: QueueDB):
    root_job = db.upsert_root("root-1", "AT-UT-202608101000", status="queued")
    app1 = db.upsert_app(root_job, "deepin-mail", app_issue_id="app-mail", sort_order=0)
    app2 = db.upsert_app(root_job, "deepin-editor", app_issue_id="app-editor", sort_order=1)
    db.upsert_exec(root_issue_id="root-1", app_task_id=app1, app_issue_id="app-mail", issue_id="at-mail", task_type="AT", app_name="deepin-mail", preferred_nodes=["local"])
    db.upsert_exec(root_issue_id="root-1", app_task_id=app1, app_issue_id="app-mail", issue_id="ut-mail", task_type="UT", app_name="deepin-mail", preferred_nodes=["local"])
    db.upsert_exec(root_issue_id="root-1", app_task_id=app2, app_issue_id="app-editor", issue_id="at-editor", task_type="AT", app_name="deepin-editor", preferred_nodes=["public-test"])
    db.upsert_exec(root_issue_id="root-1", app_task_id=app2, app_issue_id="app-editor", issue_id="ut-editor", task_type="UT", app_name="deepin-editor", preferred_nodes=["public-test"])


def test_per_node_serial_cross_node_parallel_and_app_at_before_ut():
    td, db = make_db()
    try:
        seed(db)
        ready = db.make_dispatchable_tasks()
        assert {(t["issue_id"], t["task_type"]) for t in ready} == {("at-mail", "AT"), ("at-editor", "AT")}

        local_task = db.claim_task("local", {"apps": ["deepin-mail"], "task_types": ["AT", "UT"]})
        assert local_task and local_task["issue_id"] == "at-mail"

        # Same node cannot claim a second AT/UT while one is active.
        assert db.claim_task("local", {"apps": ["deepin-mail"], "task_types": ["AT", "UT"]}) is None

        # Another node can work at the same time.
        public_task = db.claim_task("public-test", {"apps": ["deepin-editor"], "task_types": ["AT", "UT"]})
        assert public_task and public_task["issue_id"] == "at-editor"

        # UT of the same app is still blocked while AT is active.
        assert db.get_exec("ut-mail")["status"] == "waiting"

        db.complete_task(local_task["id"], "done", {"status": "done"})
        ready2 = db.make_dispatchable_tasks()
        assert [(t["issue_id"], t["task_type"]) for t in ready2] == [("ut-mail", "UT")]

        ut_mail = db.claim_task("local", {"apps": ["deepin-mail"], "task_types": ["AT", "UT"]})
        assert ut_mail and ut_mail["issue_id"] == "ut-mail"
    finally:
        td.cleanup()


if __name__ == "__main__":
    test_per_node_serial_cross_node_parallel_and_app_at_before_ut()
