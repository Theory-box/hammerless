"""Read-only saved-map extraction and BLAS build benchmark. Never saves the blend."""
from pathlib import Path
import sys,time,json,traceback,collections
import bpy
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from hammerless.blender import props,l4d2preview as preview
from hammerless.blender.previewtrace import build_bvh
OUT=ROOT.parents[1]/'outputs/l4d2-preview'


def run():
    path=Path(sys.argv[sys.argv.index('--')+1]).resolve()
    props.register()
    start=time.perf_counter()
    bpy.ops.wm.open_mainfile(filepath=str(path),load_ui=False,use_scripts=False)
    loaded=time.perf_counter()
    deps=bpy.context.evaluated_depsgraph_get()
    cache=preview.SceneCache()
    cache.update(deps)
    extracted=time.perf_counter()
    cache.update_lights(deps)
    lights_done=time.perf_counter()
    nodes=0
    for geom in cache.geometry.values():
        tree,_=build_bvh(geom.positions)
        nodes+=len(tree)
    acceleration_done=time.perf_counter()
    edits=[]
    def capture(scene, graph):
        before=cache.extractions
        begin=time.perf_counter();cache.update(graph);middle=time.perf_counter()
        cache.update_lights(graph);end=time.perf_counter()
        edits.append({'geometry_ms':(middle-begin)*1000,'lights_ms':(end-middle)*1000,
                      'extractions':cache.extractions-before})
    before=cache.extractions;begin=time.perf_counter()
    cache.update(deps);cache.update_lights(deps)
    unchanged={'ms':(time.perf_counter()-begin)*1000,'extractions':cache.extractions-before}
    target=next(o for o in bpy.context.scene.objects if o.as_pointer() in cache.geometry)
    bpy.app.handlers.depsgraph_update_post.append(capture)
    try:
        target.location.x+=.01;bpy.context.view_layer.update()
        transform=list(edits);edits.clear()
        target.data.vertices[0].co.x+=.001;target.data.update();bpy.context.view_layer.update()
        geometry=list(edits)
    finally:
        bpy.app.handlers.depsgraph_update_post.remove(capture)
    assert transform and all(r['extractions']==0 for r in transform),transform
    assert geometry and sum(r['extractions'] for r in geometry)>=1,geometry
    return {'incremental':{'unchanged':unchanged,'transform':transform,'geometry':geometry},'file':str(path),'objects':len(bpy.context.scene.objects),
        'mesh_instances':len(cache.instances),'unique_geometry':len(cache.geometry),
        'triangles':sum(len(g.positions)//3 for g in cache.geometry.values()),'bvh_nodes':nodes,
        'prepared_lights':len(cache.lights),'warnings':cache.warnings,
        'object_types':dict(collections.Counter(o.type for o in bpy.context.scene.objects)),
        'entity_classes':dict(collections.Counter(o.hammerless.classname for o in bpy.context.scene.objects if o.type=='EMPTY')),
        'imported_source':bpy.context.scene.get('hl_vmf_source',''),
        'roles':dict(collections.Counter(o.hammerless.role for o in bpy.context.scene.objects)),
        'seconds':{'load':loaded-start,'extract':extracted-loaded,'lights':lights_done-extracted,
                   'build_mesh_bvhs':acceleration_done-lights_done},
        'scope':'CPU preparation and one mesh transform/vertex edit; no file saves, GPU uploads or visual-parity claim'}

try:result=run()
except Exception:result={'error':traceback.format_exc()}
OUT.mkdir(parents=True,exist_ok=True)
(OUT/'map-preparation.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result),flush=True)

bpy.app.timers.register(lambda:bpy.ops.wm.quit_blender() and None,first_interval=.1)
