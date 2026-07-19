# Presence simulation (deterministic)

House is **vacant ≥ 24h** when **both** personal phones are offline on Omada:

| Phone | Omada name match | Person |
|-------|------------------|--------|
| S23 | `S23` | Jakub |
| Z2 Flip | `Z2 Flip` | Sylwia |

- Motion sensors **ignored** (false positives).
- Company phones / other Omada clients **ignored**.
- Car **ignored**.

## Evening lights (when vacant and not paused)

| Step | Behaviour |
|------|-----------|
| ON | **All** kitchen + salon switches together at random **20:00 ±15 min** Warsaw |
| OFF | Random time in **22:30–23:30** Warsaw |
| Notify | **One** phone push when lights actually turn ON (`notify.mobile_app_j23`) |

## Pause / override (dashboard)

| Entity | Meaning |
|--------|---------|
| `input_boolean.presence_sim_pause` | **ON** = do not change lights (guests, home with phones off, sister overnight, …). **OFF** (default) = simulation allowed when vacant |

Vacancy sensors still update while paused so you can see “phones offline but sim paused”.

Use cases:

- Both phones off / airplane but you’re home → turn **Pause** ON
- Visitor stays alone overnight while you’re away → turn **Pause** ON (no fake occupancy lights)
- Normal away trip → leave **Pause** OFF

## HA entities

| Entity | Meaning |
|--------|---------|
| `binary_sensor.house_vacant_24h` | `on` = both phones offline ≥24h |
| `sensor.house_vacancy_status` | short summary + attrs (`s23_*`, `z2_*`, `sim_paused`, `on_deadline`, …) |
| `input_boolean.presence_sim_pause` | manual override |

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

Monitor validates **real switch state** (not just timestamps) and logs only — no extra notify.
