# services-status

Publishes CT 119 scheduled-job health to Home Assistant so you get a dashboard
overview next to InternalLinks / LXC cards.

## Sensors

| Entity | Meaning |
|--------|---------|
| `sensor.homelab_services_jobs` | Fleet state (`ok`/`stale`/`fail`/…) + markdown summary |
| `sensor.homelab_job_charge_log` | Per-job tile |
| `sensor.homelab_job_wspolne_remind` | |
| `sensor.homelab_job_presence_sim` | |
| `sensor.homelab_job_fourparents_export` | |
| `sensor.homelab_job_monthly_bills` | |

## Deploy

```bash
cd ~/claude-projects/ha/tools/services_status/docker
./deploy.sh
```

Cron: `*/30 * * * *` on CT 119.

## Dashboard

Paste `ha/automations/services-jobs-card.yml` as a **Manual card** on
InternalLinks (or Overview). Until the first status push, the card shows a
static inventory with doc links.
