#!/usr/bin/env python3
"""
Deterministic house vacancy (24h) + evening presence simulation.

Vacant when BOTH personal phones are offline on Omada for >= VACANT_HOURS (24):
  - Jakub: Omada client name matching **S23**
  - Sylwia: Omada client name matching **Z8 Flip** (Galaxy Z Flip8)

Motion sensors are ignored (false positives). Company phones and other devices
are ignored. Car is ignored.

When vacant (and not paused): arm a random ON time at 20:00 Warsaw ±15 min
(all kitchen+salon lights together), then OFF at a random time 22:30–23:30.
One HA push notify when lights actually turn ON.

Pause: input_boolean.presence_sim_pause ON → no light changes (guests /
home without phones). Toggle on dashboard.

Commands:
  python presence_sim.py evaluate          # print signals + vacant flag; push HA
  python presence_sim.py run               # evaluate + evening light logic (cron)
  python presence_sim.py force-on          # turn simulation lights ON (test only)
  python presence_sim.py force-off         # turn simulation lights OFF (test only)
  python presence_sim.py monitor           # validate tonight's on/off; log 7 days

Env: OMADA_*, HA_URL, HA_TOKEN, optional VACANT_HOURS, STATE_DIR,
     HA_NOTIFY_SERVICE (default notify.mobile_app_j23)
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
        "id": "sylwia_z8",
        "person": "sylwia",
        "label": "Z8 Flip",
        # Omada: "Z8 Flip" / "Galaxy-Z-Flip8" — not Z2 Flip (old phone)
        "patterns": [
            "z8 flip",
            "z8flip",
            "galaxy-z-flip8",
            "galaxy z flip8",
            "galaxy-z-flip 8",
            "flip8",
        ],
    },
]

HA_SENSOR_VACANT = "binary_sensor.house_vacant_24h"
HA_SENSOR_DETAIL = "sensor.house_vacancy_status"
# ON = pause light simulation (guests / home with phones off). Default off = armed.
HA_PAUSE_BOOLEAN = "input_boolean.presence_sim_pause"
HA_NOTIFY_DEFAULT = "notify.mobile_app_j23"


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


def ha_call_service(
    domain: str,
    service: str,
    entity_id: str | list[str] | None = None,
    data: dict | None = None,
) -> None:
    body: dict[str, Any] = dict(data or {})
    if entity_id is not None:
        body["entity_id"] = entity_id
    ha_post(f"/api/services/{domain}/{service}", body)


def ha_current_state(entity_id: str) -> tuple[str, datetime | None]:
    d = ha_get(f"/api/states/{entity_id}")
    st = str(d.get("state", "unknown"))
    raw = d.get("last_changed")
    ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00")) if raw else None
    return st, ts


def sim_paused() -> bool:
    """True when dashboard pause helper is ON. Missing entity → not paused."""
    try:
        st, _ = ha_current_state(HA_PAUSE_BOOLEAN)
        return st == "on"
    except Exception as e:
        print(f"pause helper read failed ({HA_PAUSE_BOOLEAN}): {e}", file=sys.stderr)
        return False


def notify_presence_started(on_at: datetime, off_dl: datetime) -> None:
    """Single phone push: evening simulation lights just turned ON."""
    service = os.environ.get("HA_NOTIFY_SERVICE", HA_NOTIFY_DEFAULT).strip()
    if "." in service:
        domain, name = service.split(".", 1)
    else:
        domain, name = "notify", service
    title = "Presence simulation started"
    message = (
        f"Kitchen + salon lights ON at {on_at.strftime('%H:%M')} "
        f"(scheduled window 20:00±15). Off around {off_dl.strftime('%H:%M')}."
    )
    try:
        ha_call_service(domain, name, data={"title": title, "message": message})
        print(f"notify: {service} — {message}", file=sys.stderr)
    except Exception as e:
        print(f"notify failed ({service}): {e}", file=sys.stderr)


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
    """Resolve S23 + Z8 Flip only from Omada live + insight client lists."""
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
    z8_online: bool | None = None
    z8_age_h: float | None = None


def evaluate_vacancy() -> VacancyReport:
    """Vacant iff S23 and Z8 Flip both offline ≥ VACANT_HOURS (no motion checks)."""
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
            f"S23 + Z8 Flip both offline ≥ {hours:.0f}h (Omada only; motion ignored)"
        ]

    by_id = {p.phone_id: p for p in phones}
    s23 = by_id.get("jakub_s23")
    z8 = by_id.get("sylwia_z8")

    return VacancyReport(
        vacant=vacant,
        vacant_hours_required=hours,
        phones=phone_dicts,
        phones_blocking=blocking,
        reasons=reasons,
        evaluated_at=now_utc().isoformat(),
        s23_online=s23.online if s23 and s23.found else None,
        s23_age_h=round(s23.age_hours, 2) if s23 and s23.age_hours is not None else None,
        z8_online=z8.online if z8 and z8.found else None,
        z8_age_h=round(z8.age_hours, 2) if z8 and z8.age_hours is not None else None,
    )


def push_vacancy_to_ha(
    report: VacancyReport,
    *,
    paused: bool | None = None,
    sim: "SimState | None" = None,
) -> None:
    if paused is None:
        paused = sim_paused()
    st = sim if sim is not None else load_sim_state()
    attrs = {
        "friendly_name": "House vacant 24h",
        "device_class": "occupancy",
        "vacant_hours_required": report.vacant_hours_required,
        "s23_online": report.s23_online,
        "s23_age_h": report.s23_age_h,
        "z8_online": report.z8_online,
        "z8_age_h": report.z8_age_h,
        # legacy attr names (dashboard transition)
        "z2_online": report.z8_online,
        "z2_age_h": report.z8_age_h,
        "phones": report.phones,
        "phones_blocking": report.phones_blocking,
        "reasons": report.reasons,
        "evaluated_at": report.evaluated_at,
        "source": "omada_s23_z2_flip",
        "sim_paused": paused,
        "on_deadline": st.on_deadline,
        "off_deadline": st.off_deadline,
        "lights_on_at": st.lights_on_at,
        "lights_off_at": st.lights_off_at,
        "icon": "mdi:home-off" if report.vacant else "mdi:home-account",
    }
    ha_set_state(HA_SENSOR_VACANT, "on" if report.vacant else "off", attrs)
    if paused and report.vacant:
        summary = "vacant but sim PAUSED (override)"
    elif report.vacant:
        on_s = (st.on_deadline or "")[11:16] if st.on_deadline else "±15"
        summary = f"vacant — sim armed (ON ~{on_s})"
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
    on_deadline: str | None = None  # ISO: random 20:00 ±15 Warsaw
    off_deadline: str | None = None  # ISO when to turn off
    notified_on: bool = False  # one push per evening ON
    forced: bool = False  # legacy; force-on/off no longer poison the day cycle


def load_sim_state() -> SimState:
    path = state_dir() / "sim_state.json"
    if not path.exists():
        return SimState()
    try:
        raw = json.loads(path.read_text())
        # tolerate older state files missing new fields
        return SimState(
            **{k: v for k, v in raw.items() if k in SimState.__dataclass_fields__}
        )
    except Exception:
        return SimState()


def save_sim_state(st: SimState) -> None:
    (state_dir() / "sim_state.json").write_text(
        json.dumps(asdict(st), indent=2) + "\n"
    )


def turn_lights(on: bool) -> None:
    """All kitchen + salon switches together (single HA service call)."""
    service = "turn_on" if on else "turn_off"
    ha_call_service("switch", service, LIGHTS)
    print(f"lights {service}: {LIGHTS}", file=sys.stderr)


def pick_on_deadline(day: date) -> datetime:
    """Random ON at 20:00 Warsaw ± EVENING_ON_JITTER_MIN (default 15)."""
    hour = int(os.environ.get("EVENING_ON_HOUR", "20"))
    minute = int(os.environ.get("EVENING_ON_MINUTE", "0"))
    jitter = int(os.environ.get("EVENING_ON_JITTER_MIN", "15"))
    base = datetime(day.year, day.month, day.day, hour, minute, tzinfo=WARSAW)
    return base + timedelta(minutes=random.randint(-jitter, jitter))


def pick_off_deadline(day: date) -> datetime:
    """Random between 22:30 and 23:30 Europe/Warsaw on `day`."""
    start = datetime(day.year, day.month, day.day, 22, 30, tzinfo=WARSAW)
    delta = timedelta(minutes=random.randint(0, 60))
    return start + delta


def arm_day_schedule(st: SimState, today: date) -> SimState:
    """Pick tonight's ON/OFF deadlines once per Warsaw day."""
    today_s = today.isoformat()
    if st.date == today_s and st.on_deadline and st.off_deadline:
        return st
    on_dl = pick_on_deadline(today)
    off_dl = pick_off_deadline(today)
    # Guarantee off is after on (edge: late on + early off shouldn't happen, but)
    if off_dl <= on_dl:
        off_dl = on_dl + timedelta(hours=2, minutes=random.randint(0, 30))
    return SimState(
        date=today_s,
        on_deadline=on_dl.isoformat(),
        off_deadline=off_dl.isoformat(),
        lights_on_at=None,
        lights_off_at=None,
        notified_on=False,
        forced=False,
    )


def run_simulation_tick(report: VacancyReport, force: bool = False) -> SimState:
    st = load_sim_state()
    today = now_warsaw().date()
    today_s = today.isoformat()
    now = now_warsaw()
    paused = sim_paused()

    # New Warsaw day → fresh schedule
    if st.date != today_s:
        st = SimState()

    vacant = report.vacant or force

    if not vacant:
        save_sim_state(st)
        print("sim: not vacant — no light changes", file=sys.stderr)
        return st

    # Arm random ON/OFF for tonight (even if paused — so dashboard shows plan)
    st = arm_day_schedule(st, today)
    on_dl = datetime.fromisoformat(st.on_deadline)  # type: ignore[arg-type]
    off_dl = datetime.fromisoformat(st.off_deadline)  # type: ignore[arg-type]

    if paused:
        save_sim_state(st)
        print(
            f"sim: PAUSED (input_boolean.presence_sim_pause=on) — "
            f"would ON {on_dl.strftime('%H:%M')} OFF {off_dl.strftime('%H:%M')}",
            file=sys.stderr,
        )
        return st

    # Turn ON once when we reach the random deadline (all lights together)
    # Catch up until 22:15 if cron missed the exact minute
    latest_on = datetime(today.year, today.month, today.day, 22, 15, tzinfo=WARSAW)
    if st.lights_on_at is None and now >= on_dl and now <= latest_on:
        turn_lights(True)
        st.lights_on_at = now.isoformat()
        st.lights_off_at = None
        if not st.notified_on:
            notify_presence_started(now, off_dl)
            st.notified_on = True
        save_sim_state(st)
        print(f"sim: lights ON; scheduled_on={on_dl} off_deadline={off_dl}", file=sys.stderr)
        return st

    if st.lights_on_at and not st.lights_off_at and now >= off_dl:
        turn_lights(False)
        st.lights_off_at = now.isoformat()
        save_sim_state(st)
        print("sim: lights OFF", file=sys.stderr)
        return st

    save_sim_state(st)
    print(
        f"sim: idle date={st.date} scheduled_on={st.on_deadline} "
        f"on_at={st.lights_on_at} off_at={st.lights_off_at} "
        f"off_deadline={st.off_deadline} paused={paused}",
        file=sys.stderr,
    )
    return st


# ── Monitor ──────────────────────────────────────────────────────────────────

def monitor_once() -> dict:
    """
    Check whether tonight's simulation on/off happened (or was correctly skipped).
    Validates *real switch state*, not just timestamps. Log-only (no notify).
    Appends to STATE_DIR/monitor.jsonl. Designed for cron over 7 days.
    """
    report = evaluate_vacancy()
    st = load_sim_state()
    paused = sim_paused()
    today = now_warsaw().date().isoformat()
    light_states = {}
    for eid in LIGHTS:
        try:
            s, _ = ha_current_state(eid)
            light_states[eid] = s
        except Exception as e:
            light_states[eid] = f"err:{e}"

    any_on = any(v == "on" for v in light_states.values())
    all_on = all(v == "on" for v in light_states.values())
    entry: dict[str, Any] = {
        "ts": now_utc().isoformat(),
        "warsaw_date": today,
        "vacant": report.vacant,
        "paused": paused,
        "reasons": report.reasons,
        "sim_state": asdict(st),
        "lights": light_states,
        "any_light_on": any_on,
        "all_lights_on": all_on,
        "ok_expected_on": None,
        "ok_expected_off": None,
        "note": "",
    }

    now = now_warsaw()
    on_dl = datetime.fromisoformat(st.on_deadline) if st.on_deadline else None
    off_dl = datetime.fromisoformat(st.off_deadline) if st.off_deadline else None

    if paused:
        entry["note"] = "sim paused (presence_sim_pause=on) — no light expectation"
        entry["ok_expected_on"] = True
        entry["ok_expected_off"] = True
    elif not report.vacant:
        entry["note"] = "not vacant — simulation correctly idle"
        entry["ok_expected_on"] = True
        entry["ok_expected_off"] = True
    else:
        # Expect ON: ≥15 min after scheduled on_deadline, before off_deadline
        if on_dl and off_dl and now >= on_dl + timedelta(minutes=15) and now < off_dl:
            # Real switches must be on (timestamps alone are not enough)
            entry["ok_expected_on"] = any_on and bool(st.lights_on_at)
            if not any_on:
                entry["note"] = (
                    f"FAIL: vacant evening, scheduled ON {on_dl.strftime('%H:%M')} "
                    "but switches are OFF"
                )
            elif not st.lights_on_at:
                entry["note"] = "FAIL: lights ON in HA but sim_state missing lights_on_at"
        if off_dl and now >= off_dl + timedelta(minutes=10):
            entry["ok_expected_off"] = (not any_on) and bool(st.lights_off_at)
            if any_on:
                entry["note"] = (
                    f"FAIL: past off deadline {off_dl.strftime('%H:%M')} "
                    "but some lights still ON"
                )
            elif not st.lights_off_at:
                entry["note"] = "FAIL: lights off but sim_state missing lights_off_at"

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
    st = run_simulation_tick(report, force=args.force)
    push_vacancy_to_ha(report, sim=st)
    return 0 if report.vacant or args.force else 2


def cmd_force_on(_: argparse.Namespace) -> int:
    """Test only: toggle lights ON without poisoning tonight's schedule."""
    turn_lights(True)
    print(
        "forced ON (test only — does not mark evening cycle complete; no notify)",
        file=sys.stderr,
    )
    return 0


def cmd_force_off(_: argparse.Namespace) -> int:
    """Test only: toggle lights OFF without poisoning tonight's schedule."""
    turn_lights(False)
    print("forced OFF (test only — evening cycle state unchanged)", file=sys.stderr)
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
