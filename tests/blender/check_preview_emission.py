"""Emission snapshot parity and edit invalidation; no game launch."""
from pathlib import Path
import sys,json,traceback
import bpy,numpy as np
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from hammerless.blender import props
from hammerless.blender.l4d2preview import SceneCache
from hammerless.blender.emission import mesh_lights
from hammerless.core.previewlighting import PointLight
OUT=ROOT.parents[1]/'outputs/l4d2-preview/emission-check.json'
def run():
 props.register();scene=bpy.context.scene
 scene.hammerless.auto_sun=False;scene.hammerless.ambient_brightness=0;scene.hammerless.units_per_meter=64
 bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
 bpy.ops.mesh.primitive_plane_add();obj=bpy.context.object
 mat=bpy.data.materials.new('Emission test');mat.use_nodes=True
 node=mat.node_tree.nodes.get('Principled BSDF');node.inputs['Emission Color'].default_value=(1,.2,.05,1);node.inputs['Emission Strength'].default_value=2
 obj.data.materials.append(mat);cache=SceneCache()
 def on_update(scene,d):
  cache.update(d);cache.update_lights(d)
 bpy.app.handlers.depsgraph_update_post.append(on_update)
 def update():
  bpy.context.view_layer.update();d=bpy.context.evaluated_depsgraph_get()
  if not cache.geometry:on_update(scene,d)
  return d
 d=update();expected=mesh_lights(obj,d,64,SimpleNamespace(warnings=[]),{})
 assert len(cache.lights)==len(expected)>0, (len(cache.lights),len(expected),len(cache.geometry),cache.warnings)
 for light,entity in zip(cache.lights,expected):
  assert np.allclose(light[0],np.array(entity.origin)/64)
  assert np.allclose(light[1],PointLight.from_keys(entity.keyvalues).intensity)
 before=np.sum([l[1] for l in cache.lights],axis=0)
 node.inputs['Emission Strength'].default_value=4;update()
 assert np.allclose(np.sum([l[1] for l in cache.lights],axis=0),before*2)
 origin=np.array(cache.lights[0][0]);obj.location.x+=3;update()
 assert np.allclose(np.array(cache.lights[0][0])-origin,(3,0,0))
 node.inputs['Emission Strength'].default_value=0;update();assert not cache.lights
 return {'ok':True,'samples':len(expected),'checks':['export sample position/intensity parity','strength edit','transform edit','zero emission removal']}
try:r=run()
except Exception:r={'ok':False,'traceback':traceback.format_exc()}
OUT.write_text(json.dumps(r,indent=2));print(json.dumps(r),flush=True)
bpy.app.timers.register(lambda:bpy.ops.wm.quit_blender() and None,first_interval=.1)


