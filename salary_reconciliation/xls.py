"""Read an '.xls' file — whichever of the things that name covers it is —
with the Python standard library only.

Reporting systems hand out several different formats under that one
extension:

  * a real Excel 97-2003 workbook (binary BIFF8, inside an OLE2 container);
  * Excel 2003 XML ("XML Spreadsheet"), plain text starting with <?xml;
  * an HTML table, which Excel opens without complaint.

(CSV text or a real .xlsx with an .xls name need nothing from here: the
caller recognizes those by their contents.) read_rows() works out which one
it has and returns the first worksheet as rows of text, the same shape
xlsx.read_rows() gives.
"""

import re
import struct
from html.parser import HTMLParser
from xml.etree import ElementTree as ET

import xlsx

OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def kind(data):
    """'biff', 'xml2003', 'html' or None (not one of these)."""
    if data[:8] == OLE_MAGIC:
        return "biff"
    head = data[:4096].lstrip(b"\xef\xbb\xbf\xff\xfe\x00 \t\r\n").lower()
    text = data[:4096].replace(b"\x00", b"").lower()
    if head.startswith(b"<?xml") and b"urn:schemas-microsoft-com:office:spreadsheet" in text:
        return "xml2003"
    if b"<table" in text or head.startswith((b"<html", b"<!doctype html")):
        return "html"
    return None


def read_rows(data):
    k = kind(data)
    if k == "biff":
        return _biff_rows(_ole_stream(data, ("Workbook", "Book")))
    if k == "xml2003":
        return _xml2003_rows(data)
    if k == "html":
        return _html_rows(data)
    raise ValueError("not an Excel 97-2003, XML Spreadsheet or HTML file")


def _grid(cells):
    """{(row, col): text} -> list of rows, empty rows dropped."""
    rows = {}
    for (r, c), v in cells.items():
        if v != "":
            rows.setdefault(r, {})[c] = v
    out = []
    for r in sorted(rows):
        row = [""] * (max(rows[r]) + 1)
        for c, v in rows[r].items():
            row[c] = v
        out.append(row)
    return out


def _number_text(v):
    """1100001.0 -> '1100001', 4975.82 -> '4975.82' (as an .xlsx stores it)."""
    return str(int(v)) if v.is_integer() and abs(v) < 1e15 else repr(v)


# ---------------------------------------------------------------------------
# OLE2 compound file: the container a binary .xls lives in
# ---------------------------------------------------------------------------

_FREE, _END = 0xFFFFFFFF, 0xFFFFFFFE


def _ole_stream(data, names):
    """The contents of the first stream called one of `names`."""
    if len(data) < 512:
        raise ValueError("truncated .xls file")
    sector_size = 1 << struct.unpack_from("<H", data, 0x1E)[0]
    mini_size = 1 << struct.unpack_from("<H", data, 0x20)[0]
    (n_fat, first_dir, _, cutoff, first_minifat, n_minifat,
     first_difat, n_difat) = struct.unpack_from("<IIIIIIII", data, 0x2C)
    per_sector = sector_size // 4

    def sector(n):
        start = (n + 1) * sector_size
        return data[start:start + sector_size]

    difat = list(struct.unpack_from("<109I", data, 0x4C))
    s, seen = first_difat, 0
    while s not in (_FREE, _END) and seen < n_difat:
        entries = struct.unpack(f"<{per_sector}I", sector(s))
        difat.extend(entries[:-1])
        s, seen = entries[-1], seen + 1
    fat = []
    for s in difat[:n_fat]:
        fat.extend(struct.unpack(f"<{per_sector}I", sector(s)))

    def chain(start, table):
        out, s, guard = [], start, 0
        while s not in (_FREE, _END) and s < len(table) and guard <= len(table):
            out.append(s)
            s, guard = table[s], guard + 1
        return out

    def read_chain(start):
        return b"".join(sector(s) for s in chain(start, fat))

    directory = read_chain(first_dir)
    entries = []
    for off in range(0, len(directory) - 127, 128):
        e = directory[off:off + 128]
        name_len = struct.unpack_from("<H", e, 64)[0]
        name = e[:max(name_len - 2, 0)].decode("utf-16-le", "replace")
        etype = e[66]
        start, size = struct.unpack_from("<II", e, 116)
        entries.append((name, etype, start, size))
    if not entries:
        raise ValueError("damaged .xls file (no directory)")
    root_start = entries[0][2]
    for want in names:
        for name, etype, start, size in entries:
            if etype != 2 or name.lower() != want.lower():
                continue
            if size < cutoff:
                minifat = []
                for s in chain(first_minifat, fat)[:n_minifat]:
                    minifat.extend(struct.unpack(f"<{per_sector}I", sector(s)))
                ministream = read_chain(root_start)
                body = b"".join(ministream[m * mini_size:(m + 1) * mini_size]
                                for m in chain(start, minifat))
                return body[:size]
            return read_chain(start)[:size]
    raise ValueError("no workbook inside this .xls file")


# ---------------------------------------------------------------------------
# BIFF8 records: the workbook itself
# ---------------------------------------------------------------------------

BOF, EOF, BOUNDSHEET, SST, CONTINUE = 0x0809, 0x000A, 0x0085, 0x00FC, 0x003C
FORMAT, XF, DATEMODE = 0x041E, 0x00E0, 0x0022
LABELSST, LABEL, NUMBER, RK, MULRK = 0x00FD, 0x0204, 0x0203, 0x027E, 0x00BD
FORMULA, STRING, BOOLERR = 0x0006, 0x0207, 0x0205


def _records(stream, pos=0):
    """(type, data, start offset) for each record from `pos`; a record's
    CONTINUE records are handed over as a list of pieces instead of bytes,
    since a string split across them restarts with a flags byte."""
    n = len(stream)
    while pos + 4 <= n:
        rtype, length = struct.unpack_from("<HH", stream, pos)
        start = pos
        pieces = [stream[pos + 4:pos + 4 + length]]
        pos += 4 + length
        while pos + 4 <= n and struct.unpack_from("<H", stream, pos)[0] == CONTINUE:
            length = struct.unpack_from("<H", stream, pos + 2)[0]
            pieces.append(stream[pos + 4:pos + 4 + length])
            pos += 4 + length
        yield rtype, pieces, start


class _Pieces:
    """Reads across a record and its CONTINUE pieces."""

    def __init__(self, pieces):
        self.pieces = pieces
        self.i = 0
        self.pos = 0

    def _ensure(self):
        while self.pos >= len(self.pieces[self.i]) and self.i + 1 < len(self.pieces):
            self.i, self.pos = self.i + 1, 0

    def take(self, n):
        out = b""
        while n > 0:
            self._ensure()
            piece = self.pieces[self.i]
            chunk = piece[self.pos:self.pos + n]
            if not chunk:
                raise ValueError("damaged .xls file (record ends early)")
            out += chunk
            self.pos += len(chunk)
            n -= len(chunk)
        return out

    def u8(self):
        return self.take(1)[0]

    def u16(self):
        return struct.unpack("<H", self.take(2))[0]

    def u32(self):
        return struct.unpack("<I", self.take(4))[0]

    def string(self, length_bytes=2):
        """A BIFF8 unicode string. Its characters may run into the next
        CONTINUE piece, which then starts with a fresh compressed/16-bit
        flag for the rest."""
        cch = self.u16() if length_bytes == 2 else self.u8()
        flags = self.u8()
        wide = flags & 0x01
        runs = self.u16() if flags & 0x08 else 0
        ext = self.u32() if flags & 0x04 else 0
        chars = []
        while True:
            piece = self.pieces[self.i]
            n = min(cch, (len(piece) - self.pos) // (2 if wide else 1))
            raw = piece[self.pos:self.pos + (n * 2 if wide else n)]
            chars.append(raw.decode("utf-16-le" if wide else "latin-1"))
            self.pos += len(raw)
            cch -= n
            if cch == 0:
                break
            # Out of room: the characters go on in the next piece, which
            # opens with its own flags byte — even when none fit in this one.
            if self.i + 1 >= len(self.pieces):
                raise ValueError("damaged .xls file (string ends early)")
            self.i, self.pos = self.i + 1, 0
            wide = self.u8() & 0x01
        if runs:
            self.take(runs * 4)
        if ext:
            self.take(ext)
        return "".join(chars)


def _rk(v):
    if v & 0x02:
        n = v >> 2
        if n & 0x20000000:
            n -= 0x40000000
        num = float(n)
    else:
        num = struct.unpack("<d", struct.pack("<Q", (v & 0xFFFFFFFC) << 32))[0]
    return num / 100 if v & 0x01 else num


def _biff_rows(stream):
    globals_ = _records(stream)
    rtype, pieces, _ = next(globals_, (None, None, None))
    if rtype != BOF:
        raise ValueError("not an Excel workbook stream")
    version = struct.unpack_from("<H", pieces[0], 0)[0]
    if version != 0x0600:
        raise ValueError("this .xls is from Excel 95 or older — open it in Excel "
                         "and save it as .xlsx")
    sst, formats, xf_formats, date1904, sheet_pos = [], {}, [], False, None
    for rtype, pieces, _ in globals_:
        if rtype == EOF:
            break
        r = _Pieces(pieces)
        if rtype == SST:
            r.u32()
            unique = r.u32()
            sst = [r.string() for _ in range(unique)]
        elif rtype == FORMAT:
            fid = r.u16()
            formats[fid] = r.string()
        elif rtype == XF:
            xf_formats.append(struct.unpack_from("<H", pieces[0], 2)[0])
        elif rtype == DATEMODE:
            date1904 = struct.unpack_from("<H", pieces[0], 0)[0] == 1
        elif rtype == BOUNDSHEET and sheet_pos is None:
            pos, _, sheet_type = struct.unpack_from("<IBB", pieces[0], 0)
            if sheet_type == 0:
                sheet_pos = pos
    if sheet_pos is None:
        raise ValueError("no worksheet in this .xls file")

    date_xfs = {i for i, f in enumerate(xf_formats)
                if f in xlsx._DATE_FORMAT_IDS or xlsx.is_date_format(formats.get(f, ""))}

    def num(xf, v):
        return xlsx.serial_to_date(v, date1904) if xf in date_xfs else _number_text(v)

    cells, pending_formula = {}, None
    for rtype, pieces, _ in _records(stream, sheet_pos):
        p = pieces[0]
        if rtype == EOF:
            break
        if rtype == LABELSST:
            row, col, _, i = struct.unpack_from("<HHHI", p, 0)
            cells[row, col] = sst[i] if i < len(sst) else ""
        elif rtype == NUMBER:
            row, col, xf, v = struct.unpack_from("<HHHd", p, 0)
            cells[row, col] = num(xf, v)
        elif rtype == RK:
            row, col, xf, v = struct.unpack_from("<HHHI", p, 0)
            cells[row, col] = num(xf, _rk(v))
        elif rtype == MULRK:
            row, first = struct.unpack_from("<HH", p, 0)
            for k in range((len(p) - 6) // 6):
                xf, v = struct.unpack_from("<HI", p, 4 + 6 * k)
                cells[row, first + k] = num(xf, _rk(v))
        elif rtype == LABEL:
            row, col, _ = struct.unpack_from("<HHH", p, 0)
            r = _Pieces(pieces)
            r.take(6)
            cells[row, col] = r.string()
        elif rtype == BOOLERR:
            row, col, _, v, is_err = struct.unpack_from("<HHHBB", p, 0)
            if not is_err:
                cells[row, col] = "TRUE" if v else "FALSE"
        elif rtype == FORMULA:
            row, col, xf = struct.unpack_from("<HHH", p, 0)
            result = p[6:14]
            if result[6:8] == b"\xff\xff":
                if result[0] == 0:
                    pending_formula = (row, col)  # text follows in STRING
                elif result[0] == 1:
                    cells[row, col] = "TRUE" if result[2] else "FALSE"
            else:
                cells[row, col] = num(xf, struct.unpack("<d", result)[0])
        elif rtype == STRING and pending_formula:
            cells[pending_formula] = _Pieces(pieces).string()
            pending_formula = None
    return _grid(cells)


# ---------------------------------------------------------------------------
# Excel 2003 XML ("XML Spreadsheet")
# ---------------------------------------------------------------------------

SS = "{urn:schemas-microsoft-com:office:spreadsheet}"


def _xml2003_rows(data):
    root = ET.fromstring(data)
    sheet = root.find(f"{SS}Worksheet")
    table = sheet.find(f"{SS}Table") if sheet is not None else None
    if table is None:
        return []
    cells, r = {}, -1
    for row in table.findall(f"{SS}Row"):
        r = int(row.get(f"{SS}Index", r + 2)) - 1
        c = -1
        for cell in row.findall(f"{SS}Cell"):
            c = int(cell.get(f"{SS}Index", c + 2)) - 1
            d = cell.find(f"{SS}Data")
            if d is not None:
                text = "".join(d.itertext())
                if d.get(f"{SS}Type") == "DateTime":
                    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", text)
                    if m:
                        text = f"{m.group(2)}/{m.group(3)}/{m.group(1)}"
                cells[r, c] = text
            c += int(cell.get(f"{SS}MergeAcross", 0))
    return _grid(cells)


# ---------------------------------------------------------------------------
# HTML table
# ---------------------------------------------------------------------------

class _TableParser(HTMLParser):
    """Rows of the first <table> that has any cells."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows, self.row, self.cell = [], None, None
        self.depth, self.done = 0, False

    def handle_starttag(self, tag, attrs):
        if self.done:
            return
        if tag == "table":
            self.depth += 1
        elif tag == "tr" and self.depth:
            self.row = []
        elif tag in ("td", "th") and self.row is not None:
            self.cell = []
            self.span = int(dict(attrs).get("colspan") or 1)
        elif tag == "br" and self.cell is not None:
            self.cell.append(" ")

    def handle_endtag(self, tag):
        if self.done:
            return
        if tag in ("td", "th") and self.cell is not None:
            text = re.sub(r"\s+", " ", "".join(self.cell).replace("\xa0", " ")).strip()
            self.row.extend([text] + [""] * (self.span - 1))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            if any(self.row):
                self.rows.append(self.row)
            self.row = None
        elif tag == "table" and self.depth:
            self.depth -= 1
            if not self.depth and self.rows:
                self.done = True

    def handle_data(self, text):
        if self.cell is not None:
            self.cell.append(text)


def _decode_html(data):
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16")
    m = re.search(rb'charset=["\']?([\w-]+)', data[:4096], re.I)
    for enc in ([m.group(1).decode()] if m else []) + ["utf-8", "cp1252"]:
        try:
            return data.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return data.decode("latin-1")


def _html_rows(data):
    p = _TableParser()
    p.feed(_decode_html(data))
    p.close()
    return p.rows
