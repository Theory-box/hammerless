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
    return {'file':str(path),'objects':len(bpy.context.scene.objects),
        'mesh_instances':len(cache.instances),'unique_geometry':len(cache.geometry),
        'triangles':sum(len(g.positions)//3 for g in cache.geometry.values()),'bvh_nodes':nodes,
        'prepared_lights':len(cache.lights),'warnings':cache.warnings,
        'object_types':dict(collections.Counter(o.type for o in bpy.context.scene.objects)),
        'entity_classes':dict(collections.Counter(o.hammerless.classname for o in bpy.context.scene.objects if o.type=='EMPTY')),
        'imported_source':bpy.context.scene.get('hl_vmf_source',''),
        'roles':dict(collections.Counter(o.hammerless.role for o in bpy.context.scene.objects)),
        'seconds':{'load':loaded-start,'extract':extracted-loaded,'lights':lights_done-extracted,
                   'build_mesh_bvhs':acceleration_done-lights_done},
        'scope':'Cold CPU preparation only; no GPU uploads, frame timing, or visual-parity claim'}

try:result=run()
except Exception:result={'error':traceback.format_exc()}
OUT.mkdir(parents=True,exist_ok=True)
(OUT/'map-preparation.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result),flush=True)
