# data/

Put your CSV exports here:

* the **PI Dashboard** export (budget vs. actuals by project and category)
* one or more **expenditure detail report** exports (filename starts
  with `RPT`)

The dashboard's **Get fresh data** section will download the detail report
and drop it in here for you — see the README. Any `report_source.json` you
put here overrides where that download points (host, catalog path, report
layout name).

Looking after several PIs? Give each one a folder in here (`Holmes/`,
`Doe, Jane/`) with the dashboard's **+ New PI folder** button, which also
lists the folder in `pi_folders.json` — only listed folders count as PI
folders, so anything else you keep in here is left alone. The page header
then has a menu to switch between them, and each folder gets its own
saved scenarios.

Everything in this folder except this README is git-ignored (subfolders
included), so your financial data can never be committed or pushed. Saved
scenarios live here too (`config.json`).
