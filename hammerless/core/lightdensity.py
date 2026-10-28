"""Viewport density geometry from the compiled lightmap mapping, not scene settings."""
import numpy as np

# Upper bounds in Hammer units per luxel. Unknown/degenerate mapping is grey.
LEVELS = (4, 8, 16, 32, 64, 128)
COLORS = ((0.1, 0.3, 1.0), (0.0, 0.85, 1.0), (0.15, 0.85, 0.2),
          (1.0, 0.65, 0.05), (1.0, 0.15, 0.08), (0.75, 0.15, 1.0))


def triangle_steps(positions, uv):
    """Fallback/displacement metric; use unquantized local UVs when available."""
    p = positions.astype(np.float64).reshape(-1, 3, 3)
    u = uv.reshape(-1, 3, 2)
    e, d = p[:, 1:] - p[:, :1], u[:, 1:] - u[:, :1]
    det = d[:, 0, 0] * d[:, 1, 1] - d[:, 0, 1] * d[:, 1, 0]
    valid = np.abs(det) > 1e-10
    steps = np.zeros((len(p), 2), dtype=np.float64)
    if valid.any():
        a, b = d[valid], e[valid]
        s = (a[:, 1, 1, None] * b[:, 0] - a[:, 0, 1, None] * b[:, 1]) / det[valid, None]
        t = (-a[:, 1, 0, None] * b[:, 0] + a[:, 0, 0, None] * b[:, 1]) / det[valid, None]
        steps[valid] = np.stack((np.linalg.norm(s, axis=1), np.linalg.norm(t, axis=1)), axis=1)
    return steps


def resolution_mesh(data):
    """(luxel coordinates, vertex colours, min/max axis spacing).

    Flat faces use their compiled lightmap axes, not derivatives of tiny/sliver
    triangles in the float32 preview atlas. Displacements use their local mesh
    metric. Colour uses the larger axis step; the grid shows both directions.
    """
    h, w = data.atlas.shape[:2]
    uv = getattr(data, 'luxel_uvs', None)
    if uv is None:
        uv = data.uvs.astype(np.float64) * (w, h)
    steps = getattr(data, 'luxel_steps', None)
    if steps is None:
        steps = triangle_steps(data.positions, uv)
    valid = np.isfinite(steps).all(axis=1) & (steps > 0).all(axis=1)
    coarse = steps.max(axis=1)
    bins = np.searchsorted(LEVELS, coarse * (1 - 1e-4)).clip(0, len(LEVELS) - 1)
    colors = np.asarray(COLORS, dtype=np.float32)[bins]
    colors[~valid] = (0.4, 0.4, 0.4)
    limits = (float(steps[valid].min()), float(steps[valid].max())) if valid.any() else (0.0, 0.0)
    return uv.astype(np.float32), np.repeat(colors, 3, axis=0), limits
