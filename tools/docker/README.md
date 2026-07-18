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
| `SMTP_*` + `WEEKLY_EMAIL_TO` | for weekly email | Monday drive summary |

Host layout on **CT 119 services** (`192.168.1.219`):

```text
/opt/services/charge-log/
  docker-compose.yml
  run.sh / run-weekly-email.sh
  .env                          # mode 600
  secrets/google-sa.json        # mode 600
  data/                         # CSV output
```

## Cron (on services host)

```cron
15 6 * * * /opt/services/charge-log/run.sh
30 21 * * 1 /opt/services/charge-log/run-weekly-email.sh
```

Daily: full rebuild of charge + **drive** tabs.  
Monday 21:30: email last complete Mon–Sun week to `WEEKLY_EMAIL_TO`.
