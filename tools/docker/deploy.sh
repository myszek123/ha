#!/usr/bin/env bash
# Build image locally (or on services), deploy to CT 119 via p330.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
REPO_TOOLS="$(cd "$ROOT/.." && pwd)"
REMOTE_DIR="/opt/services/charge-log"
IMAGE_TAR="/tmp/charge-log-image.tar"

# Sync script into build context
cp -f "$REPO_TOOLS/charge_log.py" "$ROOT/charge_log.py"

echo "==> Build image"
docker build -t charge-log:latest "$ROOT"

echo "==> Save image"
docker save charge-log:latest -o "$IMAGE_TAR"

echo "==> Ensure remote dir on services (CT 119)"
ssh p330 "pct exec 119 -- mkdir -p $REMOTE_DIR/data $REMOTE_DIR/secrets"

echo "==> Copy compose + run scripts + image"
scp "$ROOT/docker-compose.yml" p330:/tmp/charge-log-compose.yml
# run.sh may live only on host; keep weekly script in deploy
if [[ -f "$ROOT/run-weekly-email.sh" ]]; then
  scp "$ROOT/run-weekly-email.sh" p330:/tmp/charge-log-weekly.sh
  ssh p330 "pct push 119 /tmp/charge-log-weekly.sh $REMOTE_DIR/run-weekly-email.sh"
  ssh p330 "pct exec 119 -- chmod +x $REMOTE_DIR/run-weekly-email.sh"
fi
scp "$IMAGE_TAR" p330:/tmp/charge-log-image.tar
ssh p330 "pct push 119 /tmp/charge-log-compose.yml $REMOTE_DIR/docker-compose.yml"
ssh p330 "pct push 119 /tmp/charge-log-image.tar /tmp/charge-log-image.tar"
ssh p330 "pct exec 119 -- docker load -i /tmp/charge-log-image.tar"
ssh p330 "pct exec 119 -- rm -f /tmp/charge-log-image.tar"
ssh p330 "rm -f /tmp/charge-log-image.tar /tmp/charge-log-compose.yml /tmp/charge-log-weekly.sh"

echo "==> Done image deploy. Ensure secrets exist:"
echo "    $REMOTE_DIR/.env   (incl. SMTP_* + WEEKLY_EMAIL_TO for Monday email)"
echo "    $REMOTE_DIR/secrets/google-sa.json"
echo "Cron:"
echo "    15 6 * * * $REMOTE_DIR/run.sh"
echo "    30 21 * * 1 $REMOTE_DIR/run-weekly-email.sh"
echo "Manual: pct exec 119 -- bash -lc 'cd $REMOTE_DIR && docker-compose run --rm charge-log'"
