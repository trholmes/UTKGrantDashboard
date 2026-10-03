#!/usr/bin/env python3
"""Describe a set of real reports without revealing what's in them —
temporary, to settle how the reports behave before the tool's guesses
become rules.

    python diagnose.py LD.csv DetailBalances.xlsx FundLineItems.xlsx

(or drag the three files onto "Diagnose.bat"). Writes diagnostic.txt next
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


def sign(c):
    return "positive" if c > 0 else "negative" if c < 0 else "zero"


def main(paths):
    raw = {}
    for p in paths:
        data = Path(p).read_bytes()
        kind, at, rows = rc.identify(data)
        say(f"FILE {Path(p).suffix or '(no extension)'}: "
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
                f"{'yes' if acct[:2] in families else 'no ':<15}  "
                f"{'yes' if acct in ld_accounts else 'no ':<13}  {names.get(acct, '')}")
        other = sum(1 for g in gl if not g["combo"][3].startswith("5") and g["activity"] != 0)
        say(f"  other (non-5xxxxx) rows with activity: {other}")
        say()

    # -- debit/credit conventions in Fund Line Items ----------------------
    if fli is not None:
        say("== FUND LINE ITEMS: debit/credit columns vs. the sign of Amount")
        combos = Counter()
        raw_amount = {}
        for r in rc._records(*raw[rc.FLI]):
            if not str(r.get("GL Account") or "").strip() or not str(r.get("Fund") or "").strip():
                continue
            a = rc.cents(r.get("Amount"))
            combos[(rc._clean(r.get("D/C")) or "-", rc._clean(r.get("Debit/Credit Indicator")) or "-",
                    sign(a))] += 1
        say("  (D/C, Debit/Credit Indicator, sign of Amount): lines")
        table(Counter({f"{k[0]:>3} | {k[1]:<8} | {k[2]}": v for k, v in combos.items()}), "kinds")

        if gl is not None:
            # Which sign convention makes Fund Line Items add up to the ledger?
            gl6 = defaultdict(int)
            for g in gl:
                if g["combo"][3][:2] in families:
                    gl6[(g["combo"][:rc.FLI_SEGMENTS], g["period"])] += g["activity"]
            as_is, flipped = defaultdict(int), defaultdict(int)
            for r, f in zip((r for r in rc._records(*raw[rc.FLI])
                             if str(r.get("GL Account") or "").strip()
                             and str(r.get("Fund") or "").strip()), fli):
                a = rc.cents(r.get("Amount"))
                as_is[(f["key"], f["period"])] += a
                flipped[(f["key"], f["period"])] += f["amount"]
            covered = [k for k in gl6 if k in as_is]
            say(f"Salary-family ledger combinations that Fund Line Items also covers: {len(covered)}")
            say(f"  Fund Line Items adds up to DetailBalances, Amount taken as is:       "
                f"{sum(1 for k in covered if as_is[k] == gl6[k])}")
            say(f"  Fund Line Items adds up to DetailBalances, credits made negative:    "
                f"{sum(1 for k in covered if flipped[k] == gl6[k])}")
        say()

    # -- the reconciliation itself ---------------------------------------
    result = rc.reconcile(ld, gl, fli)
    say("== RESULT")
    say(f"Periods compared: {result['periods']['compared']}")
    say("Combinations by status:")
    table(Counter(rc.STATUS_TEXT[r["status"]] for r in result["rows"]), "statuses")
    say(f"LD lines outside compared periods: {len(result['outside_periods'])}; "
        f"not Success: {len(result['excluded'])}")
    if fli is not None:
        det = [r["fli"] for r in result["rows"] if r["fli"] and r["fli"]["covered"]]
        uncovered = sum(1 for r in result["rows"] if r["fli"] and not r["fli"]["covered"])
        say(f"Combinations Fund Line Items covers: {len(det)}; doesn't cover: {uncovered}")
        say(f"  ...where its lines add up to DetailBalances: "
            f"{sum(1 for d in det if d['agrees_with_gl'])} of {len(det)}")
        say(f"Line pairs: by reference {sum(d['matched'] - len(d['matched_by_amount']) for d in det)}, "
            f"by amount {sum(len(d['matched_by_amount']) for d in det)}, "
            f"same reference/different amount {sum(len(d['amount_differs']) for d in det)}")
        say("Ledger lines with no LD line, by Document Type / Document Header Text "
            "(period prefix removed):")
        table(Counter(f["doc_type"] + " / " + PERIOD_PREFIX.sub("", f["header_text"])
                      for d in det for f in d["gl_only"]), "kinds")
        say("LD lines missing from the ledger, by pay element:")
        table(Counter(l["pay_element"] for d in det for l in d["ld_only"]), "pay elements")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
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
