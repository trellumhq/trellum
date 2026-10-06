"""`serve`: the output server, and finding one already running."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _serve_links(port: int, output_base: Path) -> None:
    print(f"Serving at http://localhost:{port}/")
    for entry in sorted(output_base.iterdir()):
        if (entry.is_dir() and not entry.name.startswith(("_", "."))
                and (entry / "index.html").exists()):
            print(f"  {entry.name:<22} http://localhost:{port}/{entry.name}/index.html")

def _find_our_server(output_base: Path,
                     skip: set[int] | None = None) -> int | None:
    """The port of a live server for this output directory, if any.

    Cheap before expensive: a closed port is settled by a socket probe in
    microseconds, and only listening ports pay the HTTP identity request.
    ``skip`` lets a poll loop memoise ports already identified as somebody
    else's -- Docker sits on 8051 here and eats the full probe timeout on
    every sweep otherwise.
    """
    from trellum import runner

    ours = runner._norm_dir(str(output_base))
    for candidate in range(runner.DEFAULT_PORT,
                           runner.DEFAULT_PORT + runner.PORT_SEARCH_SPAN):
        if skip and candidate in skip:
            continue
        if not runner._is_port_in_use(candidate):
            continue
        served = runner._served_dir_at(candidate)
        if served == ours:
            return candidate
        if skip is not None:
            # Not ours and never will be: a framework server for a different
            # directory keeps its directory, and a foreign process (served is
            # None) stays foreign. Do not pay its probe again.
            skip.add(candidate)
    return None

def _cmd_serve(args: argparse.Namespace) -> int:
    """Serve this project's built reports.

    Foreground by default. ``--background`` exists for the end of a working
    session: it starts the server detached, waits until it answers, prints
    the URLs and RETURNS -- so the last thing an agent does before saying
    "done" can be handing the user a clickable link, without blocking and
    without the hand-rolled `> srv.log 2>&1 &` dance every measured session
    invented for itself. Idempotent: a server already running for this
    output directory is reused, not duplicated (they identify themselves --
    see /_fw/server.json in runner.py).
    """
    from trellum.project import get_project_root

    root = Path(get_project_root())
    output_base = (root / "output").resolve()
    if not output_base.is_dir():
        print(f"nothing built yet: {output_base} does not exist. "
              f"Build first: python -m trellum.run reports/<slug> --no-serve",
              file=sys.stderr)
        return 1

    if not args.background:
        from trellum import runner
        port = runner.resolve_port(args.port, str(output_base))
        runner._serve_all(str(output_base), port)      # blocks until Ctrl+C
        return 0

    port, reused = _ensure_background_server(output_base, args.port)
    if port is None:
        print("the background server did not answer within 45s -- try "
              "foreground: python -m trellum serve", file=sys.stderr)
        return 1
    _serve_links(port, output_base)
    if reused:
        print("\n(already running -- reusing it)")
    else:
        print("\nGive the user the top link. The server keeps running; "
              "re-running this command reuses it.")
    return 0

def _ensure_background_server(
    output_base: Path, port: int | None
) -> tuple[int | None, bool]:
    """Find a running framework server for this output dir, or spawn one.

    Returns ``(port, reused)`` -- ``(None, False)`` when a spawned child
    never came up. Shared by ``serve --background`` and ``review start``.
    """
    existing = _find_our_server(output_base)
    if existing is not None:
        return existing, True

    import subprocess
    import time as _time

    from trellum.project import get_project_root

    argv = [sys.executable, "-m", "trellum", "serve"]
    if port:
        argv += ["--port", str(port)]
    if os.name == "nt":
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP: no console, survives
        # this process and the shell that launched it.
        kwargs: dict = {"creationflags": 0x00000008 | 0x00000200}
    else:
        kwargs = {"start_new_session": True}
    subprocess.Popen(argv, cwd=str(get_project_root()),
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, **kwargs)

    # The child picks its own port (the requested one may be busy); discover
    # it the same way anything else does -- ask the identity endpoint. The
    # deadline is generous because a cold start can legitimately be slow:
    # the child may have to stop a stale twin (a 3s wait inside its claim)
    # and pay identity probes on every busy port in the span before binding.
    deadline = _time.monotonic() + 45
    foreign: set[int] = set()
    while _time.monotonic() < deadline:
        found = _find_our_server(output_base, skip=foreign)
        if found is not None:
            return found, False
        _time.sleep(0.3)
    return None, False
