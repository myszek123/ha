"""Sensor entities for Myszolot Charging."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN, MODE_OVERRIDE, SIGNAL_WEEKLY_DRIVE_UPDATED
from .coordinator import MyszolotCoordinator
from .entity import MyszolotEntity
from .weekly_drive import weekly_drive_data


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: MyszolotCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            MyszolotChargeReasonSensor(coordinator, entry),
            MyszolotChargeScheduleSensor(coordinator, entry),
            MyszolotNextSessionSensor(coordinator, entry),
            MyszolotExpectedEndSocSensor(coordinator, entry),
            MyszolotPlannedDurationSensor(coordinator, entry),
            MyszolotOverrideRemainingMinutesSensor(coordinator, entry),
            MyszolotOverrideRemainingSensor(coordinator, entry),
            MyszolotMaxReachableSocSensor(coordinator, entry),
            # Last complete Mon–Sun week (pushed by charge-log)
            MyszolotWeeklyDriveSummarySensor(hass),
            MyszolotWeeklyDriveDistanceSensor(hass),
            MyszolotWeeklyDriveHoursSensor(hass),
            MyszolotWeeklyDriveEnergySensor(hass),
            MyszolotWeeklyDriveMaxSpeedSensor(hass),
        ]
    )


class _MyszolotBaseSensor(MyszolotEntity, SensorEntity):
    def __init__(
        self, coordinator: MyszolotCoordinator, entry: ConfigEntry, unique_id: str, name: str
    ) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = unique_id
        self._attr_name = name

    @property
    def _data(self) -> dict:
        return self.coordinator.data or {}


class MyszolotChargeReasonSensor(_MyszolotBaseSensor):
    """sensor.myszolot_charge_reason — current charging decision."""

    _attr_icon = "mdi:car-electric"

    def __init__(self, coordinator: MyszolotCoordinator, entry: ConfigEntry) -> None:
        super().__init__(
            coordinator, entry, "myszolot_charge_reason", "Myszolot Charge Reason"
        )

    @property
    def state(self) -> str:
        return self._data.get("reason", "unknown")

    @property
    def extra_state_attributes(self) -> dict:
        d = self._data
        ns = d.get("next_session_start")
        ps = d.get("planned_session_start")
        pe = d.get("planned_session_end")
        od = d.get("override_deadline")
        return {
            "should_charge": d.get("should_charge", False),
            "target_amps": d.get("target_amps", 0),
            "charge_amps": d.get("charge_amps", 0),
            "charge_rate_kw": d.get("charge_rate_kw", 0.0),
            "mode": d.get("mode", "smart"),
            "current_price": d.get("current_price", 0.0),
            "current_soc": d.get("current_soc", 0.0),
            "target_soc": d.get("target_soc", 80),
            "default_target_soc": d.get("default_target_soc", 80),
            "min_soc": d.get("min_soc", 30),
            "max_price_threshold": d.get("max_price_threshold", 1.0),
            "price_cap_active": d.get("price_cap_active", True),
            "deadline_hours": d.get("deadline_hours"),
            "feasible": d.get("feasible", True),
            "max_reachable_soc": d.get("max_reachable_soc"),
            "shortfall_soc": d.get("shortfall_soc", 0.0),
            "expected_end_soc": d.get("expected_end_soc"),
            "E_needed": d.get("E_needed", 0.0),
            "planned_kwh": d.get("planned_kwh", 0.0),
            "planned_cost": d.get("planned_cost", 0.0),
            "planned_duration_minutes": d.get("planned_duration_minutes", 0),
            "planned_session_start": ps.isoformat() if ps else None,
            "planned_session_end": pe.isoformat() if pe else None,
            "next_session_start": ns.isoformat() if ns else None,
            "override_deadline": od.isoformat() if od else None,
            "override_remaining_minutes": d.get("override_remaining_minutes", 0),
            "location_override_active": d.get("location_override_active", False),
            "car_limit_replan": d.get("car_limit_replan", True),
            "car_charge_limit": d.get("car_charge_limit"),
        }


class MyszolotChargeScheduleSensor(_MyszolotBaseSensor):
    """sensor.myszolot_charge_schedule — planned session cost (PLN)."""

    _attr_icon = "mdi:cash-clock"
    _attr_native_unit_of_measurement = "PLN"

    def __init__(self, coordinator: MyszolotCoordinator, entry: ConfigEntry) -> None:
        super().__init__(
            coordinator,
            entry,
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
            "max_reachable_soc": d.get("max_reachable_soc"),
            "feasible": d.get("feasible", True),
            "shortfall_soc": d.get("shortfall_soc", 0.0),
            "charge_amps": d.get("charge_amps", 0),
            "charge_rate_kw": d.get("charge_rate_kw", 0.0),
            "current_soc": d.get("current_soc", 0.0),
            "target_soc": d.get("target_soc", 80),
            "deadline_hours": d.get("deadline_hours"),
        }


class MyszolotNextSessionSensor(_MyszolotBaseSensor):
    """sensor.myszolot_next_session_start — timestamp of next scheduled charging session."""

    _attr_icon = "mdi:clock-start"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: MyszolotCoordinator, entry: ConfigEntry) -> None:
        super().__init__(
            coordinator, entry, "myszolot_next_session_start", "Myszolot Next Session Start"
        )

    @property
    def native_value(self) -> datetime | None:
        dt = self._data.get("planned_session_start") or self._data.get("next_session_start")
        if dt is None:
            return None
        return dt_util.as_local(dt)


class MyszolotExpectedEndSocSensor(_MyszolotBaseSensor):
    """sensor.myszolot_expected_end_soc — projected SoC after planned session."""

    _attr_icon = "mdi:battery-charging-80"
    _attr_native_unit_of_measurement = "%"

    def __init__(self, coordinator: MyszolotCoordinator, entry: ConfigEntry) -> None:
        super().__init__(
            coordinator,
            entry,
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

    def __init__(self, coordinator: MyszolotCoordinator, entry: ConfigEntry) -> None:
        super().__init__(
            coordinator,
            entry,
            "myszolot_planned_duration",
            "Myszolot Planned Session Duration",
        )

    @property
    def state(self) -> int:
        return int(self._data.get("planned_duration_minutes", 0) or 0)


class MyszolotMaxReachableSocSensor(_MyszolotBaseSensor):
    """sensor.myszolot_max_reachable_soc — upper-bound SoC within deadline at full rate."""

    _attr_icon = "mdi:battery-alert"
    _attr_native_unit_of_measurement = "%"

    def __init__(self, coordinator: MyszolotCoordinator, entry: ConfigEntry) -> None:
        super().__init__(
            coordinator,
            entry,
            "myszolot_max_reachable_soc",
            "Myszolot Max Reachable SoC",
        )

    @property
    def state(self) -> float | None:
        return self._data.get("max_reachable_soc")


class MyszolotOverrideRemainingMinutesSensor(_MyszolotBaseSensor):
    """sensor.myszolot_override_remaining_minutes — minutes left on override deadline."""

    _attr_icon = "mdi:timer"
    _attr_native_unit_of_measurement = "min"

    def __init__(self, coordinator: MyszolotCoordinator, entry: ConfigEntry) -> None:
        super().__init__(
            coordinator,
            entry,
            "myszolot_override_remaining_minutes",
            "Myszolot Override Remaining Minutes",
        )

    @property
    def state(self) -> int:
        return int(self._data.get("override_remaining_minutes", 0) or 0)


class MyszolotOverrideRemainingSensor(_MyszolotBaseSensor):
    """sensor.myszolot_override_remaining — human-readable override status."""

    _attr_icon = "mdi:timer-outline"

    def __init__(self, coordinator: MyszolotCoordinator, entry: ConfigEntry) -> None:
        super().__init__(
            coordinator,
            entry,
            "myszolot_override_remaining",
            "Myszolot Override Remaining",
        )

    @property
    def state(self) -> str:
        d = self._data
        if d.get("mode") != MODE_OVERRIDE:
            return "Off"
        mins = int(d.get("override_remaining_minutes", 0) or 0)
        if mins <= 0:
            return "Expired"
        hours, rem = divmod(mins, 60)
        if hours:
            return f"{hours}h {rem}m"
        return f"{rem}m"


class _WeeklyDriveSensorBase(SensorEntity):
    """Sensors backed by weekly_drive store (not charging coordinator)."""

    _attr_should_poll = False

    def __init__(self, hass: HomeAssistant, unique_id: str, name: str) -> None:
        self.hass = hass
        self._attr_unique_id = unique_id
        self._attr_name = name
        self._attr_has_entity_name = False

    @property
    def _w(self) -> dict[str, Any]:
        return weekly_drive_data(self.hass)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_WEEKLY_DRIVE_UPDATED, self._handle_update
            )
        )

    @callback
    def _handle_update(self) -> None:
        self.async_write_ha_state()


class MyszolotWeeklyDriveSummarySensor(_WeeklyDriveSensorBase):
    """sensor.myszolot_weekly_drive — last complete week label + full attributes."""

    _attr_icon = "mdi:road-variant"

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(hass, "myszolot_weekly_drive", "Myszolot Weekly Drive")

    @property
    def native_value(self) -> str:
        w = self._w
        if not w.get("week_start"):
            return "unknown"
        dist = w.get("distance_km")
        if dist is not None and dist != "":
            return f"{dist} km"
        return f"{w.get('week_start')} → {w.get('week_end')}"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        w = self._w
        route = ""
        if w.get("max_speed_from") or w.get("max_speed_to"):
            route = f"{w.get('max_speed_from', '')} → {w.get('max_speed_to', '')}"
        at_route = ""
        if w.get("all_time_max_from") or w.get("all_time_max_to"):
            at_route = f"{w.get('all_time_max_from', '')} → {w.get('all_time_max_to', '')}"
        return {
            "week_start": w.get("week_start"),
            "week_end": w.get("week_end"),
            "drives": w.get("drives"),
            "hours": w.get("hours"),
            "distance_km": w.get("distance_km"),
            "energy_kwh": w.get("energy_kwh"),
            "max_speed_kmh": w.get("max_speed_kmh"),
            "max_speed_when": w.get("max_speed_when"),
            "max_speed_driver": w.get("max_speed_driver"),
            "max_speed_route": route or None,
            "max_speed_from": w.get("max_speed_from"),
            "max_speed_to": w.get("max_speed_to"),
            "all_time_max_speed_kmh": w.get("all_time_max_speed_kmh"),
            "all_time_max_when": w.get("all_time_max_when"),
            "all_time_max_driver": w.get("all_time_max_driver"),
            "all_time_max_route": at_route or None,
            "updated_at": w.get("updated_at"),
        }


class MyszolotWeeklyDriveDistanceSensor(_WeeklyDriveSensorBase):
    _attr_icon = "mdi:map-marker-distance"
    _attr_native_unit_of_measurement = "km"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(
            hass, "myszolot_weekly_drive_distance", "Myszolot Weekly Drive Distance"
        )

    @property
    def native_value(self) -> float | None:
        v = self._w.get("distance_km")
        return float(v) if v is not None and v != "" else None


class MyszolotWeeklyDriveHoursSensor(_WeeklyDriveSensorBase):
    _attr_icon = "mdi:clock-outline"
    _attr_native_unit_of_measurement = "h"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(
            hass, "myszolot_weekly_drive_hours", "Myszolot Weekly Drive Hours"
        )

    @property
    def native_value(self) -> float | None:
        v = self._w.get("hours")
        return float(v) if v is not None and v != "" else None


class MyszolotWeeklyDriveEnergySensor(_WeeklyDriveSensorBase):
    _attr_icon = "mdi:lightning-bolt"
    _attr_native_unit_of_measurement = "kWh"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(
            hass, "myszolot_weekly_drive_energy", "Myszolot Weekly Drive Energy"
        )

    @property
    def native_value(self) -> float | None:
        v = self._w.get("energy_kwh")
        return float(v) if v is not None and v != "" else None


class MyszolotWeeklyDriveMaxSpeedSensor(_WeeklyDriveSensorBase):
    _attr_icon = "mdi:speedometer"
    _attr_native_unit_of_measurement = "km/h"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(
            hass, "myszolot_weekly_drive_max_speed", "Myszolot Weekly Drive Max Speed"
        )

    @property
    def native_value(self) -> float | None:
        v = self._w.get("max_speed_kmh")
        return float(v) if v is not None and v != "" else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        w = self._w
        return {
            "when": w.get("max_speed_when"),
            "driver": w.get("max_speed_driver"),
            "from": w.get("max_speed_from"),
            "to": w.get("max_speed_to"),
        }
