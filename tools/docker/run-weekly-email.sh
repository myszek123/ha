#!/bin/bash
# Monday 21:30 — last complete Mon–Sun drive week → email
set -euo pipefail
cd /opt/services/charge-log
/usr/bin/docker-compose run --rm charge-log weekly-email >> /var/log/charge-log-weekly.log 2>&1
