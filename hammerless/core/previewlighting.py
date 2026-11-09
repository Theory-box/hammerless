"""Source-unit direct-light preparation for the experimental viewport.

Reference: native/hlvrad/lights.c LightForString / SetLightFalloffParams,
and shaders/gather.comp Standard. Values are linear VRAD intensity, NOT
display colors. Exposure, visibility, and material response are separate.
"""
from dataclasses import dataclass
import math


def light_intensity(spec, *, hdr=True, lightscale=1.0):
    values = [float(v) for v in spec.split()]
    if len(values) == 8:
        values = values[4:] if hdr else values[:4]
    if len(values) not in (1, 3, 4):
        raise ValueError('Light requires brightness, RGB, or RGB brightness')
    if any(not math.isfinite(v) or v < 0 for v in values):
        raise ValueError('Light values must be finite and nonnegative')
    rgb = values * 3 if len(values) == 1 else values[:3]
    scale = values[3] / 255 if len(values) == 4 else 1.0
    return tuple((c / 255) ** 2.2 * 255 * scale * lightscale for c in rgb)


@dataclass(frozen=True)
class PointLight:
    intensity: tuple
    attenuation: tuple

    @classmethod
    def from_keys(cls, keys, *, hdr=True, lightscale=1.0):
        if float(keys.get('_fifty_percent_distance', 0)):
            raise NotImplementedError('Distance-based falloff solver not implemented in preview')
        attn = tuple(float(keys.get(k, 0)) for k in
                     ('_constant_attn', '_linear_attn', '_quadratic_attn'))
        if not all(math.isfinite(v) for v in attn):
            raise ValueError('Attenuation must be finite')
        attn = tuple(v if v >= 0.001 else 0.0 for v in attn)
        if not any(attn):
            attn = (1.0, 0.0, 0.0)
        ratio = attn[0] + 100 * attn[1] + 10000 * attn[2]
        spec = keys.get('_light', '0')
        if hdr and keys.get('_lightHDR', '').strip():
            try:
                light_intensity(keys['_lightHDR'])
                spec = keys['_lightHDR']
            except ValueError:
                pass  # Source's negative HDR sentinel selects the regular light.
        hdr_scale = float(keys.get('_lightscaleHDR', 1)) if hdr else 1.0
        if not math.isfinite(hdr_scale) or hdr_scale < 0:
            raise ValueError('HDR intensity scale must be finite and nonnegative')
        intensity = light_intensity(spec, hdr=hdr, lightscale=lightscale*hdr_scale)
        return cls(tuple(v * ratio for v in intensity), attn)

    def irradiance(self, distance, cosine=1.0):
        """Unoccluded point-light contribution; distance is in Source units."""
        if not math.isfinite(distance) or distance < 0:
            raise ValueError('Distance must be finite and nonnegative')
        d = max(distance, 1.0)
        c, l, q = self.attenuation
        scale = max(cosine, 0.0) / (q * d * d + l * d + c)
        return tuple(v * scale for v in self.intensity)


def spot_parameters(keys):
    """Return (enabled, inner cosine, outer cosine, exponent), as ParseLightSpot."""
    inner = float(keys.get('_inner_cone', 0)) or 10.0
    outer = float(keys.get('_cone', 0)) or inner
    exponent = float(keys.get('_exponent', 0))
    if not all(math.isfinite(v) for v in (inner, outer, exponent)):
        raise ValueError('Spot parameters must be finite')
    outer = max(outer, inner)
    if inner == 180 and outer == 180:
        return (0., 0., 0., 0.)
    return (1., math.cos(math.radians(min(inner,90))),
            math.cos(math.radians(min(outer,90))), exponent)


def spot_factor(cosine, parameters):
    enabled, inner, outer, exponent = parameters
    if not enabled:
        return 1.0
    if cosine <= outer:
        return 0.0
    if cosine >= inner:
        return cosine
    ramp = (cosine-outer)/(inner-outer)
    if exponent not in (0,1):
        ramp = ramp ** (int(4*exponent)/4)
    return ramp*cosine


def vrad_light_direction(angles, keys):
    """Match current native/hlvrad SetupLightNormalFromProps, including pitch sign.

    Do not substitute Blender -Z here: the compiled entity is authoritative.
    Target entities require separate scene resolution.
    """
    yaw = float(keys.get('angle', 0)) or angles[1]
    pitch = float(keys.get('pitch', 0)) or angles[0]
    if yaw in (-1,-2):
        xy = (0.,0.)
    else:
        xy = (math.cos(math.radians(yaw)),math.sin(math.radians(yaw)))
    p = math.radians(pitch)
    return (xy[0]*math.cos(p),xy[1]*math.cos(p),math.sin(p))
