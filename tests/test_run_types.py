from pathlib import Path
import tempfile

from utat.issue_parser import resolve_run_types, extract_timestamp_from_root_title
from utat.orchestrator import Orchestrator
from utat.queue_db import QueueDB


class FakeMultica:
    def __init__(self, title: str, desc: str):
        self.title = title
        self.desc = desc
        self.created = []

    def issue_get(self, issue_id):
        return {"id": issue_id, "title": self.title, "description": self.desc}

    def issue_children(self, issue_id):
        return {"unstaged": [], "stages": []}

    def issue_create(self, **kw):
        obj = {"id": f"id{len(self.created)+1}", "title": kw["title"]}
        self.created.append({**kw, **obj})
        return obj


def scan_types(title: str, desc: str):
    with tempfile.TemporaryDirectory() as d:
        db = QueueDB(Path(d) / "q.db")
        db.init()
        mc = FakeMultica(title, desc)
        out = Orchestrator(db, mc, {}).scan_root("root1", apply=True)
        return [x["type"] for x in out["plan"]], [c["title"] for c in mc.created], out["run_types"]


def test_resolve_run_types_from_title_and_content_override():
    assert resolve_run_types("AT-202608111430", "") == ["AT"]
    assert resolve_run_types("UT-202608111430", "") == ["UT"]
    assert resolve_run_types("AT-UT-202608111430", "") == ["AT", "UT"]
    assert resolve_run_types("AT-202608111430", "执行类型：UT") == ["UT"]
    assert resolve_run_types("UT-202608111430", "AT、UT都跑") == ["AT", "UT"]


def test_extract_timestamp_accepts_single_type_titles():
    assert extract_timestamp_from_root_title("AT-202608111430") == "202608111430"
    assert extract_timestamp_from_root_title("UT-202608111431") == "202608111431"
    assert extract_timestamp_from_root_title("AT-UT-202608111432") == "202608111432"


def test_orchestrator_creates_only_at_for_at_root():
    types, titles, run_types = scan_types("AT-202608111430", "应用：语音记事本\n仓库：r")
    assert run_types == ["AT"]
    assert types == ["AT"]
    assert titles == ["AT-202608111430-语音记事本-应用", "AT-202608111430-语音记事本"]


def test_orchestrator_creates_only_ut_for_ut_root():
    types, titles, run_types = scan_types("UT-202608111430", "应用：语音记事本\n仓库：r")
    assert run_types == ["UT"]
    assert types == ["UT"]
    assert titles == ["UT-202608111430-语音记事本-应用", "UT-202608111430-语音记事本"]


def test_orchestrator_creates_both_for_at_ut_root_or_content_override():
    types, titles, run_types = scan_types("AT-UT-202608111430", "应用：语音记事本\n仓库：r")
    assert run_types == ["AT", "UT"]
    assert types == ["AT", "UT"]
    assert titles == [
        "AT-UT-202608111430-语音记事本",
        "AT-202608111430-语音记事本",
        "UT-202608111430-语音记事本",
    ]

    types, titles, run_types = scan_types("AT-202608111430", "执行类型：AT,UT\n应用：语音记事本\n仓库：r")
    assert run_types == ["AT", "UT"]
    assert types == ["AT", "UT"]
