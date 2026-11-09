"""Imported scales, override toggles and duplicates follow VMF export policy."""
from pathlib import Path
import sys,json,traceback
import bpy
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from hammerless.blender import props,vmfimport
from hammerless.blender.previewsurfacecharts import ChartSnapshot
from hammerless.core.geometry import box_brush
from hammerless.core.vmf import VMFWriter,Block
OUT=ROOT.parents[1]/'outputs/l4d2-preview/imported-surface-scales.json'
def run():
 props.register();scene=bpy.context.scene
 bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
 brush=box_brush((0,0,0),(128,128,128),'dev/dev_measuregeneric01b')
 original=[4,8,16,32,8,16]
 for face,scale in zip(brush.faces,original):face.lightmap_scale=scale
 writer=VMFWriter();world=Block('world');world.kv('id',writer.new_id());world.kv('classname','worldspawn');world.add(writer.solid(brush))
 lines=[];world.write(lines);text=''.join(lines)
 vmfimport.import_text(bpy.context,text,'surface-cache-scale-test.vmf')
 obj=next(o for o in scene.objects if o.get(vmfimport.KIND)=='solid')
 scene.hammerless.lightmap_scale=64;scene.hammerless.lightmap_scale_imported=False
 bpy.context.view_layer.update();deps=bpy.context.evaluated_depsgraph_get()
 def scales(target=obj):return sorted(c.lightmap_scale for c in ChartSnapshot(bpy.context).object_charts(target,deps))
 assert scales()==sorted(original),scales()
 material=obj.material_slots[0].material;material.hammerless.lightmap_scale=8
 assert scales()==sorted(original),'Material override changed preserved imported scales'
 scene.hammerless.lightmap_scale_imported=True
 assert scales()==[8]*6,scales()
 material.hammerless.lightmap_scale=0
 assert scales()==[64]*6,scales()
 scene.hammerless.lightmap_scale_imported=False
 assert scales()==sorted(original),'Override toggle mutated original source'
 copy=obj.copy();copy.data=obj.data.copy();scene.collection.objects.link(copy)
 bpy.context.view_layer.update();deps=bpy.context.evaluated_depsgraph_get()
 assert scales(copy)==[64]*6,'Duplicate incorrectly kept original per-face scales'
 obj.location.x+=1;bpy.context.view_layer.update();deps=bpy.context.evaluated_depsgraph_get()
 assert scales()==sorted(original),'Moving original lost preserved scale'
 assert bpy.data.texts[scene[vmfimport.SOURCE_KEY]].as_string()==text
 return {'ok':True,'checks':['preserved original per-face scale','material override opt-in','scene default override','toggle restores original','duplicate uses new brush rules','moved brush retains scale','source text unchanged']}
try:r=run()
except Exception:r={'ok':False,'traceback':traceback.format_exc()}
OUT.write_text(json.dumps(r,indent=2));print(json.dumps(r),flush=True)
bpy.app.timers.register(lambda:bpy.ops.wm.quit_blender() and None,first_interval=.1)
