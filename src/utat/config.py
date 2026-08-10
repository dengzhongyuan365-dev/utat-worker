from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

DEFAULT_HOME = Path(os.environ.get("UTAT_HOME", str(Path.home() / ".utat-worker")))
DEFAULT_DB = DEFAULT_HOME / "queue.db"
DEFAULT_CONFIG = DEFAULT_HOME / "config.json"

DEFAULTS: Dict[str, Any] = {
    "workspace_id": "",
    "server": {"host": "127.0.0.1", "port": 8765, "token_env": "UTAT_SERVER_TOKEN"},
    "multica": {"cli": "multica", "server_url": "", "profile": ""},
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
}


def deep_merge(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(a)
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config(path: str | Path | None = None) -> Dict[str, Any]:
    p = Path(path).expanduser() if path else DEFAULT_CONFIG
    if not p.exists():
        return dict(DEFAULTS)
    data = json.loads(p.read_text(encoding="utf-8"))
    return deep_merge(DEFAULTS, data)


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
