"""Brush overlay projection shared by viewport and VMF export.

Depth selects whole receiving faces; it does NOT clip the exported overlay in Z
(Source has no projection-depth key). Only the four footprint planes clip UVs.
"""
import json
import math
from . import geometry as g
from .ir import Entity
from .vmf import fmt_vec

MARKER = "_hl_overlay_depth"

def vector(kv, key):
    v = tuple(float(x) for x in kv[key].split())
    if len(v) != 3 or not all(math.isfinite(x) for x in v):
        raise ValueError("Overlay has invalid " + key)
    return v

def validate(kv):
    u, v, n = (vector(kv, k) for k in ("BasisU", "BasisV", "BasisNormal"))
    if any(abs(g.length(a)-1) > 1e-4 for a in (u,v,n)) or any(abs(g.dot(a,b)) > 1e-4 for a,b in ((u,v),(u,n),(v,n))):
        raise ValueError("Overlay axes must be orthonormal (apply shear first)")
    vector(kv, "BasisOrigin")
    w, h, depth = (float(kv[k]) for k in ("_hl_overlay_width", "_hl_overlay_height", MARKER))
    if not all(math.isfinite(x) and x > 0 for x in (w,h,depth)):
        raise ValueError("Overlay size and depth must be positive")
    if not kv.get("material", "").strip():
        raise ValueError("Overlay needs a material")
    return w,h,depth

def project(kv, verts, material="", source=""):
    """Return clipped world vertices and UVs, or empty when not a receiver."""
    if len(verts)<3 or material.lower().startswith("tools/"): return []
    allowed=json.loads(kv.get("_hl_overlay_receivers", "[]"))
    if allowed and not any(source==a or source.startswith(a+" (part ") for a in allowed): return []
    w,h,depth=validate(kv)
    origin=vector(kv,"BasisOrigin"); u=vector(kv,"BasisU"); v=vector(kv,"BasisV"); n=vector(kv,"BasisNormal")
    if g.dot(g.polygon_normal(verts), n)<0.05: return []
    # Local coordinates carry world positions through Sutherland-Hodgman clipping.
    points=[(p, (g.dot(g.sub(p,origin),u),g.dot(g.sub(p,origin),v),g.dot(g.sub(p,origin),n))) for p in verts]
    for axis,sign,limit in ((0,1,w/2),(0,-1,w/2),(1,1,h/2),(1,-1,h/2)):
        output=[]
        for i,b in enumerate(points):
            a=points[i-1]; da=sign*a[1][axis]-limit; db=sign*b[1][axis]-limit
            if (da<=0)!=(db<=0):
                t=da/(da-db)
                output.append((tuple(a[0][j]+t*(b[0][j]-a[0][j]) for j in range(3)),tuple(a[1][j]+t*(b[1][j]-a[1][j]) for j in range(3))))
            if db<=0: output.append(b)
        points=output
        if len(points)<3:return []
    # Depth chooses a receiving face, not a clipping plane (Source only stores
    # side IDs). Keep the whole footprint when any of it intersects the forward projection range;
    # Local +Z points away from the receiver: only cast along -Z from the
    # emitter, never upward through other floors. Keep crossing faces whole.
    # Requiring every corner inside made a slight tilt discard the whole decal.
    zs=[p[1][2] for p in points]
    if min(zs)>0.01 or max(zs)<-depth-0.01:return []
    area=sum(points[i-1][1][0]*p[1][1]-p[1][0]*points[i-1][1][1] for i,p in enumerate(points))
    if abs(area)<1e-6:return []
    su,eu,sv,ev=(float(kv.get(k,d)) for k,d in (("StartU",0),("EndU",1),("StartV",0),("EndV",1)))
    # uv0=(-w/2,-h/2), uv1=(-w/2,+h/2), uv2=(+w/2,+h/2), uv3=(+w/2,-h/2).
    return [(p, (su+(local[0]/w+.5)*(eu-su),sv+(local[1]/h+.5)*(ev-sv))) for p,local in points]

def stop_at_surface(kv, hits):
    """Filter (receiver ID, projected polygon) pairs using projected occlusion.

    Source selects whole sides, so a partly occluded side is conservatively
    excluded too. This prevents bleed without preview-only polygon trimming.
    Adjacent/copanar faces do not occlude each other. Local candidates only.
    """
    if kv.get("_hl_overlay_stop", "1") == "0" or len(hits)<2:return hits
    origin=vector(kv,"BasisOrigin")
    u,v,n=(vector(kv,k) for k in ("BasisU","BasisV","BasisNormal"))
    records=[]
    for token,points in hits:
        xyz=[(g.dot(g.sub(p,origin),u),g.dot(g.sub(p,origin),v),g.dot(g.sub(p,origin),n)) for p,uv in points]
        normal=g.polygon_normal(xyz)
        plane=(normal,g.dot(normal,xyz[0]))
        xy=[p[:2] for p in xyz]
        area=sum(xy[i-1][0]*p[1]-p[0]*xy[i-1][1] for i,p in enumerate(xy))
        if area<0:xy.reverse()
        records.append((xy,plane,(min(p[0] for p in xy),min(p[1] for p in xy),max(p[0] for p in xy),max(p[1] for p in xy))))
    visible=[]
    for i,(poly,plane,bounds) in enumerate(records):
        blocked=False
        for j,(clip,other,box) in enumerate(records):
            if i==j or bounds[0]>=box[2] or box[0]>=bounds[2] or bounds[1]>=box[3] or box[1]>=bounds[3]:continue
            overlap=poly
            for k,b in enumerate(clip):
                a=clip[k-1];dx=b[0]-a[0];dy=b[1]-a[1]
                def dist(p):return dx*(p[1]-a[1])-dy*(p[0]-a[0])
                out=[]
                for l,q in enumerate(overlap):
                    p=overlap[l-1];dp,dq=dist(p),dist(q)
                    if (dp>=0)!=(dq>=0):
                        t=dp/(dp-dq);out.append((p[0]+t*(q[0]-p[0]),p[1]+t*(q[1]-p[1])))
                    if dq>=0:out.append(q)
                overlap=out
                if len(overlap)<3:break
            area=abs(sum(overlap[k-1][0]*p[1]-p[0]*overlap[k-1][1] for k,p in enumerate(overlap)))
            if area<1e-6:continue
            def height(pl,p):
                norm,d=pl
                return (d-norm[0]*p[0]-norm[1]*p[1])/norm[2]
            # Larger local Z is closer to the emitter. Ignore coplanar seams
            # and portions behind the emitter (a tilted footprint can cross it).
            if any(height(other,p)<=.01 and height(other,p)>height(plane,p)+.01 for p in overlap):
                blocked=True;break
        if not blocked:visible.append(hits[i])
    return visible


def resolve(world, entities, writer, report):
    """Resolve generated overlays against final VMF side IDs, never saved polygon indices."""
    from .vmfimport import solid_faces
    generated=[e for e in entities if e.get("classname")=="info_overlay" and e.get(MARKER) is not None]
    if not generated:return
    receivers=[]
    for owner in [world]+[e for e in entities if e.get("classname")=="func_detail"]:
        for solid in owner.blocks("solid"):
            if any(s.blocks("dispinfo") for s in solid.blocks("side")):continue
            source=writer.solid_sources.get(int(solid.get("id",0)), "")
            for side,verts in solid_faces(solid):
                receivers.append((side,verts,source))
    for overlay in generated:
        kv=dict(x for x in overlay.items if isinstance(x,tuple))
        validate(kv)
        hits=[]
        for side,verts,source in receivers:
            clipped=project(kv,verts,side.get("material",""),source)
            if clipped:hits.append((side.get("id"),clipped))
        ids=[token for token,points in stop_at_surface(kv,hits)]
        if not ids: raise ValueError("Overlay at "+kv.get("BasisOrigin","")+": no receiving brush faces; move it onto a brush or increase Receiver Depth")
        if len(ids)>=64:raise ValueError("Overlay touches too many brush sides; reduce its size or receiver list")
        overlay.items=[x for x in overlay.items if not (isinstance(x,tuple) and (x[0].startswith("_hl_overlay_") or x[0]=="sides"))]
        overlay.kv("sides"," ".join(ids))
        report.info.append("Overlay: "+str(len(ids))+" receiving brush side(s). Compiled face splits may increase this count.")
