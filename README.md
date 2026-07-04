# Myszolot EV Charging Scheduler

<img src="custom_components/myszolot/icon.svg" width="64" alt="Myszolot Icon">

Smart price-based charging scheduler for Tesla and other EVs in Home Assistant. Optimizes charging times based on electricity prices while respecting battery health and time constraints.

## Features

- **Smart Mode**: Fractional knapsack scheduling for cheapest available hours across 48h window
- **Custom Target Modes**: Schedule or charge immediately to any SoC % you choose
- **Time-Aware**: Uses hourly electricity price forecasts to choose optimal charging windows
- **Multiple Charge Modes**: Smart, Fast Now, Slow Now, Plan Trip, Trip Now, Smart Custom, Now Custom
- **Battery Health**: Respects minimum SoC emergency floor and configurable target SoC
- **Price Filter**: Skip charging if all eligible hours exceed max price threshold
- **Continuous Sessions**: Adjacent scheduled hours merge into uninterrupted charging windows
- **Location Override**: Force "at home" status if device tracker is unreliable (GPS/sleep issues)
- **Cable Reminder**: Automation notifies when cable is needed before session starts
- **Dashboard Card**: Included Lovelace card with status display and quick-select mode buttons

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
3. Configure battery capacity, charger phases, and charging speeds
4. Create automations to actuate charge control (see [Example Automations](#example-automations))
5. Optionally create [Optional Helpers](#optional-helpers) to enable extra features

On first setup, a notification will appear in HA if any optional helpers are missing.

## Optional Helpers

These HA helpers unlock optional features. The integration works without them — missing helpers simply disable the corresponding feature. Create them in **Settings → Devices & Services → Helpers**.

### Location Override

**Entity:** `input_boolean.myszolot_location_override`
**Type:** Toggle (on/off)

Useful when the Tesla device tracker reports `not_home` even though the car is in the garage (poor GPS signal, deep sleep, small zone radius). When this toggle is **on**, the integration treats the car as home regardless of what the device tracker reports.

**To create:** Settings → Helpers → + Create helper → Toggle → Name: `myszolot_location_override`

**Behaviour:**
- Toggle **off** (default): normal device tracker is used
- Toggle **on**: car treated as home; charging schedules run normally
- Visible in the dashboard card and as `location_override_active` attribute on `sensor.myszolot_charge_reason`

**Recommended: auto-reset after 12 hours** to prevent forgetting it's on:

```yaml
alias: Myszolot - Reset location override after 12h
triggers:
  - trigger: state
    entity_id: input_boolean.myszolot_location_override
    to: "on"
    for: "12:00:00"
actions:
  - action: input_boolean.turn_off
    target:
      entity_id: input_boolean.myszolot_location_override
```

> **Note:** The charge-limit automation (`charge-limit-automation.yml`) reads `device_tracker.myszolot_location` directly. If the tracker says `not_home` while override is on, Tesla's charge limit won't be updated on mode switch — this is expected and harmless.

---

### Custom Target SoC

**Entity:** `input_number.myszolot_custom_target_soc`
**Type:** Number (range 50–100, step 1)

Sets the target SoC % used by the `smart_custom` and `now_custom` charge modes. Allows hitting 86%, 90%, or any value between 50% and 100% without changing the integration config.

**To create:** Settings → Helpers → + Create helper → Number → Name: `myszolot_custom_target_soc`, Min: 50, Max: 100, Step: 1, Unit: %

**Behaviour:**
- If helper is missing, custom modes fall back to the `default_target_soc` config value (80%)
- Set the desired % **before** activating a custom mode
- Visible as a slider in the dashboard card

---

## Configuration

### Config Entry Options

| Option | Default | Description |
|---|---|---|
| `charger_phases` | 3 | 1 or 3-phase charger |
| `voltage` | 230 V | Line-to-neutral voltage |
| `fast_amps` | 10 A | Fast charging current |
| `slow_amps` | 5 A | Slow charging current |
| `battery_capacity_kWh` | 68.9 | Total usable battery capacity |
| `default_target_soc` | 80% | Smart/now modes target SoC |
| `trip_target_soc` | 95% | Plan trip / trip now target SoC |
| `min_soc` | 30% | Emergency charge floor |
| `charge_start_soc` | 69% | Smart mode: skip scheduling if SoC already above this |
| `max_price_threshold` | 1.0 PLN/kWh | Skip all hours if cheapest exceeds this |
| `plan_trip_deadline_hours` | 8 h | Plan trip scheduling window |

**Derived at runtime:**
```
max_charge_rate_kW = fast_amps × voltage × charger_phases / 1000
  Example (3-phase, 10A): 10 × 230 × 3 / 1000 = 6.9 kW
```

## Entities

### Select: Charge Mode
**Entity ID:** `select.myszolot_charge_mode`

| Mode | Target SoC | Speed | Scheduling |
|---|---|---|---|
| `smart` | 80% | Cheapest hours | 48h window, skips if SoC > charge_start_soc |
| `now_fast` | 80% | Fast amps immediately | None |
| `now_slow` | 80% | Slow amps immediately | None |
| `plan_trip` | 95% | Cheapest hours | Within deadline_hours window |
| `trip_now` | 95% | Fast amps immediately | None |
| `smart_custom` | Custom % | Cheapest hours | 48h window, no SoC gate |
| `now_custom` | Custom % | Fast amps immediately | None |

Non-smart modes auto-reset to `smart` when `soc >= target_soc`. `smart` and `smart_custom` never auto-reset.

### Sensor: Charge Reason
**Entity ID:** `sensor.myszolot_charge_reason`

**State:** Current charging decision (e.g., `scheduled`, `waiting_for_session`, `soc_sufficient`)

**Attributes:**
| Attribute | Type | Description |
|---|---|---|
| `should_charge` | bool | Whether charging is recommended right now |
| `target_amps` | int | Amperage to set if charging |
| `mode` | str | Current selected mode |
| `current_price` | float | Current electricity price (PLN/kWh) |
| `current_soc` | float | Battery SoC (%) |
| `target_soc` | int | Target SoC (%) |
| `E_needed` | float | Energy needed to reach target (kWh) |
| `next_session_start` | datetime | Next scheduled session start, or None |
| `location_override_active` | bool | Whether location override helper is on |

### Sensor: Charge Schedule
**Entity ID:** `sensor.myszolot_charge_schedule`

**State:** Estimated remaining cost in PLN (sum of all scheduled sessions at current prices).

**Attributes:** `sessions` list (start, end, kWh, cost), `E_needed`, `estimated_total_cost`

### Binary Sensor: Cable Needed
**Entity ID:** `binary_sensor.myszolot_cable_needed`

On when `should_charge=True AND cable disconnected AND device tracker reports home`. Location override is **not** counted — override only affects charging schedules, not plug-in reminders. Used to trigger the cable reminder automation.

## External Entities (Read)

| Entity | Purpose |
|---|---|
| `sensor.pstryk_current_buy_price` | Current price & 24h forecast (`All prices` attribute) |
| `sensor.myszolot_battery_level` | Current SoC (%) |
| `binary_sensor.myszolot_charge_cable` | Cable connected (on/off) |
| `device_tracker.myszolot_location` | Car location (home / not_home) |
| `sensor.myszolot_charging` | External charging status (charging / idle) |
| `input_boolean.myszolot_location_override` | Optional — location override flag |
| `input_number.myszolot_custom_target_soc` | Optional — custom target SoC % |

## External Entities (Write)

The integration does not write to these — create automations based on `sensor.myszolot_charge_reason`:

| Entity | Purpose |
|---|---|
| `switch.myszolot_charge` | Enable/disable charging |
| `number.myszolot_charge_current` | Set charging current (amps) |
| `switch.autel_charge_control` | Enable/disable charger unit |
| `number.myszolot_charge_limit` | Tesla charge limit % (set by `charge-limit-automation.yml`) |

## Charge Modes

### Smart (Default)
- **Target:** 80% (configurable via `default_target_soc`)
- **Scheduling:** Cheapest hours in 48h window
- **Gate:** Won't schedule new sessions if SoC > `charge_start_soc` (69% default)
- **Use case:** Regular daily charging, minimum cost

### Now Fast / Now Slow
- **Target:** 80%
- **Speed:** Fast or slow amps immediately, no price optimisation
- **Use case:** Quick top-up when price doesn't matter

### Plan Trip
- **Target:** 95% (configurable via `trip_target_soc`)
- **Scheduling:** Cheapest hours within `plan_trip_deadline_hours` (default 8h)
- **Also sets:** Tesla charge limit to 95% via `charge-limit-automation.yml`
- **Use case:** Road trip within 8 hours, reach 95% at minimum cost

### Trip Now
- **Target:** 95%
- **Speed:** Fast amps immediately
- **Also sets:** Tesla charge limit to 95%
- **Use case:** Emergency trip prep, charge to 95% ASAP

### Smart Custom
- **Target:** Value of `input_number.myszolot_custom_target_soc` (requires [helper](#custom-target-soc))
- **Scheduling:** Cheapest hours in 48h window
- **No SoC gate:** Always schedules toward the custom target regardless of current SoC
- **Also sets:** Tesla charge limit to custom %
- **Use case:** Schedule to 86% or 90% at minimum cost

### Now Custom
- **Target:** Value of `input_number.myszolot_custom_target_soc` (requires [helper](#custom-target-soc))
- **Speed:** Fast amps immediately, no price optimisation
- **Also sets:** Tesla charge limit to custom %
- **Use case:** Charge immediately to a specific % (not 80%, not 95%)

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

### Cable Reminder Automation

See `automations/cable-reminder.yml`. Requires `binary_sensor.myszolot_cable_needed` and `device_tracker.myszolot_location == home`.

### Location Override Auto-Reset

See `automations/location-override-reset.yml`. Recommended — prevents override from staying on after you leave.

### Dashboard Card

Copy `automations/dashboard-card.yml` into Lovelace (Edit dashboard → Add card → Manual card).

## Algorithm: Fractional Knapsack + Continuous Sessions

1. **Build Schedule**: Select cheapest eligible hours until energy need is met
2. **Merge Sessions**: Adjacent hours merge into one continuous window
3. **Shift Partial Hours**: Partial first hour of a group shifts to tail of that hour for smooth start
4. **Compute Sessions**: List of windows with start/end times, kWh, and cost

**Example:**
- Need 10 kWh, max 6 kWh/hour
- Hour 13 @ 0.50 PLN → 6 kWh (full)
- Hour 14 @ 0.25 PLN → 4 kWh (partial, 40 min)
- Result: one session 13:00–15:00 (continuous; hour 14 shifted to :20–:00)

## Implementation Notes

### Coordinator Refresh Triggers

Schedule rebuilds on: SoC change, location change, cable plug/unplug, price update, mode change, every 5 minutes.

### Mode Auto-Reset

Non-smart modes (`now_fast`, `now_slow`, `trip_now`, `now_custom`) reset to `smart` when `soc >= target_soc`. `smart` and `smart_custom` never auto-reset.

### Charging Started Flag

Once a scheduled session starts, a flag bypasses the `charge_start_soc` gate, allowing charging to continue past that threshold to reach `target_soc`. Resets when target is reached.

## Testing

```bash
pytest tests/                        # All tests
pytest tests/test_scheduler.py      # Knapsack + session merging
pytest tests/test_coordinator.py    # Reason determination logic
pytest tests/test_config_flow.py    # Configuration validation
```

## Troubleshooting

**Q: Charge schedule is empty?**
A: Check that `sensor.pstryk_current_buy_price` has `All prices` attribute with a 24-entry array.

**Q: Car shows as not home even when in garage?**
A: GPS signal or deep sleep issue. Enable the [Location Override](#location-override) helper and toggle it on from the dashboard card.

**Q: Custom modes charge to 80% instead of my target?**
A: The `input_number.myszolot_custom_target_soc` helper is missing. Create it per the [Custom Target SoC](#custom-target-soc) instructions.

**Q: Charging stops mid-session?**
A: Check cable is connected. Review `sensor.myszolot_charge_reason` state and `should_charge` attribute to diagnose.

**Q: No sessions scheduled?**
A: Either `E_needed` is 0 (already at target), all hours exceed `max_price_threshold`, or the price sensor has no `All prices` attribute.

## License

MIT
