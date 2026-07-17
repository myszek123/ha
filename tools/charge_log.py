#!/usr/bin/env python3
"""
Home EV charging log + monthly cost split (EV / homelab / fixed).

Data sources (all automated, no PDFs):
  - Tessie charges API — home sessions only (Bluszczańska / zone.home)
  - HA recorder stats — hourly sensor.pstryk_current_buy_price
    (= Pstryk API price_gross / full_price: energy + var dist + service + VAT + excise)
  - Pstryk unified-metrics month API — house kWh + full monthly total + cost breakdown
    (matches invoice; read via HA host API key)

Sheet tabs:
  charging_log  — per session, full variable cost
  monthly       — house total, EV, homelab, fixed fees

  python charge_log.py rebuild --sheet
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request
from calendar import monthrange
from collections import defaultdict
from datetime import datetime, timezone
from math import atan2, cos, radians, sin, sqrt
from pathlib import Path
from zoneinfo import ZoneInfo

WARSAW = ZoneInfo("Europe/Warsaw")
VIN_DEFAULT = "5YJ3E7EB9MF886781"
SHEET_ID_DEFAULT = "1Lwcs8wxJsSVpDWPbEkMqeBbBRCHI51DQARYMcVXiwQQ"
CSV_PATH = Path.home() / "myszolot-charging-log.csv"
MONTHLY_CSV = Path.home() / "myszolot-monthly-costs.csv"
SA_CREDS = Path.home() / "claude-projects/rozliczenia-miesieczne/google-credentials.json"
VENV_PY = Path.home() / "claude-projects/ha/.venv-sheets/bin/python"

HOME_LAT = 52.21063419776905
HOME_LON = 21.067841204650904
HOME_RADIUS_M = 200
HOME_SAVED_SUBSTRINGS = ("bluszcz",)

# Skip sessions before this date (Warsaw). Earlier kWh exist but prices were
# another supplier (Pstryk meter only); costs would be wrong.
MIN_SESSION_DATE = datetime(2026, 3, 1, tzinfo=WARSAW)

# Per-session log (full variable price = Pstryk price_gross)
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
    "avg_full_price_pln_kwh",  # price_gross average
    "full_cost_pln",           # variable full cost of session
    "tessie_cost",
    "location",
    "saved_location",
    "source",
    "cost_basis",
    "notes",
]

# Sheet charging_log — one row per session (dates, duration, kWh, full price)
SHEET_SESSION_FIELDS = [
    "start_local",
    "end_local",
    "duration_min",
    "kwh_added",
    "avg_full_price_pln_kwh",
    "full_cost_pln",
    "soc_start",
    "soc_end",
]

# Detailed monthly (CSV + optional internal use)
MONTHLY_FIELDS = [
    "month",
    "house_kwh",
    "house_total_pln",
    "ev_kwh",
    "ev_full_variable_pln",
    "ev_fixed_share_pln",
    "ev_total_pln",
    "homelab_kwh",
    "homelab_full_variable_pln",
    "homelab_fixed_share_pln",
    "homelab_total_pln",
    "fixed_fees_pln",
    "energy_net",
    "service_net",
    "var_dist_net",
    "fix_dist_net",
    "excise",
    "vat",
    "source",
]

# Sheet tab "monthly_total" — fixed fees already folded into EV / homelab
MONTHLY_TOTAL_FIELDS = [
    "month",
    "grand_total",   # full PPE bill (Pstryk API ≈ invoice)
    "ev_total",      # charging, full cost incl. share of fixed fees
    "homelab_total", # rest of home (incl. lab), full cost incl. share of fixed
]

# Sheet tab "other_locations" — all Tessie history (no March cutoff)
OTHER_LOC_FIELDS = [
    "place",
    "sessions",
    "total_kwh",
    "first_session",
    "last_session",
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
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = radians(lat1), radians(lat2)
    dp, dl = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dp / 2) ** 2 + cos(p1) * cos(p2) * sin(dl / 2) ** 2
    return 2 * r * atan2(sqrt(a), sqrt(1 - a))


def _loc_blob(c: dict) -> str:
    return f"{c.get('saved_location') or ''} {c.get('location') or ''}".lower()


def is_home_charge(c: dict) -> bool:
    if c.get("is_supercharger"):
        return False
    blob = _loc_blob(c)
    for s in HOME_SAVED_SUBSTRINGS:
        if s in blob:
            return True
    lat, lon = c.get("latitude"), c.get("longitude")
    if lat is not None and lon is not None:
        return haversine_m(float(lat), float(lon), HOME_LAT, HOME_LON) <= HOME_RADIUS_M
    return False


def is_lodz_charge(c: dict) -> bool:
    """Mother / Łódź city charging (not Supercharger)."""
    if c.get("is_supercharger"):
        return False
    blob = _loc_blob(c)
    # Family addresses in Łódź
    if any(x in blob for x in ("leżakowa", "lezakowa", "plenerowa", "paradna")):
        return True
    # City name (not only "Łódź Voivodeship" for remote towns)
    if "łódź, łódź" in blob or "lodz, lodz" in blob:
        return True
    if "łódź, łódź voivodeship" in blob or "lodz, lodz voivodeship" in blob:
        return True
    return False


def is_brajniki_charge(c: dict) -> bool:
    if c.get("is_supercharger"):
        return False
    blob = _loc_blob(c)
    return "brajnick" in blob or "brajniki" in blob


def is_supercharger_charge(c: dict) -> bool:
    return bool(c.get("is_supercharger"))


def summarize_location_group(charges: list[dict], place: str) -> dict:
    if not charges:
        return {
            "place": place,
            "sessions": 0,
            "total_kwh": 0.0,
            "first_session": "",
            "last_session": "",
        }
    ordered = sorted(charges, key=lambda c: int(c["started_at"]))
    kwh = sum(float(c.get("energy_added") or 0) for c in ordered)
    return {
        "place": place,
        "sessions": len(ordered),
        "total_kwh": round(kwh, 1),
        "first_session": fmt_local(ordered[0]["started_at"]),
        "last_session": fmt_local(ordered[-1]["started_at"]),
    }


def build_other_location_rows(all_charges: list[dict]) -> list[dict]:
    """All known Tessie history — home + travel locations (kWh only)."""
    home = [c for c in all_charges if is_home_charge(c) and float(c.get("energy_added") or 0) >= 0.1]
    lodz = [c for c in all_charges if is_lodz_charge(c) and float(c.get("energy_added") or 0) >= 0.1]
    braj = [c for c in all_charges if is_brajniki_charge(c) and float(c.get("energy_added") or 0) >= 0.1]
    sc = [c for c in all_charges if is_supercharger_charge(c) and float(c.get("energy_added") or 0) >= 0.1]
    return [
        summarize_location_group(home, "Bluszczańska"),
        summarize_location_group(lodz, "Łódź"),
        summarize_location_group(braj, "Brajniki"),
        summarize_location_group(sc, "Superchargers"),
    ]


def ts_local(epoch: int | float) -> datetime:
    return datetime.fromtimestamp(float(epoch), tz=timezone.utc).astimezone(WARSAW)


def fmt_local(epoch: int | float) -> str:
    return ts_local(epoch).strftime("%Y-%m-%d %H:%M")


# ── Tessie ───────────────────────────────────────────────────────────────────

def tessie_get(path: str, params: dict | None = None) -> dict:
    token = os.environ.get("TESSIE_TOKEN")
    if not token:
        raise SystemExit("TESSIE_TOKEN missing (~/.env.private)")
    q = f"?{urllib.parse.urlencode(params)}" if params else ""
    req = urllib.request.Request(
        f"https://api.tessie.com{path}{q}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        return json.load(resp)


def fetch_all_tessie_charges(vin: str) -> list[dict]:
    return (tessie_get(f"/{vin}/charges", {"limit": 5000}).get("results") or [])


# ── Prices (full_price / price_gross from HA stats) ──────────────────────────

def export_pstryk_prices() -> dict[int, float]:
    """Hourly full price (price_gross) from HA recorder statistics."""
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
    out: dict[int, float] = {}
    for r in json.loads(proc.stdout):
        ts = int(r["ts"])
        out[ts - (ts % 3600)] = float(r["price"])
    return out


def load_prices_json(path: Path) -> dict[int, float]:
    out: dict[int, float] = {}
    for r in json.loads(path.read_text()):
        ts = int(r["ts"])
        out[ts - (ts % 3600)] = float(r["price"])
    return out


def estimate_full_cost(
    start: int, end: int, kwh: float, prices: dict[int, float]
) -> tuple[float | None, float | None]:
    """Session full variable cost using price_gross series."""
    if kwh is None or kwh <= 0 or end <= start:
        return None, None
    hours: list[int] = []
    t = start - (start % 3600)
    while t < end:
        hours.append(t)
        t += 3600
    if not hours:
        hours = [start - (start % 3600)]
    weights = []
    for h in hours:
        weights.append(max(0.0, min(end, h + 3600) - max(start, h)))
    total_w = sum(weights) or 1.0
    cost = priced = 0.0
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


# ── Pstryk monthly totals (API, not PDF) ─────────────────────────────────────

def fetch_pstryk_monthly(year: int, month: int) -> dict | None:
    """
    Full monthly bill components from Pstryk unified-metrics.
    Runs on HA host so API key never leaves the server.
    """
    # window in UTC; API uses for_tz=Europe/Warsaw
    start = f"{year:04d}-{month:02d}-01T00:00:00Z"
    if month == 12:
        end = f"{year + 1:04d}-01-01T00:00:00Z"
    else:
        end = f"{year:04d}-{month + 1:02d}-01T00:00:00Z"

    # Run on HA host so config path is /opt/ha/config/...
    # Pick frame whose local midpoint falls in the target calendar month.
    script = f"""
import json, urllib.request
from pathlib import Path
from datetime import datetime, timezone, timedelta
ce = json.loads(Path("/opt/ha/config/.storage/core.config_entries").read_text())
api_key = next(e["data"]["api_key"] for e in ce["data"]["entries"] if e.get("domain") == "pstryk")
# Pad window by 2 days so Warsaw month is fully covered
url = (
    "https://api.pstryk.pl/integrations/meter-data/unified-metrics/"
    "?metrics=meter_values,cost&resolution=month"
    "&window_start={start}&window_end={end}&for_tz=Europe/Warsaw"
)
req = urllib.request.Request(url, headers={{"Authorization": api_key, "Accept": "application/json"}})
with urllib.request.urlopen(req, timeout=60) as r:
    data = json.load(r)
frames = data.get("frames") or []
target = "{year:04d}-{month:02d}"
chosen = None
for f in frames:
    try:
        s = datetime.fromisoformat(f["start"].replace("Z", "+00:00"))
        e = datetime.fromisoformat(f["end"].replace("Z", "+00:00"))
        mid = s + (e - s) / 2
        # Europe/Warsaw ≈ UTC+1/+2; use mid date in local-ish by adding 2h
        local_ym = (mid + timedelta(hours=2)).strftime("%Y-%m")
        if local_ym == target:
            chosen = f
            break
    except Exception:
        continue
if chosen is None and frames:
    # fallback: max import in response
    chosen = max(
        frames,
        key=lambda f: (f.get("metrics") or {{}}).get("meter_values", {{}}).get(
            "energy_active_import_register"
        ) or 0,
    )
print(json.dumps(chosen) if chosen else "null")
"""
    proc = subprocess.run(
        ["ssh", "ha", "sudo", "python3"],
        input=script,
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0 or not proc.stdout.strip() or proc.stdout.strip() == "null":
        if proc.stderr:
            print(f"  pstryk {year}-{month:02d} err: {proc.stderr[:200]}", file=sys.stderr)
        return None
    fr = json.loads(proc.stdout)
    m = (fr.get("metrics") or {}).get("meter_values") or {}
    c = (fr.get("metrics") or {}).get("cost") or {}
    kwh = float(m.get("energy_active_import_register") or 0)
    if kwh <= 0 and float(c.get("energy_import_cost") or 0) <= 0:
        return None
    fix_net = float(c.get("fix_dist_cost_net") or 0)
    # Prefer API VAT if present; fixed brutto = fix_net + share of VAT is hard —
    # use fix_net * 1.23 for allocation base (matches invoice structure).
    return {
        "house_kwh": round(kwh, 2),
        "house_total_pln": round(float(c.get("energy_import_cost") or 0), 2),
        "energy_net": round(float(c.get("energy_cost_net") or 0), 2),
        "service_net": round(float(c.get("service_cost_net") or 0), 2),
        "var_dist_net": round(float(c.get("var_dist_cost_net") or 0), 2),
        "fix_dist_net": round(fix_net, 2),
        "fixed_fees_pln": round(fix_net * 1.23, 2),
        "excise": round(float(c.get("excise") or 0), 2),
        "vat": round(float(c.get("vat") or 0), 2),
    }


def build_monthly_rows(session_rows: list[dict]) -> list[dict]:
    by_month: dict[str, list[dict]] = defaultdict(list)
    for r in session_rows:
        by_month[r["start_local"][:7]].append(r)

    months = sorted(by_month.keys())
    # also ensure current + previous month even if no EV sessions
    now = datetime.now(WARSAW)
    for y, m in [(now.year, now.month), (now.year if now.month > 1 else now.year - 1, now.month - 1 or 12)]:
        key = f"{y:04d}-{m:02d}"
        if key not in by_month:
            months.append(key)
    months = sorted(set(months))

    out = []
    for ym in months:
        y, m = map(int, ym.split("-"))
        sessions = by_month.get(ym, [])
        ev_kwh = sum(float(s["kwh_added"] or 0) for s in sessions)
        ev_var = sum(
            float(s["full_cost_pln"])
            for s in sessions
            if s.get("full_cost_pln") not in ("", None)
        )

        api = fetch_pstryk_monthly(y, m)
        if api:
            house_kwh = api["house_kwh"]
            house_total = api["house_total_pln"]
            fixed = api["fixed_fees_pln"]
            share = (ev_kwh / house_kwh) if house_kwh > 0 else 0.0
            # Cap share at 1
            share = min(share, 1.0)
            ev_fixed = round(fixed * share, 2)
            home_kwh = round(max(0.0, house_kwh - ev_kwh), 2)
            # Homelab variable ≈ house total − fixed − EV variable
            house_var = round(house_total - fixed, 2)
            home_var = round(max(0.0, house_var - ev_var), 2)
            home_fixed = round(fixed - ev_fixed, 2)
            row = {
                "month": ym,
                "house_kwh": house_kwh,
                "house_total_pln": house_total,
                "ev_kwh": round(ev_kwh, 2),
                "ev_full_variable_pln": round(ev_var, 2),
                "ev_fixed_share_pln": ev_fixed,
                "ev_total_pln": round(ev_var + ev_fixed, 2),
                "homelab_kwh": home_kwh,
                "homelab_full_variable_pln": home_var,
                "homelab_fixed_share_pln": home_fixed,
                "homelab_total_pln": round(home_var + home_fixed, 2),
                "fixed_fees_pln": fixed,
                "energy_net": api["energy_net"],
                "service_net": api["service_net"],
                "var_dist_net": api["var_dist_net"],
                "fix_dist_net": api["fix_dist_net"],
                "excise": api["excise"],
                "vat": api["vat"],
                "source": "pstryk_api_month+tessie_ev",
            }
        else:
            # No house total — only EV side from sessions
            row = {
                "month": ym,
                "house_kwh": "",
                "house_total_pln": "",
                "ev_kwh": round(ev_kwh, 2),
                "ev_full_variable_pln": round(ev_var, 2),
                "ev_fixed_share_pln": "",
                "ev_total_pln": round(ev_var, 2),
                "homelab_kwh": "",
                "homelab_full_variable_pln": "",
                "homelab_fixed_share_pln": "",
                "homelab_total_pln": "",
                "fixed_fees_pln": "",
                "energy_net": "",
                "service_net": "",
                "var_dist_net": "",
                "fix_dist_net": "",
                "excise": "",
                "vat": "",
                "source": "tessie_ev_only_no_pstryk_month",
            }
        out.append(row)
        print(
            f"  {ym}: house={row['house_total_pln']} EV={row['ev_total_pln']} "
            f"homelab={row['homelab_total_pln']} fixed={row['fixed_fees_pln']}",
            file=sys.stderr,
        )
    return out


# ── row building ─────────────────────────────────────────────────────────────

def charge_to_row(c: dict, prices: dict[int, float], source: str = "tessie") -> dict:
    start = int(c["started_at"])
    end = int(c.get("ended_at") or start)
    kwh = float(c.get("energy_added") or 0)
    full_cost, avg_full = estimate_full_cost(start, end, kwh, prices)
    start_dt = ts_local(start)
    return {
        "session_id": start_dt.strftime("%Y%m%d-%H%M"),
        "tessie_id": c.get("id", ""),
        "start_local": fmt_local(start),
        "end_local": fmt_local(end),
        "duration_min": int(round((end - start) / 60)),
        "kwh_added": round(kwh, 2),
        "kwh_used": round(float(c["energy_used"]), 2) if c.get("energy_used") is not None else "",
        "soc_start": c.get("starting_battery", ""),
        "soc_end": c.get("ending_battery", ""),
        "avg_full_price_pln_kwh": avg_full if avg_full is not None else "",
        "full_cost_pln": full_cost if full_cost is not None else "",
        "tessie_cost": c.get("cost", ""),
        "location": c.get("location") or "",
        "saved_location": c.get("saved_location") or "",
        "source": source,
        "cost_basis": "pstryk_price_gross_full_variable",
        "notes": (
            "full_cost = kWh × price_gross (energy+var dist+service+VAT+excise). "
            "Fixed monthly dist is only on monthly tab."
        ),
    }


# ── CSV / Sheet ──────────────────────────────────────────────────────────────

def write_csv(rows: list[dict], path: Path, fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})


def monthly_to_simple(monthly_rows: list[dict]) -> list[dict]:
    """Four columns only; fixed fees already inside EV / homelab totals."""
    simple = []
    for r in monthly_rows:
        simple.append(
            {
                "month": r.get("month", ""),
                "grand_total": r.get("house_total_pln", ""),
                "ev_total": r.get("ev_total_pln", ""),
                "homelab_total": r.get("homelab_total_pln", ""),
            }
        )
    return simple


def push_sheet(
    session_rows: list[dict],
    monthly_rows: list[dict],
    other_loc_rows: list[dict],
    sheet_id: str,
) -> str:
    """
    Sheet tabs:
      monthly_total     — month / grand / EV / homelab
      charging_log      — each home session: start, end, duration, kWh, full price
      monthly_detail    — full breakdown (optional)
      other_locations   — Łódź / Brajniki / Superchargers kWh (all history)
    """
    simple_rows = monthly_to_simple(monthly_rows)
    payload = {
        "session_rows": session_rows,
        "session_fields": SHEET_SESSION_FIELDS,
        "monthly_rows": monthly_rows,
        "monthly_fields": MONTHLY_FIELDS,
        "simple_rows": simple_rows,
        "simple_fields": MONTHLY_TOTAL_FIELDS,
        "other_loc_rows": other_loc_rows,
        "other_loc_fields": OTHER_LOC_FIELDS,
        "sheet_id": sheet_id,
        "creds": str(SA_CREDS),
        "updated": datetime.now(WARSAW).isoformat(timespec="seconds"),
    }
    Path("/tmp/charge_log_sheet_payload.json").write_text(json.dumps(payload))
    code = r"""
import json
from google.oauth2.service_account import Credentials
import gspread

p = json.load(open("/tmp/charge_log_sheet_payload.json"))
scopes = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]
gc = gspread.authorize(Credentials.from_service_account_file(p["creds"], scopes=scopes))
sh = gc.open_by_key(p["sheet_id"])

def upsert(title, fields, rows, min_rows=100):
    try:
        ws = sh.worksheet(title)
        ws.clear()
    except Exception:
        ws = sh.add_worksheet(
            title=title,
            rows=max(min_rows, len(rows) + 20),
            cols=max(12, len(fields) + 2),
        )
    values = [fields] + [[r.get(f, "") for f in fields] for r in rows]
    if values:
        ws.update(range_name="A1", values=values, value_input_option="USER_ENTERED")
    return ws

# Desired tabs
upsert("monthly_total", p["simple_fields"], p["simple_rows"], 50)
upsert("charging_log", p["session_fields"], p["session_rows"], 2000)
upsert("monthly_detail", p["monthly_fields"], p["monthly_rows"], 50)
upsert("other_locations", p["other_loc_fields"], p["other_loc_rows"], 20)

# Remove obsolete tab names if present
for obsolete in ("totals", "monthly"):
    try:
        if obsolete in [w.title for w in sh.worksheets()] and len(sh.worksheets()) > 1:
            sh.del_worksheet(sh.worksheet(obsolete))
    except Exception:
        pass

# Order (best-effort)
try:
    order = []
    for name in ("monthly_total", "charging_log", "monthly_detail", "other_locations"):
        try:
            order.append(sh.worksheet(name))
        except Exception:
            pass
    rest = [w for w in sh.worksheets() if w not in order]
    if order and len(order) + len(rest) == len(sh.worksheets()):
        sh.reorder_worksheets(order + rest)
except Exception as e:
    print("reorder warn", e)

print(sh.url)
"""
    py = str(VENV_PY) if VENV_PY.exists() else sys.executable
    proc = subprocess.run([py, "-c", code], text=True, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr or proc.stdout)
    return proc.stdout.strip()


# ── commands ─────────────────────────────────────────────────────────────────

def cmd_rebuild(args: argparse.Namespace) -> int:
    print(f"Tessie charges {args.vin}…", file=sys.stderr)
    all_c = fetch_all_tessie_charges(args.vin)
    home = [c for c in all_c if is_home_charge(c)]
    print(f"  {len(all_c)} total, {len(home)} home", file=sys.stderr)

    if args.prices_json:
        prices = load_prices_json(args.prices_json)
    else:
        print("HA full prices (price_gross)…", file=sys.stderr)
        prices = export_pstryk_prices()
    print(f"  {len(prices)} hourly prices", file=sys.stderr)

    rows = []
    skipped_pre = 0
    for c in sorted(home, key=lambda x: x["started_at"]):
        if float(c.get("energy_added") or 0) < 0.1:
            continue
        if ts_local(int(c["started_at"])) < MIN_SESSION_DATE:
            skipped_pre += 1
            continue
        rows.append(charge_to_row(c, prices))
    if skipped_pre:
        print(
            f"  skipped {skipped_pre} sessions before {MIN_SESSION_DATE.date()} "
            f"(pre-Pstryk billing)",
            file=sys.stderr,
        )
    write_csv(rows, args.out, FIELDS)

    print("Monthly totals from Pstryk API…", file=sys.stderr)
    monthly = build_monthly_rows(rows)
    write_csv(monthly, args.monthly_out, MONTHLY_FIELDS)

    # All-history location buckets (no March cutoff)
    other_loc = build_other_location_rows(all_c)
    print("Other locations (all history):", file=sys.stderr)
    for r in other_loc:
        print(
            f"  {r['place']}: {r['sessions']} sessions, {r['total_kwh']} kWh "
            f"({r['first_session']} → {r['last_session']})",
            file=sys.stderr,
        )

    kwh = sum(float(r["kwh_added"]) for r in rows)
    cost = sum(float(r["full_cost_pln"]) for r in rows if r["full_cost_pln"] != "")
    print(f"sessions={len(rows)}  EV_kWh={kwh:.1f}  EV_full_variable_PLN={cost:.2f}")
    print(f"csv={args.out}")
    print(f"monthly_csv={args.monthly_out}")
    if rows:
        print(f"range={rows[0]['start_local']} → {rows[-1]['start_local']}")

    if args.sheet:
        url = push_sheet(rows, monthly, other_loc, args.sheet_id)
        print(f"sheet={url}")
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    return cmd_rebuild(args)


def main() -> int:
    load_env()
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    for name, help_ in (("rebuild", "Full rebuild"), ("sync", "Alias for rebuild")):
        p = sub.add_parser(name, help=help_)
        p.add_argument("--vin", default=VIN_DEFAULT)
        p.add_argument("--out", type=Path, default=CSV_PATH)
        p.add_argument("--monthly-out", type=Path, default=MONTHLY_CSV)
        p.add_argument("--prices-json", type=Path)
        p.add_argument("--sheet", action="store_true")
        p.add_argument("--sheet-id", default=SHEET_ID_DEFAULT)
        p.set_defaults(func=cmd_rebuild if name == "rebuild" else cmd_sync)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
