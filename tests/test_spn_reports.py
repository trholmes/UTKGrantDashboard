"""Tests for the report download links (spn_reports) and their dashboard endpoints.

    python3 -m unittest discover tests

The URL details asserted here were confirmed against the live reporting system,
and getting them wrong fails quietly — a downloaded CSV full of $0 totals, or
the report viewer opening instead of a download. Hence the fussiness.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError
from urllib.parse import parse_qsl, urlparse
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dashboard          # noqa: E402
import spn_reports as sr  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "sample_dashboard.csv"


def query_of(url):
    """The URL's query as a list of (key, value) — repeats preserved."""
    return parse_qsl(urlparse(url).query, keep_blank_values=True)


class ProjectNumbers(unittest.TestCase):
    def test_dedupes_and_keeps_order(self):
        self.assertEqual(sr.normalize_projects(["SPN2", "SPN1", "SPN2"]), ["SPN2", "SPN1"])

    def test_accepts_one_typed_string(self):
        self.assertEqual(sr.normalize_projects("spn107048, SPN107049 107050"),
                         ["SPN107048", "SPN107049", "SPN107050"])

    def test_bare_numbers_get_the_prefix(self):
        self.assertEqual(sr.normalize_projects(["107048"]), ["SPN107048"])

    def test_ignores_prose_and_short_numbers(self):
        self.assertEqual(sr.normalize_projects(["no codes here 42"]), [])

    def test_reads_a_dashboard_export(self):
        self.assertEqual(sr.projects_from_csv(FIXTURE),
                         ["SPN900001", "SPN900002", "SPN900003"])

    def test_falls_back_to_scanning_when_the_header_is_renamed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "renamed.csv"
            path.write_text("Code,Note\nSPN123456,x\nSPN123456,y\nSPN654321,z\n",
                            encoding="utf-8")
            self.assertEqual(sr.projects_from_csv(path), ["SPN123456", "SPN654321"])

    def test_scan_data_dir_finds_projects_and_the_oldest_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            shutil.copy(FIXTURE, Path(tmp) / "dash.csv")
            projects, earliest = sr.scan_data_dir(tmp)
        self.assertEqual(projects, ["SPN900001", "SPN900002", "SPN900003"])
        self.assertEqual(earliest, "2022-01-01")   # the closed award's start


class Dates(unittest.TestCase):
    def test_accepts_iso_dashes_and_slashes(self):
        for value in ("2025-01-31", "01-31-2025", "01/31/2025"):
            self.assertEqual(sr.parse_date(value), date(2025, 1, 31), value)

    def test_report_format_is_mm_dd_yyyy_with_dashes(self):
        # ISO or slashes are mis-parsed by the report (year 0169, $0 totals)
        self.assertEqual(sr.report_date("2025-01-31"), "01-31-2025")

    def test_rejects_nonsense(self):
        for value in ("", None, "last tuesday", "2025-13-01"):
            with self.assertRaises(ValueError):
                sr.parse_date(value)


class ReportUrl(unittest.TestCase):
    def setUp(self):
        self.url = sr.report_url(["SPN900001", "SPN900002"],
                                 from_date="2024-01-01", to_date="2026-07-31")

    def test_carries_the_parameters_that_make_it_stream_a_csv(self):
        query = dict(query_of(self.url))
        # _xt (the layout name) is what makes BI Publisher render the document
        # instead of opening the interactive viewer and waiting for Apply
        self.assertEqual(query["_xt"], "RPT07")
        self.assertEqual(query["_xf"], "csv")
        self.assertEqual(query["_xpt"], "0")
        self.assertEqual(query["_xautorun"], "true")

    def test_repeats_the_project_parameter_to_or_the_projects(self):
        projects = [v for k, v in query_of(self.url) if k == "P_PROJECT"]
        self.assertEqual(projects, ["SPN900001", "SPN900002"])

    def test_dates_are_in_the_report_format(self):
        query = dict(query_of(self.url))
        self.assertEqual(query["P_FROM_DATE"], "01-01-2024")
        self.assertEqual(query["P_TO_DATE"], "07-31-2026")

    def test_spaces_in_the_catalog_path_become_plus(self):
        path = urlparse(self.url).path
        self.assertTrue(path.startswith("/xmlpserver/Custom/Projects/"), path)
        self.assertIn("RPT_GMS_007+-+Sponsored+Project+Detail+Report.xdo", path)
        self.assertNotIn(" ", path)
        self.assertNotIn("%20", path)

    def test_to_date_defaults_to_today(self):
        url = sr.report_url(["SPN900001"], from_date="2024-01-01")
        self.assertEqual(dict(query_of(url))["P_TO_DATE"],
                         date.today().strftime("%m-%d-%Y"))

    def test_refuses_a_backwards_window(self):
        with self.assertRaises(ValueError):
            sr.report_url(["SPN900001"], from_date="2026-01-01", to_date="2025-01-01")

    def test_refuses_an_empty_project_list(self):
        with self.assertRaises(ValueError):
            sr.report_url([], from_date="2024-01-01")

    def test_source_is_configurable(self):
        source = sr.load_source(None, template="RPT7")
        url = sr.report_url(["SPN900001"], from_date="2024-01-01", source=source)
        self.assertEqual(dict(query_of(url))["_xt"], "RPT7")

    def test_source_file_overrides_the_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / sr.SOURCE_FILE).write_text(
                json.dumps({"template": "RPT9", "host": "https://elsewhere.example"}),
                encoding="utf-8")
            source = sr.load_source(tmp)
        self.assertEqual(source["template"], "RPT9")
        self.assertEqual(source["host"], "https://elsewhere.example")
        self.assertEqual(source["projectParam"], "P_PROJECT")  # untouched keys keep defaults

    def test_a_broken_source_file_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / sr.SOURCE_FILE).write_text("{not json", encoding="utf-8")
            self.assertEqual(sr.load_source(tmp)["template"],
                             sr.DEFAULT_SOURCE["template"])


class ReportLinks(unittest.TestCase):
    def test_combined_is_one_link_for_every_project(self):
        links = sr.report_links(["SPN900001", "SPN900002"], "2024-01-01")
        self.assertEqual(len(links), 1)
        self.assertEqual([v for k, v in query_of(links[0]["url"]) if k == "P_PROJECT"],
                         ["SPN900001", "SPN900002"])

    def test_per_project_is_one_link_each(self):
        links = sr.report_links(["SPN900001", "SPN900002"], "2024-01-01", combined=False)
        self.assertEqual([l["project"] for l in links], ["SPN900001", "SPN900002"])
        self.assertEqual([l["label"] for l in links], ["SPN900001", "SPN900002"])
        for link in links:
            self.assertEqual([v for k, v in query_of(link["url"]) if k == "P_PROJECT"],
                             [link["project"]])

    def test_filenames_are_dated(self):
        links = sr.report_links(["SPN900001"], "2024-01-01", to_date="2026-07-31")
        self.assertEqual(links[0]["filename"], "detail_2026-07-31.csv")


class Bookmarklet(unittest.TestCase):
    def test_is_a_single_javascript_line(self):
        js = sr.make_bookmarklet(["SPN900001", "SPN900002"], "2024-01-01")
        self.assertTrue(js.startswith("javascript:"))
        self.assertNotIn("\n", js)

    def test_navigates_instead_of_fetching(self):
        # a background fetch is bounced to a login page by the SSO layer, so the
        # download has to happen through navigation semantics
        js = sr.make_bookmarklet(["SPN900001"], "2024-01-01")
        self.assertNotIn("fetch(", js)
        self.assertIn("a.href=", js)
        self.assertIn("a.download=", js)

    def test_open_ended_window_computes_today_at_click_time(self):
        js = sr.make_bookmarklet(["SPN900001"], "2024-01-01")
        self.assertIn("new Date()", js)
        self.assertIn("P_TO_DATE=", js)
        self.assertNotIn("P_TO_DATE=" + date.today().strftime("%m-%d-%Y"), js)

    def test_a_fixed_window_is_baked_in(self):
        js = sr.make_bookmarklet(["SPN900001"], "2024-01-01", to_date="2026-07-31")
        self.assertIn('var T="07-31-2026"', js)
        self.assertNotIn("new Date()", js)

    def test_per_project_mode_downloads_each_in_turn(self):
        js = sr.make_bookmarklet(["SPN900001", "SPN900002"], "2024-01-01", combined=False)
        self.assertIn("SPN900001", js)
        self.assertIn("SPN900002", js)
        self.assertIn("setTimeout", js)   # Safari drops a burst of downloads

    def test_generated_javascript_parses(self):
        for combined in (True, False):
            for to_date in (None, "2026-07-31"):
                js = sr.make_bookmarklet(["SPN900001", "SPN900002"], "2024-01-01",
                                         to_date=to_date, combined=combined)
                self.assertTrue(js_parses(js[len("javascript:"):]),
                                f"combined={combined} to_date={to_date}")

    def test_installer_page_escapes_its_label(self):
        js = sr.make_bookmarklet(["SPN900001"], "2024-01-01")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "install.html"
            sr.write_installer(js, out, label="Download <b>now</b>")
            page = out.read_text(encoding="utf-8")
        self.assertIn("Download &lt;b&gt;now&lt;/b&gt;", page)
        self.assertNotIn("<b>now</b>", page)
        self.assertIn("javascript:", page)


def js_parses(source):
    """True if the JS parses. Uses node when present, else balances brackets."""
    node = shutil.which("node")
    if node:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bookmarklet.js"
            path.write_text(source, encoding="utf-8")
            return subprocess.run([node, "--check", str(path)],
                                  capture_output=True).returncode == 0
    opener = {")": "(", "]": "[", "}": "{"}
    stack = []
    for char in source:
        if char in "([{":
            stack.append(char)
        elif char in ")]}":
            if not stack or stack.pop() != opener[char]:
                return False
    return not stack


class Cli(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parent.parent / "spn_reports.py"),
             *args], capture_output=True, text=True)

    def test_prints_a_url_for_the_projects_given(self):
        done = self.run_cli("107048", "SPN107049", "--from", "2025-01-01")
        self.assertEqual(done.returncode, 0, done.stderr)
        url = done.stdout.strip()
        self.assertEqual([v for k, v in query_of(url) if k == "P_PROJECT"],
                         ["SPN107048", "SPN107049"])
        self.assertIn("2 project(s)", done.stderr)

    def test_takes_the_project_list_from_an_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            shutil.copy(FIXTURE, Path(tmp) / "dash.csv")
            done = self.run_cli("--from-data", tmp)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual([v for k, v in query_of(done.stdout.strip()) if k == "P_PROJECT"],
                         ["SPN900001", "SPN900002", "SPN900003"])
        # the window defaults to the oldest project start in the export
        self.assertIn("01-01-2022", done.stderr)

    def test_per_project_prints_one_url_each(self):
        done = self.run_cli("SPN1", "SPN2", "--from", "2025-01-01", "--per-project")
        self.assertEqual(len(done.stdout.strip().splitlines()), 2)

    def test_writes_a_bookmarklet_installer(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "install.html"
            done = self.run_cli("SPN1", "--from", "2025-01-01", "--installer", str(out))
            self.assertEqual(done.returncode, 0, done.stderr)
            self.assertIn("javascript:", out.read_text(encoding="utf-8"))

    def test_complains_when_given_no_projects(self):
        done = self.run_cli("--from", "2025-01-01")
        self.assertEqual(done.returncode, 2)
        self.assertIn("no project numbers", done.stderr)


class DashboardEndpoints(unittest.TestCase):
    """The two halves the front-end talks to: link building and the inbox."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data = Path(self.tmp.name) / "data"
        self.inbox = Path(self.tmp.name) / "Downloads"
        self.data.mkdir()
        self.inbox.mkdir()
        self.addCleanup(self.tmp.cleanup)

    def drop_in_inbox(self, name, source=FIXTURE):
        shutil.copy(source, self.inbox / name)
        return self.inbox / name

    # ----- /api/report-links -----

    def test_report_links_response(self):
        payload = dashboard.report_links_response(
            {"projects": ["SPN900001 107049"], "from": ["2024-01-01"],
             "to": ["2026-07-31"], "mode": ["combined"]}, self.data)
        self.assertEqual(payload["projects"], ["SPN900001", "SPN107049"])
        self.assertEqual((payload["from"], payload["to"]), ("01-01-2024", "07-31-2026"))
        self.assertEqual(len(payload["links"]), 1)
        self.assertTrue(payload["bookmarklet"].startswith("javascript:"))
        # the bookmarklet is deliberately open-ended so it stays usable
        self.assertIn("new Date()", payload["bookmarklet"])

    def test_report_links_honours_mode_and_template(self):
        payload = dashboard.report_links_response(
            {"projects": ["SPN900001,SPN900002"], "from": ["2024-01-01"],
             "mode": ["per-project"], "template": ["RPT7"]}, self.data)
        self.assertEqual(len(payload["links"]), 2)
        self.assertEqual(payload["template"], "RPT7")
        self.assertEqual(dict(query_of(payload["links"][0]["url"]))["_xt"], "RPT7")

    def test_blank_parameters_fall_back_to_the_defaults(self):
        payload = dashboard.report_links_response(
            {"projects": ["SPN900001"], "from": ["2024-01-01"],
             "to": [""], "template": [""]}, self.data)
        self.assertEqual(payload["to"], date.today().strftime("%m-%d-%Y"))
        self.assertEqual(payload["template"], sr.DEFAULT_SOURCE["template"])

    # ----- /api/inbox and /api/import -----

    def test_inbox_lists_recognized_exports_only(self):
        self.drop_in_inbox("PI Dashboard.csv")
        (self.inbox / "shopping list.csv").write_text("eggs,milk\n", encoding="utf-8")
        (self.inbox / "notes.txt").write_text("hello", encoding="utf-8")
        found = dashboard.scan_inbox(self.inbox, self.data)
        self.assertEqual([f["name"] for f in found], ["PI Dashboard.csv"])
        self.assertEqual(found[0]["type"], "pi_dashboard")
        self.assertFalse(found[0]["imported"])

    def test_inbox_flags_a_file_still_being_written(self):
        done = self.drop_in_inbox("done.csv")
        finished_a_minute_ago = done.stat().st_mtime - 60
        os.utime(done, (finished_a_minute_ago, finished_a_minute_ago))
        self.drop_in_inbox("in-flight.csv")   # written just now
        by_name = {f["name"]: f for f in dashboard.scan_inbox(self.inbox, self.data)}
        self.assertTrue(by_name["done.csv"]["settled"])
        self.assertFalse(by_name["in-flight.csv"]["settled"])

    def test_inbox_survives_a_file_vanishing_mid_scan(self):
        missing = self.inbox / "gone.csv"
        missing.symlink_to(self.inbox / "nothing-here.csv")
        self.drop_in_inbox("PI Dashboard.csv")
        self.assertEqual([f["name"] for f in dashboard.scan_inbox(self.inbox, self.data)],
                         ["PI Dashboard.csv"])

    def test_inbox_marks_what_is_already_imported(self):
        self.drop_in_inbox("PI Dashboard.csv")
        shutil.copy(FIXTURE, self.data / "PI Dashboard.csv")
        self.assertTrue(dashboard.scan_inbox(self.inbox, self.data)[0]["imported"])

    def test_inbox_is_empty_when_not_watching(self):
        self.assertEqual(dashboard.scan_inbox(None, self.data), [])

    def test_import_copies_into_the_data_folder(self):
        self.drop_in_inbox("PI Dashboard.csv")
        result = dashboard.import_from_inbox(["PI Dashboard.csv"], self.inbox, self.data)
        self.assertEqual(result["imported"], ["PI Dashboard.csv"])
        self.assertTrue((self.data / "PI Dashboard.csv").is_file())
        self.assertTrue((self.inbox / "PI Dashboard.csv").is_file())  # original stays

    def test_import_skips_a_file_already_there(self):
        self.drop_in_inbox("PI Dashboard.csv")
        dashboard.import_from_inbox(["PI Dashboard.csv"], self.inbox, self.data)
        again = dashboard.import_from_inbox(["PI Dashboard.csv"], self.inbox, self.data)
        self.assertEqual(again, {"imported": [], "skipped": ["PI Dashboard.csv"]})

    def test_import_keeps_both_when_a_same_named_export_differs(self):
        self.drop_in_inbox("export.csv")
        dashboard.import_from_inbox(["export.csv"], self.inbox, self.data)
        with open(self.inbox / "export.csv", "a", encoding="utf-8") as f:
            f.write("SPN900004,AGENCY 444444 Doe,\"Doe, Jane\",Active,01/01/2024,"
                    "12/31/2027,1,1,0,,0,0,01. Salaries & Wages\n")
        result = dashboard.import_from_inbox(["export.csv"], self.inbox, self.data)
        self.assertEqual(result["imported"], ["export (2).csv"])

    def test_import_refuses_unrecognized_files(self):
        (self.inbox / "shopping list.csv").write_text("eggs,milk\n", encoding="utf-8")
        result = dashboard.import_from_inbox(["shopping list.csv"], self.inbox, self.data)
        self.assertEqual(result, {"imported": [], "skipped": ["shopping list.csv"]})
        self.assertEqual(list(self.data.iterdir()), [])

    def test_import_refuses_paths_outside_the_inbox(self):
        secret = Path(self.tmp.name) / "secret.csv"
        shutil.copy(FIXTURE, secret)          # recognized, but not in the inbox
        result = dashboard.import_from_inbox(
            ["../secret.csv", str(secret), "/etc/passwd"], self.inbox, self.data)
        self.assertEqual(result["imported"], [])
        self.assertEqual(list(self.data.iterdir()), [])

    def test_import_explains_itself_when_not_watching(self):
        with self.assertRaises(ValueError):
            dashboard.import_from_inbox(["PI Dashboard.csv"], None, self.data)


class Server(unittest.TestCase):
    """The HTTP layer: it must answer this page and nobody else."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        data = Path(self.tmp.name)
        shutil.copy(FIXTURE, data / "dash.csv")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), dashboard.make_handler(data, None))
        Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = "http://127.0.0.1:%d" % self.server.server_address[1]

    def get(self, path, headers=None):
        request = Request(self.base + path, headers=headers or {})
        try:
            with urlopen(request) as response:
                return response.status, json.loads(response.read())
        except HTTPError as err:
            return err.code, json.loads(err.read())

    def test_serves_the_page_and_its_data(self):
        with urlopen(self.base + "/") as response:
            self.assertIn(b"Get fresh data", response.read())
        status, payload = self.get("/api/data")
        self.assertEqual(status, 200)
        self.assertEqual(len(payload["projects"]), 3)

    def test_builds_links_over_http(self):
        status, payload = self.get("/api/report-links?projects=SPN900001&from=2024-01-01")
        self.assertEqual(status, 200)
        self.assertIn("P_PROJECT=SPN900001", payload["links"][0]["url"])

    def test_reports_a_bad_request_instead_of_crashing(self):
        status, payload = self.get("/api/report-links?projects=SPN1&from=whenever")
        self.assertEqual(status, 400)
        self.assertIn("whenever", payload["error"])

    def test_refuses_a_request_aimed_at_localhost_from_another_page(self):
        # a page on the web can point a form or fetch at 127.0.0.1; it may not
        # read your finances or move files around
        status, payload = self.get("/api/data", {"Origin": "https://evil.example"})
        self.assertEqual(status, 403)
        self.assertIn("evil.example", payload["error"])

    def test_refuses_a_hostname_that_merely_resolves_here(self):
        status, _ = self.get("/api/data", {"Host": "grants.evil.example"})
        self.assertEqual(status, 403)

    def test_allows_the_dashboards_own_origin(self):
        status, _ = self.get("/api/data", {"Origin": self.base})
        self.assertEqual(status, 200)

    def test_unknown_paths_are_not_found(self):
        self.assertEqual(self.get("/api/whatever")[0], 404)

    def test_static_files_stay_inside_the_static_folder(self):
        self.assertEqual(self.get("/static/../dashboard.py")[0], 404)


class Payload(unittest.TestCase):
    def test_report_source_reaches_the_front_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            shutil.copy(FIXTURE, Path(tmp) / "dash.csv")
            payload = dashboard.build_payload(Path(tmp))
        source = payload["reportSource"]
        self.assertEqual(source["template"], sr.DEFAULT_SOURCE["template"])
        self.assertTrue(source["piDashboardUrl"].startswith("https://"))
        self.assertEqual([p["id"] for p in payload["projects"]],
                         ["SPN900001", "SPN900002", "SPN900003"])


if __name__ == "__main__":
    unittest.main()
