"""Which port to serve on, and who already holds it.

    A dev server that silently attaches to another process's output, or
    refuses a port nothing is using, costs more time than it saves -- so
    this asks the OS and the candidate server itself rather than guessing."""

from __future__ import annotations

import json
import os
import sys
import time


def _is_port_in_use(port: int) -> bool:
    """Check if a TCP port is in use on localhost."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0

def _listening_pids(port: int) -> list[int]:
    """PIDs listening on ``port``. Raises if they cannot be determined."""
    import subprocess as _sp

    if os.name == "nt":
        result = _sp.run(
            ["netstat", "-ano", "-p", "tcp"],
            capture_output=True, text=True, timeout=5,
        )
        pids = []
        for line in result.stdout.splitlines():
            parts = line.split()
            # e.g.  TCP    0.0.0.0:8050    0.0.0.0:0    LISTENING    12345
            if len(parts) >= 5 and parts[3].upper() == "LISTENING":
                local = parts[1]
                if local.rsplit(":", 1)[-1] == str(port):
                    pids.append(int(parts[4]))
        return sorted(set(pids))

    result = _sp.run(
        ["lsof", "-ti", f"tcp:{port}"],
        capture_output=True, text=True, timeout=5,
    )
    return [int(p) for p in result.stdout.strip().split("\n") if p.strip()]

def _process_command(pid: int) -> str:
    """Full command line for ``pid``, or "" if it cannot be read."""
    import subprocess as _sp

    try:
        if os.name == "nt":
            script = (
                f"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}' "
                f"-ErrorAction SilentlyContinue).CommandLine"
            )
            result = _sp.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True, text=True, timeout=10,
            )
        else:
            result = _sp.run(
                ["ps", "-p", str(pid), "-o", "command="],
                capture_output=True, text=True, timeout=5,
            )
        return result.stdout.strip()
    except Exception:
        return ""

#: Where a preview server says what it is serving. The port-conflict logic
#: below asks this instead of inspecting command lines: a command line can say
#: "framework server" but it cannot say WHICH one, and that distinction is the
#: whole problem -- a stale previous run of this serve should be replaced, and
#: a live server somebody else is using must not be.
_IDENTITY_PATH = "/_fw/server.json"

def _norm_dir(path: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))

def _answer_identity(handler, served_dir: str) -> bool:
    """Serve the identity endpoint. Returns True if the request was for it."""
    if handler.path.split("?")[0] != _IDENTITY_PATH:
        return False
    body = json.dumps({
        "framework_preview_server": True,
        "output_dir": _norm_dir(served_dir),
        "pid": os.getpid(),
    }).encode("utf-8")
    handler.send_response(200)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    # No Cache-Control here: both serve paths already force no-cache in their
    # end_headers overrides, and a second header would be a duplicate.
    handler.end_headers()
    handler.wfile.write(body)
    return True

def _served_dir_at(port: int) -> str | None:
    """What the framework preview server on ``port`` serves, else None.

    None means "not one of ours": a foreign process, or an old framework
    version without the endpoint. Both get the same treatment -- leave it
    alone -- so an old server is never killed on the strength of a guess.
    """
    import urllib.request

    url = f"http://127.0.0.1:{port}{_IDENTITY_PATH}"
    try:
        with urllib.request.urlopen(url, timeout=1.5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None
    if isinstance(data, dict) and data.get("framework_preview_server"):
        out = data.get("output_dir")
        if out:
            return str(out)
    return None

def _holder_description(port: int) -> str:
    """Who is on ``port``, as well as this machine can say."""
    served = _served_dir_at(port)
    if served:
        return f"another framework preview server, serving {served}"
    try:
        pids = _listening_pids(port)
    except Exception:
        pids = []
    if pids:
        cmd = _process_command(pids[0])
        return f"PID {pids[0]} ({cmd[:80]})" if cmd else f"PID {pids[0]}"
    return "an unidentified process"

def _claim_port(port: int, served_dir: str) -> None:
    """Make ``port`` bindable for a server about to serve ``served_dir``.

    The only process this will ever stop is a previous framework server that
    answers the identity endpoint with the SAME output directory -- a stale
    run of this exact serve, whose replacement keeps the URL stable. Anything
    else on the port, *including another framework server serving different
    output*, is somebody's live server: killing it because it looked like us
    is how a measured benchmark run took down the demo server a person was
    browsing. Identity, then the command line as a second factor before any
    kill; on conflict, explain and exit.
    """
    import signal

    if not _is_port_in_use(port):
        return

    if _served_dir_at(port) == _norm_dir(served_dir):
        try:
            pids = _listening_pids(port)
        except Exception:
            pids = []
        for pid in pids:
            # The identity match already said this is our serve; the command
            # line check is a second factor so that a PID recycled between
            # the probe and the kill cannot take an innocent process with it.
            if _is_our_server(pid):
                print(f"Replacing the previous server for this output on port "
                      f"{port} (PID {pid})", flush=True)
                try:
                    # On Windows os.kill() maps to TerminateProcess; a stale
                    # PID raises OSError rather than ProcessLookupError.
                    os.kill(pid, signal.SIGTERM)
                except OSError:
                    pass
        for _ in range(30):
            if not _is_port_in_use(port):
                return
            time.sleep(0.1)

    print(f"Port {port} is in use by {_holder_description(port)}.", flush=True)
    print("Use --port N to pick another port, or stop that server yourself.",
          flush=True)
    sys.exit(1)

def _is_our_server(pid: int) -> bool:
    """Is this PID one of our own preview servers?

    Matches the module being run, never the substring "framework" anywhere in
    the command line: an interpreter path is enough to contain that, and the
    cost of a false positive is terminating an unrelated process.
    """
    lowered = _process_command(pid).lower()
    return "python" in lowered and any(
        tok in lowered for tok in ("-m trellum", "trellum.run", "trellum/run"))

#: First choice for the preview server. 8050 is also Plotly Dash's default,
#: which is exactly why it is so often taken -- and Docker Desktop, a stray
#: dev server or a previous session will each claim it.
DEFAULT_PORT = 8050

#: How many consecutive ports to try before giving up.
PORT_SEARCH_SPAN = 20

def resolve_port(requested: int | None, served_dir: str) -> int:
    """The port to serve ``served_dir`` on.

    An explicitly requested port is honoured or the run fails -- if somebody
    asked for 8050 they want 8050, and quietly serving somewhere else would
    send them to a page that is not there.

    With no request, take the default when it is free. When it is busy, ask
    the holder what it serves: a previous server of THIS output directory is
    reclaimed so the URL stays stable across runs, and anything else -- a
    foreign process or a framework server serving different output -- is left
    alone and the next free port is taken. Two servers for two directories are
    two servers, not a conflict, and resolving it by killing one is how a
    benchmark session once took down the demo a person was browsing.
    """
    if requested is not None:
        _claim_port(requested, served_dir)   # honour it, or exit with the reason
        return requested

    # One pass over the span: reclaim a previous server of this same output
    # wherever it landed, else take the first free port. The reclaim must not
    # stop at the default port -- a run that was once bumped to 8052 left its
    # server there, and a rerun that ignores it both loses the stable URL and
    # leaves a stale duplicate serving yesterday's build.
    first_free = None
    ours = _norm_dir(served_dir)
    for candidate in range(DEFAULT_PORT, DEFAULT_PORT + PORT_SEARCH_SPAN):
        if not _is_port_in_use(candidate):
            if first_free is None:
                first_free = candidate
            continue
        if _served_dir_at(candidate) == ours:
            _claim_port(candidate, served_dir)
            if candidate != DEFAULT_PORT:
                print(f"Reclaiming port {candidate} from the previous server "
                      f"of this output.", flush=True)
            return candidate

    if first_free is not None:
        if first_free != DEFAULT_PORT:
            print(f"Port {DEFAULT_PORT} is in use by "
                  f"{_holder_description(DEFAULT_PORT)}; serving on "
                  f"{first_free} instead.", flush=True)
        return first_free

    print(f"Ports {DEFAULT_PORT}-{DEFAULT_PORT + PORT_SEARCH_SPAN - 1} are all "
          f"in use. Free one, or pass --port N.", flush=True)
    sys.exit(1)
