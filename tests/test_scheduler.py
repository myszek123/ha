"""Unit tests for build_schedule, compute_sessions, is_in_session, next_session."""
from __future__ import annotations

from datetime import datetime, date

import pytest

from custom_components.myszolot.coordinator import (
    build_schedule,
    compute_sessions,
    is_in_session,
    next_session,
)

# ── Helpers ───────────────────────────────────────────────────────────────────

NOW_10AM = datetime(2024, 1, 15, 10, 0, 0)  # Monday 10:00
TODAY = date(2024, 1, 15)
MAX_KWH = 10.0  # simplified max charge rate for tests


def prices(*args) -> list[dict]:
    """Build price list from (hour, price) pairs."""
    return [{"hour": h, "price": p} for h, p in args]


# ── build_schedule ────────────────────────────────────────────────────────────

def test_build_schedule_no_eligible_hours():
    # All prices exceed max_price cap → empty result
    all_prices = prices(*((h, 1.5) for h in range(10, 15)))
    result = build_schedule(all_prices, E_needed=5.0, max_kWh_per_hour=MAX_KWH,
                            now_dt=NOW_10AM, max_price=1.0)
    assert result == []


def test_build_schedule_exact_fill():
    # Exactly one hour with 10 kWh capacity, need exactly 10 kWh
    all_prices = prices((13, 0.5), (14, 0.6))
    result = build_schedule(all_prices, E_needed=10.0, max_kWh_per_hour=MAX_KWH,
                            now_dt=NOW_10AM)
    assert len(result) == 1
    assert result[0]["hour"] == 13
    assert result[0]["kWh"] == pytest.approx(10.0)
    assert result[0]["full"] is True
    assert result[0]["minutes"] == 60


def test_build_schedule_partial_fill():
    # Need 6 kWh, one hour → 6/10 of the hour
    all_prices = prices((14, 0.4))
    result = build_schedule(all_prices, E_needed=6.0, max_kWh_per_hour=MAX_KWH,
                            now_dt=NOW_10AM)
    assert len(result) == 1
    assert result[0]["kWh"] == pytest.approx(6.0)
    assert result[0]["full"] is False
    assert result[0]["minutes"] == 36  # int(6/10*60)


def test_build_schedule_over_demand_capped_by_available_hours():
    # Need 30 kWh but only 2 hours (13, 14) → max 20 kWh
    all_prices = prices((13, 0.5), (14, 0.4))
    result = build_schedule(all_prices, E_needed=30.0, max_kWh_per_hour=MAX_KWH,
                            now_dt=NOW_10AM)
    assert len(result) == 2
    total = sum(s["kWh"] for s in result)
    assert total == pytest.approx(20.0)  # capped by available capacity


def test_build_schedule_cheapest_first():
    # Hour 14 is cheaper → should be allocated first
    all_prices = prices((13, 0.50), (14, 0.25))
    result = build_schedule(all_prices, E_needed=12.0, max_kWh_per_hour=MAX_KWH,
                            now_dt=NOW_10AM)
    # Sorted by hour, but cheapest (14) allocated first
    hour_order = [s["hour"] for s in result]
    assert hour_order == [13, 14]  # sorted chronologically
    # Hour 14 should be full (10 kWh), hour 13 should be partial (2 kWh)
    slot_by_hour = {s["hour"]: s for s in result}
    assert slot_by_hour[14]["full"] is True
    assert slot_by_hour[14]["kWh"] == pytest.approx(10.0)
    assert slot_by_hour[13]["kWh"] == pytest.approx(2.0)
    assert slot_by_hour[13]["full"] is False


def test_build_schedule_all_hours_eligible():
    # Without G12 filter, any in-range hour is eligible
    all_prices = prices((10, 0.3), (11, 0.4))  # hours that were previously non-G12
    result = build_schedule(all_prices, E_needed=10.0, max_kWh_per_hour=MAX_KWH,
                            now_dt=NOW_10AM)
    assert len(result) == 1
    assert result[0]["hour"] == 10  # cheapest first


def test_build_schedule_price_cap_excludes_expensive():
    all_prices = prices((13, 1.5), (14, 0.8))
    result = build_schedule(all_prices, E_needed=10.0, max_kWh_per_hour=MAX_KWH,
                            now_dt=NOW_10AM, max_price=1.0)
    # Hour 13 (1.5 PLN) excluded; hour 14 (0.8 PLN) included
    assert len(result) == 1
    assert result[0]["hour"] == 14


def test_build_schedule_price_cap_all_excluded():
    all_prices = prices((13, 1.5), (14, 1.2))
    result = build_schedule(all_prices, E_needed=10.0, max_kWh_per_hour=MAX_KWH,
                            now_dt=NOW_10AM, max_price=1.0)
    assert result == []


def test_build_schedule_deadline_hours_limits_window():
    # now=10:00, deadline=4 → only hours 10..13 eligible
    all_prices = prices((13, 0.3), (14, 0.2))
    result = build_schedule(all_prices, E_needed=10.0, max_kWh_per_hour=MAX_KWH,
                            now_dt=NOW_10AM, deadline_hours=4)
    # Hour 14 is at now_hour(10) + 4 = 14, which is NOT < 14 → excluded
    assert all(s["hour"] < 14 for s in result)


def test_build_schedule_zero_e_needed():
    all_prices = prices((13, 0.5))
    result = build_schedule(all_prices, E_needed=0.0, max_kWh_per_hour=MAX_KWH,
                            now_dt=NOW_10AM)
    assert result == []


# ── compute_sessions ──────────────────────────────────────────────────────────

def test_compute_sessions_empty():
    assert compute_sessions([], TODAY) == []


def test_compute_sessions_single_full_slot():
    schedule = [{"hour": 22, "minutes": 60, "kWh": 10.0, "cost": 5.0, "full": True}]
    sessions = compute_sessions(schedule, TODAY)
    assert len(sessions) == 1
    s = sessions[0]
    assert s["start"] == datetime(2024, 1, 15, 22, 0)
    assert s["end"] == datetime(2024, 1, 15, 23, 0)
    assert s["total_kWh"] == pytest.approx(10.0)


def test_compute_sessions_single_partial_slot():
    # 6 kWh / 10 kWh → 36 min → shift to tail: start = 14:24, end = 14:24 + 36min = 15:00
    schedule = [{"hour": 14, "minutes": 36, "kWh": 6.0, "cost": 2.4, "full": False}]
    sessions = compute_sessions(schedule, TODAY)
    assert len(sessions) == 1
    s = sessions[0]
    assert s["start"] == datetime(2024, 1, 15, 14, 24)
    assert s["end"] == datetime(2024, 1, 15, 15, 0)


def test_compute_sessions_key_scenario_12kwh():
    """
    12 kWh needed, 10 kWh/h max.
    Hours: 13@0.50, 14@0.25 → cheapest (14) allocated first.
    Result: hour 13 partial (2 kWh, 12 min), hour 14 full (10 kWh, 60 min).
    Session: start=13:48, end=15:00 (continuous 72 min).
    """
    schedule = [
        {"hour": 13, "minutes": 12, "kWh": 2.0, "cost": 1.0, "full": False},
        {"hour": 14, "minutes": 60, "kWh": 10.0, "cost": 2.5, "full": True},
    ]
    sessions = compute_sessions(schedule, TODAY)
    assert len(sessions) == 1
    s = sessions[0]
    assert s["start"] == datetime(2024, 1, 15, 13, 48)
    assert s["end"] == datetime(2024, 1, 15, 15, 0)
    assert s["total_kWh"] == pytest.approx(12.0)


def test_compute_sessions_two_adjacent_full_slots():
    # 22:00 (60 min) + 23:00 (60 min) → single session 22:00 to 00:00 next day
    schedule = [
        {"hour": 22, "minutes": 60, "kWh": 10.0, "cost": 4.0, "full": True},
        {"hour": 23, "minutes": 60, "kWh": 10.0, "cost": 3.5, "full": True},
    ]
    sessions = compute_sessions(schedule, TODAY)
    assert len(sessions) == 1
    s = sessions[0]
    assert s["start"] == datetime(2024, 1, 15, 22, 0)
    assert s["end"] == datetime(2024, 1, 16, 0, 0)  # midnight = next day 00:00


def test_compute_sessions_two_non_adjacent_slots():
    schedule = [
        {"hour": 14, "minutes": 60, "kWh": 10.0, "cost": 5.0, "full": True},
        {"hour": 22, "minutes": 30, "kWh": 5.0, "cost": 2.0, "full": False},
    ]
    sessions = compute_sessions(schedule, TODAY)
    assert len(sessions) == 2
    assert sessions[0]["start"] == datetime(2024, 1, 15, 14, 0)
    assert sessions[0]["end"] == datetime(2024, 1, 15, 15, 0)
    assert sessions[1]["start"] == datetime(2024, 1, 15, 22, 30)  # partial → shifted to tail
    assert sessions[1]["end"] == datetime(2024, 1, 15, 23, 0)    # start + 30 min


def test_compute_sessions_full_then_partial_end():
    """Full first slot, partial last slot in the same group."""
    schedule = [
        {"hour": 22, "minutes": 60, "kWh": 10.0, "cost": 4.0, "full": True},
        {"hour": 23, "minutes": 30, "kWh": 5.0, "cost": 2.0, "full": False},
    ]
    sessions = compute_sessions(schedule, TODAY)
    assert len(sessions) == 1
    s = sessions[0]
    assert s["start"] == datetime(2024, 1, 15, 22, 0)   # first is full → start at :00
    assert s["end"] == datetime(2024, 1, 15, 23, 30)    # last is partial → end at :30


def test_compute_sessions_spanning_midnight_with_tomorrow_hours():
    """
    Session spanning from today 23:00 through tomorrow 01:00 (consecutive hours 23-25).
    Then a separate session for tomorrow 13:00-14:00 (non-consecutive, gap between 25 and 37).
    """
    schedule = [
        {"hour": 23, "minutes": 60, "kWh": 10.0, "cost": 3.5, "full": True},
        {"hour": 24, "minutes": 60, "kWh": 10.0, "cost": 0.3, "full": True},  # tomorrow 00:00
        {"hour": 25, "minutes": 60, "kWh": 10.0, "cost": 0.3, "full": True},  # tomorrow 01:00
        {"hour": 37, "minutes": 60, "kWh": 10.0, "cost": 0.5, "full": True},  # tomorrow 13:00
        {"hour": 38, "minutes": 60, "kWh": 10.0, "cost": 0.5, "full": True},  # tomorrow 14:00
    ]
    sessions = compute_sessions(schedule, TODAY)
    # Should have 2 sessions: hours 23-25 (consecutive) and hours 37-38 (consecutive, but separate from first)
    assert len(sessions) == 2
    # First session: today 23:00 through tomorrow 02:00 (3 full hours = 180 min)
    s1 = sessions[0]
    assert s1["start"] == datetime(2024, 1, 15, 23, 0)
    assert s1["end"] == datetime(2024, 1, 16, 2, 0)
    assert s1["total_kWh"] == pytest.approx(30.0)
    # Second session: tomorrow 13:00 through 15:00 (2 full hours = 120 min)
    s2 = sessions[1]
    assert s2["start"] == datetime(2024, 1, 16, 13, 0)
    assert s2["end"] == datetime(2024, 1, 16, 15, 0)
    assert s2["total_kWh"] == pytest.approx(20.0)


def test_compute_sessions_tomorrow_only_hours():
    """Session with only tomorrow's hours (24-38 = tomorrow 00:00 to 14:00)."""
    schedule = [
        {"hour": 24, "minutes": 60, "kWh": 10.0, "cost": 0.3, "full": True},  # tomorrow 00:00
        {"hour": 37, "minutes": 60, "kWh": 10.0, "cost": 0.5, "full": True},  # tomorrow 13:00
        {"hour": 38, "minutes": 12, "kWh": 2.0, "cost": 0.1, "full": False},  # tomorrow 14:00 partial
    ]
    sessions = compute_sessions(schedule, TODAY)
    # Should have 2 sessions: one for hour 24, one for hours 37-38
    assert len(sessions) == 2
    # First session: tomorrow 00:00 (full hour)
    s1 = sessions[0]
    assert s1["start"] == datetime(2024, 1, 16, 0, 0)
    assert s1["end"] == datetime(2024, 1, 16, 1, 0)
    # Second session: tomorrow 13:00 (full) + 14:00 (partial)
    # Start at 13:00 (full hour), total 60 + 12 = 72 min → end at 14:12
    s2 = sessions[1]
    assert s2["start"] == datetime(2024, 1, 16, 13, 0)
    assert s2["end"] == datetime(2024, 1, 16, 14, 12)  # 60 + 12 = 72 min from 13:00


# ── is_in_session ─────────────────────────────────────────────────────────────

def _session(start: datetime, end: datetime) -> dict:
    return {"start": start, "end": end, "slots": [], "total_kWh": 0, "total_cost": 0}


def test_is_in_session_inside():
    sessions = [_session(datetime(2024, 1, 15, 14, 0), datetime(2024, 1, 15, 15, 0))]
    assert is_in_session(sessions, datetime(2024, 1, 15, 14, 30)) is True


def test_is_in_session_before():
    sessions = [_session(datetime(2024, 1, 15, 14, 0), datetime(2024, 1, 15, 15, 0))]
    assert is_in_session(sessions, datetime(2024, 1, 15, 13, 59)) is False


def test_is_in_session_after():
    sessions = [_session(datetime(2024, 1, 15, 14, 0), datetime(2024, 1, 15, 15, 0))]
    assert is_in_session(sessions, datetime(2024, 1, 15, 15, 0)) is False


def test_is_in_session_at_start_boundary():
    sessions = [_session(datetime(2024, 1, 15, 14, 0), datetime(2024, 1, 15, 15, 0))]
    assert is_in_session(sessions, datetime(2024, 1, 15, 14, 0)) is True


def test_is_in_session_between_two_sessions():
    sessions = [
        _session(datetime(2024, 1, 15, 14, 0), datetime(2024, 1, 15, 15, 0)),
        _session(datetime(2024, 1, 15, 22, 0), datetime(2024, 1, 15, 23, 0)),
    ]
    assert is_in_session(sessions, datetime(2024, 1, 15, 18, 0)) is False


def test_is_in_session_empty():
    assert is_in_session([], datetime(2024, 1, 15, 14, 0)) is False


# ── next_session ──────────────────────────────────────────────────────────────

def test_next_session_returns_upcoming():
    now = datetime(2024, 1, 15, 10, 0)
    s1 = _session(datetime(2024, 1, 15, 14, 0), datetime(2024, 1, 15, 15, 0))
    s2 = _session(datetime(2024, 1, 15, 22, 0), datetime(2024, 1, 15, 23, 0))
    ns = next_session([s1, s2], now)
    assert ns is s1


def test_next_session_skips_past():
    now = datetime(2024, 1, 15, 15, 0)
    s1 = _session(datetime(2024, 1, 15, 14, 0), datetime(2024, 1, 15, 15, 0))
    s2 = _session(datetime(2024, 1, 15, 22, 0), datetime(2024, 1, 15, 23, 0))
    ns = next_session([s1, s2], now)
    assert ns is s2


def test_next_session_none_when_all_past():
    now = datetime(2024, 1, 15, 23, 30)
    sessions = [_session(datetime(2024, 1, 15, 22, 0), datetime(2024, 1, 15, 23, 0))]
    assert next_session(sessions, now) is None


def test_next_session_empty():
    assert next_session([], datetime(2024, 1, 15, 10, 0)) is None
