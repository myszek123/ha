"""
End-to-end charging scenarios from real production incidents.

Glue: build_schedule → compute_sessions → flatten → determine_reason
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
    flatten_sessions,
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
    DEFAULT_MIN_FLAT_AMPS,
    INPUT_NUMBER_CUSTOM_TARGET_SOC,
    INPUT_NUMBER_DEADLINE_HOURS,
    INPUT_NUMBER_DEADLINE_MINUTES,
)

TODAY = date(2024, 1, 15)
MAX_KWH = 8.28  # ~12 A × 230 × 3 / 1000


def prices(*pairs: tuple[int, float]) -> list[dict]:
    return [{"hour": h, "price": p} for h, p in pairs]


# ── Incident: 6A drop / inventing spare window ───────────────────────────────

def test_hard_end_stops_flatten_inventing_spare_time():
    """
    Incident: remaining energy ~35 min @12A but flatten opened ~70 min → 6A.
    Absolute deadline must cap the window so amps stay high when time is short.
    """
    schedule = [
        {"hour": 12, "minutes": 12, "kWh": 2.0, "cost": 1.0, "full": False},
        {"hour": 13, "minutes": 60, "kWh": 10.0, "cost": 5.0, "full": True},
    ]
    sessions = compute_sessions(schedule, TODAY)
    now = datetime(2024, 1, 15, 12, 30)
    wide = flatten_sessions(sessions, 12, now, min_flat_amps=5, hard_end=None)
    hard = datetime(2024, 1, 15, 13, 0)
    tight = flatten_sessions(sessions, 12, now, min_flat_amps=5, hard_end=hard)
    assert tight[0]["end"] <= hard
    # Tight window → higher amps and/or shorter end than invent-to-14:00
    assert tight[0]["amps"] >= wide[0]["amps"] or tight[0]["end"] < wide[0]["end"]


def test_flatten_can_produce_mid_range_amps_not_only_6_11_12():
    """Any integer in [min_flat, charge_amps] is allowed — not a whitelist."""
    # need 36 min @12A in a 60 min single-hour window → floor(12*36/60)=7
    schedule = [{"hour": 14, "minutes": 36, "kWh": 6.0, "cost": 2.0, "full": False}]
    sessions = compute_sessions(schedule, TODAY)
    now = datetime(2024, 1, 15, 10, 0)  # before window → full hour 14:00–15:00
    flat = flatten_sessions(sessions, 12, now, min_flat_amps=5)
    assert flat[0]["amps"] == 7
    assert DEFAULT_MIN_FLAT_AMPS <= flat[0]["amps"] <= 12


def test_incident_style_half_rate_when_window_twice_need():
    """
    need 35 min, window ~70 min → floor(12*35/70)=6 — the 6A formula.

    Also: hard_end must never let end go past the absolute wall-clock deadline.
    """
    # Two cheap hours selected, only 35 min of energy total
    schedule = [
        {"hour": 13, "minutes": 20, "kWh": 3.0, "cost": 1.0, "full": False},
        {"hour": 14, "minutes": 15, "kWh": 2.0, "cost": 1.0, "full": False},
    ]
    sessions = compute_sessions(schedule, TODAY)
    now = datetime(2024, 1, 15, 13, 0)

    # Wide: 13:00–15:00 span → floor hits min_flat
    wide = flatten_sessions(sessions, 12, now, min_flat_amps=5)
    assert wide[0]["amps"] == 5
    assert wide[0]["flattened"] is True

    # ~70 min window → classic 6A (need 35 / avail 70)
    hard_70 = datetime(2024, 1, 15, 14, 10)
    mid = flatten_sessions(sessions, 12, now, min_flat_amps=5, hard_end=hard_70)
    assert mid[0]["end"] <= hard_70
    assert mid[0]["amps"] == 6

    # Tight window (~50 min) → higher amps, still ≤ hard_end
    hard_50 = datetime(2024, 1, 15, 13, 50)
    tight = flatten_sessions(sessions, 12, now, min_flat_amps=5, hard_end=hard_50)
    assert tight[0]["end"] <= hard_50
    assert tight[0]["amps"] >= mid[0]["amps"]


def test_flatten_amps_always_within_floor_and_charge_amps():
    """Never command below min_flat or above charge_amps (Autel ≤12)."""
    schedule = [
        {"hour": h, "minutes": m, "kWh": 1.0, "cost": 0.1, "full": False}
        for h, m in ((12, 5), (13, 5), (14, 5))
    ]
    sessions = compute_sessions(schedule, TODAY)
    now = datetime(2024, 1, 15, 12, 0)
    for min_flat, charge in ((5, 12), (6, 12), (5, 11)):
        flat = flatten_sessions(sessions, charge, now, min_flat_amps=min_flat)
        for s in flat:
            assert min_flat <= s["amps"] <= charge


def test_flatten_no_slack_keeps_full_charge_amps():
    """Full hours at capacity → stay at charge_amps (no phantom 6A)."""
    schedule = [
        {"hour": 13, "minutes": 60, "kWh": 10.0, "cost": 5.0, "full": True},
        {"hour": 14, "minutes": 60, "kWh": 10.0, "cost": 4.0, "full": True},
    ]
    sessions = compute_sessions(schedule, TODAY)
    flat = flatten_sessions(sessions, 12, datetime(2024, 1, 15, 10, 0), min_flat_amps=5)
    assert flat[0]["amps"] == 12
    assert flat[0]["flattened"] is False


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
    hass = _fake_hass_with_helpers(target=95, hours=2)
    coord = _make_coord(hass, _FakeStore())
    coord.set_mode(MODE_OVERRIDE)
    assert coord.mode == MODE_OVERRIDE
    coord.set_mode(MODE_SMART)
    assert coord.mode == MODE_SMART
    assert coord._override_deadline is None
    assert coord._override_target_soc is None


def test_deadline_minutes_preferred_over_hours():
    hass = _fake_hass_with_helpers(target=90, hours=5, minutes=90)
    coord = _make_coord(hass, _FakeStore())
    assert coord._read_deadline_minutes() == 90  # not 5*60


def test_deadline_hours_fallback_when_no_minutes_helper():
    hass = _fake_hass_with_helpers(target=90, hours=3, minutes=None)
    coord = _make_coord(hass, _FakeStore())
    assert coord._read_deadline_minutes() == 180


def test_deadline_minutes_clamped():
    hass = _fake_hass_with_helpers(target=90, hours=1, minutes=99999)
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
    sessions = flatten_sessions(sessions, 12, now, min_flat_amps=5, hard_end=hard_end)
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
    sessions = flatten_sessions(sessions, 12, now, min_flat_amps=5)
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
    assert 5 <= amps2 <= 12


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
    coord.set_mode(MODE_SMART)
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
    # Need ~8 kWh (about 1h)
    schedule = build_schedule(
        all_prices, E_needed=8.0, max_kWh_per_hour=MAX_KWH, now_dt=now,
        deadline_hours=2, max_price=None,
    )
    hours = {s["hour"] for s in schedule}
    assert 19 in hours
    assert 18 not in hours  # waits for cheaper hour 19 within 2h
    sessions = compute_sessions(schedule, TODAY)
    sessions = flatten_sessions(
        sessions, 12, now, min_flat_amps=5,
        hard_end=now + timedelta(hours=2),
    )
    for s in sessions:
        assert s["end"] <= now + timedelta(hours=2) + timedelta(seconds=1)
    assert is_in_session(sessions, now) is False
