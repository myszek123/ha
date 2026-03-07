"""Tests for config flow schema validation."""
from __future__ import annotations

import pytest
import voluptuous as vol

from custom_components.myszolot.config_flow import CONFIG_SCHEMA
from custom_components.myszolot.const import (
    DEFAULT_CHARGER_PHASES, DEFAULT_VOLTAGE, DEFAULT_FAST_AMPS, DEFAULT_SLOW_AMPS,
    DEFAULT_BATTERY_CAPACITY_KWH, DEFAULT_TARGET_SOC, DEFAULT_TRIP_TARGET_SOC,
    DEFAULT_MIN_SOC, DEFAULT_CHARGE_START_SOC, DEFAULT_MAX_PRICE_THRESHOLD,
    DEFAULT_PLAN_TRIP_DEADLINE_HOURS,
    CONF_CHARGER_PHASES, CONF_VOLTAGE, CONF_FAST_AMPS, CONF_SLOW_AMPS,
    CONF_BATTERY_CAPACITY_KWH, CONF_DEFAULT_TARGET_SOC, CONF_TRIP_TARGET_SOC,
    CONF_MIN_SOC, CONF_CHARGE_START_SOC, CONF_MAX_PRICE_THRESHOLD,
    CONF_PLAN_TRIP_DEADLINE_HOURS,
)

VALID_INPUT = {
    CONF_CHARGER_PHASES: 3,
    CONF_VOLTAGE: 230,
    CONF_FAST_AMPS: 10,
    CONF_SLOW_AMPS: 5,
    CONF_BATTERY_CAPACITY_KWH: 68.9,
    CONF_DEFAULT_TARGET_SOC: 80,
    CONF_TRIP_TARGET_SOC: 95,
    CONF_MIN_SOC: 30,
    CONF_CHARGE_START_SOC: 69,
    CONF_MAX_PRICE_THRESHOLD: 1.0,
    CONF_PLAN_TRIP_DEADLINE_HOURS: 8,
}


# ── Valid inputs ──────────────────────────────────────────────────────────────

def test_schema_valid_full_input():
    result = CONFIG_SCHEMA(VALID_INPUT)
    assert result[CONF_CHARGER_PHASES] == 3
    assert result[CONF_VOLTAGE] == 230
    assert result[CONF_FAST_AMPS] == 10
    assert result[CONF_SLOW_AMPS] == 5
    assert result[CONF_BATTERY_CAPACITY_KWH] == pytest.approx(68.9)
    assert result[CONF_DEFAULT_TARGET_SOC] == 80
    assert result[CONF_TRIP_TARGET_SOC] == 95
    assert result[CONF_MIN_SOC] == 30
    assert result[CONF_CHARGE_START_SOC] == 69
    assert result[CONF_MAX_PRICE_THRESHOLD] == pytest.approx(1.0)
    assert result[CONF_PLAN_TRIP_DEADLINE_HOURS] == 8


def test_schema_valid_1_phase():
    result = CONFIG_SCHEMA({**VALID_INPUT, CONF_CHARGER_PHASES: 1})
    assert result[CONF_CHARGER_PHASES] == 1


def test_schema_default_values():
    """Schema with only defaults (no user input) should still pass via defaults."""
    # Note: vol.Required with default fills in the value if key is missing.
    result = CONFIG_SCHEMA({})
    assert result[CONF_CHARGER_PHASES] == DEFAULT_CHARGER_PHASES
    assert result[CONF_VOLTAGE] == DEFAULT_VOLTAGE
    assert result[CONF_FAST_AMPS] == DEFAULT_FAST_AMPS
    assert result[CONF_SLOW_AMPS] == DEFAULT_SLOW_AMPS
    assert result[CONF_BATTERY_CAPACITY_KWH] == pytest.approx(DEFAULT_BATTERY_CAPACITY_KWH)
    assert result[CONF_DEFAULT_TARGET_SOC] == DEFAULT_TARGET_SOC
    assert result[CONF_TRIP_TARGET_SOC] == DEFAULT_TRIP_TARGET_SOC
    assert result[CONF_MIN_SOC] == DEFAULT_MIN_SOC
    assert result[CONF_CHARGE_START_SOC] == DEFAULT_CHARGE_START_SOC
    assert result[CONF_MAX_PRICE_THRESHOLD] == pytest.approx(DEFAULT_MAX_PRICE_THRESHOLD)
    assert result[CONF_PLAN_TRIP_DEADLINE_HOURS] == DEFAULT_PLAN_TRIP_DEADLINE_HOURS


# ── Invalid inputs ────────────────────────────────────────────────────────────

def test_schema_invalid_charger_phases():
    with pytest.raises(vol.Invalid):
        CONFIG_SCHEMA({**VALID_INPUT, CONF_CHARGER_PHASES: 2})


def test_schema_invalid_voltage_too_low():
    with pytest.raises(vol.Invalid):
        CONFIG_SCHEMA({**VALID_INPUT, CONF_VOLTAGE: 50})


def test_schema_invalid_voltage_too_high():
    with pytest.raises(vol.Invalid):
        CONFIG_SCHEMA({**VALID_INPUT, CONF_VOLTAGE: 500})


def test_schema_invalid_fast_amps_zero():
    with pytest.raises(vol.Invalid):
        CONFIG_SCHEMA({**VALID_INPUT, CONF_FAST_AMPS: 0})


def test_schema_invalid_fast_amps_too_high():
    with pytest.raises(vol.Invalid):
        CONFIG_SCHEMA({**VALID_INPUT, CONF_FAST_AMPS: 33})


def test_schema_invalid_soc_out_of_range():
    with pytest.raises(vol.Invalid):
        CONFIG_SCHEMA({**VALID_INPUT, CONF_DEFAULT_TARGET_SOC: 101})
    with pytest.raises(vol.Invalid):
        CONFIG_SCHEMA({**VALID_INPUT, CONF_DEFAULT_TARGET_SOC: 0})


def test_schema_invalid_deadline_hours_zero():
    with pytest.raises(vol.Invalid):
        CONFIG_SCHEMA({**VALID_INPUT, CONF_PLAN_TRIP_DEADLINE_HOURS: 0})


def test_schema_invalid_deadline_hours_too_high():
    with pytest.raises(vol.Invalid):
        CONFIG_SCHEMA({**VALID_INPUT, CONF_PLAN_TRIP_DEADLINE_HOURS: 73})


# ── Options flow schema (same schema reused) ──────────────────────────────────

def test_options_flow_schema_updates_values():
    """Options flow uses the same schema; verify updated values pass through."""
    updated = {**VALID_INPUT, CONF_FAST_AMPS: 16, CONF_DEFAULT_TARGET_SOC: 90}
    result = CONFIG_SCHEMA(updated)
    assert result[CONF_FAST_AMPS] == 16
    assert result[CONF_DEFAULT_TARGET_SOC] == 90


# ── Config flow class instantiation ──────────────────────────────────────────

def test_config_flow_instantiates():
    from custom_components.myszolot.config_flow import MyszolotConfigFlow
    flow = MyszolotConfigFlow()
    assert flow.VERSION == 1


def test_options_flow_instantiates():
    from custom_components.myszolot.config_flow import MyszolotOptionsFlow
    from unittest.mock import MagicMock
    mock_entry = MagicMock()
    mock_entry.data = VALID_INPUT
    mock_entry.options = {}
    flow = MyszolotOptionsFlow(mock_entry)
    assert flow.config_entry is mock_entry
