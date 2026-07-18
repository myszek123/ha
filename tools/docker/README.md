# charge-log Docker (services LXC)

Lightweight container that rebuilds the Google Sheet charge log daily.

## Secrets (env only — not in the image)

| Variable | Required | Purpose |
|----------|----------|---------|
| `TESSIE_TOKEN` | yes | Tessie API |
| `PSTRYK_API_KEY` | yes | Pstryk API (same as HA integration) |
| `GOOGLE_APPLICATION_CREDENTIALS` | yes | Path to SA JSON **inside** container |
| `SHEET_ID` | no | Default: Home Charging Costs sheet |
| `VIN` | no | Tesla VIN |
| `PUSH_SHEET` | no | `true` to push Google Sheet |

Host layout on **CT 119 services** (`192.168.1.219`):

```text
/opt/services/charge-log/
  docker-compose.yml
  .env                          # mode 600
  secrets/google-sa.json        # mode 600
  data/                         # CSV output
```

## Cron (on services host)

```cron
15 6 * * * cd /opt/services/charge-log && /usr/bin/docker compose run --rm charge-log >> /var/log/charge-log.log 2>&1
```

Not on charge-session end — full rebuild of all tabs once per day.
