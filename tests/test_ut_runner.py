from pathlib import Path

from utat.runner.ut_runner import UTRunner, parse_ut_log, parse_ut_xml_reports


def test_parse_ut_xml_reports_uses_gtest_xml(tmp_path):
    report_dir = tmp_path / "tests" / "build-qt6" / "report"
    report_dir.mkdir(parents=True)
    (report_dir / "suite-a.xml").write_text(
        """
<testsuites tests="5" failures="1" errors="0" skipped="1" disabled="0">
</testsuites>
""",
        encoding="utf-8",
    )
    metrics = parse_ut_xml_reports([report_dir])
    assert metrics["total"] == 5
    assert metrics["failed"] == 1
    assert metrics["passed"] == 3
    assert metrics["pass_rate"] == 60.0
    assert metrics["skipped"] == 1


def test_parse_ut_log_falls_back_to_suite_summary(tmp_path):
    log = tmp_path / "ut-run.log"
    log.write_text(
        """
[11:26:10] ⑤ 枚举测试套件
    共 10 个测试套件
[11:26:11] ⑥ 运行测试 (每套件独立进程, 超时 90s/套件)
    进度: 10/10
    执行 10 套件, 异常退出 2 套件
    失败用例数(汇总): 3
    lines......: 73.9% (15269 of 20666 lines)
    functions..: 87.2% (1267 of 1453 functions)
""",
        encoding="utf-8",
    )
    metrics = parse_ut_log(log)
    assert metrics["metric_source"] == "suite-summary"
    assert metrics["total"] == 10
    assert metrics["passed"] == 8
    assert metrics["failed"] == 2
    assert metrics["failed_cases"] == 3
    assert metrics["pass_rate"] == 80.0
    assert metrics["line_coverage"] == "73.9%"
    assert metrics["function_coverage"] == "87.2%"


def test_ut_runner_skips_build_and_install(tmp_path, monkeypatch):
    root = tmp_path / "repo"
    root.mkdir()
    script = root / "test-prj-running.sh"
    script.write_text("#!/bin/sh\necho done\n", encoding="utf-8")
    task_dir = tmp_path / "task"
    runner = UTRunner(
        {
            "id": "t1",
            "issue_id": "issue-1",
            "app_name": "app",
            "repo": "https://example.invalid/app.git",
            "branch": "master",
            "project_root": str(root),
            "environment": {},
        },
        task_dir,
    )

    source_log = task_dir / "logs" / "source-sync.log"
    source_log.parent.mkdir(parents=True, exist_ok=True)
    source_log.write_text("synced\n", encoding="utf-8")

    monkeypatch.setattr(runner, "prepare_source", lambda: (root, 0, source_log))
    monkeypatch.setattr(runner, "install_build_deps", lambda _root: (_ for _ in ()).throw(AssertionError("install_build_deps should not be called")))
    monkeypatch.setattr(runner, "run_build_steps", lambda _root: (_ for _ in ()).throw(AssertionError("run_build_steps should not be called")))

    def fake_run_process(cmd, *, cwd, log_path, env=None, shell=False, phase="running"):
        log_path.write_text(
            """
执行 2 套件, 异常退出 0 套件
失败用例数(汇总): 0
lines......: 100.0% (10 of 10 lines)
functions..: 100.0% (5 of 5 functions)
""",
            encoding="utf-8",
        )
        return 0

    monkeypatch.setattr(runner, "run_process", fake_run_process)
    monkeypatch.setattr(runner, "tar_dir", lambda *_args, **_kwargs: None)

    result = runner.run()
    assert result["status"] == "done"
    assert result["metrics"]["total"] == 2
    assert result["metrics"]["passed"] == 2
    assert result["metrics"]["failed"] == 0
    assert result["metrics"]["pass_rate"] == 100.0
