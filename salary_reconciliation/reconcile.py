#!/usr/bin/env python3
"""Salary reconciliation — Labor Distribution vs. the General Ledger.

The business-office routine this automates:

  1. Run the Labor Distribution report and sort it by LD Account
     Combination, then person name (the combination carries the fund *and*
     the GL account, so the sort groups each fund's salary lines by GL code).
  2. Run the DetailBalances report (General Accounting), which gives the
     period's posted amount for every account combination.
  3. Where a combination's Labor Distribution total does not match the
     ledger, run the Fund Line Items report to find out which lines differ.

This module reads the three exports (CSV or .xlsx, recognized by their
header row, not their filename), lines them up per account combination and
fiscal period, and for every difference lists the ledger lines with no
Labor Distribution line behind them and vice versa. Fund Line Items lines
are tied to Labor Distribution lines by Ref Doc = Transaction Number.

Standard library only. Also usable without the browser page:

    python reconcile.py LD.csv DetailBalances.xlsx FundLineItems.xlsx -o out.xlsx
    python reconcile.py -o out.xlsx       # the newest reports in data/
"""

import argparse
import csv
import io
import re
import sys
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import xls  # noqa: E402
import xlsx  # noqa: E402

# The eight segments of a chart-of-accounts string such as
# 10-1100001-106015-512100-210-0000-00-0000, with their usual widths.
SEGMENTS = ("entity", "fund", "department", "account", "program",
            "activity", "interco", "future")
WIDTHS = (2, 7, 6, 6, 3, 4, 2, 4)
# Fund Line Items carries only the first six; they are what ties its
# lines to a combination.
FLI_SEGMENTS = 6

LD, GL, FLI = "labor_distribution", "detail_balances", "fund_line_items"
KIND_NAMES = {LD: "Labor Distribution", GL: "DetailBalances",
              FLI: "Fund Line Items"}
# Headers that identify each report (all must be present).
SIGNATURES = {
    LD: ("LD Account Combination", "Distribution Amount", "Person Name"),
    GL: ("Accounting Period", "Fund", "Account", "Period Activity (USD)"),
    FLI: ("FI_DocNo", "GL Account", "Amount", "Ref Doc"),
}
HEADER_SEARCH_ROWS = 30
# Salary accounts outside the family of the ones payroll charges (see
# reconcile()): compared too whenever the ledger shows activity on them.
EXTRA_SALARY_ACCOUNTS = {
    "537600",  # Joint Faculty Salaries
}
DATA_DIR = Path(__file__).resolve().parent / "data"


class ReportError(ValueError):
    pass


# ---------------------------------------------------------------------------
# small parsing helpers
# ---------------------------------------------------------------------------

def cents(s):
    """'1,243.95', '$-12', '(300.00)', 1243.9500000001 -> integer cents;
    blank -> 0. Raises ReportError on anything else."""
    text = str(s if s is not None else "").strip().replace(",", "").replace("$", "")
    if not text:
        return 0
    neg = text.startswith("(") and text.endswith(")")
    if neg:
        text = text[1:-1]
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise ReportError(f"not an amount: {s!r}")
    c = int((value * 100).quantize(Decimal("1"), rounding="ROUND_HALF_UP"))
    return -c if neg else c


def segment(value, width):
    """Normalize one account segment: '1100001', 1100001.0 and ' 1100001 '
    are the same fund; '0' in an Activity column is '0000'."""
    v = str(value or "").strip()
    if re.fullmatch(r"\d+\.0+", v):
        v = v.split(".")[0]
    return v.zfill(width) if v.isdigit() else v


def combo_parts(text):
    parts = [p for p in str(text or "").strip().split("-")]
    if len(parts) != len(SEGMENTS):
        return None
    return tuple(segment(p, w) for p, w in zip(parts, WIDTHS))


def period(text):
    """'27-02', '2027-2', '27-2' -> '27-02' (fiscal year - period)."""
    v = str(text or "").strip()
    m = re.fullmatch(r"(\d{2}|\d{4})\s*-\s*(\d{1,2})", v)
    if m:
        return f"{int(m.group(1)) % 100:02d}-{int(m.group(2)):02d}"
    return v


def period_sort_key(p):
    m = re.fullmatch(r"(\d{2})-(\d{2})", p)
    return (0, int(m.group(1)), int(m.group(2))) if m else (1, 0, 0, p)


def money(c):
    """Cents -> '1,243.95' (for messages)."""
    sign = "-" if c < 0 else ""
    c = abs(c)
    return f"{sign}{c // 100:,}.{c % 100:02d}"


# ---------------------------------------------------------------------------
# reading files
# ---------------------------------------------------------------------------

def _decode(data):
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16")
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


def read_table(data):
    """Rows of text from a file's bytes: .xlsx, any of the formats that go by
    .xls, or CSV/TSV text. Decided by the contents, not the name — an
    export called .xls is often one of the others."""
    if xlsx.is_xlsx(data):
        return xlsx.read_rows(data)
    if xls.kind(data):
        return xls.read_rows(data)
    text = _decode(data)
    first = text.split("\n", 1)[0]
    delim = "\t" if first.count("\t") > first.count(",") else ","
    return [row for row in csv.reader(io.StringIO(text), delimiter=delim)]


def _clean(h):
    return re.sub(r"\s+", " ", str(h or "")).strip()


def find_header(rows):
    """(kind, header_index) for the first row that carries a report's
    signature headers, or (None, None)."""
    for i, row in enumerate(rows[:HEADER_SEARCH_ROWS]):
        names = {_clean(c) for c in row}
        for kind, sig in SIGNATURES.items():
            if all(s in names for s in sig):
                return kind, i
    return None, None


def identify(data):
    """Which report a file's bytes are, and its rows."""
    rows = read_table(data)
    kind, at = find_header(rows)
    return kind, at, rows


def _records(rows, at):
    """Dicts keyed by header for the rows after the header. Repeated header
    names (Fund Line Items has two 'Vendor' columns) keep the first."""
    header = [_clean(h) for h in rows[at]]
    index = {}
    for i, h in enumerate(header):
        index.setdefault(h, i)
    for row in rows[at + 1:]:
        if not any(str(c).strip() for c in row):
            continue
        yield {h: (row[i] if i < len(row) else "") for h, i in index.items()}


# ---------------------------------------------------------------------------
# the three reports
# ---------------------------------------------------------------------------

def parse_ld(rows, at):
    lines = []
    for r in _records(rows, at):
        combo = combo_parts(r.get("LD Account Combination"))
        if combo is None:
            continue  # a total or footer row
        lines.append({
            "combo": combo,
            "period": period(r.get("Fiscal Period - LD") or r.get("Fiscal Period - Payroll")),
            "person": _clean(r.get("Person Name")),
            "person_number": _clean(r.get("Person Number")),
            "assignment": _clean(r.get("Assignment Name")),
            "assignment_number": _clean(r.get("Assignment Number")),
            "pay_element": _clean(r.get("Pay Element Name")),
            "pay_start": _clean(r.get("Payroll Period Start Date")),
            "pay_end": _clean(r.get("Payroll Period End Date")),
            "created": _clean(r.get("Creation Date")),
            "percent": _clean(r.get("Line Percentage")),
            "txn": _clean(r.get("Transaction Number")),
            "status": _clean(r.get("Status")),
            "amount": cents(r.get("Distribution Amount")),
        })
    return lines


def parse_gl(rows, at):
    lines = []
    header = {_clean(h) for h in rows[at]}
    seg_cols = {"entity": "Entity", "fund": "Fund", "department": "Department",
                "account": "Account", "program": "Program", "activity": "Activity",
                "interco": "InterCo", "future": "Future"}
    missing = [c for c in seg_cols.values() if c not in header]
    for r in _records(rows, at):
        if not str(r.get("Account") or "").strip():
            continue
        combo = tuple(segment(r.get(seg_cols[s]) if seg_cols[s] in header else "", w)
                      for s, w in zip(SEGMENTS, WIDTHS))
        lines.append({
            "combo": combo,
            "period": period(r.get("Accounting Period")),
            "beginning": cents(r.get("Beginning Balance (USD)")),
            "activity": cents(r.get("Period Activity (USD)")),
            "ending": cents(r.get("Ending Balance (USD)")),
        })
    return lines, missing


# A payroll line's text in Fund Line Items:
# "Accounting for Holmes, Tova Assignment name: Associate Professor"
LABOR_TEXT = re.compile(r"Accounting for (.+?)\s+Assignment name:\s*(.*)$")


def parse_fli(rows, at):
    lines = []
    for r in _records(rows, at):
        if not str(r.get("GL Account") or "").strip() or not str(r.get("Fund") or "").strip():
            continue  # the sub-header row ('Descr.') and totals
        try:
            year, per = int(float(r.get("Year"))), int(float(r.get("Period")))
            fiscal = f"{year % 100:02d}-{per:02d}"
        except (TypeError, ValueError):
            fiscal = ""
        text = _clean(r.get("SPL Doc Line Item Txt"))
        labor = LABOR_TEXT.match(text)
        key = (segment(r.get("Entity ID"), 2), segment(r.get("Fund"), 7),
               segment(r.get("Department ID"), 6), segment(r.get("GL Account"), 6),
               segment(r.get("Program ID"), 3), segment(r.get("Activity Code"), 4))
        lines.append({
            "key": key,
            "period": fiscal,
            # Already signed: credits are negative, and a reversal is a
            # negative debit (checked: the lines add up to DetailBalances).
            "amount": cents(r.get("Amount")),
            "account_name": _clean(r.get("G/L Account Text")),
            "doc": _clean(r.get("FI_DocNo")),
            "line": _clean(r.get("LnItm")),
            "posted": _clean(r.get("Entry dte") or r.get("Pstng Date")),
            "text": text,
            # whose pay it is, for payroll lines ('' for anything else)
            "person": labor.group(1) if labor else "",
            "assignment": labor.group(2) if labor else "",
            "header_text": _clean(r.get("Document Header Text")),
            "doc_type": _clean(r.get("Document Type")),
            "ref": _clean(r.get("Ref Doc")),
            "user": _clean(r.get("User Name")),
            "record": _clean(r.get("Record No.")),
            "record_type": _clean(r.get("Record Type")),
        })
    return lines


# ---------------------------------------------------------------------------
# reconciliation
# ---------------------------------------------------------------------------

def combo_text(combo):
    return "-".join(combo)


def _ld_sort_key(line):
    return (line["person"].lower(), line["pay_start"], line["pay_element"], line["txn"])


def _match_fli(ld_lines, fli_lines):
    """Pair ledger lines with Labor Distribution lines by reference: Fund
    Line Items' Ref Doc is the LD Transaction Number. (Not by amount: on a
    busy account two people paid the same would be paired by mistake.)
    Returns the pairs with equal amounts, those whose amounts differ, and
    the lines left on each side."""
    ld_left = list(ld_lines)
    gl_left = []
    matched, amount_differs = [], []
    by_txn = {}
    for ld in ld_left:
        if ld["txn"]:
            by_txn.setdefault(ld["txn"], []).append(ld)
    for gl in fli_lines:
        candidates = by_txn.get(gl["ref"]) or []
        if not candidates:
            gl_left.append(gl)
            continue
        exact = [c for c in candidates if c["amount"] == gl["amount"]]
        ld = (exact or candidates)[0]
        candidates.remove(ld)
        ld_left.remove(ld)
        (matched if exact else amount_differs).append({"ld": ld, "gl": gl})
    return matched, amount_differs, gl_left, ld_left


def reconcile(ld_lines, gl_lines=None, fli_lines=None):
    """Line up the reports. Every argument but the first may be None (that
    report hasn't been loaded yet); the result says what can be concluded
    from what there is."""
    ld_ok = [l for l in ld_lines if l["status"].lower() in ("", "success")]
    excluded = [l for l in ld_lines if l not in ld_ok]

    ld_periods = sorted({l["period"] for l in ld_ok}, key=period_sort_key)
    gl_periods = (sorted({g["period"] for g in gl_lines}, key=period_sort_key)
                  if gl_lines is not None else None)
    # With a ledger report, compare the periods it covers; LD lines in
    # other periods are reported, not compared.
    compared = gl_periods if gl_periods is not None else ld_periods
    compared_set = set(compared)
    outside = [l for l in ld_ok if l["period"] not in compared_set]

    # Salary-type accounts: the ones payroll charged, their family (same
    # first two digits — 512100 Faculty Salaries brings in 51xxxx), and
    # EXTRA_SALARY_ACCOUNTS.
    salary_families = {l["combo"][3][:2] for l in ld_ok}

    def salary_account(account):
        return account[:2] in salary_families or account in EXTRA_SALARY_ACCOUNTS

    buckets = {}

    def bucket(combo, per):
        return buckets.setdefault((combo, per), {
            "combo": combo, "period": per, "ld_lines": [], "gl_total": None})

    for l in ld_ok:
        if l["period"] in compared_set:
            bucket(l["combo"], l["period"])["ld_lines"].append(l)
    for g in gl_lines or []:
        # Salary activity on a combination with no Labor Distribution
        # behind it is the other half of the question; other accounts
        # (cash, revenue, operating expense) are not.
        if (g["combo"], g["period"]) in buckets or (
                salary_account(g["combo"][3]) and g["activity"] != 0):
            b = bucket(g["combo"], g["period"])
            b["gl_total"] = (b["gl_total"] or 0) + g["activity"]

    # The funds the DetailBalances report was run for. It lists every
    # account of a fund it covers (zero balances included), so a fund and
    # department with no row at all in a period is outside the report, not
    # a missing posting.
    gl_scope = {(g["combo"][:3], g["period"]) for g in gl_lines or []}

    fli_index, fli_coverage, claimed = {}, set(), set()
    for f in fli_lines or []:
        fli_index.setdefault((f["key"], f["period"]), []).append(f)
        fli_coverage.add((f["key"][1], f["period"]))

    rows = []
    for (combo, per), b in sorted(buckets.items(),
                                  key=lambda kv: (kv[0][0], period_sort_key(kv[0][1]))):
        ld_lines_b = sorted(b["ld_lines"], key=_ld_sort_key)
        ld_total = sum(l["amount"] for l in ld_lines_b)
        row = {
            "combo": combo_text(combo),
            "segments": dict(zip(SEGMENTS, combo)),
            "period": per,
            "ld_total": ld_total,
            "ld_lines": ld_lines_b,
            "ld_people": len({l["person"] for l in ld_lines_b}),
            "gl_total": b["gl_total"],
            "diff": None,
            "status": "unchecked",
            "fli": None,
        }
        if gl_lines is not None and (combo[:3], per) not in gl_scope:
            row["status"] = "gl_not_run"
        elif gl_lines is not None:
            gl_total = b["gl_total"] or 0
            row["diff"] = gl_total - ld_total
            if row["diff"] == 0:
                row["status"] = "match"
            elif not ld_lines_b:
                row["status"] = "gl_only"
            elif b["gl_total"] is None:
                row["status"] = "not_in_gl"
            else:
                row["status"] = "mismatch"
        if fli_lines is not None:
            row["fli"] = _fli_detail(combo, per, ld_lines_b, row["gl_total"],
                                     fli_index, fli_coverage, claimed)
        rows.append(row)

    if fli_lines is not None:
        _posted_elsewhere(rows, fli_lines, claimed)
        for row in rows:
            d = row["fli"]
            if not d.get("covered") or row["diff"] is None:
                continue
            # What's left of the difference once lines posted to (or from)
            # another account or period are counted.
            d["unexplained"] = (row["diff"]
                                + sum(m["ld"]["amount"] for m in d["moved_out"])
                                - sum(m["gl"]["amount"] for m in d["moved_in"]))
            if (row["status"] in ("mismatch", "not_in_gl", "gl_only")
                    and (d["moved_out"] or d["moved_in"]) and d["unexplained"] == 0):
                row["status"] = "posted_elsewhere"

    # People the ledger paid from these accounts whom the Labor Distribution
    # file doesn't mention at all: it was run for fewer people (or funds).
    ld_names = {l["person"].lower() for l in ld_ok}
    missing_people = set()
    for row in rows:
        d = row["fli"]
        if d and d.get("covered"):
            d["people_not_in_ld"] = sorted({f["person"] for f in d["gl_only"]
                                            if f["person"] and f["person"].lower() not in ld_names})
            missing_people.update(d["people_not_in_ld"])

    people, unattributed = by_person(ld_ok, fli_lines, compared_set, salary_account)

    compared_rows = [r for r in rows if r["status"] != "gl_not_run"]
    totals = {
        "ld": sum(r["ld_total"] for r in compared_rows),
        "gl": sum(r["gl_total"] or 0 for r in compared_rows) if gl_lines is not None else None,
    }
    counts = {}
    for r in rows:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return {
        "loaded": {LD: True, GL: gl_lines is not None, FLI: fli_lines is not None},
        "periods": {"ld": ld_periods, "gl": gl_periods, "compared": compared},
        "rows": rows,
        "totals": totals,
        "counts": counts,
        "people_not_in_ld": len(missing_people),
        "people": people,
        "people_counts": dict(Counter(p["status"] for p in people)),
        "unattributed": unattributed,
        "outside_periods": sorted(outside, key=lambda l: (l["combo"], _ld_sort_key(l))),
        "excluded": sorted(excluded, key=lambda l: (l["combo"], _ld_sort_key(l))),
    }


def _posted_elsewhere(rows, fli_lines, claimed):
    """Find the Labor Distribution lines missing from their own account in
    the ledger that were posted somewhere else — accounting re-maps some
    pay elements (longevity pay charged to 512100 posts to 512400 Faculty
    Longevity Pay), and a line can land in another period. Moves each such
    pair out of the "missing" lists: on the LD side it's recorded as
    moved_out, on the ledger side (if that combination is a row here) as
    moved_in."""
    by_ref = {}
    for f in fli_lines:
        if f["ref"] and id(f) not in claimed:
            by_ref.setdefault(f["ref"], []).append(f)
    owner = {}  # id(ledger line) -> the row listing it as ledger-only
    for row in rows:
        d = row["fli"]
        if d.get("covered"):
            for f in d["gl_only"]:
                owner.setdefault(id(f), row)
    for row in rows:
        d = row["fli"]
        if not d.get("covered"):
            continue
        for ld in list(d["ld_only"]):
            f = next((f for f in by_ref.get(ld["txn"], [])
                      if f["amount"] == ld["amount"] and id(f) not in claimed), None)
            if f is None:
                continue
            claimed.add(id(f))
            d["ld_only"].remove(ld)
            d["moved_out"].append({"ld": ld, "gl": f, "to": combo_text(f["key"]),
                                   "to_period": f["period"]})
            target = owner.get(id(f))
            if target is not None:
                target["fli"]["gl_only"].remove(f)
                target["fli"]["moved_in"].append({"ld": ld, "gl": f, "from": row["combo"],
                                                  "from_period": row["period"]})


def _name_key(name):
    return re.sub(r"\s+", " ", name).strip().lower()


def by_person(ld_lines, fli_lines, compared_set, salary_account):
    """Each person's Labor Distribution against what the ledger posted for
    them, per account combination and period.

    The ledger's side comes from Fund Line Items: a line belongs to the
    person whose Labor Distribution transaction its Ref Doc names, or else
    to the person its "Accounting for <name>" text names. Ledger lines on
    salary accounts that are neither (journals, transfers, purchase orders)
    are returned separately, per account, as `unattributed`.

    fli_lines may be None (not loaded): each person then has their Labor
    Distribution only, status "unchecked"."""
    ld_lines = [l for l in ld_lines if l["period"] in compared_set]
    by_txn = {l["txn"]: l for l in ld_lines if l["txn"]}
    people = {}

    def person(name, number=""):
        p = people.setdefault(_name_key(name), {"name": name, "number": "",
                                                "ld": [], "gl": []})
        p["number"] = p["number"] or number
        return p

    for l in ld_lines:
        person(l["person"], l["person_number"])["ld"].append(l)

    unattributed, coverage, names = {}, set(), {}
    for f in fli_lines or []:
        if f["period"] not in compared_set:
            continue
        coverage.add((f["key"][1], f["period"]))
        names.setdefault(f["key"][3], f["account_name"])
        ld = by_txn.get(f["ref"])
        if ld is not None:
            person(ld["person"])["gl"].append(f)
        elif f["person"]:
            person(f["person"])["gl"].append(f)
        elif salary_account(f["key"][3]):
            unattributed.setdefault((f["key"], f["period"]), []).append(f)

    checked = fli_lines is not None
    out = []
    for p in sorted(people.values(), key=lambda p: _name_key(p["name"])):
        cells = _person_cells(p, coverage, names, checked)
        periods = sorted({c["period"] for c in cells}, key=period_sort_key)
        out.append(dict(
            _person_summary(cells, checked),
            name=p["name"], number=p["number"], cells=cells,
            # the same, period by period (for the page's period filter)
            periods={per: _person_summary([c for c in cells if c["period"] == per], checked)
                     for per in periods}))
    unattributed_out = [{
        "combo": combo_text(key), "account": key[3], "account_name": names.get(key[3], ""),
        "period": per, "total": sum(f["amount"] for f in lines),
        "lines": sorted(lines, key=lambda f: (f["posted"], f["doc"], f["line"])),
    } for (key, per), lines in sorted(unattributed.items(),
                                      key=lambda kv: (kv[0][0], period_sort_key(kv[0][1])))]
    return out, unattributed_out


def _person_summary(cells, checked):
    """Totals and status over some of a person's account cells (all of
    them, or one period's)."""
    compared = [c for c in cells if c["status"] != "not_covered"]
    ld_total = sum(c["ld_total"] for c in compared)
    gl_total = sum(c["gl_total"] for c in compared)
    if not checked:
        status = "unchecked"
    elif not compared:
        status = "not_covered"
    elif not any(c["ld_lines"] for c in cells):
        status = "not_in_ld"
    elif all(c["status"] == "match" for c in compared):
        status = "match"
    elif all(c["status"] in ("match", "explained") for c in compared):
        # every difference is a line posted to another account or period;
        # over all of a person's accounts those net to zero, but one
        # period's may not (a line posted in the next period)
        status = "explained"
    elif gl_total == 0 and not any(c["gl_lines"] for c in compared):
        status = "not_in_ledger"
    else:
        status = "mismatch"
    return {"status": status, "ld_total": ld_total,
            "gl_total": gl_total if checked else None,
            "diff": gl_total - ld_total if checked else None}


def _person_cells(p, coverage, names, checked):
    """One person's lines per (account combination, period), each line
    paired with its other side where there is one."""
    cells = {}

    def cell(key, per, combo=None):
        c = cells.setdefault((key, per), {"key": key, "period": per, "combo": None,
                                          "ld_lines": [], "gl_lines": [], "lines": []})
        c["combo"] = c["combo"] or combo
        return c

    for l in p["ld"]:
        cell(l["combo"][:FLI_SEGMENTS], l["period"], l["combo"])["ld_lines"].append(l)
    for f in p["gl"]:
        cell(f["key"], f["period"])["gl_lines"].append(f)

    # Pair lines by transaction number; a pair split across cells is a
    # line accounting posted to another account (or period).
    gl_by_ref = {}
    for f in p["gl"]:
        gl_by_ref.setdefault(f["ref"], []).append(f)
    paired = set()
    for l in sorted(p["ld"], key=_ld_sort_key):
        here = cells[(l["combo"][:FLI_SEGMENTS], l["period"])]
        candidates = [f for f in gl_by_ref.get(l["txn"], []) if id(f) not in paired]
        same = [f for f in candidates if (f["key"], f["period"]) == (here["key"], here["period"])]
        f = next((f for f in same if f["amount"] == l["amount"]), None) or \
            next(iter(same), None) or next(iter(candidates), None)
        if f is None:
            here["lines"].append({"kind": "ld_only", "ld": l, "gl": None})
            continue
        paired.add(id(f))
        if any(f is x for x in same):
            here["lines"].append({"kind": "pair" if f["amount"] == l["amount"] else "amount_differs",
                                  "ld": l, "gl": f})
        else:
            there = cells[(f["key"], f["period"])]
            here["lines"].append({"kind": "moved_out", "ld": l, "gl": f,
                                  "other": combo_text(f["key"]), "other_period": f["period"],
                                  "other_name": f["account_name"]})
            there["lines"].append({"kind": "moved_in", "ld": l, "gl": f,
                                   "other": combo_text(l["combo"]), "other_period": l["period"]})
    for f in p["gl"]:
        if id(f) not in paired:
            cell(f["key"], f["period"])["lines"].append({"kind": "gl_only", "ld": None, "gl": f})

    out = []
    for (key, per), c in sorted(cells.items(), key=lambda kv: (period_sort_key(kv[0][1]), kv[0][0])):
        ld_total = sum(l["amount"] for l in c["ld_lines"])
        gl_total = sum(f["amount"] for f in c["gl_lines"])
        moved = (sum(x["gl"]["amount"] for x in c["lines"] if x["kind"] == "moved_in")
                 - sum(x["ld"]["amount"] for x in c["lines"] if x["kind"] == "moved_out"))
        if not checked:
            status = "unchecked"
        elif not c["gl_lines"] and (key[1], per) not in coverage:
            status = "not_covered"  # Fund Line Items wasn't run for this fund
        elif gl_total == ld_total:
            status = "match"
        elif moved and gl_total - ld_total == moved:
            status = "explained"
        else:
            status = "mismatch"
        if status in ("unchecked", "not_covered"):
            # nothing to compare against: not evidence the line is missing
            for x in c["lines"]:
                if x["kind"] == "ld_only":
                    x["kind"] = "not_compared"
        c["lines"].sort(key=lambda x: (x["ld"] or {}).get("pay_element", "") or (x["gl"] or {}).get("text", ""))
        out.append({
            "combo": combo_text(c["combo"] or key), "account": key[3],
            "account_name": names.get(key[3], ""), "period": per,
            "ld_total": ld_total, "gl_total": gl_total, "diff": gl_total - ld_total,
            "status": status, "lines": c["lines"],
            "ld_lines": len(c["ld_lines"]), "gl_lines": len(c["gl_lines"]),
        })
    return out


def _fli_detail(combo, per, ld_lines_b, gl_total, fli_index, fli_coverage, claimed):
    key = combo[:FLI_SEGMENTS]
    fli = fli_index.get((key, per), [])
    if not fli and (combo[1], per) not in fli_coverage:
        return {"covered": False}
    matched, amount_differs, gl_only, ld_only = _match_fli(ld_lines_b, fli)
    claimed.update(id(m["gl"]) for m in matched + amount_differs)
    fli_total = sum(f["amount"] for f in fli)
    # entries that aren't anyone's pay (journals, transfers) first, then
    # payroll lines by person, like the Labor Distribution side
    gl_only.sort(key=lambda f: (f["person"] != "", f["person"].lower(),
                                f["posted"], f["doc"], f["line"]))
    ld_only.sort(key=_ld_sort_key)
    return {
        "covered": True,
        "total": fli_total,
        # Fund Line Items should add up to the ledger's period activity;
        # if not, it was run for a different range and the lists below
        # are incomplete.
        "agrees_with_gl": gl_total is None or fli_total == gl_total,
        "matched": len(matched),
        "amount_differs": amount_differs,
        "gl_only": gl_only,
        "ld_only": ld_only,
        "moved_out": [],   # filled in by _posted_elsewhere()
        "moved_in": [],
        "unexplained": None,
    }


# ---------------------------------------------------------------------------
# loading a set of files
# ---------------------------------------------------------------------------

def load(files):
    """files: [(name, bytes)]. Returns ({kind: (name, parsed)}, notes),
    where notes explain each file that wasn't used."""
    found, notes = {}, []
    for name, data in files:
        try:
            kind, at, rows = identify(data)
        except Exception as exc:
            notes.append(f"{name}: could not be read ({exc}).")
            continue
        if kind is None:
            notes.append(f"{name}: not one of the three reports — its header "
                         f"row doesn't match Labor Distribution, DetailBalances "
                         f"or Fund Line Items.")
            continue
        if kind == LD:
            parsed = parse_ld(rows, at)
        elif kind == GL:
            parsed, missing = parse_gl(rows, at)
            if missing:
                notes.append(f"{name}: no {', '.join(missing)} column — "
                             f"treated as blank.")
        else:
            parsed = parse_fli(rows, at)
        if kind in found:
            notes.append(f"{found[kind][0]}: replaced by {name} "
                         f"(both are {KIND_NAMES[kind]} reports).")
        found[kind] = (name, parsed)
    return found, notes


# What each report's export is called, squeezed to letters and digits:
# "Labor_Distribution_Report (2).csv", "DetailBalances_3.xlsx",
# "Fund Line Items - 3 Segments Fund Line.xlsx".
NAME_PATTERNS = {LD: "labordistribution", GL: "detailbalance", FLI: "fundlineitem"}
REPORT_SUFFIXES = (".csv", ".txt", ".xlsx", ".xls")


def kind_by_name(filename):
    squeezed = re.sub(r"[^a-z0-9]", "", filename.lower())
    return next((k for k, p in NAME_PATTERNS.items() if p in squeezed), None)


def find_reports(folder):
    """Pick the newest file of each report in a folder, by filename.

    Returns ([paths], notes). Excel's lock files (~$...) and other file
    types are skipped; a name that matches no report is mentioned, so a
    renamed export doesn't go missing silently."""
    folder = Path(folder)
    candidates, notes = {}, []
    if not folder.is_dir():
        return [], notes
    for p in sorted(folder.iterdir()):
        if not p.is_file() or p.name.startswith(("~$", ".")):
            continue
        kind = kind_by_name(p.name)
        if p.suffix.lower() not in REPORT_SUFFIXES:
            if kind is not None:
                notes.append(f"{p.name}: looks like the {KIND_NAMES[kind]} report, "
                             f"but {p.suffix or 'a file without an extension'} isn't "
                             f"a format this reads — export it as Excel or CSV.")
            continue
        if kind is None:
            notes.append(f"{p.name}: the name doesn't say which report it is "
                         f"(expected Labor Distribution, DetailBalances or "
                         f"Fund Line Items in it) — skipped.")
            continue
        candidates.setdefault(kind, []).append(p)
    newest = {}
    for kind, paths in candidates.items():
        newest[kind] = max(paths, key=lambda p: p.stat().st_mtime)
        older = [p.name for p in paths if p != newest[kind]]
        if older:
            notes.append(f"Using {newest[kind].name}, the newest {KIND_NAMES[kind]} "
                         f"file; ignoring {', '.join(older)}.")
    return [newest[k] for k in (LD, GL, FLI) if k in newest], notes


def load_folder(folder):
    """Like load(), for the reports find_reports() picks in a folder. A file
    whose contents turn out to be a different report than its name says is
    used as what its contents say, with a note."""
    paths, notes = find_reports(folder)
    found, load_notes = load([(p.name, p.read_bytes()) for p in paths])
    for kind, (name, _) in found.items():
        named = kind_by_name(name)
        if named != kind:
            notes.append(f"{name}: named like {KIND_NAMES[named]}, but its columns "
                         f"are a {KIND_NAMES[kind]} report — read as {KIND_NAMES[kind]}.")
    return found, notes + load_notes


def reconcile_files(found):
    if LD not in found:
        return None
    return reconcile(found[LD][1],
                     found[GL][1] if GL in found else None,
                     found[FLI][1] if FLI in found else None)


# ---------------------------------------------------------------------------
# results workbook
# ---------------------------------------------------------------------------

STATUS_TEXT = {
    "match": "Matches",
    "mismatch": "Does not match",
    "not_in_gl": "Not in DetailBalances",
    "gl_not_run": "Not compared: fund not in DetailBalances",
    "gl_only": "In ledger, no Labor Distribution",
    "posted_elsewhere": "Explained: posted to another account/period",
    "unchecked": "Not compared (no DetailBalances)",
}


PERSON_STATUS_TEXT = {
    "match": "Matches",
    "explained": "Explained: posted to another account/period",
    "mismatch": "Does not match",
    "not_in_ld": "Not in Labor Distribution",
    "not_in_ledger": "Not in the ledger",
    "not_covered": "Not compared: fund not in Fund Line Items",
    "unchecked": "Not compared (no Fund Line Items)",
}


def _amt(c, style=xlsx.MONEY):
    return (round(c / 100, 2), style) if c is not None else ""


def export_workbook(result, sources=None):
    summary = [["Account combination", "Fund", "Department", "Account", "Program",
                "Activity", "Period", "Labor Distribution", "DetailBalances",
                "Difference", "Status", "People", "Fund Line Items: in ledger only",
                "Fund Line Items: in LD only"]]
    for r in result["rows"]:
        s = r["segments"]
        fli = r["fli"] or {}
        summary.append([
            r["combo"], s["fund"], s["department"], s["account"], s["program"],
            s["activity"], r["period"], _amt(r["ld_total"]), _amt(r["gl_total"]),
            _amt(r["diff"]), STATUS_TEXT[r["status"]], r["ld_people"],
            len(fli.get("gl_only", [])) if fli.get("covered") else "",
            len(fli.get("ld_only", [])) if fli.get("covered") else "",
        ])
    summary.append([("Total", xlsx.BOLD), "", "", "", "", "", "",
                    _amt(result["totals"]["ld"], xlsx.MONEY_BOLD),
                    _amt(result["totals"]["gl"], xlsx.MONEY_BOLD),
                    _amt(None if result["totals"]["gl"] is None
                         else result["totals"]["gl"] - result["totals"]["ld"],
                         xlsx.MONEY_BOLD)])

    ld_cols = ["Account combination", "Period", "Person name", "Person number",
               "Assignment", "Pay element", "Payroll start", "Payroll end",
               "Amount", "Line %", "Transaction number", "Status"]
    ld_sheet = [ld_cols]
    for r in result["rows"]:
        if not r["ld_lines"]:
            continue
        for l in r["ld_lines"]:
            ld_sheet.append([r["combo"], r["period"], l["person"], l["person_number"],
                             l["assignment"], l["pay_element"], l["pay_start"],
                             l["pay_end"], _amt(l["amount"]), l["percent"], l["txn"],
                             l["status"]])
        ld_sheet.append([(f"Subtotal {r['combo']}", xlsx.BOLD), r["period"], "", "",
                         "", "", "", "", _amt(r["ld_total"], xlsx.MONEY_BOLD)])

    diff_sheet = [["Account combination", "Period", "Where", "Date",
                   "Person / description", "Amount", "Reference", "Document",
                   "Document type", "Entered by"]]
    for r in result["rows"]:
        fli = r["fli"]
        if not fli or not fli.get("covered"):
            continue
        for f in fli["gl_only"]:
            diff_sheet.append([r["combo"], r["period"], "In ledger, not in Labor Distribution",
                               f["posted"],
                               f"{f['person']} — {f['assignment']}" if f["person"]
                               else f["text"] or f["header_text"], _amt(f["amount"]),
                               f["ref"], f["doc"], f["doc_type"], f["user"]])
        for l in fli["ld_only"]:
            diff_sheet.append([r["combo"], r["period"], "In Labor Distribution, not in ledger",
                               l["pay_start"], f"{l['person']} — {l['pay_element']}",
                               _amt(l["amount"]), l["txn"], "", "", ""])
        for m in fli["amount_differs"]:
            diff_sheet.append([r["combo"], r["period"], "Amount differs (ledger side)",
                               m["gl"]["posted"], m["gl"]["text"], _amt(m["gl"]["amount"]),
                               m["gl"]["ref"], m["gl"]["doc"], m["gl"]["doc_type"],
                               m["gl"]["user"]])
            diff_sheet.append([r["combo"], r["period"], "Amount differs (LD side)",
                               m["ld"]["pay_start"],
                               f"{m['ld']['person']} — {m['ld']['pay_element']}",
                               _amt(m["ld"]["amount"]), m["ld"]["txn"], "", "", ""])
        for m in fli["moved_out"]:
            g = m["gl"]
            diff_sheet.append([r["combo"], r["period"],
                               f"Posted to {g['key'][3]} {g['account_name']} in {m['to_period']}"
                               f" ({m['to']})",
                               g["posted"], f"{m['ld']['person']} — {m['ld']['pay_element']}",
                               _amt(m["ld"]["amount"]), m["ld"]["txn"], g["doc"],
                               g["doc_type"], g["user"]])
        for m in fli["moved_in"]:
            g = m["gl"]
            diff_sheet.append([r["combo"], r["period"],
                               f"Charged in Labor Distribution to {m['from']} ({m['from_period']})",
                               g["posted"], f"{m['ld']['person']} — {m['ld']['pay_element']}",
                               _amt(g["amount"]), g["ref"], g["doc"], g["doc_type"], g["user"]])

    person_sheet = [["Person", "Person number", "Account combination", "Account", "Period",
                     "Line", "Transaction / reference", "Labor Distribution", "Ledger",
                     "Difference", "Status / note"]]
    checked = result["loaded"][FLI]
    for p in result["people"]:
        person_sheet.append([(p["name"], xlsx.BOLD), p["number"], "", "", "", "", "",
                             _amt(p["ld_total"], xlsx.MONEY_BOLD),
                             _amt(p["gl_total"], xlsx.MONEY_BOLD) if checked else "",
                             _amt(p["diff"], xlsx.MONEY_BOLD) if checked else "",
                             (PERSON_STATUS_TEXT[p["status"]], xlsx.BOLD)])
        for c in p["cells"]:
            compared = checked and c["status"] != "not_covered"
            person_sheet.append([p["name"], p["number"], c["combo"], c["account_name"], c["period"],
                                 "", "", _amt(c["ld_total"]),
                                 _amt(c["gl_total"]) if compared else "",
                                 _amt(c["diff"]) if compared else "",
                                 PERSON_STATUS_TEXT[c["status"]]])
            # the lines that don't simply pair up
            for x in c["lines"]:
                if x["kind"] in ("pair", "not_compared"):
                    continue
                ld, gl = x["ld"], x["gl"]
                note = {"amount_differs": "Same transaction, different amount",
                        "ld_only": "Not in the ledger",
                        "gl_only": "No Labor Distribution line",
                        "moved_out": f"Posted to {x.get('other', '')} {x.get('other_name', '')}"
                                     f" ({x.get('other_period', '')})",
                        "moved_in": f"Charged in Labor Distribution to {x.get('other', '')}"
                                    f" ({x.get('other_period', '')})"}[x["kind"]]
                person_sheet.append([p["name"], p["number"], c["combo"], c["account_name"],
                                     c["period"],
                                     ld["pay_element"] if ld else (gl["assignment"] or gl["text"]),
                                     ld["txn"] if ld else gl["ref"],
                                     _amt(ld["amount"]) if ld and x["kind"] != "moved_in" else "",
                                     _amt(gl["amount"]) if gl and x["kind"] != "moved_out" else "",
                                     "", note])
    for g in result["unattributed"]:
        for f in g["lines"]:
            person_sheet.append(["(not anyone's payroll line)", "", g["combo"], g["account_name"],
                                 g["period"], f["text"] or f["header_text"], f["ref"], "",
                                 _amt(f["amount"]), "", f"{f['doc_type']}, entered by {f['user']}"
                                 if f["user"] else f["doc_type"]])

    sheets = [
        {"name": "By person", "rows": person_sheet,
         "widths": [26, 13, 40, 26, 8, 40, 20, 18, 14, 14, 44]},
        {"name": "Summary", "rows": summary,
         "widths": [40, 10, 11, 10, 9, 9, 8, 18, 16, 14, 30, 8, 14, 14]},
        {"name": "Labor Distribution sorted", "rows": ld_sheet,
         "widths": [40, 8, 26, 13, 28, 40, 13, 13, 14, 8, 18, 10]},
    ]
    if result["loaded"][FLI]:
        sheets.append({"name": "Differences", "rows": diff_sheet,
                       "widths": [40, 8, 36, 12, 50, 14, 22, 12, 18, 18]})
    if result["outside_periods"] or result["excluded"]:
        other = [["Why not compared"] + ld_cols]
        for why, lines in (("Period not in DetailBalances", result["outside_periods"]),
                           ("Status is not Success", result["excluded"])):
            for l in lines:
                other.append([why, combo_text(l["combo"]), l["period"], l["person"],
                              l["person_number"], l["assignment"], l["pay_element"],
                              l["pay_start"], l["pay_end"], _amt(l["amount"]),
                              l["percent"], l["txn"], l["status"]])
        sheets.append({"name": "Not compared", "rows": other,
                       "widths": [28, 40, 8, 26, 13, 28, 40, 13, 13, 14, 8, 18, 10]})
    if sources:
        sheets.append({"name": "Source files",
                       "rows": [["Report", "File"]] + [[KIND_NAMES[k], n]
                                                       for k, n in sources.items()],
                       "widths": [22, 70]})
    return xlsx.write_workbook(sheets)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("files", nargs="*", type=Path,
                    help="the exports, in any order (CSV or .xlsx); "
                         "default: the newest of each in the data folder")
    ap.add_argument("-o", "--output", type=Path, default=Path("Salary reconciliation.xlsx"))
    args = ap.parse_args()
    if args.files:
        found, notes = load([(p.name, p.read_bytes()) for p in args.files])
    else:
        found, notes = load_folder(DATA_DIR)
    for n in notes:
        print(n)
    result = reconcile_files(found)
    if result is None:
        raise SystemExit("No Labor Distribution report among the files.")
    for r in result["rows"]:
        gl = money(r["gl_total"]) if r["gl_total"] is not None else "-"
        diff = money(r["diff"]) if r["diff"] is not None else ""
        print(f"{r['combo']}  {r['period']}  LD {money(r['ld_total']):>14}  "
              f"GL {gl:>14}  {diff:>12}  {STATUS_TEXT[r['status']]}")
    args.output.write_bytes(export_workbook(
        result, {k: v[0] for k, v in found.items()}))
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
