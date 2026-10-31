"""Deterministic surface-light quadrature, in Hammer units.

Samples are ordinary unnamed Source spotlights with a hemispherical cosine
falloff. Both Valve VRAD and Hammerless consume them; no runtime geometry is
created. Sample weights preserve total power when size/density changes.
"""
import math
from .ir import Entity

SPACING = 16.0
MAX_AREA_SAMPLES = 256
MAX_MESH_SAMPLES = 2048


def light_sample(position, normal, radiance_weight, source, keyvalues=None):
    """radiance_weight = linear radiance times sample area / 100, per RGB channel."""
    rgb = [max(0.0, float(c)) for c in radiance_weight]
    if not all(math.isfinite(c) for c in rgb):
        raise ValueError('Emission must have finite color and strength')
    peak = max(rgb)
    if peak <= 1e-10:
        return None
    # VRAD decodes color with gamma 2.2, then multiplies by brightness.
    color = [255 * (c / peak) ** (1 / 2.2) for c in rgb]
    nx, ny, nz = normal
    angles = (math.degrees(math.atan2(nz, math.hypot(nx, ny))), math.degrees(math.atan2(ny, nx)), 0)
    kv = dict(keyvalues or {})
    # Almost equal cones give Lambert's cosine over the entire forward hemisphere.
    # Avoid exactly equal cone thresholds (division by zero in VRAD's spot code).
    kv['_hl_surface_sample'] = '1'
    kv.update(_light=' '.join(f'{c:.9g}' for c in (*color, peak)),
              _inner_cone='89.999', _cone='90', _exponent='1',
              _constant_attn='0', _linear_attn='0', _quadratic_attn='1')
    kv.pop('_lightHDR', None)
    kv.pop('target', None)
    kv.pop('pitch', None)
    return Entity('light_spot', tuple(position), angles, kv, source=source)


def rectangle_samples(width, height, ellipse=False):
    """Equal-area samples on rectangle or ellipse, returning x,y,normalized weight."""
    if min(width, height) <= 0:
        return []
    count = min(MAX_AREA_SAMPLES, max(4, math.ceil(width * height / SPACING ** 2)))
    if ellipse:
        # Equal-area radial strata, golden-angle azimuth (no rejected edge cells).
        return [(math.sqrt((i + .5) / count) * math.cos(i * 2.399963229728653) * width / 2,
                 math.sqrt((i + .5) / count) * math.sin(i * 2.399963229728653) * height / 2,
                 1 / count) for i in range(count)]
    nx = max(1, min(count, round(math.sqrt(count * width / height))))
    ny = max(1, count // nx)
    return [((i + .5) * width / nx - width / 2, (j + .5) * height / ny - height / 2, 1 / (nx * ny))
            for j in range(ny) for i in range(nx)]


def triangle_samples(count):
    """Deterministic, evenly distributed barycentric samples inside a triangle."""
    for i in range(count):
        root = math.sqrt((i * .7548776662466927 + .5) % 1)
        v = (i * .6180339887498949 + .5) % 1
        yield (1 - root, root * (1 - v), root * v)
