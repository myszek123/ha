# Changelog

All notable changes to **Myszolot Charging** are documented here.

HACS / Home Assistant show these notes when you update (GitHub Releases use the same text).

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versioning: [SemVer](https://semver.org/).

## [1.5.10] — 30-08-2026

### Fixed

- **Vision blip no longer kills a live home session** (20-08 ~14:12): garage
  classifier went empty for 30 s (confidence 52 %) while Tesla was charging,
  Autel was on, GPS home. Planner treated that as `outside_charging`, cleared
  session guards, Autel Remote-off, then `soc_sufficient` at 72 % with cheap
  minutes still left. A physical home charge (Tesla charging + Autel on + GPS
  home) now holds `is_home` and the session guards; the actuator away branch
  and Autel lockdown will not Remote-off in that state either. Starting a new
  session still requires vision + GPS as before.

### Changed

- **Cable reminder arrival grace (3 min).** First plug-in ping waits until
  garage vision has seen the car for 3 minutes (GPS `home` if vision is
  unavailable). Covers 30-08 ~11:39: notify 9 s after pulling in, cable in
  43 s later — a normal plug-in should stay silent. Already-home + cheap
  hour starting still notifies immediately. Quiet hours and the 5/cycle cap
  are unchanged.

## [1.5.9] — 16-08-2026

### Changed

- **No plan is published while `soc_sufficient`.** The knapsack still finds the
  cheapest hours, but the debounce means they will not be used — showing
  "13:39–14:00" for a session that never runs was confusing. Plan attributes
  (`sessions`, `planned_session_start/end`, `planned_kwh`, `planned_cost`,
  `planned_duration_minutes`, `next_session_start`) are empty in that state and
  return as soon as the reason changes. `E_needed` still reports the real gap,
  and a suppressed plan is no longer counted as unfeasible.

### Removed

- **Legacy `sensor.myszolot_charging_reason`** — a second, G12/threshold copy of
  the charge decision, superseded by `sensor.myszolot_charge_reason`. Nothing
  consumed it and it raised a template error on every restart (defaultless
  `| float` on the retired `electricity_*_threshold` helpers).

### Fixed

- **Override timer works again.** `sensor.myszolot_override_remaining` read the
  retired `input_boolean.myszolot_charge_override`, so the dashboard timer always
  showed `Off`. It now reads the integration's `override_remaining_minutes`.

## [1.5.8] — 15-08-2026

### Fixed

- **Session guards now end with the session.** `charging_started` /
  `locked_session_end` were only cleared when the target was reached, so a lock
  from an abandoned block could force `scheduled` at an hour the fresh plan had
  rejected, and the stuck flag disabled the `charge_start_soc` debounce for
  good. Cleared when the car **positively** leaves or is **positively**
  unplugged — a sensor going `unavailable` is not either, and must never end a
  live session.
- **Restart mid-session no longer cuts power.** The guards live in RAM only, so
  after an HA restart a replan could Remote-off a running block (13-08 incident
  class), and above the debounce line the session was dropped outright. A car
  physically charging at home is now adopted as a started session; if the fresh
  plan tail-packs the remaining energy later in the same hour, that gap is
  bridged instead of switching the Autel off for a few minutes.
- **Unreadable SoC is no longer a guessed 0%.** `unavailable` parsed to `0.0`
  and looked like an empty battery, tripping the emergency floor at full amps
  **at any price**. New reason `soc_unknown`: nothing is planned, the floor does
  not fire, and a block already running keeps its lock. A real 0% still charges.

## [1.5.7] — 14-08-2026

### Fixed

- **Plan geometry restored:** cheapest hours filled first; leftover more-expensive
  minutes sit at the **tail** of the earlier hour so the block is continuous
  (today should have been ~12:44–15:00, not start at 12:00). The 1.5.5
  snap-to-now in the current hour was wrong and started sessions early.
- Still **lock** a block once it has actually started (1.5.6) so a replan
  cannot Remote-off mid-session.

## [1.5.6] — 14-08-2026

### Fixed

- **Session abandoned mid-block** (14-08 ~12:12): smart knapsack replan dropped
  the rest of the current hour (later slots cheaper for remaining kWh) → Autel
  Remote-off while still short of target. Once a contiguous block starts, its
  **end is locked** (may extend, never shrink) so charging runs through; real
  gaps after that end still wait.

## [1.5.5] — 13-08-2026

### Fixed

- **Mid-session Autel Remote-off** (13-08 overnight): remaining-energy replan
  tail-packed the current hour (e.g. start 02:33 while now was 02:31) →
  `waiting_for_session` → actuator off → stop notifications. Live sessions now
  start at **now** in the current hour and **hold** for up to 5 min if start
  slides. Planned gaps to a later cheap hour are unchanged. ~1% SoC miss at
  the end is accepted.

## [1.5.4] — 11-08-2026

### Changed

- **Removed amp flatten** — plan and charge at full `fast_amps` only (no
  mid-session 5–10 A smear inside cheap hours).
- **Default / wall cap `fast_amps` = 11 A** (shared house load; not 12).
- **Actuator:** removed 1 A/min slew; set Autel max directly to target (cap 11).

## [1.5.3] — 10-08-2026

### Fixed

- **Plan amps never above `fast_amps`** — car entity idle at 16 A no longer
  inflates `charge_amps` / brief `target_amps` 13–14.
- **Override → smart mis-tap** — 8 s pending window; re-selecting override
  **cancels** smart and keeps the existing deadline/target (no full replan).

### Changed

- **Cable reminder:** max **5** notifications per need cycle
  (`counter.myszolot_cable_reminders`, reset when `cable_needed` clears).
- **Notify** when mode is selected (or charge is planned) while
  `automation.tesla_charging_actuator` is **off**.

### Added

- Attribute `pending_smart` on `sensor.myszolot_charge_reason`.

## [1.5.2] — 01-08-2026

### Fixed

- **Override no longer silently reverts** when car is away: do not adopt a stale
  physical charge-limit; push limit on override enter; charge-limit automation
  also runs when GPS arrives home.
- Session stop notification **kWh delta** (`end − start`, not raw cumulative).
- **Await default charge limit** times out after 15 min (asleep car).
- Coordinator **home presence** matches actuator (garage vision + GPS).
- Charge-limit smart restore uses `default_target_soc` (not hardcoded 80).

### Changed

- Entities group under a **Myszolot Charging** device in HA.

## [1.5.1] — 01-08-2026

### Changed

- **Override window UI is hours again** (`input_number.myszolot_deadline_hours`).
  Coordinator prefers hours; minutes helper is optional legacy fallback only.
- Dashboard card: “Within hours” (not minutes).
- Actuator: **5 s** settle after garage socket off before Autel on (shared circuit).

### Fixed

- Missing-helper notify no longer requires the minutes helper.

## [1.5.0] — 01-08-2026

### Added

- **Feature flag `car_limit_replan` (default ON)** — Configure → Myszolot.
  - Changing **Tesla charge limit** (`number.myszolot_charge_limit`) in the car app
    recalculates the plan in **smart** and **override**.
  - **Override keeps the absolute deadline** (restart-safe window); only target changes.
  - Smart keeps the normal smart horizon (e.g. 48 h).
  - When the session **reaches target SoC**, the car limit is restored to the daily
    default (**80%** / `default_target_soc`) for the next session.
  - Turn off under **Configure** if you want HA helpers to be the only target source.
- Sensor attributes: `car_limit_replan`, `car_charge_limit`.
- Full incident regression suite (`tests/test_charging_scenarios.py`) + car-limit unit tests.
- This changelog + versioned GitHub releases for HACS update notes.

### Changed

- Flatten amp floor documented as **5 A** (Tesla + Autel support).
- Override plans **cheapest hours in window** again (not forced ASAP).
- Override **button** = full replan; **HA restart** restores absolute deadline only.
- Planner never uses car amps below `fast_amps` (stuck 5 A no longer collapses the plan).
- Amp flatten inside knapsack-selected hours; absolute `hard_end` caps override flatten.

### Fixed

- False long window inventing **6 A** sessions when deadline was short.
- Restart minting a fresh “in N hours” window instead of the stored absolute end.

## [1.4.0] — prior

- EV tunables as HA helpers; Autel stop without turning car charge off.
- Weekly drive sensors/card; presence simulation; InternalLinks dashboard.

---

## How releases work

1. Bump `custom_components/myszolot/manifest.json` → `version`.
2. Add a section here under `## [x.y.z]`.
3. Tag `vx.y.z` and publish a **GitHub Release** with this section as the body.
4. HACS picks up the new version and shows the release notes in HA.

```bash
# from repo root after commit
git tag -a v1.5.0 -m "v1.5.0"
git push origin main --tags
gh release create v1.5.0 --title "v1.5.0" --notes-file <(sed -n '/## \[1.5.0\]/,/## \[/p' CHANGELOG.md | head -n -1)
```
