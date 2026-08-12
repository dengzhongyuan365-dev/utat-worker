from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    # Reuse the existing code-pinned token without duplicating it.
    from utat.config import HARDCODED_MULTICA_TOKEN as _PINNED_TOKEN  # type: ignore
except Exception:  # pragma: no cover - available in the real repo, optional in standalone dry-runs
    _PINNED_TOKEN = ""


@dataclass(frozen=True)
class WorkerConfig:
    node_id: str
    state_home: Path
    db_path: Path
    work_root: Path
    archive_root: Path
    multica_cli: str
    workspace_id: str
    server_url: str
    pinned_token: str
    poll_interval_sec: float
    idle_exit_sec: float
    web_host: str
    web_port: int


def expand(p: str | Path) -> Path:
    return Path(p).expanduser().resolve()


def load_config() -> WorkerConfig:
    state_home = expand(os.environ.get("UTAT_STATE_HOME", "~/.utat-node"))
    return WorkerConfig(
        node_id=os.environ.get("UTAT_NODE_ID", "local"),
        state_home=state_home,
        db_path=expand(os.environ.get("UTAT_DB", str(state_home / "queue.db"))),
        work_root=expand(os.environ.get("UTAT_WORK_ROOT", "~/atut-work")),
        archive_root=expand(os.environ.get("UTAT_ARCHIVE_ROOT", "~/Documents/ATUT-WORK-Archive")),
        multica_cli=os.environ.get("UTAT_MULTICA_CLI", "multica"),
        workspace_id=os.environ.get("UTAT_WORKSPACE_ID", "b982c611-c032-4874-ac62-0f66ae001f2f"),
        server_url=os.environ.get("UTAT_MULTICA_SERVER_URL", "https://agentapi-dev.uniontech.com"),
        pinned_token=_PINNED_TOKEN,
        poll_interval_sec=float(os.environ.get("UTAT_POLL_INTERVAL", "5")),
        idle_exit_sec=float(os.environ.get("UTAT_IDLE_EXIT", "300")),
        web_host=os.environ.get("UTAT_WEB_HOST", "0.0.0.0"),
        web_port=int(os.environ.get("UTAT_WEB_PORT", "8766")),
    )
