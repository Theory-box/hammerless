"""Actual Blender material export settings feed surface cache chart density."""
from pathlib import Path
import sys,json,traceback
import bpy
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from hammerless.blender import props
from hammerless.blender.extract import MaterialResolver,mesh_to_brushes
from hammerless.core.vmf import VMFWriter
from hammerless.core.surfacecharts import chart_from_side
from hammerless.blender.previewsurfacecharts import CachedObjectCharts
OUT=ROOT.parents[1]/'outputs/l4d2-preview/live-surface-scales.json'
def run():
 props.register();scene=bpy.context.scene;scene.hammerless.units_per_meter=64;scene.hammerless.lightmap_scale=32
 bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
 bpy.ops.mesh.primitive_cube_add();obj=bpy.context.object
 mat=bpy.data.materials.new('Surface scale fixture');mat.hammerless.source_material='dev/dev_measuregeneric01b'
 obj.data.materials.append(mat)
 rows=[]
 for scale in (0,4,8,16):
  mat.hammerless.lightmap_scale=scale;bpy.context.view_layer.update()
  resolver=MaterialResolver(scene.hammerless,None,SimpleNamespace(warnings=[]))
  brushes=mesh_to_brushes(obj,bpy.context.evaluated_depsgraph_get(),64,resolver)
  charts=[chart_from_side(VMFWriter().side(face),face.verts) for brush in brushes for face in brush.faces]
  expected=scale or 32
  assert len(charts)==6 and all(c.lightmap_scale==expected for c in charts)
  assert all(c.w==128//expected+1 and c.h==128//expected+1 for c in charts)
  rows.append({'material_scale':scale,'effective_scale':expected,'samples':sum(c.w*c.h for c in charts)})
 cached=CachedObjectCharts();deps=bpy.context.evaluated_depsgraph_get()
 initial=cached.get(bpy.context,obj,deps,1)
 for _ in range(20):assert cached.get(bpy.context,obj,deps,1) is initial
 assert cached.builds==1
 mat.hammerless.lightmap_scale=8
 changed=cached.get(bpy.context,obj,deps,1)
 assert cached.builds==2 and changed is not initial and all(c.lightmap_scale==8 for c in changed)
 obj.location.x+=1;bpy.context.view_layer.update()
 moved=cached.get(bpy.context,obj,bpy.context.evaluated_depsgraph_get(),1)
 assert cached.builds==3 and moved is not changed
 cached.get(bpy.context,obj,bpy.context.evaluated_depsgraph_get(),2)
 assert cached.builds==4
 cached.retain([]);assert not cached.entries
 return {'ok':True,'layout_cache_checks':['unchanged reuse','material density invalidation','transform invalidation','geometry revision invalidation','removed object eviction'],'rows':rows,'scope':'New brush export, actual Blender material override and scene default. Imported original-face scales and adaptive compile changes not covered.'}
try:r=run()
except Exception:r={'ok':False,'traceback':traceback.format_exc()}
OUT.write_text(json.dumps(r,indent=2));print(json.dumps(r),flush=True)
bpy.app.timers.register(lambda:bpy.ops.wm.quit_blender() and None,first_interval=.1)
