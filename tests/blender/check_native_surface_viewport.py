"""Actual renderer callbacks: camera retention, light edit, brush edit. No user file writes."""
from pathlib import Path
import sys,json,time,traceback
import bpy,gpu,numpy as np
from mathutils import Quaternion
ROOT=Path(__file__).resolve().parents[2]
import_root=Path.home()/'AppData/Roaming/Blender Foundation/Blender/4.4/extensions/user_default' if '--installed' in sys.argv else ROOT
sys.path.insert(0,str(import_root))
from hammerless.blender import props,l4d2preview as preview,previewnative
OUT=ROOT.parents[1]/('outputs/l4d2-preview/native-surface-installed.json' if '--installed' in sys.argv else 'outputs/l4d2-preview/native-surface-viewport.json')
assert Path(preview.__file__).resolve().is_relative_to(import_root.resolve())
state={'stage':0,'done':False,'draws':0,'paused_checks':0,'rows':[]}
original=preview.HAMMERLESS_L4D2Preview.view_draw

def finish(error=None):
    if state['done']:return
    state['done']=True
    previewnative.shutdown()
    OUT.write_text(json.dumps({'ok':error is None,'error':error,**state},indent=2))
    bpy.app.timers.register(lambda:bpy.ops.wm.quit_blender() and None,first_interval=.1)

def later(fn):
    state['editing']=True
    def edit():
        fn();bpy.context.view_layer.update();state['editing']=False
    bpy.app.timers.register(edit,first_interval=.01)

def draw(self,context,depsgraph):
    if state['done']:return original(self,context,depsgraph)
    before=self.native_surface.evaluations if self.native_surface else 0
    original(self,context,depsgraph);state['draws']+=1
    try:
        assert not self.error,self.error
        cache=self.native_surface
        if cache is None:return
        assert not cache.error,cache.error
        if self.interacting:
            assert cache.evaluations==before,'Interaction evaluated lighting'
            if state['stage']==1:
                assert np.array_equal(cache.values,state['retained']),'Navigation changed retained atlas'
                state['paused_checks']+=1
            return
        if state.get('editing') or cache.request is not None or not len(cache.values) or not cache.counts.any():return
        if state['stage']==0:
            assert cube.as_pointer() in cache.exclude_keys,'Ready brush has duplicate direct draw'
            assert cache.display is not None,'Ready brush has no cached display'
            state['retained']=cache.values.copy();state['evals']=cache.evaluations
            state['rows'].append({'stage':'initial','evaluations':cache.evaluations,'samples':len(cache.values)})
            state['stage']=1
            def orbit():
                for area in bpy.context.screen.areas:
                    if area.type=='VIEW_3D':area.spaces.active.region_3d.view_distance*=1.1
            later(orbit)
        elif state['stage']==1:
            assert np.array_equal(cache.values,state['retained']),'Camera changed atlas'
            assert cache.evaluations==state['evals'],'Camera restarted lighting'
            assert state['paused_checks']>0,'Did not observe paused interaction'
            state['rows'].append({'stage':'camera','evaluations':cache.evaluations,'retained':True})
            state['old_light']=cache.values.copy();state['stage']=2
            def light_edit():
                lamp.hammerless.keyvalues[0].value='255 255 255 50';lamp.update_tag()
            later(light_edit)
        elif state['stage']==2:
            assert cache.evaluations>state['evals'],'Light edit did not retrace'
            assert not np.array_equal(cache.values,state['old_light']),'Light edit did not change lighting'
            state['rows'].append({'stage':'light','evaluations':cache.evaluations})
            state['layout']=cache.layout;state['stage']=3
            later(lambda:setattr(cube.location,'x',.25))
        elif state['stage']==3:
            assert cache.layout!=state['layout'],'Brush edit kept old geometry binding'
            assert self.surface_cache.faces,'Brush edit lost charts'
            assert np.isfinite(cache.values).all()
            state['rows'].append({'stage':'brush','evaluations':cache.evaluations})
            state.pop('retained',None);state.pop('old_light',None)
            finish()
    except Exception:
        state.pop('retained',None);state.pop('old_light',None)
        finish(traceback.format_exc())

try:
    props.register();preview.HAMMERLESS_L4D2Preview.view_draw=draw;preview.register()
    scene=bpy.context.scene;scene.hl_preview_native=True;scene.hl_preview_surface_cache=True
    scene.hl_preview_refine_frames=1;scene.hl_preview_bounce_samples=0
    scene.hammerless.auto_sun=False;scene.hammerless.ambient_brightness=0;scene.hammerless.units_per_meter=1
    bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
    bpy.ops.mesh.primitive_cube_add();cube=bpy.context.object
    image=bpy.data.images.new('white',1,1,float_buffer=True);image.pixels[:]=[1,1,1,1]
    mat=bpy.data.materials.new('white');mat.use_nodes=True
    tex=mat.node_tree.nodes.new('ShaderNodeTexImage');tex.image=image
    mat.node_tree.links.new(tex.outputs['Color'],mat.node_tree.nodes.get('Principled BSDF').inputs['Base Color'])
    cube.data.materials.append(mat)
    bpy.ops.object.light_add(type='POINT',location=(0,0,102));lamp=bpy.context.object
    for key,value in {'_light':'255 255 255 200','_quadratic_attn':'1'}.items():
        item=lamp.hammerless.keyvalues.add();item.key=key;item.value=value
    scene.render.engine=preview.HAMMERLESS_L4D2Preview.bl_idname
    for area in bpy.context.screen.areas:
        if area.type=='VIEW_3D':
            space=area.spaces.active;space.overlay.show_overlays=False
            space.region_3d.view_rotation=Quaternion((1,0,0,0));space.region_3d.view_location=(0,0,0)
            space.region_3d.view_distance=5;space.region_3d.view_perspective='ORTHO';space.shading.type='RENDERED'
    def force():
        if state['done']:return
        try:
            for window in bpy.context.window_manager.windows:
                for area in window.screen.areas:
                    if area.type!='VIEW_3D':continue
                    region=next(r for r in area.regions if r.type=='WINDOW');space=area.spaces.active
                    off=gpu.types.GPUOffScreen(256,256)
                    try:
                        with bpy.context.temp_override(window=window,area=area,region=region):
                            off.draw_view3d(scene,bpy.context.view_layer,space,region,space.region_3d.view_matrix,space.region_3d.window_matrix)
                    finally:off.free()
                    if state['done']:return
        except Exception:finish(traceback.format_exc());return
        return .2
    bpy.app.timers.register(force,first_interval=.5)
    def timeout():
        state.pop('retained',None);state.pop('old_light',None)
        finish('Timed out after 50 seconds')
    bpy.app.timers.register(timeout,first_interval=50)
except Exception:finish(traceback.format_exc())
