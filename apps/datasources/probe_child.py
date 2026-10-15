"""Child half of the bounded connection probe (see ``probe.py``).

Runs in its own interpreter with no Django and no portal ORM. Reads one JSON
request from stdin ({"type": ..., "info": {...}}), performs the connection test
and writes one JSON line ({"ok": bool, "detail": str}) to the *original*
stdout. Driver chatter on stdout is diverted to stderr so it can never be
mistaken for (or inflate) the result.
"""
import json
import os
import socket
import sys
from pathlib import Path

RESULT_DETAIL_MAX = 2000


def _probe(kind: str, info: dict) -> tuple[bool, str]:
    if kind == "google_sheets":
        from trellum.data.query import read_source

        df = read_source(
            "google_sheets", info["path"],
            credentials_path=info.get("credentials_path"),
            credentials_json=info.get("credentials_json"),
        )
        return True, f"Opened the spreadsheet ({len(df):,} rows in the first sheet)."
    from trellum.data.drivers import connect

    conn = connect(kind, info)
    try:
        cursor = getattr(conn, "cursor", None)
        if callable(cursor):
            cur = conn.cursor()
            cur.execute("SELECT 1")
            cur.fetchone()
            cur.close()
    finally:
        close = getattr(conn, "close", None)
        if callable(close):
            conn.close()
    return True, "Connected successfully."


def main() -> int:
    # The repository root (where ``trellum`` lives) is not on the path under -I.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    out = os.fdopen(os.dup(1), "w")
    os.dup2(2, 1)  # anything a driver prints goes to stderr
    try:
        request = json.loads(sys.stdin.read())
        timeout = request.get("socket_timeout")
        if timeout:
            socket.setdefaulttimeout(float(timeout))  # first line of defence only
        ok, detail = _probe(request["type"], request["info"])
    except BaseException as exc:  # noqa: BLE001 - report everything to the parent
        ok, detail = False, f"{type(exc).__name__}: {exc}"
    out.write(json.dumps({"ok": bool(ok), "detail": str(detail)[:RESULT_DETAIL_MAX]}) + "\n")
    out.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
