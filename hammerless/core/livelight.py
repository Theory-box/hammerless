"""A live lighting session: hlvrad kept running on a compiled map (hlvrad -live, native/hlvrad/live_session.h).

The session lights the map as a bake does, then takes light edits (entity keys) and brings the lightmaps up to date
in steps: the changed lights' direct light at once, bounce passes after, and the exact direct light when no edit
waits. Each step's lightmaps are in shared memory; snapshot() copies them out. No Blender here: the viewport side
feeds this from its lights and shows what snapshot() returns.

    live = LiveLight(hlvrad_exe, ["-hdr", "-gpu", "-gi", "100", "-girays", "1024", "-game", game, bsp])
    live.set(3, "origin", "0 0 128")
    serial = live.go()
    live.wait(serial)                   # the first result with that edit in it
    faces, light, info = live.snapshot()
    live.close()
"""
import mmap
import os
import queue
import struct
import subprocess
import threading
import time

MAGIC = 0x564C4C48      # "HLLV"
HEADER = 64
# a result's state
ROUGH, EXACT, SETTLED, BAKED = 0, 1, 2, 3


class LiveError(RuntimeError):
    pass


class LiveLight:
    def __init__(self, exe, args, low_priority=True, on_log=None, queue_events=True):
        """args: hlvrad's options and the map, as for a bake with -gpu -gi (the session adds -live <name>).
        queue_events False: results aren't kept for wait() (an owner that polls last_update() instead)."""
        self.queue_events = queue_events
        self._next_entity = None
        self.name = "hammerless_live_%d_%d" % (os.getpid(), int(time.time() * 1000) % 100000000)
        flags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
        if low_priority and hasattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS"):
            flags |= subprocess.BELOW_NORMAL_PRIORITY_CLASS
        self.proc = subprocess.Popen([exe, "-live", self.name] + list(args), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.STDOUT, creationflags=flags)
        self.on_log = on_log
        self.log = []                   # (hlvrad's other lines: the last few hundred)
        self.updates = []               # (gen, serial, state, pass, seconds since the command, time.time())
        self.ready = None               # (capacity, faces, entities) once the session has its first lightmaps
        self.serial = 0
        self._events = queue.Queue()
        self._new_entities = queue.Queue()
        self._map = None
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._read, daemon=True)
        self._thread.start()

    # ------------------------------------------------------------------ the session's lines
    def _read(self):
        for raw in self.proc.stdout:
            line = raw.decode("utf-8", "replace").rstrip()
            if line.startswith("HLLIVE_UPDATE "):
                p = line.split()
                u = (int(p[1]), int(p[2]), int(p[3]), int(p[4]), float(p[5]), time.time())
                with self._lock:
                    self.updates.append(u)
                    del self.updates[:-200]
                if self.queue_events:
                    self._events.put(u)
            elif line.startswith("HLLIVE_READY "):
                p = line.split()
                self.ready = (int(p[2]), int(p[3]), int(p[4]))
            elif line.startswith("HLLIVE_ENT "):
                pass                    # (add() knows the index: the map's entities, then one each)
            else:
                self.log.append(line)
                del self.log[:-400]
                if self.on_log:
                    self.on_log(line)
        self._events.put(None)          # (ended)

    def alive(self):
        return self.proc.poll() is None

    def _send(self, line):
        if not self.alive():
            raise LiveError("the lighting session has ended:\n" + "\n".join(self.log[-12:]))
        self.proc.stdin.write((line + "\n").encode("utf-8"))
        self.proc.stdin.flush()

    # ------------------------------------------------------------------ edits
    def set(self, entity, key, value):
        self._send("SET %d %s %s" % (entity, key, value))

    def add(self, keys):
        """A new light entity with these keys (classname, origin, _light...): its index."""
        while self.ready is None:       # (the map's entity count comes with its first lightmaps)
            if not self.alive():
                raise LiveError("the lighting session has ended:\n" + "\n".join(self.log[-12:]))
            time.sleep(0.01)
        if self._next_entity is None:
            self._next_entity = self.ready[2]
        self._send("ADD")
        entity = self._next_entity
        self._next_entity += 1
        for k, v in keys.items():
            self.set(entity, k, v)
        return entity

    def delete(self, entity):
        self._send("DEL %d" % entity)

    def rays(self, n):
        self._send("RAYS %d" % n)

    def go(self):
        """Light the edits sent so far: the serial its results carry."""
        self.serial += 1
        self._send("GO %d" % self.serial)
        return self.serial

    def cold(self):
        """The edits so far lit as a bake from nothing (one result, state BAKED): its serial."""
        self.serial += 1
        self._send("COLD %d" % self.serial)
        return self.serial

    # ------------------------------------------------------------------ results
    def wait(self, serial=None, state=None, timeout=120.0):
        """The first result (new since the last wait) of this serial or later, at this state or further: its tuple
        (gen, serial, state, pass, seconds, time)."""
        end = time.time() + timeout
        while True:
            left = end - time.time()
            if left <= 0:
                raise LiveError("the lighting session gave no such result in %.0f s" % timeout)
            try:
                u = self._events.get(timeout=left)
            except queue.Empty:
                continue
            if u is None:
                raise LiveError("the lighting session has ended:\n" + "\n".join(self.log[-12:]))
            if (serial is None or u[1] >= serial) and (state is None or u[2] >= state):
                return u

    def last_update(self):
        """The newest result's tuple, or None."""
        with self._lock:
            return self.updates[-1] if self.updates else None

    def snapshot(self):
        """(faces bytes, lightmap bytes, info) as they are now: the faces are the map's dface_t records (56 bytes
        each, with their lightofs and styles), the lightmap bytes the lighting lump's."""
        if self._map is None:
            while self.ready is None:
                if not self.alive():
                    raise LiveError("the lighting session has ended:\n" + "\n".join(self.log[-12:]))
                time.sleep(0.01)
            self._map = mmap.mmap(-1, self.ready[0], tagname=self.name, access=mmap.ACCESS_READ)
        m = self._map
        for _ in range(2000):
            h = struct.unpack_from("<16I", m, 0)
            if h[0] != MAGIC or h[2] & 1:
                time.sleep(0.001)
                continue
            nfaces, facebytes, lightbytes = h[3], h[4], h[5]
            faces = m[HEADER:HEADER + nfaces * facebytes]
            light = m[HEADER + nfaces * facebytes:HEADER + nfaces * facebytes + lightbytes]
            if struct.unpack_from("<I", m, 8)[0] == h[2]:       # (not rewritten meanwhile)
                return faces, light, {"gen": h[2], "faces": nfaces, "face_bytes": facebytes, "state": h[6], "serial": h[7],
                                      "pass": h[8], "rays": h[9]}
        raise LiveError("the lighting session's lightmaps never held still")

    def close(self):
        try:
            if self.alive():
                self._send("QUIT")
                self.proc.stdin.close()
                self.proc.wait(timeout=10)
        except Exception:
            pass
        if self.alive():
            self.proc.kill()
        if self._map is not None:
            self._map.close()
            self._map = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
