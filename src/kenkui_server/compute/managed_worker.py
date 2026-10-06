"""Keep a desktop render process tree tied to its API parent's control pipe.

This module intentionally uses only the standard library: cancellation must not
wait for model imports or for a renderer to release Python's GIL. The API keeps
stdin open. EOF (including API termination) stops the worker and descendants.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from contextlib import suppress
from threading import Event, Thread


def _windows_job() -> int:
    """Join a private kill-on-close Job before spawning any descendants.

    The non-inheritable handle stays open until this supervisor exits. Windows
    closes it even on forced termination and kills the entire contained tree.
    See https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects
    """
    if sys.platform != "win32":
        raise RuntimeError("Windows Job objects require Windows")
    import ctypes
    from ctypes import wintypes

    class BasicLimits(ctypes.Structure):
        LimitFlags: int
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class IoCounters(ctypes.Structure):
        _fields_ = [
            (name, ctypes.c_ulonglong)
            for name in (
                "ReadOperationCount",
                "WriteOperationCount",
                "OtherOperationCount",
                "ReadTransferCount",
                "WriteTransferCount",
                "OtherTransferCount",
            )
        ]

    class ExtendedLimits(ctypes.Structure):
        BasicLimitInformation: BasicLimits
        _fields_ = [
            ("BasicLimitInformation", BasicLimits),
            ("IoInfo", IoCounters),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.GetCurrentProcess.argtypes = []
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel.SetInformationJobObject.restype = wintypes.BOOL
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateJobObjectW(None, None)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    limits = ExtendedLimits()
    limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not kernel.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
        error = ctypes.WinError(ctypes.get_last_error())
        kernel.CloseHandle(handle)
        raise error
    if not kernel.AssignProcessToJobObject(handle, kernel.GetCurrentProcess()):
        error = ctypes.WinError(ctypes.get_last_error())
        kernel.CloseHandle(handle)
        raise error
    return int(handle)


def _watch_parent(stopped: Event) -> None:
    try:
        while os.read(0, 4096):
            pass
    except OSError:
        pass
    finally:
        stopped.set()


def supervise(command: list[str]) -> int:
    if os.name == "nt":
        # No CloseHandle here: closing it would also terminate this supervisor.
        # The OS closes this non-inheritable handle when our process exits.
        _windows_job()
    stopped = Event()
    Thread(target=_watch_parent, args=(stopped,), daemon=True).start()
    with subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        start_new_session=os.name == "posix",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    ) as child:
        try:
            while child.poll() is None and not stopped.wait(0.05):
                pass
            return child.returncode if child.returncode is not None else 0
        finally:
            if os.name == "posix":
                with suppress(ProcessLookupError):
                    os.killpg(child.pid, signal.SIGTERM)
                try:
                    child.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    pass
                # Also clean up descendants after the worker itself has exited.
                with suppress(ProcessLookupError):
                    os.killpg(child.pid, signal.SIGKILL)
            elif child.poll() is None:
                child.kill()
            child.wait()
            # Windows reaps the worker here; job closure on supervisor exit
            # terminates any remaining encoder/synthesis descendants.


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("Provide a worker command")
    raise SystemExit(supervise(sys.argv[1:]))
