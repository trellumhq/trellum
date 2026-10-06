"""End-to-end check against a running portal (web + worker + postgres).

Proves the sacred pipeline under Django: session login, enqueue over the
API, the worker builds through the bundled framework, the output carries the
host-contract canaries, and the page is served with permission checks.

Usage:
    python scripts/e2e.py build    # login, enqueue, wait, assert everything
    python scripts/e2e.py verify   # login, assert the built page still serves
                                   # (run after restarting the web process)

Env: E2E_BASE (default http://127.0.0.1:8060), E2E_EMAIL, E2E_PASSWORD,
     E2E_ORG (default demo), E2E_STUDIO (default demo),
     E2E_REPORT (default player-overview).
"""
from __future__ import annotations

import os
import sys
import time

import requests

BASE = os.environ.get("E2E_BASE", "http://127.0.0.1:8060").rstrip("/")
EMAIL = os.environ.get("E2E_EMAIL", "e2e@demo.local")
PASSWORD = os.environ.get("E2E_PASSWORD", "E2e-pass-w0rd")
ORG = os.environ.get("E2E_ORG", "demo")
STUDIO = os.environ.get("E2E_STUDIO", "demo")
REPORT = os.environ.get("E2E_REPORT", "player-overview")
PREFIX = f"{BASE}/s/{ORG}/{STUDIO}"


def fail(msg: str) -> None:
    print(f"E2E FAIL: {msg}", flush=True)
    sys.exit(1)


def check(cond: bool, msg: str) -> None:
    if not cond:
        fail(msg)
    print(f"  ok: {msg}", flush=True)


def login(s: requests.Session) -> None:
    r = s.get(f"{BASE}/login", timeout=10)
    check(r.status_code == 200, "login page loads")
    token = s.cookies.get("csrftoken")
    check(bool(token), "csrftoken cookie set")
    r = s.post(
        f"{BASE}/login",
        data={"email": EMAIL, "password": PASSWORD, "csrfmiddlewaretoken": token},
        headers={"Referer": f"{BASE}/login"},
        timeout=10,
    )
    me = s.get(f"{BASE}/api/me", timeout=10)
    check(me.status_code == 200 and me.json().get("authenticated"), "session login works")


def csrf_headers(s: requests.Session) -> dict:
    return {"X-CSRFToken": s.cookies.get("csrftoken", ""), "Referer": BASE + "/"}


def assert_dashboard(s: requests.Session) -> None:
    r = s.get(f"{PREFIX}/", timeout=10)
    check(r.status_code == 200, "dashboard page renders (static manifest intact)")
    check('id="portal-ctx"' in r.text, "dashboard carries the PORTAL_CTX blob")
    check('id="globalShell"' in r.text, "dashboard carries the global shell")


def assert_page(s: requests.Session) -> None:
    assert_dashboard(s)
    r = s.get(f"{PREFIX}/r/{REPORT}/index.html", timeout=10)
    check(r.status_code == 200, "report HTML serves with session auth")
    html = r.text
    check("_fwHasHost=true" in html, "canary _fwHasHost=true present")
    check("fw-back-link" in html, "canary fw-back-link present")
    check(f"/s/{ORG}/{STUDIO}/" in html, "back-link points at the studio dashboard")
    # Anonymous access must NOT serve the page.
    anon = requests.get(f"{PREFIX}/r/{REPORT}/index.html", timeout=10, allow_redirects=False)
    check(anon.status_code in (301, 302, 401), "anonymous request is turned away")


def build() -> None:
    s = requests.Session()
    login(s)

    reg = s.get(f"{PREFIX}/api/registry", timeout=10).json()
    slugs = [e["slug"] for e in reg["reports"]]
    check(REPORT in slugs, f"registry lists {REPORT} (got {slugs})")

    r = s.post(f"{PREFIX}/api/reports/{REPORT}/run", headers=csrf_headers(s), timeout=10)
    check(r.status_code == 200 and r.json().get("ok"), f"run enqueued ({r.text[:100]})")

    deadline = time.time() + 300
    last_state = ""
    while time.time() < deadline:
        st = s.get(f"{PREFIX}/api/reports/{REPORT}/status", timeout=10).json()
        state = st["state"]
        hist = st.get("history") or []
        if state != last_state:
            print(f"  ... state={state}", flush=True)
            last_state = state
        if state == "idle" and hist:
            check(
                hist[0]["status"] == "success",
                f"run finished successfully (got {hist[0]['status']}: "
                f"{(s.get(f'{PREFIX}/api/reports/{REPORT}/log', timeout=10).json().get('stderr') or '')[-800:]})",
            )
            break
        time.sleep(3)
    else:
        fail("run did not finish within 300s")

    # The registry payload is cached server-side for ~5s; retry briefly.
    entry = None
    for _ in range(8):
        reg = s.get(f"{PREFIX}/api/registry", timeout=10).json()
        entry = next(e for e in reg["reports"] if e["slug"] == REPORT)
        if entry["last_status"] == "success":
            break
        time.sleep(2)
    check(entry["last_status"] == "success", "registry shows last_status=success")
    check(entry["has_output"] is True, "registry shows has_output")

    assert_page(s)
    print("E2E BUILD: all checks passed", flush=True)


def verify() -> None:
    s = requests.Session()
    login(s)
    assert_page(s)
    print("E2E VERIFY: output survived restart", flush=True)


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "build"
    {"build": build, "verify": verify}[mode]()
