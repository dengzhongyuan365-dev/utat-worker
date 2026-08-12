#!/usr/bin/env bash
set -euo pipefail

# AT/UT worker one-key uninstaller.
# Usage:
#   bash -c "$(curl -fsSL https://raw.githubusercontent.com/dengzhongyuan365-dev/utat-worker/master/scripts/uninstall-worker.sh)"
# Optional env:
#   KEEP_SOURCE=0  # default remove $INSTALL_DIR
#   KEEP_DATA=0    # default remove state/work/archive directories
#   INSTALL_DIR=$HOME/WorkSpace/utat-worker

INSTALL_DIR="${INSTALL_DIR:-$HOME/WorkSpace/utat-worker}"
WORKER_HOME="${UTAT_WORKER_HOME:-$HOME/.utat-worker}"
STATE_HOME="${UTAT_STATE_HOME:-$HOME/.utat-node}"
WORK_ROOT="${UTAT_WORK_ROOT:-$HOME/atut-work}"
ARCHIVE_ROOT="${UTAT_ARCHIVE_ROOT:-$HOME/Documents/ATUT-WORK-Archive}"
PROFILE_FILE="${PROFILE_FILE:-$HOME/.bashrc}"
BIN_DIR="${BIN_DIR:-$HOME/.local/bin}"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/utat-worker"
KEEP_SOURCE="${KEEP_SOURCE:-0}"
KEEP_DATA="${KEEP_DATA:-0}"

say() { printf '[utat-worker] %s\n' "$*"; }

say "stop local worker/web processes if any"
python3 - <<'PYSTOP'
import os, signal, time
patterns = [
    "utat_worker.cli worker",
    "utat_worker.cli serve",
    "utat-worker-daemon",
    "utat-worker-web",
]
self_pid = os.getpid()
exclude = {self_pid, os.getppid()}
pid = os.getppid()
while pid and pid > 1:
    try:
        with open(f"/proc/{pid}/stat", "r", encoding="utf-8", errors="ignore") as f:
            parts = f.read().split()
            ppid = int(parts[3]) if len(parts) > 3 else 0
    except Exception:
        break
    exclude.add(pid)
    pid = ppid
for name in os.listdir("/proc"):
    if not name.isdigit():
        continue
    pid = int(name)
    if pid in exclude:
        continue
    try:
        raw = open(f"/proc/{pid}/cmdline", "rb").read()
    except Exception:
        continue
    cmd = raw.replace(b"\x00", b" " ).decode("utf-8", "ignore")
    if any(p in cmd for p in patterns):
        try:
            os.kill(pid, signal.SIGTERM)
            print(f"stopped pid={pid} cmd={cmd[:160]}")
        except ProcessLookupError:
            pass
        except PermissionError:
            print(f"no permission to stop pid={pid} cmd={cmd[:160]}")
time.sleep(0.5)
PYSTOP

say "remove command wrappers"
rm -f "$BIN_DIR/utat-worker" "$BIN_DIR/utat-worker-daemon" "$BIN_DIR/utat-worker-web"

say "remove profile block: $PROFILE_FILE"
if [ -f "$PROFILE_FILE" ]; then
  TMP_PROFILE="$(mktemp)"
  awk '
    $0=="# >>> utat-worker >>>" {skip=1; next}
    $0=="# <<< utat-worker <<<" {skip=0; next}
    !skip {print}
  ' "$PROFILE_FILE" > "$TMP_PROFILE"
  mv "$TMP_PROFILE" "$PROFILE_FILE"
fi

say "remove venv/config: $WORKER_HOME $CONFIG_DIR"
rm -rf "$WORKER_HOME" "$CONFIG_DIR"

if [ "$KEEP_DATA" != "1" ]; then
  say "remove data: $STATE_HOME $WORK_ROOT $ARCHIVE_ROOT"
  rm -rf "$STATE_HOME" "$WORK_ROOT" "$ARCHIVE_ROOT"
else
  say "keep data enabled; not removing state/work/archive"
fi

if [ "$KEEP_SOURCE" != "1" ]; then
  say "remove source: $INSTALL_DIR"
  rm -rf "$INSTALL_DIR"
else
  say "keep source enabled; not removing source"
fi

say "uninstalled. Open a new shell or run: source $PROFILE_FILE"
