# Myszolot EV Charging Scheduler

![Myszolot Icon](custom_components/myszolot/icon.svg)

Smart G12 charging scheduler for Tesla and other EVs in Home Assistant. Optimizes charging times based on electricity prices while respecting battery health and time constraints.

## Features

- **Smart Mode**: Fractional knapsack scheduling for cheapest available hours (G12 off-peak only)
- **Time-Aware**: Uses hourly electricity price forecasts to choose optimal charging windows
- **Multiple Charge Modes**: Smart (default), Fast Now, Slow Now, Plan Trip, Trip Now
- **Battery Health**: Respects minimum SoC emergency floor and user-configurable target SoC
- **G12 Compliance**: Off-peak rate windows for Polish G12 tariff
- **Continuous Sessions**: Adjacent scheduled hours merge into uninterrupted charging windows
- **Session Awareness**: Partial hours shifted to tail of window for smooth power transitions
- **Price-Aware**: Skip charging if all eligible hours exceed max price threshold
- **Cable Reminder**: Automation can notify when cable is needed before session starts
- **Dashboard Card**: Included Lovelace card with status display and 5 quick-select mode buttons

## Requirements

- Home Assistant 2024.6+
- Tesla or EV with:
  - SoC (battery %) sensor (e.g., `sensor.myszolot_battery_level`)
  - Cable detection (e.g., `binary_sensor.myszolot_charge_cable`)
  - Location tracking (e.g., `device_tracker.myszolot_location`)
- Electricity price sensor with hourly forecast:
  - State = current price (PLN/kWh)
  - Attribute `All prices` = 24-entry array of `{hour, price}` dicts

## Installation

1. Add this repository as a custom repository in HACS
2. Install via Home Assistant UI: Settings → Devices & Services → Integrations → Myszolot
3. Configure battery capacity, charger phases, and charging speeds
4. Create automations to actuate charge control (see Examples)

## Configuration

### Config Entry Options

| Option | Default | Description |
|---|---|---|
| `charger_phases` | 3 | 1 or 3-phase charger (3 for Autel) |
| `voltage` | 230V | Line-to-neutral voltage (Polish grid) |
| `fast_amps` | 10 A | Fast charging current |
| `slow_amps` | 5 A | Slow charging current |
| `battery_capacity_kWh` | 68.9 | Total usable battery capacity |
| `default_target_soc` | 80% | Smart/now modes target SoC |
| `trip_target_soc` | 95% | Plan trip / trip now target SoC |
| `min_soc` | 30% | Emergency charge floor |
| `charge_start_soc` | 69% | Smart mode: don't schedule if SoC > this |
| `max_price_threshold` | 1.0 PLN/kWh | Skip all hours if cheapest > this |
| `plan_trip_deadline_hours` | 8 h | Plan trip: schedule window |

**Derived at runtime:**
```
max_charge_rate_kW = fast_amps × voltage × charger_phases / 1000
  Example (3-phase, 10A): 10 × 230 × 3 / 1000 = 6.9 kW
```

## Entities

### Select: Charge Mode
**Entity ID:** `select.myszolot_charge_mode`

Available options:
- `smart` — Smart scheduling (G12 hours, cheapest first, respects charge_start_soc)
- `now_fast` — Charge immediately at fast_amps
- `now_slow` — Charge immediately at slow_amps
- `plan_trip` — Schedule to 95% using all hours (no G12 filter), respects deadline
- `trip_now` — Charge immediately at fast_amps for trip (95% target)

Non-smart modes auto-reset to `smart` when `soc >= target_soc`.

### Sensor: Charge Reason
**Entity ID:** `sensor.myszolot_charge_reason`

**State:** Human-readable charging decision (e.g., "scheduled", "waiting_for_session")

**Attributes:**
- `should_charge` (bool) — Whether charging is recommended now
- `target_amps` (int) — Amperage to set if charging
- `mode` (str) — Current selected mode
- `current_price` (float) — Current electricity price (PLN/kWh)
- `current_soc` (float) — Battery SoC (%)
- `target_soc` (int) — Target SoC (%)
- `E_needed` (float) — Energy needed to reach target (kWh)
- `in_g12` (bool) — Is current hour in G12 off-peak?
- `next_session_start` (datetime) — Next scheduled session start, or None

### Sensor: Charge Schedule
**Entity ID:** `sensor.myszolot_charge_schedule`

**State:** **Estimated remaining cost in PLN** (not minutes)
- Sums the cost of all scheduled sessions at current hourly prices
- Updates on every refresh; decreases as charging progresses

**Example:** SoC 48%, target 80%, need 20 kWh at avg 0.50 PLN/kWh = ~10 PLN state

**Attributes:**
- `sessions` (list) — Scheduled charging windows with start time, end time, kWh, cost
- `E_needed` (float) — Energy still required (kWh)
- `estimated_total_cost` (float) — Sum of session costs (PLN)

### Binary Sensor: Cable Needed
**Entity ID:** `binary_sensor.myszolot_cable_needed`

- **On** when: `should_charge=True AND cable disconnected AND at home`
- **Off** otherwise (away from home, cable connected, or no charge needed)

Trigger for pre-session cable reminder automation (see Examples).

## External Entities (Read)

The integration reads these entities from Home Assistant:

| Entity | Purpose | Example |
|---|---|---|
| `sensor.pstryk_current_buy_price` | Current electricity price & 24h forecast | state=0.50, attr `All prices`=[...] |
| `sensor.myszolot_battery_level` | Current SoC (%) | 48.2 |
| `binary_sensor.myszolot_charge_cable` | Cable connected (on/off) | on / off |
| `device_tracker.myszolot_location` | Car location | home / not_home |
| `sensor.myszolot_charging` | External charging status | charging / idle |

## External Entities (Write)

The integration **does not** write to these. Create automations to actuate them based on `sensor.myszolot_charge_reason`:

| Entity | Purpose | Example |
|---|---|---|
| `switch.myszolot_charge` | Enable/disable charging | on / off |
| `number.myszolot_charge_current` | Set charging current (amps) | 5 to 10 |
| `switch.autel_charge_control` | Enable/disable charger unit | on / off |

## Charge Modes

### Smart (Default)
- **Target SoC:** 80% (configurable)
- **Speed:** Fractional knapsack — fills cheapest hours first
- **Price Filter:** G12 off-peak hours only, skip if all > `max_price_threshold`
- **Charge Start SoC:** Don't schedule new sessions if SoC > 69% (prevents over-scheduling)
- **Window:** Today + tomorrow (24h from now)

**Use case:** Regular daily charging at home, minimize cost.

### Now Fast
- **Target SoC:** 80%
- **Speed:** Fast amperage immediately
- **No time window:** Charges right now, regardless of time
- **No price filter:** Ignores electricity cost

**Use case:** Quick top-up before a trip.

### Now Slow
- **Target SoC:** 80%
- **Speed:** Slow amperage immediately
- **No time window:** Charges right now
- **No price filter:** Ignores electricity cost

**Use case:** Gentle overnight charging to reach 80%.

### Plan Trip
- **Target SoC:** 95% (trip-safe level)
- **Speed:** Fractional knapsack across all hours (no G12 filter)
- **Price Filter:** All hours eligible (no G12 restriction)
- **Window:** Next N hours (configurable, default 8h)
- **Use case:** Schedule a longer road trip within 8 hours, reach 95% charge

### Trip Now
- **Target SoC:** 95%
- **Speed:** Fast amperage immediately
- **No time window:** Charges right now
- **Use case:** Emergency trip preparation (charge to 95% ASAP)

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

```yaml
alias: Myszolot - Cable reminder
triggers:
  - trigger: template
    value_template: >
      {% set next = state_attr('sensor.myszolot_charge_reason', 'next_session_start') %}
      {% if next %}
        {{ (as_timestamp(next) - as_timestamp(now())) | int < 900 }}
      {% else %}
        false
      {% endif %}
conditions:
  - "{{ not is_state('binary_sensor.myszolot_charge_cable', 'on') }}"
  - condition: time
    after: "06:00:00"
    before: "23:30:00"
actions:
  - action: notify.mobile_app_your_phone
    data:
      message: "Myszolot: plug in to charge (session starts soon)"
mode: single
max_exceeded: silent
```

### Dashboard Card

Paste this YAML into Home Assistant → Lovelace (Edit dashboard) → Manual Cards:

```yaml
type: vertical-stack
cards:
  - type: entities
    title: Myszolot Charging
    entities:
      - entity: sensor.myszolot_charge_reason
      - entity: sensor.myszolot_battery_level
      - entity: select.myszolot_charge_mode
      - entity: sensor.myszolot_charge_schedule
      - entity: binary_sensor.myszolot_cable_needed
  - type: grid
    columns: 3
    square: false
    cards:
      - type: button
        name: Smart
        icon: mdi:brain
        tap_action:
          action: call-service
          service: select.select_option
          service_data:
            entity_id: select.myszolot_charge_mode
            option: smart
      # ... (see dashboard-card.yml for full card)
```

Or copy the entire card from `automations/dashboard-card.yml`.

## Algorithm: Fractional Knapsack + Continuous Sessions

1. **Build Schedule**: Select cheapest eligible hours until energy need is met
2. **Merge Sessions**: Adjacent hours (n and n+1) merge into one continuous window
3. **Shift Partial Hours**: If first hour of a group is partial, shift its start to the tail of that hour
4. **Compute Sessions**: List of continuous windows with start/end times

**Example:**
- Need 10 kWh, max 6 kWh/hour
- Hour 13 @ 0.50 PLN → 6 kWh (full)
- Hour 14 @ 0.25 PLN → 4 kWh (partial, 40 min)
- Result: One session 13:00–15:00 (120 min continuous; hour 14 uses only 40 min)

## Schedule Sensor Unit Clarification

**`sensor.myszolot_charge_schedule` state = estimated remaining cost in PLN**

Not minutes, not time. The numeric value represents **the total cost** of all remaining scheduled charging sessions at current hourly prices.

- **Example:** "3.5 PLN" = finishing all remaining sessions will cost approximately 3.5 PLN
- **As charging progresses:** cost decreases (e.g., 3.5 → 1.7 PLN as sessions complete)
- **Recalculates hourly:** when electricity prices change

This is distinct from `sensor.pstryk_current_buy_price` (price/kWh).

## Implementation Behavior

### Coordinator Refresh Triggers

The integration rebuilds the schedule on:
- `sensor.myszolot_battery_level` change (SoC update)
- `device_tracker.myszolot_location` → `home` (arrival)
- `binary_sensor.myszolot_charge_cable` change (plug in/out)
- `sensor.pstryk_current_buy_price` change (hourly price update)
- `select.myszolot_charge_mode` change (mode selection)
- Every 5 minutes (polling fallback)
- Every 1 minute (when in or near a session)

### Mode Auto-Reset

Non-smart modes automatically reset to `smart` when `soc >= target_soc`. Smart mode never auto-resets.

### Charging Started Flag

Once a smart mode charging session begins (reason = `scheduled`), a flag prevents the `charge_start_soc` gate from re-triggering. Charging continues to reach `target_soc` even if SoC rises above `charge_start_soc` during the session.

The flag resets when `soc >= target_soc` is reached.

**Scenario:** Start smart charging at SoC=60%, session scheduled. As SoC rises past 69% (charge_start_soc), charging does NOT stop — it continues to 80% (target_soc).

## Testing

All coordinator logic and scheduler algorithms are tested:

```bash
pytest tests/test_scheduler.py       # Knapsack + session merging
pytest tests/test_coordinator.py    # Reason determination logic
pytest tests/test_config_flow.py    # Configuration validation
```

Run all tests:
```bash
pytest tests/
```

## Integration Lifecycle

### Initial Setup
1. Install integration and configure battery capacity, charger specs
2. Create automations to actuate charge control and cable reminder
3. Add dashboard card to Lovelace
4. Verify `sensor.myszolot_charge_schedule` is populated with sessions

### Daily Operation
- Coordinator refreshes on state changes and polling intervals
- Dashboard displays current decision (`sensor.myszolot_charge_reason`)
- Actuator automation reads `should_charge` and target amps, controls charger
- Cable reminder fires 15 min before session starts if cable is unplugged

### Mode Switching
- Select desired mode via dashboard card or service call
- Coordinator rebuilds schedule immediately
- Non-smart modes auto-reset when target SoC reached
- Smart mode never auto-resets

## Troubleshooting

**Q: `sensor.myszolot_charge_schedule` is empty?**
A: Check that `sensor.pstryk_current_buy_price` has `All prices` attribute with 24-entry array.

**Q: Charging stops mid-session?**
A: Verify cable is connected. Actuator automation calls `switch.turn_off` when `should_charge=False`. Check coordinator reason to diagnose.

**Q: G12 hours not used?**
A: G12 off-peak windows: 00:00–06:00, 13:00–15:00, 22:00–24:00. Confirm current hour is G12 via `sensor.myszolot_charge_reason.in_g12` attribute.

**Q: Session costs seem wrong?**
A: Sessions are recalculated hourly. If prices change, sessions will update. Cost is always: sum of (session kWh × price at that hour).

## License

MIT

## Links

- **GitHub:** [ha_myszolot](https://github.com/you/ha_myszolot)
- **Tesla Charging:** [Tesla Wall Connector](https://www.tesla.com/en_EU/support/wall-connector)
- **Autel Charger:** [Autel HomeCharge AC](https://www.autelpower.com)
- **G12 Tariff:** [Polish Energy (PGNiG/Tauron)](https://www.tauron.pl)
