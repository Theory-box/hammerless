"""Nav mesh visibility: which areas can see which (the lists the AI Director uses to spawn
zombies out of sight). Follows CNavArea::ComputeVisibilityToMesh / ComputeVisibility /
IsPartiallyVisible from Valve's Source SDK 2013 nav code, reimplemented.

The game first culls pairs with the compiled map's PVS; that only skips pairs the line of
sight traces would reject anyway, so we go straight to the traces.
"""
from __future__ import annotations

import math

from .collision import CollisionWorld

HUMAN_HEIGHT = 71.0
EYE = 0.75 * HUMAN_HEIGHT
# L4D2's nav_max_view_distance is 0 (asked the game): areas are gathered within the default
# 1500 units, and there is no further distance cut-off in the visibility test.
MAX_VIEW_DISTANCE = 0.0            # nav_max_view_distance
DEF_VIEW_DISTANCE = 1500.0
DOT_TOLERANCE = 0.98               # nav_potentially_visible_dot_tolerance
GENERATION_STEP = 25.0
NOT_VISIBLE, POTENTIALLY_VISIBLE, COMPLETELY_VISIBLE = 0, 1, 2
_ZERO = (0.0, 0.0, 0.0)


from .bsppvs import f32 as _f32


def in_radius(centre, pos, radius: float) -> bool:
    """ForAllAreasInRadius' test, in the game's floats: (centre - pos).LengthSqr() <= radius^2."""
    dx, dy, dz = _f32(centre[0] - pos[0]), _f32(centre[1] - pos[1]), _f32(centre[2] - pos[2])
    d2 = _f32(_f32(_f32(dx * dx) + _f32(dy * dy)) + _f32(dz * dz))
    return d2 <= _f32(radius * radius)


def _norm(v):
    length = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    return (v[0] / length, v[1] / length, v[2] / length) if length else v


class Visibility:
    def __init__(self, world: CollisionWorld, areas):
        """areas: navfile.NavArea objects (nw, se, ne_z, sw_z, centre, corners, z_at)."""
        self.world = world
        self.areas = areas
        self.traces = 0

    def clear(self, a, b) -> bool:
        """UTIL_TraceLine with MASK_NAV_VISION: nothing solid in between."""
        self.traces += 1
        return self.world.trace_hull(a, b, _ZERO, _ZERO).fraction >= 1.0

    def partially_visible(self, area, eye) -> bool:
        """IsPartiallyVisible: the centre or a corner of 'area' seen from 'eye'."""
        c = area.centre
        target = (c[0], c[1], c[2] + EYE)
        if self.clear(eye, target):
            return True
        to_centre = _norm((target[0] - eye[0], target[1] - eye[1], target[2] - eye[2]))
        for cx, cy, cz in area.corners:
            corner = (cx, cy, cz + EYE)
            to_corner = _norm((corner[0] - eye[0], corner[1] - eye[1], corner[2] - eye[2]))
            if to_corner[0] * to_centre[0] + to_corner[1] * to_centre[1] + to_corner[2] * to_centre[2] >= DOT_TOLERANCE:
                continue
            # (sic) the game adds the eye height a second time here
            if self.clear(eye, (corner[0], corner[1], corner[2] + EYE)):
                return True
        return False

    def compute(self, this, area) -> int:
        """ComputeVisibility(this -> area): how completely 'this' is visible to 'area'."""
        tc, ac = this.centre, area.centre
        dist_sq = (tc[0] - ac[0]) ** 2 + (tc[1] - ac[1]) ** 2 + (tc[2] - ac[2]) ** 2
        if MAX_VIEW_DISTANCE > 0.00001 and dist_sq > MAX_VIEW_DISTANCE ** 2:
            return NOT_VISIBLE
        corners = [(x, y, z + EYE) for x, y, z in this.corners]
        centre = (tc[0], tc[1], tc[2] + EYE)
        lo = (corners[0][0] - centre[0], corners[0][1] - centre[1], min(c[2] for c in corners) - centre[2])
        hi = (corners[2][0] - centre[0], corners[2][1] - centre[1], max(c[2] for c in corners) + 0.1 - centre[2])
        omin, omax = area.nw, area.se
        tx = min(max(centre[0], omin[0]), omax[0])
        ty = min(max(centre[1], omin[1]), omax[1])
        target = (tx, ty, area.z_at(tx, ty) + EYE)
        self.traces += 1
        tr = self.world.trace_hull(centre, target, lo, hi)
        if tr.fraction == 1.0 or (omin[0] < tr.endpos[0] < omax[0] and omin[1] < tr.endpos[1] < omax[1]):
            return COMPLETELY_VISIBLE
        vis = COMPLETELY_VISIBLE
        if self.partially_visible(area, centre):
            vis |= POTENTIALLY_VISIBLE
        else:
            vis &= ~COMPLETELY_VISIBLE
        to_centre = _norm((tc[0] - ac[0], tc[1] - ac[1], tc[2] - ac[2]))
        margin = GENERATION_STEP / 2.0
        sx_max, sy_max = this.se[0] - this.nw[0] - margin, this.se[1] - this.nw[1] - margin
        y = margin
        while y <= sy_max:
            x = margin
            while x <= sx_max:
                if vis == POTENTIALLY_VISIBLE:
                    return POTENTIALLY_VISIBLE
                px, py = this.nw[0] + x, this.nw[1] + y
                test = (px, py, this.z_at(px, py) + EYE)
                if dist_sq > 1000 ** 2:
                    d = _norm((test[0] - centre[0], test[1] - centre[1], test[2] - centre[2]))
                    if d[0] * to_centre[0] + d[1] * to_centre[1] + d[2] * to_centre[2] >= DOT_TOLERANCE:
                        x += GENERATION_STEP
                        continue
                if self.partially_visible(area, test):
                    vis |= POTENTIALLY_VISIBLE
                else:
                    vis &= ~COMPLETELY_VISIBLE
                x += GENERATION_STEP
            y += GENERATION_STEP
        return vis

    def run(self, progress=None) -> dict[int, dict[int, int]]:
        """ComputeVisibilityToMesh for every area. Returns {area id: {visible area id: attributes}}."""
        lists: dict[int, dict[int, int]] = {a.id: {} for a in self.areas}
        done = set()
        radius = MAX_VIEW_DISTANCE if MAX_VIEW_DISTANCE else DEF_VIEW_DISTANCE
        for i, cur in enumerate(self.areas):
            if progress and i % 50 == 0:
                progress(i, len(self.areas))
            cc = cur.centre
            for other in self.areas:
                if not in_radius(other.centre, cc, radius):
                    continue
                key = (min(cur.id, other.id), max(cur.id, other.id))
                if key in done:
                    continue
                done.add(key)
                if other is cur:
                    lists[cur.id][cur.id] = COMPLETELY_VISIBLE
                    continue
                other_to_this = self.compute(cur, other)
                # the SDK only computes the reverse when the first test saw something; L4D2 does it
                # every time (measured: otherwise ~2% of the game's visible pairs are missed)
                this_to_other = self.compute(other, cur)
                if not other_to_this and this_to_other:
                    other_to_this = POTENTIALLY_VISIBLE
                if not this_to_other and other_to_this:
                    this_to_other = POTENTIALLY_VISIBLE
                if this_to_other:
                    lists[cur.id][other.id] = this_to_other
                if other_to_this:
                    lists[other.id][cur.id] = other_to_this
        return lists


def expand_game_lists(mesh) -> dict[int, dict[int, int]]:
    """The game stores visibility as an inherited list plus differences; expand to full lists
    keyed by area id. In the file, list entries are area *positions* (0-based index into the
    area list: their distances top out at exactly the 1500-unit radius that way, and some
    entries are 0), while 'inherit from' is an area id."""
    by = mesh.by_id()
    ids = [a.id for a in mesh.areas]
    out = {}
    for a in mesh.areas:
        full = {}
        if a.inherit_visibility and a.inherit_visibility in by:
            full.update({ids[i]: attr for i, attr in by[a.inherit_visibility].visible if 0 <= i < len(ids)})
        full.update({ids[i]: attr for i, attr in a.visible if 0 <= i < len(ids)})
        out[a.id] = {i: attr for i, attr in full.items() if attr != NOT_VISIBLE}
    return out
