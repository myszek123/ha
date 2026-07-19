#!/bin/bash
# Main tick: vacancy flag + evening lights
set -euo pipefail
cd /opt/services/presence-sim
/usr/bin/docker-compose run --rm presence-sim run >> /var/log/presence-sim.log 2>&1
