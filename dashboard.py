#!/usr/bin/env python3
"""UTKGrantDashboard — a local-only budget dashboard for PIs.

Reads university report exports (CSV files) from the data/ folder, analyzes
them, and serves an interactive dashboard at http://127.0.0.1:<port>.

Security model (short version, verifiable by reading this file):
  * The server binds strictly to 127.0.0.1 — it is not reachable from the
    network, let alone the internet.
  * The tool makes zero outbound network requests. No CDNs, no fonts, no
    analytics. It works identically with wifi off.
  * Your data stays in the data/ folder on your machine, which is
    .gitignore'd so it can never be committed by accident.

No dependencies beyond the Python 3 standard library (Python 3.9+).

Usage:
    python3 dashboard.py                 # serve on http://127.0.0.1:8787
    python3 dashboard.py --port 9000
    python3 dashboard.py --data /path/to/exports
    python3 dashboard.py --no-browser
"""

import argparse
import csv
import json
import re
import webbrowser
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DEFAULT_DATA_DIR = BASE_DIR / "data"

MAX_CONFIG_BYTES = 2_000_000  # sanity cap on saved-config uploads


# ---------------------------------------------------------------------------
# small parsing helpers
# ---------------------------------------------------------------------------

def fnum(s):
    """Parse a number that may be empty, or contain $ , signs."""
    if s is None:
        return None
    s = str(s).strip().replace(",", "").replace("$", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def fdate(s):
    """Parse MM/DD/YYYY or ISO-ish dates to 'YYYY-MM-DD' (or None)."""
    s = (s or "").strip()
    if not s:
        return None
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[:10], fmt).date().isoformat()
        except ValueError:
            continue
    return None


def norm_category(s):
    """'01. Salaries & Wages' -> 'Salaries & Wages'."""
    return re.sub(r"^\s*\d+\.\s*", "", (s or "").strip())


def month_of(iso_date):
    return iso_date[:7] if iso_date else None


def months_between(a, b):
    """Whole-ish months from ISO date a to ISO date b (float, >= 0)."""
    da = date.fromisoformat(a)
    db = date.fromisoformat(b)
    return max(0.0, (db - da).days / 30.44)


# ---------------------------------------------------------------------------
# CSV classification and parsing
# ---------------------------------------------------------------------------

def classify_csv(path):
    """Return 'pi_dashboard', 'detail', or None based on the header row."""
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            header = f.readline()
    except OSError:
        return None
    if "P_PROJECT" in header and "L_EXP_COST" in header:
        return "detail"
    if "Project Number" in header and "Expenditure Category" in header:
        return "pi_dashboard"
    return None


def parse_pi_dashboard(path):
    """One row per (project, expenditure category) with budget/actuals."""
    rows = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            proj = (r.get("Project Number") or "").strip()
            if not proj:
                continue
            budget = fnum(r.get("Budget")) or 0.0
            direct = fnum(r.get("SUM Direct Cost")) or 0.0
            indirect = fnum(r.get("SUM Indirect Cost")) or 0.0
            spent = direct + indirect
            remaining = fnum(r.get("Remaining Balance"))
            if remaining is None:
                remaining = budget - spent
            rows.append({
                "project": proj,
                "name": (r.get("Project Name") or "").strip(),
                "status": (r.get("Project Status") or "").strip(),
                "start": fdate(r.get("Project Start Date")),
                "end": fdate(r.get("Project Finish Date")),
                "category": norm_category(r.get("Expenditure Category")),
                "budget": budget,
                "spent": spent,
                "committed": fnum(r.get("Committed Cost")) or 0.0,
                "remaining": remaining,
            })
    return rows


def parse_detail(path, labor, nonlabor, meta):
    """Parse an RPT_GMS_007 'Sponsored Project Detail Report' export.

    The reporting tool emits the cartesian product of labor x non-labor
    transactions (every labor line paired with every non-labor line), so we
    de-duplicate each side on its own key. Results accumulate into the
    passed-in dicts so several export files merge cleanly.
    """
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            proj = (r.get("PROJ_NUMBER") or "").strip()
            if not proj:
                continue

            m = meta.setdefault(proj, {
                "name": None, "start": None, "end": None,
                "faRate": None, "windows": set(),
            })
            m["name"] = m["name"] or (r.get("PROJ_NAME") or "").strip()
            m["start"] = m["start"] or fdate(r.get("PROJ_START"))
            m["end"] = m["end"] or fdate(r.get("PROJ_END"))
            fa = fnum(r.get("SPONSOR_FA_RATE"))
            if fa is None:
                fa = fnum(r.get("AUDIT_FA_RATE"))
            if fa is not None:
                m["faRate"] = fa
            w_from = fdate(r.get("PFROMDATE"))
            w_to = fdate(r.get("PTODATE"))
            if w_from and w_to:
                m["windows"].add((w_from, w_to))

            # labor side of the row
            l_trx = (r.get("L_TRX_NUM") or "").strip()
            l_amt = fnum(r.get("L_EXP_COST"))
            if l_trx or l_amt is not None:
                key = (proj, l_trx, (r.get("L_LAB_TRX") or "").strip(),
                       (r.get("L_PER_NUM") or "").strip(),
                       (r.get("L_EXP_DATE") or "").strip(),
                       (r.get("L_EXP_TYPE") or "").strip(),
                       r.get("L_EXP_COST"))
                if key not in labor:
                    labor[key] = {
                        "project": proj,
                        "kind": "labor",
                        "category": (r.get("L_EXP_CAT") or "").strip(),
                        "type": (r.get("L_EXP_TYPE") or "").strip(),
                        "date": fdate(r.get("L_EXP_DATE")),
                        "person": (r.get("L_PER_NAME") or "").strip(),
                        "amount": l_amt or 0.0,
                    }

            # non-labor side of the row
            nl_trx = (r.get("NL_TRX_NUM") or "").strip()
            nl_amt = fnum(r.get("NL_EXP_COST"))
            if nl_trx or nl_amt is not None:
                key = (proj, nl_trx,
                       (r.get("NL_EXP_DATE") or "").strip(),
                       (r.get("NL_EXP_TYPE") or "").strip(),
                       (r.get("NL_PER_NAME") or "").strip(),
                       r.get("NL_EXP_COST"))
                if key not in nonlabor:
                    nonlabor[key] = {
                        "project": proj,
                        "kind": "nonlabor",
                        "category": (r.get("NL_EXP_CAT") or "").strip(),
                        "type": (r.get("NL_EXP_TYPE") or "").strip(),
                        "date": fdate(r.get("NL_EXP_DATE")),
                        "person": (r.get("NL_PER_NAME") or "").strip(),
                        "amount": nl_amt or 0.0,
                    }


# ---------------------------------------------------------------------------
# analysis
# ---------------------------------------------------------------------------

def estimate_people(transactions, window_months):
    """Build per-person salary/fringe/fee estimates from payroll lines."""
    salaries = {}   # person -> {month: amount}
    fringe = {}     # person -> total
    fees = {}       # person -> total
    projects = {}   # person -> set of projects

    for t in transactions:
        person = t["person"]
        if not person:
            continue
        projects.setdefault(person, set()).add(t["project"])
        if t["kind"] == "labor":
            if "fringe" in t["type"].lower():
                fringe[person] = fringe.get(person, 0.0) + t["amount"]
            else:
                m = month_of(t["date"])
                if m:
                    bym = salaries.setdefault(person, {})
                    bym[m] = bym.get(m, 0.0) + t["amount"]
        elif "fee" in t["type"].lower() or "tuition" in t["type"].lower():
            fees[person] = fees.get(person, 0.0) + t["amount"]

    people = []
    for person in sorted(set(list(salaries) + list(fringe) + list(fees))):
        bym = salaries.get(person, {})
        nonzero = [(m, v) for m, v in sorted(bym.items()) if abs(v) > 1]
        recent = nonzero[-3:]
        monthly = sum(v for _, v in recent) / len(recent) if recent else 0.0
        salary_total = sum(v for _, v in nonzero)
        fr_total = fringe.get(person, 0.0)
        fr_rate = (fr_total / salary_total) if salary_total > 1 else None
        fee_total = fees.get(person, 0.0)
        annual_fees = (fee_total * 12.0 / window_months) if (fee_total and window_months) else 0.0
        people.append({
            "name": person,
            "monthlySalary": round(monthly, 2),
            "fringeRate": round(fr_rate, 4) if fr_rate is not None else None,
            "annualFees": round(annual_fees, 2),
            "lastPaid": nonzero[-1][0] if nonzero else None,
            "projects": sorted(projects.get(person, [])),
            "salaryHistory": {m: round(v, 2) for m, v in nonzero},
        })
    return people


def compute_flags(projects, today_iso):
    """Rule-based issues list, most severe first."""
    flags = []
    today = date.fromisoformat(today_iso)

    for p in projects:
        active = p["status"].lower() == "active"
        label = p["shortName"]
        tot = p["totals"]

        # 1. category overruns / charges against zero budget
        for c in p["categories"]:
            if c["remaining"] < -0.5:
                over = -c["remaining"]
                if c["budget"] <= 0:
                    flags.append({
                        "severity": "serious", "project": p["id"],
                        "title": f"{label}: {c['category']} charged with no budget",
                        "detail": f"${c['spent']:,.0f} spent against a $0 budget line.",
                    })
                else:
                    pct = over / c["budget"] * 100
                    sev = "critical" if (over > 5000 or pct > 25) else "serious"
                    flags.append({
                        "severity": sev, "project": p["id"],
                        "title": f"{label}: {c['category']} overspent by ${over:,.0f}",
                        "detail": (f"${c['spent']:,.0f} spent of a ${c['budget']:,.0f} budget "
                                   f"({c['spent']/c['budget']*100:.0f}%)."),
                    })

        if not active or not p["start"] or not p["end"]:
            continue

        # 2. past end date but still active
        end = date.fromisoformat(p["end"])
        days_left = (end - today).days
        if days_left < 0:
            flags.append({
                "severity": "warning", "project": p["id"],
                "title": f"{label}: past its end date but still active",
                "detail": f"Ended {p['end']} with ${tot['remaining']:,.0f} remaining.",
            })
            continue

        # 3. ending soon with money left
        if days_left <= 180 and tot["remaining"] > 1000:
            biggest = sorted((c for c in p["categories"] if c["remaining"] > 0),
                             key=lambda c: -c["remaining"])[:3]
            cats = ", ".join(f"{c['category']} ${c['remaining']:,.0f}" for c in biggest)
            sev = "serious" if days_left <= 90 else "warning"
            flags.append({
                "severity": sev, "project": p["id"],
                "title": f"{label}: ends in {days_left} days with ${tot['remaining']:,.0f} unspent",
                "detail": f"Largest unspent: {cats}.",
            })

        # 4. pace vs. time elapsed
        span = (end - date.fromisoformat(p["start"])).days
        if span > 0 and tot["budget"] > 0:
            t_frac = min(1.0, max(0.0, (today - date.fromisoformat(p["start"])).days / span))
            s_frac = tot["spent"] / tot["budget"]
            if t_frac > 0.2:
                projected_total = tot["spent"] / t_frac
                overrun = projected_total - tot["budget"]
                if s_frac / t_frac > 1.08 and overrun > 2000:
                    flags.append({
                        "severity": "serious", "project": p["id"],
                        "title": f"{label}: on pace to overrun by ~${overrun:,.0f}",
                        "detail": (f"{s_frac*100:.0f}% of budget spent with {t_frac*100:.0f}% "
                                   f"of the award period elapsed."),
                    })
                elif t_frac - s_frac > 0.30 and tot["remaining"] > 5000 and days_left > 180:
                    flags.append({
                        "severity": "info", "project": p["id"],
                        "title": f"{label}: spending well behind schedule",
                        "detail": (f"{s_frac*100:.0f}% spent vs {t_frac*100:.0f}% of period "
                                   f"elapsed — ${tot['remaining']:,.0f} still available."),
                    })

    order = {"critical": 0, "serious": 1, "warning": 2, "info": 3}
    flags.sort(key=lambda f: order.get(f["severity"], 9))
    return flags


def short_name(full_name, pi_names):
    """Trim the PI's own surname off the project name for display."""
    name = full_name or ""
    for pi in pi_names:
        surname = pi.split(",")[0].strip()
        if surname and name.endswith(" " + surname):
            name = name[: -len(surname) - 1]
    return name.strip() or full_name


# Large detail exports (the cartesian product can reach hundreds of MB) take
# a few seconds to parse, so cache the parsed payload keyed on the CSV files'
# (path, mtime, size) — a reload only re-parses when a file actually changes.
_payload_cache = {"key": None, "payload": None}


def build_payload(data_dir):
    key = tuple(sorted(
        (str(p), p.stat().st_mtime, p.stat().st_size)
        for p in Path(data_dir).glob("*.csv")
    )) + (date.today().isoformat(),)
    if _payload_cache["key"] == key:
        payload = dict(_payload_cache["payload"])
        payload["config"] = load_config(data_dir)  # config always fresh
        return payload
    payload = _build_payload_uncached(data_dir)
    _payload_cache["key"] = key
    _payload_cache["payload"] = payload
    return dict(payload, config=load_config(data_dir))


def _build_payload_uncached(data_dir):
    today_iso = date.today().isoformat()
    this_month = today_iso[:7]

    files_info = []
    dash_files = []           # (mtime, rows)
    labor, nonlabor, meta = {}, {}, {}

    for path in sorted(Path(data_dir).glob("*.csv")):
        kind = classify_csv(path)
        files_info.append({
            "name": path.name,
            "type": kind or "unrecognized",
            "modified": datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
        })
        if kind == "pi_dashboard":
            dash_files.append((path.stat().st_mtime, parse_pi_dashboard(path)))
        elif kind == "detail":
            parse_detail(path, labor, nonlabor, meta)

    # newest PI-dashboard file wins per project
    dash_by_project = {}
    for _, rows in sorted(dash_files, key=lambda x: x[0]):
        by_proj = {}
        for r in rows:
            by_proj.setdefault(r["project"], []).append(r)
        dash_by_project.update(by_proj)

    transactions = list(labor.values()) + list(nonlabor.values())

    # transaction aggregates per project
    monthly = {}    # project -> {month: net}
    for t in transactions:
        m = month_of(t["date"])
        if m:
            bym = monthly.setdefault(t["project"], {})
            bym[m] = bym.get(m, 0.0) + t["amount"]

    # project names conventionally end with the PI surname; collect surnames
    # so display names can drop them ("DOE DE-SC0020267 Holmes" -> "DOE DE-SC0020267")
    pi_surnames = set()
    for rows in dash_by_project.values():
        if rows:
            last = (rows[0]["name"] or "").split(" ")[-1]
            if last.isalpha():
                pi_surnames.add(last)

    projects = []
    all_ids = sorted(set(dash_by_project) | set(meta))
    for pid in all_ids:
        rows = dash_by_project.get(pid, [])
        dmeta = meta.get(pid, {})
        name = (rows[0]["name"] if rows else dmeta.get("name")) or pid
        cats = [{
            "category": r["category"],
            "budget": round(r["budget"], 2),
            "spent": round(r["spent"], 2),
            "committed": round(r["committed"], 2),
            "remaining": round(r["remaining"], 2),
        } for r in rows]
        totals = {
            "budget": round(sum(c["budget"] for c in cats), 2),
            "spent": round(sum(c["spent"] for c in cats), 2),
            "committed": round(sum(c["committed"] for c in cats), 2),
            "remaining": round(sum(c["remaining"] for c in cats), 2),
        }

        start = (rows[0]["start"] if rows else dmeta.get("start"))
        end = (rows[0]["end"] if rows else dmeta.get("end"))
        status = rows[0]["status"] if rows else "Active"

        # F&A rate: prefer the detail report's column; otherwise infer the
        # effective rate from the budget (indirect budget / direct budget)
        fa_rate = dmeta.get("faRate")
        fa_source = "report" if fa_rate is not None else None
        if fa_rate is None and cats:
            indirect_budget = sum(c["budget"] for c in cats if "indirect" in c["category"].lower())
            direct_budget = sum(c["budget"] for c in cats if "indirect" not in c["category"].lower())
            if direct_budget > 0 and indirect_budget > 0:
                fa_rate = round(indirect_budget / direct_budget, 4)
                fa_source = "inferred"

        # burn rate: average of the last 3 complete months with real activity
        bym = monthly.get(pid, {})
        complete = [(m, v) for m, v in sorted(bym.items())
                    if m < this_month and abs(v) > 1]
        recent3 = complete[-3:]
        recent_burn = (sum(v for _, v in recent3) / len(recent3)) if recent3 else None
        linear_burn = None
        if start and totals["spent"] > 0:
            elapsed = months_between(start, today_iso)
            if elapsed >= 1:
                linear_burn = totals["spent"] / elapsed

        projects.append({
            "id": pid,
            "name": name,
            "shortName": short_name(name, pi_surnames),
            "status": status,
            "start": start,
            "end": end,
            "faRate": fa_rate,
            "faSource": fa_source,
            "categories": cats,
            "totals": totals,
            "monthly": {m: round(v, 2) for m, v in sorted(bym.items())},
            "burn": {
                "recent": round(recent_burn, 2) if recent_burn is not None else None,
                "recentMonths": [m for m, _ in recent3],
                "linear": round(linear_burn, 2) if linear_burn is not None else None,
            },
            "hasDetail": pid in meta,
            "inDashboard": pid in dash_by_project,
        })

    # people estimates need the export coverage window length
    window_months = 0.0
    windows = [w for m in meta.values() for w in m["windows"]]
    if windows:
        w_from = min(w[0] for w in windows)
        w_to = max(w[1] for w in windows)
        window_months = max(1.0, months_between(w_from, w_to))
    people = estimate_people(transactions, window_months)

    flags = compute_flags([p for p in projects if p["inDashboard"]], today_iso)

    return {
        "today": today_iso,
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "files": files_info,
        "projects": projects,
        "people": people,
        "flags": flags,
    }


# ---------------------------------------------------------------------------
# saved configuration (people edits, scenarios) — lives next to the data
# ---------------------------------------------------------------------------

def config_path(data_dir):
    return Path(data_dir) / "config.json"


def load_config(data_dir):
    try:
        with open(config_path(data_dir), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def save_config(data_dir, payload):
    tmp = config_path(data_dir).with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    tmp.replace(config_path(data_dir))


# ---------------------------------------------------------------------------
# HTTP server (localhost only)
# ---------------------------------------------------------------------------

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
}


def make_handler(data_dir):
    class Handler(BaseHTTPRequestHandler):
        server_version = "UTKGrantDashboard/1.0"

        def _send(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _send_file(self, path):
            real = path.resolve()
            if not str(real).startswith(str(STATIC_DIR.resolve())) or not real.is_file():
                self._send(404, json.dumps({"error": "not found"}))
                return
            ctype = CONTENT_TYPES.get(real.suffix, "application/octet-stream")
            self._send(200, real.read_bytes(), ctype)

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/":
                self._send_file(STATIC_DIR / "index.html")
            elif path.startswith("/static/"):
                self._send_file(STATIC_DIR / path[len("/static/"):])
            elif path == "/api/data":
                try:
                    payload = build_payload(data_dir)
                    self._send(200, json.dumps(payload))
                except Exception as exc:  # surface parse errors in the UI
                    self._send(500, json.dumps({"error": str(exc)}))
            else:
                self._send(404, json.dumps({"error": "not found"}))

        def do_POST(self):
            if self.path != "/api/config":
                self._send(404, json.dumps({"error": "not found"}))
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length > MAX_CONFIG_BYTES:
                    raise ValueError("config too large")
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                save_config(data_dir, body)
                self._send(200, json.dumps({"ok": True}))
            except Exception as exc:
                self._send(400, json.dumps({"error": str(exc)}))

        def log_message(self, fmt, *args):
            pass  # keep the terminal quiet

    return Handler


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--data", type=Path, default=DEFAULT_DATA_DIR,
                    help="folder containing the CSV exports (default: ./data)")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    data_dir = args.data
    data_dir.mkdir(parents=True, exist_ok=True)

    handler = make_handler(data_dir)
    httpd = None
    port = args.port
    for candidate in range(args.port, args.port + 10):
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", candidate), handler)
            port = candidate
            break
        except OSError:
            continue
    if httpd is None:
        raise SystemExit(f"Could not bind a port in {args.port}-{args.port + 9}.")

    url = f"http://127.0.0.1:{port}"
    print(f"UTKGrantDashboard serving {data_dir} at {url}  (Ctrl-C to stop)")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
