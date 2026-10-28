"""Conservative, pre-compile lightmap scaling. No lighting bake or scene mutation.

Viewing regions cover ALL upward brush surfaces, including unreachable roofs, plus
prop hulls and spawn positions. This deliberately overestimates playable space.
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
        ok = (all(isinstance(it, tuple) and not it[0].startswith('%') for it in m.items)
              and all(m.get(k, '0').strip() in {'0', '0.0', ''} for k in (
            '$translucent', '$alphatest', '$additive', '$nocull')) and m.get('$alpha', '1') in {'1', '1.0'}
              )
    cache[name] = ok
    return ok


def optimize(text, content=None, options=Options()):
    """Return (VMF, summary). Only static world/detail receiving faces are changed.

    This initial implementation uses geometry-derived viewing regions instead of
    cached nav: never stale, and covers jumping, crouching and unreachable ledges.
    Unsupported moving platforms/instances fail open to the unmodified map.
    """
    doc = parse(text)
    entities = [b for b in doc if b.name.lower() == 'entity']
    unsafe = {'func_instance', 'func_tracktrain', 'func_train', 'func_movelinear', 'func_rotating',
              'func_physbox', 'func_physbox_multiplayer', 'trigger_teleport', 'func_platrot', 'func_elevator'}
    if any(e.get('classname', '').lower() in unsafe for e in entities):
        return text, 'Adaptive lightmaps: kept original scales (moving viewpoints or unexpanded instances).'
    faces, blockers, eyes = [], [], []
    material_cache, prop_cache = {}, {}
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
            if cls in {'func_ladder', 'func_simpleladder'}:
                eyes.append(((brush.mins[0] - 32, brush.mins[1] - 32, brush.mins[2] + 24),
                             (brush.maxs[0] + 32, brush.maxs[1] + 32, brush.maxs[2] + 144)))
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
                if (plane.normal[2] >= 0.7 and not cls.startswith('trigger_')
                        and side.get('material', '').lower() not in {'tools/toolsskybox', 'tools/toolsskybox2d'}):
                    eyes.append(((box[0][0] - 32, box[0][1] - 32, box[0][2] + 24),
                                 (box[1][0] + 32, box[1][1] + 32, box[1][2] + 144)))
                if (static and not side.get('material', '').lower().startswith('tools/')
                        and (content is None or _opaque(side.get('material', ''), content, material_cache))):
                    base = max(1, int(side.get('lightmapscale', '16')))
                    faces.append(Face(side, plane.normal, plane.dist, box, base))
            if static and all(_opaque(s.get('material', ''), content, material_cache) for s in sides):
                blockers.append(brush)
        if cls.startswith('prop_'):
            # Collision hulls are only used as possible viewpoints, never as opaque blockers.
            from .navanalyze import _prop_pieces, _prop_solid, _prop_exists
            if content is None:
                return text, 'Adaptive lightmaps: kept original scales (prop viewing bounds unavailable).'
            pieces = _prop_pieces(owner, content, prop_cache)
            if not pieces and _prop_solid(owner):
                model = owner.get('model', '').replace('\\', '/').lower()
                if content.read(model) and not _prop_exists(cls, model, content, prop_cache):
                    continue  # The compiler/game removes this invalid prop.
                # Many foliage/decorative models have no physics file. Their
                # model hull is a conservative fallback, including invalid
                # static props the game itself would discard.
                import copy
                proxy = copy.deepcopy(owner)
                _set(proxy, 'classname', 'prop_static')
                _set(proxy, 'solid', '2')
                pieces = _prop_pieces(proxy, content, {})
                if not pieces:
                    return text, 'Adaptive lightmaps: kept original scales (a prop has unknown viewing bounds).'
            for p in pieces:
                eyes.append(((p.mins[0] - 32, p.mins[1] - 32, p.mins[2] + 24),
                             (p.maxs[0] + 32, p.maxs[1] + 32, p.maxs[2] + 144)))
        if cls in {'info_player_start', 'info_survivor_position', 'info_landmark'}:
            p = tuple(float(v) for v in owner.get('origin', '0 0 0').split())
            eyes.append(((p[0] - 32, p[1] - 32, p[2] + 24), (p[0] + 32, p[1] + 32, p[2] + 144)))
    eyes = sorted(set(eyes))
    if not eyes or not faces:
        return text, 'Adaptive lightmaps: no usable viewing regions or receiving faces; kept original scales.'
    changed = hidden = 0
    before = after = 0.0
    checks = 0
    components = groups(faces)
    for component in components:
        box = bounds([p for f in component for p in f.box])
        n, d = component[0].normal, component[0].dist
        target = tuple(tuple(p[k] + n[k] * 0.5 for k in range(3)) for p in box)
        nearest = math.inf
        candidates = sorted((distance(box, eye), eye) for eye in eyes if support(eye, n) >= d - 0.05)
        for dist, eye in candidates:
            if dist <= options.near or checks >= options.budget:
                nearest = dist
                break
            occluded = False
            bundle = bounds([*eye, *target])
            for brush in blockers:
                if not overlaps(bundle, (brush.mins, brush.maxs)):
                    continue
                checks += 1
                if blocked(eye, target, brush):
                    occluded = True
                    break
                if checks >= options.budget:
                    break
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
                         f'({hidden} unseen), {len(components)} surfaces, {len(eyes)} viewing regions; '
                         f'estimated receiver samples reduced {saved:.0f}% before BSP clipping. '
                         'Geometry and shadow casters retained.')
