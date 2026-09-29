"""Reloading: parse each detail export once, one build at a time, and say
what is going on meanwhile.

    python3 -m unittest discover tests

The detail export can be hundreds of MB. Before these rules a reload
re-parsed it every time, and every extra click on Reload (or a page
refresh) started another parse alongside the first, so the page looked
hung until the dashboard was restarted.
"""

import csv
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dashboard  # noqa: E402

COLS = ["P_PROJECT", "PROJ_NUMBER", "PROJ_NAME", "PROJ_START", "PROJ_END",
        "PFROMDATE", "PTODATE", "L_TRX_NUM", "L_EXP_COST",
        "NL_TRX_NUM", "NL_EXP_DATE", "NL_EXP_TYPE", "NL_EXP_CAT", "NL_EXP_COST"]


def write_detail(path, project, trx_numbers, fa_rate=None):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(COLS + (["SPONSOR_FA_RATE"] if fa_rate else []))
        for n in trx_numbers:
            w.writerow([project, project, f"{project} name", "01/01/2024", "12/31/2027",
                        "01/01/2024", "09/01/2026", "", "",
                        str(n), "2026-07-15", "Domestic Travel", "Travel", "10.00"]
                       + ([fa_rate] if fa_rate else []))


def bump_mtime(path):
    st = path.stat()
    import os
    os.utime(path, (st.st_atime + 10, st.st_mtime + 10))


class PerFileCache(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data = Path(self.tmp.name)
        self.big = self.data / "RPT_A - detail.csv"
        write_detail(self.big, "SPN900001", [1, 2, 3])
        self.parses = []
        real = dashboard.parse_detail

        def counting(path, *args):
            self.parses.append(Path(path).name)
            return real(path, *args)
        patcher = mock.patch.object(dashboard, "parse_detail", counting)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_new_dashboard_export_does_not_reparse_the_detail_file(self):
        dashboard.build_payload(self.data)
        self.assertEqual(self.parses, ["RPT_A - detail.csv"])
        (self.data / "PI Dashboard.csv").write_text(
            "Project Number,Project Name,Expenditure Category,Budget\n"
            "SPN900001,Grant,01. Travel,100\n", encoding="utf-8")
        payload = dashboard.build_payload(self.data)
        self.assertEqual(self.parses, ["RPT_A - detail.csv"])   # still just the once
        self.assertEqual(payload["projects"][0]["totals"]["budget"], 100)
        self.assertTrue(payload["projects"][0]["hasDetail"])

    def test_a_changed_detail_file_is_reparsed_and_others_are_not(self):
        other = self.data / "RPT_B - detail.csv"
        write_detail(other, "SPN900002", [9])
        dashboard.build_payload(self.data)
        write_detail(self.big, "SPN900001", [1, 2, 3, 4])
        bump_mtime(self.big)
        payload = dashboard.build_payload(self.data)
        self.assertEqual(self.parses, ["RPT_A - detail.csv", "RPT_B - detail.csv",
                                       "RPT_A - detail.csv"])
        by_id = {p["id"]: p for p in payload["projects"]}
        self.assertEqual(len(dashboard.load_transactions(self.data)), 5)
        self.assertEqual(by_id["SPN900001"]["monthly"]["2026-07"], 40.0)

    def test_files_merge_the_same_as_before_with_first_seen_winning(self):
        # the same charge in two overlapping exports counts once; the F&A
        # rate comes from whichever file states it
        other = self.data / "RPT_B - detail.csv"
        write_detail(other, "SPN900001", [3, 4], fa_rate="0.55")
        dashboard.build_payload(self.data)
        self.assertEqual(len(dashboard.load_transactions(self.data)), 4)
        proj = dashboard.build_payload(self.data)["projects"][0]
        self.assertEqual(proj["faRate"], 0.55)
        self.assertEqual(proj["detailWindow"], ["2024-01-01", "2026-09-01"])

    def test_a_removed_file_drops_out_of_the_cache(self):
        dashboard.build_payload(self.data)
        self.big.unlink()
        self.assertEqual(dashboard.build_payload(self.data)["projects"], [])
        self.assertNotIn(str(self.big), dashboard._detail_cache)


class OneBuildAtATime(unittest.TestCase):
    def test_concurrent_reloads_share_one_parse(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            write_detail(data / "RPT_A - detail.csv", "SPN900001", [1, 2])
            parses = []
            real = dashboard.parse_detail

            def slow(path, *args):
                parses.append(1)
                time.sleep(0.3)
                return real(path, *args)
            results = []
            with mock.patch.object(dashboard, "parse_detail", slow):
                threads = [threading.Thread(
                    target=lambda: results.append(dashboard.build_payload(data)))
                    for _ in range(3)]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join()
            self.assertEqual(len(parses), 1)
            self.assertEqual([len(r["projects"]) for r in results], [1, 1, 1])


class Progress(unittest.TestCase):
    def test_reports_the_file_being_read_and_how_far_along(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            path = data / "RPT_A - detail.csv"
            write_detail(path, "SPN900001", range(2000))
            seen = []
            real = dashboard._CountingReader.readinto

            def spying(self, b):
                seen.append(dashboard.PROGRESS.snapshot())
                return real(self, b)
            with mock.patch.object(dashboard._CountingReader, "readinto", spying):
                dashboard.build_payload(data)
            size = path.stat().st_size
        self.assertTrue(seen)
        first, last = seen[0], seen[-1]
        self.assertTrue(first["active"])
        self.assertEqual(first["stage"], "reading")
        self.assertEqual(first["file"], "RPT_A - detail.csv")
        self.assertEqual((first["fileIndex"], first["fileCount"]), (1, 1))
        self.assertEqual(first["fileSize"], size)
        self.assertEqual(first["bytesTotal"], size)
        self.assertLess(first["fileBytes"], last["fileBytes"])
        self.assertLessEqual(last["fileBytes"], last["fileSize"])
        # and once the build is over, nothing is in progress
        self.assertEqual(dashboard.PROGRESS.snapshot(), {"active": False})

    def test_unchanged_files_are_not_part_of_the_plan(self):
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            write_detail(data / "RPT_A - detail.csv", "SPN900001", [1])
            dashboard.build_payload(data)
            write_detail(data / "RPT_B - detail.csv", "SPN900002", [2])
            plans = []
            real = dashboard.PROGRESS.start

            def spying(plan):
                plans.append(plan)
                return real(plan)
            with mock.patch.object(dashboard.PROGRESS, "start", spying):
                dashboard.build_payload(data)
        self.assertEqual([[name for name, _ in plan] for plan in plans],
                         [["RPT_B - detail.csv"]])

    def test_the_counting_reader_reads_the_same_text_as_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.csv"
            path.write_text("﻿a,b\n1,ünïcode\n", encoding="utf-8")
            with dashboard._open_counting(path) as f:
                self.assertEqual(list(csv.reader(f)), [["a", "b"], ["1", "ünïcode"]])


if __name__ == "__main__":
    unittest.main()
