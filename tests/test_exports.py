"""Reading an export whatever shape the reporting tool gave it, and naming
the files that are not an export instead of ignoring them.

    python3 -m unittest discover tests

A file that is silently skipped is the worst failure mode: "Reload did
nothing" and "nothing to import" with the file sitting right there. So
the near-misses (an Excel workbook, the Award Summary tab instead of the
Project Summary, a title row above the header) are named with a reason,
and the shapes that can be read anyway (UTF-16, tabs, a title row,
padded column names) are read.
"""

import codecs
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dashboard          # noqa: E402
import spn_reports as sr  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "sample_dashboard.csv"
PROJECT_HEADER = ("Project Number,Project Name,Project PI / Manager,Project Status,"
                  "Project Start Date,Project Finish Date,Budget,SUM Direct Cost,"
                  "SUM Indirect Cost,Committed Cost,Remaining Balance,"
                  "% of Budget Spent,Expenditure Category")
PROJECT_ROW = ('SPN900001,AGENCY 111111 Doe,"Doe, Jane",Active,01/01/2024,12/31/2027,'
               '10000,1234.5,0,,500.0,0.1,01. Salaries & Wages')
AWARD_HEADER = ("Award Number,Award Name,Award PI,Award Start Date,Award End Date,"
                "Award Status,Award Type,Projects Count,Budget,SUM Direct Cost,"
                "SUM Indirect Cost,Committed Cost,Remaining Balance,"
                "% of Budget Spent,Expenditure Category")
AWARD_ROW = ('2002011,UT-B CW35982 Doe,"Doe, Jane",06/27/2022,06/30/2027,Active,'
             'Federal,2,149382,120831.63,0,0,28550.37,0.8,01. Salaries & Wages')


class Shapes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def write(self, name, text, encoding="utf-8-sig"):
        path = self.dir / name
        path.write_bytes(text.encode(encoding))
        return path

    def test_the_plain_export_reads_as_before(self):
        rows = dashboard.parse_pi_dashboard(FIXTURE)
        self.assertEqual({r["project"] for r in rows}, {"SPN900001", "SPN900002", "SPN900003"})

    def test_a_title_row_above_the_header_is_skipped(self):
        path = self.write("titled.csv", "PI Dashboard - Project Summary\n\n"
                          + PROJECT_HEADER + "\n" + PROJECT_ROW + "\n")
        self.assertEqual(dashboard.classify_csv(path), "pi_dashboard")
        self.assertEqual(sr.find_header(path)["line"], 2)
        rows = dashboard.parse_pi_dashboard(path)
        self.assertEqual((rows[0]["project"], rows[0]["budget"]), ("SPN900001", 10000.0))

    def test_utf16_is_read(self):
        path = self.write("wide.csv", PROJECT_HEADER + "\n" + PROJECT_ROW + "\n", "utf-16")
        self.assertTrue(path.read_bytes().startswith(codecs.BOM_UTF16))
        self.assertEqual(dashboard.classify_csv(path), "pi_dashboard")
        self.assertEqual(dashboard.parse_pi_dashboard(path)[0]["name"], "AGENCY 111111 Doe")

    def test_tabs_are_read(self):
        path = self.write("tabbed.csv", PROJECT_HEADER.replace(",", "\t") + "\n"
                          + PROJECT_ROW.replace('"Doe, Jane"', "Doe Jane").replace(",", "\t") + "\n")
        self.assertEqual(sr.find_header(path)["delimiter"], "\t")
        self.assertEqual(dashboard.parse_pi_dashboard(path)[0]["category"], "Salaries & Wages")

    def test_padded_column_names_still_match(self):
        path = self.write("padded.csv", PROJECT_HEADER.replace("Project Number", "Project Number ")
                          + "\n" + PROJECT_ROW + "\n")
        self.assertEqual(dashboard.parse_pi_dashboard(path)[0]["project"], "SPN900001")

    def test_a_detail_export_with_a_title_row_is_read_and_progress_still_counts(self):
        path = self.write("RPT_X - detail.csv",
                          "Sponsored Project Detail Report\n"
                          "P_PROJECT,PROJ_NUMBER,PROJ_NAME,PROJ_START,PROJ_END,PFROMDATE,PTODATE,"
                          "L_TRX_NUM,L_EXP_COST,NL_TRX_NUM,NL_EXP_DATE,NL_EXP_TYPE,NL_EXP_CAT,NL_EXP_COST\n"
                          "SPN900001,SPN900001,n,01/01/2024,12/31/2027,01/01/2024,09/01/2026,"
                          ",,20001,2026-07-15,Domestic Travel,Travel,10.00\n")
        self.assertEqual(dashboard.classify_csv(path), "detail")
        labor, nonlabor, meta = {}, {}, {}
        dashboard.parse_detail(path, labor, nonlabor, meta)
        self.assertEqual(len(nonlabor), 1)
        self.assertEqual(next(iter(nonlabor.values()))["amount"], 10.0)

    def test_project_numbers_still_come_from_a_renamed_header(self):
        path = self.write("renamed.csv", "Proj,Name\nSPN123456,x\n")
        self.assertEqual(sr.projects_from_csv(path), ["SPN123456"])


class NearMisses(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def test_the_award_summary_tab_is_named_as_such(self):
        path = self.dir / "PI_Dashboard.csv"
        path.write_text("﻿" + AWARD_HEADER + "\n" + AWARD_ROW + "\n", encoding="utf-8")
        self.assertIsNone(dashboard.classify_csv(path))
        reason = dashboard.why_unrecognized(path)
        self.assertIn("Award Summary", reason)
        self.assertIn("Project Summary", reason)

    def test_an_excel_workbook_is_told_to_be_a_csv(self):
        path = self.dir / "PI Dashboard.xlsx"
        path.write_bytes(b"PK\x03\x04not really")
        self.assertIn("Excel", dashboard.why_unrecognized(path))

    def test_an_unrelated_csv_quotes_its_first_line(self):
        path = self.dir / "shopping.csv"
        path.write_text("eggs,milk\n1,2\n", encoding="utf-8")
        self.assertIn("eggs,milk", dashboard.why_unrecognized(path))
        self.assertIn("the file is empty", dashboard.why_unrecognized(
            self.dir / "empty.csv") if (self.dir / "empty.csv").write_text("") is not None else "")

    def test_the_data_folder_lists_them_with_the_reason(self):
        (self.dir / "PI_Dashboard.csv").write_text(
            "﻿" + AWARD_HEADER + "\n" + AWARD_ROW + "\n", encoding="utf-8")
        (self.dir / "book.xlsx").write_bytes(b"PK\x03\x04")
        (self.dir / "notes.md").write_text("not a spreadsheet")
        payload = dashboard.build_payload(self.dir)
        by_name = {f["name"]: f for f in payload["files"]}
        self.assertEqual(set(by_name), {"PI_Dashboard.csv", "book.xlsx"})
        self.assertIn("Award Summary", by_name["PI_Dashboard.csv"]["reason"])
        self.assertIn("Excel", by_name["book.xlsx"]["reason"])
        self.assertEqual(payload["projects"], [])
        # and a recognized export carries no reason
        import shutil
        shutil.copy(FIXTURE, self.dir / "dash.csv")
        good = {f["name"]: f for f in dashboard.build_payload(self.dir)["files"]}["dash.csv"]
        self.assertEqual((good["type"], good["reason"]), ("pi_dashboard", None))

    def test_the_downloads_folder_names_recent_near_misses_only(self):
        (self.dir / "PI_Dashboard.csv").write_text(
            "﻿" + AWARD_HEADER + "\n" + AWARD_ROW + "\n", encoding="utf-8")
        old = self.dir / "old.xlsx"
        old.write_bytes(b"PK\x03\x04")
        week_ago = time.time() - 7 * 86400
        os.utime(old, (week_ago, week_ago))
        import shutil
        shutil.copy(FIXTURE, self.dir / "good.csv")     # a real export: not listed here
        found = dashboard.scan_inbox_unrecognized(self.dir)
        self.assertEqual([f["name"] for f in found], ["PI_Dashboard.csv"])
        self.assertIn("Award Summary", found[0]["reason"])
        self.assertEqual(dashboard.scan_inbox_unrecognized(None), [])


if __name__ == "__main__":
    unittest.main()
