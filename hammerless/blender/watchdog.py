"""Freeze recorder. Blender's main thread checks in once a second (a timer); if it goes quiet for
FREEZE_SECONDS, a watcher thread writes where every Python thread is to hammerless_freeze.log in
the system's temp folder, once per freeze. Nothing is shown and nothing else changes: it's there so
a freeze can be traced to the exact line instead of guessed at.
"""
import os
import sys
import tempfile
import threading
import time
import traceback

import bpy

FREEZE_SECONDS = 20.0
LOG_PATH = os.path.join(tempfile.gettempdir(), "hammerless_freeze.log")
MAX_LOG_BYTES = 1_000_000

_state = {"beat": time.monotonic(), "stop": None, "thread": None, "main": threading.main_thread().ident}


def _beat():
    _state["beat"] = time.monotonic()
    return 1.0


def _dump(quiet: float) -> None:
    names = {t.ident: t.name for t in threading.enumerate()}
    lines = [f"=== {time.strftime('%Y-%m-%d %H:%M:%S')}: Blender's main thread hasn't responded for "
             f"{quiet:.0f} s (Hammerless {_version()})"]
    for ident, frame in sys._current_frames().items():
        if ident == threading.get_ident():
            continue                                    # (this watcher)
        lines.append(f"--- {'MAIN THREAD' if ident == _state['main'] else names.get(ident, 'thread')} ({ident})")
        lines += [line.rstrip("\n") for line in traceback.format_stack(frame)]
    try:
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > MAX_LOG_BYTES:
            os.remove(LOG_PATH)                         # keep it small: only recent freezes matter
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n\n")
    except OSError:
        pass


def _version() -> str:
    try:
        import tomllib
        with open(os.path.join(os.path.dirname(os.path.dirname(__file__)), "blender_manifest.toml"), "rb") as f:
            return tomllib.load(f).get("version", "?")
    except Exception:
        return "?"


def _watch(stop: threading.Event) -> None:
    reported = False
    while not stop.wait(2.0):
        quiet = time.monotonic() - _state["beat"]
        if quiet > FREEZE_SECONDS:
            if not reported:
                reported = True
                _dump(quiet)
        else:
            reported = False


def register():
    _state["beat"] = time.monotonic()
    bpy.app.timers.register(_beat, first_interval=1.0, persistent=True)
    stop = threading.Event()
    _state["stop"] = stop
    _state["thread"] = threading.Thread(target=_watch, args=(stop,), name="hammerless-freeze-recorder", daemon=True)
    _state["thread"].start()


def unregister():
    if _state["stop"] is not None:
        _state["stop"].set()
    if bpy.app.timers.is_registered(_beat):
        bpy.app.timers.unregister(_beat)
