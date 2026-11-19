"""Filter a build's OS output streams before its host persists the logs.

The portal starts ``trellum.run`` in both executor modes. When the host has
registered scalar secrets, that entry point supervises the actual build through
two pipes. Native code, bootstrap code and descendants therefore use the same
filter as Python output. No unfiltered spool file is created.
"""
from __future__ import annotations

import os
import re
import signal
import subprocess
import sys
import threading
import time

from trellum.redaction import environment_secrets, secret_variants
from trellum.secret_prefix import SecretPrefixes

_CHUNK_BYTES = 65536
_DRAIN_SECONDS = 2


class SecretFilter:
    """Incremental byte filter retaining only a possible secret prefix."""

    def __init__(self, secrets):
        self.secrets = tuple(value.encode('utf-8') for value in secret_variants(secrets))
        self.pattern = re.compile(b'|'.join(re.escape(value) for value in self.secrets)) if self.secrets else None
        self.replacement = b'[REDACTED]'
        if any(secret in self.replacement for secret in self.secrets):
            self.replacement = b''
        self.pending = b''
        self.prefixes = SecretPrefixes(self.secrets)

    def feed(self, chunk: bytes, *, final=False) -> bytes:
        if self.pattern is None:
            return chunk
        data = self.pending + chunk
        keep = 0 if final else self.prefixes.retained_length(data)
        boundary = len(data) - keep
        if keep:
            # Do not split a complete match when a longer secret also has a
            # prefix at the end of this chunk. Hold that match until resolved.
            for match in self.pattern.finditer(data):
                if match.start() < boundary < match.end():
                    boundary = match.start()
                    break
        self.pending = data[boundary:]
        return self.pattern.sub(self.replacement, data[:boundary])


def local_secrets(report_dir=None):
    """Read local declarations and dotenv before importing any project code."""
    from pathlib import Path

    import yaml

    from trellum.project import get_project_root
    from trellum.redaction import _secrets

    root = Path(get_project_root())
    paths = ([Path(report_dir) / 'report.yaml'] if report_dir else
             sorted((root / 'reports').glob('*/report.yaml')))
    values = set()
    for path in paths:
        if path.is_file():
            config = yaml.safe_load(path.read_text(encoding='utf-8')) or {}
            values.update(_secrets(config))
    return values


def run_filtered(command=None, *, secrets=None) -> int:
    """Run in the caller's process group/Windows job and retain its exit status."""
    try:
        secrets = environment_secrets() if secrets is None else secrets
    except ValueError:
        print('Invalid scalar secret registration; build was not started.', file=sys.stderr)
        return 1
    command = command or [sys.executable, '-c',
        "import sys; sys.argv[0]='trellum.run'; from trellum.runner import main; main(_capture=False)",
        *sys.argv[1:]]
    failed = threading.Event()

    def drain(source, destination):
        scrubber = SecretFilter(secrets)
        try:
            with source:
                while chunk := source.read(_CHUNK_BYTES):
                    destination.write(scrubber.feed(chunk))
                    destination.flush()
                destination.write(scrubber.feed(b'', final=True))
                destination.flush()
        except Exception:
            # Never echo an exception containing captured bytes. Closing the
            # read pipe also stops a writer rather than leaving it blocked.
            failed.set()

    try:
        child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
    except OSError:
        print('Unable to start the report build.', file=sys.stderr)
        return 1
    handlers = {}

    def forward(signum, _frame):
        try:
            child.send_signal(signum)
        except OSError:
            pass

    # Docker's init forwards signals to this supervisor. Host executors also
    # signal the existing process group/job; descendants remain in that scope.
    for sig in (signal.SIGTERM, signal.SIGINT):
        handlers[sig] = signal.signal(sig, forward)
    readers = [threading.Thread(target=drain, args=pair, daemon=True) for pair in (
        (child.stdout, sys.stdout.buffer), (child.stderr, sys.stderr.buffer))]
    try:
        for reader in readers:
            reader.start()
        code = child.wait()
        deadline = time.monotonic() + _DRAIN_SECONDS
        for reader in readers:
            reader.join(max(0, deadline - time.monotonic()))
        if failed.is_set() or any(reader.is_alive() for reader in readers):
            # An inherited writer must not keep the supervisor alive forever.
            # The host still owns/reaps the process group or container.
            return 1
        return code if code >= 0 else 128 - code
    finally:
        for sig, handler in handlers.items():
            signal.signal(sig, handler)


def host_registered_secrets() -> bool:
    return bool(os.environ.get('TRELLUM_SCALAR_SECRET_NAMES') or os.environ.get('TRELLUM_SCALAR_SECRETS'))
