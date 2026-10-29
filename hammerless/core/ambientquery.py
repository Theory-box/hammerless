"""Export compiled leaf ambient probes for inexpensive, leaf-local script queries.

This is the baked ambient cube field used for moving models, averaged over six
directions. It is not a light meter: dynamic lights and exposure are excluded.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import struct

MARKER = "// __HL_AMBIENT_DATA__"
MAX_BSP_BYTES = 512 * 1024 * 1024
MAX_SCRIPT_BYTES = 8 * 1024 * 1024


def read_ambient(data: bytes) -> dict:
    if len(data) < 1036 or data[:4] != b"VBSP":
        raise ValueError("invalid BSP header")
    if struct.unpack_from("<i", data, 4)[0] != 21:
        raise ValueError("ambient queries require an L4D2 BSP version 21")

    def lump(i, stride=None, version=None):
        # L4D2 stores VERSION, OFFSET, LENGTH, FOURCC, unlike most Source BSPs.
        ver, off, size, cc = struct.unpack_from("<4i", data, 8 + 16 * i)
        if size < 0 or off < 0 or off + size > len(data) or (size and off < 1036):
            raise ValueError(f"invalid BSP lump {i} bounds")
        if cc or (size and version is not None and ver != version):
            raise ValueError(f"unsupported compressed/versioned BSP lump {i}")
        if stride and size % stride:
            raise ValueError(f"invalid BSP lump {i} record size")
        return data[off:off + size]

    planes = [list(v[:4]) for v in struct.iter_unpack("<4fi", lump(1, 20))]
    raw_nodes, raw_leaves, models = lump(5, 32), lump(10, 32, 1), lump(14, 48)
    nodes = [list(struct.unpack_from("<3i", raw_nodes, i)) for i in range(0, len(raw_nodes), 32)]
    count = len(raw_leaves) // 32
    if not planes or not count or not models or count > 65536:
        raise ValueError("missing/oversized BSP world tree")
    root = struct.unpack_from("<i", models, 36)[0]
    def valid_child(n):
        return 0 <= n < len(nodes) or -count <= n < 0
    if not valid_child(root) or any(not 0 <= n[0] < len(planes) or not valid_child(n[1]) or not valid_child(n[2]) for n in nodes):
        raise ValueError("invalid BSP tree reference")
    if any(not math.isfinite(x) for p in planes for x in p):
        raise ValueError("non-finite BSP plane")
    # Reject cycles, including unreachable nodes, before exposing data to scripts.
    visited, active = set(), set()
    for start in range(len(nodes)):
        stack = [(start, False)]
        while stack:
            n, leave = stack.pop()
            if n < 0:
                continue
            if leave:
                active.remove(n)
                visited.add(n)
            elif n in active:
                raise ValueError("cyclic BSP tree")
            elif n not in visited:
                active.add(n)
                stack.extend(((n, True), (nodes[n][2], False), (nodes[n][1], False)))
    hdr = bool(lump(51) and lump(55))
    indices, samples = lump(51 if hdr else 52, 4, 0), lump(55 if hdr else 56, 28, 1)
    if len(indices) != 4 * count or not samples:
        raise ValueError("compiled map has no usable leaf ambient probes")
    records = list(struct.iter_unpack("<2H", indices))
    leaves, probes = [], []
    for leaf, (n, first) in enumerate(records):
        contents = struct.unpack_from("<i", raw_leaves, 32 * leaf)[0]
        if n > 64 or (n and (first + n) * 28 > len(samples)) or (not n and first >= count):
            raise ValueError("invalid ambient sample/neighbor reference")
        minsmaxs = struct.unpack_from("<6h", raw_leaves, 32 * leaf + 8)
        if any(minsmaxs[i] > minsmaxs[i + 3] for i in range(3)):
            raise ValueError("invalid leaf bounds")
        begin = len(probes)
        for k in range(n):
            raw = samples[28 * (first + k):28 * (first + k + 1)]
            pos = [minsmaxs[i] + raw[24 + i] / 255.0 * (minsmaxs[i + 3] - minsmaxs[i]) for i in range(3)]
            rgb = [0.0, 0.0, 0.0]
            for side in range(6):
                r, g, b, exp = struct.unpack_from("<BBBb", raw, side * 4)
                scale = math.ldexp(1.0, exp) / (255.0 * 6.0)
                for i, v in enumerate((r, g, b)):
                    rgb[i] += v * scale
            if any(not math.isfinite(v) or v > 1e30 for v in rgb):
                raise ValueError("invalid ambient intensity")
            probes.append(pos + rgb)
        leaves.append([begin, n, first if not n else -1, bool(contents & 1)])
    return dict(root=root, planes=planes, nodes=nodes, leaves=leaves, probes=probes, hdr=hdr)


def squirrel_data(data):
    if data is None:
        return "::HL_AmbientData <- null;"
    lines = ["::HL_AmbientData <- {root=%d,planes=[],nodes=[],leaves=[],probes=[]};" % data["root"]]
    for key in ("planes", "nodes", "leaves", "probes"):
        for row in data[key]:
            lines.append("HL_AmbientData.%s.append(%s);" % (key, json.dumps(row, separators=(",", ":"), allow_nan=False)))
    result = "\n".join(lines)
    if len(result.encode()) > MAX_SCRIPT_BYTES:
        raise ValueError("ambient script data exceeds the 8 MiB export budget")
    return result


def augment_scripts(files: dict, bsp_path, mapname: str):
    """Inject only into requesting scripts, never reuse a previously injected field."""
    if not any(MARKER in text for text in files.values()):
        return files, []
    warnings = []
    field = None
    try:
        if not bsp_path:
            raise ValueError("build the map to supply baked ambient data")
        path = Path(bsp_path)
        if path.stat().st_size > MAX_BSP_BYTES:
            raise ValueError("BSP exceeds the 512 MiB ambient reader budget")
        field = read_ambient(path.read_bytes())
        script = squirrel_data(field)
    except (OSError, ValueError, struct.error, OverflowError) as exc:
        warnings.append(f"Baked Ambient unavailable for {mapname}: {exc}")
        script = squirrel_data(None)
    return {name: text.replace(MARKER, script) for name, text in files.items()}, warnings
