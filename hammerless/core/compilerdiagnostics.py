"""Recover useful context from multiline compiler errors, without guessing objects."""
import json
import math
import re
from pathlib import Path

from .mapcheck import Problem

_NUMBER = r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?'
_POINT = re.compile(r'around point\s*\(\s*(' + _NUMBER + r')\s+(' + _NUMBER + r')\s+(' + _NUMBER + r')\s*\)', re.I)
_VERTEX = re.compile(r'bad surface extents point:\s*(' + _NUMBER + r')\s+(' + _NUMBER + r')\s+(' + _NUMBER + r')', re.I)


def surface_errors(text, vmf_path=None):
    """Keep material, dimensions and position; match only containing brush faces."""
    lines = text.splitlines()
    records = []
    for i, line in enumerate(lines):
        if 'bad surface extents' not in line.lower() or 'extents point:' in line.lower():
            continue
        detail = []
        for following in lines[i + 1:i + 4]:
            if not following.strip().lower().startswith(('material ', '(dimension:')):
                break
            detail.append(following.strip())
        message = line.strip() + (': ' + '; '.join(detail) if detail else '')
        match = _POINT.search(message)
        point = tuple(float(x) for x in match.groups()) if match else None
        if point and not all(math.isfinite(x) for x in point):
            point = None
        material = re.search(r'\bmaterial\s+(\S+)', message, re.I)
        vertices = []
        for preceding in reversed(lines[:i]):
            vertex = _VERTEX.search(preceding)
            if not vertex:
                break
            value = tuple(float(x) for x in vertex.groups())
            if all(math.isfinite(x) for x in value):
                vertices.append(value)
        records.append((message, point, material.group(1) if material else '', tuple(vertices)))
    # A failed Hammerless compile may be followed by Valve's more precise report.
    located_materials = {m.lower() for _, p, m, _ in records if p is not None}
    records = list(dict.fromkeys(r for r in records if r[1] is not None or r[2].lower() not in located_materials))
    document, sources = None, {}
    if vmf_path and any(p is not None for _, p, _, _ in records):
        from .vmfimport import Document
        try:
            document = Document(Path(vmf_path).read_text(encoding='utf-8', errors='replace'))
            sources = json.loads(Path(vmf_path).with_suffix('.brushes.json').read_text(encoding='utf-8'))
            if not isinstance(sources, dict):
                document = None
        except (OSError, ValueError, TypeError):
            document = None
    problems = []
    for message, point, material, vertices in records:
        matches = _sources_at(document, sources, point, material, vertices) if document and point else []
        if not matches:
            problems.append(Problem('ERROR', message, '', point))
        for source, side in matches:
            label = 'matching surface' if len(matches) == 1 else 'candidate surface'
            problems.append(Problem('ERROR', f"{message} — {label}: '{source}', exported side {side}", source, point))
    return problems


def _sources_at(document, sources, point, material, vertices=()):
    from .vmfimport import parse_plane, plane_of
    matches = []
    for solid_id, solid in document.solids.items():
        source = sources.get(str(solid_id))
        if not source:
            continue
        sides = solid.blocks('side')
        try:
            planes = [plane_of(*parse_plane(side.get('plane', ''))) for side in sides]
        except (ValueError, ZeroDivisionError):
            continue
        distances = [sum(point[k] * n[k] for k in range(3)) - d for n, d in planes]
        # Valve rounds its reported centre to tenths; permit that rounding only.
        if any(d > 0.2 for d in distances) or any(
                sum(v[k] * n[k] for k in range(3)) - d > 0.2
                for v in vertices for n, d in planes):
            continue
        for side, distance in zip(sides, distances):
            if abs(distance) <= 0.2 and side.get('material', '').lower() == material.lower():
                matches.append((source, side.get('id', '?')))
    return list(dict.fromkeys(matches))
