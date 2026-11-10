"""Conservative raster-only frustum culling; ray geometry is never removed."""
import numpy as np


def visible_bounds(world_corners, view_projection):
    clip=np.asarray(world_corners) @ np.asarray(view_projection,dtype=np.float64).T
    w=clip[:,:,3]
    outside=np.zeros(len(clip),dtype=bool)
    for axis in range(3):
        outside |= np.all(clip[:,:,axis] < -w-1e-7,axis=1)
        outside |= np.all(clip[:,:,axis] > w+1e-7,axis=1)
    # Invalid transforms must not accidentally remove geometry.
    outside &= np.isfinite(clip).all(axis=(1,2))
    return ~outside


def _local_bounds(geometry):
    """Cache once per extracted mesh, shared by every transformed instance.

    Production extract_geometry replaces Geometry on mesh edits; it does not
    mutate positions in place. Replacement arrays and shadow_revision changes
    also invalidate this cache. Any future in-place position writer must bump
    that revision (or replace its array), and publish fresh scene containers
    or clear raster_bounds before drawing.
    """
    positions=geometry.positions
    revision=getattr(geometry,'shadow_revision',0)
    state=getattr(geometry,'_raster_local_bounds',None)
    if state is None or state[0] is not positions or state[1]!=revision:
        if len(positions):
            low=positions.min(axis=0);high=positions.max(axis=0)
            corners=np.array([(x,y,z,1) for x in (low[0],high[0])
                              for y in (low[1],high[1]) for z in (low[2],high[2])],dtype=np.float64)
        else:
            corners=np.zeros((8,4),dtype=np.float64)
        state=(positions,revision,corners)
        geometry._raster_local_bounds=state
    return state[2]


def raster_instances(cache, view_projection):
    state=getattr(cache,'raster_bounds',None)
    # SceneCache.update publishes fresh instances/list and geometry/dict objects
    # for every geometry/material/transform edit. Camera-only draws therefore
    # need no per-geometry validation or bounds work at all.
    if state is None or state[0] is not cache.instances or state[1] is not cache.geometry:
        local={key:_local_bounds(g) for key,g in cache.geometry.items()}
        corners=[local[key] @ np.asarray(matrix,dtype=np.float64).T
                 for key,matrix in cache.instances]
        state=(cache.instances,cache.geometry,np.asarray(corners).reshape(-1,8,4))
        cache.raster_bounds=state
    visible=visible_bounds(state[2],view_projection)
    cache.raster_visible_count=int(visible.sum())
    return (instance for instance,keep in zip(cache.instances,visible) if keep)
