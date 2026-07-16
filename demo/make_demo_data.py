#!/usr/bin/env python3
"""Generate fictional demo data for UTKGrantDashboard.

Writes a fake PI Dashboard export and a fake expenditure detail export to
demo/data/ for a fictional PI ("Alex Example") with three awards. The
numbers are engineered so the dashboard shows one serious flag (award
ending soon with money unspent), one warning (travel overspent, ~32% of
the 10% rebudgeting allowance), a hidden note, and fully populated cards.

Usage:
    python3 demo/make_demo_data.py
    python3 dashboard.py --data demo/data --port 8790
"""

import csv
from pathlib import Path

OUT = Path(__file__).resolve().parent / "data"
OUT.mkdir(parents=True, exist_ok=True)

# window covered by the fake detail export
MONTHS = ([f"2025-{m:02d}" for m in range(1, 13)] +
          [f"2026-{m:02d}" for m in range(1, 8)])

PROJECTS = {
    "SPN900001": {
        "name": "NSF PHY-0123456 Example", "start": "09/01/2023",
        "end": "08/31/2027", "fa": 0.26,
        # spending before the detail window (award started earlier)
        "prior": {"Salaries & Wages": 60000, "Fringe Benefits": 8000,
                  "Travel": 13000, "Materials & Supplies": 12400,
                  "Other Direct Costs": 11800, "Indirect Costs": 25000},
        "budget": {"Salaries & Wages": 320000, "Fringe Benefits": 48000,
                   "Travel": 24000, "Materials & Supplies": 12000,
                   "Other Direct Costs": 40000, "Indirect Costs": 115000},
    },
    "SPN900002": {
        "name": "DOE DE-SC0099999 Example", "start": "10/01/2022",
        "end": "09/30/2026", "fa": 0.26,
        "prior": {"Salaries & Wages": 148600, "Fringe Benefits": 16100,
                  "Travel": 13850, "Other Direct Costs": 26500,
                  "Indirect Costs": 54269},
        "budget": {"Salaries & Wages": 210000, "Fringe Benefits": 23000,
                   "Travel": 18000, "Other Direct Costs": 30000,
                   "Indirect Costs": 73000},
    },
    "SPN900003": {
        "name": "Physics Foundation 2025-042 Example", "start": "01/01/2025",
        "end": "12/31/2027", "fa": None,
        "prior": {},
        "budget": {"Salaries & Wages": 70000, "Fringe Benefits": 8000,
                   "Travel": 8000, "Other Direct Costs": 14000},
    },
}

# (project, person, person_num, monthly salary, fringe rate, type, months)
PAYROLL = [
    ("SPN900001", "Riley Park", "00900101", 2600.00, 0.108,
     "GTA GA GRA Salaries", MONTHS),
    ("SPN900001", "Morgan Reyes", "00900102", 2900.00, 0.170,
     "Professional Other Academic Salaries", MONTHS),
    ("SPN900001", "Alex Example", "00900100", 5500.00, 0.340,
     "Faculty Salaries", ["2025-05", "2025-06", "2025-07", "2026-05", "2026-06"]),
    ("SPN900002", "Casey Kim", "00900103", 2600.00, 0.108,
     "GTA GA GRA Salaries", MONTHS),
    ("SPN900003", "Morgan Reyes", "00900102", 1550.00, 0.170,
     "Professional Other Academic Salaries", MONTHS),
]

NONLABOR = [  # (project, month, type, category, person, amount)
    ("SPN900001", "2025-01", "Student Fees", "Other Direct Costs", "Riley Park", 3400),
    ("SPN900001", "2025-08", "Student Fees", "Other Direct Costs", "Riley Park", 3400),
    ("SPN900001", "2026-01", "Student Fees", "Other Direct Costs", "Riley Park", 3400),
    ("SPN900002", "2025-03", "Domestic Travel", "Travel", "", 800),
    ("SPN900002", "2025-10", "Domestic Travel", "Travel", "", 1200),
    ("SPN900002", "2026-04", "Domestic Travel", "Travel", "", 950),
    ("SPN900003", "2025-02", "Domestic Travel", "Travel", "", 1250),
    ("SPN900003", "2025-04", "Foreign Travel", "Travel", "", 2100),
    ("SPN900003", "2025-06", "Domestic Travel", "Travel", "", 900),
    ("SPN900003", "2025-09", "Foreign Travel", "Travel", "", 2400),
    ("SPN900003", "2025-11", "Domestic Travel", "Travel", "", 1050),
    ("SPN900003", "2026-02", "Domestic Travel", "Travel", "", 1300),
    ("SPN900003", "2026-05", "Domestic Travel", "Travel", "", 1150),
    ("SPN900003", "2026-06", "Domestic Travel", "Travel", "", 1050),
]


def build_transactions():
    """Expand payroll into labor lines + fringe + monthly indirect."""
    labor, nonlabor = [], []
    idc = {}  # (project, month) -> base amount
    for proj, person, num, salary, fr, ty, months in PAYROLL:
        for m in months:
            labor.append((proj, m, ty, "Salaries & Wages", person, num, salary))
            labor.append((proj, m, "Negotiated Fringe Benefit Rate",
                          "Fringe Benefits", person, num, round(salary * fr, 2)))
            key = (proj, m)
            idc[key] = idc.get(key, 0.0) + salary * (1 + fr)
    for (proj, m), base in sorted(idc.items()):
        fa = PROJECTS[proj]["fa"]
        if fa:
            nonlabor.append((proj, m, "Indirect Cost", "Indirect Costs",
                             "", round(base * fa, 2)))
    for proj, m, ty, cat, person, amt in NONLABOR:
        nonlabor.append((proj, m, ty, cat, person, float(amt)))
    return labor, nonlabor


def write_detail(labor, nonlabor):
    cols = ["P_PROJECT", "PROJ_NUMBER", "PROJ_NAME", "PROJ_STATUS",
            "PROJ_START", "PROJ_END", "PROJ_PI", "SPONSOR_FA_RATE",
            "AUDIT_FA_RATE", "PFROMDATE", "PTODATE",
            "L_TRX_NUM", "L_LAB_TRX", "L_PER_NUM", "L_PER_NAME",
            "L_EXP_DATE", "L_EXP_TYPE", "L_EXP_CAT", "L_EXP_COST",
            "NL_TRX_NUM", "NL_PER_NAME", "NL_EXP_DATE", "NL_EXP_TYPE",
            "NL_EXP_CAT", "NL_EXP_COST"]
    with open(OUT / "RPT_DEMO - Expenditure Detail Report.csv", "w",
              newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        trx = 90000000

        def meta(proj):
            p = PROJECTS[proj]
            fa = p["fa"] if p["fa"] is not None else ""
            return [proj, proj, p["name"], "Active", p["start"], p["end"],
                    "Alex Example", fa, fa, "01/01/2025", "08/01/2026"]

        for proj, m, ty, cat, person, num, amt in labor:
            trx += 1
            w.writerow(meta(proj) + [trx, f"C{trx}R1", num, person,
                                     f"{m}-28", ty, cat, amt,
                                     "", "", "", "", "", ""])
        for proj, m, ty, cat, person, amt in nonlabor:
            trx += 1
            w.writerow(meta(proj) + ["", "", "", "", "", "", "", "",
                                     trx, person, f"{m}-15", ty, cat, amt])


def write_pi_dashboard(labor, nonlabor):
    # spent per (project, category) = detail-window sums + prior spending,
    # so the dashboard file is consistent with the transactions
    spent = {}
    for proj, m, ty, cat, person, num, amt in labor:
        spent[(proj, cat)] = spent.get((proj, cat), 0.0) + amt
    for proj, m, ty, cat, person, amt in nonlabor:
        spent[(proj, cat)] = spent.get((proj, cat), 0.0) + amt
    for proj, p in PROJECTS.items():
        for cat, amt in p["prior"].items():
            spent[(proj, cat)] = spent.get((proj, cat), 0.0) + amt

    cols = ["Project Number", "Project Name", "Project PI / Manager",
            "Project Status", "Project Start Date", "Project Finish Date",
            "Budget", "SUM Direct Cost", "SUM Indirect Cost",
            "Committed Cost", "Remaining Balance", "% of Budget Spent",
            "Expenditure Category"]
    order = ["Salaries & Wages", "Fringe Benefits", "Travel",
             "Materials & Supplies", "Other Direct Costs", "Indirect Costs"]
    with open(OUT / "PI Dashboard.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for i, (proj, p) in enumerate(PROJECTS.items(), start=1):
            for j, cat in enumerate(order, start=1):
                if cat not in p["budget"]:
                    continue
                budget = p["budget"][cat]
                s = round(spent.get((proj, cat), 0.0), 2)
                direct, indirect = (0, s) if cat == "Indirect Costs" else (s, 0)
                w.writerow([proj, p["name"], "Example, Alex", "Active",
                            p["start"], p["end"], budget, direct, indirect,
                            "", round(budget - s, 2),
                            round(s / budget, 6) if budget else "",
                            f"{j:02d}. {cat}"])


labor, nonlabor = build_transactions()
write_detail(labor, nonlabor)
write_pi_dashboard(labor, nonlabor)
print(f"Wrote demo exports to {OUT}/")
