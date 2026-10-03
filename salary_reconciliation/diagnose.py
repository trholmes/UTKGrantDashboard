#!/usr/bin/env python3
"""Describe a set of real reports without revealing what's in them —
temporary, to settle how the reports behave before the tool's guesses
become rules.

    python diagnose.py LD.csv DetailBalances.xlsx FundLineItems.xlsx
    python diagnose.py            # the newest reports in data/

(or double-click "Diagnose.bat" to use the data folder, or drag the three
files onto it). Writes diagnostic.txt next
to this script and prints the same text.

What it prints: column headings, fiscal periods, GL account codes and
names, pay element names, and counts. What it never prints: person names
or numbers, dollar amounts, transaction or document numbers, funds,
departments, or line descriptions. Read diagnostic.txt before sending it.
"""

import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import reconcile as rc  # noqa: E402

OUT = []
PERIOD_PREFIX = re.compile(r"^\d{2}-\d{2}\s*")  # '27-03 Salary Transfer'
DOC_NUMBER = re.compile(r"\s*\d{6,}$")  # 'Purchase Order 300000000333260'


def say(text=""):
    OUT.append(text)


def table(counter, label, limit=60):
    if not counter:
        say(f"  (no {label})")
        return
    for key, n in sorted(counter.items(), key=lambda kv: (-kv[1], str(kv[0])))[:limit]:
        say(f"  {n:>7}  {key}")
    if len(counter) > limit:
        say(f"  ... and {len(counter) - limit} more {label}")


def main(paths):
    if not paths:
        paths, notes = rc.find_reports(rc.DATA_DIR)
        say(f"From the data folder: {len(paths)} report(s)")
        for n in notes:
            say("  " + n)
        say()
    raw = {}
    for p in paths:
        data = Path(p).read_bytes()
        kind, at, rows = rc.identify(data)
        fmt = ("Excel .xlsx" if rc.xlsx.is_xlsx(data)
               else {"biff": "Excel 97-2003", "xml2003": "Excel 2003 XML",
                     "html": "HTML table"}.get(rc.xls.kind(data), "text (CSV/TSV)"))
        say(f"FILE {Path(p).suffix or '(no extension)'} ({fmt} inside): "
            f"{rc.KIND_NAMES.get(kind, 'NOT RECOGNIZED')}, header on row {at}, "
            f"{len(rows)} rows in all")
        if kind:
            say("  columns: " + " | ".join(rc._clean(h) for h in rows[at]))
            raw[kind] = (rows, at)
    say()
    if rc.LD not in raw:
        say("No Labor Distribution report among the files — nothing more to say.")
        return

    ld = rc.parse_ld(*raw[rc.LD])
    gl, missing = rc.parse_gl(*raw[rc.GL]) if rc.GL in raw else (None, [])
    fli = rc.parse_fli(*raw[rc.FLI]) if rc.FLI in raw else None
    ld_records = list(rc._records(*raw[rc.LD]))

    # -- periods ---------------------------------------------------------
    say("== PERIODS")
    say("Labor Distribution 'Fiscal Period - LD' (lines):")
    table(Counter(l["period"] for l in ld), "periods")
    differ = sum(1 for r in ld_records
                 if rc.period(r.get("Fiscal Period - LD")) != rc.period(r.get("Fiscal Period - Payroll")))
    say(f"  lines whose Payroll period differs from LD period: {differ} of {len(ld_records)}")
    unparsed = sum(1 for r in ld_records if rc.combo_parts(r.get("LD Account Combination")) is None)
    say(f"  rows skipped (no 8-part account combination): {unparsed}")
    if gl is not None:
        say("DetailBalances 'Accounting Period' (rows):")
        table(Counter(g["period"] for g in gl), "periods")
        if missing:
            say(f"  missing segment columns: {missing}")
    if fli is not None:
        say("Fund Line Items Year-Period (lines):")
        table(Counter(f["period"] for f in fli), "periods")
    say()

    # -- what Labor Distribution charges ---------------------------------
    say("== LABOR DISTRIBUTION")
    say("GL accounts charged (lines):")
    table(Counter(l["combo"][3] for l in ld), "accounts")
    say("Status values (lines):")
    table(Counter(l["status"] or "(blank)" for l in ld), "statuses")
    say("Pay element names (lines):")
    table(Counter(l["pay_element"] for l in ld), "pay elements", limit=80)
    say(f"Negative Distribution Amounts: {sum(1 for l in ld if l['amount'] < 0)} of {len(ld)}")
    say()

    # -- account names, from Fund Line Items ------------------------------
    names = {}
    if fli is not None:
        for r in rc._records(*raw[rc.FLI]):
            acct = rc.segment(r.get("GL Account"), 6)
            if acct and acct not in names:
                names[acct] = rc._clean(r.get("G/L Account Text"))

    ld_accounts = {l["combo"][3] for l in ld}
    families = {a[:2] for a in ld_accounts}

    # -- what the ledger holds -------------------------------------------
    if gl is not None:
        say("== DETAILBALANCES: expense accounts (5xxxxx) with period activity")
        say("  rows  combos  account  family-included  charged-by-LD  name")
        by_acct = defaultdict(lambda: [0, set()])
        for g in gl:
            if g["combo"][3].startswith("5") and g["activity"] != 0:
                by_acct[g["combo"][3]][0] += 1
                by_acct[g["combo"][3]][1].add(g["combo"])
        for acct in sorted(by_acct):
            n, combos = by_acct[acct]
            say(f"  {n:>4}  {len(combos):>6}  {acct}   "
                f"{'yes' if acct[:2] in families or acct in rc.EXTRA_SALARY_ACCOUNTS else 'no ':<15}  "
                f"{'yes' if acct in ld_accounts else 'no ':<13}  {names.get(acct, '')}")
        other = sum(1 for g in gl if not g["combo"][3].startswith("5") and g["activity"] != 0)
        say(f"  other (non-5xxxxx) rows with activity: {other}")
        say()

    # -- debit/credit conventions in Fund Line Items ----------------------
    if fli is not None:
        say("== FUND LINE ITEMS")
        labor = [f for f in fli if PERIOD_PREFIX.sub("", f["header_text"]) == "Labor Cost"]
        people = {m.group(1) for f in labor
                  for m in [re.match(r"Accounting for (.+?) Assignment name", f["text"])] if m}
        say(f"Labor Cost lines: {len(labor)}, for {len(people)} different people "
            f"(Labor Distribution file: {len({l['person'] for l in ld})} people)")
        say()

    # -- the reconciliation itself ---------------------------------------
    result = rc.reconcile(ld, gl, fli)
    say("== RESULT")
    say(f"Periods compared: {result['periods']['compared']}")
    say("Combinations by period and status:")
    table(Counter(f"{r['period']}  {rc.STATUS_TEXT[r['status']]}" for r in result["rows"]),
          "statuses")
    say(f"LD lines outside compared periods: {len(result['outside_periods'])}; "
        f"not Success: {len(result['excluded'])}")
    if fli is None:
        return
    det = [r["fli"] for r in result["rows"] if r["fli"]["covered"]]
    say(f"Combinations Fund Line Items covers: {len(det)}; doesn't cover: "
        f"{sum(1 for r in result['rows'] if not r['fli']['covered'])}")
    say(f"  ...where its lines add up to DetailBalances: "
        f"{sum(1 for d in det if d['agrees_with_gl'])} of {len(det)}")
    say(f"Line pairs by reference: {sum(d['matched'] for d in det)}; "
        f"same reference, different amount: {sum(len(d['amount_differs']) for d in det)}")
    say("LD lines posted to another account/period (LD account -> ledger account, pay element):")
    table(Counter(f"{m['ld']['combo'][3]} -> {m['gl']['key'][3]} {m['gl']['account_name']}"
                  f"{' (period ' + m['to_period'] + ')' if m['to_period'] != m['ld']['period'] else ''}"
                  f" | {m['ld']['pay_element']}"
                  for d in det for m in d["moved_out"]), "kinds")
    say("Ledger lines with no LD line, by account, Document Type / Document Header Text "
        "[Record Type] (period prefix and document numbers removed):")
    table(Counter(f"{f['key'][3]} {f['account_name']:<30} {f['doc_type']} / "
                  f"{DOC_NUMBER.sub('', PERIOD_PREFIX.sub('', f['header_text']))} [{f['record_type']}]"
                  for d in det for f in d["gl_only"]), "kinds", limit=100)
    say("LD lines missing from the ledger, by pay element:")
    table(Counter(l["pay_element"] for d in det for l in d["ld_only"]), "pay elements")
    say()

    say("People by status:")
    table(Counter(rc.PERSON_STATUS_TEXT[p["status"]] for p in result["people"]), "statuses")
    say("Accounts within people, by status:")
    table(Counter(f"{c['account']} {rc.PERSON_STATUS_TEXT[c['status']]}"
                  for p in result["people"] for c in p["cells"]), "kinds")
    say("Ledger lines not anyone's payroll line, by account / document type:")
    table(Counter(f"{g['account']} {g['account_name']:<30} {f['doc_type']} / "
                  f"{DOC_NUMBER.sub('', PERIOD_PREFIX.sub('', f['header_text']))}"
                  for g in result["unattributed"] for f in g["lines"]), "kinds")
    say()

    say("== EVERY COMBINATION THAT ISN'T A PLAIN MATCH (funds left out)")
    say("  period  account  status | LD lines, ledger-only lines, LD-only lines, "
        "posted out, posted in | after those: explained?")
    gl_scope = {(g["combo"][1], g["combo"][2], g["period"]) for g in gl or []}
    fli_scope = {(f["key"][1], f["period"]) for f in fli}
    for r in result["rows"]:
        if r["status"] == "match":
            continue
        d, seg = r["fli"], r["segments"]
        line = f"  {r['period']}   {seg['account']}  {rc.STATUS_TEXT[r['status']]} | {len(r['ld_lines'])}, "
        if d["covered"]:
            line += (f"{len(d['gl_only'])}, {len(d['ld_only'])}, {len(d['moved_out'])}, "
                     f"{len(d['moved_in'])} | {'yes' if d['unexplained'] == 0 else 'no'}")
        else:
            line += "Fund Line Items has nothing for this fund+period"
        if r["status"] == "not_in_gl":
            line += (f" | DetailBalances has other rows for this fund+department in this period: "
                     f"{'yes' if (seg['fund'], seg['department'], r['period']) in gl_scope else 'no'}"
                     f"; Fund Line Items has this fund+period: "
                     f"{'yes' if (seg['fund'], r['period']) in fli_scope else 'no'}")
        say(line)


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except Exception as exc:  # report it in the file too
        import traceback
        say("ERROR: " + "".join(traceback.format_exception_only(type(exc), exc)).strip())
        say(traceback.format_exc().replace(str(Path.home()), "~"))
    text = "\n".join(OUT) + "\n"
    out = Path(__file__).resolve().parent / "diagnostic.txt"
    out.write_text(text, encoding="utf-8")
    print(text)
    print(f"Written to {out} — read it, then send it back.")
