#!/usr/bin/env python3
"""
Deterministic house vacancy (24h) + evening presence simulation.

Vacant when ALL hold for >= VACANT_HOURS (default 24):
  - kitchen occupancy not active; last ON >= 24h ago
  - storage occupancy not active; last ON >= 24h ago
  - no family phones currently on Omada Wi‑Fi (Jakub / Sylwia name patterns)
    and each matched phone's lastSeen >= 24h ago

Car presence is intentionally ignored.

When vacant: on the next evening turn kitchen + salon lights ON around EVENING_ON
(Warsaw), then OFF at a random time between 22:30 and 23:30.

Commands:
  python presence_sim.py evaluate          # print signals + vacant flag; push HA
  python presence_sim.py run               # evaluate + evening light logic (cron)
  python presence_sim.py force-on          # turn simulation lights ON (test)
  python presence_sim.py force-off         # turn simulation lights OFF (test)
  python presence_sim.py monitor           # validate tonight's on/off; log 7 days

Env: OMADA_*, HA_URL, HA_TOKEN, optional VACANT_HOURS, STATE_DIR
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

try:
    import requests
    from urllib3.exceptions import InsecureRequestWarning

    requests.packages.urllib3.disable_warnings(InsecureRequestWarning)
except ImportError:
    requests = None  # type: ignore

WARSAW = ZoneInfo("Europe/Warsaw")
OMADA_ID_DEFAULT = "f73430f00af50891401c5757461f73f8"
SITE_ID_DEFAULT = "69bfd1dba7c1f205eb79303c"

KITCHEN_OCC = "binary_sensor.motiondetectionkitchenloaded_occupancy"
STORAGE_OCC = "binary_sensor.motiondetectionstorageroom_occupancy"

# Switches used for presence simulation (kitchen + salon / living room)
LIGHTS = [
    "switch.kitchenlight_l1",
    "switch.kitchenlight_l2",
    "switch.lightlivingroom_l1",
    "switch.lightlivingroom_l2",
    "switch.livinroomstandinglamp",
]

# Omada client name substrings (case-insensitive) → person bucket
PHONE_MATCHERS: dict[str, list[str]] = {
    "jakub": [
        "s23",
        "j23",
        "jakub",
        "kuby",
        "privkuby",
        "firmowykuby",
        "firmowykuby(dotdata)",
    ],
    "sylwia": [
        "sylwi",
        "z2 flip",
        "z2flip",
        "z flip",
        "firmowysylw",
        "privsylw",
        "galaxy z",
        "galaxy-z",
    ],
}

HA_SENSOR_VACANT = "binary_sensor.house_vacant_24h"
HA_SENSOR_DETAIL = "sensor.house_vacancy_status"


def load_env() -> None:
    for path in (
        Path("/secrets/.env"),
        Path(__file__).resolve().parent / ".env",
        Path.home() / ".env.private",
    ):
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            if line.startswith("export "):
                line = line[len("export ") :]
            k, v = line.split("=", 1)
            k, v = k.strip(), v.strip()
            if "#" in v and not (v.startswith('"') or v.startswith("'")):
                v = v.split("#", 1)[0].rstrip()
            os.environ.setdefault(k, v.strip().strip('"').strip("'"))


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_warsaw() -> datetime:
    return datetime.now(WARSAW)


def state_dir() -> Path:
    d = Path(os.environ.get("STATE_DIR") or os.environ.get("OUTPUT_DIR") or "/data")
    d.mkdir(parents=True, exist_ok=True)
    return d


def vacant_hours() -> float:
    return float(os.environ.get("VACANT_HOURS", "24"))


# ── HA REST ──────────────────────────────────────────────────────────────────

def ha_headers() -> dict[str, str]:
    token = os.environ.get("HA_TOKEN", "").strip()
    if not token:
        raise SystemExit("HA_TOKEN required")
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def ha_base() -> str:
    return os.environ.get("HA_URL", "http://192.168.1.201:8123").rstrip("/")


def ha_get(path: str) -> Any:
    req = urllib.request.Request(ha_base() + path, headers=ha_headers())
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def ha_post(path: str, body: dict) -> Any:
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        ha_base() + path, data=data, headers=ha_headers(), method="POST"
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read().decode()
        return json.loads(raw) if raw else None


def ha_set_state(entity_id: str, state: str, attributes: dict | None = None) -> None:
    body: dict[str, Any] = {"state": state}
    if attributes:
        body["attributes"] = attributes
    ha_post(f"/api/states/{entity_id}", body)


def ha_call_service(domain: str, service: str, entity_id: str | list[str]) -> None:
    ha_post(
        f"/api/services/{domain}/{service}",
        {"entity_id": entity_id},
    )


def ha_history_last_real_on(entity_id: str, days: int = 5) -> datetime | None:
    """
    Last genuine motion ON: transition into 'on' from 'off' (not from
    unavailable/unknown after HA restart). Ignores stuck sensors that only
    re-appear as 'on' after restarts without a prior 'off'.
    """
    end = now_utc()
    start = end - timedelta(days=days)
    qs = urllib.parse.urlencode(
        {
            "filter_entity_id": entity_id,
            "end_time": end.isoformat().replace("+00:00", "Z"),
            "significant_changes_only": "true",
        }
    )
    stamp = start.isoformat().replace("+00:00", "Z")
    data = ha_get(f"/api/history/period/{stamp}?{qs}")
    states = data[0] if data else []
    last: datetime | None = None
    prev = None
    for s in states:
        st = s.get("state")
        raw = s.get("last_changed") or s.get("last_updated")
        if not raw:
            prev = st
            continue
        ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if st == "on" and prev == "off":
            if last is None or ts > last:
                last = ts
        if st in ("on", "off"):
            prev = st
        # unavailable/unknown do not count as prev for off→on
    return last


def ha_current_state(entity_id: str) -> tuple[str, datetime | None]:
    d = ha_get(f"/api/states/{entity_id}")
    st = str(d.get("state", "unknown"))
    raw = d.get("last_changed")
    ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00")) if raw else None
    return st, ts


# ── Omada ────────────────────────────────────────────────────────────────────

def omada_token() -> tuple[str, str, str]:
    if requests is None:
        raise SystemExit("pip install requests")
    host = os.environ.get("OMADA_HOST", "").strip()
    cid = os.environ.get("OMADA_CLIENT_ID", "").strip()
    sec = os.environ.get("OMADA_CLIENT_SECRET", "").strip()
    omada_id = os.environ.get("OMADA_ID", OMADA_ID_DEFAULT).strip()
    if not all([host, cid, sec]):
        raise SystemExit("OMADA_HOST, OMADA_CLIENT_ID, OMADA_CLIENT_SECRET required")
    url = f"https://{host}:8043/openapi/authorize/token?grant_type=client_credentials"
    r = requests.post(
        url,
        json={"omadacId": omada_id, "client_id": cid, "client_secret": sec},
        verify=False,
        timeout=20,
    )
    data = r.json()
    if data.get("errorCode") != 0:
        raise SystemExit(f"Omada auth failed: {data}")
    token = data["result"]["accessToken"]
    return host, omada_id, token


def omada_get(host: str, omada_id: str, token: str, path: str) -> Any:
    assert requests is not None
    url = f"https://{host}:8043{path}"
    r = requests.get(
        url,
        headers={"Authorization": f"AccessToken={token}"},
        verify=False,
        timeout=30,
    )
    data = r.json()
    if data.get("errorCode") not in (0, None) and "result" not in data:
        raise RuntimeError(f"Omada GET {path}: {data}")
    return data


def fetch_omada_clients() -> tuple[list[dict], list[dict]]:
    host, omada_id, token = omada_token()
    site = os.environ.get("OMADA_SITE_ID", SITE_ID_DEFAULT)
    live = (
        omada_get(
            host,
            omada_id,
            token,
            f"/openapi/v1/{omada_id}/sites/{site}/clients?pageSize=100&page=1",
        )
        .get("result", {})
        .get("data")
        or []
    )
    insight = (
        omada_get(
            host,
            omada_id,
            token,
            f"/openapi/v1/{omada_id}/sites/{site}/insight/clients?pageSize=100&page=1",
        )
        .get("result", {})
        .get("data")
        or []
    )
    return live, insight


def match_person(name: str) -> str | None:
    n = (name or "").lower().replace(" ", "")
    n_sp = (name or "").lower()
    for person, patterns in PHONE_MATCHERS.items():
        for p in patterns:
            p2 = p.lower()
            if p2 in n_sp or p2.replace(" ", "") in n:
                return person
    return None


def parse_last_seen_ms(val: Any) -> datetime | None:
    if val is None:
        return None
    try:
        n = float(val)
    except (TypeError, ValueError):
        return None
    if n > 1e12:
        n = n / 1000.0
    return datetime.fromtimestamp(n, tz=timezone.utc)


@dataclass
class PhoneStatus:
    person: str
    name: str
    mac: str
    online: bool
    last_seen: datetime | None
    ap: str = ""
    age_hours: float | None = None


def collect_phones(live: list[dict], insight: list[dict]) -> list[PhoneStatus]:
    live_by_mac = {(c.get("mac") or "").upper(): c for c in live}
    # Build candidates from insight (broader) + live
    by_key: dict[str, PhoneStatus] = {}

    def consider(c: dict, online_hint: bool | None = None) -> None:
        name = c.get("name") or c.get("hostName") or ""
        person = match_person(name)
        if not person:
            return
        mac = (c.get("mac") or "").upper()
        online = online_hint if online_hint is not None else bool(c.get("active"))
        if mac in live_by_mac:
            online = True
            c_live = live_by_mac[mac]
            ap = c_live.get("apName") or ""
            ls = now_utc()
        else:
            ap = c.get("apName") or ""
            ls = parse_last_seen_ms(c.get("lastSeen"))
            # insight lastSeen can be stale even for online IoT — for phones offline it's ok
        age = None
        if ls:
            age = (now_utc() - ls).total_seconds() / 3600.0
        key = f"{person}:{mac or name}"
        prev = by_key.get(key)
        if prev and prev.online and not online:
            return
        if prev and prev.last_seen and ls and prev.last_seen > ls and not online:
            return
        by_key[key] = PhoneStatus(
            person=person,
            name=name,
            mac=mac,
            online=online,
            last_seen=ls,
            ap=ap,
            age_hours=age,
        )

    for c in insight:
        consider(c, online_hint=False)
    for c in live:
        consider(c, online_hint=True)

    return sorted(by_key.values(), key=lambda p: (p.person, p.name))


# ── Vacancy evaluation ───────────────────────────────────────────────────────

@dataclass
class VacancyReport:
    vacant: bool
    vacant_hours_required: float
    kitchen_state: str
    kitchen_last_on: str | None
    kitchen_age_h: float | None
    storage_state: str
    storage_last_on: str | None
    storage_age_h: float | None
    phones: list[dict] = field(default_factory=list)
    phones_blocking: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    evaluated_at: str = ""


def evaluate_vacancy() -> VacancyReport:
    hours = vacant_hours()
    reasons: list[str] = []

    k_state, k_lc = ha_current_state(KITCHEN_OCC)
    s_state, s_lc = ha_current_state(STORAGE_OCC)
    k_last = ha_history_last_real_on(KITCHEN_OCC)
    s_last = ha_history_last_real_on(STORAGE_OCC)

    # Live ON only counts if it came from a real off→on (see history).
    # Stuck "on" after HA restart (no off→on in history) is ignored for vacancy.
    if k_state == "on" and k_last and (now_utc() - k_last).total_seconds() < 3600:
        # recent real motion within last hour → treat as active now
        reasons.append("kitchen occupancy currently ON (recent motion)")
        k_last = now_utc()
    elif k_state == "on" and k_last is None:
        reasons.append(
            "kitchen occupancy stuck ON without off→on history (ignored for vacant)"
        )
    elif k_state == "on" and k_last and (now_utc() - k_last).total_seconds() >= 3600:
        # currently on but last real off→on older — may be stuck; still use k_last age
        reasons.append(
            f"kitchen currently ON but last real off→on "
            f"{(now_utc() - k_last).total_seconds() / 3600:.1f}h ago"
        )

    if s_state == "on":
        s_last = now_utc()
        reasons.append("storage occupancy currently ON")

    def age_h(ts: datetime | None) -> float | None:
        if not ts:
            return None
        return (now_utc() - ts).total_seconds() / 3600.0

    k_age = age_h(k_last)
    s_age = age_h(s_last)

    if k_age is None:
        reasons.append("kitchen: no ON history in window (treat as quiet)")
    elif k_age < hours:
        reasons.append(f"kitchen last ON {k_age:.1f}h ago (< {hours}h)")

    if s_age is None:
        reasons.append("storage: no ON history in window (treat as quiet)")
    elif s_age < hours:
        reasons.append(f"storage last ON {s_age:.1f}h ago (< {hours}h)")

    live, insight = fetch_omada_clients()
    phones = collect_phones(live, insight)
    phone_dicts = []
    blocking: list[str] = []
    for p in phones:
        phone_dicts.append(
            {
                "person": p.person,
                "name": p.name,
                "mac": p.mac,
                "online": p.online,
                "ap": p.ap,
                "last_seen": p.last_seen.isoformat() if p.last_seen else None,
                "age_hours": round(p.age_hours, 2) if p.age_hours is not None else None,
            }
        )
        if p.online:
            blocking.append(f"{p.person}/{p.name} ONLINE on {p.ap or '?'}")
            reasons.append(f"phone online: {p.name} ({p.person})")
        elif p.age_hours is not None and p.age_hours < hours:
            blocking.append(f"{p.person}/{p.name} seen {p.age_hours:.1f}h ago")
            reasons.append(f"phone recent: {p.name} {p.age_hours:.1f}h ago")

    # Need at least one known phone in inventory for phone criterion;
    # if none ever matched, still require no online match on live list by pattern
    live_names = [(c.get("name") or "") for c in live]
    for name in live_names:
        person = match_person(name)
        if person:
            # already handled via collect_phones
            pass

    motion_ok = (k_age is None or k_age >= hours) and (s_age is None or s_age >= hours)
    phones_ok = not blocking
    # Storage currently ON always blocks; kitchen stuck ON does not if no recent real motion
    storage_live_block = s_state == "on"
    kitchen_live_block = (
        k_state == "on"
        and k_last is not None
        and (now_utc() - k_last).total_seconds() < 3600
    )
    vacant = motion_ok and phones_ok and not storage_live_block and not kitchen_live_block

    if vacant:
        reasons = ["all signals quiet ≥ " + str(hours) + "h"]

    return VacancyReport(
        vacant=vacant,
        vacant_hours_required=hours,
        kitchen_state=k_state,
        kitchen_last_on=k_last.isoformat() if k_last else None,
        kitchen_age_h=round(k_age, 2) if k_age is not None else None,
        storage_state=s_state,
        storage_last_on=s_last.isoformat() if s_last else None,
        storage_age_h=round(s_age, 2) if s_age is not None else None,
        phones=phone_dicts,
        phones_blocking=blocking,
        reasons=reasons,
        evaluated_at=now_utc().isoformat(),
    )


def push_vacancy_to_ha(report: VacancyReport) -> None:
    attrs = {
        "friendly_name": "House vacant 24h",
        "device_class": "occupancy",
        "vacant_hours_required": report.vacant_hours_required,
        "kitchen_state": report.kitchen_state,
        "kitchen_last_on": report.kitchen_last_on,
        "kitchen_age_h": report.kitchen_age_h,
        "storage_state": report.storage_state,
        "storage_last_on": report.storage_last_on,
        "storage_age_h": report.storage_age_h,
        "phones": report.phones,
        "phones_blocking": report.phones_blocking,
        "reasons": report.reasons,
        "evaluated_at": report.evaluated_at,
        "icon": "mdi:home-off" if report.vacant else "mdi:home-account",
    }
    ha_set_state(HA_SENSOR_VACANT, "on" if report.vacant else "off", attrs)
    summary = "vacant" if report.vacant else ("blocked: " + "; ".join(report.reasons[:3]))
    ha_set_state(
        HA_SENSOR_DETAIL,
        summary[:255],
        {
            "friendly_name": "House vacancy status",
            "icon": "mdi:shield-home",
            **{k: v for k, v in attrs.items() if k != "friendly_name"},
        },
    )


# ── Simulation state machine ─────────────────────────────────────────────────

@dataclass
class SimState:
    date: str = ""  # Warsaw date we armed
    lights_on_at: str | None = None
    lights_off_at: str | None = None
    off_deadline: str | None = None  # ISO when to turn off
    forced: bool = False


def load_sim_state() -> SimState:
    path = state_dir() / "sim_state.json"
    if not path.exists():
        return SimState()
    try:
        return SimState(**json.loads(path.read_text()))
    except Exception:
        return SimState()


def save_sim_state(st: SimState) -> None:
    (state_dir() / "sim_state.json").write_text(
        json.dumps(asdict(st), indent=2) + "\n"
    )


def turn_lights(on: bool) -> None:
    service = "turn_on" if on else "turn_off"
    ha_call_service("switch", service, LIGHTS)
    print(f"lights {service}: {LIGHTS}", file=sys.stderr)


def pick_off_deadline(day: date) -> datetime:
    """Random between 22:30 and 23:30 Europe/Warsaw on `day`."""
    start = datetime(day.year, day.month, day.day, 22, 30, tzinfo=WARSAW)
    delta = timedelta(minutes=random.randint(0, 60))
    return start + delta


def evening_on_time(day: date) -> datetime:
    """Default: 20:00 Warsaw (nearest evening window start)."""
    hour = int(os.environ.get("EVENING_ON_HOUR", "20"))
    minute = int(os.environ.get("EVENING_ON_MINUTE", "0"))
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=WARSAW)


def run_simulation_tick(report: VacancyReport, force: bool = False) -> SimState:
    st = load_sim_state()
    today = now_warsaw().date()
    today_s = today.isoformat()
    now = now_warsaw()

    # Reset state on new day
    if st.date and st.date != today_s:
        st = SimState()

    vacant = report.vacant or force
    if force:
        st.forced = True

    if not vacant:
        save_sim_state(st)
        print("sim: not vacant — no light changes", file=sys.stderr)
        return st

    on_at = evening_on_time(today)
    # If we start after on_at but before 22:30, still turn on once
    latest_on = datetime(today.year, today.month, today.day, 22, 15, tzinfo=WARSAW)

    if st.lights_on_at is None and now >= on_at and now <= latest_on:
        turn_lights(True)
        st.date = today_s
        st.lights_on_at = now.isoformat()
        off_dl = pick_off_deadline(today)
        st.off_deadline = off_dl.isoformat()
        st.lights_off_at = None
        save_sim_state(st)
        print(f"sim: lights ON; off deadline {off_dl}", file=sys.stderr)
        return st

    if st.lights_on_at and not st.lights_off_at and st.off_deadline:
        off_dl = datetime.fromisoformat(st.off_deadline)
        if now >= off_dl:
            turn_lights(False)
            st.lights_off_at = now.isoformat()
            save_sim_state(st)
            print("sim: lights OFF", file=sys.stderr)
            return st

    save_sim_state(st)
    print(
        f"sim: idle date={st.date} on={st.lights_on_at} off={st.lights_off_at} "
        f"deadline={st.off_deadline}",
        file=sys.stderr,
    )
    return st


# ── Monitor ──────────────────────────────────────────────────────────────────

def monitor_once() -> dict:
    """
    Check whether tonight's simulation on/off happened (or was correctly skipped).
    Appends to STATE_DIR/monitor.jsonl. Designed for cron over 7 days.
    """
    report = evaluate_vacancy()
    st = load_sim_state()
    today = now_warsaw().date().isoformat()
    light_states = {}
    for eid in LIGHTS:
        try:
            s, _ = ha_current_state(eid)
            light_states[eid] = s
        except Exception as e:
            light_states[eid] = f"err:{e}"

    any_on = any(v == "on" for v in light_states.values())
    entry = {
        "ts": now_utc().isoformat(),
        "warsaw_date": today,
        "vacant": report.vacant,
        "reasons": report.reasons,
        "sim_state": asdict(st),
        "lights": light_states,
        "any_light_on": any_on,
        "ok_expected_on": None,
        "ok_expected_off": None,
        "note": "",
    }

    now = now_warsaw()
    # After 20:15 if vacant and not yet off deadline: expect some lights on
    if report.vacant and now.hour >= 20 and now.hour < 22:
        entry["ok_expected_on"] = bool(st.lights_on_at) or any_on
        if not entry["ok_expected_on"]:
            entry["note"] = "vacant evening but lights not marked ON yet"
    if report.vacant and st.off_deadline:
        off_dl = datetime.fromisoformat(st.off_deadline)
        if now >= off_dl + timedelta(minutes=10):
            entry["ok_expected_off"] = (not any_on) and bool(st.lights_off_at)
            if not entry["ok_expected_off"]:
                entry["note"] = "past off deadline but lights still on / not recorded"
    if not report.vacant:
        entry["note"] = "not vacant — simulation correctly idle"
        entry["ok_expected_on"] = True  # N/A pass
        entry["ok_expected_off"] = True

    path = state_dir() / "monitor.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(json.dumps(entry, indent=2, ensure_ascii=False))
    return entry


# ── CLI ──────────────────────────────────────────────────────────────────────

def cmd_evaluate(args: argparse.Namespace) -> int:
    report = evaluate_vacancy()
    print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    if not args.no_push:
        push_vacancy_to_ha(report)
        print("HA sensors updated:", HA_SENSOR_VACANT, HA_SENSOR_DETAIL, file=sys.stderr)
    return 0 if report.vacant else 2


def cmd_run(args: argparse.Namespace) -> int:
    report = evaluate_vacancy()
    print(json.dumps(asdict(report), indent=2, ensure_ascii=False))
    push_vacancy_to_ha(report)
    run_simulation_tick(report, force=args.force)
    return 0 if report.vacant or args.force else 2


def cmd_force_on(_: argparse.Namespace) -> int:
    turn_lights(True)
    st = load_sim_state()
    today = now_warsaw().date()
    st.date = today.isoformat()
    st.lights_on_at = now_warsaw().isoformat()
    st.off_deadline = pick_off_deadline(today).isoformat()
    st.forced = True
    st.lights_off_at = None
    save_sim_state(st)
    print("forced ON; off_deadline", st.off_deadline)
    return 0


def cmd_force_off(_: argparse.Namespace) -> int:
    turn_lights(False)
    st = load_sim_state()
    st.lights_off_at = now_warsaw().isoformat()
    save_sim_state(st)
    print("forced OFF")
    return 0


def cmd_monitor(_: argparse.Namespace) -> int:
    monitor_once()
    return 0


def main() -> int:
    load_env()
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("evaluate", help="Evaluate vacancy signals")
    p.add_argument("--no-push", action="store_true")
    p.set_defaults(func=cmd_evaluate)

    p = sub.add_parser("run", help="Evaluate + evening simulation tick")
    p.add_argument(
        "--force",
        action="store_true",
        help="Treat as vacant for light logic (still reports real vacant flag)",
    )
    p.set_defaults(func=cmd_run)

    sub.add_parser("force-on", help="Test: turn simulation lights ON").set_defaults(
        func=cmd_force_on
    )
    sub.add_parser("force-off", help="Test: turn simulation lights OFF").set_defaults(
        func=cmd_force_off
    )
    sub.add_parser("monitor", help="Log validation snapshot for 7-day watch").set_defaults(
        func=cmd_monitor
    )

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
