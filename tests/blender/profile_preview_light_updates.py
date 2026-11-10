"""Time actual view_update logic with real depsgraph notification batches."""
from pathlib import Path
from types import SimpleNamespace
import sys,json,time,traceback
import bpy
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from hammerless.blender import props,l4d2preview as preview
from hammerless.blender.previewchanges import LightUpdateGuard
from hammerless.core.compile import find_game_root
OUT=ROOT.parents[1]/'outputs/l4d2-preview/light-update-profile.json'

def run():
 props.register();preview.register()
 path=Path(sys.argv[sys.argv.index('--')+1]).resolve()
 bpy.ops.wm.open_mainfile(filepath=str(path),load_ui=False,use_scripts=False)
 scene=bpy.context.scene;scene.hammerless.game_root=find_game_root()
 scene.hl_preview_surface_cache=True;scene.hl_preview_bounce_samples=4
 lamp=bpy.data.objects.new('Guard profile light',bpy.data.lights.new('Guard profile light','POINT'))
 scene.collection.objects.link(lamp);bpy.context.view_layer.update()
 h=SimpleNamespace(cache=preview.SceneCache(),shader=None,shader_bounce=None,bounce_materials=None,
   bounce_signature=None,bounce_pending=False,bounce_uploads=0,history=None,history_depth_shader=None,
   material_revision=0,refresh_revision=preview._material_refresh_revision,scene_signature=None,error='',
   surface_signature=None,surface_cache=None,update_guard=LightUpdateGuard(),tag_redraw=lambda:None)
 invoke=lambda deps:preview.HAMMERLESS_L4D2Preview.view_update(h,bpy.context,deps)
 invoke(bpy.context.evaluated_depsgraph_get());assert not h.error,h.error
 rows=[];failures=[];active=False
 def handler(scene_arg,deps):
  nonlocal active
  if active:return
  active=True
  try:
   before=h.update_guard.fast_updates
   start=time.perf_counter();invoke(deps);ms=(time.perf_counter()-start)*1000
   assert not h.error,h.error
   fast=h.update_guard.fast_updates>before
   signature=h.scene_signature;generation=h.surface_cache.generation
   # Force normal work on the identical scene and notifications as reference.
   saved_guard=h.update_guard
   h.update_guard=SimpleNamespace(check=lambda *args:False,clear=lambda:None)
   try:
    start=time.perf_counter();invoke(deps);full_ms=(time.perf_counter()-start)*1000
   finally:h.update_guard=saved_guard
   assert not h.error,h.error
   assert h.scene_signature==signature,'Fast and full lighting signatures differ'
   assert h.surface_cache.generation==generation,'Fast path missed changed chart layout'
   rows.append(dict(fast=fast,update_ms=ms,forced_full_ms=full_ms,
       ids=[(u.id.bl_rna.identifier,u.id.name) for u in deps.updates]))
  except Exception:failures.append(traceback.format_exc())
  finally:active=False
 bpy.app.handlers.depsgraph_update_post.append(handler)
 try:
  for _ in range(5):
   lamp.location.x+=1/scene.hammerless.units_per_meter;bpy.context.view_layer.update()
 finally:bpy.app.handlers.depsgraph_update_post.remove(handler)
 assert not failures,failures
 assert sum(r['fast'] for r in rows)>=2,rows
 return dict(ok=True,rows=rows,instances=len(h.cache.instances),
   scope='Hotel saved scene; actual view_update logic invoked from real depsgraph_update_post, pure light transform; same-state full reference bypassing guard overhead; CPU preparation only, no drawing or GPU timing')
try:r=run()
except Exception:r=dict(ok=False,traceback=traceback.format_exc())
OUT.write_text(json.dumps(r,indent=2));print(json.dumps(r),flush=True)
bpy.app.timers.register(lambda:bpy.ops.wm.quit_blender() and None,first_interval=.1)
