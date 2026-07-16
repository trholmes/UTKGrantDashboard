# UTKGrantDashboard

A **local-only** budget dashboard for PIs. It reads the CSV exports you can
already pull from the university reporting system, and gives you:

* **Portfolio view** — every award with budget vs. spent vs. remaining by
  category, spending pace vs. time elapsed, monthly burn rate, and runway.
* **Automatic flags** — overspent categories, charges against $0 budget
  lines, awards ending soon with money unspent, on-pace-to-overrun warnings.
* **What-if hiring simulator** — put a person (grad student, postdoc, …) on
  one or more awards at some % effort for a date range and see each award's
  projected balance through the end of the award, including fringe, F&A, and
  optional student fees. People are seeded automatically from the payroll
  lines in your detail export, with their real salaries and fringe rates.

## Security model

This is the part to check before trusting it with financial data:

* `python3 dashboard.py` starts a web server bound **strictly to 127.0.0.1**
  — it is only reachable from your own machine.
* The tool makes **zero outbound network requests**. No CDN scripts, no
  fonts, no analytics. Turn wifi off and it works identically.
* Your CSVs live in the `data/` folder, which is **.gitignore'd** — they
  cannot be committed or pushed by accident. The repo contains only code.
* No dependencies to install: Python 3.9+ standard library only
  (macOS ships with this). The whole tool is a few files of readable
  Python/JS — audit it yourself.

## Quick start

1. Clone this repo (or download it).
2. Put your CSV exports in the `data/` folder (see below).
3. Run:

   ```
   python3 dashboard.py
   ```

   or double-click `Start Dashboard.command`. Your browser opens to
   `http://127.0.0.1:8787`.

4. When you have fresh exports, drop them in `data/` and click
   **Reload data** in the page header.

## What to export

Two report types are recognized (any filename ending in `.csv` works — the
tool identifies them by their columns):

1. **PI Dashboard export** — the budget-vs-actuals summary with one row per
   project × expenditure category (columns like `Project Number`, `Budget`,
   `SUM Direct Cost`, `Expenditure Category`). This drives the portfolio
   view and flags. Export it for **all your projects**.

2. **RPT_GMS_007 — Sponsored Project Detail Report** (CSV) — the
   transaction-level detail report. This drives monthly burn rates and
   seeds the hiring simulator with real people, salaries, and fringe rates.
   Export it for **all active accounts** with the **widest date range** the
   system allows. Multiple files are fine (e.g., one per account) — they
   merge and de-duplicate automatically.

You can refresh either file whenever you like; the newest file wins where
they overlap.

## Notes on the numbers

* The detail report is exported by the reporting tool as a cartesian
  product of labor × non-labor lines; the parser de-duplicates each side
  and validates against the report's own totals.
* Burn rate = average of the last 3 complete months with activity in the
  detail export; if no detail is loaded, it falls back to a linear average
  over the award period.
* Simulator cost model: `salary × effort + fringe (person's actual rate) +
  F&A × (salary + fringe)`. Student fees/tuition are excluded from the F&A
  base (MTDC) and only charged if you check the box. The F&A rate comes
  from the detail export and is editable per award.
* Scenario edits (people, assignments, overrides) save to
  `data/config.json` — local and git-ignored, like everything else in
  `data/`.

## Sharing with a colleague

Point them at this repo. They clone it, export their own two reports into
`data/`, and run the same command. Nothing about your data travels with the
repo.
