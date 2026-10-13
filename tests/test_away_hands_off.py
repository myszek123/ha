"""THE hands-off rule — top-priority invariant for the whole integration.

When the car is not at home, nothing in Home Assistant may affect the car in any
shape or form. Away, the driver charges however they like: any SoC, any time,
any duration. See docs/AWAY-HANDS-OFF.md.

Incident 12-10-2026 (cabin, Brajniki): smart mode, SoC 81 %, the driver set the
car limit to 95, 82, 83, 94, 93 ... and every value was reset to 80 within ~3 s
by should_reset_car_limit_after_session(), with every automation disabled.

Three layers, so a future change cannot quietly re-open this:
  1. behaviour — drive the real coordinator with the car away through every
     path that used to write to the car and assert no service call at all,
     with a home control case proving each path really writes when home;
  2. code — only _async_call_car_service may call a service, and it checks
     the gate first;
  3. automations — every repo automation/script action that touches the car
     or the integration's control entities sits under a home condition.
"""
from __future__ import annotations

import ast
import asyncio
import glob
import os
import re
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml

from custom_components.myszolot.const import (
    DEVICE_TRACKER,
    INPUT_BOOLEAN_LOCATION_OVERRIDE,
    MODE_OVERRIDE,
    MODE_SMART,
    NUMBER_CHARGE_LIMIT,
)
from custom_components.myszolot.coordinator import car_writes_allowed
from tests.test_charging_scenarios import _FakeStore, _fake_hass_full, _make_coord

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
AWAY_STATES = ["Brajniki", "not_home", "Szczytno", "unknown", "unavailable", "", None]


# ── 1. the predicate ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("state", AWAY_STATES)
def test_gate_closed_unless_tracker_positively_home(state):
    assert car_writes_allowed(state) is False


@pytest.mark.parametrize("state", ["home", "Home", " home "])
def test_gate_open_at_home(state):
    assert car_writes_allowed(state) is True


# ── 2. behaviour through the real coordinator ────────────────────────────────

def _hass(*, tracker, overrides=None, **kw):
    """_fake_hass_full + a recording services.async_call + state overrides."""
    away = tracker != "home"
    h = _fake_hass_full(tracker=tracker, garage=kw.pop("garage", "off" if away else "on"), **kw)
    if tracker is None:
        overrides = {**(overrides or {}), DEVICE_TRACKER: None}
    base = h.states.get
    ov = overrides or {}

    def get(eid):
        if eid in ov:
            return None if ov[eid] is None else SimpleNamespace(state=str(ov[eid]), attributes={})
        return base(eid)

    h.states.get = get
    h.services.async_call = AsyncMock()
    return h


async def _drain():
    for _ in range(5):
        await asyncio.sleep(0)


def _car_writes(hass):
    return [c for c in hass.services.async_call.await_args_list]


def _limit_writes(hass):
    return [c.args[2]["value"] for c in _car_writes(hass)
            if c.args[:2] == ("number", "set_value") and c.args[2]["entity_id"] == NUMBER_CHARGE_LIMIT]


@pytest.mark.asyncio
@pytest.mark.parametrize("tracker", AWAY_STATES)
@pytest.mark.parametrize("soc,limit", [("81", "94"), ("81", "95"), ("95", "94"), ("100", "56")])
async def test_incident_12_10_away_limit_is_never_reset(tracker, soc, limit):
    """The cabin incident: smart mode, SoC at/above target, driver's own limit.
    Many refreshes away must never send anything to the car."""
    hass = _hass(tracker=tracker, soc=soc, car_limit=limit, charging="charging")
    coord = _make_coord(hass, _FakeStore())
    for _ in range(5):
        data = await coord._async_update_data()
        await _drain()
    assert _car_writes(hass) == []
    assert coord._awaiting_default_car_limit is False
    assert data["car_writes_allowed"] is False


@pytest.mark.asyncio
async def test_control_home_session_complete_still_restores_default():
    """Home behaviour is unchanged: target reached at home → limit back to 80."""
    hass = _hass(tracker="home", soc="95", car_limit="94")
    coord = _make_coord(hass, _FakeStore())
    data = await coord._async_update_data()
    await _drain()
    assert _limit_writes(hass) == [80]
    assert data["car_writes_allowed"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("tracker", AWAY_STATES)
async def test_override_selected_away_pushes_nothing(tracker):
    hass = _hass(tracker=tracker, soc="60")
    coord = _make_coord(hass, _FakeStore())
    coord.set_mode(MODE_OVERRIDE)
    await coord._async_update_data()
    await _drain()
    assert coord._mode == MODE_OVERRIDE  # internal state may change ...
    assert _car_writes(hass) == []       # ... the car may not


@pytest.mark.asyncio
async def test_control_override_selected_home_pushes_target():
    hass = _hass(tracker="home", soc="60")
    coord = _make_coord(hass, _FakeStore())
    coord.set_mode(MODE_OVERRIDE)
    await _drain()
    assert _limit_writes(hass) == [80]  # helper target in _fake_hass_full


@pytest.mark.asyncio
@pytest.mark.parametrize("tracker", AWAY_STATES)
async def test_override_target_reached_away_writes_nothing(tracker):
    hass = _hass(tracker=tracker, soc="60")
    coord = _make_coord(hass, _FakeStore())
    coord.set_mode(MODE_OVERRIDE)
    coord.hass = hass = _hass(tracker=tracker, soc="85", car_limit="90")
    await coord._async_update_data()
    await _drain()
    assert coord._mode == MODE_SMART
    assert _car_writes(hass) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("tracker", AWAY_STATES)
async def test_override_deadline_expiry_away_writes_nothing(tracker):
    """Tonight's live case: override 90 % armed, deadline passes at the cabin."""
    hass = _hass(tracker=tracker, soc="70", car_limit="90")
    coord = _make_coord(hass, _FakeStore())
    coord.set_mode(MODE_OVERRIDE)
    coord._override_deadline = datetime.now() - timedelta(minutes=1)
    await coord._async_update_data()
    await _drain()
    assert coord._mode == MODE_SMART
    assert _car_writes(hass) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("tracker", AWAY_STATES)
async def test_override_to_smart_away_writes_nothing(tracker):
    hass = _hass(tracker=tracker, soc="70", car_limit="90")
    coord = _make_coord(hass, _FakeStore())
    coord.set_mode(MODE_OVERRIDE)
    coord._apply_smart_from_user("test: pending smart confirmed")
    await _drain()
    assert coord._mode == MODE_SMART
    assert _car_writes(hass) == []
    assert coord._awaiting_default_car_limit is False


@pytest.mark.asyncio
async def test_write_scheduled_at_home_is_dropped_if_car_left_before_it_ran():
    """The gate is re-checked when the service call runs, not only when queued."""
    coord = _make_coord(_hass(tracker="home"), _FakeStore())
    coord.hass = away = _hass(tracker="not_home")
    await coord._async_set_car_charge_limit(80)
    assert _car_writes(away) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("tracker", ["Brajniki", "not_home", "unavailable"])
async def test_location_override_and_garage_vision_do_not_open_the_gate(tracker):
    """'Force home' and a car-shaped blob in the garage decide where home
    charging may start; they never authorise commanding a car that GPS
    places elsewhere."""
    hass = _hass(tracker=tracker, garage="on", soc="95", car_limit="94",
                 overrides={INPUT_BOOLEAN_LOCATION_OVERRIDE: "on"})
    coord = _make_coord(hass, _FakeStore())
    coord.set_mode(MODE_OVERRIDE)
    coord._override_deadline = datetime.now() - timedelta(minutes=1)
    for _ in range(3):
        await coord._async_update_data()
        await _drain()
    assert _car_writes(hass) == []


# ── 3. code invariant: one guarded chokepoint ────────────────────────────────

INTEGRATION = os.path.join(REPO, "custom_components", "myszolot")
CHOKEPOINT = "_async_call_car_service"


def _functions_calling_services(path):
    tree = ast.parse(open(path).read(), path)
    hits = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(fn):
            if (isinstance(node, ast.Attribute) and node.attr in ("async_call", "call")
                    and isinstance(node.value, ast.Attribute) and node.value.attr == "services"):
                hits.append(fn.name)
    return hits


def test_only_the_chokepoint_calls_services():
    offenders = {}
    for path in glob.glob(os.path.join(INTEGRATION, "*.py")):
        names = [n for n in _functions_calling_services(path) if n != CHOKEPOINT]
        if names:
            offenders[os.path.basename(path)] = names
    assert offenders == {}, (
        "Service calls outside _async_call_car_service bypass the hands-off "
        f"gate (docs/AWAY-HANDS-OFF.md): {offenders}"
    )


def test_chokepoint_checks_the_gate_before_calling():
    src = open(os.path.join(INTEGRATION, "coordinator.py")).read()
    tree = ast.parse(src)
    fn = next(n for n in ast.walk(tree)
              if isinstance(n, ast.AsyncFunctionDef) and n.name == CHOKEPOINT)
    body = ast.get_source_segment(src, fn)
    gate, call = body.find("self._car_writes_allowed()"), body.find("services.async_call")
    assert 0 <= gate < call


# ── 4. automation invariant ──────────────────────────────────────────────────

# Anything that commands the car (Tessie platform entities) or the integration
# controls that push to it. The car's own entities are all named myszolot_*.
CAR_DOMAINS = ("number", "switch", "button", "cover", "lock", "climate",
               "media_player", "update", "select")
INTEGRATION_CONTROLS = {
    "input_number.myszolot_custom_target_soc",
    "input_number.myszolot_deadline_hours",
    "input_number.myszolot_deadline_minutes",
}
CAR_ENTITY = re.compile(r"\b(?:%s)\.myszolot_[a-z0-9_]+" % "|".join(CAR_DOMAINS))
HOME_TEMPLATE = re.compile(
    r"^\{\{-?\s*is_state\(\s*'device_tracker\.myszolot_location'\s*,\s*'home'\s*\)\s*(?:and\b|-?\}\})")


def _is_home_guard(cond) -> bool:
    if isinstance(cond, dict):
        if (cond.get("condition") == "state"
                and cond.get("entity_id") == "device_tracker.myszolot_location"
                and cond.get("state") == "home"):
            return True
        if (cond.get("condition") == "zone"
                and cond.get("entity_id") == "device_tracker.myszolot_location"
                and cond.get("zone") == "zone.home"):
            return True
        if cond.get("condition") == "template":
            return bool(HOME_TEMPLATE.match(str(cond.get("value_template", "")).strip()))
        if cond.get("condition") == "and":
            return any(_is_home_guard(c) for c in cond.get("conditions", []))
    return False


def _guarded(conds) -> bool:
    if conds is None:
        return False
    if isinstance(conds, dict):
        conds = [conds]
    return any(_is_home_guard(c) for c in conds)  # list = implicit AND


def _touches_car(action) -> list[str]:
    # What the action is aimed at — not free text such as a notification
    # message that merely mentions an entity.
    data = action.get("data") if isinstance(action.get("data"), dict) else {}
    tgt = " ".join(str(x) for x in (action.get("target", ""), action.get("entity_id", ""),
                                     data.get("entity_id", "")))
    hits = CAR_ENTITY.findall(tgt) + [e for e in INTEGRATION_CONTROLS if e in tgt]
    return sorted(set(hits))


def find_unguarded_car_writes(item: dict) -> list[str]:
    """Car-touching actions not under a home condition, as readable paths."""
    out, found = [], []

    def walk(actions, guarded, path):
        if isinstance(actions, dict):
            actions = [actions]
        for i, a in enumerate(actions or []):
            if not isinstance(a, dict):
                continue
            p = f"{path}[{i}]"
            if a.get("action") or a.get("service"):
                hits = _touches_car(a)
                if hits:
                    found.append(p)
                    if not guarded:
                        out.append(f"{p} {a.get('action') or a.get('service')} {hits}")
            if "if" in a:
                g = guarded or _guarded(a["if"])
                walk(a.get("then"), g, p + ".then")
                walk(a.get("else"), guarded, p + ".else")
            for j, opt in enumerate(a.get("choose") or []):
                walk(opt.get("sequence"), guarded or _guarded(opt.get("conditions")), f"{p}.choose[{j}]")
            if "default" in a:
                walk(a["default"], guarded, p + ".default")
            for k in ("sequence", "parallel"):
                if k in a:
                    walk(a[k], guarded, f"{p}.{k}")
            if "repeat" in a:
                walk(a["repeat"].get("sequence"), guarded, p + ".repeat")

    top = _guarded(item.get("conditions", item.get("condition")))
    walk(item.get("actions", item.get("action", item.get("sequence"))), top, "actions")
    item["_car_actions_found"] = len(found)
    return out


def _load(path):
    class L(yaml.SafeLoader):
        pass
    for tag in ("!input", "!secret", "!include"):
        L.add_constructor(tag, lambda l, n: l.construct_scalar(n))
    d = yaml.load(open(path), Loader=L)
    if isinstance(d, list):
        return d
    if isinstance(d, dict) and any(k in d for k in ("actions", "action", "sequence", "triggers")):
        return [d]
    return [v for v in (d or {}).values() if isinstance(v, dict)]


def _repo_items():
    files = sorted(glob.glob(os.path.join(REPO, "automations", "*.yml"))
                   + glob.glob(os.path.join(REPO, "scripts", "*.yml")))
    for f in files:
        for item in _load(f):
            yield os.path.relpath(f, REPO), item


def test_every_car_write_in_automations_is_home_guarded():
    bad, total = [], 0
    for name, item in _repo_items():
        bad += [f"{name} :: {item.get('alias')} :: {v}" for v in find_unguarded_car_writes(item)]
        total += item["_car_actions_found"]
    assert total >= 5, "scanner found almost no car actions — is it still looking?"
    assert bad == [], "Car writes without a home condition (docs/AWAY-HANDS-OFF.md):\n" + "\n".join(bad)


def test_scanner_catches_a_violation():
    """Guard against a scanner that silently passes everything."""
    leaky = {"actions": [{"action": "number.set_value",
                          "target": {"entity_id": "number.myszolot_charge_limit"},
                          "data": {"value": 80}}]}
    assert find_unguarded_car_writes(leaky)
    leaky_mode = {"actions": [{"action": "select.select_option",
                               "target": {"entity_id": "select.myszolot_charge_mode"},
                               "data": {"option": "override"}}]}
    assert find_unguarded_car_writes(leaky_mode)
    # A tracker check buried in an OR is not a guard.
    or_guard = {"actions": [{"if": [{"condition": "template", "value_template":
                "{{ is_state('binary_sensor.garage_car_present', 'on') or is_state('device_tracker.myszolot_location', 'home') }}"}],
                "then": [{"action": "switch.turn_on", "target": {"entity_id": "switch.myszolot_charge"}}]}]}
    assert find_unguarded_car_writes(or_guard)
    ok = {"conditions": [{"condition": "state", "entity_id": "device_tracker.myszolot_location",
                          "state": "home"}], **leaky}
    assert find_unguarded_car_writes(ok) == []
