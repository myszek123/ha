"""Tests for config flow schema validation."""
from __future__ import annotations

import pytest
import voluptuous as vol

from custom_components.myszolot.config_flow import CONFIG_SCHEMA
from custom_components.myszolot.const import (
    DEFAULT_CHARGER_PHASES, DEFAULT_VOLTAGE, DEFAULT_FAST_AMPS,
    DEFAULT_BATTERY_CAPACITY_KWH, DEFAULT_TARGET_SOC,
    DEFAULT_MIN_SOC, DEFAULT_CHARGE_START_SOC, DEFAULT_MAX_PRICE_THRESHOLD,
    DEFAULT_SMART_DEADLINE_HOURS, DEFAULT_CAR_LIMIT_REPLAN,
    CONF_CHARGER_PHASES, CONF_VOLTAGE, CONF_FAST_AMPS,
    CONF_BATTERY_CAPACITY_KWH, CONF_DEFAULT_TARGET_SOC,
    CONF_MIN_SOC, CONF_CHARGE_START_SOC, CONF_MAX_PRICE_THRESHOLD,
    CONF_SMART_DEADLINE_HOURS, CONF_CAR_LIMIT_REPLAN,
)

VALID_INPUT = {
    CONF_CHARGER_PHASES: 3,
    CONF_VOLTAGE: 230,
    CONF_FAST_AMPS: 12,
    CONF_BATTERY_CAPACITY_KWH: 68.9,
    CONF_DEFAULT_TARGET_SOC: 80,
    CONF_MIN_SOC: 30,
    CONF_CHARGE_START_SOC: 69,
    CONF_MAX_PRICE_THRESHOLD: 1.0,
    CONF_SMART_DEADLINE_HOURS: 48,
    CONF_CAR_LIMIT_REPLAN: True,
}


def test_schema_valid_full_input():
    result = CONFIG_SCHEMA(VALID_INPUT)
    assert result[CONF_CHARGER_PHASES] == 3
    assert result[CONF_VOLTAGE] == 230
    assert result[CONF_FAST_AMPS] == 12
    assert result[CONF_BATTERY_CAPACITY_KWH] == pytest.approx(68.9)
    assert result[CONF_DEFAULT_TARGET_SOC] == 80
    assert result[CONF_MIN_SOC] == 30
    assert result[CONF_CHARGE_START_SOC] == 69
    assert result[CONF_MAX_PRICE_THRESHOLD] == pytest.approx(1.0)
    assert result[CONF_SMART_DEADLINE_HOURS] == 48
    assert result[CONF_CAR_LIMIT_REPLAN] is True


def test_schema_valid_1_phase():
    result = CONFIG_SCHEMA({**VALID_INPUT, CONF_CHARGER_PHASES: 1})
    assert result[CONF_CHARGER_PHASES] == 1


def test_schema_default_values():
    result = CONFIG_SCHEMA({})
    assert result[CONF_CHARGER_PHASES] == DEFAULT_CHARGER_PHASES
    assert result[CONF_VOLTAGE] == DEFAULT_VOLTAGE
    assert result[CONF_FAST_AMPS] == DEFAULT_FAST_AMPS
    assert result[CONF_BATTERY_CAPACITY_KWH] == pytest.approx(DEFAULT_BATTERY_CAPACITY_KWH)
    assert result[CONF_DEFAULT_TARGET_SOC] == DEFAULT_TARGET_SOC
    assert result[CONF_MIN_SOC] == DEFAULT_MIN_SOC
    assert result[CONF_CHARGE_START_SOC] == DEFAULT_CHARGE_START_SOC
    assert result[CONF_MAX_PRICE_THRESHOLD] == pytest.approx(DEFAULT_MAX_PRICE_THRESHOLD)
    assert result[CONF_SMART_DEADLINE_HOURS] == DEFAULT_SMART_DEADLINE_HOURS
    assert result[CONF_CAR_LIMIT_REPLAN] is DEFAULT_CAR_LIMIT_REPLAN


def test_schema_car_limit_replan_off():
    result = CONFIG_SCHEMA({**VALID_INPUT, CONF_CAR_LIMIT_REPLAN: False})
    assert result[CONF_CAR_LIMIT_REPLAN] is False


def test_schema_invalid_charger_phases():
    with pytest.raises(vol.Invalid):
        CONFIG_SCHEMA({**VALID_INPUT, CONF_CHARGER_PHASES: 2})


def test_schema_invalid_voltage_too_low():
    with pytest.raises(vol.Invalid):
        CONFIG_SCHEMA({**VALID_INPUT, CONF_VOLTAGE: 50})


def test_schema_invalid_soc_out_of_range():
    with pytest.raises(vol.Invalid):
        CONFIG_SCHEMA({**VALID_INPUT, CONF_DEFAULT_TARGET_SOC: 150})
