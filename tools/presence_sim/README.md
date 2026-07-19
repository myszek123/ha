# Presence simulation (deterministic)

House is **vacant ≥ 24h** when **both** personal phones are offline on Omada:

| Phone | Omada name match | Person |
|-------|------------------|--------|
| S23 | `S23` | Jakub |
| Z2 Flip | `Z2 Flip` | Sylwia |

- Motion sensors **ignored** (false positives).
- Company phones / other Omada clients **ignored**.
- Car **ignored**.

When vacant: turn **kitchen + salon** switches ON at **20:00** Warsaw, OFF at a random time in **22:30–23:30**.

## HA entities

| Entity | Meaning |
|--------|---------|
| `binary_sensor.house_vacant_24h` | `on` = both phones offline ≥24h |
| `sensor.house_vacancy_status` | short summary + attributes (`s23_*`, `z2_*`, `phones`) |

## Lights

- `switch.kitchenlight_l1` / `_l2`
- `switch.lightlivingroom_l1` / `_l2`
- `switch.livinroomstandinglamp`

## Deploy (CT 119)

```bash
cd tools/presence_sim/docker
./deploy.sh
```

Cron:

```cron
*/15 * * * * /opt/services/presence-sim/run.sh
*/15 19-23 * * * /opt/services/presence-sim/run-monitor-7d.sh
*/30 0 * * * /opt/services/presence-sim/run-monitor-7d.sh
```
