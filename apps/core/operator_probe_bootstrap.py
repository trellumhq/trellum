"""Stdlib-only, pre-Django watchdog for the three closed operator probes."""
import ctypes
import os
import signal
import sys
import threading

BUDGETS = {"full-health": 30, "upload-usage": 60, "maintenance": 10}


def main():
    if len(sys.argv) != 4 or sys.argv[1] not in BUDGETS or sys.platform != "linux":
        return 69
    kind, operation_id = sys.argv[1:3]
    try:
        parent = int(sys.argv[3])
    except ValueError:
        return 69
    if os.getpgrp() != os.getpid():
        return 69

    def terminate(*unused):
        os.killpg(os.getpid(), signal.SIGKILL)

    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
    finished = threading.Event()

    def watchdog():
        if not finished.wait(BUDGETS[kind]):
            terminate()

    threading.Thread(target=watchdog, name="operator-probe-deadline", daemon=True).start()
    # Install before Django or settings imports. Check both sides of registration
    # because the supervisor could have died before prctl was called.
    if os.getppid() != parent or ctypes.CDLL(None, use_errno=True).prctl(1, signal.SIGTERM, 0, 0, 0) != 0 or os.getppid() != parent:
        terminate()
    try:
        import django
        django.setup()
        from apps.core.workspace import initialize_workspace_identity
        initialize_workspace_identity()
        from django.core.management import call_command
        call_command("operator_probe", kind=kind, operation_id=operation_id)
    except Exception:
        # Provider/environment exceptions and secrets never cross the pipe.
        sys.stderr.write("Operator measurement failed.\n")
        return 1
    finally:
        finished.set()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
