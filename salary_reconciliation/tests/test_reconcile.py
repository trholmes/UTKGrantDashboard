"""Salary reconciliation: reading the three reports and lining them up.

    python -m unittest discover salary_reconciliation/tests

Uses the fictional reports from make_demo.py (see its docstring for the
story they tell).
"""

import http.client
import io
import json
import sys
import threading
import unittest
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import make_demo  # noqa: E402
import reconcile  # noqa: E402
import server  # noqa: E402
import xlsx  # noqa: E402

A, B, C, D = make_demo.A, make_demo.B, make_demo.C, make_demo.D


def demo_files():
    return [("ld.csv", make_demo.ld_csv()), ("db.xlsx", make_demo.gl_xlsx()),
            ("fli.xlsx", make_demo.fli_xlsx())]


def run_demo(kinds=(reconcile.LD, reconcile.GL, reconcile.FLI)):
    found, notes = reconcile.load(demo_files())
    found = {k: v for k, v in found.items() if k in kinds}
    return reconcile.reconcile_files(found), notes


def row(result, combo, period="27-03"):
    return next(r for r in result["rows"]
                if r["combo"] == combo and r["period"] == period)


class Helpers(unittest.TestCase):
    def test_cents(self):
        self.assertEqual(reconcile.cents("1,243.95"), 124395)
        self.assertEqual(reconcile.cents("$-12"), -1200)
        self.assertEqual(reconcile.cents("(300.00)"), -30000)
        self.assertEqual(reconcile.cents("1243.9500000000001"), 124395)
        self.assertEqual(reconcile.cents(""), 0)
        with self.assertRaises(reconcile.ReportError):
            reconcile.cents("n/a")

    def test_segments_survive_excel_numbers(self):
        self.assertEqual(reconcile.segment("0", 4), "0000")
        self.assertEqual(reconcile.segment("1100001.0", 7), "1100001")
        self.assertEqual(reconcile.segment(" 53", 4), "0053")

    def test_periods(self):
        self.assertEqual(reconcile.period("27-02"), "27-02")
        self.assertEqual(reconcile.period("2027-2"), "27-02")


class Identify(unittest.TestCase):
    def test_each_report_recognized_by_header(self):
        kinds = [reconcile.identify(data)[0] for _, data in demo_files()]
        self.assertEqual(kinds, [reconcile.LD, reconcile.GL, reconcile.FLI])

    def test_title_rows_above_the_header(self):
        data = b"Labor Distribution Report\r\nRun 10/01/2026\r\n" + make_demo.ld_csv()
        kind, at, _ = reconcile.identify(data)
        self.assertEqual((kind, at), (reconcile.LD, 2))

    def test_utf16_tab_separated(self):
        text = make_demo.ld_csv().decode("utf-8").replace(",", "\t")
        kind, _, _ = reconcile.identify(text.encode("utf-16"))
        self.assertEqual(kind, reconcile.LD)

    def test_unrelated_file_is_named(self):
        found, notes = reconcile.load([("budget.csv", b"Project,Budget\nX,1\n")])
        self.assertEqual(found, {})
        self.assertIn("budget.csv", notes[0])


class Reconcile(unittest.TestCase):
    def test_labor_distribution_alone_is_sorted_by_combination_then_person(self):
        result, _ = run_demo((reconcile.LD,))
        combos = [(r["combo"], r["period"]) for r in result["rows"]]
        self.assertEqual(combos, sorted(combos))
        a = row(result, A)
        self.assertEqual([l["person"] for l in a["ld_lines"]], ["Chen, Wei", "Rivera, Ana"])
        self.assertEqual(a["status"], "unchecked")
        self.assertEqual(row(result, A, "27-02")["ld_total"], 497582)

    def test_against_detail_balances(self):
        result, _ = run_demo((reconcile.LD, reconcile.GL))
        self.assertEqual(row(result, A)["status"], "match")
        self.assertEqual(row(result, B)["diff"], 50000)
        self.assertEqual(row(result, C)["diff"], -120000)
        d = row(result, D)
        self.assertEqual((d["status"], d["gl_total"]), ("gl_only", 75000))
        # cash and other non-salary accounts stay out
        self.assertFalse(any(r["segments"]["account"] == "100000" for r in result["rows"]))
        # DetailBalances is for 27-03: the 27-02 line is set aside, not compared
        self.assertEqual([l["txn"] for l in result["outside_periods"]], ["29000001"])
        self.assertEqual(result["periods"]["compared"], ["27-03"])

    def test_fund_line_items_find_what_is_missing(self):
        result, _ = run_demo()
        b = row(result, B)["fli"]
        self.assertEqual([f["ref"] for f in b["gl_only"]], ["JE-88213"])
        self.assertEqual(b["ld_only"], [])
        c = row(result, C)["fli"]
        self.assertEqual([l["txn"] for l in c["ld_only"]], ["30000005"])
        self.assertEqual(c["gl_only"], [])
        self.assertTrue(c["agrees_with_gl"])
        a = row(result, A)["fli"]
        self.assertEqual((a["matched"], a["gl_only"], a["ld_only"]), (2, [], []))

    def test_reference_mismatch_falls_back_to_amount(self):
        ld = [{"combo": tuple(A.split("-")), "period": "27-03", "person": "X", "txn": "1",
               "status": "Success", "amount": 100, "pay_start": "", "pay_element": ""}]
        fli = [{"key": tuple(A.split("-"))[:6], "period": "27-03", "amount": 100,
                "ref": "OTHER", "posted": "", "doc": "", "line": ""}]
        result = reconcile.reconcile(ld, None, fli)
        detail = result["rows"][0]["fli"]
        self.assertEqual(len(detail["matched_by_amount"]), 1)
        self.assertEqual(detail["gl_only"], [])

    def test_credit_lines_are_negative(self):
        rows = [make_demo.FLI_HEADER,
                ["2027", "3", "", "1", "", "", "", "250", "", "10", "", "1100001", "", "106015",
                 "", "210", "", "512100", "", "0000", "", "Reversal", "", "H", "", "", "", "",
                 "", "", "R1", "Manual", "Credit", "Actual"]]
        self.assertEqual(reconcile.parse_fli(rows, 0)[0]["amount"], -25000)

    def test_non_success_lines_are_set_aside(self):
        found, _ = reconcile.load(demo_files()[:1])
        lines = found[reconcile.LD][1]
        lines[0]["status"] = "Error"
        result = reconcile.reconcile(lines)
        self.assertEqual([l["txn"] for l in result["excluded"]], [lines[0]["txn"]])


class Xlsx(unittest.TestCase):
    def test_shared_strings_and_date_styles(self):
        """The reporting system's own files use shared strings and
        date-formatted serials, which the writer here doesn't produce."""
        buf = io.BytesIO()
        ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("xl/workbook.xml", f'<workbook {ns}><sheets><sheet name="S" sheetId="1"/></sheets></workbook>')
            z.writestr("xl/sharedStrings.xml",
                       f'<sst {ns}><si><t>Fund</t></si><si><r><t>Rich </t></r><r><t>text</t></r>'
                       f'<rPh><t>ignored</t></rPh></si></sst>')
            z.writestr("xl/styles.xml",
                       f'<styleSheet {ns}><cellXfs><xf numFmtId="0"/><xf numFmtId="14"/></cellXfs></styleSheet>')
            z.writestr("xl/worksheets/sheet1.xml",
                       f'<worksheet {ns}><sheetData><row r="1"><c r="A1" t="s"><v>0</v></c>'
                       f'<c r="C1" t="s"><v>1</v></c></row><row r="2"><c r="A2" s="1"><v>46265</v></c>'
                       f'<c r="B2"><v>0053</v></c></row></sheetData></worksheet>')
        rows = xlsx.read_rows(buf.getvalue())
        self.assertEqual(rows[0], ["Fund", "", "Rich text"])
        self.assertEqual(rows[1], ["08/31/2026", "0053"])

    def test_export_round_trips(self):
        result, _ = run_demo()
        book = reconcile.export_workbook(result, {reconcile.LD: "ld.csv"})
        summary = xlsx.read_rows(book)
        self.assertEqual(summary[0][0], "Account combination")
        self.assertEqual(len(summary), 1 + len(result["rows"]) + 1)  # header, rows, total
        names = zipfile.ZipFile(io.BytesIO(book)).read("xl/workbook.xml").decode()
        for sheet in ("Summary", "Labor Distribution sorted", "Differences", "Not compared"):
            self.assertIn(sheet, names)


class Server(unittest.TestCase):
    def setUp(self):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(server.Session()))
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.port = self.httpd.server_address[1]

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port)
        conn.request(method, path, body=body, headers=headers or {})
        resp = conn.getresponse()
        return resp.status, resp.read()

    def test_upload_reconcile_export(self):
        for name, data in demo_files():
            status, body = self.request("POST", f"/api/file?name={name}", data)
            self.assertEqual(status, 200, body)
        status, body = self.request("GET", "/api/state")
        state = json.loads(body)
        self.assertEqual(set(state["files"]), {reconcile.LD, reconcile.GL, reconcile.FLI})
        self.assertEqual(state["result"]["counts"]["mismatch"], 2)
        status, body = self.request("GET", "/api/export")
        self.assertEqual((status, body[:2]), (200, b"PK"))

    def test_other_origins_are_refused(self):
        status, _ = self.request("POST", "/api/file?name=x.csv", make_demo.ld_csv(),
                                 {"Origin": "https://evil.example"})
        self.assertEqual(status, 403)
        status, _ = self.request("GET", "/api/state", headers={"Host": "attacker.example"})
        self.assertEqual(status, 403)

    def test_static_files_stay_in_their_folder(self):
        status, _ = self.request("GET", "/static/../server.py")
        self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
