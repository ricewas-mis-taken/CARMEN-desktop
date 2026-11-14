"""Writes where inside Qt (or anything else) a memory-access crash happened, before the process dies.

The interpreter's own crash report (private/crash.log) only shows Python frames, and a crash inside Qt's event
loop has none. Windows keeps just the module and an offset. This handler runs at the moment of an access
violation and records the whole native stack as module+offset, one line per frame, in private/native_crash.log.
It only looks, then lets the crash carry on, so it changes nothing about how the program fails."""
import ctypes
import os
import sys
import time
from ctypes import wintypes

MAX_FRAMES = 40
MAX_REPORTS = 6              # a library that probes memory on purpose must not fill the log
EXCEPTION_ACCESS_VIOLATION = 0xC0000005
_keep = []
_reports = {"n": 0}


class _ExceptionRecord(ctypes.Structure):
    pass


_ExceptionRecord._fields_ = [
    ("ExceptionCode", wintypes.DWORD), ("ExceptionFlags", wintypes.DWORD),
    ("ExceptionRecord", ctypes.POINTER(_ExceptionRecord)), ("ExceptionAddress", ctypes.c_void_p),
    ("NumberParameters", wintypes.DWORD), ("ExceptionInformation", ctypes.c_size_t * 15),
]


class _ExceptionPointers(ctypes.Structure):
    _fields_ = [("ExceptionRecord", ctypes.POINTER(_ExceptionRecord)), ("ContextRecord", ctypes.c_void_p)]


def describe(address, kernel32=None):
    """'Qt6Gui.dll+0xe3880' for a code address, or the bare address when no module owns it."""
    kernel32 = kernel32 or ctypes.windll.kernel32
    module = wintypes.HMODULE()
    flags = 0x4 | 0x2            # FROM_ADDRESS | UNCHANGED_REFCOUNT
    if not kernel32.GetModuleHandleExW(flags, ctypes.c_void_p(address), ctypes.byref(module)):
        return hex(address or 0)
    buffer = ctypes.create_unicode_buffer(520)
    kernel32.GetModuleFileNameW(module, buffer, 520)
    return "%s+%s" % (os.path.basename(buffer.value), hex(address - (module.value or 0)))


def install(path):
    """Turns the logger on (Windows only). Returns True when it is installed."""
    if sys.platform != "win32" or _keep:
        return False
    kernel32, ntdll = ctypes.windll.kernel32, ctypes.windll.ntdll
    kernel32.GetModuleHandleExW.argtypes = [wintypes.DWORD, ctypes.c_void_p, ctypes.POINTER(wintypes.HMODULE)]
    kernel32.GetModuleFileNameW.argtypes = [wintypes.HMODULE, wintypes.LPWSTR, wintypes.DWORD]
    ntdll.RtlCaptureStackBackTrace.argtypes = [wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p),
                                               ctypes.c_void_p]
    ntdll.RtlCaptureStackBackTrace.restype = wintypes.USHORT
    handler_type = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.POINTER(_ExceptionPointers))
    fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT)

    def handler(info):
        try:
            record = info.contents.ExceptionRecord.contents
            if record.ExceptionCode == EXCEPTION_ACCESS_VIOLATION and _reports["n"] < MAX_REPORTS:
                _reports["n"] += 1
                frames = (ctypes.c_void_p * MAX_FRAMES)()
                count = ntdll.RtlCaptureStackBackTrace(0, MAX_FRAMES, frames, None)
                kind = {0: "reading", 1: "writing", 8: "executing"}.get(record.ExceptionInformation[0], "touching")
                lines = ["=== access violation %s %s at %s ===" % (
                    time.strftime("%Y-%m-%d %H:%M:%S"), kind, hex(record.ExceptionInformation[1]))]
                for i in range(count):
                    lines.append("  #%d %s" % (i, describe(frames[i], kernel32)))
                os.write(fd, ("\n".join(lines) + "\n").encode("utf-8", "replace"))
        except BaseException:
            pass
        return 0                                  # EXCEPTION_CONTINUE_SEARCH: the crash goes on as before

    callback = handler_type(handler)
    ntdll.RtlAddVectoredExceptionHandler.argtypes = [ctypes.c_ulong, handler_type]
    ntdll.RtlAddVectoredExceptionHandler.restype = ctypes.c_void_p
    registered = ntdll.RtlAddVectoredExceptionHandler(1, callback)
    _keep.extend([callback, fd, registered])
    return bool(registered)
