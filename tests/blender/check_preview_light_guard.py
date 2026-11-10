"""Real depsgraph callback: guarded CPU update equals forced full update."""
from pathlib import Path
from types import SimpleNamespace
import json,sys,traceback
import bpy,numpy as np
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
from hammerless.blender import props,l4d2preview as preview,vmfimport
from hammerless.blender.previewchanges import LightUpdateGuard
OUT=ROOT.parents[1]/'outputs/l4d2-preview/preview-light-guard.json'

def harness():
 return SimpleNamespace(cache=preview.SceneCache(),shader=None,shader_bounce=None,bounce_materials=None,
  bounce_signature=None,bounce_pending=False,bounce_uploads=0,history=None,history_depth_shader=None,
  material_revision=0,refresh_revision=preview._material_refresh_revision,scene_signature=None,error='',
  surface_signature=None,surface_cache=None,update_guard=LightUpdateGuard(),tag_redraw=lambda:None)

def run():
 props.register();preview.register();scene=bpy.context.scene
 bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
 scene.hl_preview_surface_cache=True;scene.hl_preview_bounce_samples=4
 bpy.ops.mesh.primitive_cube_add();brush=bpy.context.object
 material=bpy.data.materials.new('Guard material');material.hammerless.source_material='dev/dev_measuregeneric01b'
 brush.data.materials.append(material)
 bpy.ops.object.light_add(type='POINT',location=(0,0,5));light=bpy.context.object
 source=bpy.data.texts.new('guard.vmf');source.write('world { "id" "1" "classname" "worldspawn" }')
 scene[vmfimport.SOURCE_KEY]=source.name
 bpy.context.view_layer.update();deps=bpy.context.evaluated_depsgraph_get()
 guarded=harness();full=harness();rows=[];active=False;label='';errors=[]
 for h in (guarded,full):preview.HAMMERLESS_L4D2Preview.view_update(h,bpy.context,deps);assert not h.error,h.error
 def compare():
  assert not guarded.error and not full.error,(guarded.error,full.error)
  assert guarded.cache.light_signature==full.cache.light_signature
  assert guarded.cache.sky_color==full.cache.sky_color
  assert set(guarded.cache.geometry)==set(full.cache.geometry)
  assert [(k,tuple(v for r in m for v in r)) for k,m in guarded.cache.instances]==[(k,tuple(v for r in m for v in r)) for k,m in full.cache.instances]
  for key,g in guarded.cache.geometry.items():
   other=full.cache.geometry[key]
   np.testing.assert_array_equal(g.positions,other.positions)
   assert g.use_uv==other.use_uv and g.shadow_signature==other.shadow_signature
  if guarded.surface_cache is not None:
   assert full.surface_cache is not None
   assert guarded.surface_cache.keys==full.surface_cache.keys
   assert set(guarded.surface_cache.remaining)==set(full.surface_cache.remaining)
   if guarded.surface_cache.positions is not None:
    np.testing.assert_array_equal(guarded.surface_cache.positions,full.surface_cache.positions)
   else:assert full.surface_cache.positions is None
 def handler(_scene,graph):
  nonlocal active
  if active:return
  active=True
  try:
   before=guarded.update_guard.fast_updates
   preview.HAMMERLESS_L4D2Preview.view_update(guarded,bpy.context,graph)
   full.update_guard.clear();preview.HAMMERLESS_L4D2Preview.view_update(full,bpy.context,graph)
   compare();rows.append(dict(case=label,fast=guarded.update_guard.fast_updates>before))
  except Exception:errors.append(traceback.format_exc())
  finally:active=False
 bpy.app.handlers.depsgraph_update_post.append(handler)
 def step(name,change=None,expect_fast=None):
  nonlocal label
  label=name;start=len(rows)
  if change:change()
  light.location.x+=.05;bpy.context.view_layer.update()
  assert not errors,errors
  cases=rows[start:];assert cases,name
  if expect_fast is True:assert any(c['fast'] for c in cases),(name,cases)
  if expect_fast is False:assert not any(c['fast'] for c in cases),(name,cases)
 try:
  step('warm light');step('light only fast',expect_fast=True)
  step('silent object role',lambda:setattr(brush.hammerless,'role','IGNORE'),False)
  step('restore object role',lambda:setattr(brush.hammerless,'role','AUTO'),False)
  step('silent scene density',lambda:setattr(scene.hammerless,'lightmap_scale',32),False)
  step('silent material density',lambda:setattr(material.hammerless,'lightmap_scale',8),False)
  step('silent mesh model flag',lambda:brush.data.__setitem__('hl_model','models/test.mdl'),False)
  step('remove mesh model flag',lambda:brush.data.__delitem__('hl_model'),False)
  step('silent texture dimensions',lambda:material.__setitem__('hl_tex_size',[256,256]),False)
  step('source text edit',lambda:source.write('\n// source mutation\n'),False)
  step('material name',lambda:setattr(material,'name','Changed guard material'),False)
  step('collection role',lambda:setattr(brush.users_collection[0].hammerless,'role','IGNORE'),False)
  step('restore collection role',lambda:setattr(brush.users_collection[0].hammerless,'role','NONE'),False)
  step('toggle cache',lambda:setattr(scene,'hl_preview_surface_cache',False),False)
  step('restore cache',lambda:setattr(scene,'hl_preview_surface_cache',True),False)
  step('bounce settings',lambda:setattr(scene,'hl_preview_bounce_samples',8),False)
  step('visibility',lambda:brush.hide_set(True),False)
  step('restore visibility',lambda:brush.hide_set(False),False)
  step('parent light',lambda:setattr(brush,'parent',light),False)
  step('light with geometry child remains full',expect_fast=False)
  step('unparent',lambda:setattr(brush,'parent',None),False)
  def add_parent():
   parent=bpy.data.objects.new('Guard generic parent',None);scene.collection.objects.link(parent);brush.parent=parent
  step('generic parent',add_parent,False)
  step('parent trigger classname',lambda:setattr(brush.parent.hammerless,'classname','trigger_multiple'),False)
  step('restore parent classname',lambda:setattr(brush.parent.hammerless,'classname',''),False)
  step('frame change',lambda:scene.frame_set(scene.frame_current+1),False)
  step('explicit material refresh',lambda:setattr(preview,'_material_refresh_revision',preview._material_refresh_revision+1),False)
  step('warm again');step('light fast again',expect_fast=True)
 finally:bpy.app.handlers.depsgraph_update_post.remove(handler)
 return dict(ok=True,rows=rows,fast_updates=guarded.update_guard.fast_updates,full_updates=guarded.update_guard.full_updates,
  scope='actual depsgraph_update_post calls RenderEngine.view_update on paired CPU harnesses; array/signature comparisons, not framebuffer rendering')
try:result=run()
except Exception:result=dict(ok=False,traceback=traceback.format_exc())
OUT.write_text(json.dumps(result,indent=2));print(json.dumps(result),flush=True)
bpy.app.timers.register(lambda:bpy.ops.wm.quit_blender() and None,first_interval=.1)
