"""
Test configuration: mock all homeassistant imports so tests run without HA installed.
Path setup ensures custom_components.myszolot is importable from the repo root.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

# ── Path setup ────────────────────────────────────────────────────────────────
# Insert repo root so `custom_components.myszolot` is importable.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


# ── Stub HA base classes ──────────────────────────────────────────────────────

class _DataUpdateCoordinator:
    def __init__(self, hass=None, logger=None, *, name="", update_interval=None, **kw):
        self.hass = hass
        self.name = name
        self.data = None
        self.config_entry = None

    async def async_config_entry_first_refresh(self):
        pass

    async def async_request_refresh(self):
        pass


class _CoordinatorEntity:
    def __init__(self, coordinator=None):
        self.coordinator = coordinator

    def async_write_ha_state(self):
        pass


class _SelectEntity:
    _attr_options: list = []
    _attr_current_option: str | None = None


class _SensorEntity:
    pass


class _BinarySensorEntity:
    pass


class _HAFlowMeta(type):
    """Metaclass that absorbs keyword arguments like domain= in class definitions."""
    def __new__(mcs, name, bases, namespace, **kwargs):
        return super().__new__(mcs, name, bases, namespace)

    def __init__(cls, name, bases, namespace, **kwargs):
        super().__init__(name, bases, namespace)


class _ConfigFlow(metaclass=_HAFlowMeta):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        pass

    def async_show_form(self, **kw):
        return kw

    def async_create_entry(self, **kw):
        return kw

    async def async_set_unique_id(self, uid):
        pass

    def _abort_if_unique_id_configured(self):
        pass


class _OptionsFlow(metaclass=_HAFlowMeta):
    def __init__(self, config_entry=None):
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None):
        pass

    def async_show_form(self, **kw):
        return kw

    def async_create_entry(self, **kw):
        return kw


# ── Build mock modules ────────────────────────────────────────────────────────
#
# IMPORTANT: `from homeassistant import config_entries` uses getattr() on the
# top-level mock (MagicMock auto-creates child mocks for any attr). To prevent
# that, we explicitly set submodule attributes on every parent mock so that
# getattr() returns our stub rather than an auto-generated MagicMock.

def _make_ha_mocks() -> dict:
    core_mock = MagicMock()
    core_mock.callback = lambda f: f  # pass-through decorator

    update_coordinator_mock = MagicMock()
    update_coordinator_mock.DataUpdateCoordinator = _DataUpdateCoordinator
    update_coordinator_mock.CoordinatorEntity = _CoordinatorEntity
    update_coordinator_mock.UpdateFailed = Exception

    event_mock = MagicMock()
    entity_mock = MagicMock()
    entity_platform_mock = MagicMock()

    helpers_mock = MagicMock()
    helpers_mock.update_coordinator = update_coordinator_mock
    helpers_mock.event = event_mock
    helpers_mock.entity = entity_mock
    helpers_mock.entity_platform = entity_platform_mock

    config_entries_mock = MagicMock()
    config_entries_mock.ConfigFlow = _ConfigFlow
    config_entries_mock.OptionsFlow = _OptionsFlow
    config_entries_mock.ConfigEntry = MagicMock

    select_mock = MagicMock()
    select_mock.SelectEntity = _SelectEntity

    sensor_mock = MagicMock()
    sensor_mock.SensorEntity = _SensorEntity

    binary_sensor_mock = MagicMock()
    binary_sensor_mock.BinarySensorEntity = _BinarySensorEntity

    components_mock = MagicMock()
    components_mock.select = select_mock
    components_mock.sensor = sensor_mock
    components_mock.binary_sensor = binary_sensor_mock

    const_mock = MagicMock()

    # Top-level mock: set submodule attributes explicitly so that
    # `from homeassistant import X` returns our stub, not an auto-child mock.
    ha_mock = MagicMock()
    ha_mock.core = core_mock
    ha_mock.helpers = helpers_mock
    ha_mock.components = components_mock
    ha_mock.config_entries = config_entries_mock
    ha_mock.const = const_mock

    return {
        "homeassistant": ha_mock,
        "homeassistant.core": core_mock,
        "homeassistant.helpers": helpers_mock,
        "homeassistant.helpers.update_coordinator": update_coordinator_mock,
        "homeassistant.helpers.event": event_mock,
        "homeassistant.helpers.entity": entity_mock,
        "homeassistant.helpers.entity_platform": entity_platform_mock,
        "homeassistant.components": components_mock,
        "homeassistant.components.select": select_mock,
        "homeassistant.components.sensor": sensor_mock,
        "homeassistant.components.binary_sensor": binary_sensor_mock,
        "homeassistant.config_entries": config_entries_mock,
        "homeassistant.const": const_mock,
    }


for _name, _mod in _make_ha_mocks().items():
    sys.modules[_name] = _mod
