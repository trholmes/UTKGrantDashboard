# data/

Put your CSV exports here:

* the **PI Dashboard** export (budget vs. actuals by project and category)
* one or more **expenditure detail report** exports (filename starts
  with `RPT`)

The dashboard's **Get fresh data** section will download the detail report
and drop it in here for you — see the README. Any `report_source.json` you
put here overrides where that download points (host, catalog path, report
layout name).

Everything in this folder except this README is git-ignored, so your
financial data can never be committed or pushed. Saved scenarios live here
too (`config.json`).
