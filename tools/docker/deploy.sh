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

echo "==> Copy compose + image"
scp "$ROOT/docker-compose.yml" p330:/tmp/charge-log-compose.yml
scp "$IMAGE_TAR" p330:/tmp/charge-log-image.tar
ssh p330 "pct push 119 /tmp/charge-log-compose.yml $REMOTE_DIR/docker-compose.yml"
ssh p330 "pct push 119 /tmp/charge-log-image.tar /tmp/charge-log-image.tar"
ssh p330 "pct exec 119 -- docker load -i /tmp/charge-log-image.tar"
ssh p330 "pct exec 119 -- rm -f /tmp/charge-log-image.tar"
ssh p330 "rm -f /tmp/charge-log-image.tar /tmp/charge-log-compose.yml"

echo "==> Done image deploy. Ensure secrets exist:"
echo "    $REMOTE_DIR/.env"
echo "    $REMOTE_DIR/secrets/google-sa.json"
echo "Then: ssh p330 'pct exec 119 -- bash -lc \"cd $REMOTE_DIR && docker compose run --rm charge-log\"'"
