"""Select entity: charge mode."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, CHARGE_MODES, MODE_SMART
from .coordinator import MyszolotCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: MyszolotCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([MyszolotChargeModeSelect(coordinator)])


class MyszolotChargeModeSelect(CoordinatorEntity, SelectEntity):
    """select.myszolot_charge_mode — controls which charging mode is active."""

    _attr_options = CHARGE_MODES
    _attr_name = "Myszolot Charge Mode"
    _attr_unique_id = "myszolot_charge_mode"
    _attr_icon = "mdi:ev-station"

    def __init__(self, coordinator: MyszolotCoordinator) -> None:
        super().__init__(coordinator)
        self._coordinator = coordinator

    @property
    def current_option(self) -> str:
        return self._coordinator.mode

    async def async_select_option(self, option: str) -> None:
        self._coordinator.set_mode(option)
        await self._coordinator.async_request_refresh()
