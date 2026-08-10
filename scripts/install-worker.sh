#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/dengzhongyuan365-dev/utat-worker.git}"
INSTALL_DIR="${INSTALL_DIR:-$HOME/WorkSpace/utat-worker}"
CONFIG_DIR="${CONFIG_DIR:-$HOME/.utat-worker}"

mkdir -p "$CONFIG_DIR"
if [ ! -d "$INSTALL_DIR/.git" ]; then
  git clone "$REPO_URL" "$INSTALL_DIR"
else
  git -C "$INSTALL_DIR" pull --ff-only
fi

python3 -m venv "$CONFIG_DIR/venv"
"$CONFIG_DIR/venv/bin/pip" install -U pip setuptools wheel
"$CONFIG_DIR/venv/bin/pip" install -e "$INSTALL_DIR"

if [ ! -f "$CONFIG_DIR/config.json" ]; then
  cat > "$CONFIG_DIR/config.json" <<'JSON'
{
  "workspace_id": "",
  "server": {"host": "127.0.0.1", "port": 8765, "token_env": "UTAT_SERVER_TOKEN"},
  "multica": {"cli": "multica", "server_url": "", "profile": ""},
  "scheduler": {"global_parallel": 1, "poll_interval_sec": 30},
  "worker": {
    "node_id": "local",
    "server_url": "http://127.0.0.1:8765",
    "work_root": "~/tests",
    "max_parallel": 1,
    "poll_interval_sec": 15,
    "capabilities": {"apps": [], "task_types": ["AT", "UT"]}
  },
  "routing": {},
  "mail": {"enabled": false}
}
JSON
fi

echo "Installed to $INSTALL_DIR"
echo "Config: $CONFIG_DIR/config.json"
