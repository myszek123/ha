# Changelog

All notable changes to **Myszolot Charging** are documented here.

HACS / Home Assistant show these notes when you update (GitHub Releases use the same text).

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
versioning: [SemVer](https://semver.org/).

## [1.5.12] — 10-09-2026

### Added

- **Phone alert when the Autel charger is offline**
  (`automations/autel-offline-notify.yml`). Once `switch.autel_charge_control`
  has been unavailable for 10 min, HA notifies. It then reminds while a planned
  charge is blocked by the outage (checked every 30 min, at most every 2 h,
  quiet 23:30–06:00), and reports recovery. All three share one notification
  tag, so they replace each other instead of stacking. Overnight 11/12-09-2026
  the charger stayed unreachable after a WiFi drop and a planned charge
  silently never ran.
- **`NN%` tag: trips to any destination, each with its own target SoC.** Key
  places (Łódź, Brajniki, Szczytno, działka) still arm on their own at the
  default 96 %. Any drive now arms when its calendar entry carries a two-digit
  percentage in the title or description, and that number is the target:
  `Ciechanów:95%` charges to 95 %, `Przasnysz 90%` to 90 %. Values outside the
  target helper's range (50–100) are ignored, the highest wins when an entry
  holds several, and a key place with a tag uses the tag. `\s?` also takes
  `95 %` and a phone keyboard's non-breaking space; the number must not follow
  a digit, `.` or `,`, so `196%` and a rate like `lokata 3,96%` do not count. A
  new destination is a calendar edit, not an automation change. Make the entry
  span the whole stay: its start and its end both count as departures. Known
  false positive: an unrelated entry such as `Promocja 70%` arms if 70 is above
  the current SoC.

### Fixed

- **Charging actuator no longer toggles the car's charge switch every minute
  while the Autel is offline.** The Start branch turned `switch.myszolot_charge`
  on whenever it was "not on". With no power the car rejected the command and
  flipped back to off, so it repeated every minute: 420 on/off toggles overnight
  11/12-09-2026. It now runs only while the Autel is controllable and the car
  switch is really `off`.
- **Charging actuator no longer loops start commands at an unreachable
  charger.** After the 10-09-2026 WiFi drop, `switch.autel_charge_control`
  stayed `unavailable` although the charger was charging again. The start
  guard `not is_state(..., 'on')` is also true for `unavailable`, so every run
  sent `RemoteStartTransaction`, the charger answered Rejected, and the OCPP
  integration re-raised *"Start transaction failed"* 701 times. The Autel is
  now started only when its switch is really `off` and the car is not already
  charging. The max-current write is skipped while that number is
  unavailable, and the `charge_reason` trigger ignores attribute-only updates
  (`to: null` plus an `attribute: should_charge` trigger), which had pushed
  the actuator from 60 to 106–174 runs/h. While the charger's OCPP entities
  stay unavailable HA can neither start nor stop it; power-cycle the charger
  to restore control.
- **Trip charging re-armed every 30 min once the car was already charged.**
  The `mode == smart` condition was documented as an arm-once guard. It is not:
  override ends on *either* the deadline passing *or* the target being reached,
  and target-reached is the normal case — the coordinator drops straight back to
  smart and restores the 80 % limit. The next scan then saw smart again, found
  the same departure still more than 60 min out, and re-armed. Observed
  08-09-2026: SoC hit 96 % at 13:56, and the 14:00 / 14:30 / 15:00 scans each
  flapped the car limit 80 → 96 → 80 within ~20 s and pushed a
  "Powrót — ładowanie 96 %" notification, with nothing left to charge. Arming
  now also requires SoC to be below the trip's target, checked per event; an
  unreadable SoC still arms, failing toward charging.
- **Trip charging missed every declined form of "Łódź."** The destination
  pattern used `łód|lod[zź]`, which matches the bare nominative *Łódź* and the
  ASCII *Lodz* but not *Łodzi* — the form Polish actually uses in a calendar
  entry ("Wyjazd do Łodzi", "U mamy w Łodzi"). The declined stem keeps `ł` but
  drops the `ó`, so neither alternative fired. The Łódź run is the longest
  trip on the list, so the one drive that most needs 96 % was the one that
  silently never armed — no match, no notification, no error. Pattern is now
  `[łl][oó]d[zźż]`, anchored to the start of a word so it does not fire inside
  *Włodzimierz*, *Kołodziej* or *chłodzenie*; verified against the live template
  engine. The street alternative `leżakow` likewise no longer matches
  *leżakowanie* (kindergarten nap time).
- **All-day trips armed the return leg 24 h late.** All-day `end` dates are
  exclusive (iCal/Google), so a stay whose last day is 08-09 arrives as
  `end: 2026-09-09` and the return departure was computed for the following
  morning — after you were already home. The end leg now steps back one day.
  Date-only arithmetic, so DST transitions cannot shift the 09:00.

### Changed

- `mode: queued` / `max: 5` → `mode: single`. The queue existed to protect a
  zone-arrival trigger that no longer exists; with one 30 min trigger a run
  cannot overlap itself.
- Dropped the dead upper clamp in `window_hours` — the `<= 960 min` filter
  already bounds the window to 16 h, well inside the helper's 1–48 range.

### Documentation

- **Corrected the 1.5.11 notes below, which described a feature that never
  shipped.** They documented a "Return branch" triggering on `zone` enter and
  pushing `number.myszolot_charge_limit = 96` on arrival. That branch was
  removed before release (commit *Drop arrival branch: charge only before
  departure*); the shipped automation has a single `time_pattern` trigger and
  references no zone. README carried the same phantom branch.
- `automations/trip-helpers.yml` claimed its zones were required by
  `trip-charging.yml`, and that charger-lockdown and presence depended on
  `zone.wisniewscy`'s geometry. Both false — no automation in this repo
  references any zone, and charger-lockdown keys on
  `device_tracker.myszolot_location`. The file is now marked reference-only;
  the coordinates and the Brajniki-vs-Szczytno geocoding are kept.

## [1.5.11] — 08-09-2026

### Added

- **Trip charging** (automations only — no integration code changed).
  `automations/trip-charging.yml` scans both family calendars every 30 min for
  a drive to Łódź / Brajniki / Szczytno within the next 16 h and arms override
  at 96 % with the deadline set to the departure time, so the existing planner
  still buys the cheapest hours inside the window instead of charging flat out.
  Both legs of a stay-shaped event count as departures: the start is the drive
  out, the end is the drive home. At home the planner actuates the charger;
  away it cannot, but override still pins the car's own limit to 96 %, so
  plugging in at the działka charges for the drive back. Charging is tied to
  departure only — never arrival, which would hold the pack near full for the
  whole stay.

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
