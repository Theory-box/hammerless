"""Opt-in, transient selected-effect preview. Never creates or saves scene objects."""
import time
import bpy
import gpu
import numpy as np
from mathutils import Vector
from gpu_extras.batch import batch_for_shader
from ..core.particlepreview import load_library, audit
from ..core.particleeffect import EffectSimulation
from ..core.particlegeometry import build_geometry, RENDERERS
from ..core.particlematerial import blend_mode

_state = {}
_shader = None
_selection = None
_migrated_scene = None
_library_cache = {}


def control_point_bindings(scene,emitter):
    """Resolve authored cpoint1..63 target names without changing scene data."""
    names={};duplicates=set()
    for obj in scene.objects:
        target=next((k.value for k in obj.hammerless.keyvalues if k.key=='targetname' and k.value),'')
        for name in {obj.name,target}-{''}:
            if name in names and names[name]!=obj:duplicates.add(name)
            names[name]=obj
    bindings={0:emitter};warnings=[]
    for kv in emitter.hammerless.keyvalues:
        if not kv.key.startswith('cpoint') or not kv.key[6:].isdigit() or not kv.value:continue
        index=int(kv.key[6:])
        if not 1<=index<64:continue
        if kv.value in duplicates:warnings.append('Ambiguous control-point target: '+kv.value)
        elif kv.value in names:bindings[index]=names[kv.value]
        else:warnings.append('Missing control-point target: '+kv.value)
    return bindings,warnings


def sample_control_points(bindings,frame,scale):
    inverse=frame.inverted_safe()
    result={}
    for index,obj in bindings.items():
        matrix=np.asarray(inverse@obj.matrix_world,dtype=float)
        matrix[:3,3]*=scale
        result[index]=matrix
    return result


def stop(*args):
    handle = _state.get('handle')
    if handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(handle, 'WINDOW')
    _state.clear()
    if bpy.app.timers.is_registered(tick):
        bpy.app.timers.unregister(tick)
    for wm in bpy.data.window_managers:
        for window in wm.windows:
            for area in window.screen.areas:
                if area.type == 'VIEW_3D':
                    area.tag_redraw()


def work_busy():
    """Do no particle simulation, tracing, migration or GPU drawing during builds."""
    from . import ops,navview
    if ops._BUILDING or ops._BAKES or ops.export_running() or navview._state.get('busy',False):return True
    for job in ('RENDER','OBJECT_BAKE'):
        try:
            if bpy.app.is_job_running(job):return True
        except (ValueError,TypeError):pass
    return False


def tick():
    try:
        if work_busy():
            _state['last']=time.perf_counter()
            _state.pop('finished_since',None)
            return .25
        obj = _state.get('object')
        if obj is None or obj.name not in bpy.context.scene.objects or bpy.context.active_object != obj:
            stop(); return None
        current = next((k.value for k in obj.hammerless.keyvalues if k.key == 'effect_name'), '').casefold()
        if current != _state['effect']:
            stop(); return None
        now = time.perf_counter()
        dt = min(.1, now-_state['last'])
        _state['last'] = now
        points=sample_control_points(_state['bindings'],_state['frame'],_state['scale'])
        _state['effect_simulation'].advance(dt,points)
        _state['time']=_state['effect_simulation'].time
        tree=_state['effect_simulation']
        if tree.finished():
            _state['finished_since']=_state.get('finished_since',now)
            if now-_state['finished_since']>=.75:
                tree=_state['factory'](points)
                _state['effect_simulation']=tree
                _state['systems']=[(sim,0,*_state['textures'][sim.definition.attrs.get('material','')]) for sim in tree.systems]
                _state.pop('finished_since',None)
        else:_state.pop('finished_since',None)
        for wm in bpy.data.window_managers:
            for window in wm.windows:
                for area in window.screen.areas:
                    if area.type == 'VIEW_3D': area.tag_redraw()
        return max(.001, 1/60-(time.perf_counter()-now))
    except Exception as exc:
        print('Hammerless particle preview stopped:', exc)
        stop(); return None


def shader():
    global _shader
    if _shader is None:
        interface = gpu.types.GPUStageInterfaceInfo('hl_particle_preview')
        interface.smooth('VEC2','uvInterp'); interface.smooth('VEC2','uv2Interp');
        interface.smooth('VEC2','uvNextInterp');interface.smooth('VEC2','uv2NextInterp');interface.smooth('VEC2','frameMixInterp'); interface.smooth('VEC4','tintInterp')
        info = gpu.types.GPUShaderCreateInfo()
        info.push_constant('MAT4','mvp')
        info.push_constant('FLOAT','repeat_v')
        info.push_constant('VEC4','material_settings')
        info.push_constant('VEC2','maxlum')
        info.push_constant('FLOAT','addself')
        info.sampler(0,'FLOAT_2D','sprite')
        info.vertex_in(0,'VEC3','pos'); info.vertex_in(1,'VEC2','uv'); info.vertex_in(2,'VEC4','tint'); info.vertex_in(3,'VEC2','uv2');info.vertex_in(4,'VEC2','uv_next');info.vertex_in(5,'VEC2','uv2_next');info.vertex_in(6,'VEC2','frame_mix')
        info.vertex_out(interface); info.fragment_out(0,'VEC4','fragColor')
        info.vertex_source('void main(){uvInterp=uv;uv2Interp=uv2;uvNextInterp=uv_next;uv2NextInterp=uv2_next;frameMixInterp=frame_mix;tintInterp=tint;gl_Position=mvp*vec4(pos,1.0);}')
        info.fragment_source("""
        vec4 blendFrames(vec4 a,vec4 b,float t,float maximum){
            if(t<=0.0)return a;
            return maximum>0.5?max(a*(1.0-t),b*t):mix(a,b,t);
        }
        void main(){
            vec4 a=texture(sprite,vec2(uvInterp.x,repeat_v>0.5?fract(uvInterp.y):uvInterp.y));
            if(frameMixInterp.x>0.0)a=blendFrames(a,texture(sprite,uvNextInterp),frameMixInterp.x,maxlum.x);
            if(material_settings.x>=0.0){
                vec4 b=texture(sprite,uv2Interp);
                if(frameMixInterp.y>0.0)b=blendFrames(b,texture(sprite,uv2NextInterp),frameMixInterp.y,maxlum.y);
                if(material_settings.x<0.5)a=(a+b)*0.5;
                else if(material_settings.x<1.5)a=vec4(b.rgb,a.a);
                else {float opacity=b.a+a.a*(1.0-b.a);
                    a=vec4((b.rgb*b.a+a.rgb*a.a*(1.0-b.a))/max(opacity,0.000001),opacity);}
            }
            a*=tintInterp;a.rgb*=material_settings.y;a.a*=material_settings.z;
            if(a.a<material_settings.w)discard;
            if(addself>0.0)a.rgb=a.rgb*a.a*(1.0+addself);
            fragColor=a;
        }""")
        _shader = gpu.shader.create_from_info(info)
    return _shader


def bind_material(sh,material=None):
    p=material or {}
    sh.uniform_float('addself',float(p.get('addself',0)))
    sh.uniform_float('maxlum',(float(p.get('maxlum1',False)),float(p.get('maxlum2',False))))
    sh.uniform_float('material_settings',(float(p.get('dual_mode',-1)),p.get('brightness',1),p.get('alpha',1),p.get('cutoff',-1)))


def draw():
    if work_busy():return
    context = bpy.context
    if not _state or context.region_data is None: return
    obj = _state['object']
    if not obj.visible_get() or context.scene != _state['scene']: return
    scale = _state['scale']
    matrix = _state['frame']
    view = context.region_data.view_matrix.inverted().to_3x3()
    right, up = view @ Vector((1,0,0)), view @ Vector((0,1,0))
    sh = shader()
    old_blend = gpu.state.blend_get(); old_depth = gpu.state.depth_test_get()
    old_mask = gpu.state.depth_mask_get()
    try:
        gpu.state.depth_test_set('LESS_EQUAL'); gpu.state.depth_mask_set(False)
        for sim, delay, texture, additive, sequences in _state['systems']:
            if texture is None or not len(sim.age): continue
            eye=context.region_data.view_matrix.inverted().translation
            profile=_state['profiles'].get(sim.definition.attrs.get('material',''),{})
            for renderer in sim.definition.rules['renderers']:
                if renderer.get('functionName','').casefold() not in RENDERERS:continue
                geometry,gaps=build_geometry(sim,renderer,sequences,matrix,right,up,eye,scale,texture_height=texture.height,material=profile)
                for gap in gaps | sim.warnings:
                    warning=f'{sim.definition.name}: {gap}'
                    if warning not in _state['warnings']:
                        _state['warnings'].append(warning)
                        print('Hammerless particle preview:',warning)
                if not len(geometry['pos']):continue
                gpu.state.blend_set(blend_mode(additive,profile))
                sh.bind();bind_material(sh,dict(profile,dual_mode=-1) if renderer.get('functionName','').casefold()=='render_rope' else profile);sh.uniform_float('repeat_v',float(renderer.get('functionName','').casefold()=='render_rope'));sh.uniform_float('mvp',context.region_data.perspective_matrix);sh.uniform_sampler('sprite',texture)
                batch_for_shader(sh,'TRIS',geometry).draw(sh)
    finally:
        gpu.state.blend_set(old_blend);gpu.state.depth_test_set(old_depth);gpu.state.depth_mask_set(old_mask)


class HL_OT_particle_preview(bpy.types.Operator):
    bl_idname='hammerless.particle_preview'
    bl_label='Preview Selected Particle'
    bl_description='Experimental animation; reports unsupported rules. Does not change the map'

    def execute(self,context):
        global _selection
        if work_busy():
            self.report({'INFO'},'Particle preview waits until building or baking finishes')
            return {'CANCELLED'}
        stop()
        from .ops import game_content, game_root
        from ..core.particlesheet import load_atlas
        from ..core.particlematerial import load_profile
        obj=context.active_object
        if obj is None or obj.hammerless.classname!='info_particle_system':
            self.report({'ERROR'},'Select a Particle Effect first');return {'CANCELLED'}
        from .particlebrowser import particle_empty
        obj=particle_empty(obj)
        name=next((k.value for k in obj.hammerless.keyvalues if k.key=='effect_name'),'').casefold()
        _selection=(context.scene.as_pointer(),obj.as_pointer(),name)
        content=game_content(game_root(context))
        if content is None:
            self.report({'ERROR'},'Set the game folder first');return {'CANCELLED'}
        root=game_root(context)
        if root not in _library_cache:
            library,errors=load_library(content)
            _library_cache[root]=(library,errors,audit(library))
        library,errors,coverage=_library_cache[root]
        if name not in library:
            self.report({'ERROR'},'Particle definition not found');return {'CANCELLED'}
        systems=[];textures={};profiles={};warnings=list(coverage[name]['recursive_gaps'])
        def assets(definition):
            material=definition.attrs.get('material','')
            if material not in textures:
                profiles[material]=load_profile(content,material) if material else {}
                warnings.extend(material+': '+w for w in profiles[material].get('warnings',[]))
                image=load_atlas(content,material) if material else None
                if image is None:
                    textures[material]=(None,False,{})
                    if material:warnings.append('Missing sprite: '+material)
                else:
                    rgba,sequences,additive=image;h,w=rgba.shape[:2]
                    pixels=np.ascontiguousarray(rgba[::-1],dtype=np.float32)
                    textures[material]=(gpu.types.GPUTexture((w,h),format='RGBA32F',data=gpu.types.Buffer('FLOAT',pixels.size,pixels.ravel())),additive,sequences)
            return textures[material][2]
        try:
            frame=obj.matrix_world.copy()
            scale=max(.001,context.scene.hammerless.units_per_meter)
            bindings,binding_warnings=control_point_bindings(context.scene,obj)
            warnings.extend(binding_warnings)
            def trace(origin,direction,length):
                world_origin=frame@Vector(origin/scale)
                world_direction=frame.to_3x3()@Vector(direction)
                factor=world_direction.length
                if factor<1e-9:return origin
                hit,location,*_=context.scene.ray_cast(context.evaluated_depsgraph_get(),world_origin,world_direction.normalized(),distance=length/scale*factor)
                return np.asarray(frame.inverted_safe()@location)*scale if hit else origin+direction*length
            def factory(points):
                tree=EffectSimulation(library,name,asset_loader=assets,control_points=points)
                for sim in tree.systems:sim.trace_callback=trace
                return tree
            effect_simulation=factory(sample_control_points(bindings,frame,scale))
            warnings.extend(sorted(effect_simulation.warnings))
            for sim in effect_simulation.systems:
                texture,additive,sequences=textures[sim.definition.attrs.get('material','')]
                if not any(p.get('functionName','').casefold() in RENDERERS for p in sim.definition.rules['renderers']):texture=None
                systems.append((sim,0,texture,additive,sequences))
            shader()
        except Exception as exc:
            self.report({'ERROR'},str(exc));return {'CANCELLED'}
        _state.update(object=obj,effect=name,scene=context.scene,systems=systems,last=time.perf_counter(),time=0,
                      warnings=warnings,errors=errors,profiles=profiles,effect_simulation=effect_simulation,frame=frame,scale=scale,bindings=bindings,factory=factory,textures=textures)
        print('Hammerless particle preview gaps for',name,':',warnings)
        _state['handle']=bpy.types.SpaceView3D.draw_handler_add(draw,(),'WINDOW','POST_VIEW')
        bpy.app.timers.register(tick,first_interval=.03)
        self.report({'WARNING'},f'Approximate preview: {len(warnings)} rule/parameter gaps; animated sheets, no collision')
        return {'FINISHED'}


class HL_OT_particle_preview_stop(bpy.types.Operator):
    bl_idname='hammerless.particle_preview_stop'
    bl_label='Stop Particle Preview'
    def execute(self,context):
        stop();return {'FINISHED'}


def watch_selection():
    global _selection,_migrated_scene
    try:
        if work_busy():return .25
        context=bpy.context
        if context.scene is None or context.mode != 'OBJECT':return .2
        from .particlebrowser import particle_empty
        scene_key=context.scene.as_pointer()
        if scene_key!=_migrated_scene:
            stop()
            for obj in list(context.scene.objects):
                if obj.hammerless.classname=='info_particle_system':particle_empty(obj)
            _migrated_scene=scene_key
        obj=context.active_object
        enabled=getattr(context.window_manager,'hl_pfx_autoplay',False)
        valid=enabled and obj is not None and obj.select_get() and obj.hammerless.classname=='info_particle_system'
        if valid:
            obj=particle_empty(obj)
            name=next((k.value for k in obj.hammerless.keyvalues if k.key=='effect_name'),'')
            token=(scene_key,obj.as_pointer(),name)
        else:token=None
        if token!=_selection:
            _selection=token
            stop()
            if token is not None and name and obj.visible_get():bpy.ops.hammerless.particle_preview()
    except Exception as exc:
        print('Hammerless particle selection preview:',exc)
    return .2


@bpy.app.handlers.persistent
def on_load(*args):
    global _selection,_migrated_scene
    _selection=None;_migrated_scene=None
    stop()


def register():
    bpy.utils.register_class(HL_OT_particle_preview)
    bpy.utils.register_class(HL_OT_particle_preview_stop)
    bpy.app.handlers.load_pre.append(on_load)
    if not bpy.app.background:bpy.app.timers.register(watch_selection,first_interval=.5,persistent=True)


def unregister():
    global _shader
    stop();_shader=None
    if bpy.app.timers.is_registered(watch_selection):bpy.app.timers.unregister(watch_selection)
    _library_cache.clear()
    if on_load in bpy.app.handlers.load_pre:bpy.app.handlers.load_pre.remove(on_load)
    bpy.utils.unregister_class(HL_OT_particle_preview_stop)
    bpy.utils.unregister_class(HL_OT_particle_preview)
