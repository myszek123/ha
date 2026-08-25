#!/usr/bin/env python3
"""
Homelab CT 119 — scheduled jobs status → Home Assistant sensors.

Reads last-run hints (log mtime, JSON, docker images) and POSTs to HA
so the dashboard can show an overview like the LXC / InternalLinks cards.

Env:
  HA_URL   default http://192.168.1.201:8123
  HA_TOKEN long-lived access token
  STATE_DIR optional override for job data roots (default /opt/services)

Usage:
  python services_status.py              # push all sensors
  python services_status.py --dry-run    # print JSON only
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

WARSAW = ZoneInfo("Europe/Warsaw")
SERVICES_ROOT = Path(os.environ.get("STATE_DIR", "/opt/services"))


@dataclass
class JobStatus:
    id: str
    name: str
    path: str
    cron: str
    doc: str
    state: str  # ok | stale | missing | unknown | fail
    last_run: str = ""
    detail: str = ""
    links: dict[str, str] = field(default_factory=dict)


def ha_url() -> str:
    return os.environ.get("HA_URL", "http://192.168.1.201:8123").rstrip("/")


def ha_token() -> str:
    return os.environ.get("HA_TOKEN", "").strip()


def log_mtime(path: Path) -> datetime | None:
    try:
        if path.is_file():
            return datetime.fromtimestamp(path.stat().st_mtime, tz=WARSAW)
    except OSError:
        pass
    return None


def age_hours(ts: datetime | None) -> float | None:
    if not ts:
        return None
    return (datetime.now(WARSAW) - ts).total_seconds() / 3600.0


def read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def tail_fail_hint(log: Path, max_lines: int = 40) -> str:
    try:
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()[-max_lines:]
        for line in reversed(lines):
            if re.search(r"\b(FAIL|ERROR|Error|failed|Traceback)\b", line):
                return line.strip()[:180]
    except Exception:
        pass
    return ""


def classify_daily(ts: datetime | None, max_hours: float = 36) -> str:
    if not ts:
        return "missing"
    h = age_hours(ts)
    if h is None:
        return "unknown"
    if h <= max_hours:
        return "ok"
    return "stale"


def classify_monthly(ts: datetime | None, max_days: float = 40) -> str:
    if not ts:
        return "missing"
    h = age_hours(ts)
    if h is None:
        return "unknown"
    if h <= max_days * 24:
        return "ok"
    return "stale"


def classify_frequent(ts: datetime | None, max_hours: float = 1.0) -> str:
    if not ts:
        return "missing"
    h = age_hours(ts)
    if h is None:
        return "unknown"
    if h <= max_hours:
        return "ok"
    return "stale"


def fmt_ts(ts: datetime | None) -> str:
    if not ts:
        return ""
    return ts.astimezone(WARSAW).strftime("%Y-%m-%d %H:%M %Z")


def collect_jobs() -> list[JobStatus]:
    root = SERVICES_ROOT
    jobs: list[JobStatus] = []

    # charge-log
    cl_log = Path("/var/log/charge-log.log")
    cl_ts = log_mtime(cl_log)
    jobs.append(
        JobStatus(
            id="charge_log",
            name="Charge log",
            path=str(root / "charge-log"),
            cron="15 6 * * * (daily)",
            doc="https://github.com/myszek123/homelab-infra/blob/main/infra/docs/CHARGE-LOG.md",
            state=classify_daily(cl_ts),
            last_run=fmt_ts(cl_ts),
            detail=tail_fail_hint(cl_log) or "Tessie+Pstryk → Sheet",
            links={
                "sheet": "https://docs.google.com/spreadsheets/d/1Lwcs8wxJsSVpDWPbEkMqeBbBRCHI51DQARYMcVXiwQQ",
            },
        )
    )

    # wspolne-remind
    wr_log = Path("/var/log/wspolne-remind.log")
    wr_ts = log_mtime(wr_log)
    jobs.append(
        JobStatus(
            id="wspolne_remind",
            name="Wspólne remind",
            path=str(root / "wspolne-remind"),
            cron="0 8,18 * * *",
            doc="https://github.com/myszek123/homelab-infra/blob/main/infra/docs/WSPOLNE-REMIND.md",
            state=classify_daily(wr_ts, max_hours=30),
            last_run=fmt_ts(wr_ts),
            detail=tail_fail_hint(wr_log) or "Shared account payment check",
        )
    )

    # presence-sim — also reflected in HA binary_sensor; log mtime is backup
    ps_log = Path("/var/log/presence-sim.log")
    # presence_sim may log elsewhere; try state dir
    ps_state = root / "presence-sim" / "data"
    ps_ts = log_mtime(ps_log)
    if ps_state.is_dir():
        latest = None
        for p in ps_state.glob("*"):
            try:
                t = datetime.fromtimestamp(p.stat().st_mtime, tz=WARSAW)
                if latest is None or t > latest:
                    latest = t
            except OSError:
                pass
        if latest and (ps_ts is None or latest > ps_ts):
            ps_ts = latest
    jobs.append(
        JobStatus(
            id="presence_sim",
            name="Presence sim",
            path=str(root / "presence-sim"),
            cron="*/15 * * * * (+ evening monitor)",
            doc="https://github.com/myszek123/homelab-infra/blob/main/infra/docs/PRESENCE-SIM.md",
            state=classify_frequent(ps_ts, max_hours=1.5),
            last_run=fmt_ts(ps_ts),
            detail="Vacancy + evening lights",
        )
    )

    # buy-targets
    bt_log = Path("/var/log/buy-targets.log")
    bt_ts = log_mtime(bt_log)
    jobs.append(
        JobStatus(
            id="buy_targets",
            name="Buy targets",
            path=str(root / "buy-targets"),
            cron="0 7 * * 1-5 (weekdays)",
            doc="https://github.com/myszek123/homelab-infra/blob/main/infra/docs/BUY-TARGETS.md",
            state=classify_daily(bt_ts, max_hours=72),
            last_run=fmt_ts(bt_ts),
            detail=tail_fail_hint(bt_log) or "Yahoo vs Carlson buy-ins (3%)",
            links={
                "code": "https://github.com/myszek123/buy-targets",
            },
        )
    )

    # 4parents-export
    f4_log = Path("/var/log/4parents-export.log")
    f4_ts = log_mtime(f4_log)
    jobs.append(
        JobStatus(
            id="fourparents_export",
            name="4Parents export",
            path=str(root / "4parents-export"),
            cron="15 7 2 * * (2nd → previous month)",
            doc="https://github.com/myszek123/homelab-infra/blob/main/infra/docs/4PARENTS-EXPORT.md",
            state=classify_monthly(f4_ts),
            last_run=fmt_ts(f4_ts),
            detail=tail_fail_hint(f4_log) or "Attendance + daycare → Sheet",
            links={
                "sheet": "https://docs.google.com/spreadsheets/d/1HJo3MG3kdpndtoGn6PKoNdrmpcpQMMmZLG13D9FFoAc",
                "code": "https://github.com/myszek123/4parents",
            },
        )
    )

    # monthly-bills (orchestrator results on CT 119)
    mb = root / "monthly-bills" / "last-run.json"
    mb_data = read_json(mb) or {}
    mb_ts = None
    if mb_data.get("ranAt"):
        try:
            mb_ts = datetime.fromisoformat(mb_data["ranAt"].replace("Z", "+00:00"))
            if mb_ts.tzinfo is None:
                mb_ts = mb_ts.replace(tzinfo=WARSAW)
            else:
                mb_ts = mb_ts.astimezone(WARSAW)
        except Exception:
            mb_ts = log_mtime(mb)
    else:
        mb_ts = log_mtime(mb)
    eon_rc = mb_data.get("eon_rc")
    eka_rc = mb_data.get("ekartoteka_rc")
    if eon_rc is not None and eka_rc is not None:
        state = "ok" if eon_rc == 0 and eka_rc == 0 else "fail"
    else:
        state = classify_monthly(mb_ts)
    jobs.append(
        JobStatus(
            id="monthly_bills",
            name="Monthly bills (EON+ekartoteka)",
            path=str(root / "monthly-bills"),
            cron="p330: 0 6 1 * * monthly-bills.sh",
            doc="https://github.com/myszek123/homelab-infra/blob/main/infra/docs/SERVICES.md",
            state=state,
            last_run=fmt_ts(mb_ts),
            detail=f"eon_rc={eon_rc} ekartoteka_rc={eka_rc}" if eon_rc is not None else "EON + e-kartoteka orchestrator",
        )
    )

    # eon / ekartoteka presence as children of monthly bills (path check)
    for jid, name, sub, cron in (
        ("eon_scraper", "EON scraper", "eon-scraper", "via monthly-bills"),
        ("ekartoteka", "e-kartoteka", "ekartoteka", "via monthly-bills"),
    ):
        p = root / sub
        exists = p.is_dir()
        jobs.append(
            JobStatus(
                id=jid,
                name=name,
                path=str(p),
                cron=cron,
                doc="https://github.com/myszek123/homelab-infra/blob/main/infra/docs/SERVICES.md",
                state="ok" if exists else "missing",
                last_run="",
                detail="deployed" if exists else "path missing",
            )
        )

    return jobs


def push_state(entity_id: str, state: str, attributes: dict[str, Any]) -> None:
    token = ha_token()
    if not token:
        raise SystemExit("HA_TOKEN required")
    url = f"{ha_url()}/api/states/{entity_id}"
    body = json.dumps({"state": state, "attributes": attributes}).encode()
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        if resp.status not in (200, 201):
            raise RuntimeError(f"HA {entity_id} → {resp.status}")


def build_payload(jobs: list[JobStatus]) -> tuple[str, dict[str, Any]]:
    """Aggregate sensor + per-job sensors."""
    worst = "ok"
    order = {"fail": 3, "missing": 2, "stale": 1, "unknown": 1, "ok": 0}
    for j in jobs:
        if order.get(j.state, 0) > order.get(worst, 0):
            worst = j.state

    summary_lines = []
    for j in jobs:
        icon = {
            "ok": "✅",
            "stale": "⏰",
            "fail": "❌",
            "missing": "⬜",
            "unknown": "❓",
        }.get(j.state, "·")
        lr = j.last_run or "never"
        summary_lines.append(f"{icon} **{j.name}** — `{j.state}` · last `{lr}`")

    attrs: dict[str, Any] = {
        "friendly_name": "Homelab scheduled jobs",
        "icon": "mdi:server-network",
        "updated": datetime.now(WARSAW).isoformat(timespec="seconds"),
        "jobs_json": json.dumps([asdict(j) for j in jobs], ensure_ascii=False),
        "summary_md": "\n\n".join(summary_lines),
        "host": "services (CT 119)",
        "docs": "https://github.com/myszek123/homelab-infra/blob/main/infra/docs/SERVICES.md",
    }
    for j in jobs:
        prefix = f"job_{j.id}"
        attrs[f"{prefix}_state"] = j.state
        attrs[f"{prefix}_last_run"] = j.last_run
        attrs[f"{prefix}_cron"] = j.cron
        attrs[f"{prefix}_name"] = j.name
    return worst, attrs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    jobs = collect_jobs()
    state, attrs = build_payload(jobs)

    if args.dry_run:
        print(json.dumps({"state": state, "attributes": attrs}, indent=2, ensure_ascii=False)[:8000])
        return 0

    push_state("sensor.homelab_services_jobs", state, attrs)

    # One entity per main job for tiles
    for j in jobs:
        if j.id in ("eon_scraper", "ekartoteka"):
            continue  # covered under monthly_bills detail
        push_state(
            f"sensor.homelab_job_{j.id}",
            j.state,
            {
                "friendly_name": j.name,
                "icon": "mdi:calendar-clock",
                "cron": j.cron,
                "last_run": j.last_run,
                "detail": j.detail,
                "path": j.path,
                "doc": j.doc,
                **{f"link_{k}": v for k, v in j.links.items()},
            },
        )
        time.sleep(0.15)

    print(f"pushed sensor.homelab_services_jobs state={state} jobs={len(jobs)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
