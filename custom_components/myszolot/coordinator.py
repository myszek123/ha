"""Coordinator for Myszolot charging scheduler."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, date as date_type
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.storage import Store

from .const import (
    DOMAIN,
    SENSOR_PRICE, SENSOR_SOC, BINARY_SENSOR_CABLE, BINARY_SENSOR_GARAGE_CAR,
    DEVICE_TRACKER, SENSOR_CHARGING, NUMBER_CHARGE_CURRENT,
    MODE_SMART, MODE_OVERRIDE,
    CONF_CHARGER_PHASES, CONF_VOLTAGE, CONF_FAST_AMPS,
    CONF_BATTERY_CAPACITY_KWH, CONF_DEFAULT_TARGET_SOC,
    CONF_MIN_SOC, CONF_CHARGE_START_SOC, CONF_MAX_PRICE_THRESHOLD,
    CONF_SMART_DEADLINE_HOURS,
    DEFAULT_CHARGER_PHASES, DEFAULT_VOLTAGE, DEFAULT_FAST_AMPS,
    DEFAULT_BATTERY_CAPACITY_KWH, DEFAULT_TARGET_SOC,
    DEFAULT_MIN_SOC, DEFAULT_CHARGE_START_SOC, DEFAULT_MAX_PRICE_THRESHOLD,
    DEFAULT_SMART_DEADLINE_HOURS, DEFAULT_OVERRIDE_DEADLINE_HOURS,
    DEFAULT_OVERRIDE_DEADLINE_MINUTES, DEFAULT_MIN_FLAT_AMPS,
    INPUT_BOOLEAN_LOCATION_OVERRIDE, INPUT_NUMBER_CUSTOM_TARGET_SOC,
    INPUT_NUMBER_DEADLINE_HOURS, INPUT_NUMBER_DEADLINE_MINUTES,
    INPUT_NUMBER_MIN_SOC, INPUT_NUMBER_MAX_PRICE_THRESHOLD,
    STORAGE_OVERRIDE_PLAN,
    REASON_OUTSIDE_CHARGING, REASON_OUTSIDE_NOT_CHARGING, REASON_TARGET_REACHED,
    REASON_MIN_SOC_FLOOR, REASON_SOC_SUFFICIENT, REASON_PRICE_TOO_HIGH,
    REASON_SCHEDULED, REASON_WAITING_FOR_SESSION, REASON_NO_ELIGIBLE_HOURS,
    REASON_HOME_NOT_PLUGGED, REASON_TARGET_UNREACHABLE,
)

_LOGGER = logging.getLogger(__name__)

_UNAVAILABLE = {"unknown", "unavailable", "none", ""}


def _get_pstryk_tomorrow_prices(hass: HomeAssistant, tomorrow_str: str) -> list[dict]:
    """
    Extract tomorrow's prices from the pstryk coordinator's internal data.

    Returns an empty list if fewer than 20 entries are found.
    """
    tomorrow_prices = []

    pstryk_data = hass.data.get("pstryk", {})
    for key, coordinator_or_entry in pstryk_data.items():
        if not key.endswith("_buy"):
            continue
        try:
            if hasattr(coordinator_or_entry, "data"):
                prices_list = coordinator_or_entry.data.get("prices", [])
            else:
                continue
        except Exception:
            continue

        for entry in prices_list:
            if isinstance(entry, dict):
                start = entry.get("start", "")
                if start.startswith(tomorrow_str):
                    tomorrow_prices.append(entry)

    tomorrow_prices.sort(key=lambda e: e.get("start", ""))

    if len(tomorrow_prices) < 20:
        return []

    return tomorrow_prices


def build_schedule(
    all_prices: list[dict],
    E_needed: float,
    max_kWh_per_hour: float,
    now_dt: datetime,
    deadline_hours: int = 24,
    max_price: float | None = None,
) -> list[dict]:
    """
    Fractional knapsack: select cheapest eligible hours to cover E_needed.

    Returns list of {hour, minutes, kWh, cost, full} sorted chronologically.
    """
    if E_needed <= 0 or max_kWh_per_hour <= 0:
        return []

    now_hour = now_dt.hour
    eligible = [
        h for h in all_prices
        if h["hour"] >= now_hour
        and h["hour"] < now_hour + deadline_hours
        and (max_price is None or h["price"] <= max_price)
    ]
    eligible.sort(key=lambda h: h["price"])

    schedule: list[dict] = []
    remaining = E_needed
    for slot in eligible:
        if remaining <= 0:
            break
        allocate_kWh = min(max_kWh_per_hour, remaining)
        allocate_minutes = int(allocate_kWh / max_kWh_per_hour * 60)
        schedule.append(
            {
                "hour": slot["hour"],
                "minutes": allocate_minutes,
                "kWh": allocate_kWh,
                "cost": round(allocate_kWh * slot["price"], 4),
                "full": allocate_kWh >= max_kWh_per_hour - 0.01,
            }
        )
        remaining -= allocate_kWh

    return sorted(schedule, key=lambda s: s["hour"])


def build_asap_schedule(
    E_needed: float,
    max_kWh_per_hour: float,
    now_dt: datetime,
    deadline_end: datetime,
    all_prices: list[dict] | None = None,
) -> list[dict]:
    """
    Continuous charge from *now* for E_needed (override / urgency).

    deadline_end is an absolute wall-clock end (never “another N hours from now”
    on each tick). Does not skip to later cheaper hours.
    """
    if E_needed <= 0 or max_kWh_per_hour <= 0:
        return []
    if deadline_end <= now_dt:
        return []

    price_by_hour = _price_map(all_prices)
    max_minutes = max(1, int((deadline_end - now_dt).total_seconds() / 60.0))
    need_minutes = max(1, int(E_needed / max_kWh_per_hour * 60.0 + 0.999))  # ceil
    total_minutes = min(need_minutes, max_minutes)

    midnight = now_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    schedule: list[dict] = []
    remaining = float(total_minutes)
    cur = now_dt.replace(second=0, microsecond=0)
    first = True

    while remaining > 0.5:
        if first:
            minutes_left = 60 - cur.minute
            if minutes_left <= 0:
                cur = cur.replace(minute=0) + timedelta(hours=1)
                minutes_left = 60
            alloc_m = min(remaining, minutes_left)
            first = False
        else:
            alloc_m = min(remaining, 60.0)

        hour_start = cur.replace(minute=0, second=0, microsecond=0)
        h_idx = int((hour_start - midnight).total_seconds() // 3600)
        price = float(price_by_hour.get(h_idx, 0.0))
        kwh = max_kWh_per_hour * alloc_m / 60.0
        schedule.append(
            {
                "hour": h_idx,
                "minutes": int(alloc_m),
                "kWh": kwh,
                "cost": round(kwh * price, 4),
                "full": alloc_m >= 59.5,
            }
        )
        remaining -= alloc_m
        cur = hour_start + timedelta(hours=1)

    return schedule


def max_energy_in_window(
    all_prices: list[dict],
    max_kWh_per_hour: float,
    now_dt: datetime,
    deadline_hours: int,
) -> float:
    """Max kWh if charging continuously for every eligible hour in the window."""
    if max_kWh_per_hour <= 0 or deadline_hours <= 0:
        return 0.0

    now_hour = now_dt.hour
    if all_prices:
        n_slots = sum(
            1
            for h in all_prices
            if h["hour"] >= now_hour and h["hour"] < now_hour + deadline_hours
        )
        if n_slots > 0:
            return round(n_slots * max_kWh_per_hour, 4)

    # No price grid — assume continuous availability for deadline_hours
    return round(deadline_hours * max_kWh_per_hour, 4)


def max_reachable_soc(
    current_soc: float,
    max_kWh: float,
    battery_kWh: float,
) -> float:
    """Upper-bound SoC if we charge full rate for the whole window."""
    if battery_kWh <= 0 or max_kWh <= 0:
        return round(float(current_soc), 1)
    return round(min(100.0, float(current_soc) + max_kWh / battery_kWh * 100.0), 1)


def compute_sessions(schedule: list[dict], ref_date: date_type) -> list[dict]:
    """
    Group adjacent hours into continuous charging sessions.

    A partial first slot in a group is shifted to the tail of that hour so
    charging within the group is uninterrupted.
    """
    if not schedule:
        return []

    groups: list[list[dict]] = []
    current_group = [schedule[0]]
    for slot in schedule[1:]:
        if slot["hour"] == current_group[-1]["hour"] + 1:
            current_group.append(slot)
        else:
            groups.append(current_group)
            current_group = [slot]
    groups.append(current_group)

    sessions: list[dict] = []
    for group in groups:
        first, last = group[0], group[-1]

        day_offset = first["hour"] // 24
        actual_date = ref_date + timedelta(days=day_offset)
        actual_hour = first["hour"] % 24

        start_minute = (60 - first["minutes"]) if not first["full"] else 0

        start = datetime(
            actual_date.year, actual_date.month, actual_date.day,
            actual_hour, start_minute,
        )
        total_minutes = sum(s["minutes"] for s in group)
        end = start + timedelta(minutes=total_minutes)

        sessions.append(
            {
                "start": start,
                "end": end,
                "slots": group,
                "total_kWh": round(sum(s["kWh"] for s in group), 4),
                "total_cost": round(sum(s["cost"] for s in group), 4),
                "amps": None,  # filled by flatten_sessions
                "flattened": False,
            }
        )

    return sessions


def flatten_sessions(
    sessions: list[dict],
    charge_amps: int,
    now_dt: datetime,
    min_flat_amps: int = DEFAULT_MIN_FLAT_AMPS,
    hard_end: datetime | None = None,
) -> list[dict]:
    """
    Second pass: lower continuous amps inside already-selected hours.

    Pass 1 packs energy at full rate. Pass 2 expands each continuous group to
    the hour-span of those slots (no new price hours) and drops amps.

    hard_end: absolute cap (override wall-clock deadline). Flatten must never
    invent a longer window than this — that was the “still 2h left → 6A” bug.
    """
    if charge_amps <= 0 or not sessions:
        return sessions

    min_a = max(1, min(int(min_flat_amps), int(charge_amps)))
    out: list[dict] = []

    for sess in sessions:
        slots = sess.get("slots") or []
        if not slots:
            s = dict(sess)
            s["amps"] = charge_amps
            s["flattened"] = False
            out.append(s)
            continue

        first_hour = int(slots[0]["hour"])
        last_hour = int(slots[-1]["hour"])
        # Full hour-boundary window of hours already chosen by the knapsack
        first_hour_start = sess["start"].replace(minute=0, second=0, microsecond=0)
        window_start = first_hour_start
        window_end = first_hour_start + timedelta(hours=(last_hour - first_hour + 1))
        # Never stretch past absolute override deadline
        if hard_end is not None and window_end > hard_end:
            window_end = hard_end

        ws = window_start
        if now_dt > ws:
            ws = now_dt.replace(second=0, microsecond=0)
        if ws >= window_end:
            s = dict(sess)
            s["amps"] = charge_amps
            s["flattened"] = False
            out.append(s)
            continue

        need_minutes = float(sum(int(sl["minutes"]) for sl in slots))
        avail_minutes = (window_end - ws).total_seconds() / 60.0
        if avail_minutes <= 0:
            s = dict(sess)
            s["amps"] = charge_amps
            s["flattened"] = False
            out.append(s)
            continue

        # No slack in selected hours → keep packed full-rate session
        if avail_minutes <= need_minutes + 0.5:
            s = dict(sess)
            s["amps"] = charge_amps
            s["flattened"] = False
            if hard_end is not None and s["end"] > hard_end:
                s["end"] = hard_end
            if s["end"] <= s["start"]:
                s["end"] = s["start"] + timedelta(minutes=1)
            out.append(s)
            continue

        raw_amps = charge_amps * need_minutes / avail_minutes
        flat_amps = int(raw_amps)  # floor: never exceed planned energy rate

        if flat_amps < min_a:
            # Do not go below min_a: shorter block inside the window at min_a
            flat_amps = min_a
            duration = need_minutes * charge_amps / flat_amps
            if duration >= avail_minutes - 0.5:
                flat_amps = max(
                    min_a,
                    min(charge_amps, int(charge_amps * need_minutes / avail_minutes) or min_a),
                )
                start, end = ws, window_end
            else:
                start = ws
                end = ws + timedelta(minutes=duration)
        elif flat_amps >= charge_amps:
            s = dict(sess)
            s["amps"] = charge_amps
            s["flattened"] = False
            if hard_end is not None and s["end"] > hard_end:
                s["end"] = hard_end
            out.append(s)
            continue
        else:
            start, end = ws, window_end

        if hard_end is not None and end > hard_end:
            end = hard_end
        flat_amps = max(min_a, min(charge_amps, int(flat_amps)))
        s = dict(sess)
        s["start"] = start
        s["end"] = end
        s["amps"] = flat_amps
        s["flattened"] = flat_amps < charge_amps
        out.append(s)

    return out


def session_target_amps(sessions: list[dict], now_dt: datetime, default_amps: int) -> int:
    """Amps for the active session, or default_amps if none active."""
    for s in sessions:
        if s["start"] <= now_dt < s["end"]:
            a = s.get("amps")
            if a is not None and int(a) > 0:
                return int(a)
            return default_amps
    return default_amps


def is_in_session(sessions: list[dict], now_dt: datetime) -> bool:
    """Return True if now_dt falls within any charging session."""
    return any(s["start"] <= now_dt < s["end"] for s in sessions)


def next_session(sessions: list[dict], now_dt: datetime) -> dict | None:
    """Return the next upcoming session, or None if none exist."""
    future = [s for s in sessions if s["start"] > now_dt]
    return min(future, key=lambda s: s["start"]) if future else None


def _price_map(all_prices: list[dict] | None) -> dict[int, float]:
    """Map hour index → price (hour may be >= 24 for tomorrow)."""
    if not all_prices:
        return {}
    return {int(h["hour"]): float(h["price"]) for h in all_prices}


def _hour_index(dt: datetime, ref_date: date_type) -> int:
    """Hour index relative to ref_date midnight (0–23 today, 24–47 tomorrow, …)."""
    day_offset = (dt.date() - ref_date).days
    return day_offset * 24 + dt.hour


def estimate_window_cost(
    start: datetime,
    end: datetime,
    max_charge_rate_kW: float,
    all_prices: list[dict] | None,
    fallback_price: float,
    ref_date: date_type,
) -> tuple[float, float]:
    """Estimate kWh and cost for a continuous charge window at constant rate."""
    if end <= start or max_charge_rate_kW <= 0:
        return 0.0, 0.0

    prices = _price_map(all_prices)
    total_kWh = 0.0
    total_cost = 0.0
    cursor = start
    while cursor < end:
        slot_end = min(end, cursor.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))
        minutes = (slot_end - cursor).total_seconds() / 60.0
        kwh = max_charge_rate_kW * minutes / 60.0
        price = prices.get(_hour_index(cursor, ref_date), fallback_price)
        total_kWh += kwh
        total_cost += kwh * price
        cursor = slot_end

    return round(total_kWh, 4), round(total_cost, 4)


def expected_end_soc(
    current_soc: float,
    planned_kWh: float,
    battery_kWh: float,
    target_soc: float,
) -> float:
    """Project SoC after delivering planned_kWh, clamped to target ceiling."""
    if battery_kWh <= 0 or planned_kWh <= 0:
        return round(min(float(target_soc), float(current_soc)), 1)
    delta = planned_kWh / battery_kWh * 100.0
    return round(min(float(target_soc), float(current_soc) + delta), 1)


def session_duration_minutes(sessions: list[dict]) -> int:
    """Total planned charging minutes across all sessions."""
    if not sessions:
        return 0
    total = 0.0
    for s in sessions:
        total += (s["end"] - s["start"]).total_seconds() / 60.0
    return int(round(total))


def determine_reason(
    mode: str,
    is_home: bool,
    cable_connected: bool,
    current_soc: float,
    target_soc: int,
    min_soc: int,
    charge_start_soc: int,
    charge_amps: int,
    sessions: list[dict],
    now_dt: datetime,
    E_needed: float,
    schedule_all_prices_above_max: bool,
    is_externally_charging: bool = False,
    charging_started: bool = False,
    feasible: bool = True,
) -> tuple[str, bool, int]:
    """
    Determine the charging reason, whether to charge, and target amps.

    Returns (reason, should_charge, target_amps).
    """
    # 1. Not home
    if not is_home:
        reason = REASON_OUTSIDE_CHARGING if is_externally_charging else REASON_OUTSIDE_NOT_CHARGING
        return reason, False, 0

    # 2. Home, no cable, SoC already at target
    if not cable_connected and current_soc >= target_soc:
        return REASON_TARGET_REACHED, False, 0

    # 3. Emergency: SoC below floor and cable plugged in
    if current_soc < min_soc and cable_connected:
        return REASON_MIN_SOC_FLOOR, True, charge_amps

    # 4. Smart (daily default) — price gate + charge_start_soc debounce
    if mode == MODE_SMART:
        if not charging_started and current_soc > charge_start_soc:
            return REASON_SOC_SUFFICIENT, False, 0
        if schedule_all_prices_above_max:
            return REASON_PRICE_TOO_HIGH, False, 0
        if is_in_session(sessions, now_dt):
            return REASON_SCHEDULED, True, session_target_amps(
                sessions, now_dt, charge_amps
            )
        ns = next_session(sessions, now_dt)
        if ns:
            return REASON_WAITING_FOR_SESSION, False, 0
        if E_needed > 0:
            return REASON_NO_ELIGIBLE_HOURS, False, 0
        return REASON_TARGET_REACHED, False, 0

    # 5. Override — reach target within deadline; no price hard-stop, no SoC debounce
    if mode == MODE_OVERRIDE:
        if is_in_session(sessions, now_dt):
            return REASON_SCHEDULED, True, session_target_amps(
                sessions, now_dt, charge_amps
            )
        ns = next_session(sessions, now_dt)
        if ns:
            return REASON_WAITING_FOR_SESSION, False, 0
        if E_needed > 0:
            if not feasible:
                return REASON_TARGET_UNREACHABLE, False, 0
            return REASON_NO_ELIGIBLE_HOURS, False, 0
        return REASON_TARGET_REACHED, False, 0

    # 6. Fallback
    return REASON_HOME_NOT_PLUGGED, False, 0


def _garage_car_present(hass: HomeAssistant) -> bool | None:
    """Return True/False from vision sensor, or None when unavailable."""
    state = hass.states.get(BINARY_SENSOR_GARAGE_CAR)
    if state is None or state.state.lower() in _UNAVAILABLE:
        return None
    return state.state == "on"


def _car_in_garage(hass: HomeAssistant, tracker_home: bool) -> bool:
    """Prefer Frigate spot classifier; fall back to device tracker."""
    garage = _garage_car_present(hass)
    if garage is not None:
        return garage
    return tracker_home


def _parse_float(state_obj, attr: str | None = None) -> float | None:
    """Safely read a float from a state object or its attribute."""
    if state_obj is None:
        return None
    try:
        raw = state_obj.attributes.get(attr) if attr else state_obj.state
        if str(raw).lower() in _UNAVAILABLE:
            return None
        return float(raw)
    except (ValueError, TypeError):
        return None


def _parse_all_prices(price_state) -> list[dict]:
    """Extract a list of {hour, price} from the pstryk sensor attributes."""
    if price_state is None:
        return []
    raw = price_state.attributes.get("All prices", [])
    if not raw:
        return []
    result = []
    for i, entry in enumerate(raw):
        if isinstance(entry, dict):
            hour = int(entry.get("hour", i))
            price = float(entry.get("price", 0))
        else:
            hour = i
            price = float(entry)
        result.append({"hour": hour, "price": price})
    return result


class MyszolotCoordinator(DataUpdateCoordinator):
    """Coordinator: rebuilds schedule on every relevant state change."""

    def __init__(self, hass: HomeAssistant, config_entry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=5),
        )
        self.config_entry = config_entry
        self._mode: str = MODE_SMART
        self._charging_started: bool = False
        # Locked when override is selected (target + absolute wall-clock deadline)
        self._override_target_soc: int | None = None
        self._override_deadline: datetime | None = None
        self._override_deadline_minutes: int | None = None
        self._unsub_listeners: list = []
        self._override_store = Store(hass, 1, STORAGE_OVERRIDE_PLAN)

    @property
    def mode(self) -> str:
        return self._mode

    def _read_helper_int(self, entity_id: str, default: int) -> int:
        state = self.hass.states.get(entity_id)
        if state is None or state.state in _UNAVAILABLE:
            return default
        try:
            return int(float(state.state))
        except (ValueError, TypeError):
            return default

    def _read_helper_float(self, entity_id: str, default: float) -> float:
        state = self.hass.states.get(entity_id)
        if state is None or state.state in _UNAVAILABLE:
            return default
        try:
            return float(state.state)
        except (ValueError, TypeError):
            return default

    def _read_deadline_minutes(self) -> int:
        """Prefer minutes helper; fall back to hours × 60."""
        state = self.hass.states.get(INPUT_NUMBER_DEADLINE_MINUTES)
        if state is not None and state.state not in _UNAVAILABLE:
            try:
                return max(1, min(48 * 60, int(float(state.state))))
            except (ValueError, TypeError):
                pass
        hours = self._read_helper_int(
            INPUT_NUMBER_DEADLINE_HOURS, DEFAULT_OVERRIDE_DEADLINE_HOURS
        )
        return max(1, min(48 * 60, int(hours) * 60))

    def _schedule_persist_override(self) -> None:
        self.hass.async_create_task(self._async_persist_override())

    async def _async_persist_override(self) -> None:
        if self._mode == MODE_OVERRIDE and self._override_deadline is not None:
            data = {
                "mode": MODE_OVERRIDE,
                "target_soc": self._override_target_soc,
                "deadline": self._override_deadline.isoformat(),
                "deadline_minutes": self._override_deadline_minutes,
            }
        else:
            data = {}
        await self._override_store.async_save(data)

    async def _async_restore_override(self) -> None:
        """Restore absolute deadline after HA restart (never mint a fresh N minutes)."""
        data = await self._override_store.async_load()
        if not data or data.get("mode") != MODE_OVERRIDE:
            return
        raw_dl = data.get("deadline")
        if not raw_dl:
            return
        try:
            deadline = datetime.fromisoformat(raw_dl)
        except (TypeError, ValueError):
            return
        # naive local times from datetime.now() — compare consistently
        if deadline.tzinfo is not None:
            deadline = deadline.replace(tzinfo=None)
        if deadline <= datetime.now():
            await self._override_store.async_save({})
            return
        self._mode = MODE_OVERRIDE
        self._override_deadline = deadline
        self._override_target_soc = data.get("target_soc")
        self._override_deadline_minutes = data.get("deadline_minutes")
        _LOGGER.info(
            "Restored override from storage: target=%s deadline=%s",
            self._override_target_soc,
            self._override_deadline,
        )

    def set_mode(self, mode: str) -> None:
        """Select smart (default) or override (locked target + absolute deadline).

        Button / UI select of override = full replan: target + fresh window
        (now + hours/minutes helper). HA restart restores the *persisted*
        absolute deadline and does not call set_mode — so restart ≠ button.
        """
        if mode not in (MODE_SMART, MODE_OVERRIDE):
            _LOGGER.warning("Unknown mode %s; ignoring", mode)
            return

        prev = self._mode
        now = datetime.now()

        if mode == MODE_OVERRIDE:
            target = max(50, min(100, self._read_helper_int(
                INPUT_NUMBER_CUSTOM_TARGET_SOC, DEFAULT_TARGET_SOC
            )))
            minutes = self._read_deadline_minutes()
            self._mode = MODE_OVERRIDE
            self._override_deadline_minutes = minutes
            self._override_target_soc = target
            # Always full replan on button hit (even if already in override)
            self._override_deadline = now + timedelta(minutes=minutes)
            self._charging_started = False
            _LOGGER.info(
                "Override full replan: target=%d%% within %d min (deadline %s)",
                target, minutes, self._override_deadline,
            )
            self._schedule_persist_override()
        else:
            self._mode = MODE_SMART
            self._override_target_soc = None
            self._override_deadline = None
            self._override_deadline_minutes = None
            if mode != prev:
                self._charging_started = False
            self._schedule_persist_override()

    def _reset_to_smart(self, reason: str) -> None:
        _LOGGER.info("Resetting mode to smart (%s)", reason)
        self._mode = MODE_SMART
        self._override_target_soc = None
        self._override_deadline = None
        self._override_deadline_minutes = None
        self._charging_started = False
        self._schedule_persist_override()

    async def async_setup(self) -> None:
        """Register state-change listeners for all relevant external entities."""
        await self._async_restore_override()
        tracked = [
            SENSOR_SOC,
            BINARY_SENSOR_CABLE,
            DEVICE_TRACKER,
            SENSOR_PRICE,
            NUMBER_CHARGE_CURRENT,
            INPUT_NUMBER_CUSTOM_TARGET_SOC,
            INPUT_NUMBER_DEADLINE_HOURS,
            INPUT_NUMBER_DEADLINE_MINUTES,
            INPUT_NUMBER_MIN_SOC,
            INPUT_NUMBER_MAX_PRICE_THRESHOLD,
        ]

        @callback
        def _on_state_change(_event) -> None:  # type: ignore[override]
            self.hass.async_create_task(self.async_request_refresh())

        for entity_id in tracked:
            unsub = async_track_state_change_event(self.hass, entity_id, _on_state_change)
            self._unsub_listeners.append(unsub)

    async def async_unload(self) -> None:
        """Unregister all state-change listeners."""
        for unsub in self._unsub_listeners:
            unsub()
        self._unsub_listeners.clear()

    async def _async_update_data(self) -> dict[str, Any]:
        """Compute the current charging decision."""
        cfg = {**self.config_entry.data, **(self.config_entry.options or {})}

        fast_amps: int = cfg.get(CONF_FAST_AMPS, DEFAULT_FAST_AMPS)
        battery_kWh: float = cfg.get(CONF_BATTERY_CAPACITY_KWH, DEFAULT_BATTERY_CAPACITY_KWH)
        default_target_soc: int = cfg.get(CONF_DEFAULT_TARGET_SOC, DEFAULT_TARGET_SOC)
        # Prefer HA helpers (tunable without reconfig); fall back to config entry
        min_soc_default = int(cfg.get(CONF_MIN_SOC, DEFAULT_MIN_SOC))
        min_soc: int = max(0, min(100, self._read_helper_int(
            INPUT_NUMBER_MIN_SOC, min_soc_default
        )))
        charge_start_soc: int = cfg.get(CONF_CHARGE_START_SOC, DEFAULT_CHARGE_START_SOC)
        max_price_default = float(cfg.get(CONF_MAX_PRICE_THRESHOLD, DEFAULT_MAX_PRICE_THRESHOLD))
        max_price_threshold: float = max(0.0, self._read_helper_float(
            INPUT_NUMBER_MAX_PRICE_THRESHOLD, max_price_default
        ))
        smart_deadline_hours: int = cfg.get(
            CONF_SMART_DEADLINE_HOURS, DEFAULT_SMART_DEADLINE_HOURS
        )
        charger_phases: int = cfg.get(CONF_CHARGER_PHASES, DEFAULT_CHARGER_PHASES)
        voltage: int = cfg.get(CONF_VOLTAGE, DEFAULT_VOLTAGE)

        tesla_current_state = self.hass.states.get(NUMBER_CHARGE_CURRENT)
        tesla_amps_raw = _parse_float(tesla_current_state)
        # Never plan below fast_amps — car entity often stuck at 5 A and collapsed
        # the whole schedule/flatten to a crawl (and false unfeasible targets).
        if tesla_amps_raw and tesla_amps_raw > 0:
            charge_amps = max(int(tesla_amps_raw), int(fast_amps))
        else:
            charge_amps = int(fast_amps)
        max_charge_rate_kW: float = charge_amps * voltage * charger_phases / 1000

        now = datetime.now()
        mode = self._mode

        # Override expiry: deadline passed → fall back to smart
        if mode == MODE_OVERRIDE and self._override_deadline is not None:
            if now >= self._override_deadline:
                self._reset_to_smart("override deadline reached")
                mode = MODE_SMART

        # Target / deadline for current mode
        hard_end: datetime | None = None
        if mode == MODE_OVERRIDE:
            target_soc = self._override_target_soc if self._override_target_soc is not None else (
                self._read_helper_int(INPUT_NUMBER_CUSTOM_TARGET_SOC, default_target_soc)
            )
            if self._override_deadline is not None:
                hard_end = self._override_deadline
                remaining_h = (self._override_deadline - now).total_seconds() / 3600.0
                # Fractional hours for max_energy / price append; never invent a full extra hour
                deadline_hours = max(1 / 60.0, remaining_h)
            else:
                mins = self._read_deadline_minutes()
                hard_end = now + timedelta(minutes=mins)
                deadline_hours = mins / 60.0
            use_price_cap = False
        else:
            target_soc = default_target_soc
            deadline_hours = smart_deadline_hours
            use_price_cap = True

        # External entity states
        soc_state = self.hass.states.get(SENSOR_SOC)
        current_soc = _parse_float(soc_state) or 0.0

        cable_state = self.hass.states.get(BINARY_SENSOR_CABLE)
        cable_connected = cable_state is not None and cable_state.state == "on"

        location_state = self.hass.states.get(DEVICE_TRACKER)
        is_home_actual = location_state is not None and location_state.state == "home"

        override_state = self.hass.states.get(INPUT_BOOLEAN_LOCATION_OVERRIDE)
        location_override_active = override_state is not None and override_state.state == "on"
        is_home = is_home_actual or location_override_active

        price_state = self.hass.states.get(SENSOR_PRICE)
        current_price = _parse_float(price_state) or 0.0

        charging_state = self.hass.states.get(SENSOR_CHARGING)
        is_externally_charging = (
            charging_state is not None
            and charging_state.state.lower() not in _UNAVAILABLE
            and charging_state.state.lower() in ("charging", "on")
        )

        all_prices = _parse_all_prices(price_state)

        # Append tomorrow prices when the window may span overnight
        if deadline_hours > (24 - now.hour) or deadline_hours >= 24:
            tomorrow_str = (now.date() + timedelta(days=1)).strftime("%Y-%m-%d")
            tomorrow_raw = _get_pstryk_tomorrow_prices(self.hass, tomorrow_str)
            for j, entry in enumerate(tomorrow_raw):
                price = float(entry.get("price", 0))
                all_prices.append({"hour": 24 + j, "price": price})

        # Override complete: target reached → back to smart
        if mode == MODE_OVERRIDE and current_soc >= target_soc:
            self._reset_to_smart(f"override target {target_soc}% reached (SoC {current_soc:.1f}%)")
            mode = MODE_SMART
            target_soc = default_target_soc
            deadline_hours = smart_deadline_hours
            use_price_cap = True

        if current_soc >= target_soc:
            self._charging_started = False

        E_needed = max(0.0, (target_soc - current_soc) / 100.0 * battery_kWh)

        sessions: list[dict] = []
        schedule_all_prices_above_max = False
        feasible = True
        max_kwh_window = 0.0
        max_soc = round(current_soc, 1)

        if E_needed > 0:
            max_kwh_window = max_energy_in_window(
                all_prices, max_charge_rate_kW, now, deadline_hours
            )
            max_soc = max_reachable_soc(current_soc, max_kwh_window, battery_kWh)
            feasible = max_soc + 0.05 >= float(target_soc)

            price_cap = max_price_threshold if use_price_cap else None

            uncapped = build_schedule(
                all_prices, E_needed, max_charge_rate_kW, now,
                deadline_hours=deadline_hours, max_price=None,
            )
            capped = build_schedule(
                all_prices, E_needed, max_charge_rate_kW, now,
                deadline_hours=deadline_hours, max_price=price_cap,
            )

            if use_price_cap and not capped and uncapped:
                schedule_all_prices_above_max = True

            # Smart: cheapest hours under price cap.
            # Override: cheapest hours within absolute deadline, no price hard-stop
            # (may wait for a later cheap slot — not forced ASAP).
            plan = uncapped if mode == MODE_OVERRIDE else (
                capped if use_price_cap else uncapped
            )
            # Pass 1: sessions at full rate from the plan slots
            sessions = compute_sessions(plan, now.date())
            # Pass 2: flatten inside selected hours; override never past absolute end
            sessions = flatten_sessions(
                sessions,
                charge_amps,
                now,
                hard_end=hard_end if mode == MODE_OVERRIDE else None,
            )

        reason, should_charge, target_amps = determine_reason(
            mode=mode,
            is_home=is_home,
            cable_connected=cable_connected,
            current_soc=current_soc,
            target_soc=target_soc,
            min_soc=min_soc,
            charge_start_soc=charge_start_soc,
            charge_amps=charge_amps,
            sessions=sessions,
            now_dt=now,
            E_needed=E_needed,
            schedule_all_prices_above_max=schedule_all_prices_above_max,
            is_externally_charging=is_externally_charging,
            charging_started=self._charging_started,
            feasible=feasible,
        )

        if reason == REASON_SCHEDULED:
            self._charging_started = True

        car_in_garage = _car_in_garage(self.hass, is_home_actual)
        cable_needed = should_charge and not cable_connected and car_in_garage
        ns = next_session(sessions, now)

        planned_kwh = round(sum(s.get("total_kWh", 0.0) for s in sessions), 2)
        planned_cost = round(sum(s.get("total_cost", 0.0) for s in sessions), 2)
        planned_minutes = session_duration_minutes(sessions)
        exp_soc = expected_end_soc(current_soc, planned_kwh, battery_kWh, target_soc)

        # If schedule exists but cannot hit target, mark unfeasible for UI
        if E_needed > 0 and planned_kwh + 0.05 < E_needed:
            feasible = False
            if max_soc < float(target_soc):
                max_soc = max_reachable_soc(current_soc, max_kwh_window, battery_kWh)

        shortfall_soc = round(max(0.0, float(target_soc) - max_soc), 1)

        planned_start = None
        planned_end = None
        if sessions:
            active = next((s for s in sessions if s["start"] <= now < s["end"]), None)
            display = active or (ns if ns else sessions[0])
            planned_start = display["start"]
            planned_end = display["end"]

        override_remaining_min = 0
        if mode == MODE_OVERRIDE and self._override_deadline is not None:
            override_remaining_min = max(
                0, int(round((self._override_deadline - now).total_seconds() / 60.0))
            )

        return {
            "mode": self._mode,
            "reason": reason,
            "should_charge": should_charge,
            "target_amps": target_amps,
            "cable_needed": cable_needed,
            "location_override_active": location_override_active,
            "current_price": current_price,
            "current_soc": current_soc,
            "target_soc": target_soc,
            "min_soc": min_soc,
            "max_price_threshold": max_price_threshold,
            "price_cap_active": use_price_cap,
            "deadline_hours": deadline_hours,
            "override_deadline": self._override_deadline,
            "override_remaining_minutes": override_remaining_min,
            "feasible": feasible,
            "max_reachable_soc": max_soc,
            "shortfall_soc": shortfall_soc,
            "E_needed": round(E_needed, 3),
            "sessions": sessions,
            "next_session_start": ns["start"] if ns else None,
            "estimated_total_cost": planned_cost,
            "planned_cost": planned_cost,
            "planned_kwh": planned_kwh,
            "planned_duration_minutes": planned_minutes,
            "planned_session_start": planned_start,
            "planned_session_end": planned_end,
            "expected_end_soc": exp_soc,
            "charge_amps": charge_amps,
            "charge_rate_kw": round(max_charge_rate_kW, 3),
        }
