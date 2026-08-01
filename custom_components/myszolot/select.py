"""Select entity: charge mode."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, CHARGE_MODES, MODE_SMART
from .coordinator import MyszolotCoordinator
from .entity import MyszolotEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: MyszolotCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([MyszolotChargeModeSelect(coordinator, entry)])


class MyszolotChargeModeSelect(MyszolotEntity, SelectEntity):
    """select.myszolot_charge_mode — controls which charging mode is active."""

    _attr_options = CHARGE_MODES
    _attr_name = "Myszolot Charge Mode"
    _attr_unique_id = "myszolot_charge_mode"
    _attr_icon = "mdi:ev-station"

    def __init__(self, coordinator: MyszolotCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._coordinator = coordinator

    @property
    def current_option(self) -> str:
        return self._coordinator.mode

    async def async_select_option(self, option: str) -> None:
        self._coordinator.set_mode(option)
        # Force an immediate recompute so sensors/plan match the new mode
        # (request_refresh can debounce and leave stale smart-mode data).
        await self._coordinator.async_refresh()
        self.async_write_ha_state()
