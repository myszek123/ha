#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$ROOT/.." && pwd)"
REMOTE_DIR="/opt/services/presence-sim"
IMAGE_TAR="/tmp/presence-sim-image.tar"

cp -f "$REPO/presence_sim.py" "$ROOT/presence_sim.py"

echo "==> Build"
docker build -t presence-sim:latest "$ROOT"
docker save presence-sim:latest -o "$IMAGE_TAR"

echo "==> Remote dirs"
ssh p330 "pct exec 119 -- mkdir -p $REMOTE_DIR/data"

echo "==> Push compose + scripts + image"
scp "$ROOT/docker-compose.yml" p330:/tmp/presence-sim-compose.yml
scp "$ROOT/run.sh" p330:/tmp/presence-sim-run.sh
scp "$ROOT/run-monitor.sh" p330:/tmp/presence-sim-mon.sh
scp "$IMAGE_TAR" p330:/tmp/presence-sim-image.tar
ssh p330 "pct push 119 /tmp/presence-sim-compose.yml $REMOTE_DIR/docker-compose.yml"
ssh p330 "pct push 119 /tmp/presence-sim-run.sh $REMOTE_DIR/run.sh"
ssh p330 "pct push 119 /tmp/presence-sim-mon.sh $REMOTE_DIR/run-monitor.sh"
ssh p330 "pct exec 119 -- chmod +x $REMOTE_DIR/run.sh $REMOTE_DIR/run-monitor.sh"
ssh p330 "pct push 119 /tmp/presence-sim-image.tar /tmp/presence-sim-image.tar"
ssh p330 "pct exec 119 -- docker load -i /tmp/presence-sim-image.tar"
ssh p330 "pct exec 119 -- rm -f /tmp/presence-sim-image.tar"
ssh p330 "rm -f /tmp/presence-sim-image.tar /tmp/presence-sim-compose.yml /tmp/presence-sim-run.sh /tmp/presence-sim-mon.sh"

echo "==> Done. Ensure $REMOTE_DIR/.env (OMADA_* HA_*). Cron:"
echo "    */15 * * * * $REMOTE_DIR/run.sh"
echo "    */15 19-23 * * * $REMOTE_DIR/run-monitor.sh"
echo "    */30 0 * * * $REMOTE_DIR/run-monitor.sh"
