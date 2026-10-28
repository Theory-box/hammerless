"""Conservative, pre-compile lightmap scaling. No lighting bake or scene mutation.

Viewing regions come from freshly predicted navigation areas with edge padding
and a crouch-to-jump eye-height envelope. Missing nav keeps original scales.
An occlusion decision must cover an entire receiver bounds AND eye region, not
just a handful of rays. Unknown visibility keeps detail. No geometry is removed.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

from .collision import brush_from_vmf_sides, _windings, _POINTS, _norm, _cross, _sub, _dot, snap_plane, Side
from .vmf import parse


@dataclass(frozen=True)
class Options:
    near: float = 512.0
    maximum: int = 64
    distance: bool = True
    budget: int = 2000000
    view_margin: float = 16.0


def bounds(points):
    return (tuple(min(p[k] for p in points) for k in range(3)),
            tuple(max(p[k] for p in points) for k in range(3)))


def support(box, n):
    return sum(n[k] * box[n[k] >= 0][k] for k in range(3))


def distance(a, b):
    return math.sqrt(sum(max(0, a[0][k] - b[1][k], b[0][k] - a[1][k]) ** 2 for k in range(3)))


def overlaps(a, b, margin=0.05):
    return all(a[0][k] <= b[1][k] + margin and b[0][k] <= a[1][k] + margin for k in range(3))


def blocked(eyes, target, brush):
    """Prove EVERY eye-to-target segment crosses this opaque convex brush.

    At fraction t along the segments, the maximum plane coordinate is linear in
    t. Intersect the t intervals where the whole bundle is strictly inside every
    brush halfspace. If no common interval exists, visibility is unknown.
    """
    lo, hi = 0.00001, 0.99999
    for s in brush.sides:
        a, b = support(eyes, s.normal), support(target, s.normal)
        c = s.dist - 0.05
        delta = b - a
        if abs(delta) < 1e-12:
            if a > c:
                return False
        elif delta > 0:
            hi = min(hi, (c - a) / delta)
        else:
            lo = max(lo, (c - a) / delta)
        if lo > hi:
            return False
    return True


def scale_for(base, nearest, options):
    ceiling = max(base, options.maximum)
    if math.isinf(nearest):
        return ceiling
    if not options.distance:
        return base
    scale = base
    # Double sample spacing only after doubling the protected viewing distance.
    threshold = max(1.0, options.near) * 2
    while nearest >= threshold and scale * 2 <= ceiling:
        scale *= 2
        threshold *= 2
    return scale


class Occluders:
    """Vectorized version of the same whole-bundle halfspace proof."""
    def __init__(self, brushes):
        import numpy as np
        self.np = np
        self.boxes = np.asarray([(b.mins, b.maxs) for b in brushes], dtype=float).reshape(-1, 2, 3)
        self.counts = np.asarray([len(b.sides) for b in brushes], dtype=int)
        self.owner = np.repeat(np.arange(len(brushes)), self.counts)
        self.normals = np.asarray([s.normal for b in brushes for s in b.sides], dtype=float).reshape(-1, 3)
        self.distances = np.asarray([s.dist - 0.05 for b in brushes for s in b.sides])

    def blocked(self, eye, target, remaining):
        np = self.np
        lo, hi = np.minimum(eye[0], target[0]), np.maximum(eye[1], target[1])
        eligible = np.all((self.boxes[:, 0] <= hi + 0.05) & (self.boxes[:, 1] >= lo - 0.05), axis=1)
        ids = np.flatnonzero(eligible)[:remaining]
        if not len(ids):
            return False, 0
        eligible[:] = False
        eligible[ids] = True
        mask = eligible[self.owner]
        n, c = self.normals[mask], self.distances[mask]
        a = (n * np.where(n >= 0, eye[1], eye[0])).sum(axis=1)
        b = (n * np.where(n >= 0, target[1], target[0])).sum(axis=1)
        delta = b - a
        parallel = np.abs(delta) < 1e-12
        t = np.divide(c - a, delta, out=np.zeros_like(delta), where=~parallel)
        starts = np.r_[0, np.cumsum(self.counts[ids])[:-1]]
        lower = np.maximum.reduceat(np.where((delta < 0) & ~parallel, t, 0.00001), starts)
        upper = np.minimum.reduceat(np.where((delta > 0) & ~parallel, t, 0.99999), starts)
        bad = np.logical_or.reduceat(parallel & (a > c), starts)
        hits = np.flatnonzero((lower <= upper) & ~bad)
        return (True, int(hits[0]) + 1) if len(hits) else (False, len(ids))


@dataclass
class Face:
    block: object
    normal: tuple
    dist: float
    box: tuple
    scale: int


def groups(faces):
    """Touching coplanar bounds share the finest member's requirement.

    Bounds can join extra faces around holes; this costs memory, never detail.
    Opposite normals and disconnected islands remain separate.
    """
    buckets = {}
    for f in faces:
        key = (*f.normal, f.dist)
        buckets.setdefault(key, []).append(f)
    result = []
    for bucket in buckets.values():
        components = []
        for f in bucket:
            hit = [c for c in components if any(overlaps(f.box, g.box) for g in c)]
            merged = [f]
            for c in hit:
                merged.extend(c)
                components.remove(c)
            components.append(merged)
        result.extend(components)
    return result


def _set(block, key, value):
    block.items = [x for x in block.items if not (isinstance(x, tuple) and x[0].lower() == key.lower())]
    block.kv(key, value)


def _opaque(name, content, cache):
    name = name.lower().replace('\\', '/')
    if name in cache:
        return cache[name]
    if name in {'tools/toolsnodraw', 'tools/toolsblack'}:
        return True
    if content is None or name.startswith('tools/'):
        return False
    raw = content.read('materials/' + name.removesuffix('.vmt') + '.vmt')
    if not raw:
        cache[name] = False
        return False
    blocks = parse(raw.decode('utf8', errors='replace'))
    # Patches, proxies and unknown shaders are never assumed opaque.
    ok = len(blocks) == 1 and blocks[0].name.lower() in {'lightmappedgeneric', 'worldvertextransition'}
    if ok:
        m = blocks[0]
        # %keywords is Hammer's material-browser metadata, not a compile flag.
        # Rejecting it excluded most stock brick/stone/wood receiving surfaces.
        ok = (all(isinstance(it, tuple) and (not it[0].startswith('%') or it[0].lower() == '%keywords')
                  for it in m.items)
              and all(m.get(k, '0').strip() in {'0', '0.0', ''} for k in (
            '$translucent', '$alphatest', '$additive', '$nocull')) and m.get('$alpha', '1') in {'1', '1.0'}
              )
    cache[name] = ok
    return ok


def nav_view_regions(text, options, regions=(), climbs=(), wall_climbs=False):
    """Predict from this export, not last build's nav. Never write/replace game nav."""
    from . import navpredict, fastnav
    if not fastnav.available():
        return []  # Avoid unexpectedly expensive Python nav generation during export.
    mesh = navpredict.predict(text, regions, climbs=climbs, wall_climbs=wall_climbs, roofs=False)
    return mesh_view_regions(mesh, options.view_margin)


def mesh_view_regions(mesh, margin):
    """Continuous eye envelopes over navigation; include ladder transit space."""
    out = []
    pad = max(0.0, margin)
    for area in mesh.areas:
        lo, hi = bounds(area.corners)
        out.append(((lo[0] - pad, lo[1] - pad, lo[2] + 24),
                    (hi[0] + pad, hi[1] + pad, hi[2] + 144)))
    if not out:
        return []
    for ladder in mesh.ladders:
        lo, hi = bounds([ladder.bottom, ladder.top])
        width = max(16, ladder.width / 2) + pad
        out.append(((lo[0] - width, lo[1] - width, lo[2] + 24),
                    (hi[0] + width, hi[1] + width, hi[2] + 144)))
    return out


def optimize(text, content=None, options=Options(), regions=(), climbs=(), wall_climbs=False):
    """Return (VMF, summary). Only static world/detail receiving faces are changed.

    Fresh navigation follows current geometry, with crouch/jump heights and edge
    leeway. No stale BSP/nav is reused and no game nav file is overwritten.
    Unsupported moving platforms/instances fail open to the unmodified map.
    """
    doc = parse(text)
    entities = [b for b in doc if b.name.lower() == 'entity']
    unsafe = {'func_instance', 'func_tracktrain', 'func_train', 'func_movelinear', 'func_rotating',
              'func_physbox', 'func_physbox_multiplayer', 'trigger_teleport', 'func_platrot', 'func_elevator'}
    if any(e.get('classname', '').lower() in unsafe for e in entities):
        return text, 'Adaptive lightmaps: kept original scales (moving viewpoints or unexpanded instances).'
    faces, blockers, eyes = [], [], []
    material_cache = {}
    worlds = [b for b in doc if b.name.lower() == 'world']
    for owner in worlds + entities:
        cls = owner.get('classname', '').lower()
        static = owner in worlds or cls == 'func_detail'
        # Doors and removable barriers may be walked on, but never occlude this analysis.
        for solid in owner.blocks('solid'):
            sides = solid.blocks('side')
            if any(s.blocks('dispinfo') for s in sides):
                return text, 'Adaptive lightmaps: kept original scales (displacement viewing regions not yet supported).'
            brush = brush_from_vmf_sides([(s.get('plane', ''), s.get('material', '')) for s in sides])
            if brush is None:
                continue
            original = []
            for s in sides:
                match = _POINTS.search(s.get('plane', ''))
                if not match:
                    break
                p0, p1, p2 = (tuple(float(c) for c in g.split()) for g in match.groups())
                normal, length = _norm(_cross(_sub(p0, p1), _sub(p2, p1)))
                if not length:
                    break
                n, d = snap_plane(normal, _dot(p0, normal))
                original.append(Side(n, d))
            if len(original) != len(sides):
                return text, 'Adaptive lightmaps: invalid brush planes; kept original scales.'
            windings = _windings([(s.normal, s.dist) for s in original])
            for side, plane, points in zip(sides, original, windings):
                if len(points) < 3:
                    continue
                box = bounds(points)
                if (static and not side.get('material', '').lower().startswith('tools/')
                        and (content is None or _opaque(side.get('material', ''), content, material_cache))):
                    base = max(1, int(side.get('lightmapscale', '16')))
                    faces.append(Face(side, plane.normal, plane.dist, box, base))
            if static and all(_opaque(s.get('material', ''), content, material_cache) for s in sides):
                blockers.append(brush)
    eyes = nav_view_regions(text, options, regions, climbs, wall_climbs)
    if not eyes:
        return text, 'Adaptive lightmaps: no usable predicted nav; kept original scales.'
    eyes = sorted(set(eyes))
    if not eyes or not faces:
        return text, 'Adaptive lightmaps: no usable viewing regions or receiving faces; kept original scales.'
    changed = hidden = 0
    before = after = 0.0
    checks = 0
    import numpy as np
    eye_boxes = np.asarray(eyes)
    occluders = Occluders(blockers)
    components = groups(faces)
    for component in components:
        box = bounds([p for f in component for p in f.box])
        n, d = component[0].normal, component[0].dist
        target = tuple(tuple(p[k] + n[k] * 0.5 for k in range(3)) for p in box)
        nearest = math.inf
        normal = np.asarray(n)
        facing = (np.where(normal >= 0, eye_boxes[:, 1], eye_boxes[:, 0]) * normal).sum(axis=1) > d + 0.05
        ids = np.flatnonzero(facing)
        gaps = np.maximum(0, np.maximum(eye_boxes[ids, 0] - box[1], box[0] - eye_boxes[ids, 1]))
        distances = np.sqrt((gaps * gaps).sum(axis=1))
        order = np.argsort(distances, kind='stable')
        candidates = ((float(distances[k]), eyes[ids[k]]) for k in order)
        for dist, eye in candidates:
            if checks >= options.budget:
                nearest = dist
                break
            occluded, used = occluders.blocked(eye, target, options.budget - checks)
            checks += used
            if not occluded:
                nearest = dist
                break
        base = min(f.scale for f in component)
        chosen = scale_for(base, nearest, options)
        # A coarser material override must not drag its neighbours down.
        # Preserve explicit coarser overrides; other members share chosen scale.
        for f in component:
            scale = max(f.scale, chosen)
            extent = sorted((f.box[1][k] - f.box[0][k] for k in range(3)), reverse=True)
            area = extent[0] * extent[1]  # an estimate, not the compiled sample count
            before += area / (f.scale * f.scale)
            after += area / (scale * scale)
            if scale != f.scale:
                _set(f.block, 'lightmapscale', scale)
                changed += 1
                hidden += math.isinf(nearest)
    if not changed:
        return text, f'Adaptive lightmaps: {len(components)} surfaces checked; original scales retained.'
    # Keep radiosity patch density at least as fine as before coarsening. This is
    # a conservative global floor, not a promise of numerically identical bounce.
    for w in worlds:
        _set(w, '_hl_transport_lightmap_scale', min(f.scale for f in faces))
    out = []
    for block in doc:
        block.write(out)
    saved = 100 * (1 - after / before) if before else 0
    return ''.join(out), (f'Adaptive lightmaps: coarsened {changed}/{len(faces)} brush sides '
                         f'({hidden} unseen), {len(components)} surfaces, {len(eyes)} nav viewing regions; '
                         f'estimated receiver samples reduced {saved:.0f}% before BSP clipping. '
                         'Geometry and shadow casters retained.')
