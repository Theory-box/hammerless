"""Render the measured game camera; report differences without claiming parity."""
from pathlib import Path
import sys,json,math,traceback,hashlib
import bpy,gpu,numpy as np
from mathutils import Matrix,Vector
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from hammerless.blender import props
from hammerless.blender.l4d2preview import SceneCache,Geometry,diagnostic_shader,draw_geometry
from hammerless.blender.previewmaterials import SurfaceMaterials
from hammerless.core.previewlighting import PointLight
from hammerless.core.compile import find_game_root
from hammerless.core.vpk import GameContent
OUT=Path(sys.argv[sys.argv.index('--')+1]).resolve() if '--' in sys.argv else ROOT.parents[1]/'outputs/l4d2-preview/game-reference-clean'

def run():
    props.register()
    captured=json.loads((OUT/'capture.json').read_text())
    assert hashlib.sha256(Path(captured['source']).read_bytes()).hexdigest()==captured['sha256'],'Reference BSP changed since capture'
    scene=bpy.context.scene
    scene.view_settings.view_transform='Standard';scene.view_settings.look='None'
    scene.view_settings.exposure=0;scene.view_settings.gamma=1
    mat=bpy.data.materials.new('Reference floor');mat.hammerless.source_material='dev/dev_measuregeneric01b'
    from hammerless.core.lightbake import read_faces
    faces,_=read_faces(Path(captured['source']).read_bytes())
    cache=SceneCache();cache.unit_scale=1
    for face in faces:
        triangles=face.positions.astype(np.float32).reshape(-1,3,3).copy()
        reverse=np.einsum('ij,j->i',np.cross(triangles[:,1]-triangles[:,0],triangles[:,2]-triangles[:,0]),face.normal)<0
        triangles[reverse]=triangles[reverse][:,::-1]
        pos=triangles.reshape(-1,3);normal=np.tile(face.normal,(len(pos),1)).astype(np.float32)
        g=Geometry(pos,normal);g.face_normals=normal;g.uvs=np.zeros((len(pos),2),np.float32)
        g.painted=np.zeros(len(pos),np.float32);g.material_ids=np.zeros(len(triangles),np.int32);g.materials=(mat,)
        cache.geometry[face.index]=g;cache.instances.append((face.index,Matrix.Identity(4)))
    root=find_game_root();cache.surface_materials=SurfaceMaterials(GameContent(root),str(Path(root)/'left4dead2'),'dev/dev_measuregeneric01b')
    cache.surface_materials.resolve(mat)
    assert not cache.surface_materials.warnings,cache.surface_materials.warnings
    light=PointLight.from_keys({'_light':'255 180 90 200','_quadratic_attn':'1','_linear_attn':'0','_constant_attn':'0'})
    from hammerless.core.bspdecompile import Bsp
    from hammerless.core.vmf import parse
    entity=next(e for e in parse(Path(captured['source']).with_suffix('.vmf').read_text()) if e.get('classname')=='light')
    origin=tuple(float(v) for v in entity.get('origin').split())
    cache.lights=[(origin,light.intensity,light.attenuation)]
    cam=bpy.data.objects.new('Measured camera',bpy.data.cameras.new('Measured camera'))
    scene.collection.objects.link(cam);cam.location=captured['camera']['eye']
    pitch,yaw,_=map(math.radians,captured['camera']['angles'])
    direction=Vector((math.cos(pitch)*math.cos(yaw),math.cos(pitch)*math.sin(yaw),-math.sin(pitch)))
    cam.rotation_euler=direction.to_track_quat('-Z','Y').to_euler()
    cam.data.sensor_fit='HORIZONTAL';cam.data.angle=math.pi/2;cam.data.clip_start=.1;cam.data.clip_end=2000
    bpy.context.view_layer.update()
    projection=cam.calc_matrix_camera(bpy.context.evaluated_depsgraph_get(),x=640,y=480)@cam.matrix_world.inverted()
    shader=diagnostic_shader(lit=True,textured=True,shadows=True)
    off=gpu.types.GPUOffScreen(640,480,format='RGBA32F');report={}
    try:
        for unlit in (False,True):
            cache.surface_materials.unlit[mat.as_pointer()]=unlit
            with off.bind():
                gpu.state.viewport_set(0,0,640,480)
                fb=gpu.state.active_framebuffer_get();fb.clear(color=(0,0,0,1),depth=1)
                draw_geometry(cache,shader,projection,lit=True,textured=True,shadows=True)
                pixels=np.asarray(fb.read_color(0,0,640,480,4,0,'FLOAT').to_list(),np.float32)
            im=bpy.data.images.new('Preview reference',640,480,float_buffer=True)
            im.pixels.foreach_set(pixels.ravel());im.save_render(str(OUT/f'preview-fullbright{int(unlit)}.png'),scene=scene)
            encoded=np.where(pixels[:,:,:3]<=.0031308,pixels[:,:,:3]*12.92,1.055*np.maximum(pixels[:,:,:3],0)**(1/2.4)-.055)
            reference=bpy.data.images.load(str(next(OUT.glob(f'hlref_*_fullbright{int(unlit)}.jpg'))))
            ref=np.asarray(reference.pixels[:],np.float32).reshape(480,640,4)[:,:,:3]
            report[str(unlit)]={'preview_centre':np.median(encoded[232:248,312:328],axis=(0,1)).tolist(),
                'game_centre':np.median(ref[232:248,312:328],axis=(0,1)).tolist(),
                'mean_abs_display_difference':float(np.mean(abs(encoded-ref)))}
            if not unlit and 'shadow' in OUT.name:
                # Fixed central region excludes dark vignette corners. Threshold
                # measures the dark core, not the different edge-filter profiles.
                a=encoded[120:360,200:440,0]<.02
                b=ref[120:360,200:440,0]<.02
                union=np.count_nonzero(a|b)
                report[str(unlit)]['shadow_core']={'red_threshold':.02,'roi_xywh':[200,120,240,240],
                    'preview_pixels':int(a.sum()),'game_pixels':int(b.sum()),
                    'intersection_over_union':float(np.count_nonzero(a&b)/union) if union else None}

    finally:off.free()
    return {'rendered':True,'measurements':report,'scope':'Single direct-lit floor. False compares equivalent direct-lit surfaces. True compares DIFFERENT paths: preview UnlitGeneric-style output versus game LightmappedGeneric under mat_fullbright 1; its error is diagnostic, not a parity failure. JPEG game reference includes game postprocessing. No fitted correction applied.'}

try:result=run()
except Exception:result={'error':traceback.format_exc()}
(OUT/'preview-comparison.json').write_text(json.dumps(result,indent=2));print(json.dumps(result),flush=True)
bpy.app.timers.register(lambda:bpy.ops.wm.quit_blender() and None,first_interval=.1)
