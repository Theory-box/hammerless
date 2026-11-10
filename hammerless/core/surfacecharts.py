"""Planar surface cache charts derived from the actual VMF side settings.

These are pre-BSP charts: compiler splitting and final lightmap packing remain
separate. No custom preview density is introduced.
"""
from dataclasses import dataclass
import numpy as np
from .previewgrid import lightmap_vectors
from .vmfimport import parse_axis

def chart_triangle_mask(chart,triangles,normals,tolerance=.005):
    """Conservative whole-triangle containment in Source units, not screen space.

    Caller supplies inverse-transpose transformed normals, independent of winding.
    Geometry crossing a chart boundary remains on the per-pixel path.
    """
    triangles=np.asarray(triangles,dtype=np.float64).reshape(-1,3,3)
    normals=np.asarray(normals,dtype=np.float64).reshape(-1,3)
    origin=chart.polygon[0]
    local=triangles-origin
    length=np.linalg.norm(normals,axis=1)
    facing=np.divide(normals@chart.normal,length,out=np.zeros_like(length),where=length>1e-12)
    keep=(facing>.9999)&np.all(abs(local@chart.normal)<=tolerance,axis=1)
    edges=np.roll(chart.polygon,-1,axis=0)-chart.polygon
    lengths=np.linalg.norm(edges,axis=1)
    if np.any(lengths<=1e-12):return np.zeros(len(triangles),dtype=bool)
    # (edge x displacement) . normal == displacement . (normal x edge).
    # Compute the small edge normals once instead of crossing every vertex.
    inward=np.cross(chart.normal,edges/lengths[:,None])
    for a,n in zip(chart.polygon-origin,inward):
        keep &= np.all((local-a)@n>=-tolerance,axis=1)
    keep &= np.isfinite(triangles).all(axis=(1,2))&np.isfinite(normals).all(axis=1)
    return keep

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
    material_path: str
    texture_vectors: np.ndarray

    def texture_uv(self, size):
        """Exported pixel axes to Blender image UVs (Source V points down)."""
        size=np.asarray(size,dtype=float)
        if size.shape!=(2,) or not np.isfinite(size).all() or np.any(size<=0):
            raise ValueError('Texture dimensions must be positive and finite')
        uv=(self.positions@self.texture_vectors[:,:3].T+self.texture_vectors[:,3])/size
        uv[:,1]=1-uv[:,1]
        return uv

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
        edges=np.roll(self.polygon,-1,axis=0)-self.polygon
        inward=np.cross(self.normal,edges)
        for a,edge,n in zip(self.polygon,edges,inward):
            inside &= (points-a)@n>=-1e-6
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
    local=p-p[0]
    normal=np.sum(np.cross(local,np.roll(local,-1,axis=0)),axis=0)
    length=np.linalg.norm(normal)
    if length<1e-9:raise ValueError('Degenerate surface polygon')
    normal/=length
    if np.max(abs((p-p[0])@normal))>1e-4:raise ValueError('Surface chart must be planar')
    edges=np.roll(p,-1,axis=0)-p
    inward=np.cross(normal,edges)
    for a,n in zip(local,inward):
        if np.min((local-a)@n)<-1e-5:
            raise ValueError('Surface chart requires a convex exported face')
    scale=float(side.get('lightmapscale') or 16)
    axes=[parse_axis(side.get(k)) for k in ('uaxis','vaxis')]
    rows=lightmap_vectors(axes,scale)
    texture_vectors=np.array([(* (np.asarray(axis)/step),shift) for axis,shift,step in axes])
    uv=p@rows[:,:3].T+rows[:,3]
    mins=np.floor(uv.min(axis=0)+1e-6).astype(int)
    sizes=np.ceil(uv.max(axis=0)-1e-6).astype(int)-mins+1
    if np.any(sizes<1) or int(sizes[0])*int(sizes[1])>max_samples:
        raise ValueError('Surface chart exceeds sample budget')
    matrix=np.vstack((rows[:,:3],normal))
    inverse=np.linalg.inv(matrix)
    origin=inverse@np.r_[mins-rows[:,3],np.dot(p[0],normal)]
    triangles=np.array([v for i in range(1,len(p)-1) for v in (p[0],p[i],p[i+1])],np.float32)
    return SurfaceChart(index,triangles,normal,rows,mins,int(sizes[0]),int(sizes[1]),origin,inverse[:,:2],p,scale,
                        side.get('material','').lower().replace('\\','/'),texture_vectors)
