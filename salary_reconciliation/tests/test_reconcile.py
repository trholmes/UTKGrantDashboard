"""Salary reconciliation: reading the three reports and lining them up.

    python -m unittest discover salary_reconciliation/tests

Uses the fictional reports from make_demo.py (see its docstring for the
story they tell).
"""

import http.client
import io
import json
import os
import struct
import sys
import tempfile
import threading
import unittest
import zipfile
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import make_demo  # noqa: E402
import reconcile  # noqa: E402
import server  # noqa: E402
import xls  # noqa: E402
import xlsx  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"

A, B, C, D, E = make_demo.A, make_demo.B, make_demo.C, make_demo.D, make_demo.E


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


class DataFolder(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, name, data, age=0):
        p = self.dir / name
        p.write_bytes(data)
        t = 1_700_000_000 - age
        os.utime(p, (t, t))
        return p

    def test_names_as_the_reporting_system_exports_them(self):
        for name, kind in [("5dd93ce3-Labor_Distribution_Report.csv", reconcile.LD),
                           ("Labor Distribution Report (2).csv", reconcile.LD),
                           ("DetailBalances_3.xlsx", reconcile.GL),
                           ("Detail Balances.xlsx", reconcile.GL),
                           ("Fund_Line_Items_-_3_Segments_Fund_Line_4.xlsx", reconcile.FLI),
                           ("budget.xlsx", None)]:
            self.assertEqual(reconcile.kind_by_name(name), kind, name)

    def test_newest_of_each_report_wins(self):
        ld, gl, fli = (data for _, data in demo_files())
        self.put("Labor_Distribution_Report.csv", ld, age=100)
        self.put("Labor_Distribution_Report (1).csv", ld, age=0)
        self.put("DetailBalances.xlsx", gl)
        self.put("Fund_Line_Items.xlsx", fli)
        self.put("~$DetailBalances.xlsx", b"lock file")
        self.put("notes.xlsx", b"whatever")
        found, notes = reconcile.load_folder(self.dir)
        self.assertEqual(found[reconcile.LD][0], "Labor_Distribution_Report (1).csv")
        self.assertEqual(set(found), {reconcile.LD, reconcile.GL, reconcile.FLI})
        text = " ".join(notes)
        self.assertIn("ignoring Labor_Distribution_Report.csv", text)
        self.assertIn("notes.xlsx", text)
        self.assertNotIn("~$", text)

    def test_contents_decide_when_the_name_is_wrong(self):
        self.put("DetailBalances.csv", make_demo.ld_csv())
        found, notes = reconcile.load_folder(self.dir)
        self.assertIn(reconcile.LD, found)
        self.assertIn("read as Labor Distribution", " ".join(notes))

    def test_xls_is_read_and_unreadable_types_are_named(self):
        self.put("DetailBalances_3.xls", (FIXTURES / "detail_balances_demo.xls").read_bytes())
        self.put("Labor Distribution Report.pdf", b"%PDF")
        found, notes = reconcile.load_folder(self.dir)
        self.assertEqual(found[reconcile.GL][0], "DetailBalances_3.xls")
        self.assertIn(".pdf isn't a format this reads", " ".join(notes))

    def test_missing_folder(self):
        self.assertEqual(reconcile.find_reports(self.dir / "nope"), ([], []))


class Reconcile(unittest.TestCase):
    def test_labor_distribution_alone_is_sorted_by_combination_then_person(self):
        result, _ = run_demo((reconcile.LD,))
        combos = [(r["combo"], r["period"]) for r in result["rows"]]
        self.assertEqual(combos, sorted(combos))
        a = row(result, A)
        self.assertEqual([l["person"] for l in a["ld_lines"]],
                         ["Chen, Wei", "Rivera, Ana", "Rivera, Ana"])
        self.assertEqual(a["status"], "unchecked")
        self.assertEqual(row(result, A, "27-02")["ld_total"], 497582)

    def test_against_detail_balances(self):
        result, _ = run_demo((reconcile.LD, reconcile.GL))
        # the longevity line is on 512100 in LD, 512400 in the ledger
        self.assertEqual((row(result, A)["status"], row(result, A)["diff"]), ("mismatch", -30000))
        self.assertEqual(row(result, E)["status"], "gl_only")
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
        self.assertEqual(row(result, B)["status"], "mismatch")

    def test_lines_posted_to_another_account_explain_both_rows(self):
        result, _ = run_demo()
        a, e = row(result, A), row(result, E)
        self.assertEqual((a["status"], e["status"]), ("posted_elsewhere", "posted_elsewhere"))
        self.assertEqual(a["fli"]["matched"], 2)
        self.assertEqual(a["fli"]["ld_only"], [])
        [out] = a["fli"]["moved_out"]
        self.assertEqual((out["ld"]["txn"], out["gl"]["key"][3], out["gl"]["account_name"]),
                         ("30000006", "512400", "Faculty Longevity Pay"))
        [came] = e["fli"]["moved_in"]
        self.assertEqual((came["from"], e["fli"]["gl_only"], e["fli"]["unexplained"]), (A, [], 0))
        self.assertEqual(result["counts"]["mismatch"], 2)  # B and C stay unexplained

    def test_no_pairing_by_amount_alone(self):
        """Two people paid the same on one account must not be paired."""
        ld = [{"combo": tuple(A.split("-")), "period": "27-03", "person": "X", "txn": "1",
               "status": "Success", "amount": 100, "pay_start": "", "pay_element": ""}]
        fli = [{"key": tuple(A.split("-"))[:6], "period": "27-03", "amount": 100,
                "ref": "2", "posted": "", "doc": "", "line": ""}]
        detail = reconcile.reconcile(ld, None, fli)["rows"][0]["fli"]
        self.assertEqual((len(detail["gl_only"]), len(detail["ld_only"])), (1, 1))

    def test_amount_is_taken_as_signed(self):
        """Credits come negative, and reversals as negative debits."""
        def line(amount, dc, indicator):
            return ["2027", "3", "", "1", "", "", "", amount, "", "10", "", "1100001", "",
                    "106015", "", "210", "", "512100", "", "0000", "", "Reversal", "", dc,
                    "", "", "", "", "", "", "R1", "Manual", indicator, "Actual"]
        rows = [make_demo.FLI_HEADER, line("-250", "H", "Credit"), line("-40", "S", "Debit")]
        self.assertEqual([l["amount"] for l in reconcile.parse_fli(rows, 0)], [-25000, -4000])

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


def _rec(rtype, data):
    return struct.pack("<HH", rtype, len(data)) + data


def tiny_biff():
    """A minimal Excel 97 workbook stream, small enough to live in the OLE
    mini-stream, whose shared strings split across CONTINUE records the two
    awkward ways: mid-string switching to 16-bit characters, and right after
    a string's header."""
    sst = _rec(xls.SST, struct.pack("<II", 2, 2) + struct.pack("<HB", 6, 0) + b"abc")
    sst += _rec(xls.CONTINUE, b"\x01" + "déf".encode("utf-16-le") + struct.pack("<HB", 2, 0))
    sst += _rec(xls.CONTINUE, b"\x00xy")
    bof = lambda dt: _rec(xls.BOF, struct.pack("<HH", 0x0600, dt) + bytes(12))
    sheet_name = b"Sheet1"

    def globals_(pos):
        return (bof(0x0005) + sst
                + _rec(xls.BOUNDSHEET, struct.pack("<IBB", pos, 0, 0) + bytes([len(sheet_name), 0]) + sheet_name)
                + _rec(xls.EOF, b""))
    head = globals_(0)
    sheet = (bof(0x0010)
             + _rec(xls.LABELSST, struct.pack("<HHHI", 0, 0, 0, 0))
             + _rec(xls.LABELSST, struct.pack("<HHHI", 0, 1, 0, 1))
             + _rec(xls.LABEL, struct.pack("<HHH", 0, 2, 0) + struct.pack("<HB", 4, 0) + b"Fund")
             + _rec(xls.NUMBER, struct.pack("<HHHd", 1, 0, 0, 1100001.0))
             + _rec(xls.RK, struct.pack("<HHHI", 1, 1, 0, (497582 << 2) | 0x03))  # 4975.82
             + _rec(xls.MULRK, struct.pack("<HH", 2, 0) + struct.pack("<HI", 0, (-7 << 2 & 0xFFFFFFFF) | 0x02)
                    + struct.pack("<HI", 0, (12 << 2) | 0x02) + struct.pack("<H", 1))
             + _rec(xls.EOF, b""))
    return globals_(len(head)) + sheet


def tiny_ole(stream, name="Workbook"):
    """An OLE2 container (512-byte sectors) holding one small stream in its
    mini-stream."""
    FREE, END = 0xFFFFFFFF, 0xFFFFFFFE
    mini = stream + bytes(-len(stream) % 64)
    m = len(mini) // 64
    ministream = mini + bytes(-len(mini) % 512)
    k = len(ministream) // 512
    fat = [0xFFFFFFFD, END, END] + [3 + i + 1 for i in range(k - 1)] + [END]
    fat += [FREE] * (128 - len(fat))
    minifat = [i + 1 for i in range(m - 1)] + [END]
    minifat += [FREE] * (128 - len(minifat))

    def entry(ename, etype, start, size, child=FREE):
        n = (ename + "\0").encode("utf-16-le")
        return (n + bytes(64 - len(n)) + struct.pack("<HBB", len(n), etype, 1)
                + struct.pack("<III", FREE, FREE, child) + bytes(36)
                + struct.pack("<III", start, size, 0))
    directory = (entry("Root Entry", 5, 3, len(mini), child=1) + entry(name, 2, 0, len(stream))
                 + bytes(256))
    header = (xls.OLE_MAGIC + bytes(16) + struct.pack("<HHHHH", 0x3E, 3, 0xFFFE, 9, 6) + bytes(6)
              + struct.pack("<IIIIIIIII", 0, 1, 1, 0, 4096, 2, 1, END, 0)
              + struct.pack("<I", 0) + struct.pack("<108I", *[FREE] * 108))
    return (header + struct.pack("<128I", *fat) + directory
            + struct.pack("<128I", *minifat) + ministream)


class XlsFormats(unittest.TestCase):
    """Everything an export named .xls can turn out to be."""

    def test_excel_97_workbook(self):
        data = (FIXTURES / "detail_balances_demo.xls").read_bytes()
        self.assertEqual(xls.kind(data), "biff")
        found, notes = reconcile.load([("DetailBalances.xls", data),
                                       ("ld.csv", make_demo.ld_csv())])
        self.assertEqual(notes, [])
        from_xls = reconcile.reconcile_files(found)
        from_xlsx, _ = run_demo((reconcile.LD, reconcile.GL))
        self.assertEqual([(r["combo"], r["gl_total"], r["status"]) for r in from_xls["rows"]],
                         [(r["combo"], r["gl_total"], r["status"]) for r in from_xlsx["rows"]])

    def test_shared_strings_across_continue_records(self):
        rows = xls.read_rows((FIXTURES / "long_strings.xls").read_bytes())
        self.assertEqual(len(rows), 802)
        self.assertEqual(rows[7][1], "row 7 abcdefghij abcdefghij abcdefghij "
                                     "abcdefghij abcdefghij Café ü 日本")
        self.assertEqual(rows[5][2:], ["08/06/2026", "6.25"])
        self.assertEqual(rows[6][3], "-6")
        self.assertEqual(rows[801][1], "é日" * 2000 + "x" * 9000)
        self.assertEqual(rows[800][0], "800")

    def test_small_workbook_in_the_mini_stream(self):
        rows = xls.read_rows(tiny_ole(tiny_biff()))
        self.assertEqual(rows, [["abcdéf", "xy", "Fund"], ["1100001", "4975.82"], ["-7", "12"]])

    def test_excel_95_is_named(self):
        stream = _rec(xls.BOF, struct.pack("<HH", 0x0500, 5) + bytes(4))
        with self.assertRaisesRegex(ValueError, "Excel 95"):
            xls.read_rows(tiny_ole(stream, "Book"))

    def test_xml_spreadsheet_2003(self):
        data = """<?xml version="1.0"?>
<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet"
 xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">
 <Worksheet ss:Name="Sheet1"><Table>
  <Row><Cell><Data ss:Type="String">Fund</Data></Cell><Cell ss:Index="3"><Data ss:Type="String">Date</Data></Cell></Row>
  <Row ss:Index="3"><Cell ss:MergeAcross="1"><Data ss:Type="Number">1100001</Data></Cell>
   <Cell><Data ss:Type="DateTime">2026-08-01T00:00:00.000</Data></Cell></Row>
 </Table></Worksheet></Workbook>""".encode()
        self.assertEqual(xls.kind(data), "xml2003")
        self.assertEqual(xls.read_rows(data), [["Fund", "", "Date"], ["1100001", "", "08/01/2026"]])

    def test_html_table(self):
        data = b"""<html><head><meta charset="utf-8"></head><body>
<table><tr><th>Fund</th><th colspan="2">Account &amp; Program</th><th>Amount</th></tr>
<tr><td>1100001</td><td>512100</td><td>210</td><td>4,975.82&nbsp;</td></tr>
<tr><td></td><td></td><td></td><td></td></tr></table></body></html>"""
        self.assertEqual(xls.kind(data), "html")
        self.assertEqual(xls.read_rows(data), [["Fund", "Account & Program", "", "Amount"],
                                               ["1100001", "512100", "210", "4,975.82"]])

    def test_csv_or_xlsx_named_xls_are_read_by_content(self):
        self.assertIsNone(xls.kind(make_demo.ld_csv()))
        self.assertEqual(reconcile.identify(make_demo.gl_xlsx())[0], reconcile.GL)


class Server(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.session = server.Session(Path(self.tmp.name))
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(self.session))
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.port = self.httpd.server_address[1]

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

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

    def test_load_folder(self):
        status, body = self.request("POST", "/api/load-folder")
        self.assertIn("No reports in the data folder", json.loads(body)["notes"][0])
        for (_, data), name in zip(demo_files(), ("Labor_Distribution_Report.csv",
                                                  "DetailBalances.xlsx", "Fund_Line_Items.xlsx")):
            (Path(self.tmp.name) / name).write_bytes(data)
        status, body = self.request("POST", "/api/load-folder")
        self.assertEqual(status, 200, body)
        state = json.loads(self.request("GET", "/api/state")[1])
        self.assertEqual(state["files"][reconcile.GL]["name"], "DetailBalances.xlsx")
        self.assertEqual(state["result"]["counts"]["mismatch"], 2)

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
