"""Feature flag: car charge-limit changes replan; reset to default after session."""
from __future__ import annotations

from custom_components.myszolot.coordinator import (
    clamp_charge_limit_soc,
    resolve_target_with_car_limit,
    should_reset_car_limit_after_session,
    presence_allows_home_charge,
    live_home_charge_hold,
    car_positively_away,
)
from custom_components.myszolot.const import (
    MODE_SMART,
    MODE_OVERRIDE,
    DEFAULT_TARGET_SOC,
    DEFAULT_CAR_LIMIT_REPLAN,
    CONF_CAR_LIMIT_REPLAN,
)


def test_feature_default_on():
    assert DEFAULT_CAR_LIMIT_REPLAN is True
    assert CONF_CAR_LIMIT_REPLAN == "car_limit_replan"


def test_clamp_charge_limit():
    assert clamp_charge_limit_soc(90) == 90
    assert clamp_charge_limit_soc(40) == 50
    assert clamp_charge_limit_soc(110) == 100
    assert clamp_charge_limit_soc(None) is None
    assert clamp_charge_limit_soc("bad") is None


def test_feature_off_ignores_car_limit():
    t, ov, adopted = resolve_target_with_car_limit(
        feature_enabled=False,
        mode=MODE_SMART,
        car_limit=95,
        override_target=None,
        default_target=80,
        helper_override_target=90,
    )
    assert t == 80
    assert ov is None
    assert adopted is False


def test_smart_adopts_car_limit_keeps_default_horizon():
    """Smart: car 95 → plan to 95 (horizon still config smart_deadline_hours)."""
    t, ov, adopted = resolve_target_with_car_limit(
        feature_enabled=True,
        mode=MODE_SMART,
        car_limit=95,
        override_target=None,
        default_target=80,
        helper_override_target=90,
    )
    assert t == 95
    assert ov is None
    assert adopted is True


def test_smart_car_at_default_not_special():
    t, ov, adopted = resolve_target_with_car_limit(
        feature_enabled=True,
        mode=MODE_SMART,
        car_limit=80,
        override_target=None,
        default_target=80,
        helper_override_target=90,
    )
    assert t == 80
    assert adopted is False


def test_override_adopts_car_limit_updates_locked_target():
    """Override: car 96 while locked 90 → new target 96; caller keeps absolute deadline."""
    t, ov, adopted = resolve_target_with_car_limit(
        feature_enabled=True,
        mode=MODE_OVERRIDE,
        car_limit=96,
        override_target=90,
        default_target=80,
        helper_override_target=90,
        allow_adopt=True,
    )
    assert t == 96
    assert ov == 96
    assert adopted is True


def test_override_does_not_adopt_stale_car_limit_when_away():
    """Away: physical limit may be old — keep locked override target."""
    t, ov, adopted = resolve_target_with_car_limit(
        feature_enabled=True,
        mode=MODE_OVERRIDE,
        car_limit=80,
        override_target=96,
        default_target=80,
        helper_override_target=96,
        allow_adopt=False,
    )
    assert t == 96
    assert ov is None
    assert adopted is False


def test_override_same_car_limit_no_persist_churn():
    t, ov, adopted = resolve_target_with_car_limit(
        feature_enabled=True,
        mode=MODE_OVERRIDE,
        car_limit=94,
        override_target=94,
        default_target=80,
        helper_override_target=90,
    )
    assert t == 94
    assert ov is None
    assert adopted is False


def test_ignore_car_limit_after_our_write():
    t, _, _ = resolve_target_with_car_limit(
        feature_enabled=True,
        mode=MODE_SMART,
        car_limit=80,
        override_target=None,
        default_target=80,
        helper_override_target=90,
        ignore_car_limit=80,
    )
    assert t == 80


def test_presence_matches_actuator_gate():
    assert presence_allows_home_charge("on", "home", False) is True
    assert presence_allows_home_charge("on", "unknown", False) is True
    assert presence_allows_home_charge("on", "not_home", False) is False
    assert presence_allows_home_charge("off", "home", False) is False
    assert presence_allows_home_charge("unavailable", "home", False) is True
    assert presence_allows_home_charge("off", "not_home", True) is True  # force-home


def test_live_home_charge_hold_needs_tesla_autel_and_gps_home():
    """20-08 14:12: only a physical home charge holds through vision-empty."""
    assert live_home_charge_hold("charging", "on", "home") is True
    assert live_home_charge_hold("starting", "on", "home") is True
    assert live_home_charge_hold("on", "on", "home") is True
    assert live_home_charge_hold("stopped", "on", "home") is False
    assert live_home_charge_hold("charging", "off", "home") is False
    assert live_home_charge_hold("charging", "on", "not_home") is False
    assert live_home_charge_hold("charging", "on", "unknown") is False
    assert live_home_charge_hold(None, "on", "home") is False


def test_car_positively_away_vision_empty_is_away_unless_live_hold():
    assert car_positively_away("off", "home", False) is True
    assert car_positively_away(
        "off", "home", False, live_home_charge=True
    ) is False
    assert car_positively_away("off", "not_home", False) is True
    assert car_positively_away("on", "home", False) is False
    assert car_positively_away("unavailable", "home", False) is False


def test_reset_car_limit_when_session_complete():
    assert should_reset_car_limit_after_session(
        feature_enabled=True,
        current_soc=96,
        target_soc=96,
        car_limit=96,
        default_target=DEFAULT_TARGET_SOC,
    )
    assert not should_reset_car_limit_after_session(
        feature_enabled=True,
        current_soc=50,
        target_soc=96,
        car_limit=96,
        default_target=80,
    )
    assert not should_reset_car_limit_after_session(
        feature_enabled=True,
        current_soc=80,
        target_soc=80,
        car_limit=80,
        default_target=80,
    )
    assert not should_reset_car_limit_after_session(
        feature_enabled=False,
        current_soc=96,
        target_soc=96,
        car_limit=96,
        default_target=80,
    )
