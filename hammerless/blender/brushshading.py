"""Translate evaluated Blender smooth faces / sharp edges into Source brush smoothing groups."""
from collections import Counter

HARD = 0x80000000


def groups(mesh):
    # Smooth by Angle is Geometry Nodes: its evaluated sharp_edge/sharp_face attributes are
    # consumed by Blender's own grouping, including the modifier's Ignore Sharpness option.
    ids, _count = mesh.calc_smooth_groups(use_bitflags=False)
    counts = Counter(group for face, group in zip(mesh.polygons, ids) if face.use_smooth)
    # Blender's bitflag allocator separates edge neighbours, whereas vrad also joins faces
    # sharing just a vertex. Allocate bits against that stricter neighbourhood instead.
    at_vertex = {}
    for face, group in zip(mesh.polygons, ids):
        if face.use_smooth and counts[group] > 1:
            for vertex in face.vertices:
                at_vertex.setdefault(vertex, set()).add(group)
    neighbours = {group: set() for group, count in counts.items() if count > 1}
    for touching in at_vertex.values():
        for group in touching:
            neighbours[group].update(touching - {group})
    bits = {}
    for group in sorted(neighbours, key=lambda g: (-len(neighbours[g]), g)):
        used = {bits[n] for n in neighbours[group] if n in bits}
        available = next((1 << i for i in range(31) if (1 << i) not in used), None)
        if available is None:
            raise ValueError("Brush shading needs more than Source's 31 smoothing groups; split this mesh "
                             "into smaller objects before exporting")
        bits[group] = available
    result = []
    for face, group in zip(mesh.polygons, ids):
        if not face.use_smooth or counts[group] == 1:
            result.append(HARD)
        else:
            result.append(bits[group])
    return result
