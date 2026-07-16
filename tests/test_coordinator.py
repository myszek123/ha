"""Tests for coordinator reason-determination logic."""
from __future__ import annotations

from datetime import datetime

import pytest

from custom_components.myszolot.coordinator import (
    determine_reason,
    is_in_session,
    expected_end_soc,
    estimate_window_cost,
    session_duration_minutes,
    max_energy_in_window,
    max_reachable_soc,
    build_schedule,
)
from custom_components.myszolot.const import (
    MODE_SMART, MODE_OVERRIDE,
    REASON_OUTSIDE_CHARGING, REASON_OUTSIDE_NOT_CHARGING,
    REASON_TARGET_REACHED, REASON_MIN_SOC_FLOOR,
    REASON_SOC_SUFFICIENT, REASON_PRICE_TOO_HIGH,
    REASON_SCHEDULED, REASON_WAITING_FOR_SESSION, REASON_NO_ELIGIBLE_HOURS,
    REASON_HOME_NOT_PLUGGED, REASON_TARGET_UNREACHABLE,
)

DEFAULTS = dict(
    mode=MODE_SMART,
    is_home=True,
    cable_connected=True,
    current_soc=50.0,
    target_soc=80,
    min_soc=30,
    charge_start_soc=69,
    charge_amps=12,
    sessions=[],
    now_dt=datetime(2024, 1, 15, 10, 0),
    E_needed=20.0,
    schedule_all_prices_above_max=False,
    is_externally_charging=False,
    feasible=True,
)


def dr(**overrides) -> tuple:
    """Call determine_reason with defaults overridden by kwargs."""
    return determine_reason(**{**DEFAULTS, **overrides})


def _session(start: datetime, end: datetime) -> dict:
    return {"start": start, "end": end, "slots": [], "total_kWh": 0, "total_cost": 0}


# ── Priority 1: NOT home ──────────────────────────────────────────────────────

def test_not_home_not_charging():
    reason, should_charge, amps = dr(is_home=False, is_externally_charging=False)
    assert reason == REASON_OUTSIDE_NOT_CHARGING
    assert should_charge is False
    assert amps == 0


def test_not_home_is_charging():
    reason, should_charge, amps = dr(is_home=False, is_externally_charging=True)
    assert reason == REASON_OUTSIDE_CHARGING
    assert should_charge is False
    assert amps == 0


# ── Priority 2: home, no cable, SoC >= target ─────────────────────────────────

def test_home_no_cable_soc_at_target():
    reason, should_charge, amps = dr(cable_connected=False, current_soc=80, target_soc=80)
    assert reason == REASON_TARGET_REACHED
    assert should_charge is False


def test_home_no_cable_soc_above_target():
    reason, should_charge, amps = dr(cable_connected=False, current_soc=85, target_soc=80)
    assert reason == REASON_TARGET_REACHED
    assert should_charge is False


# ── Priority 3: min_soc floor (emergency) ────────────────────────────────────

def test_min_soc_floor_with_cable():
    reason, should_charge, amps = dr(
        current_soc=20, min_soc=30, cable_connected=True, mode=MODE_SMART
    )
    assert reason == REASON_MIN_SOC_FLOOR
    assert should_charge is True
    assert amps == DEFAULTS["charge_amps"]


def test_min_soc_floor_no_cable_not_triggered():
    reason, should_charge, amps = dr(
        current_soc=20, min_soc=30, cable_connected=False
    )
    assert reason == REASON_NO_ELIGIBLE_HOURS
    assert should_charge is False


# ── Smart mode ────────────────────────────────────────────────────────────────

def test_smart_soc_sufficient():
    reason, should_charge, amps = dr(mode=MODE_SMART, current_soc=70, charge_start_soc=69)
    assert reason == REASON_SOC_SUFFICIENT
    assert should_charge is False


def test_smart_price_too_high():
    reason, should_charge, amps = dr(
        mode=MODE_SMART, current_soc=50, schedule_all_prices_above_max=True
    )
    assert reason == REASON_PRICE_TOO_HIGH
    assert should_charge is False


def test_smart_scheduled_in_session():
    now = datetime(2024, 1, 15, 14, 30)
    sessions = [_session(datetime(2024, 1, 15, 14, 0), datetime(2024, 1, 15, 15, 0))]
    reason, should_charge, amps = dr(
        mode=MODE_SMART, sessions=sessions, now_dt=now, current_soc=50
    )
    assert reason == REASON_SCHEDULED
    assert should_charge is True
    assert amps == DEFAULTS["charge_amps"]


def test_smart_waiting_for_session():
    now = datetime(2024, 1, 15, 10, 0)
    sessions = [_session(datetime(2024, 1, 15, 14, 0), datetime(2024, 1, 15, 15, 0))]
    reason, should_charge, amps = dr(
        mode=MODE_SMART, sessions=sessions, now_dt=now, current_soc=50
    )
    assert reason == REASON_WAITING_FOR_SESSION
    assert should_charge is False


def test_smart_no_eligible_hours():
    reason, should_charge, amps = dr(
        mode=MODE_SMART, sessions=[], E_needed=10.0,
        schedule_all_prices_above_max=False, current_soc=50
    )
    assert reason == REASON_NO_ELIGIBLE_HOURS
    assert should_charge is False


def test_smart_target_reached_e_needed_zero():
    reason, should_charge, amps = dr(mode=MODE_SMART, E_needed=0.0, current_soc=50)
    assert reason == REASON_TARGET_REACHED
    assert should_charge is False


# ── Override mode ─────────────────────────────────────────────────────────────

def test_override_no_soc_sufficient_check():
    # Override ignores charge_start_soc — should NOT return soc_sufficient
    reason, should_charge, amps = dr(
        mode=MODE_OVERRIDE,
        current_soc=75,
        charge_start_soc=69,
        target_soc=95,
        E_needed=13.0,
        sessions=[],
        feasible=True,
    )
    assert reason == REASON_NO_ELIGIBLE_HOURS


def test_override_ignores_price_too_high_flag():
    # schedule_all_prices_above_max is a smart-mode concept; override still schedules
    now = datetime(2024, 1, 15, 14, 30)
    sessions = [_session(datetime(2024, 1, 15, 14, 0), datetime(2024, 1, 15, 15, 0))]
    reason, should_charge, amps = dr(
        mode=MODE_OVERRIDE,
        sessions=sessions,
        now_dt=now,
        current_soc=50,
        target_soc=95,
        E_needed=30.0,
        schedule_all_prices_above_max=True,
    )
    assert reason == REASON_SCHEDULED
    assert should_charge is True


def test_override_scheduled():
    now = datetime(2024, 1, 15, 14, 30)
    sessions = [_session(datetime(2024, 1, 15, 14, 0), datetime(2024, 1, 15, 15, 0))]
    reason, should_charge, amps = dr(
        mode=MODE_OVERRIDE, sessions=sessions, now_dt=now,
        current_soc=50, target_soc=95, E_needed=30.0,
    )
    assert reason == REASON_SCHEDULED
    assert should_charge is True


def test_override_unreachable():
    reason, should_charge, amps = dr(
        mode=MODE_OVERRIDE,
        sessions=[],
        E_needed=40.0,
        current_soc=40,
        target_soc=95,
        feasible=False,
    )
    assert reason == REASON_TARGET_UNREACHABLE
    assert should_charge is False


def test_override_waiting():
    now = datetime(2024, 1, 15, 10, 0)
    sessions = [_session(datetime(2024, 1, 15, 14, 0), datetime(2024, 1, 15, 16, 0))]
    reason, should_charge, amps = dr(
        mode=MODE_OVERRIDE, sessions=sessions, now_dt=now,
        current_soc=55, target_soc=95, E_needed=25.0,
    )
    assert reason == REASON_WAITING_FOR_SESSION
    assert should_charge is False


# ── charging_started flag ─────────────────────────────────────────────────────

def test_charging_started_bypasses_soc_sufficient():
    now = datetime(2024, 1, 15, 14, 30)
    sessions = [_session(datetime(2024, 1, 15, 14, 0), datetime(2024, 1, 15, 15, 0))]
    reason, should_charge, amps = determine_reason(
        mode=MODE_SMART,
        is_home=True,
        cable_connected=True,
        current_soc=72,
        target_soc=80,
        min_soc=30,
        charge_start_soc=69,
        charge_amps=12,
        sessions=sessions,
        now_dt=now,
        E_needed=5.0,
        schedule_all_prices_above_max=False,
        charging_started=True,
    )
    assert reason == REASON_SCHEDULED
    assert should_charge is True
    assert amps == 12


def test_charging_started_false_allows_soc_sufficient():
    reason, should_charge, amps = determine_reason(
        mode=MODE_SMART,
        is_home=True,
        cable_connected=True,
        current_soc=72,
        target_soc=80,
        min_soc=30,
        charge_start_soc=69,
        charge_amps=12,
        sessions=[],
        now_dt=datetime(2024, 1, 15, 10, 0),
        E_needed=5.0,
        schedule_all_prices_above_max=False,
        charging_started=False,
    )
    assert reason == REASON_SOC_SUFFICIENT
    assert should_charge is False


# ── Fallback ──────────────────────────────────────────────────────────────────

def test_home_not_plugged_fallback():
    reason, should_charge, amps = determine_reason(
        mode="unknown_mode",
        is_home=True,
        cable_connected=False,
        current_soc=50,
        target_soc=80,
        min_soc=30,
        charge_start_soc=69,
        charge_amps=12,
        sessions=[],
        now_dt=datetime(2024, 1, 15, 10, 0),
        E_needed=10.0,
        schedule_all_prices_above_max=False,
    )
    assert reason == REASON_HOME_NOT_PLUGGED
    assert should_charge is False


# ── Helpers / estimates ───────────────────────────────────────────────────────

def test_expected_end_soc_clamped_to_target():
    assert expected_end_soc(40.0, 10.0, 50.0, 80) == 60.0
    assert expected_end_soc(75.0, 20.0, 50.0, 80) == 80.0


def test_estimate_window_cost_uses_hourly_prices():
    start = datetime(2024, 1, 15, 2, 0)
    end = datetime(2024, 1, 15, 4, 0)
    prices = [{"hour": 2, "price": 0.2}, {"hour": 3, "price": 0.4}]
    kwh, cost = estimate_window_cost(start, end, 10.0, prices, 1.0, start.date())
    assert kwh == 20.0
    assert cost == pytest.approx(0.2 * 10 + 0.4 * 10)


def test_session_duration_minutes():
    sessions = [
        {
            "start": datetime(2024, 1, 15, 2, 0),
            "end": datetime(2024, 1, 15, 3, 15),
            "total_kWh": 0,
            "total_cost": 0,
        }
    ]
    assert session_duration_minutes(sessions) == 75


def test_max_energy_in_window():
    prices = [{"hour": h, "price": 0.5} for h in range(10, 20)]
    now = datetime(2024, 1, 15, 10, 0)
    # 5 hours × 8.28 kW
    assert max_energy_in_window(prices, 8.28, now, 5) == pytest.approx(5 * 8.28)


def test_max_reachable_soc():
    # 10 kWh into 50 kWh pack from 40% → 60%
    assert max_reachable_soc(40.0, 10.0, 50.0) == 60.0
    assert max_reachable_soc(95.0, 50.0, 50.0) == 100.0


def test_build_schedule_picks_cheapest():
    prices = [
        {"hour": 10, "price": 1.0},
        {"hour": 11, "price": 0.2},
        {"hour": 12, "price": 0.5},
    ]
    now = datetime(2024, 1, 15, 10, 0)
    sched = build_schedule(prices, E_needed=8.0, max_kWh_per_hour=8.0, now_dt=now, deadline_hours=5)
    assert len(sched) == 1
    assert sched[0]["hour"] == 11


def test_is_in_session_exclusive_end():
    now = datetime(2024, 1, 15, 5, 0)
    sessions = [_session(datetime(2024, 1, 15, 2, 0), datetime(2024, 1, 15, 5, 0))]
    assert is_in_session(sessions, now) is False
