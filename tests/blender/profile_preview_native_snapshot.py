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


    native=NativeView(owner='snapshot-profile')
    try:
        token=lambda revision: (('materials',0),('geometry',revision))
        ts=time.perf_counter();native.prepare(scene,resolver,token(0),0)
        initial_ms=(time.perf_counter()-ts)*1000
        old_vertices=native.vertices;old_metadata=native.metadata
        import hashlib
        def digest(vertices,metadata):
            h=hashlib.sha256();h.update(vertices);h.update(metadata);return h.hexdigest()
        old_hash=digest(old_vertices,old_metadata)
        eligible=[(i,key,m) for i,(key,m) in enumerate(scene.instances)
                  if not scene.geometry[key].use_uv and 0<len(scene.geometry[key].positions)<300]
        assert eligible,'No small brush found'
        index,key,matrix=eligible[0]
        moved=matrix.copy();moved.translation.x+=16/scene.unit_scale
        scene.instances=list(scene.instances);scene.instances[index]=(key,moved)
        ts=time.perf_counter();native.prepare(scene,resolver,token(1),0)
        incremental_ms=(time.perf_counter()-ts)*1000
        ts=time.perf_counter();reference_vertices,reference_metadata=previewnative.flatten_scene(scene,resolver)
        reference_ms=(time.perf_counter()-ts)*1000
        assert np.array_equal(native.vertices,reference_vertices),'Incremental vertex mismatch'
        assert np.array_equal(native.metadata,reference_metadata),'Incremental material/normal mismatch'
        assert digest(old_vertices,old_metadata)==old_hash,'Prior snapshot mutated'
        assert not np.shares_memory(old_vertices,native.vertices)
        return dict(ok=True,triangles=len(native.vertices),instances=len(scene.instances),
            moved_brush_vertices=len(scene.geometry[key].positions),
            initial_prepare_ms=initial_ms,one_brush_prepare_ms=incremental_ms,
            full_reference_ms=reference_ms,old_snapshot_unchanged=True,exact_reference_match=True,
            scope='CPU snapshot preparation only; excludes scene extraction, GPU upload and acceleration-structure rebuild.')
    finally:
        native.close();previewnative.shutdown()
try:result=run()
except Exception:result=dict(ok=False,traceback=traceback.format_exc())
OUT.mkdir(parents=True,exist_ok=True);(OUT/'native-snapshot-profile.json').write_text(json.dumps(result,indent=2));print(json.dumps(result),flush=True)
bpy.app.timers.register(lambda:bpy.ops.wm.quit_blender() and None,first_interval=.1)
