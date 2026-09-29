#!/usr/bin/env python3
"""UTKGrantDashboard — a local-only budget dashboard for PIs.

Reads university report exports (CSV files) from the data/ folder, analyzes
them, and serves an interactive dashboard at http://127.0.0.1:<port>.

Security model (short version, verifiable by reading this file):
  * The server binds strictly to 127.0.0.1 — it is not reachable from the
    network, let alone the internet.
  * This server makes zero outbound network requests. No CDNs, no fonts, no
    analytics. The "Get fresh data" section composes report URLs as text
    (see spn_reports.py); clicking one is your own browser downloading from
    the university system, exactly as if you had typed the URL.
  * Your data stays in the data/ folder on your machine, which is
    .gitignore'd so it can never be committed by accident. The only other
    folder touched is your Downloads folder, listed read-only so freshly
    downloaded exports can be copied into data/ on a click (--no-inbox
    turns that off).

No dependencies beyond the Python 3 standard library (Python 3.9+).

Usage (or double-click "Start Dashboard.command" on a Mac, "Start
Dashboard.bat" on Windows):
    python3 dashboard.py                 # serve on http://127.0.0.1:8787
    python3 dashboard.py --port 9000
    python3 dashboard.py --data /path/to/exports
                                         # (with a subfolder per PI, if you
                                         #  look after several — see README)
    python3 dashboard.py --downloads /path/to/Downloads
    python3 dashboard.py --no-inbox      # don't look at the Downloads folder
    python3 dashboard.py --no-browser
"""

import argparse
import csv
import io
import json
import re
import shutil
import sys
import threading
import time
import webbrowser
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import spn_reports

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
DEFAULT_DATA_DIR = BASE_DIR / "data"


def default_inbox_dir():
    """Where the browser drops downloads: ~/Downloads, except on Windows,
    where the folder can be relocated (OneDrive, a second drive) and the
    registry knows where it went."""
    if sys.platform == "win32":
        try:
            import ntpath
            import winreg
            key = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
                raw, _ = winreg.QueryValueEx(k, "{374DE290-123F-4565-9164-39C4925E467B}")
            found = Path(ntpath.expandvars(raw))  # %USERPROFILE%\... style
            if found.is_dir():
                return found
        except OSError:
            pass
    return Path.home() / "Downloads"


DEFAULT_INBOX_DIR = default_inbox_dir()

MAX_CONFIG_BYTES = 2_000_000  # sanity cap on saved-config uploads
MAX_INBOX_FILES = 25          # newest N candidate exports shown per scan
SETTLE_SECONDS = 3            # how long a download must sit still to count as done
SNIFF_ROWS = 400              # rows of a detail export read to tell whose it is

# A PI folder is a plain subfolder of the data folder, named by the user
# ("Holmes", "Doe, Jane"). Nothing exotic: letters, digits, spaces and a few
# punctuation marks — never a path.
PROFILE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ._,'&()-]{0,79}$")


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


def month_add(m, n):
    """'2026-07' + n months."""
    y, mo = int(m[:4]), int(m[5:7]) - 1 + n
    return f"{y + mo // 12}-{mo % 12 + 1:02d}"


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
    # Expenditure detail exports keep the RPT filename prefix even when the
    # report number/name changes; accept any RPT* csv that looks the part.
    if path.name.upper().startswith("RPT") and any(
            marker in header for marker in ("PROJ_NUMBER", "L_EXP", "NL_EXP")):
        return "detail"
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


def _cols_with(headers, prefix, words):
    """Header columns on one side (L_/NL_) whose name contains any of words."""
    return [h for h in headers or []
            if (h or "").upper().startswith(prefix)
            and any(w in h.upper() for w in words)]


def _desc_columns(headers, prefix):
    """Columns on one side (L_/NL_) that can hold a line description — an
    expense report title, a PO text, a payroll comment. Report layouts get
    renamed, so match on shape instead of hard-coding one column name (the
    live RPT07 layout calls it NL_EXP_CMNT); when several exist, a TITLE
    outranks a DESC outranks a COMMENT/CMNT."""
    rank = {"TITLE": 0, "DESC": 1, "COMMENT": 2, "CMNT": 2}
    hits = _cols_with(headers, prefix, rank)
    hits.sort(key=lambda h: min(v for w, v in rank.items() if w in h.upper()))
    return hits


def _first_desc(row, cols):
    for c in cols:
        v = (row.get(c) or "").strip()
        if v:
            return v
    return ""


def parse_detail(path, labor, nonlabor, meta):
    """Parse an RPT_GMS_007 'Sponsored Project Detail Report' export.

    The reporting tool emits the cartesian product of labor x non-labor
    transactions (every labor line paired with every non-labor line), so we
    de-duplicate each side on its own key. Results accumulate into the
    passed-in dicts so several export files merge cleanly.
    """
    with _open_counting(path) as f:
        reader = csv.DictReader(f)
        l_desc_cols = _desc_columns(reader.fieldnames, "L_")
        nl_desc_cols = _desc_columns(reader.fieldnames, "NL_")
        nl_vend_cols = _cols_with(reader.fieldnames, "NL_", ("VEND",))
        for r in reader:
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
                l_desc = _first_desc(r, l_desc_cols)
                key = (proj, l_trx, (r.get("L_LAB_TRX") or "").strip(),
                       (r.get("L_PER_NUM") or "").strip(),
                       (r.get("L_EXP_DATE") or "").strip(),
                       (r.get("L_EXP_TYPE") or "").strip(),
                       r.get("L_EXP_COST"), l_desc)
                if key not in labor:
                    labor[key] = {
                        "project": proj,
                        "kind": "labor",
                        "trx": l_trx,
                        "category": (r.get("L_EXP_CAT") or "").strip(),
                        "type": (r.get("L_EXP_TYPE") or "").strip(),
                        "date": fdate(r.get("L_EXP_DATE")),
                        "person": (r.get("L_PER_NAME") or "").strip(),
                        "desc": l_desc,
                        "vendor": "",
                        "amount": l_amt or 0.0,
                    }

            # non-labor side of the row
            nl_trx = (r.get("NL_TRX_NUM") or "").strip()
            nl_amt = fnum(r.get("NL_EXP_COST"))
            if nl_trx or nl_amt is not None:
                nl_desc = _first_desc(r, nl_desc_cols)
                nl_vend = _first_desc(r, nl_vend_cols)
                key = (proj, nl_trx,
                       (r.get("NL_EXP_DATE") or "").strip(),
                       (r.get("NL_EXP_TYPE") or "").strip(),
                       (r.get("NL_PER_NAME") or "").strip(),
                       r.get("NL_EXP_COST"), nl_desc, nl_vend)
                if key not in nonlabor:
                    nonlabor[key] = {
                        "project": proj,
                        "kind": "nonlabor",
                        "trx": nl_trx,
                        "category": (r.get("NL_EXP_CAT") or "").strip(),
                        "type": (r.get("NL_EXP_TYPE") or "").strip(),
                        "date": fdate(r.get("NL_EXP_DATE")),
                        "person": (r.get("NL_PER_NAME") or "").strip(),
                        "desc": nl_desc,
                        "vendor": nl_vend,
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
    types = {}      # person -> set of salary expense types

    for t in transactions:
        person = t["person"]
        if not person:
            continue
        projects.setdefault(person, set()).add(t["project"])
        if t["kind"] == "labor":
            if "fringe" in t["type"].lower():
                fringe[person] = fringe.get(person, 0.0) + t["amount"]
            else:
                types.setdefault(person, set()).add(t["type"])
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
            "facultySalary": any("faculty" in ty.lower() for ty in types.get(person, ())),
            "gra": any("gta" in ty.lower() or "gra" in ty.lower()
                       for ty in types.get(person, ())),
            "paidMonthNums": sorted({int(m[5:7]) for m, _ in nonzero}),
        })
    return people


def current_support(proj_people):
    """Per person: how their support splits across projects, using the most
    recent month that has salary activity for them."""
    by_person = {}  # person -> {month: {project: amount}}
    for pid, ppl in proj_people.items():
        for person, bym in ppl.items():
            for m, v in bym.items():
                if abs(v) > 1:
                    bym_p = by_person.setdefault(person, {}).setdefault(m, {})
                    bym_p[pid] = bym_p.get(pid, 0.0) + v

    out = {}
    for person, months in by_person.items():
        m = max(months)
        shares = {pid: v for pid, v in months[m].items() if abs(v) > 1}
        total = sum(shares.values())
        if abs(total) < 1:
            continue
        out[person] = {
            "month": m,
            "shares": sorted(
                ({"project": pid, "amount": round(v, 2), "pct": round(v / total, 4)}
                 for pid, v in shares.items()),
                key=lambda s: -s["amount"]),
        }
    return out


def project_personnel(people_months, faculty_people):
    """Who is paid from a project: recent monthly salary and last paid month."""
    out = []
    for person, bym in people_months.items():
        nonzero = [(m, v) for m, v in sorted(bym.items()) if abs(v) > 1]
        if not nonzero:
            continue
        recent = nonzero[-3:]
        out.append({
            "name": person,
            "monthly": round(sum(v for _, v in recent) / len(recent), 2),
            "lastPaid": nonzero[-1][0],
            "faculty": person in faculty_people,
        })
    out.sort(key=lambda p: (p["lastPaid"], p["monthly"]), reverse=True)
    return out


def compute_flags(projects, today_iso):
    """Rule-based issues list, most severe first."""
    flags = []
    today = date.fromisoformat(today_iso)

    for p in projects:
        active = p["status"].lower() == "active"
        label = p["shortName"]
        tot = p["totals"]

        # Rebudgeting rule: any line item may deviate by up to 10% of the
        # TOTAL award, so overruns are graded against that allowance.
        allowance = tot["budget"] * 0.10 if tot["budget"] > 0 else None

        # Exception: fringe charging far above the budgeted rate is treated
        # as critical regardless of the allowance — it usually signals a
        # rate/charging error that grows with every payroll run.
        suppress = set()
        sal = next((c for c in p["categories"]
                    if c["category"].lower().startswith("salaries")), None)
        fr = next((c for c in p["categories"]
                   if "fringe" in c["category"].lower()), None)
        if (sal and fr and sal["spent"] > 1000 and sal["budget"] > 0
                and fr["budget"] > 0 and fr["spent"] - fr["budget"] > 5000):
            charged = fr["spent"] / sal["spent"]
            budgeted = fr["budget"] / sal["budget"]
            if budgeted > 0 and charged > 1.5 * budgeted:
                suppress.add(fr["category"])
                flags.append({
                    "severity": "critical", "project": p["id"], "kind": "fringe_rate",
                    "title": f"{label}: fringe charging far above the budgeted rate",
                    "detail": (f"Fringe is running at {charged*100:.0f}% of salaries vs "
                               f"{budgeted*100:.0f}% budgeted (${fr['spent']-fr['budget']:,.0f} "
                               f"over so far, growing with every payroll) — likely a "
                               f"rate or charging error rather than a rebudgeting choice."),
                })

        for c in p["categories"]:
            if c["remaining"] >= -0.5 or c["category"] in suppress:
                continue
            over = -c["remaining"]
            if allowance:
                frac = over / allowance
                sev = ("critical" if frac >= 1.0 else "serious" if frac >= 0.5
                       else "warning" if frac >= 0.25 else "info")
                flex = (f" Over by {over/tot['budget']*100:.1f}% of the total award "
                        f"— the rebudgeting rule allows up to 10% (${allowance:,.0f}).")
            else:
                sev, flex = "serious", ""
            if c["budget"] <= 0:
                flags.append({
                    "severity": sev, "project": p["id"], "kind": "no_budget",
                    "title": f"{label}: {c['category']} charged with no budget",
                    "detail": f"${c['spent']:,.0f} spent against a $0 budget line.{flex}",
                })
            else:
                flags.append({
                    "severity": sev, "project": p["id"], "kind": "overspent",
                    "title": f"{label}: {c['category']} overspent by ${over:,.0f}",
                    "detail": (f"${c['spent']:,.0f} spent of a ${c['budget']:,.0f} budget "
                               f"({c['spent']/c['budget']*100:.0f}%).{flex}"),
                })

        if not active or not p["start"] or not p["end"]:
            continue

        # 2. past end date but still active
        end = date.fromisoformat(p["end"])
        days_left = (end - today).days
        if days_left < 0:
            flags.append({
                "severity": "warning", "project": p["id"], "kind": "past_end",
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
                "severity": sev, "project": p["id"], "kind": "ending_soon",
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
                        "severity": "serious", "project": p["id"], "kind": "overrun_pace",
                        "title": f"{label}: on pace to overrun by ~${overrun:,.0f}",
                        "detail": (f"{s_frac*100:.0f}% of budget spent with {t_frac*100:.0f}% "
                                   f"of the award period elapsed."),
                    })
                elif t_frac - s_frac > 0.30 and tot["remaining"] > 5000 and days_left > 180:
                    flags.append({
                        "severity": "info", "project": p["id"], "kind": "behind_pace",
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


# ---------------------------------------------------------------------------
# caching, and telling the page what a slow reload is doing
# ---------------------------------------------------------------------------
# A detail export can be hundreds of MB and take a good while to parse, so:
#   * each detail file is parsed once and its transactions kept, keyed on
#     (mtime, size) — a reload after a new PI-dashboard export never re-reads
#     the big file, and a new detail file costs one parse of that file only;
#   * one build runs at a time (the lock), so a second click on Reload, or a
#     page refresh, waits for the parse in flight instead of starting another;
#   * while a build runs, PROGRESS says which file is being read and how far
#     along it is, for /api/progress — the page shows it in the header.

class _Progress:
    """Where the current build is, readable from any thread."""

    def __init__(self):
        self._lock = threading.Lock()
        self._state = {"active": False}

    def start(self, plan):
        """plan: [(name, size), ...] — the files about to be read."""
        with self._lock:
            self._state = {
                "active": True, "stage": "reading",
                "file": None, "fileIndex": 0, "fileCount": len(plan),
                "fileBytes": 0, "fileSize": 0,
                "bytesDone": 0, "bytesTotal": sum(size for _, size in plan),
                "startedAt": time.time(),
            }

    def begin_file(self, name, size):
        with self._lock:
            st = self._state
            st.update(file=name, fileIndex=st["fileIndex"] + 1,
                      fileBytes=0, fileSize=size)

    def advance(self, n):
        with self._lock:
            st = self._state
            if st["active"]:
                st["fileBytes"] += n
                st["bytesDone"] += n

    def stage(self, name):
        with self._lock:
            self._state["stage"] = name

    def finish(self):
        with self._lock:
            self._state = {"active": False}

    def snapshot(self):
        with self._lock:
            return dict(self._state)


PROGRESS = _Progress()


class _CountingReader(io.RawIOBase):
    """A raw file whose reads report their byte counts to PROGRESS."""

    def __init__(self, raw):
        self._raw = raw

    def readable(self):
        return True

    def readinto(self, b):
        n = self._raw.readinto(b)
        if n:
            PROGRESS.advance(n)
            # parsing is CPU-bound and holds the interpreter lock; yielding
            # once per chunk keeps /api/progress answering promptly meanwhile
            time.sleep(0)
        return n

    def close(self):
        self._raw.close()
        super().close()


def _open_counting(path):
    """open(path) for CSV reading, with progress reporting on the way in."""
    raw = open(path, "rb", buffering=0)
    return io.TextIOWrapper(io.BufferedReader(_CountingReader(raw), 1 << 16),
                            encoding="utf-8-sig", newline="")


_build_lock = threading.Lock()
_detail_cache = {}    # str(path) -> {"key": (mtime, size), "labor", "nonlabor", "meta"}
_payload_cache = {}   # str(data_dir) -> {"key", "payload", "transactions"}
_recent_dirs = []     # data folders built most recently, newest first
KEEP_DIRS = 3         # parsed data kept in memory for this many folders


def _cache_key(data_dir):
    return tuple(sorted(
        (str(p), p.stat().st_mtime, p.stat().st_size)
        for p in Path(data_dir).glob("*.csv")
    )) + (date.today().isoformat(),)


def _ensure_cache(data_dir, root=None):
    with _build_lock:
        key = _cache_key(data_dir)
        entry = _payload_cache.get(str(data_dir))
        if entry is None or entry["key"] != key:
            payload, transactions = _build_payload_uncached(data_dir, root)
            entry = {"key": key, "payload": payload, "transactions": transactions}
            _payload_cache[str(data_dir)] = entry
            _remember_dir(data_dir)
        return entry


def _remember_dir(data_dir):
    """Keep parsed data for the last few folders looked at; drop the rest.

    Someone switching between several PIs' folders would otherwise keep every
    one of their detail exports in memory.
    """
    d = str(Path(data_dir).resolve())
    if d in _recent_dirs:
        _recent_dirs.remove(d)
    _recent_dirs.insert(0, d)
    del _recent_dirs[KEEP_DIRS:]
    for k in list(_payload_cache):
        if str(Path(k).resolve()) not in _recent_dirs:
            del _payload_cache[k]
    for k in list(_detail_cache):
        if str(Path(k).resolve().parent) not in _recent_dirs or not Path(k).is_file():
            del _detail_cache[k]


def _cached_detail(path, stat):
    key = (stat.st_mtime, stat.st_size)
    entry = _detail_cache.get(str(path))
    if entry is None or entry["key"] != key:
        labor, nonlabor, meta = {}, {}, {}
        parse_detail(path, labor, nonlabor, meta)
        entry = {"key": key, "labor": labor, "nonlabor": nonlabor, "meta": meta}
        _detail_cache[str(path)] = entry
    return entry


def _merge_meta(into, other):
    """Fold one export's per-project metadata into the merged view: first
    file to name a project wins its name and dates, the F&A rate is the
    latest one seen, and the report windows accumulate."""
    for pid, src in other.items():
        m = into.setdefault(pid, {
            "name": None, "start": None, "end": None,
            "faRate": None, "windows": set(),
        })
        m["name"] = m["name"] or src["name"]
        m["start"] = m["start"] or src["start"]
        m["end"] = m["end"] or src["end"]
        if src["faRate"] is not None:
            m["faRate"] = src["faRate"]
        m["windows"] |= src["windows"]


def build_payload(data_dir, root=None):
    payload = dict(_ensure_cache(data_dir, root)["payload"])
    payload["config"] = load_config(data_dir)  # config always fresh
    if root is not None:
        payload["profile"] = profile_info(root, data_dir)
    return payload


def load_transactions(data_dir, root=None):
    """Every de-duplicated transaction from the detail exports (cached)."""
    return _ensure_cache(data_dir, root)["transactions"]


def _build_payload_uncached(data_dir, root=None):
    today_iso = date.today().isoformat()
    this_month = today_iso[:7]

    files_info = []
    dash_files = []           # (mtime, rows)
    labor, nonlabor, meta = {}, {}, {}

    files = [(p, p.stat(), classify_csv(p)) for p in sorted(Path(data_dir).glob("*.csv"))]
    # what actually has to be read: detail exports not parsed yet (or changed)
    plan = [(p.name, st.st_size) for p, st, kind in files
            if kind == "detail" and (str(p) not in _detail_cache
                                     or _detail_cache[str(p)]["key"] != (st.st_mtime, st.st_size))]
    PROGRESS.start(plan)
    try:
        for path, stat, kind in files:
            files_info.append({
                "name": path.name,
                "type": kind or "unrecognized",
                "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
            })
            if kind == "pi_dashboard":
                dash_files.append((stat.st_mtime, parse_pi_dashboard(path)))
            elif kind == "detail":
                if any(name == path.name for name, _ in plan):
                    PROGRESS.begin_file(path.name, stat.st_size)
                entry = _cached_detail(path, stat)
                for key, t in entry["labor"].items():
                    labor.setdefault(key, t)
                for key, t in entry["nonlabor"].items():
                    nonlabor.setdefault(key, t)
                _merge_meta(meta, entry["meta"])
        PROGRESS.stage("analyzing")
        return _analyze(data_dir, root, today_iso, this_month, files_info, dash_files,
                        labor, nonlabor, meta)
    finally:
        PROGRESS.finish()


def _analyze(data_dir, root, today_iso, this_month, files_info, dash_files,
             labor, nonlabor, meta):
    """The number-crunching half of a build, on already-parsed exports."""

    # newest PI-dashboard file wins per project
    dash_by_project = {}
    for _, rows in sorted(dash_files, key=lambda x: x[0]):
        by_proj = {}
        for r in rows:
            by_proj.setdefault(r["project"], []).append(r)
        dash_by_project.update(by_proj)

    transactions = list(labor.values()) + list(nonlabor.values())

    # transaction aggregates per project, split four ways:
    #   fac       — faculty (PI summer) salary and its fringe
    #   personnel — all other salaries and fringe
    #   fees      — student fees / tuition (person-linked, so projections
    #               can retire them with the person)
    #   other     — every remaining non-labor cost (travel, indirect, ...)
    faculty_people = {t["person"] for t in transactions
                      if t["kind"] == "labor" and t["person"]
                      and "faculty" in t["type"].lower()}
    monthly = {}        # project -> {month: net}
    monthly_parts = {}  # project -> {month: {fac, personnel, other}}
    proj_people = {}    # project -> person -> {month: salary}
    proj_fees = {}      # project -> person -> {month: fees/tuition}
    for t in transactions:
        m = month_of(t["date"])
        if not m:
            continue
        bym = monthly.setdefault(t["project"], {})
        bym[m] = bym.get(m, 0.0) + t["amount"]
        parts = monthly_parts.setdefault(t["project"], {}).setdefault(
            m, {"fac": 0.0, "personnel": 0.0, "fees": 0.0, "other": 0.0})
        if t["kind"] == "labor":
            ty = t["type"].lower()
            is_fringe = "fringe" in ty
            is_fac = "faculty" in ty or (is_fringe and t["person"] in faculty_people)
            parts["fac" if is_fac else "personnel"] += t["amount"]
            if not is_fringe and t["person"]:
                bymp = proj_people.setdefault(t["project"], {}).setdefault(t["person"], {})
                bymp[m] = bymp.get(m, 0.0) + t["amount"]
        else:
            ty = t["type"].lower()
            if "fee" in ty or "tuition" in ty:
                parts["fees"] += t["amount"]
                if t["person"]:
                    bymf = proj_fees.setdefault(t["project"], {}).setdefault(t["person"], {})
                    bymf[m] = bymf.get(m, 0.0) + t["amount"]
            else:
                parts["other"] += t["amount"]

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

        # burn rates averaged over CALENDAR months — quiet months count as
        # zero, so lumpy/quarterly billing isn't overstated. Windows are
        # anchored at the last complete month inside the export's coverage
        # and never reach back before the coverage (or award) start.
        bym = monthly.get(pid, {})
        recent_burn = None
        avg12 = None
        recent_months = []
        if bym:
            windows = dmeta.get("windows") or set()
            cov_start = min(w[0] for w in windows)[:7] if windows else min(bym)
            cov_end = max(w[1] for w in windows)[:7] if windows else max(bym)
            floor = max(cov_start, start[:7]) if start else cov_start
            last_complete = min(month_add(this_month, -1), cov_end)
            if floor <= last_complete:
                w3 = [m for m in (month_add(last_complete, -i) for i in range(3))
                      if m >= floor]
                recent_months = sorted(w3)
                recent_burn = sum(bym.get(m, 0.0) for m in w3) / len(w3)
                w12 = [m for m in (month_add(last_complete, -i) for i in range(12))
                       if m >= floor]
                avg12 = sum(bym.get(m, 0.0) for m in w12) / len(w12)
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
            "monthlyParts": {m: {k: round(v, 2) for k, v in parts.items()}
                             for m, parts in sorted(monthly_parts.get(pid, {}).items())},
            "personnel": project_personnel(proj_people.get(pid, {}), faculty_people),
            "burn": {
                "recent": round(recent_burn, 2) if recent_burn is not None else None,
                "recentMonths": recent_months,
                "avg12": round(avg12, 2) if avg12 is not None else None,
                "linear": round(linear_burn, 2) if linear_burn is not None else None,
            },
            "hasDetail": pid in meta,
            "inDashboard": pid in dash_by_project,
            # the accounting-date window the detail export was pulled for —
            # the charge lookup warns when asked about months outside it
            "detailWindow": ([min(w[0] for w in dmeta["windows"]),
                              max(w[1] for w in dmeta["windows"])]
                             if dmeta.get("windows") else None),
        })

    # people estimates need the export coverage window length
    window_months = 0.0
    windows = [w for m in meta.values() for w in m["windows"]]
    if windows:
        w_from = min(w[0] for w in windows)
        w_to = max(w[1] for w in windows)
        window_months = max(1.0, months_between(w_from, w_to))
    people = estimate_people(transactions, window_months)
    support = current_support(proj_people)
    by_person_proj = {}
    for proj_id, ppl in proj_people.items():
        for name, bym in ppl.items():
            by_person_proj.setdefault(name, {})[proj_id] = {
                m: round(v, 2) for m, v in sorted(bym.items()) if abs(v) > 1}
    fees_by_person_proj = {}
    for proj_id, ppl in proj_fees.items():
        for name, bym in ppl.items():
            fees_by_person_proj.setdefault(name, {})[proj_id] = {
                m: round(v, 2) for m, v in sorted(bym.items()) if abs(v) > 1}
    for person in people:
        person["support"] = support.get(person["name"])
        person["salaryByProject"] = by_person_proj.get(person["name"], {})
        person["feesByProject"] = fees_by_person_proj.get(person["name"], {})

    flags = compute_flags([p for p in projects if p["inDashboard"]], today_iso)

    source = load_report_source(data_dir, root)
    return {
        "today": today_iso,
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "files": files_info,
        "projects": projects,
        "people": people,
        "flags": flags,
        # what the "Get fresh data" section needs to offer download links
        "reportSource": {
            "host": source["host"],
            "template": source["template"],
            "piDashboardUrl": source["piDashboardUrl"],
        },
    }, transactions


# ---------------------------------------------------------------------------
# PI folders — one data folder per PI, for someone who looks after several
# ---------------------------------------------------------------------------
# A PI keeps their exports straight in data/ and never sees any of this. A
# business office keeps a subfolder per PI (data/Holmes, data/Lee, ...): each
# is a complete data folder of its own — exports, config.json, scenarios —
# and the page switches between them with ?pi=<name>. Nothing is ever moved
# between folders; imports land in whichever folder the page is showing.

class NoSuchProfile(ValueError):
    pass


def profile_name_ok(name):
    return bool(PROFILE_NAME_RE.match(name or "")) and ".." not in name \
        and not name.endswith(".")


def profile_dir(root, name):
    """The data folder for a PI name (the root itself for no name)."""
    root = Path(root)
    if not name:
        return root
    if not profile_name_ok(name):
        raise NoSuchProfile(f"not a usable PI folder name: {name!r}")
    d = root / name
    if not d.is_dir() or d.resolve().parent != root.resolve():
        raise NoSuchProfile(f"there is no PI folder named {name!r} in {root}")
    return d


def create_profile(root, name):
    """Make data/<name>/ (a no-op if it exists) and return the clean name."""
    name = " ".join(str(name or "").split())
    if not profile_name_ok(name):
        raise ValueError("a PI folder name is letters, digits, spaces and "
                         "simple punctuation — e.g. \"Holmes\" or \"Doe, Jane\"")
    d = Path(root) / name
    d.mkdir(exist_ok=True)
    return name


def _folder_summary(d):
    files = [p for p in Path(d).glob("*.csv") if classify_csv(p)]
    newest = max((p.stat().st_mtime for p in files), default=None)
    return {
        "files": len(files),
        "modified": datetime.fromtimestamp(newest).strftime("%Y-%m-%d") if newest else None,
        "hasConfig": config_path(d).is_file(),
    }


def list_profiles(root):
    """The PI folders inside the data folder, alphabetically."""
    out = []
    try:
        children = sorted(Path(root).iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return out
    for d in children:
        if d.is_dir() and not d.name.startswith((".", "_")) and profile_name_ok(d.name):
            out.append(dict(_folder_summary(d), name=d.name))
    return out


def profile_info(root, data_dir):
    """What the page needs for its PI switcher."""
    root = Path(root)
    data_dir = Path(data_dir)
    current = "" if data_dir.resolve() == root.resolve() else data_dir.name
    return {
        "current": current,
        "root": dict(_folder_summary(root), name=root.name, path=str(root)),
        "profiles": list_profiles(root),
    }


def load_report_source(data_dir, root=None, **overrides):
    """report_source.json from the data folder, then the PI folder's own."""
    dirs = [root, data_dir] if root and Path(root) != Path(data_dir) else [data_dir]
    return spn_reports.load_source(dirs, **overrides)


# --- whose export is this? -------------------------------------------------
# The Downloads folder is shared by every PI someone looks after, and the
# reporting system names every export the same way. A PI-dashboard export
# carries the PI's name; a detail export carries project numbers; either
# can be matched against what each PI folder already holds.

_sniff_cache = {}   # str(path) -> {"key": (mtime, size), "pis": set, "projects": set}


def sniff_csv(path, kind=None):
    """PI names and project numbers named in an export (the first rows of a
    detail export are plenty — its cartesian product repeats them)."""
    path = Path(path)
    try:
        stat = path.stat()
    except OSError:
        return {"pis": set(), "projects": set()}
    key = (stat.st_mtime, stat.st_size)
    hit = _sniff_cache.get(str(path))
    if hit and hit["key"] == key:
        return hit
    kind = kind or classify_csv(path)
    pis, projects = set(), set()
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            headers = reader.fieldnames or []
            pi_col = next((h for h in headers if "PI" in h and "Manager" in h), None)
            proj_col = ("Project Number" if kind == "pi_dashboard" else "PROJ_NUMBER")
            for i, row in enumerate(reader):
                if kind == "detail" and i >= SNIFF_ROWS:
                    break
                code = (row.get(proj_col) or "").strip().upper()
                if code:
                    projects.add(code)
                if pi_col:
                    pi = " ".join((row.get(pi_col) or "").split())
                    if pi:
                        pis.add(pi)
    except (OSError, csv.Error, ValueError):   # ValueError: a half-written file
        pass
    hit = {"key": key, "pis": pis, "projects": projects}
    _sniff_cache[str(path)] = hit
    return hit


def folder_identity(d):
    """PI names and projects in a data folder's PI-dashboard exports (small
    files; the detail exports are not read for this)."""
    pis, projects = set(), set()
    for p in Path(d).glob("*.csv"):
        if classify_csv(p) == "pi_dashboard":
            found = sniff_csv(p, "pi_dashboard")
            pis |= found["pis"]
            projects |= found["projects"]
    return {"pis": pis, "projects": projects}


def match_owner(found, identities):
    """Names of the folders an export's PI names or projects overlap with."""
    return [name for name, ident in identities
            if (found["pis"] & ident["pis"]) or (found["projects"] & ident["projects"])]



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
# the inbox: exports sitting in the Downloads folder, waiting to be imported
# ---------------------------------------------------------------------------
# A report link downloads to wherever your browser puts downloads, so the last
# step of "get fresh data" is moving that file into data/. Rather than make the
# user do it in Finder, we list the CSVs there that we recognize as exports and
# copy them across on a click. Read-only until then; nothing is imported by
# itself unless the front-end asks (which it only does for files that appeared
# after you clicked a download link).

def scan_inbox(inbox_dir, data_dir, root=None):
    """Recognized CSV exports in the Downloads folder, newest first.

    Everything here tolerates files appearing and vanishing mid-scan: this
    runs while a browser is writing a download into the very same folder.
    With a root, each file also says which PI folders it seems to belong to
    (by PI name or project numbers) and which already hold a copy.
    """
    if inbox_dir is None:
        return []
    # (name, folder) for every data folder an export could go into
    folders = [("", Path(root))] if root else []
    folders += [(p["name"], Path(root) / p["name"]) for p in list_profiles(root)] if root else []
    identities = [(name, folder_identity(d)) for name, d in folders]
    try:
        candidates = [p for p in Path(inbox_dir).iterdir() if p.suffix.lower() == ".csv"]
    except OSError:
        return []

    stats = []
    for path in candidates:
        try:
            stat = path.stat()
        except OSError:
            continue
        if not path.is_file():
            continue
        stats.append((path, stat))

    found = []
    now = datetime.now().timestamp()
    for path, stat in sorted(stats, key=lambda ps: ps[1].st_mtime, reverse=True):
        kind = classify_csv(path)
        if kind is None:
            continue
        existing = Path(data_dir) / path.name

        def has_copy(d):
            try:
                other = d / path.name
                return other.is_file() and other.stat().st_size == stat.st_size
            except OSError:
                return False

        sniffed = sniff_csv(path, kind) if root else {"pis": set(), "projects": set()}
        found.append({
            "name": path.name,
            "type": kind,
            "size": stat.st_size,
            "mtime": stat.st_mtime,
            "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
            # same name and size in data/ already: importing again is a no-op
            "imported": has_copy(Path(data_dir)),
            # a detail export can be hundreds of MB, and some browsers write
            # straight to the final name — don't import one mid-download
            "settled": now - stat.st_mtime >= SETTLE_SECONDS,
            # the PI named inside a PI-dashboard export, if any
            "pi": ", ".join(sorted(sniffed["pis"])),
            # PI folders whose exports overlap this one, and those with a copy
            "matches": match_owner(sniffed, identities),
            "importedTo": [name for name, d in folders if has_copy(d)],
        })
        if len(found) >= MAX_INBOX_FILES:
            break
    return found


def import_from_inbox(names, inbox_dir, data_dir):
    """Copy named Downloads-folder exports into the data folder.

    Only plain filenames directly inside the inbox folder are accepted, and
    only files whose header marks them as one of the two reports — so a stray
    request can't pull in arbitrary files. The original stays in Downloads.
    """
    if inbox_dir is None:
        raise ValueError("the Downloads folder is not being watched (--no-inbox)")
    inbox = Path(inbox_dir).resolve()
    imported, skipped = [], []
    for raw in list(names)[:MAX_INBOX_FILES]:
        name = Path(str(raw)).name
        src = inbox / name
        if src.parent.resolve() != inbox or not src.is_file() \
                or src.suffix.lower() != ".csv" or classify_csv(src) is None:
            skipped.append(name)
            continue
        dest = Path(data_dir) / name
        if dest.is_file() and dest.stat().st_size == src.stat().st_size:
            skipped.append(name)  # already imported
            continue
        # keep both when a same-named export differs: exports merge, and the
        # newest PI-dashboard file wins per project anyway
        n = 2
        while dest.exists():
            dest = Path(data_dir) / f"{src.stem} ({n}){src.suffix}"
            n += 1
        shutil.copy2(src, dest)
        imported.append(dest.name)
    return {"imported": imported, "skipped": skipped}


def charges_response(query, data_dir, root=None):
    """Filtered transaction list for /api/charges (the charge lookup section).

    ?project=SPN107048&project=SPN107049&from=2026-06-01&to=2026-09-18 —
    one or more projects (repeated, or space/comma separated), inclusive date
    bounds (both optional). Each charge carries its project so a combined
    lookup stays attributable. Charges the export left undated are always
    included and counted separately, so nothing disappears silently.
    """
    codes = spn_reports.normalize_projects(query.get("project", []))
    if not codes:
        raise ValueError("pass at least one project, e.g. ?project=SPN107048")
    wanted = set(codes)
    from_iso = to_iso = None
    if _one(query, "from"):
        from_iso = spn_reports.parse_date(_one(query, "from")).isoformat()
    if _one(query, "to"):
        to_iso = spn_reports.parse_date(_one(query, "to")).isoformat()
    if from_iso and to_iso and to_iso < from_iso:
        raise ValueError("the end of the date window is before its start")

    charges, undated, total = [], 0, 0.0
    for t in load_transactions(data_dir, root):
        if t["project"] not in wanted:
            continue
        if t["date"] is None:
            undated += 1
        elif (from_iso and t["date"] < from_iso) or (to_iso and t["date"] > to_iso):
            continue
        charges.append({k: t[k] for k in
                        ("project", "date", "kind", "trx", "category", "type",
                         "person", "desc", "vendor", "amount")})
        total += t["amount"]
    # newest first; undated lines sink to the bottom
    charges.sort(key=lambda c: (c["date"] is not None, c["date"] or ""), reverse=True)
    return {
        "projects": codes,
        "from": from_iso,
        "to": to_iso,
        "count": len(charges),
        "undated": undated,
        "total": round(total, 2),
        "charges": charges,
    }


def report_links_response(query, data_dir, root=None):
    """Build the download links for /api/report-links from its query string."""
    projects = spn_reports.normalize_projects(query.get("projects", []))
    source = load_report_source(data_dir, root, template=_one(query, "template"))
    from_date = _one(query, "from")
    to_date = _one(query, "to")
    combined = _one(query, "mode") != "per-project"
    links = spn_reports.report_links(projects, from_date, to_date,
                                     combined=combined, source=source)
    return {
        "projects": projects,
        "from": spn_reports.report_date(from_date, source),
        "to": spn_reports.report_date(to_date or date.today(), source),
        "combined": combined,
        "links": links,
        # to_date omitted on purpose: the bookmarklet re-computes "today" every
        # time it is clicked, so it keeps working without being regenerated
        "bookmarklet": spn_reports.make_bookmarklet(
            projects, from_date, combined=combined, source=source),
        "template": source["template"],
    }


def _one(query, key):
    """First value of a query parameter, or None."""
    values = query.get(key) or []
    value = (values[0] or "").strip() if values else ""
    return value or None


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


def make_handler(root_dir, inbox_dir=None):
    """The request handler for a data folder (whose PI subfolders, if any,
    are reached with ?pi=<name> on every API call)."""
    root_dir = Path(root_dir)

    class Handler(BaseHTTPRequestHandler):
        server_version = "UTKGrantDashboard/1.0"

        def _data_dir(self, query):
            """The data folder this request is about: the PI folder named
            by ?pi=, or the data folder itself."""
            return profile_dir(root_dir, _one(query, "pi"))

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

        def _local_caller(self):
            """Refuse anything that isn't this page talking to its own server.

            The server only listens on 127.0.0.1, but a web page you visit can
            still aim a request at localhost (and a hostname that resolves to
            127.0.0.1 can carry it further). Checking Host and Origin keeps
            those out: a page on another origin can neither read your data nor
            ask for an import.
            """
            for header, value in (("Host", self.headers.get("Host")),
                                  ("Origin", self.headers.get("Origin"))):
                if not value:
                    continue
                host = urlparse(value if "//" in value else "//" + value).hostname
                if host not in ("127.0.0.1", "::1", "localhost"):
                    self._send(403, json.dumps(
                        {"error": f"refusing a request with {header}: {value} — "
                                  f"open the dashboard at http://127.0.0.1"}))
                    return False
            return True

        def do_GET(self):
            if not self._local_caller():
                return
            parts = urlparse(self.path)
            path = parts.path
            query = parse_qs(parts.query, keep_blank_values=True)
            if path == "/":
                self._send_file(STATIC_DIR / "index.html")
            elif path.startswith("/static/"):
                self._send_file(STATIC_DIR / path[len("/static/"):])
            elif path == "/api/progress":
                # never waits on a build — it is how the page watches one
                self._send(200, json.dumps(PROGRESS.snapshot()))
            elif path == "/api/data":
                try:
                    payload = build_payload(self._data_dir(query), root_dir)
                    self._send(200, json.dumps(payload))
                except NoSuchProfile as exc:
                    self._send(404, json.dumps({"error": str(exc)}))
                except Exception as exc:  # surface parse errors in the UI
                    self._send(500, json.dumps({"error": str(exc)}))
            elif path == "/api/report-links":
                try:
                    self._send(200, json.dumps(
                        report_links_response(query, self._data_dir(query), root_dir)))
                except Exception as exc:
                    self._send(400, json.dumps({"error": str(exc)}))
            elif path == "/api/charges":
                try:
                    self._send(200, json.dumps(
                        charges_response(query, self._data_dir(query), root_dir)))
                except Exception as exc:
                    self._send(400, json.dumps({"error": str(exc)}))
            elif path == "/api/inbox":
                try:
                    self._send(200, json.dumps({
                        "dir": str(inbox_dir) if inbox_dir else None,
                        "files": scan_inbox(inbox_dir, self._data_dir(query), root_dir),
                    }))
                except Exception as exc:
                    self._send(400, json.dumps({"error": str(exc)}))
            else:
                self._send(404, json.dumps({"error": "not found"}))

        def do_POST(self):
            if not self._local_caller():
                return
            parts = urlparse(self.path)
            path = parts.path
            query = parse_qs(parts.query, keep_blank_values=True)
            if path not in ("/api/config", "/api/import", "/api/profiles"):
                self._send(404, json.dumps({"error": "not found"}))
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length > MAX_CONFIG_BYTES:
                    raise ValueError("request too large")
                body = json.loads(self.rfile.read(length).decode("utf-8"))
                if path == "/api/profiles":
                    name = body.get("name") if isinstance(body, dict) else None
                    created = create_profile(root_dir, name)
                    self._send(200, json.dumps({"ok": True, "name": created}))
                    return
                data_dir = self._data_dir(query)
                if path == "/api/config":
                    save_config(data_dir, body)
                    self._send(200, json.dumps({"ok": True}))
                else:
                    names = body.get("names") if isinstance(body, dict) else None
                    if not isinstance(names, list):
                        raise ValueError("expected {\"names\": [...]}")
                    result = import_from_inbox(names, inbox_dir, data_dir)
                    self._send(200, json.dumps(dict(result, ok=True)))
            except Exception as exc:
                self._send(400, json.dumps({"error": str(exc)}))

        def log_message(self, fmt, *args):
            pass  # keep the terminal quiet

    return Handler


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--data", type=Path, default=DEFAULT_DATA_DIR,
                    help="folder containing the CSV exports (default: ./data); "
                         "a subfolder per PI if you look after several")
    ap.add_argument("--downloads", type=Path, default=DEFAULT_INBOX_DIR,
                    help="where your browser saves downloads, so freshly "
                         "downloaded exports can be imported with one click "
                         f"(default: {DEFAULT_INBOX_DIR})")
    ap.add_argument("--no-inbox", action="store_true",
                    help="don't look at the downloads folder at all")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    data_dir = args.data
    data_dir.mkdir(parents=True, exist_ok=True)
    inbox_dir = None if args.no_inbox or not args.downloads.is_dir() else args.downloads

    handler = make_handler(data_dir, inbox_dir)
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
