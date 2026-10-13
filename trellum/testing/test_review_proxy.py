"""Security boundary tests for the standalone phone review proxy."""

import http.server
import threading
import unittest
import urllib.error
import urllib.request

from trellum.demo.tools.review_proxy import ReviewProxy


class _Upstream(http.server.BaseHTTPRequestHandler):
    seen = []

    def log_message(self, *_args):
        pass

    def _reply(self):
        type(self).seen.append((self.command, self.path, self.headers.get("Host"),
                                self.headers.get("Origin"), self.headers.get("Cookie"),
                                self.headers.get("X-Review-Key")))
        body = ("asset" if self.path.endswith("review.js") else "ok").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_GET = _reply
    do_POST = _reply


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class ReviewProxyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.upstream = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Upstream)
        cls.upstream_thread = threading.Thread(target=cls.upstream.serve_forever, daemon=True)
        cls.upstream_thread.start()
        cls.proxy = http.server.ThreadingHTTPServer(("127.0.0.1", 0), ReviewProxy)
        cls.proxy.access_key = "one-time-key"
        cls.proxy.cookie_token = "session-cookie-secret"
        cls.proxy.cookie_name = "trellum_review_test"
        cls.proxy.cookie_value = "trellum_review_test=session-cookie-secret; HttpOnly; SameSite=Lax; Path=/"
        cls.proxy.report_path = "/example-report/index.html"
        cls.proxy.upstream_port = cls.upstream.server_port
        cls.proxy.expected_host = f"127.0.0.1:{cls.proxy.server_port}"
        cls.proxy_thread = threading.Thread(target=cls.proxy.serve_forever, daemon=True)
        cls.proxy_thread.start()
        cls.base = f"http://127.0.0.1:{cls.proxy.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.proxy.shutdown()
        cls.proxy.server_close()
        cls.upstream.shutdown()
        cls.upstream.server_close()

    def setUp(self):
        _Upstream.seen.clear()

    def request(self, path, *, method="GET", cookie=None, origin=None, site=None, data=None, headers=None):
        headers = dict(headers or {})
        if cookie:
            headers["Cookie"] = cookie
        if origin:
            headers["Origin"] = origin
        if site:
            headers["Sec-Fetch-Site"] = site
        req = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            return urllib.request.build_opener(_NoRedirect).open(req, timeout=3)
        except urllib.error.HTTPError as exc:
            return exc

    def test_unauthenticated_page_and_asset_are_forbidden(self):
        self.assertEqual(self.request("/example-report/index.html").status, 403)
        self.assertEqual(self.request("/example-report/data.json").status, 403)
        self.assertEqual(_Upstream.seen, [])

    def test_bootstrap_sets_cookie_and_redirects_to_clean_page(self):
        response = self.request("/example-report/index.html?review_key=one-time-key")
        self.assertEqual(response.status, 303)
        self.assertEqual(response.headers["Location"], "/example-report/index.html")
        self.assertIn("HttpOnly", response.headers["Set-Cookie"])
        self.assertIn("SameSite=Lax", response.headers["Set-Cookie"])
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")
        self.assertEqual(_Upstream.seen, [])

    def test_authenticated_assets_and_status_are_forwarded_without_cookie(self):
        cookie = "trellum_review_test=session-cookie-secret"
        for path in ("/example-report/index.html", "/_fw/review.js", "/_fw/review/status"):
            response = self.request(path, cookie=cookie)
            self.assertEqual(response.status, 200)
        self.assertEqual(len(_Upstream.seen), 3)
        self.assertTrue(all(entry[4] is None for entry in _Upstream.seen))
        self.assertEqual(_Upstream.seen[0][1], "/example-report/index.html")

    def test_linked_document_navigation_works_but_cross_site_api_is_denied(self):
        cookie = "trellum_review_test=session-cookie-secret"
        navigation = {"Sec-Fetch-Mode": "navigate", "Sec-Fetch-Dest": "document"}
        with self.request("/example-report/index.html", cookie=cookie, site="cross-site",
                          headers=navigation) as response:
            self.assertEqual(response.status, 200)
        for path in ("/_fw/review/status", "/_fw/review.js", "/example-report/data.json"):
            with self.request(path, cookie=cookie, site="cross-site", headers=navigation) as response:
                self.assertEqual(response.status, 403)
        self.assertEqual(len(_Upstream.seen), 1)

    def test_feedback_rewrites_host_and_origin_and_drops_credentials(self):
        cookie = "trellum_review_test=session-cookie-secret"
        origin = self.base
        response = self.request("/_fw/review/feedback", method="POST", cookie=cookie,
                                origin=origin, site="same-origin", data=b"{}")
        self.assertEqual(response.status, 200)
        method, _path, host, upstream_origin, upstream_cookie, key = _Upstream.seen[0]
        self.assertEqual(method, "POST")
        self.assertEqual(host, f"127.0.0.1:{self.upstream.server_port}")
        self.assertEqual(upstream_origin, f"http://127.0.0.1:{self.upstream.server_port}")
        self.assertIsNone(upstream_cookie)
        self.assertIsNone(key)

    def test_invalid_origin_and_cross_site_write_are_rejected(self):
        cookie = "trellum_review_test=session-cookie-secret"
        self.assertEqual(self.request("/_fw/review/feedback", method="POST", cookie=cookie,
                                      origin="http://attacker.invalid", data=b"{}").status, 403)
        self.assertEqual(self.request("/_fw/review/feedback", method="POST", cookie=cookie,
                                      origin=self.base, site="cross-site", data=b"{}").status, 403)
        self.assertEqual(_Upstream.seen, [])

    def test_privileged_review_and_analysis_paths_are_denied(self):
        cookie = "trellum_review_test=session-cookie-secret"
        for path in ("/_fw/review/poll", "/_fw/review/last", "/_fw/review/reply",
                     "/_fw/review/start", "/_fw/review/analysis/start", "/_fw/review/analysis/end",
                     "/_fw/live-query"):
            self.assertEqual(self.request(path, cookie=cookie).status, 403)
        self.assertEqual(self.request("/_fw/review/end", method="POST", cookie=cookie,
                                      origin=self.base, data=b"{}").status, 200)
        self.assertEqual(len(_Upstream.seen), 1)

    def test_workspace_browser_routes_are_allowed_but_agent_routes_remain_private(self):
        cookie = "trellum_review_test=session-cookie-secret"
        for path in ("/_fw/review/workspace", "/_fw/review/workspace/state",
                     "/_fw/review/workspace.js", "/_fw/review/workspace-bridge.js",
                     "/_fw/review/workspace.css"):
            with self.request(path, cookie=cookie) as response:
                self.assertEqual(response.status, 200)
        for path in ("/_fw/review/workspace/open", "/_fw/review/workspace/capture",
                     "/_fw/review/workspace/feedback"):
            with self.request(path, method="POST", cookie=cookie,
                              origin=self.base, data=b"{}") as response:
                self.assertEqual(response.status, 200)
        with self.request("/_fw/review/workspace/poll", cookie=cookie) as response:
            self.assertEqual(response.status, 403)
        with self.request("/_fw/review/poll", cookie=cookie) as response:
            self.assertEqual(response.status, 403)

    def test_key_in_other_query_is_never_forwarded(self):
        cookie = "trellum_review_test=session-cookie-secret"
        response = self.request("/example-report/data.json?review_key=one-time-key", cookie=cookie)
        self.assertEqual(response.status, 403)
        self.assertEqual(_Upstream.seen, [])


if __name__ == "__main__":
    unittest.main()
