"""Token-protected, bounded phone access to a local Trellum review server."""

from __future__ import annotations

import argparse
import hmac
import http.server
import secrets
import socket
import urllib.error
import urllib.request
from http.cookies import CookieError, SimpleCookie
from urllib.parse import parse_qsl, quote, urlsplit

MAX_BODY_BYTES = 32 * 1024 * 1024
_REVIEW_GET = {
    "/_fw/review.js", "/_fw/review/status", "/_fw/review/analysis",
    "/_fw/review/workspace", "/_fw/review/workspace/state",
    "/_fw/review/workspace.js", "/_fw/review/workspace-bridge.js",
    "/_fw/review/workspace.css",
}
_REVIEW_POST = {
    "/_fw/review/feedback",
    "/_fw/review/end",
    "/_fw/review/analysis/capture",
    "/_fw/review/workspace/open",
    "/_fw/review/workspace/capture",
    "/_fw/review/workspace/feedback",
}
_HOP_HEADERS = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "transfer-encoding", "upgrade", "host",
}


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ReviewProxy(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "TrellumReviewProxy"
    sys_version = ""

    def _send(self, status: int, body: bytes = b"", headers=()):
        self.send_response(status)
        self.send_header("Content-Length", str(len(body)))
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def _security_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")

    def send_response(self, code, message=None):
        super().send_response(code, message)
        self._security_headers()

    def log_message(self, fmt, *args):
        # Request targets can contain the one-time bootstrap key.
        pass

    def do_GET(self):
        parsed = urlsplit(self.path)
        try:
            pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
        except ValueError:
            self._send(400, b"Bad query")
            return
        if any(key == "review_key" for key, _value in pairs):
            if (parsed.path == self.server.report_path and len(pairs) == 1
                    and hmac.compare_digest(pairs[0][1].encode("utf-8"),
                                            self.server.access_key.encode("utf-8"))):
                self._send(303, headers=(
                    ("Location", getattr(self.server, "landing_path", self.server.report_path)),
                    ("Set-Cookie", self.server.cookie_value),
                ))
                return
            else:
                self._send(403, b"Forbidden")
                return
        self._proxy("GET")

    def do_POST(self):
        self._proxy("POST")

    def do_HEAD(self):
        self._send(405, b"Method not allowed", (("Allow", "GET, POST"),))

    def do_PUT(self):
        self._send(405, b"Method not allowed", (("Allow", "GET, POST"),))

    def do_DELETE(self):
        self._send(405, b"Method not allowed", (("Allow", "GET, POST"),))

    def _proxy(self, method: str):
        path = urlsplit(self.path).path
        if path.startswith("/_fw/") and not (
            (method == "GET" and path in _REVIEW_GET)
            or (method == "POST" and path in _REVIEW_POST)
        ):
            self._send(403, b"Forbidden")
            return

        if self.headers.get("Host") != self.server.expected_host:
            self._send(403, b"Forbidden")
            return
        if (self.headers.get("Transfer-Encoding") is not None
                or (method == "GET" and self.headers.get("Content-Length") not in (None, "0"))):
            self.close_connection = True
            self._send(400, b"Bad request framing")
            return

        if not self._authenticated():
            self._send(403, b"Forbidden")
            return
        origin = self.headers.get("Origin")
        site = self.headers.get("Sec-Fetch-Site", "").lower()
        # A link opened from chat keeps its cross-site navigation metadata
        # through the sign-in redirect. Only the report document may use it;
        # API requests and writes still require the same origin.
        linked_document = (method == "GET" and path == self.server.report_path
                           and origin is None
                           and self.headers.get("Sec-Fetch-Mode") == "navigate"
                           and self.headers.get("Sec-Fetch-Dest") == "document")
        if (site == "cross-site" and not linked_document) or (origin is not None and not self._same_origin(origin)):
            self._send(403, b"Forbidden")
            return
        if method == "POST" and origin is None:
            self._send(403, b"Forbidden")
            return

        body = b""
        if method == "POST":
            lengths = self.headers.get_all("Content-Length", [])
            if (len(lengths) != 1 or self.headers.get("Transfer-Encoding") is not None
                    or not lengths[0].isdigit()):
                self._send(400, b"Bad request framing")
                self.close_connection = True
                return
            length = int(lengths[0])
            if length > MAX_BODY_BYTES:
                self._send(413, b"Request too large")
                self.close_connection = True
                return
            body = self.rfile.read(length)
            if len(body) != length:
                self._send(400, b"Bad request framing")
                self.close_connection = True
                return

        headers = {}
        connection_tokens = set()
        for value in self.headers.get_all("Connection", []):
            connection_tokens.update(part.strip().lower() for part in value.split(","))
        for name, value in self.headers.items():
            low = name.lower()
            if low in _HOP_HEADERS or low in connection_tokens or low in {"cookie", "origin", "sec-fetch-site"}:
                continue
            headers[name] = value
        headers["Host"] = f"127.0.0.1:{self.server.upstream_port}"
        if origin is not None:
            headers["Origin"] = f"http://127.0.0.1:{self.server.upstream_port}"
        target = f"http://127.0.0.1:{self.server.upstream_port}{self.path}"
        request = urllib.request.Request(target, data=body if method == "POST" else None,
                                         headers=headers, method=method)
        opener = urllib.request.build_opener(_NoRedirect)
        try:
            response = opener.open(request, timeout=5)
        except urllib.error.HTTPError as exc:
            response = exc
        except (urllib.error.URLError, TimeoutError, OSError):
            self._send(502, b"Review server unavailable")
            return
        with response:
            response_body = response.read(MAX_BODY_BYTES + 1)
            if len(response_body) > MAX_BODY_BYTES:
                self._send(502, b"Review response too large")
                return
            out_headers = []
            conn_tokens = set()
            for value in response.headers.get_all("Connection", []):
                conn_tokens.update(part.strip().lower() for part in value.split(","))
            for name, value in response.headers.items():
                low = name.lower()
                if low in _HOP_HEADERS or low in conn_tokens or low in {"set-cookie", "content-length"}:
                    continue
                out_headers.append((name, value))
            self._send(response.status, response_body, out_headers)

    def _authenticated(self) -> bool:
        try:
            cookies = SimpleCookie(self.headers.get("Cookie", ""))
            supplied = cookies[self.server.cookie_name].value.encode("utf-8")
        except (KeyError, CookieError, UnicodeEncodeError):
            return False
        return hmac.compare_digest(supplied, self.server.cookie_token.encode("utf-8"))

    def _same_origin(self, origin: str) -> bool:
        try:
            parts = urlsplit(origin)
            return (parts.scheme == "http" and parts.netloc == self.headers.get("Host")
                    and not parts.path and not parts.query and not parts.fragment)
        except ValueError:
            return False


def _valid_bind(value: str) -> str:
    try:
        socket.inet_pton(socket.AF_INET, value)
    except OSError as exc:
        raise argparse.ArgumentTypeError("--bind must be an IPv4 address") from exc
    return value


def _valid_port(value: str) -> int:
    try:
        port = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("port must be an integer from 1 to 65535") from exc
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be an integer from 1 to 65535")
    return port


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bind", required=True, type=_valid_bind)
    parser.add_argument("--port", required=True, type=_valid_port)
    parser.add_argument("--upstream-port", required=True, type=_valid_port)
    parser.add_argument("--report", required=True,
                        help="report slug using lowercase letters, digits, hyphens or underscores")
    parser.add_argument("--workspace", action="store_true",
                        help="open the active analysis workspace after sign-in")
    parser.add_argument("--source", help="initial source report in the workspace")
    args = parser.parse_args(argv)
    import re
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,99}", args.report):
        parser.error("--report must be a valid lowercase report slug")
    if args.source and (not args.workspace or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,99}", args.source)):
        parser.error("--source requires --workspace and a valid lowercase report slug")
    access_key = secrets.token_urlsafe(32)
    cookie_token = secrets.token_urlsafe(32)
    cookie_name = f"trellum_review_{args.port}"
    cookie_value = f"{cookie_name}={cookie_token}; HttpOnly; SameSite=Lax; Path=/"
    server = http.server.ThreadingHTTPServer((args.bind, args.port), ReviewProxy)
    server.access_key = access_key
    server.cookie_token = cookie_token
    server.cookie_name = cookie_name
    server.cookie_value = cookie_value
    server.report_path = ("/_fw/review/workspace" if args.workspace
                          else f"/{quote(args.report)}/index.html")
    server.landing_path = server.report_path + (f"?source={quote(args.source)}" if args.source else "")
    server.upstream_port = args.upstream_port
    server.expected_host = f"{args.bind}:{args.port}"
    display_host = f"[{args.bind}]" if ":" in args.bind else args.bind
    print(f"Phone review URL: http://{display_host}:{args.port}{server.report_path}?review_key={access_key}", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
