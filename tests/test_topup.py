"""Topping up: the usual way to get fresh data is a download of just the
newest months, dropped in data/ next to the exports already there.

    python3 -m unittest discover tests

Two halves: the download link should know where the exports in hand end
and start a little before that (charges post late), and the parser should
count a charge that is in both the old export and the top-up exactly once
— even when the two runs format a cell differently.
"""

import csv
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dashboard  # noqa: E402
import spn_reports as sr  # noqa: E402

COLS = ["P_PROJECT", "PROJ_NUMBER", "PROJ_NAME", "PROJ_START", "PROJ_END",
        "PFROMDATE", "PTODATE",
        "L_TRX_NUM", "L_LAB_TRX", "L_PER_NUM", "L_PER_NAME", "L_EXP_DATE",
        "L_EXP_TYPE", "L_EXP_CAT", "L_EXP_COST",
        "NL_TRX_NUM", "NL_PER_NAME", "NL_EXP_DATE", "NL_EXP_TYPE",
        "NL_EXP_CAT", "NL_EXP_COST"]


def labor(proj, window, trx, person, when, amount):
    return [proj, proj, f"{proj} name", "01/01/2024", "12/31/2027", *window,
            trx, f"C{trx}R1", "001", person, when, "GTA GA GRA Salaries",
            "Salaries & Wages", amount, "", "", "", "", "", ""]


def nonlabor(proj, window, trx, when, amount, ty="Domestic Travel", cat="Travel"):
    return [proj, proj, f"{proj} name", "01/01/2024", "12/31/2027", *window,
            "", "", "", "", "", "", "", "",
            trx, "", when, ty, cat, amount]


def write_detail(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(COLS)
        w.writerows(rows)
    return path


FULL = ("01/01/2024", "09/01/2026")      # the big export, pulled in September
TOP_UP = ("06/01/2026", "10/13/2026")    # the top-up, pulled in October


class Windows(unittest.TestCase):
    def test_touching_and_overlapping_windows_merge(self):
        merged = sr.merge_windows([("2026-06-01", "2026-10-13"),
                                   ("2024-01-01", "2026-09-01"),
                                   ("2026-10-14", "2026-11-01")])
        self.assertEqual(merged, [["2024-01-01", "2026-11-01"]])

    def test_a_hole_between_windows_is_a_gap(self):
        windows = [("2024-01-01", "2026-06-30"), ("2026-09-01", "2026-10-13")]
        self.assertEqual(sr.merge_windows(windows),
                         [["2024-01-01", "2026-06-30"], ["2026-09-01", "2026-10-13"]])
        self.assertEqual(sr.window_gaps(windows), [["2026-07-01", "2026-08-31"]])

    def test_no_windows_is_no_coverage(self):
        self.assertEqual(sr.merge_windows([]), [])
        self.assertEqual(sr.window_gaps(None), [])

    def test_top_up_starts_a_few_months_before_the_coverage_ends(self):
        # the first of the month, OVERLAP_MONTHS back from the month it ends in
        self.assertEqual(sr.OVERLAP_MONTHS, 3)
        self.assertEqual(sr.top_up_start("2026-10-13"), "2026-07-01")
        self.assertEqual(sr.top_up_start("2026-01-31"), "2025-10-01")
        self.assertEqual(sr.top_up_start("2026-10-13", overlap_months=0), "2026-10-01")


class TopUpFromDate(unittest.TestCase):
    WINDOWS = {"SPN1": {("2024-01-01", "2026-09-01")},
               "SPN2": {("2024-01-01", "2026-06-30"), ("2026-09-01", "2026-10-13")}}

    def test_overlaps_the_end_of_the_coverage(self):
        self.assertEqual(sr.top_up_from_date(self.WINDOWS, ["SPN1"]), "2026-06-01")

    def test_a_gap_is_filled_first(self):
        self.assertEqual(sr.top_up_from_date(self.WINDOWS, ["SPN2"]), "2026-07-01")

    def test_the_earliest_project_wins_so_one_download_covers_all(self):
        self.assertEqual(sr.top_up_from_date(self.WINDOWS, ["SPN1", "SPN2"]), "2026-06-01")

    def test_an_award_without_history_gets_its_whole_history(self):
        got = sr.top_up_from_date(self.WINDOWS, ["SPN1", "SPN3"],
                                  starts={"SPN3": "2025-03-01"})
        self.assertEqual(got, "2025-03-01")

    def test_nothing_known_is_none(self):
        self.assertIsNone(sr.top_up_from_date({}, ["SPN9"]))
        self.assertIsNone(sr.top_up_from_date(self.WINDOWS, ["SPN9"], starts={}))


class ScanningExports(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        write_detail(self.data / "RPT_A - detail.csv", [
            nonlabor("SPN900001", FULL, "1", "2026-07-15", "10.00"),
            nonlabor("SPN900002", FULL, "2", "2026-07-15", "10.00"),
        ])
        write_detail(self.data / "RPT_B - top up.csv", [
            nonlabor("SPN900001", TOP_UP, "3", "2026-10-02", "10.00"),
        ])
        (self.data / "dash.csv").write_text(
            "Project Number,Project Name,Project Start Date,Expenditure Category,Budget\n"
            "SPN900001,Grant A,01/01/2024,01. Travel,100\n"
            "SPN900003,Grant C,03/01/2025,01. Travel,100\n", encoding="utf-8")

    def test_windows_are_read_per_project(self):
        self.assertEqual(sr.scan_detail_windows(self.data), {
            "SPN900001": {("2024-01-01", "2026-09-01"), ("2026-06-01", "2026-10-13")},
            "SPN900002": {("2024-01-01", "2026-09-01")},
        })

    def test_default_from_date_tops_up_on_request(self):
        # the usual default is still the oldest award start...
        self.assertEqual(sr.default_from_date(self.data), "2024-01-01")
        # ...a top-up starts before the earliest coverage end among the
        # projects asked for (SPN900002's, pulled in September)
        self.assertEqual(sr.default_from_date(self.data, top_up=True), "2025-03-01")
        self.assertEqual(sr.default_from_date(self.data, top_up=True,
                                              projects=["SPN900001"]), "2026-07-01")
        self.assertEqual(sr.default_from_date(self.data, top_up=True,
                                              projects=["SPN900001", "SPN900002"]),
                         "2026-06-01")

    def test_a_folder_without_detail_exports_falls_back_to_the_full_window(self):
        for p in self.data.glob("RPT*.csv"):
            p.unlink()
        self.assertEqual(sr.default_from_date(self.data, top_up=True), "2024-01-01")

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parent.parent / "spn_reports.py"),
             *args], capture_output=True, text=True)

    def test_cli_top_up_flag(self):
        done = self.run_cli("--from-data", str(self.data), "--top-up")
        self.assertEqual(done.returncode, 0, done.stderr)
        # SPN900003 has no detail history, so its award start sets the window
        self.assertIn("window: 03-01-2025", done.stderr)
        done = self.run_cli("SPN900001", "--from-data", str(self.data), "--top-up")
        # ...but the dashboard export adds its projects, so the same window
        self.assertIn("window: 03-01-2025", done.stderr)

    def test_cli_top_up_needs_a_folder(self):
        done = self.run_cli("SPN1", "--top-up")
        self.assertEqual(done.returncode, 2)
        self.assertIn("--from-data", done.stderr)


class MergingATopUp(unittest.TestCase):
    """A big export plus a top-up that overlaps it: every charge once."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        write_detail(self.data / "RPT_A - detail.csv", [
            labor("SPN900001", FULL, "10001", "Riley Park", "2026-06-28", "2600.00"),
            labor("SPN900001", FULL, "10002", "Riley Park", "2026-07-28", "2600.00"),
            nonlabor("SPN900001", FULL, "20001", "2026-07-15", "812.34"),
            nonlabor("SPN900001", FULL, "20002", "2026-08-20", "100.00"),
            nonlabor("SPN900002", FULL, "20009", "2026-08-20", "5.00"),
        ])
        (self.data / "dash.csv").write_text(
            "Project Number,Project Name,Project Start Date,Project Finish Date,"
            "Project Status,Expenditure Category,Budget\n"
            "SPN900001,Grant A,01/01/2024,12/31/2027,Active,01. Travel,100\n"
            "SPN900002,Grant B,01/01/2024,12/31/2027,Active,01. Travel,100\n"
            "SPN900003,Grant C,03/01/2025,12/31/2027,Active,01. Travel,100\n",
            encoding="utf-8")

    def by_id(self):
        return {p["id"]: p for p in dashboard.build_payload(self.data)["projects"]}

    def test_before_the_top_up(self):
        p = self.by_id()["SPN900001"]
        self.assertEqual(p["detailWindow"], ["2024-01-01", "2026-09-01"])
        self.assertEqual(p["detailGaps"], [])
        self.assertEqual(p["latestCharge"], "2026-08-20")
        self.assertEqual(p["topUpFrom"], "2026-06-01")
        # no history yet: the award start
        self.assertEqual(self.by_id()["SPN900003"]["topUpFrom"], "2025-03-01")
        self.assertIsNone(self.by_id()["SPN900003"]["detailWindow"])

    def test_the_overlap_counts_each_charge_once(self):
        # the top-up repeats July and August — in the report's other habits:
        # slash dates, no trailing zeros, padded cells — and adds October
        write_detail(self.data / "RPT_B - top up.csv", [
            labor("SPN900001", TOP_UP, "10002", "Riley Park", "07/28/2026", "2600"),
            nonlabor("SPN900001", TOP_UP, "20001", "07/15/2026", " 812.34 "),
            nonlabor("SPN900001", TOP_UP, "20002", "08/20/2026", "100"),
            labor("SPN900001", TOP_UP, "10003", "Riley Park", "10/01/2026", "2600.00"),
            nonlabor("SPN900001", TOP_UP, "20003", "10/05/2026", "40.00"),
        ])
        p = self.by_id()["SPN900001"]
        self.assertEqual(p["monthly"], {"2026-06": 2600.0, "2026-07": 3412.34,
                                        "2026-08": 100.0, "2026-10": 2640.0})
        self.assertEqual(len([t for t in dashboard.load_transactions(self.data)
                              if t["project"] == "SPN900001"]), 6)
        self.assertEqual(p["detailWindow"], ["2024-01-01", "2026-10-13"])
        self.assertEqual(p["detailGaps"], [])
        self.assertEqual(p["latestCharge"], "2026-10-05")
        self.assertEqual(p["topUpFrom"], "2026-07-01")
        # the other award was not in the top-up: its coverage is unchanged
        self.assertEqual(self.by_id()["SPN900002"]["detailWindow"],
                         ["2024-01-01", "2026-09-01"])

    def test_a_top_up_that_starts_late_leaves_a_hole_and_says_so(self):
        late = ("10/01/2026", "10/13/2026")
        write_detail(self.data / "RPT_B - top up.csv", [
            nonlabor("SPN900001", late, "20003", "10/05/2026", "40.00"),
        ])
        payload = dashboard.build_payload(self.data)
        p = {p["id"]: p for p in payload["projects"]}["SPN900001"]
        self.assertEqual(p["detailWindow"], ["2024-01-01", "2026-10-13"])
        self.assertEqual(p["detailGaps"], [["2026-09-02", "2026-09-30"]])
        # the next top-up starts where the hole does
        self.assertEqual(p["topUpFrom"], "2026-09-02")
        self.assertEqual(payload["coverage"]["gaps"], {"SPN900001": [["2026-09-02", "2026-09-30"]]})

    def test_the_folder_wide_coverage(self):
        cov = dashboard.build_payload(self.data)["coverage"]
        self.assertEqual(cov["from"], "2024-01-01")
        self.assertEqual(cov["to"], "2026-09-01")
        self.assertEqual(cov["latestCharge"], "2026-08-20")
        self.assertEqual(cov["gaps"], {})
        self.assertEqual(cov["overlapMonths"], sr.OVERLAP_MONTHS)

    def test_no_detail_export_means_no_coverage(self):
        (self.data / "RPT_A - detail.csv").unlink()
        self.assertIsNone(dashboard.build_payload(self.data)["coverage"])
