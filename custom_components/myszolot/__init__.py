"""Myszolot Charging — custom HA integration."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import (
    DOMAIN,
    INPUT_BOOLEAN_LOCATION_OVERRIDE,
    INPUT_NUMBER_CUSTOM_TARGET_SOC,
    INPUT_NUMBER_TIMED_START_HOUR,
    INPUT_NUMBER_TIMED_DURATION_MINUTES,
)
from .coordinator import MyszolotCoordinator

_OPTIONAL_HELPERS = {
    INPUT_BOOLEAN_LOCATION_OVERRIDE: "Toggle (on/off) — enables location override feature",
    INPUT_NUMBER_CUSTOM_TARGET_SOC: "Number (50–100, step 1) — enables custom target SoC modes",
    INPUT_NUMBER_TIMED_START_HOUR: "Number (0–23, step 1) — start hour for timed mode (default 2)",
    INPUT_NUMBER_TIMED_DURATION_MINUTES: "Number (15–480, step 15) — duration minutes for timed mode (default 180)",
}

PLATFORMS = ["select", "sensor", "binary_sensor"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = MyszolotCoordinator(hass, entry)
    await coordinator.async_setup()
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    missing = [
        f"- `{eid}`: {desc}"
        for eid, desc in _OPTIONAL_HELPERS.items()
        if hass.states.get(eid) is None
    ]
    if missing:
        hass.components.persistent_notification.async_create(
            "Optional helpers not found — some features are disabled until you create them "
            "in **Settings → Devices & Services → Helpers**:\n\n" + "\n".join(missing),
            title="Myszolot: optional helpers missing",
            notification_id="myszolot_optional_helpers",
        )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator: MyszolotCoordinator = hass.data[DOMAIN][entry.entry_id]
    await coordinator.async_unload()

    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unloaded


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)
