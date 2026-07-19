#!/usr/bin/env python3
"""
Deterministic house vacancy (24h) + evening presence simulation.

Vacant when BOTH personal phones are offline on Omada for >= VACANT_HOURS (24):
  - Jakub: Omada client name matching **S23**
  - Sylwia: Omada client name matching **Z2 Flip**

Motion sensors are ignored (false positives). Company phones and other devices
are ignored. Car is ignored.

When vacant: next evening turn kitchen + salon lights ON around EVENING_ON
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

# Switches used for presence simulation (kitchen + salon / living room)
LIGHTS = [
    "switch.kitchenlight_l1",
    "switch.kitchenlight_l2",
    "switch.lightlivingroom_l1",
    "switch.lightlivingroom_l2",
    "switch.livinroomstandinglamp",
]

# Only personal phones (exact Omada client names / tight substrings)
# Company phones (firmowy*) intentionally excluded.
REQUIRED_PHONES: list[dict[str, Any]] = [
    {
        "id": "jakub_s23",
        "person": "jakub",
        "label": "S23",
        "patterns": ["s23"],  # Omada name "S23"
    },
    {
        "id": "sylwia_z2",
        "person": "sylwia",
        "label": "Z2 Flip",
        "patterns": ["z2 flip", "z2flip"],  # Omada name "Z2 Flip"
    },
]

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


def name_matches_patterns(name: str, patterns: list[str]) -> bool:
    n = (name or "").lower().strip()
    n_ns = n.replace(" ", "")
    for p in patterns:
        p2 = p.lower()
        if p2 == n or p2.replace(" ", "") == n_ns:
            return True
        # allow "S23" contained as whole token-ish match
        if p2 in n or p2.replace(" ", "") in n_ns:
            return True
    return False


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
    phone_id: str
    person: str
    label: str
    name: str
    mac: str
    online: bool
    last_seen: datetime | None
    ap: str = ""
    age_hours: float | None = None
    found: bool = True


def resolve_required_phones(
    live: list[dict], insight: list[dict]
) -> list[PhoneStatus]:
    """Resolve S23 + Z2 Flip only from Omada live + insight client lists."""
    live_by_mac = {(c.get("mac") or "").upper(): c for c in live}
    out: list[PhoneStatus] = []

    for req in REQUIRED_PHONES:
        patterns = list(req["patterns"])
        best: dict | None = None
        online = False
        # Prefer live match
        for c in live:
            name = c.get("name") or c.get("hostName") or ""
            if name_matches_patterns(name, patterns):
                best = c
                online = True
                break
        if best is None:
            for c in insight:
                name = c.get("name") or c.get("hostName") or ""
                if name_matches_patterns(name, patterns):
                    best = c
                    break

        if best is None:
            out.append(
                PhoneStatus(
                    phone_id=req["id"],
                    person=req["person"],
                    label=req["label"],
                    name="",
                    mac="",
                    online=False,
                    last_seen=None,
                    found=False,
                    age_hours=None,
                )
            )
            continue

        mac = (best.get("mac") or "").upper()
        if online or mac in live_by_mac:
            online = True
            live_c = live_by_mac.get(mac, best)
            ap = live_c.get("apName") or ""
            ls = now_utc()
            name = live_c.get("name") or best.get("name") or req["label"]
        else:
            ap = best.get("apName") or ""
            ls = parse_last_seen_ms(best.get("lastSeen"))
            name = best.get("name") or best.get("hostName") or req["label"]

        age = (now_utc() - ls).total_seconds() / 3600.0 if ls else None
        out.append(
            PhoneStatus(
                phone_id=req["id"],
                person=req["person"],
                label=req["label"],
                name=name,
                mac=mac,
                online=online,
                last_seen=ls,
                ap=ap,
                age_hours=age,
                found=True,
            )
        )
    return out


# ── Vacancy evaluation ───────────────────────────────────────────────────────

@dataclass
class VacancyReport:
    vacant: bool
    vacant_hours_required: float
    phones: list[dict] = field(default_factory=list)
    phones_blocking: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    evaluated_at: str = ""
    s23_online: bool | None = None
    s23_age_h: float | None = None
    z2_online: bool | None = None
    z2_age_h: float | None = None


def evaluate_vacancy() -> VacancyReport:
    """Vacant iff S23 and Z2 Flip both offline ≥ VACANT_HOURS (no motion checks)."""
    hours = vacant_hours()
    reasons: list[str] = []
    blocking: list[str] = []

    live, insight = fetch_omada_clients()
    phones = resolve_required_phones(live, insight)
    phone_dicts: list[dict] = []

    for p in phones:
        phone_dicts.append(
            {
                "phone_id": p.phone_id,
                "person": p.person,
                "label": p.label,
                "name": p.name,
                "mac": p.mac,
                "online": p.online,
                "ap": p.ap,
                "found": p.found,
                "last_seen": p.last_seen.isoformat() if p.last_seen else None,
                "age_hours": round(p.age_hours, 2) if p.age_hours is not None else None,
            }
        )
        if not p.found:
            blocking.append(f"{p.label} not found in Omada")
            reasons.append(f"{p.label}: not found in Omada client list")
            continue
        if p.online:
            blocking.append(f"{p.label} ONLINE on {p.ap or '?'}")
            reasons.append(f"{p.label} online ({p.ap or 'AP unknown'})")
        elif p.age_hours is None:
            blocking.append(f"{p.label} no lastSeen")
            reasons.append(f"{p.label}: no lastSeen timestamp")
        elif p.age_hours < hours:
            blocking.append(f"{p.label} seen {p.age_hours:.1f}h ago")
            reasons.append(f"{p.label} last seen {p.age_hours:.1f}h ago (< {hours}h)")

    vacant = len(blocking) == 0 and len(phones) == len(REQUIRED_PHONES)
    if vacant:
        reasons = [
            f"S23 + Z2 Flip both offline ≥ {hours:.0f}h (Omada only; motion ignored)"
        ]

    by_id = {p.phone_id: p for p in phones}
    s23 = by_id.get("jakub_s23")
    z2 = by_id.get("sylwia_z2")

    return VacancyReport(
        vacant=vacant,
        vacant_hours_required=hours,
        phones=phone_dicts,
        phones_blocking=blocking,
        reasons=reasons,
        evaluated_at=now_utc().isoformat(),
        s23_online=s23.online if s23 and s23.found else None,
        s23_age_h=round(s23.age_hours, 2) if s23 and s23.age_hours is not None else None,
        z2_online=z2.online if z2 and z2.found else None,
        z2_age_h=round(z2.age_hours, 2) if z2 and z2.age_hours is not None else None,
    )


def push_vacancy_to_ha(report: VacancyReport) -> None:
    attrs = {
        "friendly_name": "House vacant 24h",
        "device_class": "occupancy",
        "vacant_hours_required": report.vacant_hours_required,
        "s23_online": report.s23_online,
        "s23_age_h": report.s23_age_h,
        "z2_online": report.z2_online,
        "z2_age_h": report.z2_age_h,
        "phones": report.phones,
        "phones_blocking": report.phones_blocking,
        "reasons": report.reasons,
        "evaluated_at": report.evaluated_at,
        "source": "omada_s23_z2_flip",
        "icon": "mdi:home-off" if report.vacant else "mdi:home-account",
    }
    ha_set_state(HA_SENSOR_VACANT, "on" if report.vacant else "off", attrs)
    if report.vacant:
        summary = "vacant (S23+Z2 Flip offline ≥24h)"
    else:
        summary = "home/active: " + "; ".join(report.reasons[:3])
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
