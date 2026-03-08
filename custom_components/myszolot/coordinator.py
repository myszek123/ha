"""Coordinator for Myszolot charging scheduler."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, date as date_type
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.helpers.event import async_track_state_change_event

from .const import (
    DOMAIN,
    SENSOR_PRICE, SENSOR_SOC, BINARY_SENSOR_CABLE, DEVICE_TRACKER, SENSOR_CHARGING,
    MODE_SMART, MODE_NOW_FAST, MODE_NOW_SLOW, MODE_PLAN_TRIP, MODE_TRIP_NOW,
    CONF_CHARGER_PHASES, CONF_VOLTAGE, CONF_FAST_AMPS, CONF_SLOW_AMPS,
    CONF_BATTERY_CAPACITY_KWH, CONF_DEFAULT_TARGET_SOC, CONF_TRIP_TARGET_SOC,
    CONF_MIN_SOC, CONF_CHARGE_START_SOC, CONF_MAX_PRICE_THRESHOLD, CONF_PLAN_TRIP_DEADLINE_HOURS,
    DEFAULT_CHARGER_PHASES, DEFAULT_VOLTAGE, DEFAULT_FAST_AMPS, DEFAULT_SLOW_AMPS,
    DEFAULT_BATTERY_CAPACITY_KWH, DEFAULT_TARGET_SOC, DEFAULT_TRIP_TARGET_SOC,
    DEFAULT_MIN_SOC, DEFAULT_CHARGE_START_SOC, DEFAULT_MAX_PRICE_THRESHOLD,
    DEFAULT_PLAN_TRIP_DEADLINE_HOURS,
    REASON_OUTSIDE_CHARGING, REASON_OUTSIDE_NOT_CHARGING, REASON_TARGET_REACHED,
    REASON_MIN_SOC_FLOOR, REASON_CHARGING_NOW_FAST, REASON_CHARGING_NOW_SLOW,
    REASON_TRIP_CHARGING_NOW, REASON_SOC_SUFFICIENT, REASON_PRICE_TOO_HIGH,
    REASON_SCHEDULED, REASON_WAITING_FOR_SESSION, REASON_NO_ELIGIBLE_HOURS,
    REASON_HOME_NOT_PLUGGED,
)

_LOGGER = logging.getLogger(__name__)

_UNAVAILABLE = {"unknown", "unavailable", "none", ""}


def _get_pstryk_tomorrow_prices(hass: HomeAssistant, tomorrow_str: str) -> list[dict]:
    """
    Extract tomorrow's prices from the pstryk coordinator's internal data.

    The pstryk coordinator stores 48h of prices in coordinator.data["prices"].
    This function returns entries for the given tomorrow_str date (format: "YYYY-MM-DD").

    Returns an empty list if fewer than 20 entries are found (validation that data exists).
    """
    tomorrow_prices = []

    # Search pstryk coordinators in hass.data
    pstryk_data = hass.data.get("pstryk", {})
    for key, coordinator_or_entry in pstryk_data.items():
        # Keys ending in "_buy" are the ones with price data
        if not key.endswith("_buy"):
            continue

        # Try to get the coordinator's data
        try:
            if hasattr(coordinator_or_entry, "data"):
                prices_list = coordinator_or_entry.data.get("prices", [])
            else:
                continue
        except Exception:
            continue

        # Filter for entries matching tomorrow's date
        for entry in prices_list:
            if isinstance(entry, dict):
                start = entry.get("start", "")
                if start.startswith(tomorrow_str):
                    tomorrow_prices.append(entry)

    # Sort by start time
    tomorrow_prices.sort(key=lambda e: e.get("start", ""))

    # Validate: if < 20 entries, it's not real data (should be 24 for a full day)
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


def compute_sessions(schedule: list[dict], ref_date: date_type) -> list[dict]:
    """
    Group adjacent hours into continuous charging sessions.

    A partial first slot in a group is shifted to the tail of that hour so
    charging within the group is uninterrupted.

    Example:
      [{hour:13, minutes:12}, {hour:14, minutes:60}]
      → session start=13:48, end=15:00  (72 min continuous)
    """
    if not schedule:
        return []

    # Group into runs of consecutive hours
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

        # Calculate the actual date for this group (handles hours >= 24)
        day_offset = first["hour"] // 24
        actual_date = ref_date + timedelta(days=day_offset)
        actual_hour = first["hour"] % 24

        # Partial first slot → shift to tail of its hour for uninterrupted charging
        start_minute = (60 - first["minutes"]) if not first["full"] else 0

        start = datetime(actual_date.year, actual_date.month, actual_date.day,
                         actual_hour, start_minute)
        # End = start + total allocated minutes; handles midnight crossings naturally
        total_minutes = sum(s["minutes"] for s in group)
        end = start + timedelta(minutes=total_minutes)

        sessions.append(
            {
                "start": start,
                "end": end,
                "slots": group,
                "total_kWh": round(sum(s["kWh"] for s in group), 4),
                "total_cost": round(sum(s["cost"] for s in group), 4),
            }
        )

    return sessions


def is_in_session(sessions: list[dict], now_dt: datetime) -> bool:
    """Return True if now_dt falls within any charging session."""
    return any(s["start"] <= now_dt < s["end"] for s in sessions)


def next_session(sessions: list[dict], now_dt: datetime) -> dict | None:
    """Return the next upcoming session, or None if none exist."""
    future = [s for s in sessions if s["start"] > now_dt]
    return min(future, key=lambda s: s["start"]) if future else None


def determine_reason(
    mode: str,
    is_home: bool,
    cable_connected: bool,
    current_soc: float,
    target_soc: int,
    min_soc: int,
    charge_start_soc: int,
    fast_amps: int,
    slow_amps: int,
    sessions: list[dict],
    now_dt: datetime,
    E_needed: float,
    schedule_all_prices_above_max: bool,
    is_externally_charging: bool = False,
    charging_started: bool = False,
) -> tuple[str, bool, int]:
    """
    Determine the charging reason, whether to charge, and target amps.

    Returns (reason, should_charge, target_amps).
    Priority order follows the coordinator spec.
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
        return REASON_MIN_SOC_FLOOR, True, fast_amps

    # 4-6. Immediate manual modes (cable check is the actuator's concern)
    if mode == MODE_NOW_FAST:
        return REASON_CHARGING_NOW_FAST, True, fast_amps

    if mode == MODE_NOW_SLOW:
        return REASON_CHARGING_NOW_SLOW, True, slow_amps

    if mode == MODE_TRIP_NOW:
        return REASON_TRIP_CHARGING_NOW, True, fast_amps

    # 7. Scheduled modes
    if mode == MODE_SMART:
        if not charging_started and current_soc > charge_start_soc:
            return REASON_SOC_SUFFICIENT, False, 0
        if schedule_all_prices_above_max:
            return REASON_PRICE_TOO_HIGH, False, 0
        if is_in_session(sessions, now_dt):
            return REASON_SCHEDULED, True, fast_amps
        ns = next_session(sessions, now_dt)
        if ns:
            return REASON_WAITING_FOR_SESSION, False, 0
        if E_needed > 0:
            return REASON_NO_ELIGIBLE_HOURS, False, 0
        return REASON_TARGET_REACHED, False, 0

    if mode == MODE_PLAN_TRIP:
        # No charge_start_soc check — always try to reach trip target
        if schedule_all_prices_above_max:
            return REASON_PRICE_TOO_HIGH, False, 0
        if is_in_session(sessions, now_dt):
            return REASON_SCHEDULED, True, fast_amps
        ns = next_session(sessions, now_dt)
        if ns:
            return REASON_WAITING_FOR_SESSION, False, 0
        if E_needed > 0:
            return REASON_NO_ELIGIBLE_HOURS, False, 0
        return REASON_TARGET_REACHED, False, 0

    # 8. Fallback: home but not plugged in
    return REASON_HOME_NOT_PLUGGED, False, 0


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
        self._unsub_listeners: list = []

    @property
    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> None:
        self._mode = mode

    async def async_setup(self) -> None:
        """Register state-change listeners for all relevant external entities."""
        tracked = [
            SENSOR_SOC,
            BINARY_SENSOR_CABLE,
            DEVICE_TRACKER,
            SENSOR_PRICE,
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
        slow_amps: int = cfg.get(CONF_SLOW_AMPS, DEFAULT_SLOW_AMPS)
        battery_kWh: float = cfg.get(CONF_BATTERY_CAPACITY_KWH, DEFAULT_BATTERY_CAPACITY_KWH)
        default_target_soc: int = cfg.get(CONF_DEFAULT_TARGET_SOC, DEFAULT_TARGET_SOC)
        trip_target_soc: int = cfg.get(CONF_TRIP_TARGET_SOC, DEFAULT_TRIP_TARGET_SOC)
        min_soc: int = cfg.get(CONF_MIN_SOC, DEFAULT_MIN_SOC)
        charge_start_soc: int = cfg.get(CONF_CHARGE_START_SOC, DEFAULT_CHARGE_START_SOC)
        max_price_threshold: float = cfg.get(CONF_MAX_PRICE_THRESHOLD, DEFAULT_MAX_PRICE_THRESHOLD)
        plan_trip_deadline_hours: int = cfg.get(CONF_PLAN_TRIP_DEADLINE_HOURS, DEFAULT_PLAN_TRIP_DEADLINE_HOURS)
        charger_phases: int = cfg.get(CONF_CHARGER_PHASES, DEFAULT_CHARGER_PHASES)
        voltage: int = cfg.get(CONF_VOLTAGE, DEFAULT_VOLTAGE)

        # kW available per hour at fast_amps (max charge rate)
        max_charge_rate_kW: float = fast_amps * voltage * charger_phases / 1000

        mode = self._mode

        # Determine target SoC for current mode
        target_soc = trip_target_soc if mode in (MODE_PLAN_TRIP, MODE_TRIP_NOW) else default_target_soc

        # Read external entity states
        now = datetime.now()

        soc_state = self.hass.states.get(SENSOR_SOC)
        current_soc = _parse_float(soc_state) or 0.0

        cable_state = self.hass.states.get(BINARY_SENSOR_CABLE)
        cable_connected = cable_state is not None and cable_state.state == "on"

        location_state = self.hass.states.get(DEVICE_TRACKER)
        is_home = location_state is not None and location_state.state == "home"

        price_state = self.hass.states.get(SENSOR_PRICE)
        current_price = _parse_float(price_state) or 0.0

        charging_state = self.hass.states.get(SENSOR_CHARGING)
        is_externally_charging = (
            charging_state is not None
            and charging_state.state.lower() not in _UNAVAILABLE
            and charging_state.state.lower() in ("charging", "on")
        )

        all_prices = _parse_all_prices(price_state)

        # Append tomorrow's prices if available and in scheduled modes
        if mode in (MODE_SMART, MODE_PLAN_TRIP):
            tomorrow_str = (now.date() + timedelta(days=1)).strftime("%Y-%m-%d")
            tomorrow_raw = _get_pstryk_tomorrow_prices(self.hass, tomorrow_str)
            for j, entry in enumerate(tomorrow_raw):
                price = float(entry.get("price", 0))
                all_prices.append({"hour": 24 + j, "price": price})

        # Auto-reset non-smart modes when SoC reaches target
        if current_soc >= target_soc and mode != MODE_SMART:
            _LOGGER.info(
                "SoC %.1f%% >= target %d%%; resetting mode from %s to smart",
                current_soc, target_soc, mode,
            )
            self._mode = MODE_SMART
            mode = MODE_SMART
            target_soc = default_target_soc

        # Clear charging_started flag when target is reached
        if current_soc >= target_soc:
            self._charging_started = False

        E_needed = max(0.0, (target_soc - current_soc) / 100.0 * battery_kWh)

        # Build schedule for scheduled modes
        sessions: list[dict] = []
        schedule_all_prices_above_max = False

        if mode in (MODE_SMART, MODE_PLAN_TRIP) and E_needed > 0:
            deadline_hours = plan_trip_deadline_hours if mode == MODE_PLAN_TRIP else 48

            # Check if hours exist at all (without price cap) vs with price cap
            uncapped = build_schedule(
                all_prices, E_needed, max_charge_rate_kW, now,
                deadline_hours=deadline_hours, max_price=None,
            )
            capped = build_schedule(
                all_prices, E_needed, max_charge_rate_kW, now,
                deadline_hours=deadline_hours, max_price=max_price_threshold,
            )

            if not capped and uncapped:
                # Eligible hours exist but all exceed the price threshold
                schedule_all_prices_above_max = True

            sessions = compute_sessions(capped, now.date())

        reason, should_charge, target_amps = determine_reason(
            mode=mode,
            is_home=is_home,
            cable_connected=cable_connected,
            current_soc=current_soc,
            target_soc=target_soc,
            min_soc=min_soc,
            charge_start_soc=charge_start_soc,
            fast_amps=fast_amps,
            slow_amps=slow_amps,
            sessions=sessions,
            now_dt=now,
            E_needed=E_needed,
            schedule_all_prices_above_max=schedule_all_prices_above_max,
            is_externally_charging=is_externally_charging,
            charging_started=self._charging_started,
        )

        # Set flag when a scheduled session starts
        if reason == REASON_SCHEDULED:
            self._charging_started = True

        cable_needed = should_charge and not cable_connected and is_home
        ns = next_session(sessions, now)

        return {
            "mode": mode,
            "reason": reason,
            "should_charge": should_charge,
            "target_amps": target_amps,
            "cable_needed": cable_needed,
            "current_price": current_price,
            "current_soc": current_soc,
            "target_soc": target_soc,
            "E_needed": round(E_needed, 3),
            "sessions": sessions,
            "next_session_start": ns["start"] if ns else None,
            "estimated_total_cost": round(sum(s["total_cost"] for s in sessions), 4),
        }
