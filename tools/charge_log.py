#!/usr/bin/env python3
"""
Myszolot home charging log — Tessie (home only) + Pstryk HA prices → CSV + Google Sheet.

Sources:
  - Tessie /{vin}/charges  filtered to home (Bluszczańska 40 + GPS near zone.home,
    optionally previous home labels)
  - HA long-term stats for sensor.pstryk_current_buy_price (hourly mean)

Cost:
  est_cost_pln = energy_added allocated evenly across session hours × hourly Pstryk buy price.
  Tessie's own `cost` field is kept for reference (often under-priced if not configured).

Secrets (env / ~/.env.private):
  TESSIE_TOKEN, HA_TOKEN (optional for live append), GOOGLE creds path below.

Usage:
  # full rebuild + sheet
  python charge_log.py rebuild --sheet

  # append one finished session (called from HA)
  python charge_log.py append-live --start-epoch S --end-epoch E --kwh K

Never commit tokens.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from math import atan2, cos, radians, sin, sqrt
from pathlib import Path
from zoneinfo import ZoneInfo

WARSAW = ZoneInfo("Europe/Warsaw")
VIN_DEFAULT = "5YJ3E7EB9MF886781"
SHEET_ID_DEFAULT = "1Lwcs8wxJsSVpDWPbEkMqeBbBRCHI51DQARYMcVXiwQQ"
CSV_PATH = Path.home() / "myszolot-charging-log.csv"
SA_CREDS = Path.home() / "claude-projects/rozliczenia-miesieczne/google-credentials.json"
VENV_PY = Path.home() / "claude-projects/ha/.venv-sheets/bin/python"

# Current HA zone.home (Bluszczańska 40)
HOME_LAT = 52.21063419776905
HOME_LON = 21.067841204650904
HOME_RADIUS_M = 200

# Saved-location substrings treated as "home" (current + previous flats if desired)
HOME_SAVED_SUBSTRINGS = (
    "bluszcz",          # Bluszczańska 40 — current
    # Uncomment if you want previous home included:
    # "brajnick",
)

# Columns written to CSV / Sheet
FIELDS = [
    "session_id",
    "tessie_id",
    "start_local",
    "end_local",
    "duration_min",
    "kwh_added",
    "kwh_used",
    "soc_start",
    "soc_end",
    "avg_price_pln_kwh",
    "est_cost_pln",
    "tessie_cost",
    "location",
    "saved_location",
    "is_home",
    "is_supercharger",
    "source",
    "cost_basis",
    "notes",
]


def load_env() -> None:
    env = Path.home() / ".env.private"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or not line.startswith("export "):
            continue
        body = line[len("export ") :]
        if "=" not in body:
            continue
        k, v = body.split("=", 1)
        v = v.strip().strip('"').strip("'")
        os.environ.setdefault(k.strip(), v)


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = radians(lat1), radians(lat2)
    dp = radians(lat2 - lat1)
    dl = radians(lon2 - lon1)
    a = sin(dp / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
    return 2 * r * atan2(sqrt(a), sqrt(1 - a))


def is_home_charge(c: dict) -> bool:
    if c.get("is_supercharger"):
        return False
    saved = (c.get("saved_location") or "").lower()
    loc = (c.get("location") or "").lower()
    for s in HOME_SAVED_SUBSTRINGS:
        if s in saved or s in loc:
            return True
    lat, lon = c.get("latitude"), c.get("longitude")
    if lat is not None and lon is not None:
        return haversine_m(float(lat), float(lon), HOME_LAT, HOME_LON) <= HOME_RADIUS_M
    return False


def ts_local(epoch: int | float) -> datetime:
    return datetime.fromtimestamp(float(epoch), tz=timezone.utc).astimezone(WARSAW)


def fmt_local(epoch: int | float) -> str:
    return ts_local(epoch).strftime("%Y-%m-%d %H:%M")


# ── Tessie ───────────────────────────────────────────────────────────────────

def tessie_get(path: str, params: dict | None = None) -> dict:
    token = os.environ.get("TESSIE_TOKEN")
    if not token:
        raise SystemExit("TESSIE_TOKEN missing (set in ~/.env.private)")
    q = f"?{urllib.parse.urlencode(params)}" if params else ""
    url = f"https://api.tessie.com{path}{q}"
    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.load(resp)


def fetch_all_tessie_charges(vin: str) -> list[dict]:
    """Tessie returns at most ~335 recent charges; offset is broken, use high limit."""
    data = tessie_get(f"/{vin}/charges", {"limit": 5000})
    return data.get("results") or []


# ── HA prices ────────────────────────────────────────────────────────────────

def export_pstryk_prices() -> dict[int, float]:
    """Hourly mean buy price keyed by UTC hour-start epoch (int)."""
    script = r"""
import sqlite3, json
c = sqlite3.connect("/config/home-assistant_v2.db").cursor()
mid = c.execute(
    "select id from statistics_meta where statistic_id=?",
    ("sensor.pstryk_current_buy_price",),
).fetchone()
if not mid:
    print("[]")
else:
    rows = c.execute(
        "select start_ts, mean from statistics where metadata_id=? order by start_ts",
        (mid[0],),
    ).fetchall()
    print(json.dumps([{"ts": r[0], "price": r[1]} for r in rows if r[1] is not None]))
"""
    proc = subprocess.run(
        ["ssh", "ha", "sudo", "docker", "exec", "-i", "ha", "python3"],
        input=script,
        text=True,
        capture_output=True,
        check=True,
    )
    rows = json.loads(proc.stdout)
    # normalize to hour floor
    out: dict[int, float] = {}
    for r in rows:
        ts = int(r["ts"])
        hour = ts - (ts % 3600)
        out[hour] = float(r["price"])
    return out


def load_prices_json(path: Path) -> dict[int, float]:
    rows = json.loads(path.read_text())
    out: dict[int, float] = {}
    for r in rows:
        ts = int(r["ts"])
        hour = ts - (ts % 3600)
        out[hour] = float(r["price"])
    return out


def estimate_cost(start: int, end: int, kwh: float, prices: dict[int, float]) -> tuple[float | None, float | None]:
    """Return (est_cost, avg_price) using even kWh allocation across hours."""
    if kwh is None or kwh <= 0 or end <= start:
        return None, None
    # build hour list covering [start, end)
    hours: list[int] = []
    t = start - (start % 3600)
    while t < end:
        hours.append(t)
        t += 3600
    if not hours:
        hours = [start - (start % 3600)]

    # weight by seconds spent in each hour within session
    weights: list[float] = []
    for h in hours:
        h_end = h + 3600
        seg_start = max(start, h)
        seg_end = min(end, h_end)
        weights.append(max(0.0, seg_end - seg_start))
    total_w = sum(weights) or 1.0
    cost = 0.0
    priced = 0.0
    for h, w in zip(hours, weights):
        p = prices.get(h)
        if p is None:
            continue
        share = (w / total_w) * kwh
        cost += share * p
        priced += share
    if priced <= 0:
        return None, None
    return round(cost, 2), round(cost / priced, 4)


# ── row building ─────────────────────────────────────────────────────────────

def charge_to_row(c: dict, prices: dict[int, float], source: str = "tessie") -> dict:
    start = int(c["started_at"])
    end = int(c.get("ended_at") or start)
    kwh = float(c.get("energy_added") or 0)
    kwh_used = c.get("energy_used")
    est, avg = estimate_cost(start, end, kwh, prices)
    start_dt = ts_local(start)
    return {
        "session_id": start_dt.strftime("%Y%m%d-%H%M"),
        "tessie_id": c.get("id", ""),
        "start_local": fmt_local(start),
        "end_local": fmt_local(end),
        "duration_min": int(round((end - start) / 60)),
        "kwh_added": round(kwh, 2),
        "kwh_used": round(float(kwh_used), 2) if kwh_used is not None else "",
        "soc_start": c.get("starting_battery", ""),
        "soc_end": c.get("ending_battery", ""),
        "avg_price_pln_kwh": avg if avg is not None else "",
        "est_cost_pln": est if est is not None else "",
        "tessie_cost": c.get("cost", ""),
        "location": c.get("location") or "",
        "saved_location": c.get("saved_location") or "",
        "is_home": "yes",
        "is_supercharger": "yes" if c.get("is_supercharger") else "no",
        "source": source,
        "cost_basis": "pstryk_buy_hourly_x_tessie_kwh",
        "notes": "Home only (Bluszczańska / zone.home). Cost from HA Pstryk buy price.",
    }


# ── CSV / Sheet ──────────────────────────────────────────────────────────────

def write_csv(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})


def push_sheet(rows: list[dict], sheet_id: str) -> str:
    """Push via venv python if needed for gspread."""
    # Prefer in-process if import works
    try:
        from google.oauth2.service_account import Credentials
        import gspread
    except ImportError:
        # re-exec with venv
        if VENV_PY.exists():
            payload = {
                "rows": rows,
                "sheet_id": sheet_id,
                "creds": str(SA_CREDS),
                "fields": FIELDS,
            }
            tmp = Path("/tmp/charge_log_sheet_payload.json")
            tmp.write_text(json.dumps(payload))
            code = r"""
import json, sys
from google.oauth2.service_account import Credentials
import gspread
p=json.load(open("/tmp/charge_log_sheet_payload.json"))
scopes=["https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive"]
gc=gspread.authorize(Credentials.from_service_account_file(p["creds"], scopes=scopes))
sh=gc.open_by_key(p["sheet_id"])
title="charging_log"
try:
    ws=sh.worksheet(title); ws.clear()
except Exception:
    ws=sh.add_worksheet(title=title, rows=max(2000,len(p["rows"])+20), cols=25)
fields=p["fields"]
values=[fields]+[[r.get(f,"") for f in fields] for r in p["rows"]]
ws.update(range_name="A1", values=values, value_input_option="USER_ENTERED")
# also clear/update sheet1 summary
try:
    s1=sh.sheet1
    s1.clear()
    s1.update(range_name="A1", values=[
        ["Myszolot home charging log"],
        ["Updated", __import__("datetime").datetime.now().isoformat(timespec="seconds")],
        ["Sessions", len(p["rows"])],
        ["See tab: charging_log"],
    ], value_input_option="USER_ENTERED")
except Exception as e:
    print("sheet1 warn", e, file=sys.stderr)
print(sh.url)
"""
            proc = subprocess.run(
                [str(VENV_PY), "-c", code],
                text=True,
                capture_output=True,
            )
            if proc.returncode != 0:
                raise RuntimeError(proc.stderr or proc.stdout)
            return proc.stdout.strip()
        raise

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
    except Exception:
        ws = sh.add_worksheet(title=title, rows=max(2000, len(rows) + 20), cols=25)
    values = [FIELDS] + [[r.get(f, "") for f in FIELDS] for r in rows]
    ws.update(range_name="A1", values=values, value_input_option="USER_ENTERED")
    return sh.url


def append_csv_row(row: dict, path: Path) -> None:
    exists = path.exists() and path.stat().st_size > 0
    with path.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        if not exists:
            w.writeheader()
        w.writerow({k: row.get(k, "") for k in FIELDS})


def append_sheet_row(row: dict, sheet_id: str) -> None:
    payload = {"row": row, "sheet_id": sheet_id, "creds": str(SA_CREDS), "fields": FIELDS}
    Path("/tmp/charge_log_append_payload.json").write_text(json.dumps(payload))
    code = r"""
import json
from google.oauth2.service_account import Credentials
import gspread
p=json.load(open("/tmp/charge_log_append_payload.json"))
scopes=["https://www.googleapis.com/auth/spreadsheets","https://www.googleapis.com/auth/drive"]
gc=gspread.authorize(Credentials.from_service_account_file(p["creds"], scopes=scopes))
sh=gc.open_by_key(p["sheet_id"])
ws=sh.worksheet("charging_log")
ws.append_row([p["row"].get(f,"") for f in p["fields"]], value_input_option="USER_ENTERED")
print("ok")
"""
    py = str(VENV_PY) if VENV_PY.exists() else sys.executable
    subprocess.run([py, "-c", code], check=True)


# ── commands ─────────────────────────────────────────────────────────────────

def cmd_rebuild(args: argparse.Namespace) -> int:
    vin = args.vin
    print(f"Fetching Tessie charges for {vin}…", file=sys.stderr)
    all_charges = fetch_all_tessie_charges(vin)
    home = [c for c in all_charges if is_home_charge(c)]
    print(
        f"Tessie: {len(all_charges)} total, {len(home)} home (non-supercharger)",
        file=sys.stderr,
    )

    if args.prices_json:
        prices = load_prices_json(args.prices_json)
    else:
        print("Exporting Pstryk prices from HA…", file=sys.stderr)
        prices = export_pstryk_prices()
    print(f"Price hours: {len(prices)}", file=sys.stderr)

    rows = [charge_to_row(c, prices) for c in sorted(home, key=lambda x: x["started_at"])]
    # drop zero-energy noise
    rows = [r for r in rows if float(r["kwh_added"] or 0) >= 0.1]

    write_csv(rows, args.out)
    kwh = sum(float(r["kwh_added"]) for r in rows)
    cost = sum(float(r["est_cost_pln"]) for r in rows if r["est_cost_pln"] != "")
    missing = sum(1 for r in rows if r["est_cost_pln"] == "")
    print(f"sessions={len(rows)}  kWh={kwh:.1f}  est_PLN={cost:.2f}  missing_price={missing}")
    print(f"csv={args.out}")
    if rows:
        print(f"range={rows[0]['start_local']} → {rows[-1]['start_local']}")

    if args.sheet:
        url = push_sheet(rows, args.sheet_id)
        print(f"sheet={url}")
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    """Fetch latest Tessie home charges; full rewrite CSV+sheet (small dataset)."""
    return cmd_rebuild(args)


def cmd_append_live(args: argparse.Namespace) -> int:
    """Append a single finished home session (from HA automation)."""
    if args.prices_json:
        prices = load_prices_json(args.prices_json)
    else:
        try:
            prices = export_pstryk_prices()
        except Exception:
            prices = {}

    c = {
        "id": "",
        "started_at": args.start_epoch,
        "ended_at": args.end_epoch,
        "energy_added": args.kwh,
        "energy_used": args.kwh_used,
        "starting_battery": args.soc_start,
        "ending_battery": args.soc_end,
        "location": "home",
        "saved_location": "Bluszczańska 40",
        "is_supercharger": False,
        "cost": "",
    }
    row = charge_to_row(c, prices, source="ha_live")
    row["notes"] = "Live append from HA on charge stop"
    append_csv_row(row, args.out)
    print(f"appended csv {args.out}: {row['start_local']} {row['kwh_added']} kWh {row['est_cost_pln']} PLN")
    if args.sheet:
        try:
            append_sheet_row(row, args.sheet_id)
            print("appended sheet ok")
        except Exception as e:
            print(f"sheet append failed: {e}", file=sys.stderr)
            return 2
    return 0


def main() -> int:
    load_env()
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    rb = sub.add_parser("rebuild", help="Full rebuild from Tessie + HA prices")
    rb.add_argument("--vin", default=VIN_DEFAULT)
    rb.add_argument("--out", type=Path, default=CSV_PATH)
    rb.add_argument("--prices-json", type=Path)
    rb.add_argument("--sheet", action="store_true")
    rb.add_argument("--sheet-id", default=SHEET_ID_DEFAULT)
    rb.set_defaults(func=cmd_rebuild)

    al = sub.add_parser("append-live", help="Append one finished session")
    al.add_argument("--start-epoch", type=int, required=True)
    al.add_argument("--end-epoch", type=int, required=True)
    al.add_argument("--kwh", type=float, required=True)
    al.add_argument("--kwh-used", type=float, default=None)
    al.add_argument("--soc-start", type=float, default=None)
    al.add_argument("--soc-end", type=float, default=None)
    al.add_argument("--out", type=Path, default=CSV_PATH)
    al.add_argument("--prices-json", type=Path)
    al.add_argument("--sheet", action="store_true")
    al.add_argument("--sheet-id", default=SHEET_ID_DEFAULT)
    al.set_defaults(func=cmd_append_live)

    sy = sub.add_parser("sync", help="Same as rebuild (refresh CSV+optional sheet)")
    sy.add_argument("--vin", default=VIN_DEFAULT)
    sy.add_argument("--out", type=Path, default=CSV_PATH)
    sy.add_argument("--prices-json", type=Path)
    sy.add_argument("--sheet", action="store_true")
    sy.add_argument("--sheet-id", default=SHEET_ID_DEFAULT)
    sy.set_defaults(func=cmd_sync)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
