"""Camera-independent native receiver cache for live brush charts.

Uses the existing native path tracer, not the experimental BSP/VRAD server.
Only receiver scheduling is culled; the complete ray scene remains resident.
Retained illumination may be stale during edits, but displayed geometry never is.
"""
import math
import numpy as np
import gpu
from .previewlightcache import SurfaceCacheDisplay
from .previewcull import visible_bounds


class NativeSurfaceCache:
    REQUEST_SAMPLES = 8192

    def __init__(self):
        self.layout = None
        self.signature = None
        self.request = None
        self.display = None
        self.texture = None
        self.size = (1, 1)
        self.values = np.empty((0, 4), dtype=np.float32)
        self.counts = np.empty(0, dtype=np.int32)
        self.chart_ranges = {}
        self.error = ''
        self.evaluations = 0
        self.uploads = 0
        self.exclude_keys = set()
        self.remaining = {}
        self.display_signature = None
        self.chart_owners = []
        self.ready_objects = set()

    def _refresh_display(self, live):
        """Partition objects between direct and cached drawing, never both.

        Switching only complete objects avoids coincident fallback triangles and
        cached triangles, whose transform rounding otherwise causes z-fighting.
        """
        owner_by_generation = {entry['generation']: key for key, entry in live.object_data.items()}
        owners = [owner_by_generation.get(live.display_tokens[c['face']][0]) for c in live.charts]
        ready = {}
        for owner, chart in zip(owners, live.charts):
            start = chart['start']; length = chart['width'] * chart['height']
            valid = bool(np.all(self.values[start:start + length, 3] > 0))
            ready[owner] = ready.get(owner, True) and valid
        # Unknown ownership or missing coverage metadata must retain direct drawing.
        ready_objects = {key for key, valid in ready.items() if valid and key is not None
                         and (key in live.keys or key in live.remaining)}
        self.chart_owners = owners
        self.ready_objects = ready_objects
        signature = (self.layout, frozenset(ready_objects))
        if signature == self.display_signature:
            return
        charts = [chart for owner, chart in zip(owners, live.charts) if owner in ready_objects]
        self.exclude_keys = live.keys & ready_objects
        self.remaining = {key: value for key, value in live.remaining.items() if key in ready_objects}
        self.display_signature = signature
        if charts:
            self.display = SurfaceCacheDisplay(self, live.faces, charts,
                lambda face: live.bindings[face.index], units_per_meter=live.units,
                reuse=self.display, face_tokens=live.display_tokens)
        else:
            self.display = None

    def _upload(self):
        pixels = np.zeros((self.size[0] * self.size[1], 4), dtype=np.float32)
        pixels[:len(self.values)] = self.values
        self.texture = gpu.types.GPUTexture(self.size, format='RGBA32F',
            data=gpu.types.Buffer('FLOAT', pixels.size, pixels.reshape(-1)))
        self.uploads += 1

    def sync(self, live):
        """Rebind to current geometry; retain only unchanged chart illumination."""
        layout = (id(live), live.generation)
        if layout == self.layout:
            return False
        previous = self.values
        previous_counts = self.counts
        old_ranges = self.chart_ranges
        count = len(live.positions) if live.positions is not None else 0
        values = np.zeros((count, 4), dtype=np.float32)
        counts = np.zeros(count, dtype=np.int32)
        ranges = {}
        for chart in live.charts:
            start = chart['start']; length = chart['width'] * chart['height']
            key = (live.display_tokens[chart['face']], chart['width'], chart['height'])
            ranges[key] = (start, length)
            old = old_ranges.get(key)
            if old is not None and old[1] == length:
                values[start:start + length] = previous[old[0]:old[0] + length]
                counts[start:start + length] = previous_counts[old[0]:old[0] + length]
        self.values = values
        self.counts = counts
        self.chart_ranges = ranges
        self.layout = layout
        if count:
            width = min(2048, max(1, math.ceil(math.sqrt(count))))
            self.size = (width, math.ceil(count / width))
            self._upload()
        else:
            self.display = None
            self.texture = None
        self._refresh_display(live)
        return True

    def pause(self, native):
        native.pause()
        self.request = None

    def step(self, native, live, scene, signature, *, rays, bounces, frames,
             view_projection=None, paused=False, yield_after_result=False):
        changed = self.sync(live)
        key = (signature, int(rays), int(bounces),
               float(scene.unit_scale), tuple(scene.sky_color))
        if changed or key != self.signature:
            self.pause(native)
            if key != self.signature:
                self.counts[:] = 0
                self.signature = key
            self.error = ''
        if paused:
            self.pause(native)
            return False, 'Surface lighting paused; last results retained'
        if not len(self.values):
            return False, 'No supported brush charts; direct preview only'
        native._flush_pending()
        if self.request is not None:
            serial, indices, seed = self.request
            result = native.client.poll(native.owner, serial)
            if result is None:
                return True, 'Native surface lighting calculating; last results retained'
            if result is not None:
                self.request = None
                if result.error:
                    self.error = result.error
                else:
                    pixels = np.asarray(result.pixels, dtype=np.float32)
                    if pixels.shape != (len(indices), 4) or not np.isfinite(pixels).all():
                        self.error = 'Invalid native surface lighting result'
                    else:
                        # First fresh sample replaces retained stale lighting.
                        if seed == 0:
                            self.values[indices, :3] = pixels[:, :3]
                        else:
                            self.values[indices, :3] += (pixels[:, :3] - self.values[indices, :3]) / (seed + 1)
                        self.values[indices, 3] = 1
                        self.counts[indices] = seed + 1
                        self.evaluations += len(indices)
                        self._upload()
                        self._refresh_display(live)
                        if yield_after_result:
                            return True, 'Preparing visible surface lighting'
        visible = (np.flatnonzero(visible_bounds(live.bounds, view_projection))
                   if view_projection is not None else np.arange(len(live.charts)))
        # First display requires all chart samples on a visible object. Once
        # ready, further refinement stays restricted to its visible charts.
        unfinished = {self.chart_owners[i] for i in visible} - self.ready_objects
        if unfinished:
            visible = np.unique(np.r_[visible, [i for i, owner in enumerate(self.chart_owners)
                                                if owner in unfinished]]).astype(np.int64)
        candidates = [np.arange(live.charts[i]['start'], live.charts[i]['start'] +
                      live.charts[i]['width'] * live.charts[i]['height'], dtype=np.int32)
                      for i in visible]
        indices = np.concatenate(candidates) if candidates else np.empty(0, dtype=np.int32)
        target = max(1, int(frames))
        pending = indices[self.counts[indices] < target]
        if self.request is None and not self.error and len(pending):
            seed = int(self.counts[pending].min())
            selected = pending[self.counts[pending] == seed][:self.REQUEST_SAMPLES]
            points = np.zeros((len(selected), 8), dtype=np.float32)
            points[:, :3] = live.positions[selected] + live.normals[selected] / scene.unit_scale
            points[:, 3] = 1
            points[:, 4:7] = live.normals[selected]
            native.serial += 1
            serial = native.serial
            params = (int(rays), int(bounces), seed, 3, float(scene.unit_scale), *scene.sky_color)
            native.client.submit(native.owner, serial, native.geometry_token,
                native.vertices, native.metadata, native.lights_token, native.lights, points, params)
            self.request = (serial, selected, seed)
        progress = int(self.counts[indices].min()) if len(indices) else target
        status = self.error or f'Native surface lighting {progress}/{target}; unsupported surfaces use direct preview'
        return self.request is not None, status

    def draw(self, matrix, exposure=0):
        if self.display is not None:
            self.display.draw(matrix, exposure)

    def draw_depth(self, matrix):
        if self.display is not None:
            self.display.draw_depth(matrix)
