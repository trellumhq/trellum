"""One coordinator-supervised, killable operator measurement process."""
import json
import logging
import os
from pathlib import Path
import selectors
import signal
import socket
import subprocess
import sys
import threading
import time
from uuid import uuid4

from django.db import close_old_connections

from apps.core import operator_observations as observations

logger = logging.getLogger(__name__)
OUTPUT_BYTES = 128 * 1024


def process_scope():
    return socket.gethostname() + ":" + os.readlink("/proc/self/ns/pid")


def process_birth(pid):
    try:
        # The comm field may contain spaces/parentheses; starttime is field22.
        data = Path(f"/proc/{pid}/stat").read_text()
        return data[data.rfind(")") + 2:].split()[19]
    except FileNotFoundError:
        return None


def termination_known(proof):
    try:
        if not proof or proof.get("scope") != process_scope():
            return False
        birth = process_birth(proof["pid"])
        if birth is not None and birth == proof["birth"]:
            return False
        # A surviving grandchild still occupies this group even if its leader
        # exited. Never recycle an unverified group after a parent restart.
        try:
            os.killpg(proof["pid"], 0)
        except ProcessLookupError:
            return True
        return False
    except (OSError, KeyError, ValueError):
        return False


def kill_and_reap(process):
    if process.poll() is not None:
        # Never signal a recycled PID/group after the leader was reaped.
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            return True
        return False
    for sig, timeout in ((signal.SIGTERM, 1), (signal.SIGKILL, 1)):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            continue
        # Reap first, then ensure the process group has disappeared too.
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            return True
        return False
    return False


class ProbeSupervisor:
    def __init__(self, node):
        self.node, self.epoch = node, str(uuid4())
        self.stopping = threading.Event()
        self.lock = threading.Lock()
        self.process = None

    def tick(self):
        if self.stopping.is_set() or not self.lock.acquire(blocking=False):
            return
        close_old_connections()
        entry = None
        try:
            if sys.platform != "linux":
                return
            if not observations.has_work():
                return  # measurements happen only on request
            observations.recover(self.node, termination_known)
            entry = observations.claim(self.node, self.epoch)
            if entry:
                self.run(entry)
        except Exception:
            # Never include subprocess/environment/provider exception text.
            logger.error("Operator measurement supervisor unavailable")
            if entry:
                try:
                    observations.finish(entry, terminated=self.process is None)
                except Exception:
                    pass
        finally:
            close_old_connections()
            self.lock.release()

    def run(self, entry):
        started = time.monotonic()
        process = subprocess.Popen(
            [sys.executable, "-m", "apps.core.operator_probe_bootstrap", entry["kind"], entry["id"], str(os.getpid())],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
        )
        self.process = process
        stdout = bytearray()
        size = 0
        state, message = "unconfirmed", "Measurement outcome is unconfirmed."
        result = None
        try:
            birth = process_birth(process.pid)
            if not birth:
                raise RuntimeError("No child identity")
            proof = {"scope": process_scope(), "pid": process.pid, "birth": birth}
            observations.child_started(entry, proof)
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ, "stdout")
                selector.register(process.stderr, selectors.EVENT_READ, "stderr")
                while selector.get_map():
                    if self.stopping.is_set() or time.monotonic() - started >= observations.KINDS[entry["kind"]][0]:
                        break
                    for key, unused in selector.select(timeout=.1):
                        chunk = os.read(key.fileobj.fileno(), 8192)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        size += len(chunk)
                        if size > OUTPUT_BYTES:
                            raise ValueError("Output limit")
                        if key.data == "stdout":
                            stdout.extend(chunk)
                else:
                    process.wait(timeout=1)
                    if process.returncode == 0:
                        candidate = json.loads(stdout)
                        if isinstance(candidate, dict) and candidate.get("version") == 1 and candidate.get("kind") == entry["kind"]:
                            result = candidate
                    if result is None:
                        state, message = "failed", "Measurement failed or returned an invalid result."
        except (ValueError, UnicodeError):
            state, message = "failed", "Measurement output was invalid or exceeded its limit."
        finally:
            terminated = kill_and_reap(process)
            process.stdout.close()
            process.stderr.close()
            # Retain a blocked process reference if termination could not be
            # proved; scheduler admission remains unavailable.
            self.process = None if terminated else process
        observations.finish(entry, result=result, state=state, message=message, terminated=terminated)

    def stop(self):
        self.stopping.set()
        # The owning tick observes the flag within its .1s select window and
        # alone kills/reaps; competing wait/kill calls could race PID reuse.
        # Wait only a bounded grace; any unreaped work remains unconfirmed.
        if self.lock.acquire(timeout=3):
            self.lock.release()
