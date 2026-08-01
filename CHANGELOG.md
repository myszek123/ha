# Changelog

All notable changes to **Myszolot Charging** are documented here.

HACS / Home Assistant show these notes when you update (GitHub Releases use the same text).

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versioning: [SemVer](https://semver.org/).

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
