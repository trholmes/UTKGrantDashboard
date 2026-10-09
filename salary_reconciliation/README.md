# Salary Reconciliation

A business-office tool, separate from the grant dashboard in the folder
above (it shares no code with it). It checks that what payroll charged —
the **Labor Distribution** report — is what was posted to the General
Ledger, as the **DetailBalances** and **Fund Line Items** reports show it,
per account combination and fiscal period, and when they differ, finds
the lines that are missing on one side.

It automates this routine:

1. Run the **Labor Distribution** report and sort it by *LD Account
   Combination*, then person name. The combination holds the fund and the
   GL code, so this groups each fund's salary by GL code.
2. Run **DetailBalances** (General Accounting), which shows the posted
   amount for each GL code.
3. Where the Labor Distribution total doesn't match DetailBalances, run
   **Fund Line Items** to find what is missing.

The page does all three steps as soon as the reports are loaded, and works
with any subset: Labor Distribution alone gives the sorted list with
subtotals; adding DetailBalances shows which combinations match; adding
Fund Line Items explains each difference line by line.

## Starting it (Windows)

1. Install Python 3 once, from
   [python.org/downloads/windows](https://www.python.org/downloads/windows/)
   — tick **Add python.exe to PATH** on the installer's first screen.
   Nothing else is ever installed.
2. Double-click **`Start Salary Reconciliation.bat`** in this folder. A
   small black window stays open while it runs, and your browser opens to
   the page. Close that window to stop.

On a Mac, double-click **`Start Salary Reconciliation.command`** instead
(the first time, macOS may want you to right-click it → *Open*). On Linux:
`python3 server.py`.

## Using it

Save the three exports into the **`data`** folder in this folder, and start
the tool (or click **Reload data folder** if it's already open). It reads the
folder every time the page opens. Each report is found by its name:

| Report | Name contains |
| --- | --- |
| Labor Distribution | `Labor Distribution` — e.g. `Labor_Distribution_Report.csv` |
| DetailBalances | `DetailBalances` — e.g. `DetailBalances_3.xlsx` |
| Fund Line Items | `Fund Line Items` — e.g. `Fund_Line_Items_-_3_Segments_Fund_Line.xlsx` |

Capitals, spaces, underscores and dashes don't matter, so the names the
reporting system gives its exports work as they are. CSV, `.xlsx` and `.xls`
all work — including the things reporting systems save under an `.xls` name
(an old-style Excel 97-2003 workbook, Excel 2003 XML, an HTML table, or
plain CSV text): each file is read by what's inside it, not its extension.
A file named like one of the reports but in a format this can't read (a
PDF, say) is named on the page rather than skipped silently. Old exports can stay in the folder: the newest file of each report
is used, and the page says which ones it skipped. Files whose names match
none of the three are named on the page and skipped. If a file's columns
turn out to be a different report than its name says, the columns win and
the page says so.

You can also drag files onto the page, or click **Choose files…** — those
are recognized by their column headings and held in memory only.

### By person (the main list)

One row per person: what Labor Distribution charged for them, what Fund
Line Items posted for them, the difference, and a status — *Matches*,
*Explained — posted to another account*, *Does not match*, *Not in
Labor Distribution* (Fund Line Items paid them; the Labor Distribution
file doesn't mention them), *Not in Fund Line Items*, *Total matches,
accounts differ* (the right total, but not in the accounts or periods
payroll charged, with nothing tying the lines back), or *Fund not in
Fund Line Items* (not compared).

Open a person to see their pay period by period — each period's totals
and whether it lines up — and within each period every account
combination they were charged to, with Labor Distribution against Fund
Line Items. Under each account the lines are listed **by report**: the
*Labor Distribution* lines with their subtotal, then the *Fund Line
Items* lines with theirs (subtotals are the green rows, and there is one
for every report even when it has a single line), so the two reports
can be compared side by side. Each line says how it fared on the other
side: nothing when it pairs up by transaction number, or *not in Fund
Line Items*, *no Labor Distribution line*, *Fund Line Items has … on
this transaction*, or *posted to 512400 Faculty Longevity Pay* /
*charged in Labor Distribution to 512100* when accounting posted a line
to a different account than Labor Distribution shows.

The posted side of this comes from **Fund Line Items** (DetailBalances
only has account totals). A Fund Line Items line belongs to the person
whose Labor Distribution transaction its *Ref Doc* names, or else to the
person its "Accounting for *Name* Assignment name: …" text names. Fund
Line Items entries on salary accounts that are neither — journals,
salary transfers — are listed below the people, per account: they're
what explains an account total that differs when every person lines up.

### Narrowing the lists

The **Period** menu above the list narrows everything — people, their
totals and statuses, the tiles, and the account tab — to one period. A
line charged in one period but posted in the next shows on both, as
explained.

Under it is a box for **each element of the chart string** — Entity,
Fund, Department, Account, Program, Activity, InterCo, Future — and one
for the **Person**. Each box takes a value (`512100`), the start of one
(`512` for every 512xxx account), a range (`512100-512400`; a short
range is widened to the segment, so `512-513` on Account is 512000 to
513999), or a list separated by commas or spaces (`512100, 537600`).
The Person box takes part of a name or a person number; since names hold
commas, several people are separated by `;` (`Rivera; Chen`). On the
By person tab the account boxes narrow each person to the accounts that
match — their totals, status and the tiles follow, so "everyone paid
from fund 1100001" is a one-box question; on the By account tab they
pick the rows, and the Person box the rows someone is paid from (and,
inside a row, which people are shown).

**Hide lines that cancel out** leaves out pairs of lines for the same
person, account combination and period whose amounts are exact opposites
(306.98 and −306.98: a payment reversed and issued again). They net to
zero, so no total or status changes; a note under each list says how many
lines were hidden. The Excel download follows the toggle.

### By account

The second tab is the account view: for each account combination and
period, the Labor Distribution total, the DetailBalances period activity,
the difference, and a status:

| Status | Meaning |
| --- | --- |
| **Matches** | Labor Distribution and DetailBalances agree. |
| **Does not match** | Both have amounts, and they differ. |
| **Not in DetailBalances** | Payroll charged this combination; DetailBalances has no row for it. |
| **DetailBalances only** | DetailBalances shows salary-type activity on a combination no Labor Distribution line was charged to. |
| **Fund not in DetailBalances** | Not compared: the DetailBalances report has no rows at all for this fund and department in the period, so it wasn't run for it. Not counted as a difference. |
| **Explained — posted to another account** | The whole difference is lines accounting posted to a different GL account (or period) than Labor Distribution shows — see below. |

Click a row to open it. A sentence or two says how far apart the two
reports are and what kind of lines account for it; then comes **each
person** paid from the combination — their Labor Distribution and Fund
Line Items totals for it, the difference, and a status — and under each
person their lines **by report**: the *Labor Distribution* lines with a
subtotal, then the *Fund Line Items* lines with a subtotal, every line
noting how it fared on the other side (*not in Fund Line Items*, *no
Labor Distribution line*, *posted to 512400 …*, and so on). People whose
pay is in Fund Line Items but not in the Labor Distribution file (read
from the line's "Accounting for *Name* Assignment name: …" text) are
listed too, with no Labor Distribution lines; the page says how many
there are. Fund Line Items entries that aren't anyone's pay — a salary
transfer, a correcting journal — come last under **Not anyone's payroll
line**, with their own subtotal.

**Download Excel** saves a workbook with a *By person* sheet (each person,
their accounts, and the lines that don't simply pair up), a *Summary* sheet, the *Labor
Distribution sorted* sheet with subtotal rows (the step 1 result), a
*Differences* sheet listing every unmatched line, and a *Not compared* sheet
for lines set aside (below).

## How the reports are lined up

* **Account combination.** The LD Account Combination
  (`10-1100001-106015-512100-210-0000-00-0000`) is
  Entity-Fund-Department-Account-Program-Activity-InterCo-Future — the same
  columns DetailBalances has. Fund Line Items has the first six of them.
  The page shows each combination with its fund, GL account and activity
  code picked out ("Fund 1100001 · Account 512100 · Activity 0053"), so
  two activity codes on one fund are separate rows; typing an activity
  code in the Activity box shows just that activity.
* **Period.** Labor Distribution's *Fiscal Period - Payroll* (`27-02`) —
  the period DetailBalances lines up with; *Fiscal Period - LD* only if
  that's blank — is matched
  to DetailBalances' *Accounting Period* and to Fund Line Items' *Year* and
  *Period* (`2027`, `2` → `27-02`). The periods compared are the ones in
  the DetailBalances report; Labor Distribution lines in other periods are
  listed separately as *not compared* — run both reports for the same
  period(s).
* **Which DetailBalances rows count.** Only the salary GL accounts,
  **511100–518900**, plus any listed in `EXTRA_SALARY_ACCOUNTS` (for now
  `537600` Joint Faculty Salaries) — both at the top of `reconcile.py`.
  Labor Distribution no longer carries fringe, so fringe (`528100`) and
  every other account in DetailBalances or Fund Line Items is left out,
  even on a line that names a person. Combinations Labor Distribution
  charged are always compared.
* **Matching Fund Line Items to Labor Distribution.** A Fund Line Items line's
  *Ref Doc* is the Labor Distribution *Transaction Number*, so lines are
  paired by reference only — never by amount alone, which would pair two
  people paid the same. *Amount* is taken as signed: credits come out
  negative and reversals as negative debits (on real exports these add up
  to DetailBalances exactly).
* **Lines posted to another account.** Accounting re-maps some pay
  elements when it posts: a longevity payment Labor Distribution charges to
  512100 Faculty Salaries lands on 512400 Faculty Longevity Pay, and
  overtime charged to 516100 lands on 516200. A Labor Distribution line
  missing from its own account is looked up by its transaction number
  across every account and period in Fund Line Items — the line with the
  same amount is taken, so a transaction that carries regular pay and
  overtime pairs each part with its own posting. Failing that, a Fund
  Line Items payroll line for the **same person, period and amount** on
  another account, with no Labor Distribution line of its own, is taken
  to be it, and the note says so ("same amount, no reference"). Either
  way both rows show it — "posted to 516200 Overtime Pay" on one,
  "charged in Labor Distribution to 516100" on the other — and a row
  whose whole difference is accounted for this way is marked
  **Explained — posted to another account** rather than as a difference.
* **Check on the Fund Line Items report.** For each combination its lines
  should add up to the DetailBalances amount; if not, the page says so —
  the report was probably run for a different date range.
* Labor Distribution lines whose *Status* isn't *Success* are listed
  separately and not compared.

## Security

* The page is served by a small program bound to **127.0.0.1** — reachable
  only from this computer — which refuses requests from any other web
  page.
* No outbound network requests, no external scripts or fonts.
* It reads the reports in `data/` and never writes there. Everything in
  `data/` except its README is **git-ignored**, so payroll data can't be
  committed by accident. Files dragged onto the page are held **in memory
  only**.
* Python standard library only; every file here is short and readable.

## Trying it without real data, and for developers

```
python make_demo.py                 # three fictional reports in demo/
python reconcile.py demo/* -o result.xlsx   # same thing, no browser
python reconcile.py -o result.xlsx          # the newest reports in data/
python -m unittest discover tests   # run from this folder
```

| File | What it does |
| --- | --- |
| `reconcile.py` | Reads the reports and lines them up; also a command-line tool. |
| `xlsx.py` | Reads and writes `.xlsx` with the standard library. |
| `xls.py` | Reads the formats that go by `.xls` with the standard library. |
| `server.py` | The local web server behind the page. |
| `static/` | The page itself. |
| `make_demo.py` | Writes fictional reports in the real layouts. |
