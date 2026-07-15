"""Sensor entities for Myszolot Charging."""
from __future__ import annotations

from datetime import datetime

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import MyszolotCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: MyszolotCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            MyszolotChargeReasonSensor(coordinator),
            MyszolotChargeScheduleSensor(coordinator),
            MyszolotNextSessionSensor(coordinator),
            MyszolotExpectedEndSocSensor(coordinator),
            MyszolotPlannedDurationSensor(coordinator),
            MyszolotOverrideRemainingMinutesSensor(coordinator),
            MyszolotOverrideRemainingSensor(coordinator),
        ]
    )


class _MyszolotBaseSensor(CoordinatorEntity, SensorEntity):
    def __init__(self, coordinator: MyszolotCoordinator, unique_id: str, name: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = unique_id
        self._attr_name = name

    @property
    def _data(self) -> dict:
        return self.coordinator.data or {}


class MyszolotChargeReasonSensor(_MyszolotBaseSensor):
    """sensor.myszolot_charge_reason — current charging decision."""

    _attr_icon = "mdi:car-electric"

    def __init__(self, coordinator: MyszolotCoordinator) -> None:
        super().__init__(coordinator, "myszolot_charge_reason", "Myszolot Charge Reason")

    @property
    def state(self) -> str:
        return self._data.get("reason", "unknown")

    @property
    def extra_state_attributes(self) -> dict:
        d = self._data
        ns = d.get("next_session_start")
        ps = d.get("planned_session_start")
        pe = d.get("planned_session_end")
        return {
            "should_charge": d.get("should_charge", False),
            "target_amps": d.get("target_amps", 0),
            "charge_amps": d.get("charge_amps", 0),
            "charge_rate_kw": d.get("charge_rate_kw", 0.0),
            "mode": d.get("mode", "smart"),
            "current_price": d.get("current_price", 0.0),
            "current_soc": d.get("current_soc", 0.0),
            "target_soc": d.get("target_soc", 80),
            "expected_end_soc": d.get("expected_end_soc"),
            "E_needed": d.get("E_needed", 0.0),
            "planned_kwh": d.get("planned_kwh", 0.0),
            "planned_cost": d.get("planned_cost", 0.0),
            "planned_duration_minutes": d.get("planned_duration_minutes", 0),
            "planned_session_start": ps.isoformat() if ps else None,
            "planned_session_end": pe.isoformat() if pe else None,
            "next_session_start": ns.isoformat() if ns else None,
            "location_override_active": d.get("location_override_active", False),
        }


class MyszolotChargeScheduleSensor(_MyszolotBaseSensor):
    """sensor.myszolot_charge_schedule — planned session cost (PLN)."""

    _attr_icon = "mdi:cash-clock"
    _attr_native_unit_of_measurement = "PLN"

    def __init__(self, coordinator: MyszolotCoordinator) -> None:
        super().__init__(
            coordinator,
            "myszolot_charge_schedule",
            "Myszolot Planned Session Cost",
        )

    @property
    def state(self) -> float:
        return self._data.get("planned_cost", self._data.get("estimated_total_cost", 0.0))

    @property
    def extra_state_attributes(self) -> dict:
        d = self._data
        sessions = d.get("sessions", [])
        serialised = [
            {
                "start": s["start"].isoformat(),
                "end": s["end"].isoformat(),
                "total_kWh": s["total_kWh"],
                "total_cost": s["total_cost"],
                "duration_minutes": s.get(
                    "duration_minutes",
                    int(round((s["end"] - s["start"]).total_seconds() / 60)),
                ),
            }
            for s in sessions
        ]
        ps = d.get("planned_session_start")
        pe = d.get("planned_session_end")
        return {
            "sessions": serialised,
            "E_needed": d.get("E_needed", 0.0),
            "planned_cost": d.get("planned_cost", 0.0),
            "estimated_total_cost": d.get("estimated_total_cost", 0.0),
            "planned_kwh": d.get("planned_kwh", 0.0),
            "planned_duration_minutes": d.get("planned_duration_minutes", 0),
            "planned_session_start": ps.isoformat() if ps else None,
            "planned_session_end": pe.isoformat() if pe else None,
            "expected_end_soc": d.get("expected_end_soc"),
            "charge_amps": d.get("charge_amps", 0),
            "charge_rate_kw": d.get("charge_rate_kw", 0.0),
            "current_soc": d.get("current_soc", 0.0),
            "target_soc": d.get("target_soc", 80),
        }


class MyszolotNextSessionSensor(_MyszolotBaseSensor):
    """sensor.myszolot_next_session_start — timestamp of next scheduled charging session."""

    _attr_icon = "mdi:clock-start"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: MyszolotCoordinator) -> None:
        super().__init__(coordinator, "myszolot_next_session_start", "Myszolot Next Session Start")

    @property
    def native_value(self) -> datetime | None:
        # Prefer planned start (covers active window) then next future start
        dt = self._data.get("planned_session_start") or self._data.get("next_session_start")
        if dt is None:
            return None
        return dt_util.as_local(dt)


class MyszolotExpectedEndSocSensor(_MyszolotBaseSensor):
    """sensor.myszolot_expected_end_soc — projected SoC after planned session."""

    _attr_icon = "mdi:battery-charging-80"
    _attr_native_unit_of_measurement = "%"

    def __init__(self, coordinator: MyszolotCoordinator) -> None:
        super().__init__(
            coordinator,
            "myszolot_expected_end_soc",
            "Myszolot Expected End SoC",
        )

    @property
    def state(self) -> float | None:
        return self._data.get("expected_end_soc")


class MyszolotPlannedDurationSensor(_MyszolotBaseSensor):
    """sensor.myszolot_planned_duration — planned session length in minutes."""

    _attr_icon = "mdi:timer-outline"
    _attr_native_unit_of_measurement = "min"

    def __init__(self, coordinator: MyszolotCoordinator) -> None:
        super().__init__(
            coordinator,
            "myszolot_planned_duration",
            "Myszolot Planned Session Duration",
        )

    @property
    def state(self) -> int:
        return int(self._data.get("planned_duration_minutes", 0) or 0)


class MyszolotOverrideRemainingMinutesSensor(_MyszolotBaseSensor):
    """sensor.myszolot_override_remaining_minutes — always 0 (no timeout feature)."""

    _attr_icon = "mdi:timer"
    _attr_native_unit_of_measurement = "min"

    def __init__(self, coordinator: MyszolotCoordinator) -> None:
        super().__init__(
            coordinator,
            "myszolot_override_remaining_minutes",
            "Myszolot Override Remaining Minutes",
        )

    @property
    def state(self) -> int:
        # Non-smart modes have no time-based expiry in this integration.
        return 0


class MyszolotOverrideRemainingSensor(_MyszolotBaseSensor):
    """sensor.myszolot_override_remaining — always 'Off' (no timeout feature)."""

    _attr_icon = "mdi:timer-off"

    def __init__(self, coordinator: MyszolotCoordinator) -> None:
        super().__init__(
            coordinator,
            "myszolot_override_remaining",
            "Myszolot Override Remaining",
        )

    @property
    def state(self) -> str:
        return "Off"
