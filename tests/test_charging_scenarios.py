"""
End-to-end charging scenarios from real production incidents.

Glue: build_schedule → compute_sessions → assign_session_amps → determine_reason
plus override set_mode / restore. Failures here = don't redeploy trust.
"""
from __future__ import annotations

from datetime import datetime, date, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from custom_components.myszolot.coordinator import (
    build_schedule,
    build_asap_schedule,
    compute_sessions,
    assign_session_amps,
    is_in_session,
    next_session,
    session_target_amps,
    determine_reason,
    MyszolotCoordinator,
)
from custom_components.myszolot.const import (
    MODE_SMART,
    MODE_OVERRIDE,
    REASON_SCHEDULED,
    REASON_WAITING_FOR_SESSION,
    REASON_MIN_SOC_FLOOR,
    REASON_PRICE_TOO_HIGH,
    REASON_TARGET_REACHED,
    REASON_SOC_SUFFICIENT,
    REASON_NO_ELIGIBLE_HOURS,
    REASON_SOC_UNKNOWN,
    REASON_OUTSIDE_NOT_CHARGING,
    INPUT_NUMBER_CUSTOM_TARGET_SOC,
    INPUT_NUMBER_DEADLINE_HOURS,
    INPUT_NUMBER_DEADLINE_MINUTES,
    INPUT_NUMBER_MIN_SOC,
    INPUT_NUMBER_MAX_PRICE_THRESHOLD,
    INPUT_BOOLEAN_LOCATION_OVERRIDE,
    SENSOR_PRICE,
    SENSOR_SOC,
    SENSOR_CHARGING,
    SWITCH_AUTEL_CHARGE_CONTROL,
    BINARY_SENSOR_CABLE,
    BINARY_SENSOR_GARAGE_CAR,
    DEVICE_TRACKER,
    NUMBER_CHARGE_LIMIT,
)

TODAY = date(2024, 1, 15)
MAX_KWH = 7.59  # ~11 A × 230 × 3 / 1000


def prices(*pairs: tuple[int, float]) -> list[dict]:
    return [{"hour": h, "price": p} for h, p in pairs]


# ── Full-rate sessions (no amp flatten) ──────────────────────────────────────

def test_assign_session_amps_always_full_rate():
    """Every planned session stamps fixed wall amps; never mid-range flatten."""
    schedule = [
        {"hour": 13, "minutes": 20, "kWh": 3.0, "cost": 1.0, "full": False},
        {"hour": 14, "minutes": 15, "kWh": 2.0, "cost": 1.0, "full": False},
    ]
    sessions = assign_session_amps(compute_sessions(schedule, TODAY), 11)
    assert len(sessions) == 1
    assert sessions[0]["amps"] == 11
    assert sessions[0]["flattened"] is False
    now = datetime(2024, 1, 15, 13, 5)
    assert session_target_amps(sessions, now, 11) == 11


def test_tail_pack_expensive_remainder_before_cheap_hours():
    """14-08: fill 13–14 fully, leftover ~16 min of hour 12 at the tail → 12:44–15:00.

    Must NOT start at 12:00 (snap-to-now was the regression).
    """
    schedule = [
        {"hour": 12, "minutes": 16, "kWh": 2.0, "cost": 1.2, "full": False},
        {"hour": 13, "minutes": 60, "kWh": 7.6, "cost": 3.0, "full": True},
        {"hour": 14, "minutes": 60, "kWh": 7.6, "cost": 3.1, "full": True},
    ]
    ref = datetime(2026, 8, 14).date()
    s = compute_sessions(schedule, ref)[0]
    assert s["start"] == datetime(2026, 8, 14, 12, 44)
    assert s["end"] == datetime(2026, 8, 14, 15, 0)
    noon = datetime(2026, 8, 14, 12, 0)
    assert is_in_session([s], noon) is False
    reason, should, _ = determine_reason(
        mode=MODE_SMART,
        is_home=True,
        cable_connected=True,
        current_soc=55,
        target_soc=80,
        min_soc=30,
        charge_start_soc=69,
        charge_amps=11,
        sessions=assign_session_amps([s], 11),
        now_dt=noon,
        E_needed=17.2,
        schedule_all_prices_above_max=False,
        charging_started=False,
    )
    assert should is False
    assert reason == REASON_WAITING_FOR_SESSION


def test_hold_keeps_charging_if_start_slides_two_minutes():
    """Already charging: 5 min hold covers a 2 min start slide."""
    from custom_components.myszolot.coordinator import is_holding_session

    sessions = [{
        "start": datetime(2026, 8, 13, 2, 33),
        "end": datetime(2026, 8, 13, 4, 0),
        "amps": 11,
    }]
    now = datetime(2026, 8, 13, 2, 31)
    assert is_in_session(sessions, now) is False
    assert is_holding_session(sessions, now) is True
    reason, should, amps = determine_reason(
        mode=MODE_OVERRIDE,
        is_home=True,
        cable_connected=True,
        current_soc=54,
        target_soc=70,
        min_soc=30,
        charge_start_soc=69,
        charge_amps=11,
        sessions=sessions,
        now_dt=now,
        E_needed=10.0,
        schedule_all_prices_above_max=False,
        charging_started=True,
    )
    assert reason == REASON_SCHEDULED
    assert should is True
    assert amps == 11


def test_hold_does_not_bridge_to_later_cheap_hour():
    """Real planned gap (next session 1h later) must still wait."""
    from custom_components.myszolot.coordinator import is_holding_session

    sessions = [{
        "start": datetime(2026, 8, 13, 4, 0),
        "end": datetime(2026, 8, 13, 5, 0),
        "amps": 11,
    }]
    now = datetime(2026, 8, 13, 3, 1)
    assert is_holding_session(sessions, now) is False
    reason, should, _ = determine_reason(
        mode=MODE_OVERRIDE,
        is_home=True,
        cable_connected=True,
        current_soc=62,
        target_soc=70,
        min_soc=30,
        charge_start_soc=69,
        charge_amps=11,
        sessions=sessions,
        now_dt=now,
        E_needed=5.0,
        schedule_all_prices_above_max=False,
        charging_started=True,
    )
    assert should is False
    assert reason == REASON_WAITING_FOR_SESSION


def test_locked_session_keeps_charging_when_knapsack_drops_current_hour():
    """Incident 14-08: at 12:12 knapsack preferred 13:00–15:00 and stopped Autel.

    Once a contiguous block was started (lock end 14:16), keep charging.
    """
    later_only = [{
        "start": datetime(2026, 8, 14, 13, 0),
        "end": datetime(2026, 8, 14, 14, 59),
        "amps": 11,
    }]
    now = datetime(2026, 8, 14, 12, 12)
    locked = datetime(2026, 8, 14, 14, 16)
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
        E_needed=15.0,
        schedule_all_prices_above_max=False,
        charging_started=True,
        locked_session_end=locked,
    )
    assert reason == REASON_SCHEDULED
    assert should is True
    assert amps == 11


def test_locked_session_expires_at_end():
    later_only = [{
        "start": datetime(2026, 8, 14, 15, 0),
        "end": datetime(2026, 8, 14, 16, 0),
        "amps": 11,
    }]
    now = datetime(2026, 8, 14, 14, 16)
    reason, should, _ = determine_reason(
        mode=MODE_SMART,
        is_home=True,
        cable_connected=True,
        current_soc=70,
        target_soc=80,
        min_soc=30,
        charge_start_soc=69,
        charge_amps=11,
        sessions=later_only,
        now_dt=now,
        E_needed=7.0,
        schedule_all_prices_above_max=False,
        charging_started=True,
        locked_session_end=datetime(2026, 8, 14, 14, 16),
    )
    assert should is False
    assert reason == REASON_WAITING_FOR_SESSION


# ── Override: cheapest in window, may wait ───────────────────────────────────

def test_override_picks_cheapest_hour_not_asap():
    """Override may wait for cheaper later hour inside the window (not forced ASAP)."""
    now = datetime(2024, 1, 15, 12, 0)
    all_prices = prices((12, 1.5), (13, 0.3), (14, 0.4), (15, 0.9))
    schedule = build_schedule(
        all_prices, E_needed=8.0, max_kWh_per_hour=10.0, now_dt=now, deadline_hours=3
    )
    assert schedule
    hours = {s["hour"] for s in schedule}
    assert 13 in hours
    assert 12 not in hours  # expensive now skipped
    sessions = compute_sessions(schedule, TODAY)
    assert is_in_session(sessions, now) is False
    assert next_session(sessions, now) is not None
    reason, should, amps = determine_reason(
        mode=MODE_OVERRIDE,
        is_home=True,
        cable_connected=True,
        current_soc=50,
        target_soc=80,
        min_soc=30,
        charge_start_soc=69,
        charge_amps=12,
        sessions=sessions,
        now_dt=now,
        E_needed=8.0,
        schedule_all_prices_above_max=False,
    )
    assert reason == REASON_WAITING_FOR_SESSION
    assert should is False
    assert amps == 0


def test_asap_path_still_starts_immediately_when_used():
    """build_asap_schedule (urgency path) starts now — contrast with knapsack wait."""
    now = datetime(2024, 1, 15, 12, 12)
    plan = build_asap_schedule(15.0, 10.0, now, deadline_end=now + timedelta(hours=2))
    sessions = compute_sessions(plan, TODAY)
    assert is_in_session(sessions, now) is True
    assert next_session(sessions, now) is None


def test_override_uses_expensive_hour_if_only_option_in_window():
    """No price hard-stop: expensive hour still eligible when max_price=None."""
    now = datetime(2024, 1, 15, 12, 0)
    all_prices = prices((12, 2.0), (13, 2.1))
    uncapped = build_schedule(
        all_prices, E_needed=5.0, max_kWh_per_hour=10.0, now_dt=now,
        deadline_hours=2, max_price=None,
    )
    capped = build_schedule(
        all_prices, E_needed=5.0, max_kWh_per_hour=10.0, now_dt=now,
        deadline_hours=2, max_price=1.0,
    )
    assert uncapped  # override path
    assert capped == []  # smart would skip


def test_smart_skips_hours_above_price_cap():
    now = datetime(2024, 1, 15, 12, 0)
    all_prices = prices((12, 1.5), (13, 0.5), (14, 1.2))
    schedule = build_schedule(
        all_prices, E_needed=8.0, max_kWh_per_hour=10.0, now_dt=now,
        deadline_hours=4, max_price=1.0,
    )
    assert all(s["hour"] != 12 for s in schedule)
    assert all(s["hour"] != 14 for s in schedule)
    assert any(s["hour"] == 13 for s in schedule)


def test_smart_price_too_high_reason_when_all_capped():
    reason, should, _ = determine_reason(
        mode=MODE_SMART,
        is_home=True,
        cable_connected=True,
        current_soc=50,
        target_soc=80,
        min_soc=30,
        charge_start_soc=69,
        charge_amps=12,
        sessions=[],
        now_dt=datetime(2024, 1, 15, 12, 0),
        E_needed=20.0,
        schedule_all_prices_above_max=True,
    )
    assert reason == REASON_PRICE_TOO_HIGH
    assert should is False


# ── Session amps / emergency ─────────────────────────────────────────────────

def test_scheduled_uses_session_amps_not_always_full():
    now = datetime(2024, 1, 15, 14, 30)
    sessions = [{
        "start": datetime(2024, 1, 15, 14, 0),
        "end": datetime(2024, 1, 15, 16, 0),
        "slots": [],
        "total_kWh": 10,
        "total_cost": 1,
        "amps": 7,
        "flattened": True,
    }]
    reason, should, amps = determine_reason(
        mode=MODE_OVERRIDE,
        is_home=True,
        cable_connected=True,
        current_soc=50,
        target_soc=90,
        min_soc=30,
        charge_start_soc=69,
        charge_amps=12,
        sessions=sessions,
        now_dt=now,
        E_needed=20,
        schedule_all_prices_above_max=False,
    )
    assert reason == REASON_SCHEDULED
    assert should is True
    assert amps == 7
    assert session_target_amps(sessions, now, 12) == 7


def test_session_target_amps_defaults_outside_session():
    sessions = [{
        "start": datetime(2024, 1, 15, 14, 0),
        "end": datetime(2024, 1, 15, 16, 0),
        "amps": 7,
    }]
    assert session_target_amps(sessions, datetime(2024, 1, 15, 10, 0), 12) == 12


def test_min_soc_floor_full_amps_ignores_waiting_sessions():
    """Emergency charges at full charge_amps even if sessions say wait."""
    now = datetime(2024, 1, 15, 10, 0)
    sessions = [{
        "start": datetime(2024, 1, 15, 22, 0),
        "end": datetime(2024, 1, 15, 23, 0),
        "slots": [],
        "amps": 6,
        "flattened": True,
    }]
    reason, should, amps = determine_reason(
        mode=MODE_SMART,
        is_home=True,
        cable_connected=True,
        current_soc=15,
        target_soc=80,
        min_soc=30,
        charge_start_soc=69,
        charge_amps=12,
        sessions=sessions,
        now_dt=now,
        E_needed=40,
        schedule_all_prices_above_max=True,
    )
    assert reason == REASON_MIN_SOC_FLOOR
    assert should is True
    assert amps == 12  # full, not session 6


def test_override_ignores_soc_sufficient_gate():
    """Override does not stop at charge_start_soc — only target matters."""
    reason, should, _ = determine_reason(
        mode=MODE_OVERRIDE,
        is_home=True,
        cable_connected=True,
        current_soc=72,
        target_soc=95,
        min_soc=30,
        charge_start_soc=69,
        charge_amps=12,
        sessions=[{
            "start": datetime(2024, 1, 15, 12, 0),
            "end": datetime(2024, 1, 15, 14, 0),
            "amps": 12,
        }],
        now_dt=datetime(2024, 1, 15, 13, 0),
        E_needed=15.0,
        schedule_all_prices_above_max=False,
    )
    assert reason == REASON_SCHEDULED
    assert should is True


def test_smart_stops_above_charge_start_soc_when_not_started():
    reason, should, _ = determine_reason(
        mode=MODE_SMART,
        is_home=True,
        cable_connected=True,
        current_soc=72,
        target_soc=80,
        min_soc=30,
        charge_start_soc=69,
        charge_amps=12,
        sessions=[],
        now_dt=datetime(2024, 1, 15, 12, 0),
        E_needed=5.0,
        schedule_all_prices_above_max=False,
        charging_started=False,
    )
    assert reason == REASON_SOC_SUFFICIENT
    assert should is False


# ── set_mode: button full replan vs restart restore ──────────────────────────

class _FakeStore:
    def __init__(self, initial=None):
        self.data = initial or {}

    async def async_save(self, data):
        self.data = dict(data or {})

    async def async_load(self):
        return dict(self.data)


def _fake_hass_with_helpers(*, target=94, hours=2, minutes=None):
    hass = MagicMock()
    states = {}

    def _state(eid, value):
        states[eid] = SimpleNamespace(state=str(value), attributes={})

    _state(INPUT_NUMBER_CUSTOM_TARGET_SOC, target)
    _state(INPUT_NUMBER_DEADLINE_HOURS, hours)
    if minutes is not None:
        _state(INPUT_NUMBER_DEADLINE_MINUTES, minutes)

    hass.states.get = lambda eid: states.get(eid)

    import asyncio

    def _create_task(coro):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(coro)
            finally:
                loop.close()
        return asyncio.ensure_future(coro)

    hass.async_create_task = _create_task
    return hass


def _make_coord(hass, store=None):
    entry = MagicMock()
    entry.data = {}
    entry.options = {}
    coord = MyszolotCoordinator(hass, entry)
    if store is not None:
        coord._override_store = store
    return coord


def test_button_override_always_sets_fresh_absolute_deadline():
    """Start override = full replan: target from helper, deadline = now + window."""
    coord = _make_coord(_fake_hass_with_helpers(target=94, hours=2), _FakeStore())
    coord.set_mode(MODE_OVERRIDE)
    first_deadline = coord._override_deadline
    assert first_deadline is not None
    assert coord._override_target_soc == 94
    remaining = (first_deadline - datetime.now()).total_seconds()
    assert 100 * 60 < remaining <= 121 * 60  # ~2h

    # Second press with new target + 3h helper → full replan
    coord.hass = _fake_hass_with_helpers(target=96, hours=3)
    old = coord._override_deadline
    coord.set_mode(MODE_OVERRIDE)
    assert coord._override_target_soc == 96
    assert coord.mode == MODE_OVERRIDE
    remaining2 = (coord._override_deadline - datetime.now()).total_seconds()
    assert remaining2 > remaining  # longer window rewritten
    assert remaining2 > 150 * 60


def test_button_override_rewrites_deadline_from_now():
    """Even if already in override with far deadline, button resets to now+N."""
    hass = _fake_hass_with_helpers(target=90, hours=2)
    coord = _make_coord(hass, _FakeStore())
    # Stale far deadline as if leftover
    coord._mode = MODE_OVERRIDE
    coord._override_deadline = datetime.now() + timedelta(hours=20)
    coord._override_target_soc = 80
    coord.set_mode(MODE_OVERRIDE)
    remaining = (coord._override_deadline - datetime.now()).total_seconds()
    assert remaining < 3 * 3600  # 2h window, not 20h
    assert coord._override_target_soc == 90


@pytest.mark.asyncio
async def test_restart_restores_absolute_deadline_not_fresh_window():
    """HA restart loads persisted absolute end — does not mint now+2h."""
    fixed_deadline = datetime.now() + timedelta(hours=6)
    store = _FakeStore({
        "mode": MODE_OVERRIDE,
        "target_soc": 94,
        "deadline": fixed_deadline.isoformat(),
        "deadline_minutes": 120,
    })
    hass = _fake_hass_with_helpers(target=80, hours=2)  # helpers would say 80/2h
    coord = _make_coord(hass, store)
    await coord._async_restore_override()
    assert coord.mode == MODE_OVERRIDE
    assert coord._override_target_soc == 94
    assert coord._override_deadline == fixed_deadline.replace(tzinfo=None) or (
        abs((coord._override_deadline - fixed_deadline).total_seconds()) < 1
    )


@pytest.mark.asyncio
async def test_restart_clears_expired_override():
    store = _FakeStore({
        "mode": MODE_OVERRIDE,
        "target_soc": 94,
        "deadline": (datetime.now() - timedelta(hours=1)).isoformat(),
    })
    coord = _make_coord(_fake_hass_with_helpers(), store)
    await coord._async_restore_override()
    assert coord.mode == MODE_SMART
    assert coord._override_deadline is None


@pytest.mark.asyncio
async def test_restart_ignores_smart_mode_storage():
    store = _FakeStore({"mode": MODE_SMART, "target_soc": 94})
    coord = _make_coord(_fake_hass_with_helpers(), store)
    await coord._async_restore_override()
    assert coord.mode == MODE_SMART
    assert coord._override_deadline is None


def test_set_mode_smart_clears_override_state():
    """Override → smart is delayed; timer fire applies smart."""
    hass = _fake_hass_with_helpers(target=95, hours=2)
    coord = _make_coord(hass, _FakeStore())
    coord.set_mode(MODE_OVERRIDE)
    assert coord.mode == MODE_OVERRIDE
    coord.set_mode(MODE_SMART)
    # Still override until delay fires
    assert coord.mode == MODE_OVERRIDE
    assert coord.pending_smart is True
    assert coord._pending_smart_fire is not None
    coord._pending_smart_fire()
    assert coord.mode == MODE_SMART
    assert coord._override_deadline is None
    assert coord._override_target_soc is None
    assert coord.pending_smart is False


def test_pending_smart_cancelled_by_override_keeps_deadline():
    """Mis-tap smart then override again: stay in override, no full replan."""
    hass = _fake_hass_with_helpers(target=90, hours=3)
    coord = _make_coord(hass, _FakeStore())
    coord.set_mode(MODE_OVERRIDE)
    deadline = coord._override_deadline
    target = coord._override_target_soc
    assert deadline is not None
    assert target == 90

    # Simulate 1h already consumed
    coord._override_deadline = deadline - timedelta(hours=1)
    kept = coord._override_deadline

    coord.set_mode(MODE_SMART)
    assert coord.pending_smart is True
    assert coord.mode == MODE_OVERRIDE

    # Change helpers as if user edited for a "new" override — cancel must ignore
    coord.hass = _fake_hass_with_helpers(target=99, hours=5)
    coord.set_mode(MODE_OVERRIDE)

    assert coord.pending_smart is False
    assert coord.mode == MODE_OVERRIDE
    assert coord._override_target_soc == 90  # not 99
    assert coord._override_deadline == kept  # not fresh 5h window


def test_override_while_not_pending_still_full_replans():
    """Normal second override press (no pending smart) still full-replans."""
    hass = _fake_hass_with_helpers(target=90, hours=2)
    coord = _make_coord(hass, _FakeStore())
    coord.set_mode(MODE_OVERRIDE)
    first = coord._override_deadline
    coord.hass = _fake_hass_with_helpers(target=95, hours=4)
    coord.set_mode(MODE_OVERRIDE)
    assert coord._override_target_soc == 95
    remaining = (coord._override_deadline - datetime.now()).total_seconds()
    assert remaining > 200 * 60
    assert coord._override_deadline != first


def test_deadline_hours_is_ui_source_not_minutes():
    """Dashboard uses hours; minutes helper must not override when hours exist."""
    hass = _fake_hass_with_helpers(target=90, hours=5, minutes=90)
    coord = _make_coord(hass, _FakeStore())
    assert coord._read_deadline_minutes() == 5 * 60  # not 90


def test_deadline_hours_when_no_minutes_helper():
    hass = _fake_hass_with_helpers(target=90, hours=3, minutes=None)
    coord = _make_coord(hass, _FakeStore())
    assert coord._read_deadline_minutes() == 180


def test_deadline_minutes_fallback_when_no_hours_helper():
    hass = MagicMock()
    hass.states.get = lambda eid: {
        INPUT_NUMBER_CUSTOM_TARGET_SOC: SimpleNamespace(state="90", attributes={}),
        INPUT_NUMBER_DEADLINE_MINUTES: SimpleNamespace(state="90", attributes={}),
    }.get(eid)
    import asyncio

    def _create_task(coro):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(coro)
            finally:
                loop.close()
        return asyncio.ensure_future(coro)

    hass.async_create_task = _create_task
    coord = _make_coord(hass, _FakeStore())
    assert coord._read_deadline_minutes() == 90


def test_deadline_hours_clamped():
    hass = _fake_hass_with_helpers(target=90, hours=999, minutes=None)
    coord = _make_coord(hass, _FakeStore())
    assert coord._read_deadline_minutes() == 48 * 60


# ── Pipeline: override window + hard_end ─────────────────────────────────────

def test_full_pipeline_override_respects_absolute_end():
    now = datetime(2024, 1, 15, 12, 0)
    hard_end = now + timedelta(hours=2)  # 14:00 absolute
    all_prices = prices(*((h, 0.5 + (h % 3) * 0.1) for h in range(12, 20)))
    schedule = build_schedule(
        all_prices, E_needed=15.0, max_kWh_per_hour=10.0, now_dt=now,
        deadline_hours=2, max_price=None,
    )
    sessions = compute_sessions(schedule, TODAY)
    sessions = assign_session_amps(sessions, 12)
    for s in sessions:
        assert s["end"] <= hard_end + timedelta(seconds=1)


def test_pipeline_smart_price_cap_then_wait_or_charge():
    """Smart: knapsack under cap → waiting until cheap hour → charge at session amps."""
    now = datetime(2024, 1, 15, 10, 0)
    all_prices = prices(
        (10, 1.2), (11, 1.1), (12, 0.9), (13, 0.4), (14, 0.5), (15, 0.8)
    )
    schedule = build_schedule(
        all_prices, E_needed=10.0, max_kWh_per_hour=10.0, now_dt=now,
        deadline_hours=8, max_price=1.0,
    )
    assert all(s["price"] <= 1.0 if "price" in s else True for s in schedule)
    assert all(s["hour"] >= 12 for s in schedule)  # expensive 10–11 skipped via cap
    sessions = compute_sessions(schedule, TODAY)
    sessions = assign_session_amps(sessions, 12)
    assert is_in_session(sessions, now) is False
    reason, should, _ = determine_reason(
        mode=MODE_SMART,
        is_home=True,
        cable_connected=True,
        current_soc=50,
        target_soc=80,
        min_soc=30,
        charge_start_soc=69,
        charge_amps=12,
        sessions=sessions,
        now_dt=now,
        E_needed=10.0,
        schedule_all_prices_above_max=False,
    )
    assert reason == REASON_WAITING_FOR_SESSION
    assert should is False
    # When cheap hour arrives
    mid = sessions[0]["start"] + timedelta(minutes=1)
    reason2, should2, amps2 = determine_reason(
        mode=MODE_SMART,
        is_home=True,
        cable_connected=True,
        current_soc=50,
        target_soc=80,
        min_soc=30,
        charge_start_soc=69,
        charge_amps=12,
        sessions=sessions,
        now_dt=mid,
        E_needed=10.0,
        schedule_all_prices_above_max=False,
    )
    assert reason2 == REASON_SCHEDULED
    assert should2 is True
    assert amps2 == 12  # session stamped full rate


def test_target_soc_gate_when_not_in_session():
    """E_needed=0 and no active session → target_reached."""
    reason, should, _ = determine_reason(
        mode=MODE_OVERRIDE,
        is_home=True,
        cable_connected=True,
        current_soc=96,
        target_soc=96,
        min_soc=30,
        charge_start_soc=69,
        charge_amps=12,
        sessions=[],
        now_dt=datetime(2024, 1, 15, 13, 0),
        E_needed=0.0,
        schedule_all_prices_above_max=False,
    )
    assert reason == REASON_TARGET_REACHED
    assert should is False


def test_no_eligible_hours_when_empty_schedule():
    reason, should, _ = determine_reason(
        mode=MODE_OVERRIDE,
        is_home=True,
        cable_connected=True,
        current_soc=50,
        target_soc=90,
        min_soc=30,
        charge_start_soc=69,
        charge_amps=12,
        sessions=[],
        now_dt=datetime(2024, 1, 15, 12, 0),
        E_needed=20.0,
        schedule_all_prices_above_max=False,
    )
    assert reason == REASON_NO_ELIGIBLE_HOURS
    assert should is False


def test_persist_override_writes_absolute_deadline():
    """set_mode(override) persists mode/target/deadline for restart restore."""
    hass = _fake_hass_with_helpers(target=93, hours=2)
    store = _FakeStore()
    coord = _make_coord(hass, store)
    coord.set_mode(MODE_OVERRIDE)
    assert store.data.get("mode") == MODE_OVERRIDE
    assert store.data.get("target_soc") == 93
    assert "deadline" in store.data
    # ISO parseable absolute time
    dl = datetime.fromisoformat(store.data["deadline"])
    assert dl > datetime.now()


def test_persist_smart_clears_storage():
    hass = _fake_hass_with_helpers(target=93, hours=2)
    store = _FakeStore({"mode": MODE_OVERRIDE, "target_soc": 90})
    coord = _make_coord(hass, store)
    # Already smart at construct: selecting smart clears stale storage
    coord.set_mode(MODE_SMART)
    assert store.data == {}

    # Live override → smart (after pending fire) also clears
    coord.set_mode(MODE_OVERRIDE)
    assert store.data.get("mode") == MODE_OVERRIDE
    coord.set_mode(MODE_SMART)
    assert store.data.get("mode") == MODE_OVERRIDE  # still override while pending
    coord._pending_smart_fire()
    assert store.data == {}


def test_projected_miss_replan_threshold_design():
    """
    Design: replan if projected end SoC off by >3%.
    Pure helper not wired in coordinator yet — lock the intended rule.
    """
    def should_replan(expected_end: float, target: float, thr: float = 3.0) -> bool:
        return abs(expected_end - target) > thr

    assert should_replan(76, 80) is True
    assert should_replan(79, 80) is False
    assert should_replan(94, 94) is False
    assert should_replan(97, 94) is False  # exactly 3% → within band
    assert should_replan(98, 94) is True


# ── Realistic Autel numbers (12A, 3φ, 230V) ───────────────────────────────────

def test_realistic_energy_math_12a_three_phase():
    """max_kWh/h ≈ 8.28; 30% → 80% on 68.9 kWh pack needs ~34.45 kWh."""
    max_kwh = 12 * 230 * 3 / 1000
    assert max_kwh == pytest.approx(8.28)
    pack = 68.9
    e_needed = pack * (80 - 30) / 100
    assert e_needed == pytest.approx(34.45)
    hours_full = e_needed / max_kwh
    assert 4.0 < hours_full < 5.0


def test_realistic_override_two_hour_window_knapsack():
    """2h override window: only fill what fits; may leave gap if cheaper later in window."""
    now = datetime(2024, 1, 15, 18, 0)
    # Evening: 18 expensive, 19 cheap
    all_prices = prices((18, 0.9), (19, 0.35), (20, 0.4), (21, 0.5))
    # Need ~1h of energy at wall rate → only cheapest hour in window
    schedule = build_schedule(
        all_prices, E_needed=MAX_KWH * 0.9, max_kWh_per_hour=MAX_KWH, now_dt=now,
        deadline_hours=2, max_price=None,
    )
    hours = {s["hour"] for s in schedule}
    assert 19 in hours
    assert 18 not in hours  # waits for cheaper hour 19 within 2h
    sessions = compute_sessions(schedule, TODAY)
    sessions = assign_session_amps(sessions, 11)
    for s in sessions:
        assert s["end"] <= now + timedelta(hours=2) + timedelta(seconds=1)
    assert is_in_session(sessions, now) is False



_LADDER_DEFAULTS = dict(
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
)


def dr(**overrides) -> tuple:
    """determine_reason with ladder defaults overridden per test."""
    return determine_reason(**{**_LADDER_DEFAULTS, **overrides})


# ── Session guard lifecycle (1.5.8) ──────────────────────────────────────────
# Three field bugs, all in the wiring rather than the ladder, so these drive the
# real _async_update_data instead of calling determine_reason directly.

def _fake_hass_full(
    *,
    soc="62",
    cable="on",
    tracker="home",
    garage="on",
    charging="stopped",
    autel="off",
    car_limit="80",
    cheap_now=True,
):
    """hass carrying every entity the update loop reads.

    The price grid is built around the real clock (the loop uses datetime.now),
    so `cheap_now` decides whether a planned block contains this moment.
    """
    import asyncio

    hass = MagicMock()
    hass.data = {}  # pstryk lookup iterates this
    states = {}

    def _state(eid, value, attributes=None):
        states[eid] = SimpleNamespace(state=str(value), attributes=attributes or {})

    now = datetime.now()
    if cheap_now:
        cheap = {h for h in (now.hour, now.hour + 1) if h < 24}
    else:
        cheap = {h for h in (now.hour + 3, now.hour + 4) if h < 24}
    grid = [{"hour": h, "price": (0.10 if h in cheap else 5.00)} for h in range(24)]

    _state(SENSOR_PRICE, 0.10 if cheap_now else 5.00, {"All prices": grid})
    _state(SENSOR_SOC, soc)
    _state(BINARY_SENSOR_CABLE, cable)
    _state(DEVICE_TRACKER, tracker)
    _state(BINARY_SENSOR_GARAGE_CAR, garage)
    _state(SENSOR_CHARGING, charging)
    _state(SWITCH_AUTEL_CHARGE_CONTROL, autel)
    _state(NUMBER_CHARGE_LIMIT, car_limit)
    _state(INPUT_BOOLEAN_LOCATION_OVERRIDE, "off")
    _state(INPUT_NUMBER_MIN_SOC, 30)
    _state(INPUT_NUMBER_MAX_PRICE_THRESHOLD, 1.0)
    _state(INPUT_NUMBER_CUSTOM_TARGET_SOC, 80)
    _state(INPUT_NUMBER_DEADLINE_HOURS, 2)

    hass.states.get = lambda eid: states.get(eid)

    def _create_task(coro):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            coro.close()
            return None
        return asyncio.ensure_future(coro)

    hass.async_create_task = _create_task
    return hass


@pytest.mark.asyncio
async def test_guards_cleared_when_car_leaves_and_stale_lock_cannot_force_charge():
    """Block starts → car unplugged and driven off → home again inside the old
    block: the lock from the abandoned session must not charge against the new
    plan (it forced 'scheduled' at an hour the planner had rejected)."""
    coord = _make_coord(_fake_hass_full(), _FakeStore())

    data = await coord._async_update_data()
    assert data["reason"] == REASON_SCHEDULED
    assert coord._charging_started is True
    assert coord._locked_session_end is not None
    lock_was = coord._locked_session_end

    # Unplugged and gone (vision empty, tracker elsewhere)
    coord.hass = _fake_hass_full(cable="off", garage="off", tracker="not_home")
    await coord._async_update_data()
    assert coord._charging_started is False
    assert coord._locked_session_end is None

    # Back home, plugged in, but the cheap window is hours away now
    coord.hass = _fake_hass_full(cheap_now=False)
    data = await coord._async_update_data()
    assert datetime.now() < lock_was          # the old lock would still be live
    assert data["reason"] != REASON_SCHEDULED
    assert data["should_charge"] is False


@pytest.mark.asyncio
async def test_vision_blip_does_not_kill_live_home_charge():
    """20-08-2026 14:12: garage vision empty 30s while Tesla charging, Autel on,
    GPS home. Must not go outside_charging or clear session guards — otherwise
    soc_sufficient at 72% kills the rest of the cheap window."""
    coord = _make_coord(
        _fake_hass_full(soc="72", charging="charging", autel="on"),
        _FakeStore(),
    )
    data = await coord._async_update_data()
    assert data["reason"] == REASON_SCHEDULED
    assert data["should_charge"] is True
    assert coord._charging_started is True
    lock_was = coord._locked_session_end
    assert lock_was is not None

    coord.hass = _fake_hass_full(
        soc="72",
        charging="charging",
        autel="on",
        garage="off",
        tracker="home",
    )
    data = await coord._async_update_data()
    assert data["reason"] == REASON_SCHEDULED
    assert data["should_charge"] is True
    assert coord._charging_started is True
    assert coord._locked_session_end is not None


@pytest.mark.asyncio
async def test_vision_empty_without_live_charge_is_still_not_home():
    """Start gate unchanged: vision empty + GPS home, but Tesla/Autel idle,
    is not a home charge — do not invent a session."""
    coord = _make_coord(
        _fake_hass_full(soc="62", charging="stopped", autel="off", garage="off"),
        _FakeStore(),
    )
    data = await coord._async_update_data()
    assert data["should_charge"] is False
    assert data["reason"] == REASON_OUTSIDE_NOT_CHARGING
    assert coord._charging_started is False


@pytest.mark.asyncio
async def test_cable_sensor_blip_does_not_end_a_running_session():
    """'unavailable' is not 'unplugged' — a blip must never clear the guards,
    or it re-opens the mid-session Remote-off this suite exists to prevent."""
    coord = _make_coord(_fake_hass_full(), _FakeStore())
    await coord._async_update_data()
    assert coord._charging_started is True

    coord.hass = _fake_hass_full(cable="unavailable")
    data = await coord._async_update_data()
    assert coord._charging_started is True
    assert coord._locked_session_end is not None
    assert data["reason"] == REASON_SCHEDULED
    assert data["should_charge"] is True


@pytest.mark.asyncio
async def test_restart_adopts_a_live_session_instead_of_cutting_it():
    """HA restart mid-block: the guards are empty, so the SoC debounce applied
    again and stopped a session already running at 75 %. A car physically
    charging at home is adopted as started."""
    coord = _make_coord(_fake_hass_full(soc="75", charging="charging"), _FakeStore())
    assert coord._charging_started is False  # fresh process, as after a restart

    data = await coord._async_update_data()
    assert coord._charging_started is True
    assert data["reason"] == REASON_SCHEDULED
    assert data["should_charge"] is True

    # Not charging → no adoption, and 75 % > charge_start_soc means skip.
    fresh = _make_coord(_fake_hass_full(soc="75", charging="stopped"), _FakeStore())
    data = await fresh._async_update_data()
    assert fresh._charging_started is False
    assert data["reason"] == REASON_SOC_SUFFICIENT


@pytest.mark.asyncio
async def test_unavailable_soc_does_not_trigger_the_emergency_floor():
    """An unreadable SoC parsed as 0 % looked like an empty battery and charged
    at full amps at any price. It must plan nothing instead."""
    coord = _make_coord(_fake_hass_full(soc="unavailable"), _FakeStore())
    data = await coord._async_update_data()
    assert data["reason"] == REASON_SOC_UNKNOWN
    assert data["should_charge"] is False
    assert data["E_needed"] == 0.0
    assert data["sessions"] == []


def test_unknown_soc_blocks_the_floor_but_a_real_zero_still_charges():
    """The fix must not disable genuine emergency charging."""
    unknown = dr(
        soc_known=False, current_soc=0.0, min_soc=30, cable_connected=True,
        schedule_all_prices_above_max=True,
    )
    assert unknown == (REASON_SOC_UNKNOWN, False, 0)

    real_zero = dr(
        soc_known=True, current_soc=0.0, min_soc=30, cable_connected=True,
        schedule_all_prices_above_max=True,
    )
    assert real_zero[0] == REASON_MIN_SOC_FLOOR
    assert real_zero[1] is True


def test_unknown_soc_never_cuts_a_locked_block():
    """A blip mid-session keeps the block alive on its lock."""
    reason, should, amps = dr(
        soc_known=False, current_soc=0.0, cable_connected=True,
        charging_started=True,
        locked_session_end=datetime(2024, 1, 15, 15, 0),
        now_dt=datetime(2024, 1, 15, 14, 0),
    )
    assert (reason, should) == (REASON_SCHEDULED, True)
    assert amps == 12


# ── Plan display when the debounce skips the session (1.5.9) ─────────────────

@pytest.mark.asyncio
async def test_soc_sufficient_publishes_no_plan():
    """SoC above charge_start_soc: the knapsack still finds cheap hours, but we
    are not going to use them — the dashboard must not show a session."""
    coord = _make_coord(_fake_hass_full(soc="76"), _FakeStore())
    data = await coord._async_update_data()

    assert data["reason"] == REASON_SOC_SUFFICIENT
    assert data["should_charge"] is False
    assert data["sessions"] == []
    assert data["planned_session_start"] is None
    assert data["planned_session_end"] is None
    assert data["next_session_start"] is None
    assert data["planned_kwh"] == 0
    assert data["planned_cost"] == 0
    assert data["planned_duration_minutes"] == 0
    # Suppressing the plan is a choice, not a shortfall.
    assert data["feasible"] is True
    # The energy gap itself is still reported honestly.
    assert data["E_needed"] > 0


@pytest.mark.asyncio
async def test_plan_returns_once_the_debounce_no_longer_applies():
    """Same car below the debounce line: the plan is published again."""
    coord = _make_coord(_fake_hass_full(soc="62"), _FakeStore())
    data = await coord._async_update_data()

    assert data["reason"] == REASON_SCHEDULED
    assert data["sessions"] != []
    assert data["planned_session_start"] is not None
    assert data["planned_kwh"] > 0
