"""Shared entity base for Myszolot (device grouping)."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import MyszolotCoordinator


class MyszolotEntity(CoordinatorEntity[MyszolotCoordinator]):
    """Coordinator entity attached to a single Myszolot device."""

    _attr_has_entity_name = False  # keep existing friendly names stable

    def __init__(self, coordinator: MyszolotCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Myszolot Charging",
            manufacturer="Myszolot",
            model="EV charge scheduler",
        )
