from utat.multica_client import MulticaClient


def test_multica_client_env_is_non_agent(monkeypatch):
    monkeypatch.setenv("MULTICA_TOKEN", "mat_task_token")
    monkeypatch.setenv("UTAT_MULTICA_TOKEN", "bad")
    monkeypatch.setenv("MULTICA_RUN_ID", "run-1")
    monkeypatch.setenv("AGENT_TASK_ID", "task-1")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    env = MulticaClient("ws-1", server_url="https://agentapi-dev.uniontech.com")._env()

    assert env["MULTICA_WORKSPACE_ID"] == "ws-1"
    assert env["MULTICA_SERVER_URL"] == "https://agentapi-dev.uniontech.com"
    assert "MULTICA_TOKEN" not in env
    assert "UTAT_MULTICA_TOKEN" not in env
    assert "MULTICA_RUN_ID" not in env
    assert "AGENT_TASK_ID" not in env
    assert env["PATH"] == "/usr/bin:/bin"
