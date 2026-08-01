"""Binary sensor entities for Myszolot Charging."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import MyszolotCoordinator
from .entity import MyszolotEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: MyszolotCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([MyszolotCableNeededBinarySensor(coordinator, entry)])


class MyszolotCableNeededBinarySensor(MyszolotEntity, BinarySensorEntity):
    """
    binary_sensor.myszolot_cable_needed

    On when: should_charge=True AND cable not connected AND car in garage
    (vision or GPS fallback). Used as trigger for the pre-session cable
    reminder automation.
    """

    _attr_name = "Myszolot Cable Needed"
    _attr_unique_id = "myszolot_cable_needed"
    _attr_icon = "mdi:power-plug-alert"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator: MyszolotCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)

    @property
    def is_on(self) -> bool:
        if self.coordinator.data is None:
            return False
        return bool(self.coordinator.data.get("cable_needed", False))
