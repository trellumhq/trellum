"""The preview HTTP server: one report, or the whole output tree."""

from __future__ import annotations

import os
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from trellum.artifacts import is_private_artifact
from trellum.runner.ports import (
    _answer_identity,
    _claim_port,
)


class _PublicReportHandler(SimpleHTTPRequestHandler):
    def _deny_private(self) -> bool:
        if is_private_artifact(self.path) or is_private_artifact(os.path.realpath(self.translate_path(self.path))):
            self.send_error(404)
            return True
        return False

    def send_head(self):
        # SimpleHTTPRequestHandler's HEAD path must enforce the same boundary.
        if self._deny_private():
            return None
        return super().send_head()


def _render_output_index(output_base: str) -> bytes:
    """The index page for a whole output directory, for the dev server.

    Shares trellum.reporting.gallery with the published site rather than rendering
    its own: two index pages that drift is how you discover at release time
    that what you developed against is not what you shipped.

    Rendered per request so a newly built report appears without a restart.
    """
    from trellum.reporting.gallery import collect_reports, render_gallery

    base = Path(output_base)
    reports = collect_reports(base) if base.is_dir() else []
    return render_gallery(reports, "Reports").encode("utf-8")

def _serve_all(output_base: str, port: int) -> None:
    """Serve every generated report under ``output_base`` on one port.

    The framework's own multi-report viewer. A host offers far more
    (scheduling, auth, run-on-demand), but the framework must remain usable
    on its own, so browsing a full output directory cannot depend on it.
    """
    import gzip as _gzip

    from trellum.rendering.cdn import serve_vendor_request

    _claim_port(port, output_base)

    _COMPRESSIBLE = {".json", ".html", ".js", ".css", ".svg"}

    class Handler(_PublicReportHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=output_base, **kwargs)

        def log_message(self, format, *args):
            pass

        def do_GET(self):
            if self._deny_private():
                return
            from trellum import review
            from trellum.runner import live_query_dev
            if _answer_identity(self, output_base):
                return
            if serve_vendor_request(self):
                return
            if review.handle_review_request(self, output_base):
                return
            # The admin-badge companion of the injection below: only ever
            # requested by a page live_query_dev has itself armed.
            if live_query_dev.handle_auth_me_request(self):
                return

            # Generate the index fresh on each request so newly built reports
            # appear without restarting the server.
            path = self.path.split("?")[0]
            if path in ("/", "/index.html"):
                payload = _render_output_index(output_base)
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(payload)
                return

            # Review overlay injection sits AFTER the gallery branch above:
            # the gallery index must never get the overlay, only reports.
            if review.maybe_serve_injected_html(self, output_base):
                return

            # Same idea, independent feature: a report that declared a live
            # query gets window._fwHasHost/_fwLiveQueryUrl stamped into its
            # served HTML so its FilterBar arms — see
            # trellum.runner.live_query_dev. A report with nothing live
            # falls straight through, unchanged.
            if live_query_dev.maybe_serve_injected_html(self):
                return

            fs_path = self.translate_path(self.path)
            if not os.path.isfile(fs_path):
                return super().do_GET()

            ext = os.path.splitext(fs_path)[1].lower()
            if ext in _COMPRESSIBLE and "gzip" in self.headers.get("Accept-Encoding", ""):
                ct = {
                    ".html": "text/html", ".json": "application/json",
                    ".js": "application/javascript", ".css": "text/css",
                    ".svg": "image/svg+xml",
                }.get(ext, "application/octet-stream")
                with open(fs_path, "rb") as fh:
                    body = _gzip.compress(fh.read())
                self.send_response(200)
                self.send_header("Content-Type", ct)
                self.send_header("Content-Encoding", "gzip")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(body)
                return

            return super().do_GET()

        def do_POST(self):
            from trellum import review
            if review.handle_review_request(self, output_base):
                return
            from trellum.runner import live_query_dev
            if live_query_dev.handle_live_query_request(self):
                return
            self.send_error(404)

        def end_headers(self):
            # Reports get rebuilt constantly during development. A cached
            # index.html paired with a freshly fetched data.json (or the
            # reverse) produces components wired to datasets that no longer
            # exist -- filters that silently do nothing. Never cache report
            # output; /_vendor/ is content-addressed and handled separately.
            if not self.path.startswith("/_vendor/"):
                self.send_header("Cache-Control", "no-cache")
            super().end_headers()

    from trellum.reporting.gallery import collect_reports

    reports = collect_reports(Path(output_base)) if os.path.isdir(output_base) else []
    print(f"\nServing {len(reports)} report(s) at http://localhost:{port}", flush=True)
    for r in reports:
        print(f"  http://localhost:{port}/{r['_slug']}/index.html", flush=True)
    print("\nPress Ctrl+C to stop.", flush=True)

    server = _ReuseHTTPServer(("", port), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.", flush=True)
        server.shutdown()

class _ReuseHTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer with address reuse where reuse means what it says.

    On POSIX, SO_REUSEADDR lets a restart rebind a port still in TIME_WAIT --
    exactly what a dev server wants. On Windows the same flag lets a socket
    bind a port that another socket is ACTIVELY listening on, silently: two
    servers both believe they own the port and traffic reaches one of them.
    So: reuse on POSIX, a loud bind failure on Windows, which the claim logic
    above turns into an explanation rather than a stack trace.
    """
    allow_reuse_address = os.name != "nt"

def _serve(output_dir: str, port: int):
    """Start a simple HTTP server for the report output with gzip support."""
    import gzip as _gzip

    _claim_port(port, output_dir)

    _COMPRESSIBLE = {".json", ".html", ".js", ".css", ".svg"}

    class Handler(_PublicReportHandler):
        def __init__(self, *args, **kwargs):
            self._gzip_handled = False
            super().__init__(*args, directory=output_dir, **kwargs)

        def log_message(self, format, *args):
            pass

        def do_GET(self):
            if self._deny_private():
                return
            from trellum import review
            from trellum.rendering.cdn import serve_vendor_request
            from trellum.runner import live_query_dev
            if _answer_identity(self, output_dir):
                return
            if serve_vendor_request(self):
                return
            if review.handle_review_request(self, output_dir):
                return
            # The admin-badge companion of the injection below: only ever
            # requested by a page live_query_dev has itself armed.
            if live_query_dev.handle_auth_me_request(self):
                return
            if review.maybe_serve_injected_html(self, output_dir):
                return
            if live_query_dev.maybe_serve_injected_html(self):
                return

            path = self.translate_path(self.path)
            if not os.path.isfile(path):
                return super().do_GET()

            ext = os.path.splitext(path)[1].lower()
            accept_enc = self.headers.get("Accept-Encoding", "")

            if ext in _COMPRESSIBLE and "gzip" in accept_enc:
                ct = {
                    ".html": "text/html",
                    ".json": "application/json",
                    ".js": "application/javascript",
                    ".css": "text/css",
                    ".svg": "image/svg+xml",
                }.get(ext, "application/octet-stream")

                gz_path = path + ".gz"
                if os.path.isfile(gz_path) and os.path.getmtime(gz_path) >= os.path.getmtime(path):
                    with open(gz_path, "rb") as f:
                        compressed = f.read()
                else:
                    with open(path, "rb") as f:
                        raw = f.read()
                    compressed = _gzip.compress(raw, compresslevel=6)

                self.send_response(200)
                self.send_header("Content-Type", ct)
                self.send_header("Content-Encoding", "gzip")
                self.send_header("Content-Length", str(len(compressed)))
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
                self._gzip_handled = True
                self.end_headers()
                self.wfile.write(compressed)
            else:
                super().do_GET()

        def do_POST(self):
            from trellum import review
            if review.handle_review_request(self, output_dir):
                return
            from trellum.runner import live_query_dev
            if live_query_dev.handle_live_query_request(self):
                return
            self.send_error(404)

        def end_headers(self):
            if not self._gzip_handled:
                self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self._gzip_handled = False
            super().end_headers()

    server = _ReuseHTTPServer(("", port), Handler)
    print(f"Serving at http://localhost:{port} -- press Ctrl+C to stop", flush=True)
    server.serve_forever()

def _schedule_refreshes(report_dir: str, output_dir: str, interval_sec: int):
    """Re-run the report on a timer, updating data.json."""

    # Imported inside the loop, not at module scope: execute.py starts a
    # server through this module, so a top-level import would close the cycle.
    from trellum.runner.execute import run_report

    def loop():
        while True:
            time.sleep(interval_sec)
            try:
                run_report(report_dir, output_dir)
            except Exception as e:
                print(f"[REFRESH ERROR] {e}", flush=True)

    t = threading.Thread(target=loop, daemon=True)
    t.start()
