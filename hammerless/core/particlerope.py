"""Independent camera-facing ribbon approximation; no engine implementation used."""
import numpy as np


def geometry(sim,renderer,centers,right,up,eye,scale,texture_height):
    empty={k:np.zeros((0,n),dtype=np.float32) for k,n in [('pos',3),('uv',2),('tint',4)]}
    if len(centers)<2:return empty,set()
    # Birth order is topology. Camera depth sorting would scramble the rope.
    order=np.argsort(sim.ids,kind='stable')
    centers=centers[order]
    radius=np.maximum(sim.radius[order],0)/scale
    tint=sim.color[order].copy();tint[:,3]*=sim.alpha2[order]
    # Remove zero-length spans before interpolation: their nonzero neighboring
    # tangents otherwise manufacture a small loop beyond the endpoint.
    keep=np.r_[True,np.linalg.norm(np.diff(centers,axis=0),axis=1)>1e-9]
    centers=centers[keep];radius=radius[keep];tint=tint[keep]
    if len(centers)<2:return empty,set()
    divisions=max(1,min(16,int(renderer.get('subdivision_count',1))))
    gaps=set()
    if int(renderer.get('subdivision_count',1))>16:gaps.add('rope subdivision limit: 16')
    # Cubic Hermite interpolation, with endpoint tangents along the end segment.
    tangent=np.empty_like(centers)
    tangent[0]=centers[1]-centers[0];tangent[-1]=centers[-1]-centers[-2]
    if len(centers)>2:tangent[1:-1]=(centers[2:]-centers[:-2])*.5
    path=[];widths=[];colors=[]
    for i in range(len(centers)-1):
        for j in range(divisions):
            t=j/divisions
            path.append((2*t**3-3*t*t+1)*centers[i]+(t**3-2*t*t+t)*tangent[i]
                        +(-2*t**3+3*t*t)*centers[i+1]+(t**3-t*t)*tangent[i+1])
            widths.append(radius[i]*(1-t)+radius[i+1]*t)
            colors.append(tint[i]*(1-t)+tint[i+1]*t)
    path=np.array(path+[centers[-1]]);widths=np.array(widths+[radius[-1]])
    colors=np.array(colors+[tint[-1]])
    # Collapse coincident points before calculating normals or UV distance.
    keep=np.r_[True,np.linalg.norm(np.diff(path,axis=0),axis=1)>1e-9]
    path=path[keep];widths=widths[keep];colors=colors[keep]
    if len(path)<2:return empty,gaps
    tangent=np.empty_like(path);tangent[0]=path[1]-path[0];tangent[-1]=path[-1]-path[-2]
    if len(path)>2:tangent[1:-1]=path[2:]-path[:-2]
    sides=[]
    for i,direction in enumerate(tangent):
        if np.linalg.norm(direction)<1e-9:direction=path[min(i+1,len(path)-1)]-path[max(0,i-1)]
        side=np.cross(direction,eye-path[i])
        for fallback in (up,right,np.array([0.,0.,1.])):
            if np.linalg.norm(side)<1e-9:side=np.cross(direction,fallback)
        side=side/max(np.linalg.norm(side),1e-9)
        if sides and np.dot(side,sides[-1])<0:side=-side
        sides.append(side)
    sides=np.asarray(sides)*widths[:,None]
    left=path-sides;right_edge=path+sides
    distance=np.r_[0,np.cumsum(np.linalg.norm(np.diff(path,axis=0),axis=1))]*scale
    # Independent preview interpretation: texel_size is a density, not a
    # repeat length. Tiny authored values keep laser/melee ribbons uniform;
    # large values expose the water texture instead of stretching one row.
    # Use the original mapping height (never the downsampled GPU resolution).
    density=max(0,float(renderer.get('texel_size',1)))
    height=max(1,texture_height)
    offset=sim.time*float(renderer.get('texture_scroll_rate',0))/height
    v=distance*density/height+(offset%1.)
    positions=[];uv=[];tints=[]
    for i in range(len(path)-1):
        for index,edge in ((i,0),(i,1),(i+1,1),(i,0),(i+1,1),(i+1,0)):
            positions.append((left if edge==0 else right_edge)[index])
            uv.append((edge,v[index]));tints.append(colors[index])
    return {k:np.ascontiguousarray(a,dtype=np.float32) for k,a in
            [('pos',positions),('uv',uv),('tint',tints)]},gaps
