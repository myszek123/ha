#!/bin/bash
set -euo pipefail
cd /opt/services/services-status
/usr/bin/docker-compose run --rm services-status >>/var/log/services-status.log 2>&1
