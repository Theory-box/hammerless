"""Observe Blender depsgraph notifications for simultaneous light/custom edits.

Records evidence only; does not assume an absent notification is safe to ignore.
Run with run_isolated.py; no user files or preferences are saved.
"""
from pathlib import Path
import sys,json,traceback
import bpy
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from hammerless.blender import props,vmfimport
OUT=ROOT.parents[1]/'outputs/l4d2-preview/preview-light-notifications.json'

def run():
 props.register();scene=bpy.context.scene
 bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
 bpy.ops.mesh.primitive_cube_add();brush=bpy.context.object
 material=bpy.data.materials.new('Notification material');material.use_nodes=True
 brush.data.materials.append(material)
 bpy.ops.object.light_add(type='POINT');light=bpy.context.object
 text=bpy.data.texts.new('Notification source.vmf');text.write('world { "id" "1" "classname" "worldspawn" }')
 scene[vmfimport.SOURCE_KEY]=text.name
 bpy.context.view_layer.update();bpy.context.evaluated_depsgraph_get()
 captured=[];rows=[]
 def capture(scene_arg,depsgraph):
  captured.append([dict(type=u.id.bl_rna.identifier,name=u.id.name,
                       original_type=u.id.original.bl_rna.identifier,
                       transform=u.is_updated_transform,geometry=u.is_updated_geometry,
                       shading=u.is_updated_shading) for u in depsgraph.updates])
 bpy.app.handlers.depsgraph_update_post.append(capture)
 def record(name,change):
  captured.clear();change();light.location.x+=.125
  bpy.context.view_layer.update();deps=bpy.context.evaluated_depsgraph_get()
  rows.append(dict(case=name,events=list(captured),post_updates=[dict(type=u.id.bl_rna.identifier,name=u.id.name) for u in deps.updates],
                   state=dict(role=brush.hammerless.role,scene_scale=scene.hammerless.lightmap_scale,
                              material_scale=material.hammerless.lightmap_scale,
                              text=text.as_string(),hidden=brush.hide_get())))
 try:
  record('light transform only',lambda:None)
  record('object role plus light transform',lambda:setattr(brush.hammerless,'role','IGNORE'))
  record('scene lightmap scale plus light transform',lambda:setattr(scene.hammerless,'lightmap_scale',32))
  record('material lightmap scale plus light transform',lambda:setattr(material.hammerless,'lightmap_scale',8))
  record('source text plus light transform',lambda:text.write('\nentity { "id" "999" "classname" "info_target" }'))
  record('object visibility plus light transform',lambda:brush.hide_set(True))
  record('material node value plus light transform',lambda:setattr(material.node_tree.nodes.get('Principled BSDF').inputs['Roughness'],'default_value',.25))
 finally:bpy.app.handlers.depsgraph_update_post.remove(capture)
 assert all(row['events'] for row in rows),'No handlers observed; inconclusive fixture'
 return dict(ok=True,blender=bpy.app.version_string,rows=rows,
             scope='depsgraph_update_post in isolated ordinary evaluated scene; not RenderEngine callbacks')
try:result=run()
except Exception:result=dict(ok=False,traceback=traceback.format_exc())
OUT.write_text(json.dumps(result,indent=2));print(json.dumps(result),flush=True)
bpy.app.timers.register(lambda:bpy.ops.wm.quit_blender() and None,first_interval=.1)
