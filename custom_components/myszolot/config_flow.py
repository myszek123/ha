"""Config flow for Myszolot Charging integration."""
from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback

from .const import (
    DOMAIN,
    CONF_CHARGER_PHASES, CONF_VOLTAGE, CONF_FAST_AMPS, CONF_SLOW_AMPS,
    CONF_BATTERY_CAPACITY_KWH, CONF_DEFAULT_TARGET_SOC, CONF_TRIP_TARGET_SOC,
    CONF_MIN_SOC, CONF_CHARGE_START_SOC, CONF_MAX_PRICE_THRESHOLD, CONF_PLAN_TRIP_DEADLINE_HOURS,
    DEFAULT_CHARGER_PHASES, DEFAULT_VOLTAGE, DEFAULT_FAST_AMPS, DEFAULT_SLOW_AMPS,
    DEFAULT_BATTERY_CAPACITY_KWH, DEFAULT_TARGET_SOC, DEFAULT_TRIP_TARGET_SOC,
    DEFAULT_MIN_SOC, DEFAULT_CHARGE_START_SOC, DEFAULT_MAX_PRICE_THRESHOLD,
    DEFAULT_PLAN_TRIP_DEADLINE_HOURS,
)

CONFIG_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_CHARGER_PHASES, default=DEFAULT_CHARGER_PHASES): vol.In([1, 3]),
        vol.Required(CONF_VOLTAGE, default=DEFAULT_VOLTAGE): vol.All(
            vol.Coerce(int), vol.Range(min=100, max=400)
        ),
        vol.Required(CONF_FAST_AMPS, default=DEFAULT_FAST_AMPS): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=32)
        ),
        vol.Required(CONF_SLOW_AMPS, default=DEFAULT_SLOW_AMPS): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=32)
        ),
        vol.Required(CONF_BATTERY_CAPACITY_KWH, default=DEFAULT_BATTERY_CAPACITY_KWH): vol.Coerce(
            float
        ),
        vol.Required(CONF_DEFAULT_TARGET_SOC, default=DEFAULT_TARGET_SOC): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=100)
        ),
        vol.Required(CONF_TRIP_TARGET_SOC, default=DEFAULT_TRIP_TARGET_SOC): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=100)
        ),
        vol.Required(CONF_MIN_SOC, default=DEFAULT_MIN_SOC): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=100)
        ),
        vol.Required(CONF_CHARGE_START_SOC, default=DEFAULT_CHARGE_START_SOC): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=100)
        ),
        vol.Required(CONF_MAX_PRICE_THRESHOLD, default=DEFAULT_MAX_PRICE_THRESHOLD): vol.Coerce(
            float
        ),
        vol.Required(CONF_PLAN_TRIP_DEADLINE_HOURS, default=DEFAULT_PLAN_TRIP_DEADLINE_HOURS): vol.All(
            vol.Coerce(int), vol.Range(min=1, max=72)
        ),
    }
)


class MyszolotConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle the initial configuration flow."""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                data = CONFIG_SCHEMA(user_input)
            except vol.Invalid as exc:
                errors["base"] = str(exc)
            else:
                # Prevent duplicate entries
                await self.async_set_unique_id(DOMAIN)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title="Myszolot Charging", data=data)

        return self.async_show_form(
            step_id="user",
            data_schema=CONFIG_SCHEMA,
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return MyszolotOptionsFlow(config_entry)


class MyszolotOptionsFlow(config_entries.OptionsFlow):
    """Handle options (reconfigure) flow."""

    def __init__(self, config_entry) -> None:
        self.config_entry = config_entry

    async def async_step_init(self, user_input=None):
        errors = {}
        current = {**self.config_entry.data, **(self.config_entry.options or {})}

        if user_input is not None:
            try:
                options = CONFIG_SCHEMA(user_input)
            except vol.Invalid as exc:
                errors["base"] = str(exc)
            else:
                return self.async_create_entry(title="", data=options)

        # Pre-fill form with current values
        schema_with_defaults = vol.Schema(
            {
                vol.Required(CONF_CHARGER_PHASES, default=current.get(CONF_CHARGER_PHASES, DEFAULT_CHARGER_PHASES)): vol.In([1, 3]),
                vol.Required(CONF_VOLTAGE, default=current.get(CONF_VOLTAGE, DEFAULT_VOLTAGE)): vol.All(vol.Coerce(int), vol.Range(min=100, max=400)),
                vol.Required(CONF_FAST_AMPS, default=current.get(CONF_FAST_AMPS, DEFAULT_FAST_AMPS)): vol.All(vol.Coerce(int), vol.Range(min=1, max=32)),
                vol.Required(CONF_SLOW_AMPS, default=current.get(CONF_SLOW_AMPS, DEFAULT_SLOW_AMPS)): vol.All(vol.Coerce(int), vol.Range(min=1, max=32)),
                vol.Required(CONF_BATTERY_CAPACITY_KWH, default=current.get(CONF_BATTERY_CAPACITY_KWH, DEFAULT_BATTERY_CAPACITY_KWH)): vol.Coerce(float),
                vol.Required(CONF_DEFAULT_TARGET_SOC, default=current.get(CONF_DEFAULT_TARGET_SOC, DEFAULT_TARGET_SOC)): vol.All(vol.Coerce(int), vol.Range(min=1, max=100)),
                vol.Required(CONF_TRIP_TARGET_SOC, default=current.get(CONF_TRIP_TARGET_SOC, DEFAULT_TRIP_TARGET_SOC)): vol.All(vol.Coerce(int), vol.Range(min=1, max=100)),
                vol.Required(CONF_MIN_SOC, default=current.get(CONF_MIN_SOC, DEFAULT_MIN_SOC)): vol.All(vol.Coerce(int), vol.Range(min=0, max=100)),
                vol.Required(CONF_CHARGE_START_SOC, default=current.get(CONF_CHARGE_START_SOC, DEFAULT_CHARGE_START_SOC)): vol.All(vol.Coerce(int), vol.Range(min=0, max=100)),
                vol.Required(CONF_MAX_PRICE_THRESHOLD, default=current.get(CONF_MAX_PRICE_THRESHOLD, DEFAULT_MAX_PRICE_THRESHOLD)): vol.Coerce(float),
                vol.Required(CONF_PLAN_TRIP_DEADLINE_HOURS, default=current.get(CONF_PLAN_TRIP_DEADLINE_HOURS, DEFAULT_PLAN_TRIP_DEADLINE_HOURS)): vol.All(vol.Coerce(int), vol.Range(min=1, max=72)),
            }
        )

        return self.async_show_form(
            step_id="init",
            data_schema=schema_with_defaults,
            errors=errors,
        )
