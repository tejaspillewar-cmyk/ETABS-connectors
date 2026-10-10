"""
Opens the finished report in the report app (etabs-report) in the browser.

A tiny local web server (127.0.0.1 only, random port) serves the built app and the report JSON; the
app loads it from /api/report, so there is nothing to import or export by hand. The server lives as
long as the ETABS Live window; edits made in the app are kept in the browser (autosave) and are
finished by Export Excel in the app.
"""
import http.server
import json
import mimetypes
import os
import threading
import webbrowser


def find_app_dist():
    here = os.path.dirname(os.path.abspath(__file__))
    for p in (os.environ.get("ETABS_REPORT_APP", ""),
              os.path.join(here, "..", "etabs-report", "dist"),
              os.path.join(here, "report_app", "dist")):
        if p and os.path.isfile(os.path.join(p, "index.html")):
            return os.path.abspath(p)
    return None


class ReportServer:
    def __init__(self, dist_dir):
        self.dist = dist_dir
        self.report_path = None
        self.httpd = None

    def _handler(self):
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body, ctype):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                path = self.path.split("?")[0]
                if path == "/api/report":
                    try:
                        with open(outer.report_path, "rb") as fh:
                            return self._send(200, fh.read(), "application/json")
                    except OSError:
                        return self._send(404, b'{"error":"no report"}', "application/json")
                rel = "index.html" if path in ("/", "") else path.lstrip("/")
                full = os.path.abspath(os.path.join(outer.dist, rel))
                if not full.startswith(outer.dist) or not os.path.isfile(full):
                    return self._send(404, b"not found", "text/plain")
                with open(full, "rb") as fh:
                    self._send(200, fh.read(), mimetypes.guess_type(full)[0] or "application/octet-stream")

        return H

    def start(self):
        if self.httpd is None:
            self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
            threading.Thread(target=self.httpd.serve_forever, daemon=True, name="report-server").start()
        return self.httpd.server_address[1]

    def open_report(self, report_path):
        """Serve `report_path` and open the app on it. Returns the URL."""
        self.report_path = report_path
        url = f"http://127.0.0.1:{self.start()}/?etabs=1&t={int(os.path.getmtime(report_path))}"
        webbrowser.open(url)
        return url

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd = None
