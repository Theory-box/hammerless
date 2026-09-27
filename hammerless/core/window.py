"""Game window placement (Windows only): list monitors, move the L4D2 window.

Used so the game can open on a side monitor while you keep working in Blender.
"""
from __future__ import annotations

import sys
import threading
import time
from dataclasses import dataclass


@dataclass
class Monitor:
    index: int
    x: int
    y: int
    width: int
    height: int
    primary: bool

    @property
    def label(self) -> str:
        side = "primary" if self.primary else ("left" if self.x < 0 else "right" if self.x > 0 else "")
        return f"{self.index + 1}: {self.width}x{self.height} {side}".strip()


def monitors() -> list[Monitor]:
    if sys.platform != "win32":
        return []
    import ctypes
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    try:
        user32.SetProcessDPIAware()
    except Exception:
        pass
    found: list[Monitor] = []

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

    proc_type = ctypes.WINFUNCTYPE(ctypes.c_int, wintypes.HMONITOR, wintypes.HDC,
                                   ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
    # 64-bit handles: without argtypes ctypes passes them as 32-bit ints and overflows
    user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFO)]
    user32.EnumDisplayMonitors.argtypes = [wintypes.HDC, ctypes.POINTER(wintypes.RECT), proc_type, wintypes.LPARAM]

    def callback(hmon, hdc, rect, data):
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        user32.GetMonitorInfoW(hmon, ctypes.byref(info))
        r = info.rcMonitor
        found.append(Monitor(0, r.left, r.top, r.right - r.left, r.bottom - r.top, bool(info.dwFlags & 1)))
        return 1

    user32.EnumDisplayMonitors(None, None, proc_type(callback), 0)
    found.sort(key=lambda m: (m.x, m.y))  # left to right
    for i, m in enumerate(found):
        m.index = i
    return found


def _find_window(process_name: str = "left4dead2.exe"):
    import ctypes
    from ctypes import wintypes
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    result = []
    enum_type = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def callback(hwnd, lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        h = kernel32.OpenProcess(0x1000, False, pid.value)  # QUERY_LIMITED_INFORMATION
        if h:
            buf = ctypes.create_unicode_buffer(260)
            size = wintypes.DWORD(260)
            if kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                if buf.value.lower().endswith(process_name.lower()):
                    title = ctypes.create_unicode_buffer(256)
                    user32.GetWindowTextW(hwnd, title, 256)
                    if title.value:
                        result.append(hwnd)
            kernel32.CloseHandle(h)
        return True

    user32.EnumWindows(enum_type(callback), 0)
    return result[0] if result else None


def move_game_window(monitor: Monitor, timeout: float = 120.0) -> threading.Thread | None:
    """In the background, wait for the game window and centre it on `monitor`."""
    if sys.platform != "win32":
        return None

    def run():
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        deadline = time.time() + timeout
        placed = 0
        while time.time() < deadline and placed < 3:
            hwnd = _find_window()
            if hwnd:
                r = wintypes.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(r))
                w, h = r.right - r.left, r.bottom - r.top
                x = monitor.x + max(0, (monitor.width - w) // 2)
                y = monitor.y + max(0, (monitor.height - h) // 2)
                user32.SetWindowPos(hwnd, None, x, y, 0, 0, 0x0001 | 0x0004)  # NOSIZE | NOZORDER
                placed += 1  # the game re-positions itself once after loading; repeat a few times
            time.sleep(3.0)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t
