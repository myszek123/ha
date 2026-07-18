"""Persisted last-week drive summary (filled by charge-log via service)."""
from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN, SIGNAL_WEEKLY_DRIVE_UPDATED, STORAGE_WEEKLY_DRIVE

STORAGE_VERSION = 1


class WeeklyDriveStore:
    """Load/save weekly drive payload for sensors."""

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self._store = Store(hass, STORAGE_VERSION, STORAGE_WEEKLY_DRIVE)
        self.data: dict[str, Any] = {}

    async def async_load(self) -> None:
        raw = await self._store.async_load()
        self.data = dict(raw) if isinstance(raw, dict) else {}

    async def async_save(self, payload: dict[str, Any]) -> None:
        self.data = dict(payload)
        if not self.data.get("updated_at"):
            self.data["updated_at"] = dt_util.utcnow().isoformat()
        await self._store.async_save(self.data)
        async_dispatcher_send(self.hass, SIGNAL_WEEKLY_DRIVE_UPDATED)


def get_weekly_store(hass: HomeAssistant) -> WeeklyDriveStore | None:
    return hass.data.get(DOMAIN, {}).get("weekly_drive_store")


async def async_setup_weekly_drive(hass: HomeAssistant) -> WeeklyDriveStore:
    store = WeeklyDriveStore(hass)
    await store.async_load()
    hass.data.setdefault(DOMAIN, {})["weekly_drive_store"] = store

    async def _handle_set(call: ServiceCall) -> None:
        payload = {k: v for k, v in call.data.items() if v is not None}
        await store.async_save(payload)

    hass.services.async_register(DOMAIN, "set_weekly_drive", _handle_set)
    return store


@callback
def weekly_drive_data(hass: HomeAssistant) -> dict[str, Any]:
    store = get_weekly_store(hass)
    return dict(store.data) if store else {}
