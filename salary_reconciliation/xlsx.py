"""Just enough .xlsx to read the reporting system's exports and write a
results workbook — Python standard library only.

An .xlsx file is a ZIP of XML parts. Reading takes the first worksheet,
streamed (a Fund Line Items export is mostly padding and can be tens of MB),
and turns every cell into text the way Excel would show it, except that
dates come back as MM/DD/YYYY and numbers without their display format.
"""

import io
import re
import zipfile
from datetime import date, timedelta
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
PKG_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"

# Built-in number formats that display a date (ECMA-376 18.8.30).
_DATE_FORMAT_IDS = set(range(14, 23)) | {45, 46, 47}


def is_xlsx(data):
    return data[:2] == b"PK"


def _col_index(ref):
    """'A1' -> 0, 'AB12' -> 27."""
    n = 0
    for ch in ref:
        if not ch.isalpha():
            break
        n = n * 26 + (ord(ch.upper()) - 64)
    return n - 1


def _first_sheet_path(z):
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    sheet = wb.find(f"{NS}sheets/{NS}sheet")
    rid = sheet.get(f"{REL_NS}id") if sheet is not None else None
    if rid and "xl/_rels/workbook.xml.rels" in z.namelist():
        rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        for rel in rels.iter(f"{PKG_REL_NS}Relationship"):
            if rel.get("Id") == rid:
                target = rel.get("Target").lstrip("/")
                return target if target.startswith("xl/") else "xl/" + target
    return "xl/worksheets/sheet1.xml"


def _shared_strings(z):
    if "xl/sharedStrings.xml" not in z.namelist():
        return []
    out = []
    with z.open("xl/sharedStrings.xml") as f:
        for _, el in ET.iterparse(f):
            if el.tag == f"{NS}si":
                # plain (<t>) or rich text (<r><t>); phonetic hints
                # (<rPh><t>) are not part of the text
                parts = el.findall(f"{NS}t") + el.findall(f"{NS}r/{NS}t")
                out.append("".join(t.text or "" for t in parts))
                el.clear()
    return out


def _date_styles(z):
    """Indexes of the cell styles (the c/@s attribute) that show a date."""
    if "xl/styles.xml" not in z.namelist():
        return set()
    root = ET.fromstring(z.read("xl/styles.xml"))
    date_fmts = set(_DATE_FORMAT_IDS)
    for fmt in root.iter(f"{NS}numFmt"):
        code = re.sub(r'"[^"]*"|\[[^\]]*\]', "", fmt.get("formatCode", "")).lower()
        if re.search(r"[dy]", code) or re.search(r"m{3,}", code):
            date_fmts.add(int(fmt.get("numFmtId")))
    xfs = root.find(f"{NS}cellXfs")
    if xfs is None:
        return set()
    return {i for i, xf in enumerate(xfs.findall(f"{NS}xf"))
            if int(xf.get("numFmtId", "0")) in date_fmts}


def _serial_to_date(v):
    try:
        days = float(v)
    except ValueError:
        return v
    d = date(1899, 12, 30) + timedelta(days=int(days))
    return d.strftime("%m/%d/%Y")


def read_rows(data):
    """The first worksheet of an .xlsx (bytes) as a list of rows of text."""
    z = zipfile.ZipFile(io.BytesIO(data))
    strings = _shared_strings(z)
    date_styles = _date_styles(z)
    rows = []
    with z.open(_first_sheet_path(z)) as f:
        for _, el in ET.iterparse(f):
            if el.tag != f"{NS}row":
                continue
            cells = {}
            for c in el.findall(f"{NS}c"):
                t = c.get("t")
                v = c.find(f"{NS}v")
                if t == "inlineStr":
                    val = "".join(x.text or "" for x in c.iter(f"{NS}t"))
                elif v is None or v.text is None:
                    continue
                elif t == "s":
                    val = strings[int(v.text)]
                elif t in ("str", "e"):
                    val = v.text
                elif t == "b":
                    val = "TRUE" if v.text == "1" else "FALSE"
                elif c.get("s") and int(c.get("s")) in date_styles:
                    val = _serial_to_date(v.text)
                else:
                    val = v.text
                cells[_col_index(c.get("r", "A"))] = val
            el.clear()
            if not cells:
                continue
            row = [""] * (max(cells) + 1)
            for i, val in cells.items():
                row[i] = val
            rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# writing
# ---------------------------------------------------------------------------

# Style indexes written into styles.xml below.
PLAIN, BOLD, MONEY, MONEY_BOLD, HEADER = 0, 1, 2, 3, 4

_STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
<numFmts count="1"><numFmt numFmtId="164" formatCode="#,##0.00;[Red]\\-#,##0.00"/></numFmts>
<fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><sz val="11"/><name val="Calibri"/></font></fonts>
<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FFDDE6F0"/></patternFill></fill></fills>
<borders count="1"><border/></borders>
<cellStyleXfs count="1"><xf/></cellStyleXfs>
<cellXfs count="5">
<xf numFmtId="0" fontId="0" fillId="0" borderId="0"/>
<xf numFmtId="0" fontId="1" fillId="0" borderId="0" applyFont="1"/>
<xf numFmtId="164" fontId="0" fillId="0" borderId="0" applyNumberFormat="1"/>
<xf numFmtId="164" fontId="1" fillId="0" borderId="0" applyNumberFormat="1" applyFont="1"/>
<xf numFmtId="0" fontId="1" fillId="2" borderId="0" applyFont="1" applyFill="1"/>
</cellXfs>
</styleSheet>"""


def _col_name(i):
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


_XML_BAD = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _cell(ref, value, style):
    s = f' s="{style}"' if style else ""
    if value is None or value == "":
        return f'<c r="{ref}"{s}/>' if style else ""
    if isinstance(value, bool):
        value = "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return f'<c r="{ref}"{s}><v>{value!r}</v></c>'
    text = escape(_XML_BAD.sub("", str(value)))
    return f'<c r="{ref}"{s} t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'


def _sheet_xml(sheet):
    """sheet: {"name", "rows": [[value or (value, style)]], "widths": [..]}.
    The first row is the header: styled, frozen and filterable."""
    rows = sheet["rows"]
    ncols = max((len(r) for r in rows), default=1)
    out = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
           '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
           '<sheetViews><sheetView workbookViewId="0">'
           '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
           '</sheetView></sheetViews>']
    widths = sheet.get("widths") or []
    if widths:
        out.append("<cols>" + "".join(
            f'<col min="{i + 1}" max="{i + 1}" width="{w}" customWidth="1"/>'
            for i, w in enumerate(widths)) + "</cols>")
    out.append("<sheetData>")
    for r, row in enumerate(rows, start=1):
        cells = []
        for c, value in enumerate(row):
            style = HEADER if r == 1 else PLAIN
            if isinstance(value, tuple):
                value, style = value
            cells.append(_cell(f"{_col_name(c)}{r}", value, style))
        out.append(f'<row r="{r}">{"".join(cells)}</row>')
    out.append("</sheetData>")
    if len(rows) > 1:
        out.append(f'<autoFilter ref="A1:{_col_name(ncols - 1)}{len(rows)}"/>')
    out.append("</worksheet>")
    return "".join(out)


def write_workbook(sheets):
    """An .xlsx (bytes) with one worksheet per entry of `sheets`."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                   '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
                   + "".join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
                             for i in range(1, len(sheets) + 1))
                   + "</Types>")
        z.writestr("_rels/.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
                   "</Relationships>")
        z.writestr("xl/workbook.xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                   'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
                   + "".join(f'<sheet name="{escape(s["name"][:31])}" sheetId="{i}" r:id="rId{i}"/>'
                             for i, s in enumerate(sheets, start=1))
                   + "</sheets>"
                   + "</workbook>")
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   + "".join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>'
                             for i in range(1, len(sheets) + 1))
                   + f'<Relationship Id="rId{len(sheets) + 1}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
                   "</Relationships>")
        z.writestr("xl/styles.xml", _STYLES)
        for i, sheet in enumerate(sheets, start=1):
            z.writestr(f"xl/worksheets/sheet{i}.xml", _sheet_xml(sheet))
    return buf.getvalue()
