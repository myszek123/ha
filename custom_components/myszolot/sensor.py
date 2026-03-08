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
        return {
            "should_charge": d.get("should_charge", False),
            "target_amps": d.get("target_amps", 0),
            "mode": d.get("mode", "smart"),
            "current_price": d.get("current_price", 0.0),
            "current_soc": d.get("current_soc", 0.0),
            "target_soc": d.get("target_soc", 80),
            "E_needed": d.get("E_needed", 0.0),
            "next_session_start": ns.isoformat() if ns else None,
        }


class MyszolotChargeScheduleSensor(_MyszolotBaseSensor):
    """sensor.myszolot_charge_schedule — estimated remaining cost and sessions."""

    _attr_icon = "mdi:calendar-clock"
    _attr_native_unit_of_measurement = "PLN"

    def __init__(self, coordinator: MyszolotCoordinator) -> None:
        super().__init__(coordinator, "myszolot_charge_schedule", "Myszolot Charge Schedule")

    @property
    def state(self) -> float:
        return self._data.get("estimated_total_cost", 0.0)

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
            }
            for s in sessions
        ]
        return {
            "sessions": serialised,
            "E_needed": d.get("E_needed", 0.0),
            "estimated_total_cost": d.get("estimated_total_cost", 0.0),
        }


class MyszolotNextSessionSensor(_MyszolotBaseSensor):
    """sensor.myszolot_next_session_start — timestamp of next scheduled charging session."""

    _attr_icon = "mdi:clock-start"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: MyszolotCoordinator) -> None:
        super().__init__(coordinator, "myszolot_next_session_start", "Myszolot Next Session Start")

    @property
    def native_value(self) -> datetime | None:
        dt = self._data.get("next_session_start")
        if dt is None:
            return None
        return dt_util.as_local(dt)


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
