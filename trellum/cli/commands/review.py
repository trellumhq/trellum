"""The `review` command group: the agent's half of the review loop.

    The browser half is static/js/review/, and the session state it
    talks to is trellum.review."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from trellum.cli.commands.serve import (
    _ensure_background_server,
    _find_our_server,
)


def _review_http(port: int, method: str, path: str, payload: dict | None = None,
                 timeout: float = 10.0) -> tuple[int, dict]:
    """One JSON request to the review endpoints of a running server."""
    import json as _json
    import urllib.error
    import urllib.request

    url = f"http://127.0.0.1:{port}{path}"
    data = _json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, _json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, _json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}

def _review_port_or_explain(output_base: Path) -> int | None:
    port = _find_our_server(output_base)
    if port is None:
        print("no framework server is running for this project -- start the "
              "loop with: python -m trellum review start reports/<slug>",
              file=sys.stderr)
    return port

def _resolve_report_dir(report: str) -> Path | None:
    """Accept `reports/<slug>` (any path) or a bare slug."""
    from trellum.project import get_project_root

    p = Path(report)
    if p.is_dir() and (p / "report.yaml").exists():
        return p.resolve()
    candidate = Path(get_project_root()) / "reports" / report
    if candidate.is_dir() and (candidate / "report.yaml").exists():
        return candidate.resolve()
    print(f"no report at '{report}' -- expected a report directory "
          f"(reports/<slug> with a report.yaml) or a bare slug", file=sys.stderr)
    return None

def _cmd_review_start(args: argparse.Namespace) -> int:
    import webbrowser

    from trellum import runner
    from trellum.project import get_project_root
    from trellum.report import BaseReport

    report_dir = _resolve_report_dir(args.report)
    if report_dir is None:
        return 1
    config = BaseReport.load_config(str(report_dir))
    slug = config.get("slug", report_dir.name)

    root = Path(get_project_root())
    output_base = (root / "output").resolve()
    built = output_base / slug / "index.html"
    if args.rebuild or not built.exists():
        runner.run_report(str(report_dir))

    port, _reused = _ensure_background_server(output_base, args.port)
    if port is None:
        print("the background server did not answer within 45s -- try "
              "foreground: python -m trellum serve", file=sys.stderr)
        return 1

    code, resp = _review_http(port, "POST", "/_fw/review/start", {})
    # A server that predates review mode is still holding the port: it 404s
    # the path -- or 501s the POST outright (SimpleHTTPRequestHandler with
    # no do_POST at all).
    if code in (404, 501):
        if args.restart_server:
            runner._claim_port(port, str(output_base))
            port, _reused = _ensure_background_server(output_base, args.port)
            if port is not None:
                code, resp = _review_http(port, "POST", "/_fw/review/start", {})
        if code in (404, 501) or port is None:
            print("the running server predates review mode -- restart it with: "
                  "python -m trellum review start "
                  f"{args.report} --restart-server", file=sys.stderr)
            return 1
    if code != 200:
        print(f"could not start review mode: HTTP {code} {resp}", file=sys.stderr)
        return 1

    url = f"http://localhost:{port}/{slug}/index.html"
    if not args.no_browser:
        webbrowser.open(url)
    print(f"Review session #{resp.get('session')} is live: {url}")
    print("\nThe user clicks elements in the browser, queues change requests")
    print("plus an optional chat message, and hits Send.")
    print("\nNow run:  python -m trellum review poll")
    print("It blocks until feedback arrives. When the user is finished:")
    print("          python -m trellum review end")
    return 0

def _print_review_batches(batches: list[dict]) -> None:
    for batch in batches:
        head = batch.get("slug") or "report"
        if batch.get("page"):
            head += f"  ({batch['page']})"
        print(f"\n── feedback on {head} " + "─" * max(1, 46 - len(head)))
        if batch.get("note"):
            print(f"  Message: {batch['note']}")
        for i, item in enumerate(batch.get("items") or [], 1):
            comp = item.get("component") or {}
            sec = item.get("section") or {}
            where = ""
            if sec.get("title"):
                where = f' in section "{sec["title"]}"' + (
                    f' (#{sec["id"]})' if sec.get("id") else "")
            named = ""
            if comp.get("kind"):
                named = f', component {comp.get("id") or "?"} ({comp["kind"]}'
                named += f' "{comp["title"]}")' if comp.get("title") else ")"
            print(f"  [{i}] <{item.get('tag', '?')}>{where}{named}")
            if item.get("selector"):
                print(f"      selector: {item['selector']}")
            if item.get("text"):
                print(f"      text: {item['text'][:120]}")
            data = item.get("data") or {}
            if data.get("type") == "chart-point":
                print(f"      data point: {data.get('series')} @ {data.get('x')} "
                      f"= {data.get('value')}")
                for s in (data.get("stack") or [])[:8]:
                    print(f"        {s.get('label')}: {s.get('value')}")
            elif data.get("type") == "chart-range":
                srs = data.get("series") or []
                print(f"      data range: {data.get('x_from')} -> {data.get('x_to')} "
                      f"({len(srs)} series)")
                for s in srs[:4]:
                    print(f"        {s.get('label')}: n={s.get('n')} "
                          f"min={s.get('min')} max={s.get('max')}")
            elif data:
                import json as _json
                print(f"      data: {_json.dumps(data)[:200]}")
            print(f"      prompt: {item.get('prompt', '')}")

def _slug_of(report: str) -> str:
    """A bare slug stays itself; a report directory resolves to its slug."""
    p = Path(report)
    if p.is_dir() and (p / "report.yaml").exists():
        from trellum.report import BaseReport

        return BaseReport.load_config(str(p)).get("slug", p.name)
    return report

def _cmd_review_poll(args: argparse.Namespace) -> int:
    import json as _json
    import time as _time
    from urllib.parse import quote

    from trellum.project import get_project_root

    output_base = (Path(get_project_root()) / "output").resolve()
    port = _review_port_or_explain(output_base)
    if port is None:
        return 1
    slug = _slug_of(args.report) if args.report else ""
    slug_q = f"&slug={quote(slug)}" if slug else ""

    if args.reply:
        code, _resp = _review_http(port, "POST", "/_fw/review/reply",
                                   {"text": args.reply, "slug": slug})
        if code != 200:
            print(f"reply not delivered: HTTP {code}", file=sys.stderr)

    deadline = _time.monotonic() + args.timeout if args.timeout else None
    while True:
        # The server parks each request for up to 30s; with a CLI-level
        # deadline, shrink the park to the remaining budget so `--timeout 2`
        # answers in ~2s, not at the end of a full server round.
        park = 30.0
        if deadline is not None:
            park = max(0.0, min(park, deadline - _time.monotonic()))
        try:
            code, resp = _review_http(port, "GET",
                                      f"/_fw/review/poll?timeout={park:g}{slug_q}",
                                      timeout=park + 5)
        except OSError:
            print("the server went away mid-poll -- restart the loop with "
                  "review start", file=sys.stderr)
            return 1
        if code != 200:
            print(f"poll failed: HTTP {code} {resp}", file=sys.stderr)
            return 1

        if resp.get("status") == "feedback":
            if args.json:
                print(_json.dumps(resp, indent=2))
            else:
                _print_review_batches(resp.get("batches") or [])
                slug = (resp.get("batches") or [{}])[0].get("slug", "<slug>")
                print(f"\nNext: edit the source in reports/{slug}/, then rebuild:")
                print(f"  python -m trellum.run reports/{slug} --no-serve")
                print("(the browser reloads itself) and acknowledge with:")
                print("  python -m trellum review poll "
                      "--reply \"what you changed\"")
            if resp.get("session_ended"):
                print("\nThe user ended the session with this batch -- resolve "
                      "it, but don't expect another round.")
            return 0
        if resp.get("status") == "ended":
            if args.json:
                print(_json.dumps(resp, indent=2))
            else:
                print("The user ended the review session.")
            return 3
        # waiting -- keep polling unless the CLI-level timeout says stop
        if deadline is not None and _time.monotonic() >= deadline:
            if args.json:
                print(_json.dumps({"status": "waiting"}, indent=2))
            else:
                print("no feedback within the timeout")
            return 2

def _cmd_review_reply(args: argparse.Namespace) -> int:
    from trellum.project import get_project_root

    output_base = (Path(get_project_root()) / "output").resolve()
    port = _review_port_or_explain(output_base)
    if port is None:
        return 1
    slug = _slug_of(args.report) if getattr(args, "report", None) else ""
    code, resp = _review_http(port, "POST", "/_fw/review/reply",
                              {"text": args.text, "slug": slug})
    if code != 200:
        print(f"reply not delivered: HTTP {code} {resp}", file=sys.stderr)
        return 1
    print("delivered -- it appears in the browser's chat")
    return 0

def _cmd_review_end(args: argparse.Namespace) -> int:
    from trellum.project import get_project_root

    output_base = (Path(get_project_root()) / "output").resolve()
    port = _review_port_or_explain(output_base)
    if port is None:
        return 1
    code, _resp = _review_http(port, "POST", "/_fw/review/end", {})
    if code != 200:
        print(f"end failed: HTTP {code}", file=sys.stderr)
        return 1
    print("review session ended")
    return 0

def _cmd_review_status(args: argparse.Namespace) -> int:
    import json as _json

    from trellum.project import get_project_root

    output_base = (Path(get_project_root()) / "output").resolve()
    port = _review_port_or_explain(output_base)
    if port is None:
        return 1
    code, resp = _review_http(port, "GET", "/_fw/review/status")
    if code != 200:
        print(f"status failed: HTTP {code}", file=sys.stderr)
        return 1
    if args.json:
        print(_json.dumps(resp, indent=2))
        return 0
    active = "active" if resp.get("active") else (
        "ended" if resp.get("ended") else "inactive")
    agent = (resp.get("agent") or {}).get("state", "?")
    print(f"review: {active} (session #{resp.get('session')}), "
          f"agent {agent}, {resp.get('queued', 0)} batch(es) queued, "
          f"{len(resp.get('replies') or [])} repl(ies)")
    return 0

def _cmd_review_overview(args: argparse.Namespace) -> int:
    print("the live review loop -- verbs:\n"
          "  review start <report>   build if needed, serve, open the browser\n"
          "                          (--rebuild --no-browser --port N "
          "--restart-server)\n"
          "  review poll             block until the user sends feedback\n"
          "                          (--reply TEXT --timeout SECS --json;\n"
          "                           --report SLUG when several agents share\n"
          "                           the project; exit 0 feedback, 2 timeout,\n"
          "                           3 ended)\n"
          "  review reply TEXT       answer into the browser's chat\n"
          "                          (--report SLUG to reach one report only)\n"
          "  review status [--json]  one-line state of the session\n"
          "  review end              end the session from this side\n"
          "\nTargets the whole-output server (trellum serve). See: "
          "python -m trellum guide review")
    return 0
