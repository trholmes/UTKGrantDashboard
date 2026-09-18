"""Tests for the charge lookup: /api/charges and the transaction plumbing.

    python3 -m unittest discover tests

The section exists to answer "did everything I expect land on this SPN?" —
so the tests focus on nothing being dropped: window bounds are inclusive,
undated lines still show up, and the cartesian-product duplication of the
raw export never double-counts a charge.
"""

import csv
import json
import shutil
import sys
import tempfile
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dashboard  # noqa: E402

DETAIL_COLS = ["P_PROJECT", "PROJ_NUMBER", "PROJ_NAME", "PROJ_STATUS",
               "PROJ_START", "PROJ_END", "SPONSOR_FA_RATE", "AUDIT_FA_RATE",
               "PFROMDATE", "PTODATE",
               "L_TRX_NUM", "L_LAB_TRX", "L_PER_NUM", "L_PER_NAME",
               "L_EXP_DATE", "L_EXP_TYPE", "L_EXP_CAT", "L_EXP_COST",
               "NL_TRX_NUM", "NL_PER_NAME", "NL_EXP_DATE", "NL_EXP_TYPE",
               "NL_EXP_CAT", "NL_EXP_COST"]


def labor_row(proj, trx, person, date, ty, cat, amount):
    meta = [proj, proj, f"{proj} name", "Active", "01/01/2025", "12/31/2027",
            "0.26", "0.26", "01/01/2025", "09/01/2026"]
    return meta + [trx, f"C{trx}R1", "001", person, date, ty, cat, amount,
                   "", "", "", "", "", ""]


def nonlabor_row(proj, trx, person, date, ty, cat, amount):
    meta = [proj, proj, f"{proj} name", "Active", "01/01/2025", "12/31/2027",
            "0.26", "0.26", "01/01/2025", "09/01/2026"]
    return meta + ["", "", "", "", "", "", "", "",
                   trx, person, date, ty, cat, amount]


ROWS = [
    labor_row("SPN900001", "10001", "Riley Park", "2026-06-28",
              "GTA GA GRA Salaries", "Salaries & Wages", "2600.00"),
    labor_row("SPN900001", "10002", "Riley Park", "2026-07-28",
              "GTA GA GRA Salaries", "Salaries & Wages", "2600.00"),
    nonlabor_row("SPN900001", "20001", "", "2026-07-15",
                 "Domestic Travel", "Travel", "812.34"),
    # a credit, and a line the export left undated
    nonlabor_row("SPN900001", "20002", "", "2026-08-02",
                 "Domestic Travel", "Travel", "-100.00"),
    nonlabor_row("SPN900001", "20003", "", "",
                 "Indirect Cost", "Indirect Costs", "50.00"),
    # outside the tested window, and on another project
    nonlabor_row("SPN900001", "20004", "", "2025-01-10",
                 "Domestic Travel", "Travel", "999.00"),
    labor_row("SPN900002", "10003", "Casey Kim", "2026-07-28",
              "GTA GA GRA Salaries", "Salaries & Wages", "3000.00"),
]


def write_detail(data_dir, rows=ROWS, duplicate=False):
    path = Path(data_dir) / "RPT_TEST - Detail.csv"
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(DETAIL_COLS)
        for row in rows:
            w.writerow(row)
            if duplicate:  # the export's cartesian product repeats lines
                w.writerow(row)
    return path


class ChargesResponse(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        write_detail(self.data)

    def lookup(self, **params):
        query = {k: [v] for k, v in params.items()}
        return dashboard.charges_response(query, self.data)

    def test_returns_only_the_asked_for_project(self):
        payload = self.lookup(project="SPN900002")
        self.assertEqual([c["trx"] for c in payload["charges"]], ["10003"])
        self.assertEqual(payload["total"], 3000.00)

    def test_window_bounds_are_inclusive(self):
        # from and to fall exactly on the dates of 10001 and 20001
        payload = self.lookup(project="SPN900001",
                              **{"from": "2026-06-28", "to": "2026-07-15"})
        self.assertEqual({c["trx"] for c in payload["charges"] if c["date"]},
                         {"10001", "20001"})

    def test_undated_lines_are_kept_and_counted(self):
        payload = self.lookup(project="SPN900001",
                              **{"from": "2026-06-01", "to": "2026-08-31"})
        self.assertEqual(payload["undated"], 1)
        self.assertIn("20003", [c["trx"] for c in payload["charges"]])
        # newest first, undated last
        dates = [c["date"] for c in payload["charges"]]
        self.assertEqual(dates, sorted([d for d in dates if d], reverse=True) + [None])

    def test_totals_include_credits(self):
        payload = self.lookup(project="SPN900001",
                              **{"from": "2026-06-01", "to": "2026-08-31"})
        self.assertAlmostEqual(payload["total"],
                               2600 + 2600 + 812.34 - 100 + 50, places=2)

    def test_the_duplicated_export_rows_never_double_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            write_detail(tmp, duplicate=True)
            payload = dashboard.charges_response(
                {"project": ["SPN900002"]}, Path(tmp))
        self.assertEqual(payload["count"], 1)

    def test_normalizes_the_project_code(self):
        payload = self.lookup(project="900002")
        self.assertEqual(payload["project"], "SPN900002")
        self.assertEqual(payload["count"], 1)

    def test_requires_exactly_one_project(self):
        with self.assertRaises(ValueError):
            self.lookup(project="")
        with self.assertRaises(ValueError):
            self.lookup(project="SPN900001 SPN900002")

    def test_rejects_a_backwards_window(self):
        with self.assertRaises(ValueError):
            self.lookup(project="SPN900001",
                        **{"from": "2026-08-01", "to": "2026-07-01"})

    def test_detail_window_reaches_the_front_end(self):
        payload = dashboard.build_payload(self.data)
        proj = next(p for p in payload["projects"] if p["id"] == "SPN900001")
        self.assertEqual(proj["detailWindow"], ["2025-01-01", "2026-09-01"])


class ChargesEndpoint(unittest.TestCase):
    """The /api/charges route itself."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        data = Path(self.tmp.name)
        write_detail(data)
        self.server = ThreadingHTTPServer(
            ("127.0.0.1", 0), dashboard.make_handler(data, None))
        Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = "http://127.0.0.1:%d" % self.server.server_address[1]

    def get(self, path):
        try:
            with urlopen(self.base + path) as response:
                return response.status, json.loads(response.read())
        except HTTPError as err:
            return err.code, json.loads(err.read())

    def test_serves_filtered_charges(self):
        status, payload = self.get(
            "/api/charges?project=SPN900001&from=2026-07-01&to=2026-07-31")
        self.assertEqual(status, 200)
        self.assertEqual({c["trx"] for c in payload["charges"] if c["date"]},
                         {"10002", "20001"})

    def test_reports_a_bad_request_instead_of_crashing(self):
        status, payload = self.get("/api/charges?project=&from=whenever")
        self.assertEqual(status, 400)
        self.assertIn("project", payload["error"])
