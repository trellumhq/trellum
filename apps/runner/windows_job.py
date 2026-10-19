"""Contain a direct Windows report build in a kill-on-close Job Object.

The child waits on a pipe before importing report code. This closes the race
between CreateProcess and AssignProcessToJobObject: no report code can create
descendants before assignment succeeds.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import signal
import subprocess
import sys


_GATED_RUN = (
    "import runpy,sys; "
    "ready=sys.stdin.buffer.read(1); "
    "sys.exit(1) if ready!=b'1' else None; "
    "sys.argv=['trellum.run',*sys.argv[1:]]; "
    "runpy.run_module('trellum.run',run_name='__main__')"
)
_KILL_ON_CLOSE = 0x2000
_EXTENDED_LIMIT_INFO = 9
_BASIC_ACCOUNTING_INFO = 1


class _BasicLimit(ctypes.Structure):
    _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD)]


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class _ExtendedLimit(ctypes.Structure):
    _fields_ = [("BasicLimitInformation", _BasicLimit),
                ("IoInfo", _IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t)]


class _Accounting(ctypes.Structure):
    _fields_ = [("TotalUserTime", ctypes.c_int64),
                ("TotalKernelTime", ctypes.c_int64),
                ("ThisPeriodTotalUserTime", ctypes.c_int64),
                ("ThisPeriodTotalKernelTime", ctypes.c_int64),
                ("TotalPageFaultCount", wintypes.DWORD),
                ("TotalProcesses", wintypes.DWORD),
                ("ActiveProcesses", wintypes.DWORD),
                ("TotalTerminatedProcesses", wintypes.DWORD)]


def _api():
    dll = ctypes.WinDLL("kernel32", use_last_error=True)
    dll.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    dll.CreateJobObjectW.restype = wintypes.HANDLE
    dll.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                            ctypes.c_void_p, wintypes.DWORD]
    dll.SetInformationJobObject.restype = wintypes.BOOL
    dll.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    dll.AssignProcessToJobObject.restype = wintypes.BOOL
    dll.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                              ctypes.c_void_p, wintypes.DWORD,
                                              ctypes.c_void_p]
    dll.QueryInformationJobObject.restype = wintypes.BOOL
    dll.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    dll.TerminateJobObject.restype = wintypes.BOOL
    dll.CloseHandle.argtypes = [wintypes.HANDLE]
    dll.CloseHandle.restype = wintypes.BOOL
    return dll


def _check(ok):
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())


class WindowsJobProcess:
    """Popen facade whose termination check covers the assigned process tree."""

    def __init__(self, command, **kwargs):
        api = _api()
        handle = api.CreateJobObjectW(None, None)
        _check(handle)
        self._api = api
        self._job = handle
        self._proc = None
        try:
            limits = _ExtendedLimit()
            limits.BasicLimitInformation.LimitFlags = _KILL_ON_CLOSE
            _check(api.SetInformationJobObject(handle, _EXTENDED_LIMIT_INFO,
                                               ctypes.byref(limits), ctypes.sizeof(limits)))
            self._proc = subprocess.Popen(
                [sys.executable, "-c", _GATED_RUN, *command[3:]],
                stdin=subprocess.PIPE,
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                **kwargs,
            )
            _check(api.AssignProcessToJobObject(handle, self._proc._handle))
            self._proc.stdin.write(b"1")
            self._proc.stdin.close()
        except BaseException:
            try:
                if self._proc is not None:
                    self._proc.kill()
                    self._proc.wait(timeout=5)
            finally:
                api.CloseHandle(handle)
                self._job = None
            raise

    @property
    def pid(self):
        return self._proc.pid

    def poll(self):
        return self._proc.poll()

    @property
    def termination_confirmed(self):
        info = _Accounting()
        _check(self._api.QueryInformationJobObject(
            self._job, _BASIC_ACCOUNTING_INFO, ctypes.byref(info), ctypes.sizeof(info), None,
        ))
        return info.ActiveProcesses == 0

    def send_signal(self, _sig):
        try:
            self._proc.send_signal(signal.CTRL_BREAK_EVENT)
        except OSError:
            self.kill()

    def kill(self):
        _check(self._api.TerminateJobObject(self._job, 1))

    def cleanup(self):
        if self._job is not None:
            self._api.CloseHandle(self._job)
            self._job = None
