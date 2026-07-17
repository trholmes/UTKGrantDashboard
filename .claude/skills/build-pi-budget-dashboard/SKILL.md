---
name: build-pi-budget-dashboard
description: Guide a faculty member / PI through building a local-only grant-budgeting dashboard from whatever reports their institution can export, using UTKGrantDashboard as the reference implementation. Use when someone wants to adapt this tool to another university's data, or build a similar PI budget tool from scratch.
---

# Build a PI budget dashboard from institutional exports

You are helping a faculty member turn their institution's clunky financial
report exports into a local dashboard that answers: *Am I on track? Where
should I put new effort? What happens to each grant if I add a person?*
This repo (UTKGrantDashboard) is a complete working example built for the
University of Tennessee's Oracle exports. Their institution's exports WILL
be different — the process below is what transfers.

## Non-negotiable design rules

PIs are handling salary and grant data. These defaults earned the user's
trust and should not be relaxed without explicit direction:

1. **Local-only.** A web server bound strictly to 127.0.0.1, or a generated
   local file. Never hosted, never GitHub Pages, even with client-side-only
   parsing.
2. **Zero outbound requests.** No CDN scripts, fonts, or analytics. The
   claim "works with wifi off" must be literally true and verifiable.
3. **Data can't be committed.** Exports live in a `data/` folder that is
   git-ignored (pattern `data/*` with `!data/README.md`). The repo holds
   only code, so it can be shared freely.
4. **No dependencies.** Python stdlib + vanilla JS keeps it auditable and
   installs nothing. A colleague should run one command.
5. **Deterministic analysis.** All flags/projections are plain formulas in
   the code, not model judgments — same inputs, same outputs, offline.

## Phase 1 — inventory the data (interview first, design second)

Ask what they can export, and get REAL sample files before writing any
code. You are looking to fill two roles; map whatever they have onto them:

| Role | Must contain | Powers | Priority |
|---|---|---|---|
| **Budget snapshot** | budget vs. spent (ideally by category), per award, with award dates | award cards, remaining balances, overspend flags, the anchor for every projection | required |
| **Transaction detail** | dated line items, ideally with person names on payroll lines | burn rates, spending history, people + salaries/fringe for hire planning, support splits | strongly recommended |

If no single export has budgets, look for a "budget vs. actual" or "award
summary" report — some budget-bearing source is non-negotiable. If detail
has no person names, hires can still be modeled with manually entered
people; say so rather than blocking.

## Phase 2 — inspect the exports before designing

Real institutional exports are weird. Check each sample for:

- **Encoding/BOM** — open with `utf-8-sig`; check date and number formats
  (commas, currency symbols, `MM/DD/YYYY` vs ISO).
- **Padding/duplication** — UT's detail export is a full cartesian product
  of labor × non-labor lines (a 200 MB file with ~200 real transactions).
  Test: de-duplicate each side on its own key and compare sums against any
  running-total columns embedded in the report. If totals columns exist,
  ALWAYS validate against them and tell the user the result.
- **Cumulative vs. dated** — a snapshot report has no time axis; you can
  still do pace analysis (spent% vs. time-elapsed%) from award start/end
  dates.
- **Classify files by their columns, not filenames** — report names/numbers
  change; header sniffing survives that. A filename-prefix fallback is fine
  as a secondary signal.
- **Useful extras to look for:** F&A/indirect rate columns, encumbrances/
  committed costs, fringe as its own lines, fee/tuition lines.

## Phase 3 — learn the institution's rules (they change the analysis)

Ask; don't assume UT's answers:

- **Rebudgeting rule** — e.g., at UT any line item may deviate by up to 10%
  of the *total award*. This determines flag severity: grade overruns
  against that allowance, not against the line's own budget.
- **Summer salary** — 9-month faculty appear in payroll only ~3 months/yr.
  Detect them (expense type or asking), display their spending separately,
  and never annualize their monthly rate. Use a 12-month average burn (not
  last-3-months) wherever seasonality would distort projections.
- **Fringe** — estimate each person's real rate from data (fringe paid ÷
  salary paid, per person); rates differ by class (UT: grad ~11%, postdoc
  ~17%, faculty ~35%). Flag fringe charging far above the *budgeted* rate
  as critical — it's usually a charging error that grows every payroll.
- **F&A / indirects** — prefer a rate column from the data; else infer
  effective rate = indirect budget ÷ direct budget, mark it "(est.)", keep
  it editable. Ask what's excluded from the base (MTDC: typically tuition,
  fees, equipment, subaward tails).

## Phase 4 — build in this order, verifying each step

Copy this repo's architecture (`dashboard.py` = parsing + analysis + HTTP
on 127.0.0.1; `static/` = one page of vanilla JS/CSS; `data/config.json`
= saved people/scenarios; mtime-keyed cache for big files):

1. **Parser + validation** — parse both file roles; prove totals match the
   report's own numbers before building anything on top.
2. **Portfolio cards** — per award: time-elapsed vs. budget-spent meters,
   category table with overruns highlighted, monthly spend bars (shared
   month range AND shared $-scale across cards so they compare), burn line.
3. **Flags** — deterministic rules, severity graded by the institution's
   rebudgeting rule; deadline flags (ending soon with money unspent); pace
   flags (projected overrun / far behind schedule). A toggle to hide
   note-level flags.
4. **Balance history + trend** — reconstruct past month-end balances by
   walking BACKWARD from today's known balance through the transactions
   (never forward from an unknown start); superimpose the trend line from
   today to award end. Don't back-extrapolate the trend over history — on
   lumpy-billing awards it's misleading.
5. **People** — seed from payroll lines: monthly salary = average of last
   3 nonzero months, per-person fringe rate, annualized fees; show where
   each person's support currently comes from (%, from their latest paid
   month). Everything editable.
6. **Planning fields on People** — expected end date (graduation), a
   scheduled pay change, and for manual people a grant selector + start
   timing, so hires and departures are modeled in one place. Monthly cost
   = salary × (1+fringe) × support share × (1 + that award's F&A), with
   fees excluded from the F&A base. Keep planning edits out of history.

## Verification norms

- Validate parsed sums against report-embedded totals; report the check.
- Look at the rendered page (screenshot or the user's own browser) after
  every visual change; check dark mode; check that charts don't have
  colliding labels or misformatted negative currency.
- Sanity-check findings with the PI ("your fringe is charging at 16% vs 7%
  budgeted — is that expected?"). Their reactions calibrate the flags.

## Using this repo

`dashboard.py` — parsers (`classify_csv`, `parse_pi_dashboard`,
`parse_detail`), analysis (`compute_flags`, burn rates, people), and the
localhost server. `static/app.js` — all rendering: cards, hand-rolled SVG
charts with hover tooltips, the planning fields. `README.md` — the shape of good
export instructions (link + click-path + what each file powers).

Adapt the parsers and rules; keep the architecture and the security model.
