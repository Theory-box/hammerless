"""Predict VMF 220 planar lightmap axes, before BSP subdivision.

Based on Valve SDK 2013 vbsp/textures.cpp TexinfoForBrushTexture; checked
against L4D2 VBSP fixtures. Caller supplies the effective lightmap scale
(after material/build overrides). Not a displacement parameterization.
"""
import numpy as np


def lightmap_vectors(axes, lightmap_scale, origin=(0, 0, 0)):
    """axes: two (direction3, texture_shift, texture_scale) tuples.

    Returns world/local BSP-coordinate -> global luxel affine rows (2,4).
    `origin` follows VBSP's brush-origin argument, not a Blender transform.
    Directions are deliberately not normalized: VMF stores their magnitude.
    """
    scale=float(lightmap_scale)
    if not np.isfinite(scale) or scale<=0:
        raise ValueError('Effective lightmap scale must be finite and positive')
    rows=[]
    for direction,shift,texture_scale in axes:
        direction=np.asarray(direction,dtype=np.float64)
        if direction.shape!=(3,) or not np.all(np.isfinite(direction)) or np.linalg.norm(direction)==0:
            raise ValueError('Texture axis must be a finite nonzero 3-vector')
        texture_scale=float(texture_scale) or 1.0
        row=direction/scale
        rows.append([*row,float(shift)*texture_scale/scale+np.dot(origin,row)])
    result=np.asarray(rows,dtype=np.float64)
    if result.shape!=(2,4) or not np.all(np.isfinite(result)):
        raise ValueError('Two finite texture axes are required')
    return result
