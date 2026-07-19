# Presence simulation (deterministic)

House is **vacant ≥ 24h** when:

1. Kitchen occupancy not active and last ON ≥ 24h ago  
2. Storage occupancy not active and last ON ≥ 24h ago  
3. No Jakub/Sylwia phones on Omada (live), and lastSeen ≥ 24h  

Car is **ignored**.

When vacant: turn **kitchen + salon** switches ON at 20:00 Warsaw, OFF at a random time in **22:30–23:30**.

## Entities pushed to HA

| Entity | Meaning |
|--------|---------|
| `binary_sensor.house_vacant_24h` | `on` = vacant |
| `sensor.house_vacancy_status` | short summary + attributes |

## Lights

- `switch.kitchenlight_l1` / `_l2`
- `switch.lightlivingroom_l1` / `_l2`
- `switch.livinroomstandinglamp`

## Deploy (CT 119)

```bash
cd tools/presence_sim/docker
# .env with OMADA_* + HA_*
./deploy.sh
```

Cron (root on services):

```cron
*/15 * * * * /opt/services/presence-sim/run.sh
*/15 19-23 * * * /opt/services/presence-sim/run-monitor.sh
*/30 0 * * * /opt/services/presence-sim/run-monitor.sh
```

Monitor log: `/var/log/presence-sim-monitor.log` + `/opt/services/presence-sim/data/monitor.jsonl`  
Expire monitor after 7 days manually or leave (jsonl grows slowly).
