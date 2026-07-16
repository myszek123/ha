#!/usr/bin/env bash
# Hourly refresh of home charging log (Tessie home-only + Pstryk prices → CSV + Sheet).
set -euo pipefail
export PATH="/usr/bin:/bin:$HOME/claude-projects/ha/.venv-sheets/bin:$PATH"
# shellcheck disable=SC1090
source "$HOME/.env.private" 2>/dev/null || true

LOG="${HOME}/myszolot-charge-log-sync.log"
PY="${HOME}/claude-projects/ha/.venv-sheets/bin/python"
SCRIPT="${HOME}/claude-projects/ha/tools/charge_log.py"

{
  echo "==== $(date -Iseconds) ===="
  "$PY" "$SCRIPT" rebuild --sheet
} >>"$LOG" 2>&1
