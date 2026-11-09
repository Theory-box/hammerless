"""Planar surface cache charts derived from the actual VMF side settings.

These are pre-BSP charts: compiler splitting and final lightmap packing remain
separate. No custom preview density is introduced.
"""
from dataclasses import dataclass
import numpy as np
from .previewgrid import lightmap_vectors
from .vmfimport import parse_axis

@dataclass
class SurfaceChart:
    index: int
    positions: np.ndarray
    normal: np.ndarray
    lvecs: np.ndarray
    mins: np.ndarray
    w: int
    h: int
    origin: np.ndarray
    basis: np.ndarray
    polygon: np.ndarray
    lightmap_scale: float

    def to_luxel(self,points):
        return np.asarray(points)@self.lvecs[:,:3].T+self.lvecs[:,3]-self.mins

    def to_world(self,coords):
        return self.origin+np.asarray(coords)@self.basis.T

    def samples(self):
        y,x=np.mgrid[:self.h,:self.w]
        points=self.to_world(np.column_stack((x.ravel(),y.ravel())))
        # Extend edge lighting to the rectangular storage margin, but never
        # trace from unrelated empty space outside the convex source polygon.
        inside=np.ones(len(points),dtype=bool)
        closest=points.copy();best=np.full(len(points),np.inf)
        for a,b in zip(self.polygon,np.roll(self.polygon,-1,axis=0)):
            edge=b-a
            inside &= np.cross(edge,points-a)@self.normal>=-1e-6
            length=np.dot(edge,edge)
            t=np.clip((points-a)@edge/length,0,1) if length>1e-12 else np.zeros(len(points))
            q=a+t[:,None]*edge
            distance=np.sum((points-q)**2,axis=1)
            take=distance<best;closest[take]=q[take];best[take]=distance[take]
        points[~inside]=closest[~inside]
        return points


def chart_from_side(side,vertices,index=0,max_samples=1048576):
    p=np.asarray(vertices,dtype=np.float64)
    if len(p)<3 or not np.isfinite(p).all():raise ValueError('Invalid surface polygon')
    normal=np.sum(np.cross(p,np.roll(p,-1,axis=0)),axis=0)
    length=np.linalg.norm(normal)
    if length<1e-9:raise ValueError('Degenerate surface polygon')
    normal/=length
    if np.max(abs((p-p[0])@normal))>1e-4:raise ValueError('Surface chart must be planar')
    for a,b in zip(p,np.roll(p,-1,axis=0)):
        if np.min(np.cross(b-a,p-a)@normal)<-1e-5:
            raise ValueError('Surface chart requires a convex exported face')
    scale=float(side.get('lightmapscale') or 16)
    rows=lightmap_vectors([parse_axis(side.get(k)) for k in ('uaxis','vaxis')],scale)
    uv=p@rows[:,:3].T+rows[:,3]
    mins=np.floor(uv.min(axis=0)+1e-6).astype(int)
    sizes=np.ceil(uv.max(axis=0)-1e-6).astype(int)-mins+1
    if np.any(sizes<1) or int(sizes[0])*int(sizes[1])>max_samples:
        raise ValueError('Surface chart exceeds sample budget')
    matrix=np.vstack((rows[:,:3],normal))
    inverse=np.linalg.inv(matrix)
    origin=inverse@np.r_[mins-rows[:,3],np.dot(p[0],normal)]
    triangles=np.array([v for i in range(1,len(p)-1) for v in (p[0],p[i],p[i+1])],np.float32)
    return SurfaceChart(index,triangles,normal,rows,mins,int(sizes[0]),int(sizes[1]),origin,inverse[:,:2],p,scale)
