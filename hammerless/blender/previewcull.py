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


def raster_instances(cache, view_projection):
    state=getattr(cache,'raster_bounds',None)
    if state is None or state[0] is not cache.instances or state[1] is not cache.geometry:
        corners=[]
        for key,matrix in cache.instances:
            g=cache.geometry[key]
            if len(g.positions):
                low=g.positions.min(axis=0);high=g.positions.max(axis=0)
                local=np.array([(x,y,z,1) for x in (low[0],high[0])
                                for y in (low[1],high[1]) for z in (low[2],high[2])])
            else:local=np.zeros((8,4))
            corners.append(local @ np.asarray(matrix,dtype=np.float64).T)
        state=(cache.instances,cache.geometry,np.asarray(corners).reshape(-1,8,4))
        cache.raster_bounds=state
    visible=visible_bounds(state[2],view_projection)
    cache.raster_visible_count=int(visible.sum())
    return (instance for instance,keep in zip(cache.instances,visible) if keep)
