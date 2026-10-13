# The hands-off rule (top priority)

> **When the car is not at home, nothing in Home Assistant may affect the car
> in any shape or form.**
>
> Away, the driver charges however they like: any SoC, any time of day, any
> duration. This applies to the whole integration and every automation, not to
> one component. When it conflicts with any other feature, this rule wins.

Adopted 12-10-2026 after the same class of bug kept coming back (see
*History*). Every change to `custom_components/myszolot/` or `automations/`
must keep it, and its tests must stay green.

## Definitions

| Term | Meaning |
|---|---|
| **Car at home** | `device_tracker.myszolot_location` is exactly `home`. |
| **Car away** | Anything else: another zone (`Brajniki`, `Szczytno`…), `not_home`, `unknown`, `unavailable`, or no tracker. **Unknown counts as away**: the gate fails closed. |
| **Affect the car** | Any service call on a car entity (Tessie: `number.myszolot_*`, `switch.myszolot_*`, `button.myszolot_*`, `cover.myszolot_*`, `lock.myszolot_*`, `climate.myszolot_*`, `select.myszolot_*`, `media_player.myszolot_*`, `update.myszolot_*`). It also covers any automation write to the integration's controls that push to the car (`select.myszolot_charge_mode`, `input_number.myszolot_custom_target_soc`, `input_number.myszolot_deadline_hours` / `_minutes`). |

**`input_boolean.myszolot_location_override` ("force home") and garage vision
do not open the gate.** They decide where *home charging* may start (the Autel
side). They never authorise commanding a car that GPS places elsewhere.

## What is allowed while away

- The integration keeps computing: sensors, reasons (`outside_charging` /
  `outside_not_charging`), plans, mode state. An override can expire or reach
  its target and fall back to smart internally. **Nothing is sent to the car.**
- The **home charger** (Autel) may be switched off and locked. It is a home
  device and does not affect the car or any public charger it uses.
- Notifications to the phone.
- Anything the **user** does by hand (dashboard, app, scripts they press).

## What is never allowed while away

- Writing the car charge limit for any reason: session-complete reset to the
  daily default, override push, override expiry, override → smart.
- Turning car charging on or off, or changing car amps.
- An automation arming override, or changing the target or deadline helpers
  (trip charging arms only at home).

## Returning home

Home rules resume as soon as the tracker reports `home`.
`Tesla Charging - Set Charge Limit by Mode` writes the mode's limit on arrival
(80 % in smart), and the planner takes over. Nothing set away is carried home
on purpose: an override left running away will push its target once you are
home, until it expires or is cancelled.

## How it is enforced

1. **One chokepoint in the integration.**
   `MyszolotCoordinator._async_call_car_service()` is the only function that may
   call a Home Assistant service. It checks `car_writes_allowed()`
   (`coordinator.py`) when the call runs, not only when it is queued, so a write
   scheduled at home is dropped if the car has left by then.
   `_schedule_set_car_charge_limit()` checks too, before it touches any
   adopt-guard state. A refused write is logged once at INFO:
   `Car not home — hands off: not sending …`. `sensor.myszolot_charge_reason`
   exposes `car_writes_allowed`.
2. **Automations.** Every action aimed at a car entity or an integration
   control must sit under a home condition, in one of three canonical forms:
   - a state condition (automation-level, a `choose` option or an `if`):
     ```yaml
     - condition: state
       entity_id: device_tracker.myszolot_location
       state: home
     ```
   - a zone condition: `condition: zone`,
     `entity_id: device_tracker.myszolot_location`, `zone: zone.home` (used by
     the live garage charge-button script);
   - a template condition **starting** with
     `{{ is_state('device_tracker.myszolot_location', 'home') and …`.

   A tracker check buried in an `or` does not count.
3. **Tests**, in `tests/test_away_hands_off.py`:
   - **Behaviour:** the real update loop with the car away (7 tracker states)
     through every path that used to write, asserting zero service calls. Each
     path has a home control case showing it really writes when home. These
     tests fail 63 times against the pre-fix code.
   - **Code:** an AST check that no function but the chokepoint calls a
     service, and that the chokepoint checks the gate first.
   - **Automations:** a scanner over `automations/*.yml` and `scripts/*.yml`
     that fails on any unguarded car action. A self-test makes sure the scanner
     still catches violations.

## Checklist for any change touching control logic

- [ ] Does it send anything to the car? Then it goes through
      `_async_call_car_service` (integration) or under a canonical home
      condition (automation).
- [ ] Does it add a new car-affecting entity or control? Add it to
      `CAR_DOMAINS` / `INTEGRATION_CONTROLS` in the test.
- [ ] Does a new path write to the car? Add an away test **and** a home
      control test.
- [ ] Does the PR description say how the rule is kept?

## History

- **08-09-2026:** a manually set 96 % was reset to 80 by
  `should_reset_car_limit_after_session()` when SoC reached 80. Worked around
  with override, which itself pushed limits while away.
- **Trip charging** was deliberately built to arm the return leg from the
  cabin and push the trip target to the car while away.
- **12-10-2026, Brajniki:** smart mode, SoC 81 %. The driver set 95, 82, 56,
  83, 94 and 93, and every value was reset to 80 within ~3 s, even with every
  automation disabled. The writes came from the integration's
  `car_limit_replan` feature, and its options dialog returned HTTP 500, so it
  could not be switched off either. This rule exists because of that day.
