from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

DEFAULT_HOME = Path(os.environ.get("UTAT_HOME", str(Path.home() / ".utat-worker")))
DEFAULT_DB = DEFAULT_HOME / "queue.db"
DEFAULT_NODE_HOME = Path(os.environ.get("UTAT_NODE_HOME", str(Path.home() / ".utat-node")))
DEFAULT_NODE_DB = DEFAULT_NODE_HOME / "queue.db"
DEFAULT_CONFIG = DEFAULT_HOME / "config.json"
DEFAULT_MULTICA_ENV = DEFAULT_HOME / "multica.env"
HARDCODED_MULTICA_TOKEN = "mul_8dcefbc9d85b2fa1c57810850e537986f7284e28"

DEFAULTS: Dict[str, Any] = {
    "workspace_id": "",
    "server": {"host": "127.0.0.1", "port": 8765, "token_env": "UTAT_SERVER_TOKEN"},
    "multica": {"cli": "multica", "server_url": "", "profile": "", "token": HARDCODED_MULTICA_TOKEN},
    "scheduler": {"poll_interval_sec": 30},
    "worker": {
        "node_id": "local",
        "server_url": "http://127.0.0.1:8765",
        "work_root": "~/atut-work",
        "max_parallel": 1,
        "poll_interval_sec": 15,
        "capabilities": {"apps": [], "task_types": ["AT", "UT"]},
    },
    "routing": {},
    "mail": {"enabled": False},
    "node": {
        "node_id": "local",
        "home": str(DEFAULT_NODE_HOME),
        "queue_db": str(DEFAULT_NODE_DB),
        "work_root": "~/atut-work",
        "archive_root": "~/Documents/ATUT-WORK-Archive",
        "worker_cwd": "",
        "multica_cwd": "",
        "poll_interval_sec": 5,
        "idle_exit_sec": 300,
    },
    "callback": {
        "AT": {
            "agent_id": "6163dd0b-4cac-4d3c-9002-8f47855afaa2",
            "agent_name": "AT研发自测-local（实验）",
        },
        "UT": {
            "agent_id": "a9650cc4-237f-449d-b166-7757a16f0f72",
            "agent_name": "UT研发自测-local（实验）",
        },
    },
}


def deep_merge(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(a)
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_dotenv(path: str | Path | None = None) -> Dict[str, str]:
    p = Path(path).expanduser() if path else DEFAULT_MULTICA_ENV
    if not p.exists():
        return {}
    env: Dict[str, str] = {}
    for raw in p.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            env[key] = value
    return env


def apply_env_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    out = deep_merge({}, cfg)
    file_env = load_dotenv()
    merged = dict(file_env)
    # Only non-secret routing/runtime values are allowed from the environment.
    # Multica token is intentionally fixed in code and must not be overridden by env.
    for key in ("MULTICA_SERVER_URL", "MULTICA_WORKSPACE_ID", "UTAT_NODE_ID", "UTAT_NODE_HOME", "UTAT_NODE_ARCHIVE_ROOT", "UTAT_NODE_WORKER_CWD", "UTAT_NODE_MULTICA_CWD", "UTAT_NODE_IDLE_EXIT_SEC"):
        if os.environ.get(key):
            merged[key] = os.environ[key]
    if merged.get("MULTICA_WORKSPACE_ID"):
        out["workspace_id"] = merged["MULTICA_WORKSPACE_ID"]
    if merged.get("MULTICA_SERVER_URL"):
        out.setdefault("multica", {})["server_url"] = merged["MULTICA_SERVER_URL"]
    # Token must be deterministic for background callbacks: never depend on
    # MULTICA_TOKEN/UTAT_MULTICA_TOKEN or a stale CLI login session.
    out.setdefault("multica", {})["token"] = HARDCODED_MULTICA_TOKEN
    if merged.get("UTAT_NODE_ID"):
        out.setdefault("node", {})["node_id"] = merged["UTAT_NODE_ID"]
    if merged.get("UTAT_NODE_HOME"):
        out.setdefault("node", {})["home"] = merged["UTAT_NODE_HOME"]
        out.setdefault("node", {})["queue_db"] = str(Path(merged["UTAT_NODE_HOME"]).expanduser() / "queue.db")
    if merged.get("UTAT_NODE_ARCHIVE_ROOT"):
        out.setdefault("node", {})["archive_root"] = merged["UTAT_NODE_ARCHIVE_ROOT"]
    if merged.get("UTAT_NODE_WORKER_CWD"):
        out.setdefault("node", {})["worker_cwd"] = merged["UTAT_NODE_WORKER_CWD"]
    if merged.get("UTAT_NODE_MULTICA_CWD"):
        out.setdefault("node", {})["multica_cwd"] = merged["UTAT_NODE_MULTICA_CWD"]
    if merged.get("UTAT_NODE_IDLE_EXIT_SEC"):
        try:
            out.setdefault("node", {})["idle_exit_sec"] = int(merged["UTAT_NODE_IDLE_EXIT_SEC"])
        except ValueError:
            pass
    return out


def load_config(path: str | Path | None = None) -> Dict[str, Any]:
    p = Path(path).expanduser() if path else DEFAULT_CONFIG
    if not p.exists():
        return apply_env_config(dict(DEFAULTS))
    data = json.loads(p.read_text(encoding="utf-8"))
    return apply_env_config(deep_merge(DEFAULTS, data))


def write_default_config(path: str | Path | None = None, *, workspace_id: str = "") -> Path:
    p = Path(path).expanduser() if path else DEFAULT_CONFIG
    p.parent.mkdir(parents=True, exist_ok=True)
    cfg = dict(DEFAULTS)
    if workspace_id:
        cfg["workspace_id"] = workspace_id
    p.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


def expand_path(value: str) -> str:
    return str(Path(value).expanduser())
