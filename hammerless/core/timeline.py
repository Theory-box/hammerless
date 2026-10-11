"""When each step of a build ran: a chart for the build log, so it's clear what Build & Play waits on.

Steps are recorded as (name, start, end) in seconds since the build started (time.time() based, from any thread).
The chart shows each on its own row along a shared time axis, and names the steps that end last in a chain
(the critical path: shortening any other step doesn't make the build finish sooner).
"""
from __future__ import annotations

import threading
import time

WIDTH = 60          # characters for the time axis


class Timeline:
    def __init__(self, start: float | None = None):
        self.start = time.time() if start is None else start
        self.steps: list[tuple[str, float, float]] = []
        self._lock = threading.Lock()

    def add(self, name: str, t0: float, t1: float) -> None:
        """A step that ran from t0 to t1 (time.time() values)."""
        with self._lock:
            self.steps.append((name, max(0.0, t0 - self.start), max(0.0, t1 - self.start)))

    def total(self) -> float:
        return max((e for _n, _s, e in self.steps), default=0.0)

    def critical_path(self) -> list[str]:
        """From the step that ends last, back through the step that ended just before each one started (what it
        waited on), as far as the chain goes."""
        steps = sorted(self.steps, key=lambda s: s[2])
        if not steps:
            return []
        path = [steps[-1]]
        while True:
            name, s, e = path[-1]
            before = [x for x in steps if x[2] <= s + 0.05 and x not in path]
            if not before:
                break
            path.append(max(before, key=lambda x: x[2]))
        return [n for n, _s, _e in reversed(path)]

    def chart(self) -> list[str]:
        total = self.total()
        if total <= 0:
            return []
        scale = WIDTH / total
        w = max(len(n) for n, _s, _e in self.steps)
        out = [f"Build timeline ({total:.1f} s from pressing Build; each mark is {total / WIDTH:.2f} s):"]
        for name, s, e in sorted(self.steps, key=lambda x: (x[1], x[2])):
            a, b = int(s * scale), max(int(s * scale) + 1, int(e * scale))
            out.append(f"  {name:<{w}} {s:6.1f} - {e:6.1f}  " + " " * a + "#" * (b - a))
        path = self.critical_path()
        if path:
            out.append("  waited on, in order: " + " -> ".join(path))
        return out
