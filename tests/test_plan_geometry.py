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
    drop_short_sessions,
    frozen_plan_stale,
    should_start_replan,
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


def _sessions_from(schedule, now=NOON):
    return assign_session_amps(compute_sessions(schedule, TODAY), 11)


def test_late_plug_1245_extends_into_third_cheapest_hour():
    """Need 90 min. 13 cheapest, 12 second, 14 third. Original 12:30–14:00.

    Plug in at 12:45 → remaining of 12 + 13 + 15 min of 14 = 12:45–14:15.
    """
    grid = _hours({13: 0.20, 12: 0.30, 14: 0.40, 15: 1.00})
    now = datetime(2026, 8, 14, 12, 45)
    # 90 min at 8 kWh/h = 12 kWh
    schedule = build_schedule(
        grid, E_needed=12.0, max_kWh_per_hour=RATE, now_dt=now,
        deadline_hours=12, prefer_current_hour=True,
    )
    sessions = drop_short_sessions(_sessions_from(schedule, now))
    assert len(sessions) == 1
    assert sessions[0]["start"] == datetime(2026, 8, 14, 12, 45)
    assert sessions[0]["end"] == datetime(2026, 8, 14, 14, 15)
    assert is_in_session(sessions, now) is True


def test_late_plug_uses_current_hour_then_tomorrow_not_a_one_minute_slice():
    """12:45 plugged in; 14 today expensive; tomorrow 13 is next cheapest.

    Use 12:45–14:00 now, remainder tomorrow as a ≥10 min block — never 13:00–13:01.
    """
    grid = _hours({13: 0.20, 12: 0.30, 14: 0.90, 15: 1.00})
    grid = grid + [{"hour": 24 + 13, "price": 0.20}, {"hour": 24 + 14, "price": 0.90}]
    now = datetime(2026, 8, 14, 12, 45)
    schedule = build_schedule(
        grid, E_needed=12.0, max_kWh_per_hour=RATE, now_dt=now,
        deadline_hours=36, prefer_current_hour=True,
    )
    sessions = drop_short_sessions(_sessions_from(schedule, now))
    starts = sorted((s["start"], s["end"]) for s in sessions)
    assert starts[0] == (
        datetime(2026, 8, 14, 12, 45),
        datetime(2026, 8, 14, 14, 0),
    )
    assert all(
        (e - s).total_seconds() / 60.0 > 10
        for s, e in starts
    )


def test_current_hour_at_1335_cannot_claim_1300_to_1335():
    """19-09: planner at 13:35 must not publish 13:00–14:21 as if 13:00 ran."""
    grid = _hours({13: 0.22, 14: 0.22, 15: 0.49})
    now = datetime(2026, 8, 14, 13, 35)
    schedule = build_schedule(
        grid, E_needed=10.8, max_kWh_per_hour=RATE, now_dt=now,
        deadline_hours=12, prefer_current_hour=True,
    )
    sessions = drop_short_sessions(_sessions_from(schedule, now))
    assert len(sessions) == 1
    assert sessions[0]["start"] == datetime(2026, 8, 14, 13, 35)
    assert sessions[0]["end"] > datetime(2026, 8, 14, 14, 21)
    assert is_in_session(sessions, datetime(2026, 8, 14, 14, 21)) is True
    assert is_in_session(sessions, datetime(2026, 8, 14, 14, 28)) is True


def test_drop_short_sessions_kills_ten_minute_stub():
    sessions = [
        {
            "start": datetime(2026, 8, 14, 13, 50),
            "end": datetime(2026, 8, 14, 14, 0),
            "total_kWh": 1.0,
            "total_cost": 0.2,
        },
        {
            "start": datetime(2026, 8, 15, 12, 40),
            "end": datetime(2026, 8, 15, 14, 0),
            "total_kWh": 10.0,
            "total_cost": 2.0,
        },
    ]
    kept = drop_short_sessions(sessions)
    assert len(kept) == 1
    assert kept[0]["start"] == datetime(2026, 8, 15, 12, 40)


def test_eight_minute_stub_glued_to_next_hour_is_kept():
    """≤10 min of the current hour + a full cheap next hour is one long block."""
    now = datetime(2026, 8, 14, 13, 52)
    grid = _hours({13: 0.22, 14: 0.22, 15: 0.90})
    schedule = build_schedule(
        grid, E_needed=8.0, max_kWh_per_hour=RATE, now_dt=now,
        deadline_hours=6, prefer_current_hour=True,
    )
    sessions = drop_short_sessions(_sessions_from(schedule, now))
    assert len(sessions) == 1
    assert sessions[0]["start"] == datetime(2026, 8, 14, 13, 52)
    assert (sessions[0]["end"] - sessions[0]["start"]).total_seconds() > 10 * 60


def test_frozen_plan_does_not_slide_every_minute():
    frozen = [{
        "start": datetime(2026, 8, 14, 12, 31),
        "end": datetime(2026, 8, 14, 14, 1),
    }]
    sig = ("smart", 80)
    hour = (TODAY, 12)
    assert frozen_plan_stale(
        frozen=frozen, frozen_hour=hour, frozen_signature=sig,
        now=datetime(2026, 8, 14, 12, 32), signature=sig,
        charging_started=False, locked_session_end=None,
    ) is None
    assert frozen_plan_stale(
        frozen=frozen, frozen_hour=hour, frozen_signature=sig,
        now=datetime(2026, 8, 14, 13, 5), signature=sig,
        charging_started=False, locked_session_end=None,
    ) == "hour"


def test_frozen_plan_drops_dying_stub():
    frozen = [{
        "start": datetime(2026, 8, 14, 13, 0),
        "end": datetime(2026, 8, 14, 14, 0),
    }]
    sig = ("smart", 80)
    assert frozen_plan_stale(
        frozen=frozen, frozen_hour=(TODAY, 13), frozen_signature=sig,
        now=datetime(2026, 8, 14, 13, 52), signature=sig,
        charging_started=False, locked_session_end=None,
    ) == "short"


def test_start_replan_only_inside_window_with_cable():
    sessions = [{
        "start": datetime(2026, 8, 14, 12, 45),
        "end": datetime(2026, 8, 14, 14, 0),
    }]
    kw = dict(
        charging_started=False, start_replan_done=False,
        cable_connected=True, is_home=True, sessions=sessions,
    )
    assert should_start_replan(now=datetime(2026, 8, 14, 12, 0), **kw) is False
    assert should_start_replan(now=datetime(2026, 8, 14, 12, 45), **kw) is True
    assert should_start_replan(
        now=datetime(2026, 8, 14, 12, 45), **{**kw, "start_replan_done": True}
    ) is False
