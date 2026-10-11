"""Processes Hammerless starts for a build (compilers, light baking, the nav worker) end when Blender does, even if
Blender is closed mid-build or crashes: they're put in a Windows job object that kills everything in it when its
last handle closes (Blender's, on exit). Not used for the game: it keeps running when Blender is closed.

adopt(proc) after starting one; a no-op off Windows or if Windows refuses (then the process just isn't tied).
"""
from __future__ import annotations

import os
import sys

_job = None


def _make_job():
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    k32.SetInformationJobObject.argtypes = (wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    k32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)

    class BASIC(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class IO(ctypes.Structure):
        _fields_ = [(n, ctypes.c_uint64) for n in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                                                   "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class EXTENDED(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IO), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]

    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    JobObjectExtendedLimitInformation = 9
    job = k32.CreateJobObjectW(None, None)
    if not job:
        return None
    info = EXTENDED()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not k32.SetInformationJobObject(job, JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info)):
        k32.CloseHandle(job)
        return None
    return k32, job          # (the handle is never closed: Windows closes it when Blender exits, ending the job)


def adopt(proc) -> bool:
    """Tie a started subprocess.Popen to Blender's lifetime. True if it worked."""
    global _job
    if sys.platform != "win32" or os.environ.get("HAMMERLESS_NO_CHILDKILL"):
        return False
    try:
        if _job is None:
            _job = _make_job() or False
        if not _job:
            return False
        k32, job = _job
        return bool(k32.AssignProcessToJobObject(job, int(proc._handle)))
    except Exception:            # (never stops a build: the process just isn't tied)
        return False
