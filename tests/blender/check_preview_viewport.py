"""Exercise actual RenderEngine callbacks in a separate factory-startup window.
No mouse control, preferences changes, scene saves, or user files.
"""
from pathlib import Path
import sys,json,time,traceback
import bpy,gpu
from mathutils import Quaternion
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))
from hammerless.blender import props,l4d2preview as preview
OUT=ROOT.parents[1]/('outputs/l4d2-preview/viewport-bounce-check.json' if '--bounce' in sys.argv else 'outputs/l4d2-preview/viewport-check.json')
state={'frames':0,'updates':0,'done':False,'stage':0,'samples':[],'await_update':0}
original_draw=preview.HAMMERLESS_L4D2Preview.view_draw
original_update=preview.HAMMERLESS_L4D2Preview.view_update


def finish(result):
    if state['done']:return
    state['done']=True
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(result,indent=2))
    bpy.app.timers.register(lambda:bpy.ops.wm.quit_blender() and None,first_interval=.1)


def update(self,context,depsgraph):
    state['updates']+=1
    original_update(self,context,depsgraph)


def draw(self,context,depsgraph):
    original_draw(self,context,depsgraph)
    if state['done']:return
    state['frames']+=1
    if state.get('pending_edit'):return
    if state['updates']<state['await_update']:return
    try:
        fb=gpu.state.active_framebuffer_get()
        x,y,w,h=gpu.state.viewport_get()
        pixel=fb.read_color(x+w//2,y+h//2,1,1,4,0,'FLOAT').to_list()[0][0]
        brightness=200 if state['stage']==0 else 50
        if state['stage']==4:brightness*=2
        expected=1.055*(brightness/255)**(1/2.4)-.055
        assert not self.error,self.error
        assert len(self.cache.geometry)==1, len(self.cache.geometry)
        assert max(abs(v-expected) for v in pixel[:3])<.015,(pixel,expected)
        if '--bounce' in sys.argv:
            visited=state.setdefault('history_stages',{})
            stage=str(state['stage'])
            if stage not in visited:
                wanted=3 if state['stage']==4 else 1
                assert self.history.frames==wanted,('Wrong history count after edit',state['stage'],self.history.frames)
                visited[stage]=True
            if self.history.frames<3:return
            depth=fb.read_depth(x+w//2,y+h//2,1,1).to_list()[0][0]
            assert 0<depth<1,('Viewport depth missing',depth)
        if state['stage']>0:
            assert self.cache.extractions==state['initial_extractions'],'Transform/light edit re-extracted geometry'
            if '--bounce' in sys.argv:
                assert self.bounce_uploads==state['initial_bounce_uploads'],'Transform/light edit rebuilt bounce materials'
        state['samples'].append({'stage':state['stage'],'pixel':pixel,'expected_srgb':expected,
                                'history_frames':self.history.frames if self.history else 0})
        if state['stage']==(4 if '--bounce' in sys.argv else 2):
            finish({'ok':True,**state,'geometry':len(self.cache.geometry)})
        else:
            state['initial_extractions']=self.cache.extractions
            state['initial_bounce_uploads']=self.bounce_uploads
            state['stage']+=1
            state['await_update']=state['updates']+(1 if state['stage']<3 else 0)
            state['pending_edit']=True
            def edit():
                if state['stage']==1:
                    lamp.hammerless.keyvalues[0].value='255 255 255 50'
                    lamp.update_tag()
                elif state['stage']==2:
                    cube.location.x=.1
                elif state['stage']==3:
                    for area in bpy.context.screen.areas:
                        if area.type=='VIEW_3D':area.spaces.active.region_3d.view_location.x=.1
                else:
                    scene.hl_preview_exposure=1
                state['pending_edit']=False
                for area in bpy.context.screen.areas:area.tag_redraw()
                return None
            bpy.app.timers.register(edit,first_interval=.1)
    except Exception:
        finish({'ok':False,'traceback':traceback.format_exc(),'frames':state['frames'],
                'updates':state['updates'],'engine_error':self.error})


try:
    props.register()
    preview.HAMMERLESS_L4D2Preview.view_draw=draw
    preview.HAMMERLESS_L4D2Preview.view_update=update
    preview.register()
    scene=bpy.context.scene
    scene.hammerless.auto_sun=False
    scene.hammerless.ambient_brightness=0
    scene.hammerless.units_per_meter=1
    scene.hl_preview_bounce_samples=4 if '--bounce' in sys.argv else 0
    scene.hl_preview_refine_frames=3
    bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
    bpy.ops.mesh.primitive_cube_add()
    cube=bpy.context.object
    bpy.ops.object.light_add(type='POINT',location=(0,0,102))
    lamp=bpy.context.object
    for key,value in {'_light':'255 255 255 200','_quadratic_attn':'1'}.items():
        item=bpy.context.object.hammerless.keyvalues.add();item.key=key;item.value=value
    scene.render.engine=preview.HAMMERLESS_L4D2Preview.bl_idname
    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type=='VIEW_3D':
                space=area.spaces.active
                space.overlay.show_overlays=False
                space.region_3d.view_rotation=Quaternion((1,0,0,0))
                space.region_3d.view_location=(0,0,0)
                space.region_3d.view_distance=5
                space.region_3d.view_perspective='ORTHO'
                space.shading.type='RENDERED'
                area.tag_redraw()
    def timeout():
        if not state['done']:finish({'ok':False,'reason':'No successful viewport callback within 20 seconds',**state})
        return None
    bpy.app.timers.register(timeout,first_interval=20)
    def force_redraw():
        if state['done']:return None
        state['windows']=[[(a.type, a.spaces.active.shading.type if a.type=='VIEW_3D' else '') for a in w.screen.areas] for w in bpy.context.window_manager.windows]
        state['engine']=bpy.context.scene.render.engine
        # Hidden windows receive no paint event. Ask Blender to draw its actual
        # 3D region offscreen; do not invoke our RenderEngine methods directly.
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type != 'VIEW_3D':continue
                region=next(r for r in area.regions if r.type=='WINDOW')
                space=area.spaces.active
                off=gpu.types.GPUOffScreen(256,256)
                try:
                    with bpy.context.temp_override(window=window,area=area,region=region):
                        off.draw_view3d(scene,bpy.context.view_layer,space,region,
                            space.region_3d.view_matrix,space.region_3d.window_matrix)
                finally:off.free()
                if state['done']:return None
        return .2
    bpy.app.timers.register(force_redraw,first_interval=.5)
except Exception:
    finish({'ok':False,'traceback':traceback.format_exc()})
