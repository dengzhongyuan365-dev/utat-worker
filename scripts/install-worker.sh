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
    "work_root": "~/atut-work",
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
    "work_root": "~/atut-work",
    "archive_root": "~/Documents/ATUT-WORK-Archive",
    "poll_interval_sec": 5,
    "idle_exit_sec": 300
  }
}
JSON
fi

# Optional fixed Multica callback credentials for private execution nodes.
# Do not print token. The worker loads this file before every callback.
MULTICA_ENV_FILE="$CONFIG_DIR/multica.env"
CALLBACK_TOKEN="${UTAT_MULTICA_TOKEN:-${MULTICA_TOKEN:-}}"
CALLBACK_WORKSPACE="${MULTICA_WORKSPACE_ID:-${UTAT_WORKSPACE_ID:-}}"
CALLBACK_SERVER="${MULTICA_SERVER_URL:-${UTAT_MULTICA_SERVER_URL:-https://agentapi-dev.uniontech.com}}"
CALLBACK_NODE="${UTAT_NODE_ID:-}"
CALLBACK_ARCHIVE_ROOT="${UTAT_NODE_ARCHIVE_ROOT:-}"
CALLBACK_IDLE_EXIT_SEC="${UTAT_NODE_IDLE_EXIT_SEC:-}"
if [ -n "$CALLBACK_TOKEN" ] || [ -n "$CALLBACK_WORKSPACE" ] || [ -n "$CALLBACK_NODE" ] || [ -n "$CALLBACK_ARCHIVE_ROOT" ] || [ -n "$CALLBACK_IDLE_EXIT_SEC" ]; then
  umask 077
  {
    [ -n "$CALLBACK_SERVER" ] && printf 'MULTICA_SERVER_URL=%s\n' "$CALLBACK_SERVER"
    [ -n "$CALLBACK_WORKSPACE" ] && printf 'MULTICA_WORKSPACE_ID=%s\n' "$CALLBACK_WORKSPACE"
    [ -n "$CALLBACK_TOKEN" ] && printf 'MULTICA_TOKEN=%s\n' "$CALLBACK_TOKEN"
    [ -n "$CALLBACK_NODE" ] && printf 'UTAT_NODE_ID=%s\n' "$CALLBACK_NODE"
    [ -n "$CALLBACK_ARCHIVE_ROOT" ] && printf 'UTAT_NODE_ARCHIVE_ROOT=%s\n' "$CALLBACK_ARCHIVE_ROOT"
    [ -n "$CALLBACK_IDLE_EXIT_SEC" ] && printf 'UTAT_NODE_IDLE_EXIT_SEC=%s\n' "$CALLBACK_IDLE_EXIT_SEC"
  } > "$MULTICA_ENV_FILE"
  chmod 600 "$MULTICA_ENV_FILE"
  echo "Callback env: $MULTICA_ENV_FILE"
fi

echo "Installed to $INSTALL_DIR"
echo "Config: $CONFIG_DIR/config.json"
