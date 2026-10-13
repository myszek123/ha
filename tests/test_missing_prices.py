"""Planning when next-day prices are missing (regression for 13-09-2026)."""
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest

from custom_components.myszolot.coordinator import (
    _parse_all_prices,
    build_schedule,
    fill_missing_price_hours,
    max_energy_in_window,
    max_reachable_soc,
)

RATE = 7.59  # kWh per hour at 11 A, 3-phase
BATTERY = 68.9
NIGHT = [1.11, 1.08, 1.06, 1.02, 1.00, 1.03, 1.10]  # 00:00–06:00


def _day_entries(day: date, prices: list[float]) -> list[dict]:
    base = datetime(day.year, day.month, day.day)
    return [{"start": (base + timedelta(hours=h)).isoformat(), "price": p} for h, p in enumerate(prices)]


def _today_prices() -> list[float]:
    return NIGHT + [1.30] * 16 + [1.25]  # 24 hours, 23:00 = 1.25


def _state(entries: list[dict]) -> SimpleNamespace:
    return SimpleNamespace(attributes={"All prices": entries})


def test_parse_maps_entries_by_start_timestamp():
    day = date(2026, 9, 13)
    parsed = _parse_all_prices(_state(_day_entries(day, _today_prices())), day)
    assert [h["hour"] for h in parsed] == list(range(24))
    assert parsed[23]["price"] == pytest.approx(1.25)


def test_parse_cached_list_from_yesterday_lands_on_negative_hours():
    yesterday = date(2026, 9, 13)
    parsed = _parse_all_prices(_state(_day_entries(yesterday, _today_prices())), date(2026, 9, 14))
    assert [h["hour"] for h in parsed] == list(range(-24, 0))


def test_parse_48h_list_has_no_duplicate_hours():
    day = date(2026, 9, 13)
    entries = _day_entries(day, _today_prices()) + _day_entries(day + timedelta(days=1), _today_prices())
    parsed = _parse_all_prices(_state(entries), day)
    assert [h["hour"] for h in parsed] == list(range(48))


def test_parse_entries_without_start_keep_hour_key():
    grid = [{"hour": h, "price": 0.5} for h in range(24)]
    assert [h["hour"] for h in _parse_all_prices(_state(grid), date(2026, 9, 13))] == list(range(24))


def test_fill_estimates_missing_hours_from_previous_day():
    day = date(2026, 9, 13)
    today = _parse_all_prices(_state(_day_entries(day, _today_prices())), day)
    now = datetime(2026, 9, 13, 23, 15)
    filled, estimated = fill_missing_price_hours(today, now, 7.22)
    assert estimated == list(range(24, 31))
    by_hour = {h["hour"]: h["price"] for h in filled}
    for h in range(24, 31):
        assert by_hour[h] == pytest.approx(NIGHT[h - 24])


def test_fill_is_a_no_op_when_prices_cover_the_window():
    day = date(2026, 9, 13)
    entries = _day_entries(day, _today_prices()) + _day_entries(day + timedelta(days=1), _today_prices())
    prices = _parse_all_prices(_state(entries), day)
    filled, estimated = fill_missing_price_hours(prices, datetime(2026, 9, 13, 23, 15), 7.22)
    assert estimated == []
    assert filled == prices


def test_fill_without_any_grid_keeps_continuous_fallback():
    now = datetime(2026, 9, 13, 23, 15)
    filled, estimated = fill_missing_price_hours([], now, 5)
    assert (filled, estimated) == ([], [])
    assert max_energy_in_window(filled, RATE, now, 5) == pytest.approx(5 * RATE)


def test_override_window_is_not_cut_at_midnight_when_tomorrow_is_missing():
    """13-09-2026: override 64 % from 44 %, deadline 06:31, only today's prices."""
    day = date(2026, 9, 13)
    now = datetime(2026, 9, 13, 23, 15)
    deadline_hours = 7.22
    e_needed = 13.78
    today_only = _parse_all_prices(_state(_day_entries(day, _today_prices())), day)

    # The bug: without estimates only 23:00 is usable and the target looks unreachable.
    old_plan = build_schedule(today_only, e_needed, RATE, now, deadline_hours=deadline_hours)
    assert [s["hour"] for s in old_plan] == [23]
    assert max_reachable_soc(44, max_energy_in_window(today_only, RATE, now, deadline_hours), BATTERY) < 64

    prices, estimated = fill_missing_price_hours(today_only, now, deadline_hours)
    assert estimated
    max_kwh = max_energy_in_window(prices, RATE, now, deadline_hours)
    # 23:15 → only 45 min of 23:00 (1.5.13), then 00:00 … 06:00 in full
    assert max_kwh == pytest.approx(7.75 * RATE)
    assert max_reachable_soc(44, max_kwh, BATTERY) >= 64

    plan = build_schedule(prices, e_needed, RATE, now, deadline_hours=deadline_hours)
    hours = [s["hour"] for s in plan]
    assert 23 not in hours, "must not charge at 23:00 when the night is cheaper"
    assert set(hours) == {27, 28}  # 03:00 and 04:00, the cheapest night hours
    assert sum(s["kWh"] for s in plan) == pytest.approx(e_needed)
