"""PI folders: one data folder per PI, for someone who looks after several.

    python3 -m unittest discover tests

A PI with everything straight in data/ must see no difference. A business
office keeps data/<PI>/ folders; every API call names one with ?pi=, an
import lands in the folder the page is showing, and an export sitting in
the shared Downloads folder is matched to the PI it belongs to.
"""

import json
import shutil
import sys
import tempfile
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dashboard  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "sample_dashboard.csv"


def dashboard_export(path, pi, projects):
    """A minimal PI Dashboard export naming one PI and some projects."""
    lines = ["Project Number,Project Name,Project PI / Manager,Project Status,"
             "Project Start Date,Project Finish Date,Budget,SUM Direct Cost,"
             "SUM Indirect Cost,Committed Cost,Remaining Balance,"
             "% of Budget Spent,Expenditure Category"]
    for code in projects:
        lines.append(f'{code},AGENCY {code[-6:]} {pi.split(",")[0]},"{pi}",Active,'
                     f'01/01/2024,12/31/2027,1000,100,0,,900,0.1,01. Salaries & Wages')
    Path(path).write_text("﻿" + "\n".join(lines) + "\n", encoding="utf-8")


def detail_export(path, projects):
    """A minimal detail export: one non-labor line per project."""
    lines = ["P_PROJECT,PROJ_NUMBER,PROJ_NAME,PROJ_START,PROJ_END,PFROMDATE,"
             "PTODATE,L_TRX_NUM,L_EXP_COST,NL_TRX_NUM,NL_EXP_DATE,NL_EXP_TYPE,"
             "NL_EXP_CAT,NL_EXP_COST"]
    for i, code in enumerate(projects):
        lines.append(f"{code},{code},{code} name,01/01/2024,12/31/2027,01/01/2024,"
                     f"09/01/2026,,,2000{i},2026-07-15,Domestic Travel,Travel,10.00")
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")


class Names(unittest.TestCase):
    def test_plain_names_are_fine(self):
        for name in ("Holmes", "Doe, Jane", "O'Brien", "Lee (physics)", "PI 2"):
            self.assertTrue(dashboard.profile_name_ok(name), name)

    def test_paths_and_oddities_are_not(self):
        for name in ("", ".", "..", "../x", "a/b", "a\\b", ".hidden", "x" * 90,
                     "trailing.", "semi;colon", "tab\tname"):
            self.assertFalse(dashboard.profile_name_ok(name), repr(name))


class Folders(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_no_name_means_the_data_folder_itself(self):
        self.assertEqual(dashboard.profile_dir(self.root, None), self.root)
        self.assertEqual(dashboard.profile_dir(self.root, ""), self.root)

    def test_a_name_is_a_registered_subfolder_that_must_exist(self):
        dashboard.create_profile(self.root, "Holmes")
        self.assertEqual(dashboard.profile_dir(self.root, "Holmes"), self.root / "Holmes")
        with self.assertRaises(dashboard.NoSuchProfile):
            dashboard.profile_dir(self.root, "Lee")
        # a folder made by hand is not a PI folder until it is registered
        (self.root / "Lee").mkdir()
        with self.assertRaises(dashboard.NoSuchProfile):
            dashboard.profile_dir(self.root, "Lee")
        dashboard.create_profile(self.root, "Lee")
        self.assertEqual(dashboard.profile_dir(self.root, "Lee"), self.root / "Lee")

    def test_a_name_can_never_leave_the_data_folder(self):
        outside = self.root.parent
        for name in ("..", "../" + outside.name, str(outside), "/etc"):
            with self.assertRaises(dashboard.NoSuchProfile):
                dashboard.profile_dir(self.root, name)

    def test_create_tidies_the_name_and_tolerates_a_repeat(self):
        self.assertEqual(dashboard.create_profile(self.root, "  Doe,   Jane "), "Doe, Jane")
        self.assertTrue((self.root / "Doe, Jane").is_dir())
        self.assertEqual(dashboard.create_profile(self.root, "Doe, Jane"), "Doe, Jane")
        self.assertEqual(dashboard.registered_profiles(self.root), ["Doe, Jane"])
        with self.assertRaises(ValueError):
            dashboard.create_profile(self.root, "../escape")
        self.assertFalse((self.root.parent / "escape").exists())

    def test_listing_is_the_registered_folders_only(self):
        # a stray subfolder (old exports, a backup) must not turn a single
        # PI's dashboard into the multi-PI layout
        for name in ("old exports", "backup 2025", ".git"):
            (self.root / name).mkdir()
        self.assertEqual(dashboard.list_profiles(self.root), [])
        dashboard.create_profile(self.root, "Holmes")
        dashboard.create_profile(self.root, "Doe, Jane")
        (self.root / "Holmes" / "stray.txt").write_text("x")
        shutil.copy(FIXTURE, self.root / "Holmes" / "dash.csv")
        listed = dashboard.list_profiles(self.root)
        self.assertEqual([p["name"] for p in listed], ["Doe, Jane", "Holmes"])
        by_name = {p["name"]: p for p in listed}
        self.assertEqual(by_name["Holmes"]["files"], 1)
        self.assertEqual(by_name["Doe, Jane"]["files"], 0)
        # a registered folder that was deleted drops out of the menu
        shutil.rmtree(self.root / "Doe, Jane")
        self.assertEqual([p["name"] for p in dashboard.list_profiles(self.root)], ["Holmes"])

    def test_a_broken_registry_is_an_empty_one(self):
        (self.root / "pi_folders.json").write_text("{not json", encoding="utf-8")
        self.assertEqual(dashboard.registered_profiles(self.root), [])
        (self.root / "pi_folders.json").write_text('["ok", 3, "../x", "ok"]', encoding="utf-8")
        self.assertEqual(dashboard.registered_profiles(self.root), ["ok"])

    def test_profile_info_names_the_current_folder(self):
        dashboard.create_profile(self.root, "Holmes")
        info = dashboard.profile_info(self.root, self.root / "Holmes")
        self.assertEqual(info["current"], "Holmes")
        self.assertEqual(info["root"]["name"], self.root.name)
        self.assertEqual(dashboard.profile_info(self.root, self.root)["current"], "")

    def test_report_source_falls_through_from_the_data_folder(self):
        dashboard.create_profile(self.root, "Holmes")
        (self.root / "report_source.json").write_text('{"template": "RPT9"}')
        self.assertEqual(dashboard.load_report_source(self.root / "Holmes", self.root)["template"],
                         "RPT9")
        (self.root / "Holmes" / "report_source.json").write_text('{"template": "RPT8"}')
        self.assertEqual(dashboard.load_report_source(self.root / "Holmes", self.root)["template"],
                         "RPT8")


class WhoseExport(unittest.TestCase):
    """Downloads is shared by every PI; each export is matched to its folder."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "data"
        self.inbox = Path(self.tmp.name) / "Downloads"
        for d in (self.root, self.inbox):
            d.mkdir()
        dashboard.create_profile(self.root, "Holmes")
        dashboard.create_profile(self.root, "Lee")
        dashboard_export(self.root / "Holmes" / "PI Dashboard.csv", "Holmes, T",
                         ["SPN900001", "SPN900002"])
        dashboard_export(self.root / "Lee" / "PI Dashboard.csv", "Lee, L", ["SPN900003"])

    def scan(self, pi):
        return {f["name"]: f for f in dashboard.scan_inbox(
            self.inbox, dashboard.profile_dir(self.root, pi), self.root)}

    def test_a_dashboard_export_is_matched_by_the_pi_named_in_it(self):
        dashboard_export(self.inbox / "PI Dashboard.csv", "Lee, L", ["SPN900009"])
        found = self.scan("Holmes")["PI Dashboard.csv"]
        self.assertEqual(found["pi"], "Lee, L")
        self.assertEqual(found["matches"], ["Lee"])
        self.assertFalse(found["imported"])

    def test_a_detail_export_is_matched_by_its_project_numbers(self):
        detail_export(self.inbox / "RPT07 detail.csv", ["SPN900002"])
        found = self.scan("Lee")["RPT07 detail.csv"]
        self.assertEqual(found["pi"], "")
        self.assertEqual(found["matches"], ["Holmes"])

    def test_an_export_for_nobody_known_matches_nothing(self):
        detail_export(self.inbox / "RPT07 detail.csv", ["SPN900777"])
        self.assertEqual(self.scan("Lee")["RPT07 detail.csv"]["matches"], [])

    def test_imported_is_per_folder_and_the_other_copies_are_listed(self):
        dashboard_export(self.inbox / "PI Dashboard.csv", "Holmes, T",
                         ["SPN900001", "SPN900002"])
        self.assertTrue(self.scan("Holmes")["PI Dashboard.csv"]["imported"])
        lee_view = self.scan("Lee")["PI Dashboard.csv"]
        self.assertFalse(lee_view["imported"])
        self.assertEqual(lee_view["importedTo"], ["Holmes"])

    def test_without_a_root_the_scan_is_as_it_always_was(self):
        dashboard_export(self.inbox / "PI Dashboard.csv", "Lee, L", ["SPN900003"])
        found = dashboard.scan_inbox(self.inbox, self.root / "Holmes")[0]
        self.assertEqual((found["matches"], found["importedTo"], found["pi"]), ([], [], ""))

    def test_the_sniff_is_cached_on_the_files_identity(self):
        path = self.inbox / "PI Dashboard.csv"
        dashboard_export(path, "Lee, L", ["SPN900003"])
        first = dashboard.sniff_csv(path)
        self.assertIs(dashboard.sniff_csv(path), first)
        dashboard_export(path, "Holmes, T", ["SPN900001", "SPN900002", "SPN900004"])
        self.assertEqual(dashboard.sniff_csv(path)["pis"], {"Holmes, T"})


class Routes(unittest.TestCase):
    """Every API call takes ?pi=; the page for one PI never touches another's."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / "data"
        self.inbox = Path(self.tmp.name) / "Downloads"
        for d in (self.root, self.inbox):
            d.mkdir()
        dashboard.create_profile(self.root, "Holmes")
        shutil.copy(FIXTURE, self.root / "Holmes" / "dash.csv")
        self.server = ThreadingHTTPServer(
            ("127.0.0.1", 0), dashboard.make_handler(self.root, self.inbox))
        Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = "http://127.0.0.1:%d" % self.server.server_address[1]

    def call(self, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        try:
            with urlopen(Request(self.base + path, data=data)) as response:
                return response.status, json.loads(response.read())
        except HTTPError as err:
            return err.code, json.loads(err.read())

    def test_the_data_folder_and_a_pi_folder_are_separate_views(self):
        status, payload = self.call("/api/data")
        self.assertEqual(status, 200)
        self.assertEqual(payload["projects"], [])
        self.assertEqual(payload["profile"]["current"], "")
        self.assertEqual([p["name"] for p in payload["profile"]["profiles"]], ["Holmes"])
        status, payload = self.call("/api/data?pi=Holmes")
        self.assertEqual(status, 200)
        self.assertEqual(len(payload["projects"]), 3)
        self.assertEqual(payload["profile"]["current"], "Holmes")

    def test_an_unknown_pi_is_not_found_not_a_crash(self):
        status, payload = self.call("/api/data?pi=Nobody")
        self.assertEqual(status, 404)
        self.assertIn("Nobody", payload["error"])
        self.assertEqual(self.call("/api/data?pi=../..")[0], 404)

    def test_config_saves_into_the_pi_folder(self):
        status, _ = self.call("/api/config?pi=Holmes", {"people": [{"name": "x"}]})
        self.assertEqual(status, 200)
        self.assertTrue((self.root / "Holmes" / "config.json").is_file())
        self.assertFalse((self.root / "config.json").exists())
        self.assertEqual(self.call("/api/data?pi=Holmes")[1]["config"]["people"][0]["name"], "x")
        self.assertIsNone(self.call("/api/data")[1]["config"])

    def test_imports_land_in_the_pi_folder_the_page_shows(self):
        # (SPN900003 is in Holmes's fixture; Lee's export must not overlap it)
        dashboard_export(self.inbox / "PI Dashboard.csv", "Lee, L", ["SPN900009"])
        status, payload = self.call("/api/profiles", {"name": "Lee"})
        self.assertEqual((status, payload["name"]), (200, "Lee"))
        status, payload = self.call("/api/import?pi=Lee", {"names": ["PI Dashboard.csv"]})
        self.assertEqual((status, payload["imported"]), (200, ["PI Dashboard.csv"]))
        self.assertTrue((self.root / "Lee" / "PI Dashboard.csv").is_file())
        self.assertFalse((self.root / "PI Dashboard.csv").exists())
        # and the inbox, seen from Holmes's page, now says so
        status, payload = self.call("/api/inbox?pi=Holmes")
        self.assertEqual(payload["files"][0]["importedTo"], ["Lee"])
        self.assertEqual(payload["files"][0]["matches"], ["Lee"])

    def test_a_new_folder_needs_a_usable_name(self):
        status, payload = self.call("/api/profiles", {"name": "../nope"})
        self.assertEqual(status, 400)
        self.assertIn("name", payload["error"])
        self.assertFalse((self.root.parent / "nope").exists())

    def test_the_charge_lookup_and_links_follow_the_pi(self):
        self.assertEqual(self.call("/api/charges?pi=Holmes&project=SPN900001")[0], 200)
        self.assertEqual(self.call("/api/charges?pi=Nobody&project=SPN900001")[0], 400)
        self.assertEqual(self.call("/api/report-links?pi=Holmes&projects=SPN900001&from=2024-01-01")[0], 200)


if __name__ == "__main__":
    unittest.main()
