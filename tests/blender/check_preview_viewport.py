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
PROGRESSIVE=any(x in sys.argv for x in ('--bounce','--sky','--area','--emission'))
TARGET_FRAMES=18 if '--sky' in sys.argv else 8 if any(v in sys.argv for v in ('--area','--emission')) else 3
if '--sky' in sys.argv:OUT=OUT.with_name('viewport-sky-check.json')
if any(v in sys.argv for v in ('--area','--emission')):OUT=OUT.with_name('viewport-area-check.json')
if '--emission' in sys.argv:OUT=OUT.with_name('viewport-emission-check.json')
if '--prepass' in sys.argv:OUT=OUT.with_name('viewport-prepass-check.json')
if '--surface-cache' in sys.argv:OUT=OUT.with_name('viewport-surface-cache-check.json')
if '--native' in sys.argv:OUT=OUT.with_name('viewport-native-check.json')
if '--mixed-cache' in sys.argv:OUT=OUT.with_name('viewport-mixed-cache-check.json')
state={'frames':0,'updates':0,'done':False,'stage':0,'samples':[],'await_update':0}
original_draw=preview.HAMMERLESS_L4D2Preview.view_draw
original_update=preview.HAMMERLESS_L4D2Preview.view_update

if '--native' in sys.argv:
    from hammerless.blender.previewnativeview import NativeView
    original_capture=NativeView._capture
    def capture(self,*args):
        original_capture(self,*args)
        state['captures']=state.get('captures',0)+1
    NativeView._capture=capture


def finish(result):
    if state['done']:return
    if '--light-fast' in sys.argv and result.get('ok') and not state.get('fast_updates'):
        result={'ok':False,'error':'Viewport never exercised fast light path','state':dict(state)}
    state['done']=True
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(result,indent=2))
    bpy.app.timers.register(lambda:bpy.ops.wm.quit_blender() and None,first_interval=.1)


def update(self,context,depsgraph):
    state['updates']+=1
    state.setdefault('update_events',[]).append({'stage':state['stage'],'ids':[(u.id.bl_rna.identifier,u.id.name,u.is_updated_geometry,u.is_updated_transform,u.is_updated_shading) for u in depsgraph.updates]})
    original_update(self,context,depsgraph)
    state['fast_updates']=self.update_guard.fast_updates
    if '--light-fast' in sys.argv and state['stage']==1 and not state.get('repeat_light_tag'):
        state['repeat_light_tag']=True
        state['await_update']=state['updates']+1
        def retag():
            lamp=next(o for o in bpy.context.scene.objects if o.type=='LIGHT')
            lamp.hammerless.keyvalues[0].value='255 255 255 50'
            lamp.update_tag()
        bpy.app.timers.register(retag,first_interval=.01)


def draw(self,context,depsgraph):
    def work_counts():
        live=self.surface_cache
        return (self.bounce_uploads,
                live.cache.evaluations if live and live.cache else 0,
                (self.history.frames,self.history.batch_index) if self.history else None,
                self.cache.trace.builds if self.cache.trace else 0)
    before=work_counts()
    original_draw(self,context,depsgraph)
    if '--native' in sys.argv and not self.error:
        native=self.native_view
        if native is None or native.history is None or native.completed==0:return
        self.history=native.history
    if state['done']:return
    if self.interacting:
        state['interaction_draws']=state.get('interaction_draws',0)+1
        try:
            assert not self.error,self.error
            assert work_counts()==before,(before,work_counts())
            x,y,w,h=gpu.state.viewport_get()
            depth=gpu.state.active_framebuffer_get().read_depth(x+w//2,y+h//2,1,1).to_list()[0][0]
            assert 0<depth<1,('Interaction depth missing',depth)
        except Exception:
            finish({'ok':False,'traceback':traceback.format_exc()})
        return
    state['frames']+=1
    if state.get('pending_edit'):return
    if state['updates']<state['await_update']:return
    try:
        fb=gpu.state.active_framebuffer_get()
        x,y,w,h=gpu.state.viewport_get()
        pixel=fb.read_color(x+w//2,y+h//2,1,1,4,0,'FLOAT').to_list()[0][0]
        brightness=200 if state['stage']==0 else 50
        if '--mixed-cache' in sys.argv and state['stage']<2:brightness*=(100/98)**2
        if '--sky' in sys.argv:brightness+=self.cache.sky_color[0]
        if any(v in sys.argv for v in ('--area','--emission')):
            # Analytic flat receiver: source and receiver cosine / distance squared.
            brightness=sum(light[1][0]*(light[0][2]-2)**2 / (light[0][0]**2+light[0][1]**2+(light[0][2]-2)**2)**2 for light in self.cache.lights)
        if state['stage']>=4:brightness*=2
        expected=1.055*(brightness/255)**(1/2.4)-.055
        assert not self.error,self.error
        if '--surface-cache' in sys.argv:
            live=self.surface_cache
            assert live and live.cache and len(live.keys)==1,(live.warnings if live else 'Missing cache')
            if state['stage'] in (3,4):
                assert live.cache.evaluations==state['cached_evaluations'],'Camera/exposure retraced brush lighting'
        assert len(self.cache.geometry)==(2 if '--emission' in sys.argv else 1)+(256 if '--prepass' in sys.argv else 0)+int('--mixed-cache' in sys.argv), len(self.cache.geometry)
        if PROGRESSIVE:
            if self.history.frames==0 and self.history.batch_index:return
            visited=state.setdefault('history_stages',{})
            stage=str(state['stage'])
            if stage not in visited:
                wanted=TARGET_FRAMES if state['stage']==4 else 1
                assert self.history.frames==wanted,('Wrong history count after edit',state['stage'],self.history.frames)
                visited[stage]=True
            if self.history.frames<TARGET_FRAMES:return
            depth=fb.read_depth(x+w//2,y+h//2,1,1).to_list()[0][0]
            assert 0<depth<1,('Viewport depth missing',depth)
        assert max(abs(v-expected) for v in pixel[:3])<.015,(pixel,expected)

        if '--native' in sys.argv:
            assert self.native_view.compose[2], 'Optimized depth-copy path was not exercised'
            state['depth_copy_verified']=True
            wanted_captures=(1,1,2,3,3,4)[state['stage']]
            assert state['captures']==wanted_captures,('Receiver recapture mismatch',state['stage'],state['captures'])
            with self.native_view.framebuffer.bind():
                source_depth=gpu.state.active_framebuffer_get().read_depth(w//2,h//2,1,1).to_list()[0][0]
            assert abs(source_depth-depth)<1e-6,('Copied depth mismatch',source_depth,depth)
            background=fb.read_depth(x+1,y+1,1,1).to_list()[0][0]
            assert abs(background-1)<1e-6,('Background depth mismatch',background)
            if state['stage']==0:
                from gpu_extras.batch import batch_for_shader
                info=gpu.types.GPUShaderCreateInfo()
                info.vertex_in(0,'VEC3','position');info.fragment_out(0,'VEC4','color')
                info.vertex_source('void main(){gl_Position=vec4(position,1);}')
                info.fragment_source('void main(){color=vec4(1,0,1,1);}')
                overlay=gpu.shader.create_from_info(info)
                old_test=gpu.state.depth_test_get();old_mask=gpu.state.depth_mask_get()
                old_blend=gpu.state.blend_get()
                try:
                    gpu.state.depth_test_set('LESS_EQUAL');gpu.state.depth_mask_set(False);gpu.state.blend_set('NONE')
                    def marker(d):
                        overlay.bind()
                        batch_for_shader(overlay,'TRIS',{'position':[(-.02,-.02,2*d-1),(.02,-.02,2*d-1),(0,.02,2*d-1)]}).draw(overlay)
                        return fb.read_color(x+w//2,y+h//2,1,1,4,0,'FLOAT').to_list()[0][0]
                    behind=marker(min(.99,depth+.05))
                    assert max(abs(a-b) for a,b in zip(behind,pixel))<.002,('Behind-surface overlay visible',behind,pixel)
                    front=marker(max(.01,depth-.05))
                    assert front[0]>.99 and front[1]<.001 and front[2]>.99,('Foreground overlay missing',front)
                    state['overlay_occlusion_verified']=True
                finally:
                    gpu.state.depth_test_set(old_test);gpu.state.depth_mask_set(old_mask);gpu.state.blend_set(old_blend)
        if '--native' not in sys.argv and ('--sky' in sys.argv or any(v in sys.argv for v in ('--area','--emission'))):
            assert self.bounce_materials is None and self.bounce_uploads==0, 'Sky-only preview prepared bounce data'
        if state['stage']>0:
            assert self.cache.extractions==state['initial_extractions'],'Transform/light edit re-extracted geometry'
            if '--bounce' in sys.argv and '--native' not in sys.argv:
                assert self.bounce_uploads==state['initial_bounce_uploads']+(1 if state['stage']==5 else 0),'Unexpected bounce material rebuild count'
        state['samples'].append({'stage':state['stage'],'pixel':pixel,'expected_srgb':expected,
                                'history_frames':self.history.frames if self.history else 0})
        if '--surface-cache' in sys.argv:state['cached_evaluations']=self.surface_cache.cache.evaluations
        if state['stage']==(5 if PROGRESSIVE else 2):
            assert state.get('interaction_draws',0)>0,'No interaction pause exercised'
            finish({'ok':True,**state,'geometry':len(self.cache.geometry)})
        else:
            state['initial_extractions']=self.cache.extractions
            state['initial_bounce_uploads']=self.bounce_uploads
            state['stage']+=1
            state['await_update']=state['updates']+(1 if state['stage']<3 or state['stage']==5 else 0)
            state['pending_edit']=True
            def edit():
                if state['stage']==1:
                    lamp.hammerless.keyvalues[0].value='255 255 255 75' if '--light-fast' in sys.argv else '255 255 255 50'
                    if any(v in sys.argv for v in ('--area','--emission')):lamp.data.energy=2500
                    lamp.update_tag()
                    if '--emission' in sys.argv:emission.inputs['Strength'].default_value=.25
                elif state['stage']==2:
                    cube.location.x=.1
                    if '--mixed-cache' in sys.argv:prop.location.z=-3
                elif state['stage']==3:
                    for area in bpy.context.screen.areas:
                        if area.type=='VIEW_3D':area.spaces.active.region_3d.view_location.x=.1
                elif state['stage']==4:
                    scene.hl_preview_exposure=1
                else:
                    assert bpy.ops.hammerless.refresh_preview_materials()=={'FINISHED'}
                state['pending_edit']=False
                for area in bpy.context.screen.areas:area.tag_redraw()
                return None
            bpy.app.timers.register(edit,first_interval=.1)
    except Exception:
        finish({'ok':False,'traceback':traceback.format_exc(),'frames':state['frames'],
                'updates':state['updates'],'engine_error':self.error,
                })


try:
    props.register()
    preview.HAMMERLESS_L4D2Preview.view_draw=draw
    preview.HAMMERLESS_L4D2Preview.view_update=update
    preview.register()
    scene=bpy.context.scene
    scene.hl_preview_native='--native' in sys.argv
    scene.hl_preview_surface_cache='--surface-cache' in sys.argv
    scene.hammerless.auto_sun=False
    scene.hammerless.ambient_brightness=30 if '--sky' in sys.argv else 0
    scene.hammerless.ambient_color=(1,1,1)
    scene.hammerless.sky_light='FLAT'
    scene.hammerless.units_per_meter=1
    scene.hl_preview_bounce_samples=4 if '--bounce' in sys.argv else 0
    scene.hl_preview_refine_frames=TARGET_FRAMES
    bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
    bpy.ops.mesh.primitive_cube_add()
    cube=bpy.context.object
    # Explicit exported white texture isolates lighting from scene-default material.
    image=bpy.data.images.new('White lighting reference',1,1,float_buffer=True)
    image.pixels[:]=[1.,1.,1.,1.]
    mat=bpy.data.materials.new('White lighting reference');mat.use_nodes=True
    texture=mat.node_tree.nodes.new('ShaderNodeTexImage');texture.image=image
    bsdf=next(n for n in mat.node_tree.nodes if n.type=='BSDF_PRINCIPLED')
    mat.node_tree.links.new(texture.outputs['Color'],bsdf.inputs['Base Color'])
    cube.data.materials.append(mat)
    if '--mixed-cache' in sys.argv:
        prop=cube.copy();prop.data=cube.data.copy();scene.collection.objects.link(prop)
        prop.hammerless.role='MODEL';prop.location.z=3
    bpy.ops.object.light_add(type='AREA' if any(v in sys.argv for v in ('--area','--emission')) else 'POINT',location=(0,0,102))
    lamp=bpy.context.object
    if any(v in sys.argv for v in ('--area','--emission')):lamp.data.size=64;lamp.data.energy=10000
    for key,value in {'_light':'255 255 255 200','_quadratic_attn':'1'}.items():
        item=bpy.context.object.hammerless.keyvalues.add();item.key=key;item.value=value
    if '--emission' in sys.argv:
        lamp.data.energy=0
        bpy.ops.mesh.primitive_plane_add(size=64,location=(64,0,102),rotation=(3.141592653589793,0,0))
        emitter=bpy.context.object
        emitting=bpy.data.materials.new('Emitting reference');emitting.use_nodes=True
        emission=emitting.node_tree.nodes.new('ShaderNodeEmission')
        emission.inputs['Strength'].default_value=1
        output=next(n for n in emitting.node_tree.nodes if n.type=='OUTPUT_MATERIAL')
        emitting.node_tree.links.new(emission.outputs[0],output.inputs['Surface'])
        emitter.data.materials.append(emitting)
    if '--prepass' in sys.argv:
        for index in range(256):
            extra=cube.copy();extra.data=cube.data.copy()
            scene.collection.objects.link(extra)
            extra.location=(10000+index*4,10000,0)
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
        if not state['done']:finish({'ok':False,'reason':'No successful viewport callback within 60 seconds',**state})
        return None
    bpy.app.timers.register(timeout,first_interval=60)
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
