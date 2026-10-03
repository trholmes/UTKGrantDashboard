#!/usr/bin/env python3
"""Write three fictional reports to demo/ — the same layout as the real
exports — to try the page or the command line without real payroll data:

    python make_demo.py
    python reconcile.py demo/* -o demo-result.xlsx

The story they tell, for fiscal period 27-03:
  * fund 1100001 / 512100 ties out;
  * fund 1100017 / 512100: the ledger is 500.00 higher — a salary
    transfer journal posted with no Labor Distribution line behind it;
  * fund 2200231 / 512300: one Labor Distribution line (1,200.00) never
    reached the ledger;
  * fund 1100001 / 513100: 750.00 in the ledger, no Labor Distribution;
  * 27-02 lines are in Labor Distribution but DetailBalances is run for
    27-03 only, so they are listed as not compared.
"""

import csv
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import xlsx  # noqa: E402

LD_HEADER = ["Person Number", "Person Name", "Business Unit", "Assignment Number",
             "Assignment Status", "Assignment Category", "Assignment Name",
             "Department Name", "Labor Schedule Name", "Version Name",
             "Labor Schedule Type", "Pay Element Name", "Payroll Period Start Date",
             "Payroll Period End Date", "Fiscal Period - Payroll", "Fiscal Period - LD",
             "Creation Date", "Distribution Amount", "Line Percentage",
             "Transaction Number", "Original Transaction Reference", "Project Number",
             "Project Name", "Expenditure Type", "Funding Source Name",
             "Funding Source Type", "Award Purpose", "Award Type",
             "LD Account Combination", "Status"]
GL_HEADER = ["Accounting Period", "Ledger or Ledger Set", "Entity", "Fund", "Department",
             "Account", "Program", "Activity", "InterCo", "Future",
             "Beginning Balance (USD)", "Period Activity (USD)", "Ending Balance (USD)"]
FLI_HEADER = ["Year", "Period", "FI_DocNo", "LnItm", "Pstng Date", "Entry dte", "Time",
              "Amount", "Crcy", "Entity ID", "Entity Description", "Fund", "Fund Name",
              "Department ID", "Department Description", "Program ID",
              "Program Description", "GL Account", "G/L Account Text", "Activity Code",
              "Activity Description", "SPL Doc Line Item Txt", "Document Header Text",
              "D/C", "Header Ref #", "User Name", "Record No.", "CoCd", "Ld", "Crcy",
              "Ref Doc", "Document Type", "Debit/Credit Indicator", "Record Type"]

A = "10-1100001-106015-512100-210-0000-00-0000"
B = "10-1100017-106015-512100-220-0053-00-0000"
C = "10-2200231-100100-512300-210-0000-00-0000"
D = "10-1100001-106015-513100-210-0000-00-0000"

# person, number, assignment, pay element, period, amount, txn, combo
LD_LINES = [
    ("Rivera, Ana", "00100001", "Associate Professor", "UT Monthly 9 Month Salary Earnings Results", "27-03", "4975.82", "30000001", A),
    ("Rivera, Ana", "00100001", "Associate Professor", "UT Monthly 9 Month Salary Earnings Results", "27-03", "4975.83", "30000002", B),
    ("Chen, Wei", "00100002", "Graduate Research Assistant", "UT Monthly GRA Earnings Results", "27-03", "2100.00", "30000003", A),
    ("Okafor, Grace", "00100003", "Research Associate", "UT Monthly Staff Earnings Results", "27-03", "3800.00", "30000004", C),
    ("Okafor, Grace", "00100003", "Research Associate", "Ern E Longevity Pay Earnings Results", "27-03", "1200.00", "30000005", C),
    ("Rivera, Ana", "00100001", "Associate Professor", "UT Monthly 9 Month Salary Earnings Results", "27-02", "4975.82", "29000001", A),
]

GL_ROWS = [
    ("27-03", A, "9951.64", "7075.82"),
    ("27-03", B, "4975.83", "5475.83"),
    ("27-03", C, "0", "3800.00"),
    ("27-03", D, "0", "750.00"),
    ("27-03", "10-1100001-106015-100000-210-0000-00-0000", "0", "-12000.00"),
]

# combo, amount, ref, text, header text, doc type, user, D/C
FLI_LINES = [
    (A, "4975.82", "30000001", "Accounting for Rivera, Ana Assignment name: Associate Professor", "27-03 Labor Cost", "Project Accounting", "", "S"),
    (A, "2100.00", "30000003", "Accounting for Chen, Wei Assignment name: Graduate Research Assistant", "27-03 Labor Cost", "Project Accounting", "", "S"),
    (B, "4975.83", "30000002", "Accounting for Rivera, Ana Assignment name: Associate Professor", "27-03 Labor Cost", "Project Accounting", "", "S"),
    (B, "500.00", "JE-88213", "Salary transfer Rivera Aug summer correction", "27-03 Salary Transfer", "Manual", "JDOE", "S"),
    (C, "3800.00", "30000004", "Accounting for Okafor, Grace Assignment name: Research Associate", "27-03 Labor Cost", "Project Accounting", "", "S"),
    (D, "750.00", "JE-88214", "Hourly wages reclass", "27-03 Reclass", "Manual", "JDOE", "S"),
    ("10-1100001-106015-200010-210-0000-00-0000", "1486.84", "349782", "Payment Created", "27-03 Payments", "Payables", "", "S"),
]


def ld_csv():
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(LD_HEADER)
    for person, num, assignment, element, per, amount, txn, combo in LD_LINES:
        row = dict.fromkeys(LD_HEADER, "~No Value~")
        row.update({
            "Person Number": num, "Person Name": person,
            "Business Unit": "UT Knoxville Campus BU", "Assignment Number": "E" + num,
            "Assignment Status": "Active - Payroll Eligible", "Assignment Name": assignment,
            "Pay Element Name": element,
            "Payroll Period Start Date": "09/01/2026" if per == "27-03" else "08/01/2026",
            "Payroll Period End Date": "09/30/2026" if per == "27-03" else "08/31/2026",
            "Fiscal Period - Payroll": per, "Fiscal Period - LD": per,
            "Creation Date": "09/30/2026", "Distribution Amount": amount,
            "Line Percentage": "100", "Transaction Number": txn,
            "LD Account Combination": combo, "Status": "Success",
        })
        w.writerow([row[h] for h in LD_HEADER])
    return buf.getvalue().encode("utf-8")


def gl_xlsx():
    rows = [GL_HEADER]
    for per, combo, begin, activity in GL_ROWS:
        segs = combo.split("-")
        end = f"{float(begin) + float(activity):.2f}"
        rows.append([per, "UT System and Campus"] + segs
                    + [float(begin), float(activity), float(end)])
    return xlsx.write_workbook([{"name": "Sheet1", "rows": rows}])


def fli_xlsx():
    rows = [FLI_HEADER, [""] * 33 + ["Descr."]]
    for i, (combo, amount, ref, text, head, dtype, user, dc) in enumerate(FLI_LINES, 1):
        e, fund, dept, acct, prog, act = combo.split("-")[:6]
        row = dict.fromkeys(FLI_HEADER, "")
        row.update({
            "Year": 2027.0, "Period": 3.0, "LnItm": str(i), "Pstng Date": "09/30/2026 11:10 PM",
            "Entry dte": "09/30/2026", "Amount": float(amount), "Entity ID": e, "Fund": fund,
            "Department ID": dept, "Program ID": prog, "GL Account": acct, "Activity Code": act,
            "SPL Doc Line Item Txt": text, "Document Header Text": head, "D/C": dc,
            "User Name": user, "Record No.": str(2400000 + i), "Ref Doc": ref,
            "Document Type": dtype, "Debit/Credit Indicator": "Debit" if dc == "S" else "Credit",
            "Record Type": "Actual",
        })
        rows.append([row[h] for h in FLI_HEADER])
    return xlsx.write_workbook([{"name": "Sheet1", "rows": rows}])


def main():
    out = Path(__file__).resolve().parent / "demo"
    out.mkdir(exist_ok=True)
    (out / "Labor_Distribution_Report.csv").write_bytes(ld_csv())
    (out / "DetailBalances.xlsx").write_bytes(gl_xlsx())
    (out / "Fund_Line_Items.xlsx").write_bytes(fli_xlsx())
    print(f"Wrote three fictional reports to {out}")


if __name__ == "__main__":
    main()
