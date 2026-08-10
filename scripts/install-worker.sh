#!/usr/bin/env bash
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/dengzhongyuan365-dev/utat-worker.git}"
INSTALL_DIR="${INSTALL_DIR:-$HOME/WorkSpace/utat-worker}"
CONFIG_DIR="${CONFIG_DIR:-$HOME/.utat-worker}"
SKIP_REPO_FETCH="${SKIP_REPO_FETCH:-0}"

mkdir -p "$CONFIG_DIR"
CLONE_URL="$REPO_URL"
if [ -n "${GITHUB_TOKEN:-}" ] && [[ "$REPO_URL" == https://github.com/* ]]; then
  # Private repository support. Do not print CLONE_URL because it contains token.
  CLONE_URL="${REPO_URL/https:\\/\\/github.com\\//https:\\/\\/x-access-token:${GITHUB_TOKEN}@github.com\\/}"
fi

if [ "$SKIP_REPO_FETCH" != "1" ]; then
  if [ ! -d "$INSTALL_DIR/.git" ]; then
    git clone "$CLONE_URL" "$INSTALL_DIR"
  else
    git -C "$INSTALL_DIR" pull --ff-only
  fi
elif [ ! -f "$INSTALL_DIR/pyproject.toml" ]; then
  echo "SKIP_REPO_FETCH=1 but $INSTALL_DIR is not a worker source directory" >&2
  exit 2
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
  "scheduler": {"poll_interval_sec": 30},
  "worker": {
    "node_id": "local",
    "server_url": "http://127.0.0.1:8765",
    "work_root": "~/tests",
    "max_parallel": 1,
    "poll_interval_sec": 15,
    "capabilities": {"apps": [], "task_types": ["AT", "UT"]}
  },
  "routing": {},
  "mail": {"enabled": false},
  "node": {
    "node_id": "local",
    "home": "~/.utat-node",
    "queue_db": "~/.utat-node/queue.db",
    "work_root": "~/tests",
    "poll_interval_sec": 5
  }
}
JSON
fi

echo "Installed to $INSTALL_DIR"
echo "Config: $CONFIG_DIR/config.json"
