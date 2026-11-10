"""Owned, asynchronous transport for the experimental native preview worker.

No Blender or GPU API is called from a worker thread. Callers create immutable
NumPy snapshots on the main thread. One global client owns one hidden process;
there is one running request and at most one replacement request. A worker error
is terminal until explicit shutdown/retry, avoiding crash/restart loops.
"""
from dataclasses import dataclass
from pathlib import Path
import atexit
import ctypes
from ctypes import wintypes
import os
import operator
import struct
import subprocess
import tempfile
import threading
import time

import numpy as np

MAGIC = 0x48504C31
HEADER = struct.Struct('<4I')
PARAMS = struct.Struct('<4I4f')
MAX_TRIANGLES = 8_000_000
MAX_POINTS = 8_000_000
MAX_LIGHTS = 65_536


class _OwnedJob:
    """The handle belongs only to this client, never a process-name search."""
    def __init__(self):
        self.handle = None
        if os.name != 'nt':
            return
        class Basic(ctypes.Structure):
            _fields_ = [('process_time', ctypes.c_longlong), ('job_time', ctypes.c_longlong),
                        ('flags', wintypes.DWORD), ('min_ws', ctypes.c_size_t),
                        ('max_ws', ctypes.c_size_t), ('active', wintypes.DWORD),
                        ('affinity', ctypes.c_size_t), ('priority', wintypes.DWORD),
                        ('scheduling', wintypes.DWORD)]
        class IO(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in
                        ('read_ops', 'write_ops', 'other_ops', 'read_bytes', 'write_bytes', 'other_bytes')]
        class Extended(ctypes.Structure):
            _fields_ = [('basic', Basic), ('io', IO), ('process_memory', ctypes.c_size_t),
                        ('job_memory', ctypes.c_size_t), ('peak_process', ctypes.c_size_t),
                        ('peak_job', ctypes.c_size_t)]
        self.api = ctypes.WinDLL('kernel32', use_last_error=True)
        self.api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.api.CreateJobObjectW.restype = wintypes.HANDLE
        self.api.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.handle = self.api.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        limits = Extended()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            error = ctypes.WinError(ctypes.get_last_error())
            self.close()
            raise error

    def assign(self, process):
        if self.handle and not self.api.AssignProcessToJobObject(self.handle, int(process._handle)):
            raise ctypes.WinError(ctypes.get_last_error())

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


def pack_lights(scene):
    """Same five vec4 rows as SceneCache.upload_lights, stored per light."""
    data = np.zeros((len(scene.lights), 20), dtype='<f4')
    for i, light in enumerate(scene.lights):
        for row, value in enumerate(light):
            data[i, row*4:row*4+len(value)] = value
    return data


def flatten_scene(scene, resolver):
    """World-space opaque triangles + original preview bounce metadata.

    Mirrors TraceCache's material/receiver policy, including models that block
    rays but do not return bounced radiance. Geometric normals use inverse-
    transpose, matching the software tracer even for mirrored instances.
    """
    local = {}
    vertices = []
    metadata = []
    total = 0
    for key, matrix in scene.instances:
        g = scene.geometry[key]
        if key not in local:
            positions = g.positions if g.shadow_positions is None else g.shadow_positions
            tris = np.asarray(positions, dtype=np.float32).reshape(-1, 3, 3)
            ids = np.arange(len(tris)) if g.shadow_triangle_ids is None else np.asarray(g.shadow_triangle_ids)
            mids = (np.asarray(g.material_ids) if g.material_ids is not None
                    else np.zeros(len(g.positions)//3, dtype=np.int32))
            reflectivity = np.zeros((len(tris), 4), dtype=np.float32)
            if not g.use_uv:
                for mi in np.unique(mids[ids]):
                    mat = g.materials[mi] if 0 <= mi < len(g.materials) else None
                    surface = resolver.resolve(mat)
                    selected = mids[ids] == mi
                    reflectivity[selected, :3] = surface.reflectivity
                    reflectivity[selected, 3] = float(surface.receives_light and surface.verified)
            normals = np.cross(tris[:, 1]-tris[:, 0], tris[:, 2]-tris[:, 0])
            local[key] = tris, reflectivity, normals
        tris, reflectivity, normals = local[key]
        if not len(tris):
            continue
        m = np.asarray(matrix, dtype=np.float64)
        if abs(np.linalg.det(m[:3, :3])) < 1e-15:
            continue
        total += len(tris)
        if total > MAX_TRIANGLES:
            raise ValueError('Native preview triangle limit exceeded')
        world = tris @ m[:3, :3].T + m[:3, 3]
        transformed = normals @ np.linalg.inv(m[:3, :3])
        lengths = np.linalg.norm(transformed, axis=1)
        transformed = np.divide(transformed, lengths[:, None],
                                out=np.zeros_like(transformed), where=lengths[:, None] > 1e-20)
        meta = np.zeros((len(tris), 8), dtype='<f4')
        meta[:, :4] = reflectivity
        meta[:, 4:7] = transformed
        vertices.append(np.asarray(world.reshape(-1, 9), dtype='<f4'))
        metadata.append(meta)
    if not vertices:
        return np.zeros((0, 9), dtype='<f4'), np.zeros((0, 8), dtype='<f4')
    return np.concatenate(vertices), np.concatenate(metadata)


def _array(data, columns, limit, name, copy=False):
    a = np.array(data, dtype='<f4', order='C', copy=True) if copy else np.asarray(data, dtype='<f4', order='C')
    if a.ndim != 2 or a.shape[1] != columns or len(a) > limit:
        raise ValueError(f'Invalid native preview {name} shape')
    if not np.isfinite(a).all():
        raise ValueError(f'Nonfinite native preview {name}')
    return a


@dataclass(frozen=True)
class Result:
    pixels: object = None
    error: str = ''
    elapsed_ms: float = 0.0
    scene_uploaded: bool = False
    lights_uploaded: bool = False


@dataclass(frozen=True)
class _Request:
    owner: object
    token: object
    epoch: int
    geometry_token: object
    vertices: object
    metadata: object
    lights_token: object
    lights: object
    points: object
    params: tuple


class NativeClient:
    def __init__(self, command=None, timeout=30.0, packet_points=4096):
        if not np.isfinite(timeout) or timeout <= 0:
            raise ValueError('Native preview deadline must be positive')
        self.command = list(command) if command is not None else [str(Path(__file__).parents[1]/'core'/'hlpreview.exe')]
        self.timeout = float(timeout)
        self.packet_points = operator.index(packet_points)
        if not 1 <= self.packet_points <= 4096:
            raise ValueError('Native preview packet size must be between 1 and 4096')
        self._condition = threading.Condition(threading.RLock())
        self._closed = False
        self._epoch = 0
        self._desired = None
        self._owner = None
        self._pending = None
        self._result = None
        self._fault = ''
        self._process = None
        self._job = None
        self._deadline = 0.0
        self._stderr = None
        self._geometry_token = None
        self._lights_token = None
        self._have_geometry = False
        self._have_lights = False
        self._validated_geometry = None
        self._wake = threading.Event()
        self._thread = threading.Thread(target=self._run, name='Hammerless native preview', daemon=True)
        self._watchdog = threading.Thread(target=self._watch, name='Hammerless native preview deadline', daemon=True)
        self._thread.start()
        self._watchdog.start()

    def submit(self, owner, token, geometry_token, vertices, metadata, lights_token, lights, points, params):
        """Queue latest snapshot. Geometry/light arrays must not be mutated afterward.

        The points array is copied because it is routinely replaced by viewport
        readbacks. Geometry snapshots can be large and are retained, not copied.
        Token changes must cover every corresponding data change.
        """
        with self._condition:
            if self._closed:
                return False
            if self._desired == (owner, token):
                return False
        # Large immutable geometry snapshots are validated once, not once per
        # progressive frame. Strong references prevent recycled-ID cache hits.
        validated = self._validated_geometry
        if (validated is None or validated[0] != geometry_token
                or validated[1] is not vertices or validated[2] is not metadata):
            vertices = _array(vertices, 9, MAX_TRIANGLES, 'vertices')
            metadata = _array(metadata, 8, MAX_TRIANGLES, 'metadata')
            if len(vertices) != len(metadata):
                raise ValueError('Native preview metadata length mismatch')
            self._validated_geometry = (geometry_token, vertices, metadata)
        lights = _array(lights, 20, MAX_LIGHTS, 'lights')
        points = _array(points, 8, MAX_POINTS, 'points', copy=True)
        if len(params) != 8:
            raise ValueError('Native preview parameters require eight values')
        rays, depth, seed, flags = (operator.index(v) for v in params[:4])
        scale, *sky = (float(v) for v in params[4:])
        if not (0 <= rays <= 128 and 1 <= depth <= 8 and 0 <= seed <= 1_000_000 and 0 <= flags <= 3):
            raise ValueError('Invalid native preview sampling parameters')
        if not np.isfinite([scale, *sky]).all() or not 0 < scale <= 1e6:
            raise ValueError('Invalid native preview light units')
        params = (rays, depth, seed, flags, scale, *sky)
        with self._condition:
            if self._closed:
                return False
            self._desired = (owner, token)
            self._owner = owner
            self._result = None
            if self._fault:
                self._result = (owner, token, Result(error=self._fault))
                return False
            self._pending = _Request(owner, token, self._epoch, geometry_token, vertices, metadata,
                                     lights_token, lights, points, params)
            self._condition.notify()
            return True

    def poll(self, owner, token):
        with self._condition:
            if self._result is not None and self._result[:2] == (owner, token):
                return self._result[2]
            if self._owner != owner:
                message = ('Hardware preview is active in another viewport' if self._owner is not None
                           else 'Hardware preview request is no longer active')
                return Result(error=message+'; change this viewport or toggle hardware preview to retry')
            return None

    def pause(self, owner):
        """Discard the owner's pending/result work, retaining the native scene.

        Active shading stops between bounded point packets. No synchronous wait,
        process restart, or Blender calls are involved.
        """
        with self._condition:
            if self._desired is not None and self._desired[0] == owner:
                self._desired = None
                self._pending = None
                self._result = None

    def release(self, owner):
        with self._condition:
            if self._owner != owner:
                return
            self._epoch += 1
            self._owner = None
            self._desired = None
            self._pending = None
            self._result = None
            self._stop_locked()

    def close(self):
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._epoch += 1
            self._pending = None
            self._desired = None
            self._result = None
            self._stop_locked()
            self._condition.notify_all()
            self._wake.set()
        # Both threads are daemons, and the process has already been killed/reaped.
        # Do not block the Blender UI on thread shutdown.

    def _stop_locked(self):
        process, job = self._process, self._job
        active = bool(self._deadline)
        stderr = self._stderr
        self._process = self._job = None
        self._deadline = 0.0
        self._have_geometry = self._have_lights = False
        self._validated_geometry = None
        if job is not None:
            job.close()
        if process is not None:
            if process.poll() is None:
                process.kill()
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                # An owned, killed process must not be replaced if it cannot reap.
                self._fault = 'Native preview worker did not exit after termination'
        # Active handles are closed by the IO thread after the blocking call
        # returns. Idle handles can close here without racing pipe operations.
        if process is not None and not active:
            self._close_streams(process, stderr)

    @staticmethod
    def _close_streams(process, stderr):
        for stream in (process.stdin, process.stdout, stderr):
            try:
                stream.close()
            except Exception:
                pass

    def _start_locked(self):
        job = _OwnedJob()
        process = None
        stderr = None
        try:
            stderr = tempfile.TemporaryFile(mode='w+b')
            startup = None
            if os.name == 'nt':
                startup = subprocess.STARTUPINFO()
                startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                startup.wShowWindow = 0
            process = subprocess.Popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=stderr, bufsize=0, startupinfo=startup,
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
            job.assign(process)
        except Exception:
            job.close()
            if process is not None:
                try:
                    process.kill()
                    process.wait(timeout=1.0)
                finally:
                    self._close_streams(process, stderr)
            elif stderr is not None:
                stderr.close()
            raise
        self._process, self._job, self._stderr = process, job, stderr
        self._have_geometry = self._have_lights = False
        return process, stderr

    @staticmethod
    def _read(stream, size):
        result = bytearray(size)
        view = memoryview(result)
        offset = 0
        while offset < size:
            n = stream.readinto(view[offset:])
            if not n:
                raise RuntimeError('Native preview worker closed its output')
            offset += n
        return result

    @staticmethod
    def _write(stream, data):
        view = memoryview(data)
        if not view.nbytes:
            return
        view = view.cast('B')
        while view:
            n = stream.write(view)
            if not n:
                raise RuntimeError('Native preview worker closed its input')
            view = view[n:]

    def _ack(self, process, count):
        magic, status, received, reserved = HEADER.unpack(self._read(process.stdout, HEADER.size))
        if magic != MAGIC or status != 0 or received != count or reserved != 0:
            raise RuntimeError('Invalid native preview worker response')

    def _cancelled(self, request):
        with self._condition:
            return (self._closed or request.epoch != self._epoch
                    or self._desired != (request.owner, request.token))

    def _execute(self, process, request):
        scene_upload = not self._have_geometry or self._geometry_token != (request.owner, request.geometry_token)
        lights_upload = not self._have_lights or self._lights_token != (request.owner, request.lights_token)
        if self._cancelled(request):
            return None
        if scene_upload:
            self._write(process.stdin, HEADER.pack(MAGIC, 1, len(request.vertices), 0))
            self._write(process.stdin, request.vertices)
            self._write(process.stdin, request.metadata)
            self._ack(process, len(request.vertices))
            self._geometry_token = (request.owner, request.geometry_token)
            self._have_geometry = True
        if self._cancelled(request):
            return None
        if lights_upload:
            self._write(process.stdin, HEADER.pack(MAGIC, 2, len(request.lights), 0))
            self._write(process.stdin, request.lights)
            self._ack(process, len(request.lights))
            self._lights_token = (request.owner, request.lights_token)
            self._have_lights = True
        pixels = np.empty((len(request.points), 4), dtype='<f4')
        for first in range(0, len(request.points), self.packet_points):
            if self._cancelled(request):
                return None
            points = request.points[first:first+self.packet_points]
            self._write(process.stdin, HEADER.pack(MAGIC, 3, len(points), 0))
            self._write(process.stdin, PARAMS.pack(*request.params))
            self._write(process.stdin, points)
            self._ack(process, len(points))
            chunk = np.frombuffer(self._read(process.stdout, len(points)*16), dtype='<f4').reshape(-1, 4)
            if not np.isfinite(chunk).all():
                raise RuntimeError('Native preview worker returned nonfinite lighting')
            pixels[first:first+len(points)] = chunk
        if self._cancelled(request):
            return None
        return pixels, scene_upload, lights_upload

    def _run(self):
        process = stderr = None
        try:
            while True:
                with self._condition:
                    while self._pending is None and not self._closed:
                        self._condition.wait()
                    if self._closed:
                        return
                    request, self._pending = self._pending, None
                    try:
                        if self._process is None:
                            process, stderr = self._start_locked()
                        else:
                            process, stderr = self._process, self._stderr
                        self._deadline = time.monotonic()+self.timeout
                    except Exception as exc:
                        self._fault = str(exc)
                        if self._desired is not None:
                            self._result = (*self._desired, Result(error=self._fault))
                        continue
                start = time.monotonic()
                try:
                    completed = self._execute(process, request)
                    if completed is not None:
                        pixels, scene_upload, lights_upload = completed
                        result = Result(pixels=pixels, elapsed_ms=(time.monotonic()-start)*1000,
                                        scene_uploaded=scene_upload, lights_uploaded=lights_upload)
                    else:
                        result = None
                except Exception as exc:
                    with self._condition:
                        was_cancelled = request.epoch != self._epoch or self._closed
                        self._stop_locked()
                        if not was_cancelled:
                            detail = ''
                            try:
                                stderr.seek(0, 2)
                                size = stderr.tell()
                                stderr.seek(max(0, size-4096))
                                detail = stderr.read().decode('utf-8', 'replace').strip()
                            except Exception:
                                pass
                            self._fault = self._fault or str(exc)
                            if detail:
                                self._fault += ': '+detail
                            if self._desired is not None:
                                self._result = (*self._desired, Result(error=self._fault))
                            self._pending = None
                    self._close_streams(process, stderr)
                    process = stderr = None
                    continue
                with self._condition:
                    self._deadline = 0.0
                    if result is not None and request.epoch == self._epoch and self._desired == (request.owner, request.token):
                        self._result = (request.owner, request.token, result)
                    if process is not self._process:
                        self._close_streams(process, stderr)
                        process = stderr = None
        finally:
            with self._condition:
                self._stop_locked()
            if process is not None:
                self._close_streams(process, stderr)

    def _watch(self):
        while not self._wake.wait(.1):
            with self._condition:
                if self._closed:
                    return
                if self._stderr is not None and not self._stderr.closed and os.fstat(self._stderr.fileno()).st_size > 1024*1024:
                    self._fault = 'Native preview worker exceeded its diagnostic output limit'
                    self._stop_locked()
                if self._deadline and time.monotonic() > self._deadline:
                    self._fault = 'Native preview worker exceeded its deadline'
                    self._stop_locked()


_client = None
_client_lock = threading.Lock()


def get_client():
    global _client
    with _client_lock:
        if _client is None:
            _client = NativeClient()
        return _client


def shutdown():
    global _client
    with _client_lock:
        previous, _client = _client, None
    if previous is not None:
        previous.close()


atexit.register(shutdown)
