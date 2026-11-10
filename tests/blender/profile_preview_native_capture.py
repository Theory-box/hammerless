"""Bounded actual-map native/software parity. One Blender, one owned native worker.
No saving the input file. Run interactively with --factory-startup --python this.py -- MAP.blend.
"""
from pathlib import Path
import sys,json,time,traceback,os
import bpy,gpu,numpy as np
from mathutils import Vector
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from hammerless.blender import props,l4d2preview as preview,previewnative
from hammerless.blender.previewnativeview import NativeView
from hammerless.blender.previewmaterials import BounceMaterials,SurfaceMaterials
from hammerless.blender.previewtrace import TraceCache
from hammerless.core.compile import find_game_root
from hammerless.blender.ops import game_content
OUT=ROOT.parents[1]/'outputs/l4d2-preview'

def run():
    props.register();started=time.perf_counter()
    path=Path(sys.argv[sys.argv.index('--')+1]).resolve()
    bpy.ops.wm.open_mainfile(filepath=str(path),load_ui=False,use_scripts=False)
    sc=bpy.context.scene;scale=sc.hammerless.units_per_meter
    spawns=[o for o in sc.objects if o.hammerless.classname in ('info_player_start','info_survivor_position')]
    assert spawns,'Map requires a player spawn for reproducible views'
    camera=bpy.data.objects.new('Native parity camera',bpy.data.cameras.new('Native parity camera'))
    sc.collection.objects.link(camera);camera.location=spawns[0].matrix_world.translation+Vector((0,0,64/scale));camera.data.clip_end=10000
    bpy.context.view_layer.update();deps=bpy.context.evaluated_depsgraph_get()
    scene=preview.SceneCache();scene.update(deps);scene.update_lights(deps)
    root=find_game_root();assert root
    content=game_content(root);game=os.path.join(root,'left4dead2')
    resolver=BounceMaterials(content,game,sc.hammerless.default_material)
    surface=SurfaceMaterials(content,game,sc.hammerless.default_material)
    for g in scene.geometry.values():
        for mat in g.materials or (None,):surface.resolve(mat);resolver.resolve(mat)
    scene.surface_materials=surface
    scene_setup_ms=(time.perf_counter()-started)*1000

    import cProfile,pstats,io
    width,height=320,180
    off=gpu.types.GPUOffScreen(width,height,format='RGBA32F')
    native=NativeView(owner='capture-profile')
    rows=[]
    try:
        for index,direction in enumerate((Vector((1,0,0)),Vector((0,1,0)),Vector((1,0,0)),Vector((0,1,0)),Vector((1,0,0)))):
            camera.rotation_euler=direction.to_track_quat('-Z','Y').to_euler()
            bpy.context.view_layer.update()
            matrix=camera.calc_matrix_camera(deps,x=width,y=height)@camera.matrix_world.inverted()
            if index==4:
                # Mirror SceneCache's transform-update container replacement.
                # Geometry itself is unchanged, so local bounds should survive.
                scene.instances=list(scene.instances)
                scene.geometry=dict(scene.geometry)
            profile=cProfile.Profile()
            ts=time.perf_counter();profile.enable()
            with off.bind():
                gpu.state.viewport_set(0,0,width,height)
                native._capture(scene,matrix,(width,height))
            elapsed_ms=(time.perf_counter()-ts)*1000
            profile.disable()
            stream=io.StringIO()
            pstats.Stats(profile,stream=stream).sort_stats('cumtime').print_stats(25)
            rows.append(dict(view=index,total_ms=elapsed_ms,
                covered=len(native.covered),profile=stream.getvalue()))
            print(stream.getvalue(),flush=True)
        ts=time.perf_counter()
        for revision in range(20):native.stage(scene,resolver,revision,revision)
        stage_ms=(time.perf_counter()-ts)*1000
        assert native.vertices is None,'Staging performed geometry preparation'
        ts=time.perf_counter();native._flush_pending();flush_ms=(time.perf_counter()-ts)*1000
        assert native.geometry_token==19 and native.lights_token==19
        return dict(ok=True,scene_setup_ms=scene_setup_ms,screen=[width,height],rows=rows,
                    twenty_stage_calls_ms=stage_ms,one_idle_flush_ms=flush_ms,
                    native_triangles=len(native.vertices))
    finally:
        native.close();previewnative.shutdown();off.free()
try:result=run()
except Exception:result=dict(ok=False,traceback=traceback.format_exc())
OUT.mkdir(parents=True,exist_ok=True);(OUT/'native-capture-profile.json').write_text(json.dumps(result,indent=2));print(json.dumps(result),flush=True)
bpy.app.timers.register(lambda:bpy.ops.wm.quit_blender() and None,first_interval=.1)
