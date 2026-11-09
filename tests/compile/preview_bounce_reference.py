"""Bounded first-bounce comparison with hlvrad; factory GUI Blender, no game launch.

Compiles a closed diffuse room, bakes direct and one GI pass, and evaluates the
preview at floor luxel centers using the compiled faces and reflectivities.
This isolates transport from Blender-to-brush export differences.
"""
from pathlib import Path
import sys,json,subprocess,traceback
import bpy,gpu,numpy as np
from mathutils import Matrix
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from hammerless.core.ir import MapIR,Entity
from hammerless.core.geometry import box_brush
from hammerless.core.build import build_vmf
from hammerless.core.compile import find_game_root
from hammerless.core.lightbake import read_faces
from hammerless.core.lightmap import decode_luxels
from hammerless.core.bspdecompile import Bsp
from hammerless.core.previewlighting import PointLight
from hammerless.blender.l4d2preview import SceneCache,Geometry,diagnostic_shader,draw_geometry
from hammerless.blender.previewtrace import TraceCache
from hammerless.blender.previewmaterials import BounceMaterial
OUT=ROOT.parents[1]/'outputs/l4d2-preview/bounce-reference'
OUT.mkdir(parents=True,exist_ok=True)


def command(args,name):
    with (OUT/(name+'.log')).open('w') as log:
        subprocess.run(list(map(str,args)),check=True,stdout=log,stderr=subprocess.STDOUT,
                       timeout=120,creationflags=subprocess.CREATE_NO_WINDOW)


def run():
    root=Path(find_game_root());game=root/'left4dead2'
    ir=MapIR();ir.settings.name='preview_bounce_reference'
    ir.settings.auto_light_environment=False;ir.settings.ambient_brightness=0
    ir.settings.auto_detail='OFF'
    boxes=[((-128,-128,-16),(128,128,0)),((-128,-128,256),(128,128,272)),
           ((-144,-128,-16),(-128,128,272)),((128,-128,-16),(144,128,272)),
           ((-144,-144,-16),(144,-128,272)),((-144,128,-16),(144,144,272))]
    for low,high in boxes:
        brush=box_brush(low,high,'dev/dev_measuregeneric01b')
        for f in brush.faces:f.lightmap_scale=4
        ir.brushes.append(brush)
    keys={'_light':'255 180 90 200','_quadratic_attn':'1','_linear_attn':'0','_constant_attn':'0'}
    ir.entities.append(Entity('light',(0,0,128),keyvalues=keys))
    text,report=build_vmf(ir);assert report.ok,report.errors
    vmf=OUT/'room.vmf';vmf.write_text(text)
    command([root/'bin/vbsp.exe','-game',game,vmf],'vbsp')
    command([root/'bin/vvis.exe','-fast','-game',game,vmf.with_suffix('.bsp')],'vvis')
    base=vmf.with_suffix('.bsp').read_bytes()
    for bounce in (0,1):
        path=OUT/f'bounce{bounce}.bsp';path.write_bytes(base)
        args=[ROOT/'hammerless/core/hlvrad.exe','-gpu','-noextra','-bounce','0']
        if bounce:args+=['-gi','1','-girays','4096']
        command(args+['-game',game,path],f'bounce{bounce}')
        assert 'Lighting on the GPU:' in (OUT/f'bounce{bounce}.log').read_text()
    direct_data=(OUT/'bounce0.bsp').read_bytes();bounced_data=(OUT/'bounce1.bsp').read_bytes()
    faces,lump=read_faces(direct_data);bounced_faces,bounced_lump=read_faces(bounced_data)
    reference={f.index:f for f in bounced_faces}
    cache=SceneCache();cache.unit_scale=1
    light=PointLight.from_keys(keys);cache.lights=[((0,0,128),light.intensity,light.attenuation)]
    for f in faces:
        positions=f.positions.astype(np.float32).reshape(-1,3,3).copy()
        backwards=np.einsum('ij,j->i',np.cross(positions[:,1]-positions[:,0],positions[:,2]-positions[:,0]),f.normal)<0
        positions[backwards]=positions[backwards][:,::-1]
        g=Geometry(positions.reshape(-1,3),np.tile(f.normal,(len(f.positions),1)).astype(np.float32))
        g.materials=(BounceMaterial(f.reflectivity,True,True,'BSP'),)
        cache.geometry[f.index]=g;cache.instances.append((f.index,Matrix.Identity(4)))
    class Resolver:
        def resolve(self,mat):return mat
    cache.trace=TraceCache();cache.trace.update_bounce_materials(cache,Resolver())
    shader=diagnostic_shader(lit=True,shadows=True,bounce=True)
    off=gpu.types.GPUOffScreen(1,1,format='RGBA32F');rows=[]
    try:
        with off.bind():
            gpu.state.viewport_set(0,0,1,1);fb=gpu.state.active_framebuffer_get()
            for f in faces:
                if f.normal[2]<.99 or np.max(abs(f.positions[:,2]))>.01:continue
                bf=reference[f.index]
                def values(data,face,lump_id):
                    raw=Bsp(data).lump(lump_id)
                    return decode_luxels(raw[face.light_ofs:face.light_ofs+4*face.w*face.h]).reshape(face.h,face.w,3)
                direct=values(direct_data,f,lump);baked=values(bounced_data,bf,bounced_lump)
                for y in sorted(set([f.h//4,f.h//2,3*f.h//4])):
                    for x in sorted(set([f.w//4,f.w//2,3*f.w//4])):
                        p=f.to_world(np.array([x,y]))
                        matrix=Matrix(((1/128,0,0,-p[0]/128),(0,1/128,0,-p[1]/128),(0,0,-1/64,0),(0,0,0,1)))
                        samples=[]
                        for rays in (0,4096):
                            fb.clear(color=(0,0,0,1),depth=1)
                            draw_geometry(cache,shader,matrix,lit=True,shadows=True,bounce_samples=rays)
                            samples.append(np.array(fb.read_color(0,0,1,1,4,0,'FLOAT').to_list()[0][0][:3]))
                        rows.append({'position':p.tolist(),'preview_direct':samples[0].tolist(),
                            'bake_direct':direct[y,x].tolist(),'preview_indirect':(samples[1]-samples[0]).tolist(),
                            'bake_indirect':(baked[y,x]-direct[y,x]).tolist()})
    finally:off.free()
    assert rows,'No floor samples'
    a=np.array([r['preview_indirect'] for r in rows]);b=np.array([r['bake_indirect'] for r in rows])
    return {'samples':rows,'indirect_mean_absolute_error':float(abs(a-b).mean()),
            'indirect_max_absolute_error':float(abs(a-b).max()),
            'indirect_mean_relative_error':float((abs(a-b)/np.maximum(b,1e-5)).mean()),
            'scope':'One closed room; compiled geometry and reflectivity, 4096 rays, one bounce; descriptive comparison, no universal parity claim'}

try:result=run()
except Exception:result={'error':traceback.format_exc()}
(OUT/'results.json').write_text(json.dumps(result,indent=2));print(json.dumps(result),flush=True)
bpy.app.timers.register(lambda:bpy.ops.wm.quit_blender() and None,first_interval=.1)
