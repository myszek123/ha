#!/usr/bin/env python3
"""
Rebuild Myszolot home-charging session log from Home Assistant long-term statistics.

Sources (HA recorder statistics, hourly):
  - sensor.myszolot_charge_energy_added  (lifetime sum → per-hour kWh deltas)
  - sensor.pstryk_current_buy_price      (hourly mean — YOUR Pstryk buy price)
  - sensor.autel_energy_active_import_register[_2]  (optional wall kWh cross-check)

Output:
  ~/myszolot-charging-log.csv

Optional Google Sheet push (needs Drive API enabled + sheet shared with the
service account in rozliczenia-miesieczne/google-credentials.json):
  --sheet-id 1Lwcs8wxJsSVpDWPbEkMqeBbBRCHI51DQARYMcVXiwQQ

Cost basis note:
  est_cost_pln = Σ (hour_kWh_car × pstryk_buy_price_mean). This is the Pstryk
  *account* buy price stored in HA — not public PSE. Distribution/delivery is
  only included if already embedded in that buy price (HA currently shows
  Distribution cost = 0 on Pstryk financial sensors).
"""
from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

WARSAW = ZoneInfo("Europe/Warsaw")
DEFAULT_OUT = Path.home() / "myszolot-charging-log.csv"
MIN_KWH = 0.15
GAP_HOURS = 2
SHEET_ID_DEFAULT = "1Lwcs8wxJsSVpDWPbEkMqeBbBRCHI51DQARYMcVXiwQQ"
SA_CREDS = Path.home() / "claude-projects/rozliczenia-miesieczne/google-credentials.json"


def export_stats_via_ssh() -> dict:
    """Pull hourly statistics JSON from HA container DB."""
    script = r"""
import sqlite3, json
c = sqlite3.connect("/config/home-assistant_v2.db").cursor()
out = {}
for sid in [
    "sensor.myszolot_charge_energy_added",
    "sensor.pstryk_current_buy_price",
    "sensor.autel_energy_active_import_register_2",
    "sensor.autel_energy_active_import_register",
]:
    mid = c.execute(
        "select id from statistics_meta where statistic_id=?", (sid,)
    ).fetchone()
    if not mid:
        out[sid] = []
        continue
    rows = c.execute(
        "select start_ts, mean, sum, state from statistics "
        "where metadata_id=? order by start_ts",
        (mid[0],),
    ).fetchall()
    out[sid] = [
        {"ts": r[0], "mean": r[1], "sum": r[2], "state": r[3]} for r in rows
    ]
print(json.dumps(out))
"""
    cmd = ["ssh", "ha", f"sudo docker exec ha python3 -c {json.dumps(script)}"]
    # safer: pipe script
    proc = subprocess.run(
        ["ssh", "ha", "sudo", "docker", "exec", "-i", "ha", "python3"],
        input=script,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(proc.stdout)


def ts_local(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(WARSAW)


def rebuild(data: dict) -> list[dict]:
    energy = data["sensor.myszolot_charge_energy_added"]
    prices = {
        int(r["ts"]): r["mean"]
        for r in data["sensor.pstryk_current_buy_price"]
        if r["mean"] is not None
    }
    autel: dict[int, float] = {}
    for key in (
        "sensor.autel_energy_active_import_register_2",
        "sensor.autel_energy_active_import_register",
    ):
        for r in data.get(key, []):
            val = r.get("sum") if r.get("sum") is not None else r.get("state")
            if val is not None:
                autel[int(r["ts"])] = float(val)

    points: list[tuple[int, float]] = []
    prev_sum = None
    for r in energy:
        s = r.get("sum")
        if s is None:
            continue
        ts = int(r["ts"])
        if prev_sum is None:
            prev_sum = s
            continue
        delta = max(0.0, float(s) - float(prev_sum))
        if float(s) + 1.0 < float(prev_sum):
            delta = 0.0
        points.append((ts, delta))
        prev_sum = s

    sessions: list[dict] = []
    cur = None

    def close(c):
        if c and c["kwh"] >= MIN_KWH:
            sessions.append(c)

    for ts, delta in points:
        if delta >= 0.05:
            price = prices.get(ts)
            if cur is None or ts - cur["end_ts"] > GAP_HOURS * 3600:
                close(cur)
                cur = {
                    "start_ts": ts,
                    "end_ts": ts + 3600,
                    "kwh": 0.0,
                    "cost": 0.0,
                    "hours": 0,
                    "price_sum": 0.0,
                    "priced_kwh": 0.0,
                }
            cur["end_ts"] = ts + 3600
            cur["kwh"] += delta
            cur["hours"] += 1
            if price is not None:
                cur["cost"] += delta * price
                cur["price_sum"] += price * delta
                cur["priced_kwh"] += delta
        else:
            if cur is not None and ts - cur["end_ts"] > GAP_HOURS * 3600:
                close(cur)
                cur = None
    close(cur)

    def autel_kwh(start_ts: int, end_ts: int) -> float | None:
        keys = sorted(autel)
        if not keys:
            return None
        before = [k for k in keys if k <= start_ts]
        after = [k for k in keys if k >= end_ts - 3600]
        if before and after:
            d = autel[after[0]] - autel[before[-1]]
            if 0 <= d < 200:
                return d
        inside = [k for k in keys if start_ts <= k <= end_ts]
        if len(inside) >= 2:
            d = autel[inside[-1]] - autel[inside[0]]
            if 0 <= d < 200:
                return d
        return None

    rows = []
    for s in sessions:
        start = ts_local(s["start_ts"])
        end = ts_local(s["end_ts"])
        avg = (s["price_sum"] / s["priced_kwh"]) if s["priced_kwh"] else None
        wall = autel_kwh(s["start_ts"], s["end_ts"])
        rows.append(
            {
                "session_id": start.strftime("%Y%m%d-%H%M"),
                "start_local": start.strftime("%Y-%m-%d %H:%M"),
                "end_local": end.strftime("%Y-%m-%d %H:%M"),
                "duration_min": int(round((s["end_ts"] - s["start_ts"]) / 60)),
                "kwh_car": round(s["kwh"], 2),
                "kwh_wall_autel": round(wall, 2) if wall is not None else "",
                "avg_price_pln_kwh": round(avg, 4) if avg is not None else "",
                "est_cost_pln": round(s["cost"], 2),
                "priced_hours": s["hours"],
                "source": "ha_statistics",
                "cost_basis": "pstryk_buy_price_hourly_x_car_kwh",
                "notes": (
                    "Pstryk account buy price (not public PSE). "
                    "kwh_car from Tesla energy_added lifetime sum; "
                    "kwh_wall from Autel meter when available (home only)."
                ),
            }
        )
    return rows


def write_csv(rows: list[dict], path: Path) -> None:
    fields = list(rows[0].keys()) if rows else [
        "session_id", "start_local", "end_local", "duration_min", "kwh_car",
        "kwh_wall_autel", "avg_price_pln_kwh", "est_cost_pln", "priced_hours",
        "source", "cost_basis", "notes",
    ]
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def push_sheet(rows: list[dict], sheet_id: str) -> str:
    from google.oauth2.service_account import Credentials
    import gspread

    if not SA_CREDS.exists():
        raise SystemExit(f"Missing credentials: {SA_CREDS}")
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(str(SA_CREDS), scopes=scopes)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(sheet_id)
    title = "charging_log"
    try:
        ws = sh.worksheet(title)
        ws.clear()
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=title, rows=max(1000, len(rows) + 10), cols=20)
    fields = list(rows[0].keys()) if rows else []
    values = [fields] + [[r.get(f, "") for f in fields] for r in rows]
    ws.update("A1", values, value_input_option="USER_ENTERED")
    return sh.url


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stats-json", type=Path, help="Pre-exported stats JSON")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--sheet-id", nargs="?", const=SHEET_ID_DEFAULT, default=None)
    args = ap.parse_args()

    if args.stats_json:
        data = json.loads(args.stats_json.read_text())
    else:
        print("Exporting statistics from HA (ssh ha)…", file=sys.stderr)
        data = export_stats_via_ssh()

    rows = rebuild(data)
    write_csv(rows, args.out)
    total_kwh = sum(r["kwh_car"] for r in rows)
    total_cost = sum(r["est_cost_pln"] for r in rows)
    print(f"sessions={len(rows)}  kWh={total_kwh:.1f}  est_PLN={total_cost:.2f}")
    print(f"csv={args.out}")

    if args.sheet_id:
        try:
            url = push_sheet(rows, args.sheet_id)
            print(f"sheet={url}")
        except Exception as e:
            print(f"sheet_push_failed: {type(e).__name__}: {e}", file=sys.stderr)
            print(
                "Share the sheet with automations@electric-attic-440118-j5.iam.gserviceaccount.com "
                "as Editor, and enable Google Drive API + Sheets API on project electric-attic-440118-j5.",
                file=sys.stderr,
            )
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
