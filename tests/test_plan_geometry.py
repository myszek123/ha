"""
Plan geometry (the behaviour we want, not ASAP / not flatten).

Core cheap block is 13:00–15:00 (hours 13 and 14). Energy needs those
two hours plus 15 extra minutes. Remaining 15 min go on the next-cheapest
hour:

  - hour 12 cheaper than 15 → attach *before*, tail-packed: 12:45–15:00
  - hour 15 cheaper than 12 → attach *after*:               13:00–15:15
  - some other hour cheaper than both → extra 15 min there
    (may be a second session)

Once a session has started, it runs that block with no mid-session offs.
"""
from __future__ import annotations

from datetime import datetime, date

from custom_components.myszolot.coordinator import (
    build_schedule,
    compute_sessions,
    assign_session_amps,
    is_in_session,
    determine_reason,
)
from custom_components.myszolot.const import (
    MODE_SMART,
    REASON_SCHEDULED,
    REASON_WAITING_FOR_SESSION,
)

TODAY = date(2026, 8, 14)
NOON = datetime(2026, 8, 14, 12, 0)
# Round numbers: 8 kWh/h → 15 min = 2 kWh. Need 2h + 15 min = 18 kWh.
RATE = 8.0
NEED = 18.0  # 16 kWh in 13–14 + 2 kWh leftover


def _hours(by_hour: dict[int, float]) -> list[dict]:
    """Price grid 12–22; unspecified hours are expensive (1.00)."""
    return [{"hour": h, "price": by_hour.get(h, 1.00)} for h in range(12, 23)]


def _plan(price_grid: list[dict], now: datetime = NOON):
    schedule = build_schedule(
        price_grid, E_needed=NEED, max_kWh_per_hour=RATE,
        now_dt=now, deadline_hours=12,
    )
    sessions = assign_session_amps(compute_sessions(schedule, TODAY), 11)
    return schedule, sessions


def test_extra_15min_on_cheaper_hour_12_is_1245_to_1500():
    """13–14 cheapest; 12 cheaper than 15 → leftover at tail of 12."""
    _, sessions = _plan(_hours({13: 0.20, 14: 0.20, 12: 0.30, 15: 0.40}))
    assert len(sessions) == 1
    s = sessions[0]
    assert s["start"] == datetime(2026, 8, 14, 12, 45)
    assert s["end"] == datetime(2026, 8, 14, 15, 0)
    assert is_in_session(sessions, NOON) is False
    assert is_in_session(sessions, datetime(2026, 8, 14, 12, 45)) is True


def test_extra_15min_on_cheaper_hour_15_is_1300_to_1515():
    """13–14 cheapest; 15 cheaper than 12 → leftover at start of 15."""
    _, sessions = _plan(_hours({13: 0.20, 14: 0.20, 12: 0.40, 15: 0.30}))
    assert len(sessions) == 1
    s = sessions[0]
    assert s["start"] == datetime(2026, 8, 14, 13, 0)
    assert s["end"] == datetime(2026, 8, 14, 15, 15)
    assert is_in_session(sessions, NOON) is False
    assert is_in_session(sessions, datetime(2026, 8, 14, 13, 0)) is True


def test_extra_15min_on_isolated_cheaper_hour_is_separate_session():
    """Hour 22 cheaper than shoulders 12 and 15 (but not than 13–14).

    Extra 15 min goes to 22, not glued onto 12 or 15.
    """
    schedule, sessions = _plan(
        _hours({13: 0.20, 14: 0.20, 22: 0.25, 12: 0.30, 15: 0.30})
    )
    hours = {s["hour"] for s in schedule}
    assert 13 in hours and 14 in hours
    assert 22 in hours
    assert 12 not in hours and 15 not in hours

    starts = sorted((s["start"], s["end"]) for s in sessions)
    assert (datetime(2026, 8, 14, 13, 0), datetime(2026, 8, 14, 15, 0)) in starts
    # 15 min tail-packed in hour 22
    assert (datetime(2026, 8, 14, 22, 45), datetime(2026, 8, 14, 23, 0)) in starts


def test_wait_until_tail_start_do_not_charge_at_noon():
    """12:45–15:00 plan: at 12:00 still waiting (no snap-to-now)."""
    _, sessions = _plan(_hours({13: 0.20, 14: 0.20, 12: 0.30, 15: 0.40}))
    reason, should, _ = determine_reason(
        mode=MODE_SMART,
        is_home=True,
        cable_connected=True,
        current_soc=55,
        target_soc=80,
        min_soc=30,
        charge_start_soc=69,
        charge_amps=11,
        sessions=sessions,
        now_dt=NOON,
        E_needed=NEED,
        schedule_all_prices_above_max=False,
        charging_started=False,
    )
    assert should is False
    assert reason == REASON_WAITING_FOR_SESSION


def test_started_session_runs_without_breaks_if_knapsack_drops_hour():
    """Already running 12:45–15:00; replan later prefers 13–15 only.

    Must keep charging (locked end) — no Remote-off mid-block.
    """
    later_only = assign_session_amps(
        compute_sessions(
            [
                {"hour": 13, "minutes": 60, "kWh": 8.0, "cost": 1.6, "full": True},
                {"hour": 14, "minutes": 60, "kWh": 8.0, "cost": 1.6, "full": True},
            ],
            TODAY,
        ),
        11,
    )
    now = datetime(2026, 8, 14, 12, 50)
    assert is_in_session(later_only, now) is False
    reason, should, amps = determine_reason(
        mode=MODE_SMART,
        is_home=True,
        cable_connected=True,
        current_soc=58,
        target_soc=80,
        min_soc=30,
        charge_start_soc=69,
        charge_amps=11,
        sessions=later_only,
        now_dt=now,
        E_needed=16.0,
        schedule_all_prices_above_max=False,
        charging_started=True,
        locked_session_end=datetime(2026, 8, 14, 15, 0),
    )
    assert reason == REASON_SCHEDULED
    assert should is True
    assert amps == 11
