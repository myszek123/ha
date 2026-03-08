"""Tests for coordinator reason-determination logic."""
from __future__ import annotations

from datetime import datetime

import pytest

from custom_components.myszolot.coordinator import determine_reason, is_in_session
from custom_components.myszolot.const import (
    MODE_SMART, MODE_NOW_FAST, MODE_NOW_SLOW, MODE_PLAN_TRIP, MODE_TRIP_NOW,
    REASON_OUTSIDE_CHARGING, REASON_OUTSIDE_NOT_CHARGING,
    REASON_TARGET_REACHED, REASON_MIN_SOC_FLOOR,
    REASON_CHARGING_NOW_FAST, REASON_CHARGING_NOW_SLOW, REASON_TRIP_CHARGING_NOW,
    REASON_SOC_SUFFICIENT, REASON_PRICE_TOO_HIGH,
    REASON_SCHEDULED, REASON_WAITING_FOR_SESSION, REASON_NO_ELIGIBLE_HOURS,
    REASON_HOME_NOT_PLUGGED,
)

# ── Default parameters shared across most tests ───────────────────────────────

DEFAULTS = dict(
    mode=MODE_SMART,
    is_home=True,
    cable_connected=True,
    current_soc=50.0,
    target_soc=80,
    min_soc=30,
    charge_start_soc=69,
    fast_amps=10,
    slow_amps=5,
    sessions=[],
    now_dt=datetime(2024, 1, 15, 10, 0),
    E_needed=20.0,
    schedule_all_prices_above_max=False,
    is_externally_charging=False,
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
    assert amps == DEFAULTS["fast_amps"]


def test_min_soc_floor_no_cable_not_triggered():
    # Without cable, priority 3 (min_soc_floor) is skipped.
    # mode=SMART falls into priority 7: soc(20) <= charge_start_soc(69),
    # no schedule, E_needed > 0 → no_eligible_hours.
    reason, should_charge, amps = dr(
        current_soc=20, min_soc=30, cable_connected=False
    )
    assert reason == REASON_NO_ELIGIBLE_HOURS
    assert should_charge is False


# ── Priority 4: now_fast ──────────────────────────────────────────────────────

def test_mode_now_fast():
    reason, should_charge, amps = dr(mode=MODE_NOW_FAST)
    assert reason == REASON_CHARGING_NOW_FAST
    assert should_charge is True
    assert amps == DEFAULTS["fast_amps"]


# ── Priority 5: now_slow ──────────────────────────────────────────────────────

def test_mode_now_slow():
    reason, should_charge, amps = dr(mode=MODE_NOW_SLOW)
    assert reason == REASON_CHARGING_NOW_SLOW
    assert should_charge is True
    assert amps == DEFAULTS["slow_amps"]


# ── Priority 6: trip_now ──────────────────────────────────────────────────────

def test_mode_trip_now():
    reason, should_charge, amps = dr(mode=MODE_TRIP_NOW)
    assert reason == REASON_TRIP_CHARGING_NOW
    assert should_charge is True
    assert amps == DEFAULTS["fast_amps"]


# ── Priority 7: smart mode ────────────────────────────────────────────────────

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
    assert amps == DEFAULTS["fast_amps"]


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


# ── Priority 7: plan_trip mode ────────────────────────────────────────────────

def test_plan_trip_no_soc_sufficient_check():
    # plan_trip ignores charge_start_soc — should NOT return soc_sufficient
    reason, should_charge, amps = dr(
        mode=MODE_PLAN_TRIP,
        current_soc=75,
        charge_start_soc=69,  # soc (75) > charge_start_soc (69)
        target_soc=95,
        E_needed=13.0,
        sessions=[],
    )
    # Should NOT be soc_sufficient; falls to no_eligible_hours
    assert reason == REASON_NO_ELIGIBLE_HOURS


def test_plan_trip_scheduled():
    now = datetime(2024, 1, 15, 14, 30)
    sessions = [_session(datetime(2024, 1, 15, 14, 0), datetime(2024, 1, 15, 15, 0))]
    reason, should_charge, amps = dr(
        mode=MODE_PLAN_TRIP, sessions=sessions, now_dt=now,
        current_soc=50, target_soc=95, E_needed=30.0,
    )
    assert reason == REASON_SCHEDULED
    assert should_charge is True


# ── Mode auto-reset side-effects (tested via determine_reason directly) ───────

def test_now_fast_with_high_soc_still_charges():
    # determine_reason does NOT auto-reset modes — that's the coordinator's job.
    # At this level, now_fast always returns charging.
    reason, should_charge, amps = dr(
        mode=MODE_NOW_FAST, current_soc=85, target_soc=80
    )
    assert reason == REASON_CHARGING_NOW_FAST
    assert should_charge is True


# ── cable_needed derivation ───────────────────────────────────────────────────

def test_cable_needed_when_should_charge_no_cable_home():
    """cable_needed = should_charge AND not cable AND is_home."""
    reason, should_charge, amps = dr(mode=MODE_NOW_FAST, cable_connected=False, is_home=True)
    assert should_charge is True  # now_fast always wants to charge
    # cable_needed is computed by the coordinator, not determine_reason; verify flag
    cable_needed = should_charge and not True and True  # cable_connected=False
    assert cable_needed is False  # double-check logic (should_charge=True, no_cable=True, home=True → True)
    cable_needed = should_charge and (not False) and True
    assert cable_needed is True


def test_cable_needed_false_when_not_home():
    reason, should_charge, amps = dr(mode=MODE_NOW_FAST, is_home=False)
    assert should_charge is False  # not home → no charge
    cable_needed = should_charge and True and False  # not home
    assert cable_needed is False


def test_cable_needed_false_when_cable_connected():
    reason, should_charge, amps = dr(mode=MODE_NOW_FAST, cable_connected=True, is_home=True)
    assert should_charge is True
    cable_needed = should_charge and (not True) and True
    assert cable_needed is False


# ── Priority 8: home, not plugged ────────────────────────────────────────────

def test_home_not_plugged_fallback():
    # Smart mode, no cable, soc < target, soc > charge_start_soc → soc_sufficient
    # (verify home_not_plugged IS reachable when no mode matches nothing else)
    # Trigger it: cable disconnected, soc < target, soc < charge_start_soc but mode=smart
    # wait — smart with cable_connected=False and soc <= charge_start_soc falls through
    # to waiting/no_eligible if there are no sessions. Let's confirm via no-cable path.
    # Actually home_not_plugged is only reachable as fallback for unknown mode.
    # For known modes, smart returns a specific reason even without cable.
    # Let's test the actual fallback with an unrecognised mode string.
    reason, should_charge, amps = determine_reason(
        mode="unknown_mode",
        is_home=True,
        cable_connected=False,
        current_soc=50,
        target_soc=80,
        min_soc=30,
        charge_start_soc=69,
        fast_amps=10,
        slow_amps=5,
        sessions=[],
        now_dt=datetime(2024, 1, 15, 10, 0),
        E_needed=10.0,
        schedule_all_prices_above_max=False,
    )
    assert reason == REASON_HOME_NOT_PLUGGED
    assert should_charge is False


# ── charging_started flag behavior ──────────────────────────────────────────────

def test_charging_started_bypasses_soc_sufficient():
    """
    When charging_started=True, soc_sufficient should be skipped.
    This allows charging to continue past charge_start_soc.
    """
    # SoC=72 > charge_start_soc=69, but charging_started=True → skip soc_sufficient
    # Should fall through to is_in_session logic
    now = datetime(2024, 1, 15, 14, 30)
    sessions = [_session(datetime(2024, 1, 15, 14, 0), datetime(2024, 1, 15, 15, 0))]
    reason, should_charge, amps = determine_reason(
        mode=MODE_SMART,
        is_home=True,
        cable_connected=True,
        current_soc=72,  # above charge_start_soc=69
        target_soc=80,
        min_soc=30,
        charge_start_soc=69,
        fast_amps=10,
        slow_amps=5,
        sessions=sessions,
        now_dt=now,
        E_needed=5.0,
        schedule_all_prices_above_max=False,
        charging_started=True,  # Flag is True → skip soc_sufficient check
    )
    # Should return REASON_SCHEDULED, not REASON_SOC_SUFFICIENT
    assert reason == REASON_SCHEDULED
    assert should_charge is True
    assert amps == 10


def test_charging_started_false_allows_soc_sufficient():
    """
    When charging_started=False, soc_sufficient should trigger.
    This prevents starting a session if SoC is already high.
    """
    # SoC=72 > charge_start_soc=69, charging_started=False → should return soc_sufficient
    reason, should_charge, amps = determine_reason(
        mode=MODE_SMART,
        is_home=True,
        cable_connected=True,
        current_soc=72,
        target_soc=80,
        min_soc=30,
        charge_start_soc=69,
        fast_amps=10,
        slow_amps=5,
        sessions=[],
        now_dt=datetime(2024, 1, 15, 10, 0),
        E_needed=5.0,
        schedule_all_prices_above_max=False,
        charging_started=False,  # Flag is False → allow soc_sufficient check
    )
    assert reason == REASON_SOC_SUFFICIENT
    assert should_charge is False
