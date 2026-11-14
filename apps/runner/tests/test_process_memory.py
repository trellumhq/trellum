"""Report memory remains observable behind the scalar log supervisor."""
from __future__ import annotations

import ctypes
import io
import json
import os
import signal
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from apps.runner import executor, windows_job


def test_windows_job_samples_report_members_not_only_supervisor(monkeypatch):
    """Exercise the Windows API contract with a fake job on every platform."""
    def query(_job, kind, pointer, _size, _return_size):
        assert kind == windows_job._BASIC_PROCESS_ID_LIST
        info = ctypes.cast(pointer, ctypes.POINTER(windows_job._ProcessIds)).contents
        info.NumberOfAssignedProcesses = info.NumberOfProcessIdsInList = 3
        info.ProcessIdList[:3] = [101, 102, 103]
        return True

    job = object.__new__(windows_job.WindowsJobProcess)
    job._job = 1
    job._api = SimpleNamespace(QueryInformationJobObject=query)
    monkeypatch.setattr(executor, "read_peak_rss_mb", {101: 20, 102: 160, 103: None}.get)
    # These are historical per-process peaks, not concurrent allocations.
    assert job.peak_rss_mb() == 160


def test_windows_job_memory_query_failure_is_unavailable():
    job = object.__new__(windows_job.WindowsJobProcess)
    job._job = 1
    job._api = SimpleNamespace(QueryInformationJobObject=lambda *args: False)
    assert job.peak_rss_mb() is None


def test_missing_process_memory_is_unavailable():
    assert executor.read_process_tree_peak_rss_mb(0) is None


@pytest.mark.heavy
@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux procfs sampling")
def test_process_memory_sampling_bounds_procfs_work(monkeypatch):
    @contextmanager
    def tasks(_path):
        yield (SimpleNamespace(path=f"/proc/123/task/{i}") for i in range(10000))

    reads = []

    def children(path, **kwargs):
        reads.append(path)
        return io.StringIO("")

    monkeypatch.setattr(executor.os, "scandir", tasks)
    monkeypatch.setattr(executor, "open", children, raising=False)
    monkeypatch.setattr(executor, "read_peak_rss_mb", lambda _pid: 10)
    assert executor.read_process_tree_peak_rss_mb(123) == 10
    assert 0 < len(reads) <= 4096


@pytest.mark.heavy
@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux procfs/process groups")
def test_filtered_child_allocation_is_included_in_build_peak(tmp_path):
    ready = tmp_path / "ready"
    child = (
        "import pathlib,sys,time; "
        "allocation=bytearray(64*1024*1024); del allocation; "
        "pathlib.Path(sys.argv[1]).touch(); time.sleep(30)"
    )
    supervisor = (
        "from trellum.log_capture import run_filtered; import sys; "
        "sys.exit(run_filtered([sys.executable, '-c', sys.argv[1], sys.argv[2]]))"
    )
    repo = Path(__file__).resolve().parents[3]
    process = subprocess.Popen(
        [sys.executable, "-c", supervisor, child, str(ready)],
        cwd=repo,
        env={**os.environ, "TRELLUM_SCALAR_SECRETS": json.dumps(["synthetic-memory-secret"])},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and time.monotonic() < deadline and process.poll() is None:
            time.sleep(0.01)
        assert ready.exists()
        leader_peak = executor.read_peak_rss_mb(process.pid)
        build_peak = executor.read_process_tree_peak_rss_mb(process.pid)
        assert build_peak is not None and build_peak >= 64
        assert leader_peak is not None and build_peak > leader_peak
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.communicate(timeout=5)
