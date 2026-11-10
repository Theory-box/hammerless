"""Test-only per-instance triangle radiance cache; no addon integration.
All sampled donors remain in the full trace scene. Missing entries fall back to
original direct lighting. Square storage uses triangular lattice interpolation.
"""
import time,math
from dataclasses import dataclass
import numpy as np
import gpu
from mathutils import Matrix
from hammerless.blender import previewbounce
from hammerless.blender.l4d2preview import diagnostic_shader,draw_geometry
from hammerless.blender.previewtrace import build_bvh,texture
from hammerless.blender.previewlightcache import SurfaceLightCache


def mesh_rows(scene,resolver):
    rows=[];meshes={};offset=0
    for key,g in scene.geometry.items():
        order=scene.trace.meshes[key][4]
        originals=order if g.shadow_triangle_ids is None else g.shadow_triangle_ids[order]
        mids=g.material_ids if g.material_ids is not None else np.zeros(len(g.positions)//3,dtype=int)
        data=np.zeros((len(order),4),np.float32)
        if not g.use_uv:
            for mi in np.unique(mids[originals]):
                mat=g.materials[mi] if 0<=mi<len(g.materials) else None
                surface=resolver.resolve(mat);mask=mids[originals]==mi
                data[mask,:3]=surface.reflectivity;data[mask,3]=float(surface.receives_light and surface.verified)
        # Nonreceiving prop meshes still retain their reflectivity rows and
        # trace geometry, but need no donor positions or per-instance grids.
        tri=((g.positions if g.shadow_positions is None else g.shadow_positions).reshape(-1,3,3)[order]
             if np.any(data[:,3]>=.5) else None)
        meshes[key]=(offset,tri,data);offset+=len(order);rows.append(data)
    return np.concatenate(rows) if rows else np.zeros((0,4),np.float32),meshes


def ordered_instances(scene):
    entries=[];instances=[]
    for key,matrix in scene.instances:
        token=(key,tuple(v for row in matrix for v in row))
        entry=scene.trace.instance_entries.get(token)
        if entry is not None:entries.append(entry);instances.append((key,matrix))
    if not entries:return []
    entries=np.asarray(entries);boxes=np.stack((entries[:,4,:3],entries[:,5,:3],entries[:,4,:3]),axis=1)
    _,_,order=build_bvh(boxes,return_order=True)
    assert len(order)==scene.trace.instance_count
    return [instances[i] for i in order]


@dataclass
class Prepared:
    donor: object
    packed: object
    stats: dict
    def install(self,scene):scene.trace.surfaces=texture(self.packed)


def prepare(scene,resolver,spacing=.25,max_samples=500000,reuse=None,max_side=129,donor_batch_size=2048):
    if not np.isfinite(spacing) or spacing<=0 or max_samples<0:raise ValueError('Invalid cache budget or spacing')
    if donor_batch_size<1 or int(donor_batch_size)!=donor_batch_size:raise ValueError('Invalid donor batch size')
    donor_batch_size=int(donor_batch_size)
    started=time.perf_counter();scene.trace.update(scene)
    base,meshes=mesh_rows(scene,resolver);instances=ordered_instances(scene)
    headers=np.zeros((len(instances),4),np.float32);descs=[];positions=[];normals=[]
    sample_count=0;eligible=0;cached=0;capped=0;skipped=0
    for ii,(key,matrix) in enumerate(instances):
        tri_start,tris,materials=meshes[key]
        if tris is None:
            # The path integrator rejects these before calling cachedHitLight;
            # count=0 also makes any unexpected query use exact direct fallback.
            headers[ii,:3]=(0,tri_start,0)
            continue
        headers[ii,:3]=(len(base)+len(instances)+len(descs),tri_start,len(tris))
        m=np.asarray(matrix,np.float64);normal_matrix=np.linalg.inv(m[:3,:3]).T
        for t,material in zip(tris,materials):
            desc=np.zeros(4,np.float32);descs.append(desc)
            if material[3]<.5:continue
            eligible+=1
            world=np.c_[t,np.ones(3)]@m.T;world=world[:,:3]
            n=normal_matrix@np.cross(t[1]-t[0],t[2]-t[0]);length=np.linalg.norm(n)
            if length<1e-12:skipped+=1;continue
            n/=length
            edge=max(np.linalg.norm(world[1]-world[0]),np.linalg.norm(world[2]-world[0]),np.linalg.norm(world[2]-world[1]))
            wanted=max(2,int(math.ceil(edge/spacing))+1);side=min(max_side,wanted);capped+=int(side<wanted)
            count=side*side
            if sample_count+count>max_samples:skipped+=1;continue
            uv=np.linspace(0,1,side,dtype=np.float32);u,v=np.meshgrid(uv,uv)
            u=u.ravel();v=v.ravel();fold=u+v>1;u[fold]=1-u[fold];v[fold]=1-v[fold]
            pos=world[0]+u[:,None]*(world[1]-world[0])+v[:,None]*(world[2]-world[0])
            positions.append(pos.astype(np.float32));normals.append(np.tile(n,(count,1)).astype(np.float32))
            desc[:2]=(sample_count,side);sample_count+=count;cached+=1
    descs=np.asarray(descs,np.float32).reshape(-1,4)
    prefix=len(base)+len(headers)+len(descs)
    if prefix+sample_count>=2**24:raise ValueError('Packed float indices exceed exact integer range')
    valid=descs[:,1]>0;descs[valid,0]+=prefix
    geometry_done=time.perf_counter();donor=None
    stages={}
    if sample_count:
        donor=SurfaceLightCache(np.concatenate(positions),np.concatenate(normals),reuse=reuse.donor if reuse else None)
        constructed=time.perf_counter();viewport=gpu.state.viewport_get()
        try:
            with donor.sample_fb.bind():
                gpu.state.viewport_set(0,0,*donor.size);donor.sample_fb.clear(color=(0,0,0,0),depth=1)
                # Keep every submission bounded. The first large-map trial
                # terminated abnormally, so do not submit all donors at once.
                # Every batch uses the original sampleClip attributes and the
                # framebuffer is cleared only once, preserving previous batches.
                batch_count=0
                for first in range(0,sample_count,donor_batch_size):
                    last=min(sample_count,first+donor_batch_size)
                    if first==0 and last==sample_count:batch=donor.batch
                    else:
                        index=gpu.types.GPUIndexBuf(type='POINTS',seq=np.arange(first,last,dtype=np.int32))
                        batch=gpu.types.GPUBatch(type='POINTS',buf=donor.vbo,elem=index)
                    draw_geometry(scene,donor.shader,Matrix.Identity(4),lit=True,shadows=True,bounce_samples=0,
                                  lighting_samples=batch,primary_sky_index=-1,primary_area_phase=-1)
                    # Readback fences completion even if this pixel was written
                    # by an earlier batch; it prevents an unbounded GPU queue.
                    donor.sample_fb.read_color(0,0,1,1,4,0,'FLOAT')
                    batch_count+=1
                submitted=time.perf_counter()
                measured=np.asarray(donor.sample_fb.read_color(0,0,*donor.size,4,0,'FLOAT').to_list(),np.float32).reshape(-1,4)[:sample_count].copy()
        finally:gpu.state.viewport_set(*viewport)
        measured[:,:3]*=255
        assert np.isfinite(measured).all()
        stages=dict(constructor_ms=(constructed-geometry_done)*1000,direct_batches_with_sync_ms=(submitted-constructed)*1000,donor_batches=batch_count,
                    readback_gpu_completion_ms=(time.perf_counter()-submitted)*1000)
    else:measured=np.zeros((0,4),np.float32)
    packed=np.concatenate((base,headers,descs,measured));done=time.perf_counter()
    return Prepared(donor,packed,dict(samples=sample_count,eligible_triangles=eligible,cached_triangles=cached,
        fallback_triangles=skipped,capped_triangles=capped,instance_count=len(instances),global_triangles=len(base),
        geometry_prepare_ms=(geometry_done-started)*1000,prepare_cpu_observed_ms=(done-started)*1000,
        stages=stages,spacing=spacing,max_side=max_side,coverage_fraction=cached/max(1,eligible)))


def compile_reuse_shader(scene,textured=False):
    count=sum(len(scene.trace.meshes[key][4]) for key in scene.geometry)
    helper=r'''
vec3 cachedHitLight(vec3 hit,vec3 hn,int tri,int inst,vec2 bary,int skyIndex,int areaPhase) {
    vec4 header=traceFetch(traceSurfaces,HEADER+inst);
    int local=tri-int(header.y);
    if(local<0 || local>=int(header.z))return previewDirect(hit+hn*(1.0/unitScale),hn,skyIndex,1,areaPhase);
    vec4 meta=traceFetch(traceSurfaces,int(header.x)+local);
    int side=int(meta.y),start=int(meta.x);
    if(side<2 || bounceSkySampled!=0 || bounceAreaSampled!=0)
        return previewDirect(hit+hn*(1.0/unitScale),hn,skyIndex,1,areaPhase);
    vec2 bc=max(bary,vec2(0));bc/=max(1.0,bc.x+bc.y);
    vec2 q=bc*float(side-1);ivec2 p=ivec2(floor(q));vec2 f=fract(q);
    ivec2 px=min(p+ivec2(1,0),ivec2(side-1));
    ivec2 py=min(p+ivec2(0,1),ivec2(side-1));
    vec3 a=traceFetch(traceSurfaces,start+p.y*side+p.x).rgb;
    vec3 b=traceFetch(traceSurfaces,start+px.y*side+px.x).rgb;
    vec3 c=traceFetch(traceSurfaces,start+py.y*side+py.x).rgb;
    if(f.x+f.y<=1.0)return a*(1.0-f.x-f.y)+b*f.x+c*f.y;
    ivec2 pd=min(p+ivec2(1),ivec2(side-1));
    vec3 d=traceFetch(traceSurfaces,start+pd.y*side+pd.x).rgb;
    return b*(1.0-f.y)+c*(1.0-f.x)+d*(f.x+f.y-1.0);
}
'''.replace('HEADER',str(count))
    original=previewbounce.GLSL
    old='previewDirect(hit+hn*(1.0/unitScale),hn,skyIndex,1,areaPhase)'
    assert old in original
    try:
        previewbounce.GLSL=helper+original.replace(old,'cachedHitLight(hit,hn,tri,inst,bary,skyIndex,areaPhase)')
        return diagnostic_shader(lit=True,textured=textured,shadows=True,bounce=True)
    finally:previewbounce.GLSL=original
