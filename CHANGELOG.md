# Changelog

All notable changes to **Myszolot Charging** are documented here.

HACS / Home Assistant show these notes when you update (GitHub Releases use the same text).

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versioning: [SemVer](https://semver.org/).

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
