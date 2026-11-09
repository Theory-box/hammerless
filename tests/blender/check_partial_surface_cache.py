"""One bad imported face must not discard five valid charts or lose geometry."""
from pathlib import Path
import sys,json,traceback
import bpy,numpy as np
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from hammerless.blender import props,vmfimport
from hammerless.blender.l4d2preview import SceneCache
from hammerless.blender.previewlivecache import LiveSurfaceCache,matched_charts
from hammerless.blender.previewmaterials import SurfaceMaterials
from hammerless.core.geometry import box_brush
from hammerless.core.vmf import VMFWriter,Block
OUT=ROOT.parents[1]/'outputs/l4d2-preview/partial-surface-cache.json'
def run():
 props.register();scene=bpy.context.scene
 bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
 brush=box_brush((0,0,0),(128,128,128),'dev/dev_measuregeneric01b')
 brush.faces[0].tex_axes=(((1,0,0),0,.25),((1,0,0),0,.25))
 writer=VMFWriter();world=Block('world');world.kv('id',writer.new_id());world.kv('classname','worldspawn');world.add(writer.solid(brush))
 lines=[];world.write(lines);text=''.join(lines)
 vmfimport.import_text(bpy.context,text,'partial-chart-test.vmf')
 obj=next(o for o in scene.objects if o.get(vmfimport.KIND)=='solid')
 results=[]
 live=LiveSurfaceCache();cache=SceneCache()
 cache.surface_materials=SurfaceMaterials(None,None,scene.hammerless.default_material)
 for transform in ((1,1,1),(-1,2,.5)):
  obj.scale=transform;bpy.context.view_layer.update();deps=bpy.context.evaluated_depsgraph_get()
  cache.update(deps);live.update(bpy.context,deps,cache,0)
  key=obj.as_pointer();original=cache.geometry[key]
  assert len(live.faces)==5,(len(live.faces),live.warnings)
  assert key not in live.keys and key in live.remaining
  assert len(live.remaining[key].positions)==6
  assert sum(len(f.positions) for f in live.faces)+len(live.remaining[key].positions)==len(original.positions)==36
  previous=live.remaining[key];builds=live.layouts.builds;generation=live.generation
  for _ in range(10):live.update(bpy.context,deps,cache,0)
  assert live.remaining[key] is previous and live.layouts.builds==builds and live.generation==generation
  # Two competing charts never swallow ambiguous triangles.
  duplicate=[live.faces[0],live.faces[0]]
  accepted,covered=matched_charts(duplicate,original,obj.matrix_world,scene.hammerless.units_per_meter,scene.hammerless.default_material)
  assert not accepted and not covered.any()
  results.append({'transform':transform,'cached_faces':5,'fallback_triangles':2,'stable_reuse':True})
 limited=LiveSurfaceCache();limited.SAMPLE_BUDGET=1
 limited.update(bpy.context,deps,cache,0);assert limited.budget_warnings and not limited.faces
 limited.update(bpy.context,deps,cache,0);assert limited.budget_warnings and any('budget' in w for w in limited.warnings)
 assert bpy.data.texts[scene[vmfimport.SOURCE_KEY]].as_string()==text
 return {'ok':True,'cases':results,'ambiguous_rejected':True,'budget_warning_persists':True,'source_unchanged':True}
try:r=run()
except Exception:r={'ok':False,'traceback':traceback.format_exc()}
OUT.write_text(json.dumps(r,indent=2));print(json.dumps(r),flush=True)
bpy.app.timers.register(lambda:bpy.ops.wm.quit_blender() and None,first_interval=.1)
