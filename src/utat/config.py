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

DEFAULTS: Dict[str, Any] = {
    "workspace_id": "",
    "server": {"host": "127.0.0.1", "port": 8765, "token_env": "UTAT_SERVER_TOKEN"},
    "multica": {"cli": "multica", "server_url": "", "profile": "", "token": ""},
    "scheduler": {"poll_interval_sec": 30},
    "worker": {
        "node_id": "local",
        "server_url": "http://127.0.0.1:8765",
        "work_root": "~/tests",
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
        "work_root": "~/tests",
        "poll_interval_sec": 5,
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
    # Real environment wins over file values.
    for key in ("MULTICA_SERVER_URL", "MULTICA_WORKSPACE_ID", "MULTICA_TOKEN", "UTAT_MULTICA_TOKEN", "UTAT_NODE_ID", "UTAT_NODE_HOME"):
        if os.environ.get(key):
            merged[key] = os.environ[key]
    if merged.get("MULTICA_WORKSPACE_ID"):
        out["workspace_id"] = merged["MULTICA_WORKSPACE_ID"]
    if merged.get("MULTICA_SERVER_URL"):
        out.setdefault("multica", {})["server_url"] = merged["MULTICA_SERVER_URL"]
    token = merged.get("MULTICA_TOKEN") or merged.get("UTAT_MULTICA_TOKEN")
    if token:
        out.setdefault("multica", {})["token"] = token
    if merged.get("UTAT_NODE_ID"):
        out.setdefault("node", {})["node_id"] = merged["UTAT_NODE_ID"]
    if merged.get("UTAT_NODE_HOME"):
        out.setdefault("node", {})["home"] = merged["UTAT_NODE_HOME"]
        out.setdefault("node", {})["queue_db"] = str(Path(merged["UTAT_NODE_HOME"]).expanduser() / "queue.db")
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
