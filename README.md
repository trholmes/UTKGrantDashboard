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

## Getting the data

Two reports feed the dashboard. Any filename ending in `.csv` works — the
tool identifies each file by its columns, so don't worry about renaming.

### 1. PI Dashboard export — required

This is the budget-vs-actuals summary (one row per project × expenditure
category). It provides the **budgets, remaining balances, and committed
costs** — without it there are no award cards, no flags, and nothing for
projections to anchor to.

1. Open the [PI Dashboard](https://oaxfdiprod-idabxacptyfb-ia.analytics.ocp.oraclecloud.com/ui/dv/?pageid=visualAnalyzer&reportmode=full&reportpath=%2F%40Catalog%2Fshared%2FUT%2FFIN%2FPI%2FPI%20Dashboard)
   in Oracle Analytics.
2. Navigate to **Project Summary**.
3. Enter your name in **Project PI / Manager**.
4. Export the Project Summary table as **CSV** and drop it in `data/`.

### 2. Expenditure detail report — strongly recommended

This is the transaction-level report (in Oracle BI Publisher; the report
number changes from time to time, but the export filename always starts
with `RPT`). It provides **monthly burn rates and spending history,
everyone paid from each award (with salaries and fringe rates that seed
the hiring simulator), support splits, and exact F&A rates**. Without it
the dashboard still works, but falls back to linear burn estimates and an
empty People section.

1. Open the [expenditure detail report](https://fa-ewlq-saasfaprod1.fa.ocs.oraclecloud.com/analytics/saw.dll?bipublisherEntry&Action=open&itemType=.xdo&bipPath=%2FCustom%2FProjects%2FSponsored%20Projects%2FRPT_GMS_007%20-%20Sponsored%20Project%20Detail%20Report.xdo&path=%2Fshared%2FCustom%2FProjects%2FSponsored%20Projects%2FRPT_GMS_007%20-%20Sponsored%20Project%20Detail%20Report.xdo)
   in Oracle BI Publisher. (If the link goes stale, search BI Publisher
   for the current "Sponsored Project Detail Report".)
2. Add **all of your project IDs** (the `SPN…` numbers from the PI
   Dashboard export) and select the **widest time period available** —
   more history means better burn rates and seasonality detection.
3. Export to **CSV** and drop it in `data/`.

Notes:

* Multiple detail-report files are fine (e.g., one per account, or overlapping date
  ranges) — they merge and de-duplicate automatically. For PI Dashboard
  files, the newest file wins per project.
* Re-export both whenever you want fresh numbers (monthly is plenty) and
  click **Reload data** in the page header. Old files can stay in `data/`.
* Don't worry that the detail export is huge (hundreds of MB) — the reporting
  tool pads it heavily; parsing is a few seconds, once, per new file.

## Notes on the numbers

* The detail report is exported by the reporting tool as a cartesian
  product of labor × non-labor lines; the parser de-duplicates each side
  and validates against the report's own totals.
* Line-item overruns are graded against the rebudgeting rule that any
  line may deviate by up to **10% of the total award**: a flag is only
  critical when that allowance is exhausted, and small overruns show as
  notes. Exception: fringe charging far above the budgeted fringe rate is
  always critical, since it usually signals a charging error that grows
  with every payroll.
* Burn rate prefers a 12-month average (capturing seasonality like summer
  salary), falling back to the last 3 active months, then to a linear
  average over the award period.
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

## Adapting this for another institution

Faculty elsewhere won't have these exact exports, but they can build the
same product tailored to whatever their institution exports. This repo
ships a skill for exactly that:
[`.claude/skills/build-pi-budget-dashboard/SKILL.md`](.claude/skills/build-pi-budget-dashboard/SKILL.md)
— it walks an AI coding assistant (e.g. Claude Code) through the process:
inventorying what the institution can export, inspecting the files'
quirks, learning the local rules (rebudgeting allowances, fringe rates,
summer salary, F&A), and building/verifying each feature, with this
codebase as the reference implementation.

To use it: clone this repo and open it in Claude Code (the skill loads
automatically), or copy the skill folder into your own project's
`.claude/skills/` — then ask for a dashboard built from your sample
exports.
