"""Myszolot Charging — custom HA integration."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import (
    DOMAIN,
    INPUT_BOOLEAN_LOCATION_OVERRIDE,
    INPUT_NUMBER_CUSTOM_TARGET_SOC,
    INPUT_NUMBER_DEADLINE_HOURS,
    INPUT_NUMBER_DEADLINE_MINUTES,
    INPUT_NUMBER_MIN_SOC,
    INPUT_NUMBER_MAX_PRICE_THRESHOLD,
)
from .coordinator import MyszolotCoordinator
from .weekly_drive import async_setup_weekly_drive

_OPTIONAL_HELPERS = {
    INPUT_BOOLEAN_LOCATION_OVERRIDE: "Toggle (on/off) — enables location override feature",
    INPUT_NUMBER_CUSTOM_TARGET_SOC: "Number (50–100, step 1) — override target SoC %",
    INPUT_NUMBER_DEADLINE_MINUTES: (
        "Number (1–2880, step 1) — override window in minutes (preferred; default 120)"
    ),
    INPUT_NUMBER_DEADLINE_HOURS: (
        "Number (1–48) — legacy override window in hours (used only if minutes helper missing)"
    ),
    INPUT_NUMBER_MIN_SOC: "Number (0–100, step 1) — emergency min SoC floor (default 30); not on dashboard",
    INPUT_NUMBER_MAX_PRICE_THRESHOLD: (
        "Number (PLN/kWh, step 0.05) — smart-mode max price hard-stop (default 1.0); "
        "ignored in override; not on dashboard"
    ),
}

PLATFORMS = ["select", "sensor", "binary_sensor"]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Domain-level setup: weekly drive store + service (used by charge-log)."""
    hass.data.setdefault(DOMAIN, {})
    if "weekly_drive_store" not in hass.data[DOMAIN]:
        await async_setup_weekly_drive(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})
    if "weekly_drive_store" not in hass.data[DOMAIN]:
        await async_setup_weekly_drive(hass)

    coordinator = MyszolotCoordinator(hass, entry)
    await coordinator.async_setup()
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))

    missing = [
        f"- `{eid}`: {desc}"
        for eid, desc in _OPTIONAL_HELPERS.items()
        if hass.states.get(eid) is None
    ]
    if missing:
        from homeassistant.components.persistent_notification import (
            async_create as async_create_notification,
        )

        async_create_notification(
            hass,
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
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload entry when options change."""
    await hass.config_entries.async_reload(entry.entry_id)
