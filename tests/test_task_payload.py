import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from utat.node_cli import main
from utat.task_payload import load_payload_file, normalize_payload, resolve_environment, validate_payload


def test_normalize_structured_payload_and_resolve_env(monkeypatch):
    monkeypatch.setenv("MAIL_ACCOUNT", "tester@example.com")
    payload = normalize_payload(
        {
            "schema": "utat-task/v1",
            "issue": {"id": "at-1", "root_id": "root-1", "app_id": "app-1"},
            "task": {"type": "AT", "app_name": "deepin-mail", "node_id": "local"},
            "source": {"repo": "https://example.invalid/mail.git", "branch": "develop", "project_root": "~/atut-work/deepin-mail"},
            "test": {"suite": "login", "spec_ids": ["1", "2"], "at_path": "~/atut-work/deepin-mail/at"},
            "environment": {"MAIL_AT_ACCOUNT": "${MAIL_ACCOUNT}"},
        }
    )
    validate_payload(payload)
    assert payload["issue_id"] == "at-1"
    assert payload["suite"] == "login"
    assert payload["spec_ids"] == ["1", "2"]
    assert resolve_environment(payload)["MAIL_AT_ACCOUNT"] == "tester@example.com"


def test_unresolved_environment_fails(monkeypatch):
    monkeypatch.delenv("MISSING_ACCOUNT", raising=False)
    with pytest.raises(ValueError, match="MISSING_ACCOUNT"):
        resolve_environment({"environment": {"MAIL_AT_ACCOUNT": "${MISSING_ACCOUNT}"}})


def test_submit_from_payload_file_without_cli_flags(tmp_path, monkeypatch, capsys):
    db = tmp_path / "queue.db"
    payload = {
        "issue": {"id": "at-1"},
        "task": {"type": "AT", "app_name": "deepin-mail", "node_id": "local"},
        "source": {"repo": "https://example.invalid/mail.git"},
    }
    payload_file = tmp_path / "task.json"
    payload_file.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("HOME", str(tmp_path))
    main(["--db", str(db), "submit", "--payload-file", str(payload_file), "--no-auto-start"])
    output = json.loads(capsys.readouterr().out)
    assert output["issue_id"] == "at-1"
    assert output["state"] == "queued"


def test_payload_workspace_and_placeholder_cleanup():
    payload = normalize_payload(
        {
            "issue": {"id": "at-2", "root_id": "root-2", "app_id": "app-2", "workspace_id": "ws-1"},
            "task": {"type": "AT", "app_name": "app", "node_id": "local"},
            "test": {"suite": "可选", "at_path": "可选", "script": "UT脚本可选"},
            "build": {"build_command": "可选", "install_command": "待填写"},
        }
    )
    validate_payload(payload)
    assert payload["workspace_id"] == "ws-1"
    assert payload["suite"] == ""
    assert payload["at_path"] == ""
    assert payload["test_script"] == ""
    assert payload["build_command"] == ""
    assert payload["install_command"] == ""
