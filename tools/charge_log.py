#!/usr/bin/env python3
"""
Home EV charging log + monthly EV/homelab split → CSV + Google Sheet.

Runs with env vars only (no SSH, no baked-in secrets):

  TESSIE_TOKEN                 required
  PSTRYK_API_KEY               required  (same key as HA Pstryk integration)
  GOOGLE_APPLICATION_CREDENTIALS  path to SA JSON (required for --sheet)
  SHEET_ID                     optional  (default: Home Charging Costs)
  VIN                          optional
  OUTPUT_DIR                   optional  (default: /data or $HOME)

  python charge_log.py rebuild --sheet

Daily on services LXC via Docker + host cron.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from math import atan2, cos, radians, sin, sqrt
from pathlib import Path
from zoneinfo import ZoneInfo

WARSAW = ZoneInfo("Europe/Warsaw")
VIN_DEFAULT = "5YJ3E7EB9MF886781"
SHEET_ID_DEFAULT = "1Lwcs8wxJsSVpDWPbEkMqeBbBRCHI51DQARYMcVXiwQQ"
PSTRYK_BASE = "https://api.pstryk.pl/integrations/"

HOME_LAT = 52.21063419776905
HOME_LON = 21.067841204650904
HOME_RADIUS_M = 200
HOME_SAVED_SUBSTRINGS = ("bluszcz",)
MIN_SESSION_DATE = datetime(2026, 3, 1, tzinfo=WARSAW)

FIELDS = [
    "session_id", "tessie_id", "start_local", "end_local", "duration_min",
    "kwh_added", "kwh_used", "soc_start", "soc_end",
    "avg_full_price_pln_kwh", "full_cost_pln", "tessie_cost",
    "location", "saved_location", "source", "cost_basis", "notes",
]
SHEET_SESSION_FIELDS = [
    "start_local", "end_local", "duration_min", "kwh_added",
    "avg_full_price_pln_kwh", "full_cost_pln", "soc_start", "soc_end",
]
MONTHLY_FIELDS = [
    "month", "house_kwh", "house_total_pln", "ev_kwh", "ev_full_variable_pln",
    "ev_fixed_share_pln", "ev_total_pln", "homelab_kwh",
    "homelab_full_variable_pln", "homelab_fixed_share_pln", "homelab_total_pln",
    "fixed_fees_pln", "energy_net", "service_net", "var_dist_net",
    "fix_dist_net", "excise", "vat", "source",
]
MONTHLY_TOTAL_FIELDS = ["month", "grand_total", "ev_total", "homelab_total"]
OTHER_LOC_FIELDS = ["place", "sessions", "total_kwh", "first_session", "last_session"]


def load_env_file() -> None:
    """Optional local helper: load ~/.env.private or /secrets/.env if present."""
    for path in (
        Path("/secrets/.env"),
        Path.home() / ".env.private",
        Path("/run/secrets/charge-log.env"),
    ):
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export ") :]
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def require_env(name: str) -> str:
    v = os.environ.get(name, "").strip()
    if not v:
        raise SystemExit(f"Missing required env var: {name}")
    return v


def output_dir() -> Path:
    d = Path(os.environ.get("OUTPUT_DIR") or "/data")
    if not d.exists() and not os.environ.get("OUTPUT_DIR"):
        d = Path.home()
    d.mkdir(parents=True, exist_ok=True)
    return d


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
    if any(s in blob for s in HOME_SAVED_SUBSTRINGS):
        return True
    lat, lon = c.get("latitude"), c.get("longitude")
    if lat is not None and lon is not None:
        return haversine_m(float(lat), float(lon), HOME_LAT, HOME_LON) <= HOME_RADIUS_M
    return False


def is_lodz_charge(c: dict) -> bool:
    if c.get("is_supercharger"):
        return False
    blob = _loc_blob(c)
    if any(x in blob for x in ("leżakowa", "lezakowa", "plenerowa", "paradna")):
        return True
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


def ts_local(epoch: int | float) -> datetime:
    return datetime.fromtimestamp(float(epoch), tz=timezone.utc).astimezone(WARSAW)


def fmt_local(epoch: int | float) -> str:
    return ts_local(epoch).strftime("%Y-%m-%d %H:%M")


# ── HTTP helpers ─────────────────────────────────────────────────────────────

def http_json(url: str, headers: dict | None = None, timeout: int = 120) -> dict:
    req = urllib.request.Request(url, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def pstryk_get(path_and_query: str) -> dict:
    key = require_env("PSTRYK_API_KEY")
    url = PSTRYK_BASE + path_and_query.lstrip("/")
    return http_json(url, {"Authorization": key, "Accept": "application/json"})


# ── Tessie ───────────────────────────────────────────────────────────────────

def fetch_all_tessie_charges(vin: str) -> list[dict]:
    token = require_env("TESSIE_TOKEN")
    url = f"https://api.tessie.com/{vin}/charges?limit=5000"
    data = http_json(url, {"Authorization": f"Bearer {token}", "Accept": "application/json"})
    return data.get("results") or []


# ── Pstryk prices (full_price / price_gross) ─────────────────────────────────

def fetch_pstryk_hourly_prices(start: datetime, end: datetime) -> dict[int, float]:
    """
    Fetch hourly price_gross for [start, end) in chunks (API-friendly windows).
    Keys: UTC hour-start epoch.
    """
    out: dict[int, float] = {}
    # API windows in UTC ISO Z
    cursor = start.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    end_u = end.astimezone(timezone.utc)
    chunk = timedelta(days=7)
    while cursor < end_u:
        chunk_end = min(cursor + chunk, end_u)
        qs = urllib.parse.urlencode(
            {
                "metrics": "pricing",
                "resolution": "hour",
                "window_start": cursor.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "window_end": chunk_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        )
        try:
            data = pstryk_get(f"meter-data/unified-metrics/?{qs}")
        except Exception as e:
            print(f"  price window {cursor.date()} failed: {e}", file=sys.stderr)
            cursor = chunk_end
            time.sleep(0.3)
            continue
        for fr in data.get("frames") or []:
            try:
                s = fr["start"].replace("Z", "+00:00")
                ts = int(datetime.fromisoformat(s).timestamp())
                hour = ts - (ts % 3600)
                pricing = (fr.get("metrics") or {}).get("pricing") or {}
                price = pricing.get("price_gross")
                if price is None:
                    price = pricing.get("full_price")
                if price is not None:
                    out[hour] = float(price)
            except Exception:
                continue
        cursor = chunk_end
        time.sleep(0.15)
    return out


def fetch_pstryk_monthly(year: int, month: int) -> dict | None:
    if month == 12:
        end = f"{year + 1:04d}-01-01T00:00:00Z"
    else:
        end = f"{year:04d}-{month + 1:02d}-01T00:00:00Z"
    start = f"{year:04d}-{month:02d}-01T00:00:00Z"
    qs = urllib.parse.urlencode(
        {
            "metrics": "meter_values,cost",
            "resolution": "month",
            "window_start": start,
            "window_end": end,
            "for_tz": "Europe/Warsaw",
        }
    )
    try:
        data = pstryk_get(f"meter-data/unified-metrics/?{qs}")
    except Exception as e:
        print(f"  monthly {year}-{month:02d} failed: {e}", file=sys.stderr)
        return None
    frames = data.get("frames") or []
    target = f"{year:04d}-{month:02d}"
    chosen = None
    for f in frames:
        try:
            s = datetime.fromisoformat(f["start"].replace("Z", "+00:00"))
            e = datetime.fromisoformat(f["end"].replace("Z", "+00:00"))
            mid = s + (e - s) / 2
            local_ym = (mid + timedelta(hours=2)).strftime("%Y-%m")
            if local_ym == target:
                chosen = f
                break
        except Exception:
            continue
    if chosen is None and frames:
        chosen = max(
            frames,
            key=lambda f: (f.get("metrics") or {})
            .get("meter_values", {})
            .get("energy_active_import_register")
            or 0,
        )
    if not chosen:
        return None
    m = (chosen.get("metrics") or {}).get("meter_values") or {}
    c = (chosen.get("metrics") or {}).get("cost") or {}
    kwh = float(m.get("energy_active_import_register") or 0)
    if kwh <= 0 and float(c.get("energy_import_cost") or 0) <= 0:
        return None
    fix_net = float(c.get("fix_dist_cost_net") or 0)
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


def estimate_full_cost(
    start: int, end: int, kwh: float, prices: dict[int, float]
) -> tuple[float | None, float | None]:
    if kwh is None or kwh <= 0 or end <= start:
        return None, None
    hours: list[int] = []
    t = start - (start % 3600)
    while t < end:
        hours.append(t)
        t += 3600
    if not hours:
        hours = [start - (start % 3600)]
    weights = [max(0.0, min(end, h + 3600) - max(start, h)) for h in hours]
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
        "notes": "full_cost = kWh × price_gross (energy+var dist+service+VAT+excise).",
    }


def build_monthly_rows(session_rows: list[dict]) -> list[dict]:
    by_month: dict[str, list[dict]] = defaultdict(list)
    for r in session_rows:
        by_month[r["start_local"][:7]].append(r)
    months = sorted(by_month.keys())
    now = datetime.now(WARSAW)
    for y, m in [
        (now.year, now.month),
        (now.year if now.month > 1 else now.year - 1, now.month - 1 or 12),
    ]:
        months.append(f"{y:04d}-{m:02d}")
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
            share = min((ev_kwh / house_kwh) if house_kwh > 0 else 0.0, 1.0)
            ev_fixed = round(fixed * share, 2)
            home_kwh = round(max(0.0, house_kwh - ev_kwh), 2)
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
            f"homelab={row['homelab_total_pln']}",
            file=sys.stderr,
        )
    return out


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
    def ok(c):
        return float(c.get("energy_added") or 0) >= 0.1

    return [
        summarize_location_group([c for c in all_charges if is_home_charge(c) and ok(c)], "Bluszczańska"),
        summarize_location_group([c for c in all_charges if is_lodz_charge(c) and ok(c)], "Łódź"),
        summarize_location_group([c for c in all_charges if is_brajniki_charge(c) and ok(c)], "Brajniki"),
        summarize_location_group([c for c in all_charges if is_supercharger_charge(c) and ok(c)], "Superchargers"),
    ]


def monthly_to_simple(monthly_rows: list[dict]) -> list[dict]:
    return [
        {
            "month": r.get("month", ""),
            "grand_total": r.get("house_total_pln", ""),
            "ev_total": r.get("ev_total_pln", ""),
            "homelab_total": r.get("homelab_total_pln", ""),
        }
        for r in monthly_rows
    ]


def write_csv(rows: list[dict], path: Path, fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fields})


def push_sheet(
    session_rows: list[dict],
    monthly_rows: list[dict],
    other_loc_rows: list[dict],
    sheet_id: str,
) -> str:
    from google.oauth2.service_account import Credentials
    import gspread

    creds_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
    if not creds_path or not Path(creds_path).exists():
        raise SystemExit(
            "GOOGLE_APPLICATION_CREDENTIALS must point to a service-account JSON file"
        )
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    gc = gspread.authorize(
        Credentials.from_service_account_file(creds_path, scopes=scopes)
    )
    sh = gc.open_by_key(sheet_id)
    simple_rows = monthly_to_simple(monthly_rows)

    def upsert(title: str, fields: list[str], rows: list[dict], min_rows: int = 100):
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

    upsert("monthly_total", MONTHLY_TOTAL_FIELDS, simple_rows, 50)
    upsert("charging_log", SHEET_SESSION_FIELDS, session_rows, 2000)
    upsert("monthly_detail", MONTHLY_FIELDS, monthly_rows, 50)
    upsert("other_locations", OTHER_LOC_FIELDS, other_loc_rows, 20)

    for obsolete in ("totals", "monthly"):
        try:
            if obsolete in [w.title for w in sh.worksheets()] and len(sh.worksheets()) > 1:
                sh.del_worksheet(sh.worksheet(obsolete))
        except Exception:
            pass

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
        print(f"reorder warn: {e}", file=sys.stderr)

    return sh.url


def cmd_rebuild(args: argparse.Namespace) -> int:
    vin = os.environ.get("VIN") or args.vin
    sheet_id = os.environ.get("SHEET_ID") or args.sheet_id
    out_dir = output_dir()
    out_csv = Path(args.out) if args.out else out_dir / "myszolot-charging-log.csv"
    monthly_csv = (
        Path(args.monthly_out) if args.monthly_out else out_dir / "myszolot-monthly-costs.csv"
    )

    print(f"Tessie charges {vin}…", file=sys.stderr)
    all_c = fetch_all_tessie_charges(vin)
    home = [c for c in all_c if is_home_charge(c)]
    print(f"  {len(all_c)} total, {len(home)} home", file=sys.stderr)

    # Build candidate session list first to know price window
    candidates = []
    skipped_pre = 0
    for c in sorted(home, key=lambda x: x["started_at"]):
        if float(c.get("energy_added") or 0) < 0.1:
            continue
        if ts_local(int(c["started_at"])) < MIN_SESSION_DATE:
            skipped_pre += 1
            continue
        candidates.append(c)
    if skipped_pre:
        print(
            f"  skipped {skipped_pre} sessions before {MIN_SESSION_DATE.date()}",
            file=sys.stderr,
        )

    if candidates:
        price_start = ts_local(int(candidates[0]["started_at"])) - timedelta(hours=2)
        price_end = ts_local(int(candidates[-1].get("ended_at") or candidates[-1]["started_at"])) + timedelta(hours=2)
    else:
        price_start = MIN_SESSION_DATE
        price_end = datetime.now(WARSAW)

    print(
        f"Pstryk hourly full prices {price_start.date()} → {price_end.date()}…",
        file=sys.stderr,
    )
    prices = fetch_pstryk_hourly_prices(price_start, price_end)
    print(f"  {len(prices)} hourly prices", file=sys.stderr)

    rows = [charge_to_row(c, prices) for c in candidates]
    write_csv(rows, out_csv, FIELDS)

    print("Monthly totals from Pstryk API…", file=sys.stderr)
    monthly = build_monthly_rows(rows)
    write_csv(monthly, monthly_csv, MONTHLY_FIELDS)

    other_loc = build_other_location_rows(all_c)
    print("Other locations (all history):", file=sys.stderr)
    for r in other_loc:
        print(
            f"  {r['place']}: {r['sessions']} sessions, {r['total_kwh']} kWh",
            file=sys.stderr,
        )

    kwh = sum(float(r["kwh_added"]) for r in rows)
    cost = sum(float(r["full_cost_pln"]) for r in rows if r["full_cost_pln"] != "")
    print(f"sessions={len(rows)}  EV_kWh={kwh:.1f}  EV_full_variable_PLN={cost:.2f}")
    print(f"csv={out_csv}")
    print(f"monthly_csv={monthly_csv}")
    if rows:
        print(f"range={rows[0]['start_local']} → {rows[-1]['start_local']}")

    do_sheet = args.sheet or os.environ.get("PUSH_SHEET", "").lower() in ("1", "true", "yes")
    if do_sheet:
        url = push_sheet(rows, monthly, other_loc, sheet_id)
        print(f"sheet={url}")
    return 0


def main() -> int:
    load_env_file()
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("rebuild", "sync"):
        p = sub.add_parser(name)
        p.add_argument("--vin", default=VIN_DEFAULT)
        p.add_argument("--out", default=None)
        p.add_argument("--monthly-out", default=None)
        p.add_argument("--sheet", action="store_true")
        p.add_argument("--sheet-id", default=SHEET_ID_DEFAULT)
        p.set_defaults(func=cmd_rebuild)
    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
