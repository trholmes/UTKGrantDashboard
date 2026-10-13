#!/usr/bin/env python3
"""Build one-click download links for the sponsored-project detail report.

Give it a list of SPN project numbers and a start date; get back a URL that
makes the university's BI Publisher stream the expenditure detail report
straight to your Downloads folder — no navigating the report UI, no
Apply → gear → Export → CSV.

    python3 spn_reports.py SPN107048 SPN107049 --from 2025-01-01
    python3 spn_reports.py --from-data data --open      # projects from your
                                                       # PI Dashboard export
    python3 spn_reports.py --from-data data --installer install.html

The dashboard uses this module too (see the "Get fresh data" section), so the
URL is built in exactly one place.

Nothing here talks to the network: it composes a URL string. The download
happens in your browser, using the Oracle session you are already logged into.

Why a plain link and not a background fetch: the tenant sits behind Oracle
Access Manager, which bounces XHR/fetch requests to a login redirect even
with a live session. A top-level navigation (clicking a link, typing the URL,
or a bookmarklet's `<a download>` click) carries the session and returns the
CSV. Do not "improve" this into a fetch().
"""

import argparse
import codecs
import csv
import html
import json
import re
import sys
import webbrowser
from datetime import date, datetime, timedelta
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote

# ---------------------------------------------------------------------------
# where the report lives (all of it overridable — see load_source)
# ---------------------------------------------------------------------------

# Every value below was confirmed against the live system. The one to check
# first if a link stops downloading is "template" (`_xt`): it is the label on
# the report's output tab, and without it BI Publisher opens the interactive
# viewer instead of streaming a file.
DEFAULT_SOURCE = {
    "host": "https://fa-ewlq-saasfaprod1.fa.ocs.oraclecloud.com",
    "reportPath": ("/Custom/Projects/Sponsored Projects/"
                   "RPT_GMS_007 - Sponsored Project Detail Report.xdo"),
    "template": "RPT07",
    "projectParam": "P_PROJECT",
    "fromParam": "P_FROM_DATE",
    "toParam": "P_TO_DATE",
    # The report mis-parses ISO and slash-separated dates (they come back as
    # year 0169 with $0 totals). mm-dd-yyyy with dashes is the confirmed format.
    "dateFormat": "%m-%d-%Y",
    "piDashboardUrl": (
        "https://oaxfdiprod-idabxacptyfb-ia.analytics.ocp.oraclecloud.com/ui/dv/"
        "?pageid=visualAnalyzer&reportmode=full&reportpath=%2F%40Catalog%2Fshared"
        "%2FUT%2FFIN%2FPI%2FPI%20Dashboard"),
}

# Optional per-machine override, dropped in the data folder (git-ignored):
# {"template": "RPT7"} is enough to retarget a renamed layout.
SOURCE_FILE = "report_source.json"

PROJECT_RE = re.compile(r"SPN\d+", re.IGNORECASE)
BARE_NUMBER_RE = re.compile(r"\d{4,}")

# A top-up export (just the newest charges, to go on top of the exports
# already in data/) starts this many months before the existing coverage
# ends. Charges post late — payroll a few days after month end, expense
# reports weeks after the trip, cost transfers months after the fact — and
# the report selects on the charge's own date, so a window that started
# exactly where the last one ended would miss every one of them. Overlap is
# free: a charge in two exports is counted once (see parse_detail).
OVERLAP_MONTHS = 3


# ---------------------------------------------------------------------------
# reading an export: encoding, header row, delimiter
# ---------------------------------------------------------------------------
# The reporting tools are not consistent about the shape of a CSV: a BOM or
# not, UTF-8 or UTF-16, a title row or two above the header, commas or tabs.
# Everything that reads an export goes through here so all of that is
# handled in one place.

# a line is the header row when it names one of these columns
HEADER_MARKERS = ("Project Number", "PROJ_NUMBER", "P_PROJECT", "L_EXP_COST",
                  "Expenditure Category", "Award Number")
HEADER_SCAN_LINES = 20   # title rows tolerated above the header


def export_encoding(path):
    with open(path, "rb") as f:
        head = f.read(4)
    if head.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        return "utf-16"
    return "utf-8-sig"


def _delimiter(header):
    counts = {d: header.count(d) for d in (",", "\t", ";", "|")}
    best = max(counts, key=counts.get)
    return best if counts[best] else ","


def find_header(path):
    """Where an export's header row is: {"encoding", "line", "header",
    "delimiter"}, or None when no line near the top names a known column."""
    try:
        encoding = export_encoding(path)
        with open(path, encoding=encoding, newline="", errors="replace") as f:
            for i in range(HEADER_SCAN_LINES):
                line = f.readline()
                if not line:
                    return None
                if any(m in line for m in HEADER_MARKERS):
                    return {"encoding": encoding, "line": i, "header": line,
                            "delimiter": _delimiter(line)}
    except (OSError, ValueError):
        return None
    return None


def header_reader(f, info):
    """A DictReader on an open export, skipping title rows and tidying
    header names (a trailing space in a column name is not a new column)."""
    for _ in range(info["line"]):
        f.readline()
    reader = csv.DictReader(f, delimiter=info["delimiter"])
    reader.fieldnames = [(h or "").strip() for h in (reader.fieldnames or [])]
    return reader


@contextmanager
def export_reader(path):
    """with export_reader(path) as rows: ... — a DictReader over an export,
    whatever its encoding, delimiter and title rows."""
    info = find_header(path) or {"encoding": export_encoding(path), "line": 0,
                                 "delimiter": ","}   # no known column: line 1 it is
    with open(path, encoding=info["encoding"], newline="") as f:
        yield header_reader(f, info)


def read_source_file(data_dir):
    """The usable overrides in data_dir/report_source.json, or {}."""
    try:
        with open(Path(data_dir) / SOURCE_FILE, encoding="utf-8") as f:
            loaded = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(loaded, dict):
        return {}
    return {k: v for k, v in loaded.items()
            if k in DEFAULT_SOURCE and isinstance(v, str) and v.strip()}


def load_source(data_dir=None, **overrides):
    """DEFAULT_SOURCE, plus data/report_source.json, plus explicit overrides.

    data_dir may be a list of folders, applied in order — the data folder
    first, then a PI's own folder inside it, so the latter's file wins.
    """
    src = dict(DEFAULT_SOURCE)
    dirs = data_dir if isinstance(data_dir, (list, tuple)) else [data_dir]
    for d in dirs:
        if d:
            src.update(read_source_file(d))
    src.update({k: v for k, v in overrides.items() if v})
    return src


# ---------------------------------------------------------------------------
# inputs: project numbers and dates
# ---------------------------------------------------------------------------

def normalize_projects(values):
    """Pull project codes out of anything the user can plausibly hand over.

    Accepts a string ("SPN107048, 107049" — commas, spaces or newlines) or an
    iterable of strings. Bare numbers get the SPN prefix, codes are upcased,
    and duplicates drop out while first-seen order is kept.
    """
    if isinstance(values, str):
        values = [values]
    seen, out = set(), []
    for chunk in values or []:
        text = str(chunk or "")
        for token in re.split(r"[\s,;]+", text):
            if not token:
                continue
            match = PROJECT_RE.fullmatch(token) or PROJECT_RE.search(token)
            if match:
                code = match.group(0).upper()
            elif BARE_NUMBER_RE.fullmatch(token):
                code = "SPN" + token
            else:
                continue
            if code not in seen:
                seen.add(code)
                out.append(code)
    return out


def parse_date(value):
    """Parse an ISO, mm-dd-yyyy or mm/dd/yyyy date into a date object."""
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        raise ValueError("missing date")
    for fmt in ("%Y-%m-%d", "%m-%d-%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unrecognized date: {value!r} (use YYYY-MM-DD)")


def report_date(value, source=None):
    """Format a date the way the report expects it (mm-dd-yyyy)."""
    src = source or DEFAULT_SOURCE
    return parse_date(value).strftime(src["dateFormat"])


# ---------------------------------------------------------------------------
# coverage: what the detail exports in hand already span, and where a top-up
# should start
# ---------------------------------------------------------------------------
# Every row of a detail export carries the window it was pulled for
# (PFROMDATE / PTODATE). Merging those windows per project says what history
# is in hand; the next download then only has to start a little before that
# ends, instead of back at the award's first day every time.

def merge_windows(windows):
    """Merge (from, to) ISO-date windows into sorted, non-overlapping spans.

    Windows that touch (one ends the day before the next starts) merge too.
    Returns [[from, to], ...]; [] for no windows.
    """
    spans = sorted((f, t) for f, t in windows or () if f and t and f <= t)
    merged = []
    for f, t in spans:
        if merged and f <= _day_after(merged[-1][1]):
            merged[-1][1] = max(merged[-1][1], t)
        else:
            merged.append([f, t])
    return merged


def window_gaps(windows):
    """Stretches of time between the merged windows: [[from, to], ...] —
    dates that no export in hand covers, where a month looks empty even if
    charges exist. A top-up pulled too late, after the last one ended, is
    how one appears."""
    merged = merge_windows(windows)
    return [[_day_after(a[1]), _day_before(b[0])]
            for a, b in zip(merged, merged[1:])]


def _day_after(iso):
    return (date.fromisoformat(iso) + timedelta(days=1)).isoformat()


def _day_before(iso):
    return (date.fromisoformat(iso) - timedelta(days=1)).isoformat()


def top_up_start(coverage_end, overlap_months=OVERLAP_MONTHS):
    """Where a top-up window starts: the first of the month, overlap_months
    before the month the existing coverage ends in."""
    end = parse_date(coverage_end)
    months = end.year * 12 + end.month - 1 - overlap_months
    return date(months // 12, months % 12 + 1, 1).isoformat()


def top_up_from_date(windows_by_project, projects=None, starts=None,
                     overlap_months=OVERLAP_MONTHS):
    """The from-date that tops up the exports in hand for these projects.

    windows_by_project : {project: iterable of (from, to)} — the detail
                         windows read from the exports (see scan_detail_windows)
    projects           : the projects the download is for (default: every
                         project with a window)
    starts             : {project: start date} for projects without any
                         detail history yet — they get their whole history

    Per project: the start of any gap in its coverage, else overlap_months
    before its coverage ends; a project with no coverage starts at its
    award start. The earliest of those wins, so one download tops up every
    project at once. None when nothing is known about any of them.
    """
    projects = list(projects) if projects is not None else list(windows_by_project)
    candidates = []
    for pid in projects:
        windows = windows_by_project.get(pid) or ()
        merged = merge_windows(windows)
        if merged:
            gaps = window_gaps(windows)
            candidates.append(gaps[0][0] if gaps
                              else top_up_start(merged[-1][1], overlap_months))
        elif starts and starts.get(pid):
            candidates.append(parse_date(starts[pid]).isoformat())
    return min(candidates) if candidates else None


def scan_detail_windows(data_dir):
    """{project: {(from, to), ...}} from every detail export in a folder.

    Reads each export in full (a project's rows can sit anywhere in the
    cartesian product), so this takes a while on a big file. The dashboard
    has the parsed exports in memory and never needs this — it is for the
    CLI's --top-up.
    """
    found = {}
    for path in sorted(Path(data_dir).glob("*.csv")):
        try:
            info = find_header(path)
            if not info or "PROJ_NUMBER" not in info["header"] \
                    or "PTODATE" not in info["header"]:
                continue
            with export_reader(path) as reader:
                seen = set()
                for row in reader:
                    proj = (row.get("PROJ_NUMBER") or "").strip().upper()
                    try:
                        key = (proj, parse_date(row.get("PFROMDATE")).isoformat(),
                               parse_date(row.get("PTODATE")).isoformat())
                    except ValueError:
                        continue
                    if proj and key not in seen:
                        seen.add(key)
                        found.setdefault(proj, set()).add(key[1:])
        except (OSError, ValueError, csv.Error):
            continue
    return found


# ---------------------------------------------------------------------------
# the URL
# ---------------------------------------------------------------------------

def report_url(projects, from_date, to_date=None, source=None, _open_to_date=False):
    """The BI Publisher URL that streams the detail report as CSV.

    projects  : project codes (list, or a string to be parsed)
    from_date : start of the accounting-date window (any format parse_date takes)
    to_date   : end of the window; None means today
    _open_to_date : internal — leave the to-date value off the end of the URL
                    so a bookmarklet can append today's date at click time.

    Repeating the project parameter ORs the values, so one URL yields one CSV
    covering every project.
    """
    src = source or DEFAULT_SOURCE
    codes = normalize_projects(projects)
    if not codes:
        raise ValueError("Give me at least one project number, e.g. SPN107048.")

    start, end = parse_date(from_date), parse_date(to_date or date.today())
    if end < start:
        raise ValueError("The end of the date window is before its start.")

    # Spaces in the catalog path become '+'; the slashes stay as they are.
    path = "/xmlpserver" + src["reportPath"].replace(" ", "+")
    query = [
        # _xt is what makes BIP render the document instead of the viewer;
        # _xf/_xpt/_xautorun ask for a CSV, document-only, run immediately.
        ("_xt", src["template"]),
        ("_xf", "csv"),
        ("_xpt", "0"),
        ("_xautorun", "true"),
    ]
    query += [(src["projectParam"], code) for code in codes]
    query += [(src["fromParam"], start.strftime(src["dateFormat"])),
              (src["toParam"], "" if _open_to_date else end.strftime(src["dateFormat"]))]
    encoded = "&".join(f"{k}={quote(v, safe='')}" for k, v in query)
    return src["host"] + path + "?" + encoded


def report_links(projects, from_date, to_date=None, combined=True, source=None):
    """One link per download: [{"project", "label", "filename", "url"}, ...].

    combined=True gives a single link covering every project (the report's
    project parameter is multi-valued). combined=False gives one link each,
    for the day a report turns out to be single-select.
    """
    src = source or DEFAULT_SOURCE
    codes = normalize_projects(projects)
    if not codes:
        raise ValueError("Give me at least one project number, e.g. SPN107048.")
    stamp = parse_date(to_date or date.today()).isoformat()
    if combined:
        return [{
            "project": None,
            "label": f"{len(codes)} project{'' if len(codes) == 1 else 's'}",
            "filename": f"detail_{stamp}.csv",
            "url": report_url(codes, from_date, to_date, src),
        }]
    return [{
        "project": code,
        "label": code,
        "filename": f"{code}_{stamp}.csv",
        "url": report_url([code], from_date, to_date, src),
    } for code in codes]


# ---------------------------------------------------------------------------
# bookmarklet (for Safari, where a link click may not carry the SSO session)
# ---------------------------------------------------------------------------

def make_bookmarklet(projects, from_date, to_date=None, combined=True,
                     source=None, label=None):
    """A `javascript:` one-liner that downloads the report from a logged-in tab.

    to_date=None means "today, computed when you click" — so the bookmarklet
    stays useful for months without regenerating it. Clicked while an Oracle
    tab is in front, it builds an `<a download>` and clicks it: navigation
    semantics, which the SSO layer honours (a fetch() would be redirected to
    a login page).
    """
    src = source or DEFAULT_SOURCE
    codes = normalize_projects(projects)
    if not codes:
        raise ValueError("Give me at least one project number, e.g. SPN107048.")

    live_to_date = to_date is None
    groups = [codes] if combined else [[code] for code in codes]
    # Each URL is complete except for the to-date, which the JS appends: a
    # fixed one is baked in here, "today" is filled in at click time.
    urls = [report_url(group, from_date, to_date, src, _open_to_date=True)
            for group in groups]
    names = ["detail" if combined else group[0] for group in groups]

    if live_to_date:
        # today, in the report's format (T) and as a filename stamp (S)
        dates_js = ("var d=new Date(),z=function(n){return('0'+n).slice(-2)};"
                    "var T=z(d.getMonth()+1)+'-'+z(d.getDate())+'-'+d.getFullYear();"
                    "var S=d.getFullYear()+'-'+z(d.getMonth()+1)+'-'+z(d.getDate());")
    else:
        fixed = parse_date(to_date)
        dates_js = ("var T=" + json.dumps(report_date(fixed, src))
                    + ",S=" + json.dumps(fixed.isoformat()) + ";")

    js = ("(function(){" + dates_js
          + "var U=" + json.dumps(urls) + ",N=" + json.dumps(names) + ";"
          "function dl(i){if(i>=U.length)return;"
          "var a=document.createElement('a');a.href=U[i]+encodeURIComponent(T);"
          "a.download=N[i]+'_'+S+'.csv';"
          "document.body.appendChild(a);a.click();a.remove();"
          # Safari drops a burst of downloads, so space them out.
          "if(i+1<U.length)setTimeout(function(){dl(i+1);},1500);}dl(0);})();")
    return "javascript:" + js


def write_installer(bookmarklet, path="install.html", label="Download report CSV"):
    """Write a page with a draggable link.

    Safari won't let you paste `javascript:` into the address bar, but it will
    let you drag a link onto the Favorites bar — which is how you install this.
    """
    page = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><title>{html.escape(label)}</title>
<style>
 body{{font:15px/1.5 system-ui,-apple-system,sans-serif;max-width:34rem;margin:4rem auto;padding:0 1.25rem}}
 .card{{border:1px solid #ddd;border-radius:10px;padding:1.25rem 1.5rem}}
 a.bm{{display:inline-block;padding:.55rem 1rem;border:1px solid #b06a00;border-radius:8px;
      background:#fff7ec;color:#8a4b00;text-decoration:none;font-weight:600}}
 ol{{padding-left:1.2rem}} li{{margin:.4rem 0}}
 code{{background:#f4f4f4;padding:.1rem .3rem;border-radius:4px}}
</style></head>
<body>
 <h2>{html.escape(label)}</h2>
 <div class="card">
  <p><a class="bm" href="{html.escape(bookmarklet, quote=True)}">{html.escape(label)}</a></p>
  <ol>
   <li>Show the Favorites bar: <code>View &rsaquo; Show Favorites Bar</code>.</li>
   <li>Drag the button above onto that bar.</li>
   <li>Open the report system and log in, then click the bookmark. The CSV
       lands in your Downloads folder.</li>
  </ol>
  <p>The date window ends "today" every time you click it, so this stays
     good until your project list changes.</p>
 </div>
</body></html>
"""
    Path(path).write_text(page, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# reading project numbers out of an export you already have
# ---------------------------------------------------------------------------

def projects_from_csv(path, column="Project Number"):
    """Project codes from a PI Dashboard export, in first-seen order.

    Reads the named column when it exists; otherwise scans every cell for the
    SPN pattern, which survives header renames and totals rows.
    """
    found = []
    with export_reader(path) as reader:
        use_column = column in (reader.fieldnames or [])
        for row in reader:
            cells = [row.get(column)] if use_column else row.values()
            found.extend(str(c) for c in cells if c)
    return normalize_projects(found)


def scan_data_dir(data_dir):
    """Projects and the earliest project start date across the CSVs in a folder.

    Returns (projects, earliest_start_iso_or_None). The earliest start makes a
    good default for the report's from-date: the widest window that can hold
    any history worth having.
    """
    projects, starts = [], []
    seen = set()
    for path in sorted(Path(data_dir).glob("*.csv")):
        try:
            with export_reader(path) as reader:
                headers = reader.fieldnames or []
                if "Project Number" not in headers:
                    continue
                for row in reader:
                    for code in normalize_projects([row.get("Project Number") or ""]):
                        if code not in seen:
                            seen.add(code)
                            projects.append(code)
                    try:
                        starts.append(parse_date(row.get("Project Start Date")))
                    except ValueError:
                        pass
        except (OSError, ValueError, csv.Error):
            continue
    return projects, min(starts).isoformat() if starts else None


def default_from_date(data_dir=None, top_up=False, projects=None):
    """A sensible start of the window: the oldest project start we can see,
    else three years back (enough history for burn rates and seasonality).

    top_up=True asks instead for the date that adds the newest charges on
    top of the detail exports already in data_dir (see top_up_from_date),
    falling back to the full window when there are none yet.
    """
    if data_dir and top_up:
        windows = scan_detail_windows(data_dir)
        starts = _project_starts(data_dir)
        wanted = list(projects) if projects else sorted(set(windows) | set(starts))
        found = top_up_from_date(windows, wanted, starts)
        if found:
            return found
    if data_dir:
        _, earliest = scan_data_dir(data_dir)
        if earliest:
            return earliest
    return date(date.today().year - 3, 1, 1).isoformat()


def _project_starts(data_dir):
    """{project: start date} from the PI Dashboard exports in a folder."""
    starts = {}
    for path in sorted(Path(data_dir).glob("*.csv")):
        try:
            with export_reader(path) as reader:
                if "Project Number" not in (reader.fieldnames or []):
                    continue
                for row in reader:
                    for code in normalize_projects([row.get("Project Number") or ""]):
                        try:
                            start = parse_date(row.get("Project Start Date")).isoformat()
                        except ValueError:
                            continue
                        if code not in starts or start < starts[code]:
                            starts[code] = start
        except (OSError, ValueError, csv.Error):
            continue
    return starts


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Build a one-click download link for the expenditure detail report.")
    ap.add_argument("projects", nargs="*",
                    help="project numbers, e.g. SPN107048 SPN107049 (or 107048)")
    ap.add_argument("--from-data", metavar="DIR", dest="from_data",
                    help="take the project list from the CSV exports in DIR (e.g. data)")
    ap.add_argument("--from", dest="from_date",
                    help="start of the date window (default: oldest project start, "
                         "else three years back)")
    ap.add_argument("--top-up", action="store_true", dest="top_up",
                    help="start the window just before the detail exports in "
                         "--from-data DIR end (overlapping %d months, since "
                         "charges post late; the dashboard drops the "
                         "duplicates) instead of at the oldest project start"
                         % OVERLAP_MONTHS)
    ap.add_argument("--to", dest="to_date",
                    help="end of the date window (default: today)")
    ap.add_argument("--per-project", action="store_true",
                    help="one CSV per project instead of one combined CSV")
    ap.add_argument("--open", action="store_true", dest="open_browser",
                    help="open the link in your browser, which starts the download "
                         "(log into the report system first)")
    ap.add_argument("--installer", metavar="HTML",
                    help="write a page with a draggable Safari bookmarklet")
    ap.add_argument("--template", help="report layout name (_xt), e.g. RPT07")
    ap.add_argument("--data", metavar="DIR",
                    help="folder to read report_source.json overrides from")
    args = ap.parse_args(argv)

    source = load_source(args.data or args.from_data, template=args.template)

    projects = normalize_projects(args.projects)
    if args.from_data:
        found, _ = scan_data_dir(args.from_data)
        projects = normalize_projects(projects + found)
    if not projects:
        ap.error("no project numbers given (pass them as arguments or use --from-data DIR)")

    if args.top_up and not args.from_data:
        ap.error("--top-up needs --from-data DIR (the exports to top up)")
    from_date = args.from_date
    if not from_date and args.top_up:
        print("reading the detail exports' date windows…", file=sys.stderr)
        from_date = default_from_date(args.from_data, top_up=True, projects=projects)
    if not from_date:
        from_date = default_from_date(args.from_data)
    try:
        links = report_links(projects, from_date, args.to_date,
                             combined=not args.per_project, source=source)
    except ValueError as exc:
        ap.error(str(exc))

    print(f"{len(projects)} project(s): {', '.join(projects)}", file=sys.stderr)
    print(f"window: {report_date(from_date, source)} → "
          f"{report_date(args.to_date or date.today(), source)}", file=sys.stderr)

    for link in links:
        print(link["url"])
        if args.open_browser:
            webbrowser.open(link["url"])

    if args.installer:
        bookmarklet = make_bookmarklet(projects, from_date,
                                       combined=not args.per_project, source=source)
        write_installer(bookmarklet, args.installer,
                        label=f"Download detail report ({len(projects)} projects)")
        print(f"wrote {args.installer} — open it and drag the button to your "
              f"Favorites bar", file=sys.stderr)


if __name__ == "__main__":
    main()
