#!/bin/bash
# Validation snapshot (evenings + overnight)
set -euo pipefail
cd /opt/services/presence-sim
/usr/bin/docker-compose run --rm presence-sim monitor >> /var/log/presence-sim-monitor.log 2>&1
