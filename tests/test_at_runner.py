from pathlib import Path

from utat.runner.at_runner import parse_at_log


def test_parse_at_log_marks_failed_specs(tmp_path):
    log = tmp_path / "at-run.log"
    log.write_text(
        """
ERROR: 应用程序未启动,deepin-voice-note
============================================================
Suites: 0 passed, 50 failed, 0 error (50 total)
Specs:  0 passed, 50 failed, 0 skipped
============================================================
""",
        encoding="utf-8",
    )
    metrics = parse_at_log(log)
    assert metrics["failed"] == 50
    assert metrics["passed"] == 0
    assert metrics["total"] == 50
    assert metrics["pass_rate"] == 0
    assert "应用程序未启动" in metrics["failure_reason"]

from utat.runner.at_runner import ATRunner


def test_app_check_fails_fast_when_command_missing(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    runner = ATRunner({"id": "t1", "issue_id": "i", "app_name": "app", "repo": "https://example.invalid/not-existing-app", "environment": {}}, tmp_path / "task-app")
    rc, log = runner.check_application_available(root)
    assert rc == 127
    assert "应用未安装或命令不存在" in log.read_text(encoding="utf-8")
