#!/usr/bin/env python3
"""Salary Reconciliation — a local page that lines up the Labor
Distribution report against the General Ledger.

Drop the three exports — Labor Distribution, DetailBalances and Fund Line
Items — into the data folder next to this file (or drag them onto the
page), and the page shows, per account combination and
fiscal period, whether payroll and the ledger agree, and for each
difference which lines are missing on which side. Results download as an
Excel workbook.

Security model (the same as the grant dashboard next door):
  * The server binds strictly to 127.0.0.1 and refuses requests from any
    other page or hostname.
  * It makes zero outbound network requests.
  * It reads the reports in its data folder (git-ignored, so they can't
    be committed by accident) and never writes there. Files dragged onto
    the page are held in memory only, never written to disk.

No dependencies beyond the Python 3 standard library (Python 3.9+).

Usage (or double-click "Start Salary Reconciliation.bat" on Windows):
    python3 server.py                  # serve on http://127.0.0.1:8790
    python3 server.py --port 9000
    python3 server.py --data /path/to/reports
    python3 server.py --no-browser
"""

import argparse
import json
import sys
import threading
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))

import reconcile  # noqa: E402

HERE = Path(__file__).resolve().parent
STATIC_DIR = HERE / "static"
DEFAULT_DATA_DIR = HERE / "data"
CONTENT_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript",
                 ".css": "text/css"}
MAX_UPLOAD_BYTES = 1024 * 1024 * 1024  # a Fund Line Items export pads heavily
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class Session:
    """The reports picked so far: {kind: (filename, parsed lines)}."""

    def __init__(self, data_dir=None):
        self.lock = threading.Lock()
        self.data_dir = data_dir
        self.found = {}
        self.notes = []

    def add(self, name, data):
        return self._take(*reconcile.load([(name, data)]))

    def load_folder(self):
        """Read the reports in the data folder, replacing what's loaded."""
        found, notes = reconcile.load_folder(self.data_dir) if self.data_dir else ({}, [])
        if not found:
            notes.append(f"No reports in the data folder ({self.data_dir}) — "
                         f"drop the exports there and click Reload data folder.")
        with self.lock:
            self.found = {}
        return self._take(found, notes)

    def _take(self, found, notes):
        with self.lock:
            for kind, value in found.items():
                if kind in self.found:
                    notes.append(f"{self.found[kind][0]}: replaced by {value[0]}.")
                self.found[kind] = value
            self.notes = notes
        return found, notes

    def clear(self, kind=None):
        with self.lock:
            if kind:
                self.found.pop(kind, None)
            else:
                self.found = {}
            self.notes = []

    def state(self):
        with self.lock:
            found = dict(self.found)
            notes = list(self.notes)
        result = reconcile.reconcile_files(found)
        return {
            "data_dir": str(self.data_dir) if self.data_dir else None,
            "files": {k: {"name": v[0], "lines": len(v[1])} for k, v in found.items()},
            "notes": notes,
            "result": _jsonable(result),
        }

    def workbook(self, hide_cancelled=False):
        with self.lock:
            found = dict(self.found)
        result = reconcile.reconcile_files(found)
        if result is None:
            raise ValueError("load a Labor Distribution report first")
        return reconcile.export_workbook(result, {k: v[0] for k, v in found.items()},
                                         hide_cancelled=hide_cancelled)


def _jsonable(x):
    """Tuples (account segments) become hyphenated text for the page."""
    if isinstance(x, dict):
        return {k: _jsonable(v) for k, v in x.items()}
    if isinstance(x, list):
        return [_jsonable(v) for v in x]
    if isinstance(x, tuple):
        return "-".join(x)
    return x


def make_handler(session):
    class Handler(BaseHTTPRequestHandler):
        server_version = "SalaryReconciliation/1.0"

        def _send(self, code, body, ctype="application/json", headers=None):
            data = body if isinstance(body, bytes) else body.encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def _send_file(self, path):
            real = path.resolve()
            if not str(real).startswith(str(STATIC_DIR)) or not real.is_file():
                self._send(404, json.dumps({"error": "not found"}))
                return
            ctype = CONTENT_TYPES.get(real.suffix, "application/octet-stream")
            self._send(200, real.read_bytes(), ctype)

        def _local_caller(self):
            """Refuse anything that isn't this page talking to its own
            server: a web page you visit can aim a request at localhost, and
            Host/Origin is how it gives itself away."""
            for header in ("Host", "Origin"):
                value = self.headers.get(header)
                if not value:
                    continue
                host = urlparse(value if "//" in value else "//" + value).hostname
                if host not in ("127.0.0.1", "::1", "localhost"):
                    self._send(403, json.dumps(
                        {"error": f"refusing a request with {header}: {value}"}))
                    return False
            return True

        def do_GET(self):
            if not self._local_caller():
                return
            parts = urlparse(self.path)
            path = parts.path
            if path == "/":
                self._send_file(STATIC_DIR / "index.html")
            elif path.startswith("/static/"):
                self._send_file(STATIC_DIR / path[len("/static/"):])
            elif path == "/api/state":
                try:
                    self._send(200, json.dumps(session.state()))
                except Exception as exc:
                    self._send(500, json.dumps({"error": str(exc)}))
            elif path == "/api/export":
                query = parse_qs(parts.query)
                try:
                    data = session.workbook(
                        hide_cancelled=(query.get("hide_cancelled") or ["0"])[0] in ("1", "true"))
                except Exception as exc:
                    self._send(400, json.dumps({"error": str(exc)}))
                    return
                name = f"Salary reconciliation {datetime.now():%Y-%m-%d %H%M}.xlsx"
                self._send(200, data, XLSX_TYPE,
                           {"Content-Disposition": f'attachment; filename="{name}"'})
            else:
                self._send(404, json.dumps({"error": "not found"}))

        def do_POST(self):
            if not self._local_caller():
                return
            parts = urlparse(self.path)
            query = parse_qs(parts.query)
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if length > MAX_UPLOAD_BYTES:
                    raise ValueError("file too large")
                body = self.rfile.read(length)
                if parts.path == "/api/file":
                    _, notes = session.add(unquote((query.get("name") or ["file"])[0]), body)
                    self._send(200, json.dumps({"ok": True, "notes": notes}))
                elif parts.path == "/api/load-folder":
                    _, notes = session.load_folder()
                    self._send(200, json.dumps({"ok": True, "notes": notes}))
                elif parts.path == "/api/clear":
                    kind = (query.get("kind") or [None])[0]
                    session.clear(kind)
                    self._send(200, json.dumps({"ok": True}))
                else:
                    self._send(404, json.dumps({"error": "not found"}))
            except Exception as exc:
                self._send(400, json.dumps({"error": str(exc)}))

        def log_message(self, fmt, *args):
            pass  # keep the window quiet

    return Handler


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=8790)
    ap.add_argument("--data", type=Path, default=DEFAULT_DATA_DIR,
                    help="folder the reports are dropped into (default: ./data)")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    args.data.mkdir(parents=True, exist_ok=True)
    handler = make_handler(Session(args.data))
    httpd = None
    for candidate in range(args.port, args.port + 10):
        try:
            httpd = ThreadingHTTPServer(("127.0.0.1", candidate), handler)
            break
        except OSError:
            continue
    if httpd is None:
        raise SystemExit(f"Could not bind a port in {args.port}-{args.port + 9}.")

    url = f"http://127.0.0.1:{httpd.server_address[1]}"
    print(f"Salary Reconciliation running at {url}  (close this window or Ctrl-C to stop)")
    print(f"Reports are read from {args.data}")
    if not args.no_browser:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
