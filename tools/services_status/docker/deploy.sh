#!/usr/bin/env bash
# Deploy services-status to CT 119 (same pattern as charge-log / 4parents).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$ROOT/../.." && pwd)"  # ha/tools
REMOTE_DIR="/opt/services/services-status"
IMAGE_TAR="/tmp/services-status-image.tar"

cp -f "$ROOT/../services_status.py" "$ROOT/services_status.py"

echo "==> Build"
docker build -t services-status:latest "$ROOT"
docker save services-status:latest -o "$IMAGE_TAR"

echo "==> Push to CT 119"
ssh p330 "pct exec 119 -- mkdir -p $REMOTE_DIR"
scp "$ROOT/docker-compose.yml" "$ROOT/run.sh" "$IMAGE_TAR" p330:/tmp/
ssh p330 "pct push 119 /tmp/docker-compose.yml $REMOTE_DIR/docker-compose.yml"
ssh p330 "pct push 119 /tmp/run.sh $REMOTE_DIR/run.sh"
ssh p330 "pct exec 119 -- chmod +x $REMOTE_DIR/run.sh"
ssh p330 "pct push 119 /tmp/services-status-image.tar /tmp/services-status-image.tar"
ssh p330 "pct exec 119 -- docker load -i /tmp/services-status-image.tar"
ssh p330 "pct exec 119 -- rm -f /tmp/services-status-image.tar"
ssh p330 "rm -f /tmp/docker-compose.yml /tmp/run.sh /tmp/services-status-image.tar"

if ! ssh p330 "pct exec 119 -- test -f $REMOTE_DIR/.env"; then
  echo "==> Need HA_TOKEN — write $REMOTE_DIR/.env on CT 119:"
  echo "    HA_URL=http://192.168.1.201:8123"
  echo "    HA_TOKEN=<long-lived token>"
  echo "    (can copy HA_TOKEN from presence-sim/.env if present)"
  # try seed from presence-sim
  ssh p330 "pct exec 119 -- bash -lc '
    if [[ -f /opt/services/presence-sim/.env ]]; then
      grep -E \"^HA_\" /opt/services/presence-sim/.env > $REMOTE_DIR/.env
      chmod 600 $REMOTE_DIR/.env
      echo seeded from presence-sim
    fi
  '" || true
else
  echo "==> .env present"
fi

echo "==> Cron every 30 min"
ssh p330 "pct exec 119 -- bash -lc '
  CRON_LINE=\"*/30 * * * * $REMOTE_DIR/run.sh\"
  (crontab -l 2>/dev/null | grep -v services-status/run.sh; echo \"\$CRON_LINE\") | crontab -
  crontab -l | grep services-status || true
'"

echo "Done. Manual: pct exec 119 -- bash -lc 'cd $REMOTE_DIR && docker-compose run --rm services-status'"
echo "HA card: ha/automations/services-jobs-card.yml"
