# Myszolot EV Charging Scheduler

<img src="custom_components/myszolot/icon.svg" width="64" alt="Myszolot Icon">

Smart price-based charging scheduler for Tesla and other EVs in Home Assistant. Optimizes charging times based on electricity prices while respecting battery health and time constraints.

## Features

- **Smart (default)**: Always-on daily charging to 80% using cheapest hours; skips new sessions when SoC is already high enough (≥ `charge_start_soc`); hard price stop above threshold
- **Override**: Temporary plan — charge to a chosen SoC **within N hours** (default 24h) using the cheapest hours, **no price hard-stop**; auto-returns to smart when target is hit or the window ends
- **Feasibility warnings**: Surfaces max reachable SoC at current amps when the target cannot be met in time
- **Battery health**: Emergency min-SoC floor; daily charge-start debounce
- **Continuous sessions**: Adjacent scheduled hours merge into uninterrupted charging windows
- **Location override**: Force "at home" if device tracker is unreliable
- **Cable reminder**: Notifies when cable is needed before a session
- **Dashboard card**: Status, plan summary, smart reset, override controls

## Requirements

- Home Assistant 2024.6+
- Tesla or EV with:
  - SoC (battery %) sensor (`sensor.myszolot_battery_level`)
  - Cable detection (`binary_sensor.myszolot_charge_cable`)
  - Location tracking (`device_tracker.myszolot_location`)
- Electricity price sensor with hourly forecast:
  - State = current price (PLN/kWh)
  - Attribute `All prices` = 24-entry array of `{hour, price}` dicts

## Installation

1. Add this repository as a custom repository in HACS
2. Install via Home Assistant UI: Settings → Devices & Services → Integrations → Myszolot
3. Configure battery capacity, charger phases, and charging amps
4. Create automations to actuate charge control (see [Example Automations](#example-automations))
5. Create [Optional Helpers](#optional-helpers) for override + location features

On first setup, a notification will appear in HA if any optional helpers are missing.

## Optional Helpers

Create these in **Settings → Devices & Services → Helpers**.

### Location Override

**Entity:** `input_boolean.myszolot_location_override`  
**Type:** Toggle (on/off)

When **on**, the car is treated as home regardless of the device tracker (GPS/sleep issues).

**Recommended:** auto-reset after 12h — see `automations/location-override-reset.yml`.

### Override target SoC

**Entity:** `input_number.myszolot_custom_target_soc`  
**Type:** Number (50–100, step 1)

Target SoC % for **override** mode (e.g. 95% before a trip).

### Override deadline (within hours)

**Entity:** `input_number.myszolot_deadline_hours`  
**Type:** Number (1–48, step 1), default **24**

How many hours from activation the override has to reach the target. Example: need 95% by 19:00 and it is 14:00 → set **5** hours, target **95**, then **Start override**.

**Behaviour when override is selected:**
- Target + deadline are **locked** at activation
- Scheduler picks the cheapest hours inside that window (ignores max price threshold)
- When SoC reaches target **or** the deadline passes → mode returns to **smart**
- You can always press **Smart (default)** to cancel early

## Configuration

### Config Entry Options

| Option | Default | Description |
|---|---|---|
| `charger_phases` | 3 | 1 or 3-phase charger |
| `voltage` | 230 V | Line-to-neutral voltage |
| `fast_amps` | 12 A | Fallback charging current if Tessie amps unavailable |
| `battery_capacity_kWh` | 68.9 | Total usable battery capacity |
| `default_target_soc` | 80% | Smart mode daily target |
| `min_soc` | 30% | Emergency charge floor |
| `charge_start_soc` | 69% | Smart: do not **start** a new plan if SoC already above this |
| `max_price_threshold` | 1.0 PLN/kWh | Smart: skip hours above this price |
| `smart_deadline_hours` | 48 h | Smart: planning horizon for cheapest hours |

**Derived at runtime:**
```
max_charge_rate_kW = charge_amps × voltage × charger_phases / 1000
  Example (3-phase, 12A): 12 × 230 × 3 / 1000 = 8.28 kW
```

## Entities

### Select: Charge Mode
**Entity ID:** `select.myszolot_charge_mode`

| Mode | Target SoC | Price hard-stop | SoC debounce | Scheduling |
|---|---|---|---|---|
| `smart` | 80% (config) | Yes (`max_price_threshold`) | Yes (`charge_start_soc`) | Cheapest hours in smart horizon (48h) |
| `override` | Helper target % | **No** | **No** | Cheapest hours within locked deadline |

Override auto-resets to `smart` when target is reached or the deadline ends.

### Sensor: Charge Reason
**Entity ID:** `sensor.myszolot_charge_reason`

**State:** Current decision (e.g. `scheduled`, `waiting_for_session`, `soc_sufficient`, `target_unreachable`)

**Key attributes:**

| Attribute | Description |
|---|---|
| `should_charge` | Whether charging is recommended now |
| `target_amps` | Amperage to set if charging |
| `mode` | `smart` or `override` |
| `target_soc` | Active target % |
| `deadline_hours` | Active planning window (hours) |
| `feasible` | Whether target is reachable at current amps in the window |
| `max_reachable_soc` | Upper-bound SoC if charging full rate for the whole window |
| `shortfall_soc` | `target − max_reachable` when unfeasible |
| `expected_end_soc` | Projected SoC from the planned (price-optimised) sessions |
| `override_remaining_minutes` | Minutes left on override deadline |
| `location_override_active` | GPS force-home helper on |

### Other sensors

| Entity | Purpose |
|---|---|
| `sensor.myszolot_charge_schedule` | Planned session cost (PLN) + session list |
| `sensor.myszolot_expected_end_soc` | Projected end SoC from plan |
| `sensor.myszolot_planned_session_duration` | Planned charge minutes |
| `sensor.myszolot_max_reachable_soc` | Feasibility ceiling at full rate |
| `sensor.myszolot_override_remaining` | Human-readable override timer (`Off` / `3h 20m`) |
| `binary_sensor.myszolot_cable_needed` | Cable reminder trigger |

## External Entities (Read)

| Entity | Purpose |
|---|---|
| `sensor.pstryk_current_buy_price` | Current price & 24h forecast (`All prices`) |
| `sensor.myszolot_battery_level` | Current SoC (%) |
| `binary_sensor.myszolot_charge_cable` | Cable connected |
| `device_tracker.myszolot_location` | Car location |
| `sensor.myszolot_charging` | External charging status |
| `input_boolean.myszolot_location_override` | Optional force-home |
| `input_number.myszolot_custom_target_soc` | Override target % |
| `input_number.myszolot_deadline_hours` | Override window (hours) |

## External Entities (Write)

Actuated by automations from `sensor.myszolot_charge_reason`:

| Entity | Purpose |
|---|---|
| `switch.myszolot_charge` | Enable/disable charging |
| `number.myszolot_charge_current` | Set charging current (amps) |
| `switch.autel_charge_control` | Enable/disable charger unit |
| `number.myszolot_charge_limit` | Tesla charge limit % (`charge-limit-automation.yml`) |

## Charge Modes (detail)

### Smart (default, always on)

- **Target:** 80% (`default_target_soc`)
- **Debounce:** if SoC already **>** `charge_start_soc` (69%), do not start a new daily plan (`soc_sufficient`)
- **Price:** skip hours above `max_price_threshold` — wait longer for cheaper power
- **Emergency:** if SoC &lt; `min_soc` and cable is in, charge immediately at full amps
- **Once a session starts:** `charging_started` allows finishing through to 80% even past the debounce line

### Override (temporary)

- **Target:** `input_number.myszolot_custom_target_soc` (e.g. 95%)
- **Window:** `input_number.myszolot_deadline_hours` from the moment you press **Start override**
- **Price:** no hard stop — always pick the **cheapest** hours inside the window
- **No SoC debounce** — always tries to reach the target
- **Feasibility:** if max rate × available hours cannot hit target, plan still uses best-effort cheapest hours and UI shows `max_reachable_soc` / `target_unreachable`
- **Exit:** target reached, deadline expired, or manual return to **smart**

## Example Automations

### Actuator Automation

```yaml
alias: Tesla Charging - Actuator
triggers:
  - entity_id: sensor.myszolot_charge_reason
    trigger: state
  - trigger: time_pattern
    minutes: /1
actions:
  - choose:
      - conditions:
          - "{{ not state_attr('sensor.myszolot_charge_reason', 'should_charge') }}"
        sequence:
          - action: switch.turn_off
            target: { entity_id: switch.myszolot_charge }
      default:
        - if:
            - "{{ not is_state('switch.autel_charge_control', 'on') }}"
          then:
            - action: switch.turn_on
              target: { entity_id: switch.autel_charge_control }
        - action: number.set_value
          target: { entity_id: number.myszolot_charge_current }
          data:
            value: "{{ state_attr('sensor.myszolot_charge_reason', 'target_amps') }}"
        - action: switch.turn_on
          target: { entity_id: switch.myszolot_charge }
mode: single
```

### Charge limit by mode

See `automations/charge-limit-automation.yml` — smart → 80%, override → custom target helper.

### Cable reminder / location override reset / dashboard

- `automations/cable-reminder.yml`
- `automations/location-override-reset.yml`
- `automations/dashboard-card.yml` (paste as Manual card)

## Voice announcements (xAI Grok TTS)

Speak on the family-room Yamaha soundbar (`media_player.pokoj_rodzinny`) via Grok TTS. See `scripts/README.md` and `custom_components/xai_tts/`.

Deploy path:

```bash
# Prefer SSH alias `ha` (user ansible). Root SSH is disabled on containers.
rsync -av custom_components/xai_tts/ ansible@192.168.1.201:/tmp/xai_tts/
ssh ha "sudo rsync -av /tmp/xai_tts/ /opt/ha/config/custom_components/xai_tts/ && sudo docker restart ha"
```

## Algorithm: Fractional Knapsack + Continuous Sessions

1. **Build Schedule**: Select cheapest eligible hours until energy need is met
2. **Merge Sessions**: Adjacent hours merge into one continuous window
3. **Shift Partial Hours**: Partial first hour of a group shifts to tail of that hour
4. **Feasibility**: Compare full-rate capacity of the window vs energy needed

**Example (override 95% by evening):**
- Need ~20 kWh at 8.28 kW → ~2.5 h of charge time
- Window = 5 h → feasible; knapsack picks the cheapest 3 hours inside that window

## Implementation Notes

### Coordinator refresh

Schedule rebuilds on: SoC change, location change, cable, price update, mode change, target/deadline helpers, every 5 minutes.

### Mode auto-reset

Only **override** auto-resets to **smart** (target reached or deadline end). Smart never resets.

### Charging started flag

Once a smart scheduled session starts, the flag bypasses `charge_start_soc` so the session can finish to 80%. Clears when target is reached.

## Testing

```bash
pytest tests/                        # All tests
pytest tests/test_scheduler.py      # Knapsack + session merging
pytest tests/test_coordinator.py    # Reason determination + feasibility
pytest tests/test_config_flow.py    # Configuration validation
```

## Troubleshooting

**Q: Charge schedule is empty?**  
A: Check `sensor.pstryk_current_buy_price` has `All prices` with a 24-entry array. In smart mode, all hours may exceed `max_price_threshold`.

**Q: Car shows not home in the garage?**  
A: Enable location override toggle from the dashboard.

**Q: Override does nothing / falls back immediately?**  
A: Create `input_number.myszolot_custom_target_soc` and `input_number.myszolot_deadline_hours`, set them **before** Start override.

**Q: Warning “target may be unreachable”?**  
A: At current amps (e.g. 12 A), continuous charging for the whole window still cannot hit the target. Plan still uses cheapest hours (best effort). Raise amps, lengthen the window, or lower the target.

**Q: Smart won’t top up from 72%?**  
A: Expected — `charge_start_soc` debounce. Use **override** if you need a higher target soon.

## License

MIT
