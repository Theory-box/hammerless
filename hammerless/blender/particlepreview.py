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

_state = {}
_shader = None


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


def tick():
    try:
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
        for wm in bpy.data.window_managers:
            for window in wm.windows:
                for area in window.screen.areas:
                    if area.type == 'VIEW_3D': area.tag_redraw()
        return 1/30
    except Exception as exc:
        print('Hammerless particle preview stopped:', exc)
        stop(); return None


def shader():
    global _shader
    if _shader is None:
        interface = gpu.types.GPUStageInterfaceInfo('hl_particle_preview')
        interface.smooth('VEC2','uvInterp'); interface.smooth('VEC4','tintInterp')
        info = gpu.types.GPUShaderCreateInfo()
        info.push_constant('MAT4','mvp')
        info.push_constant('FLOAT','repeat_v')
        info.sampler(0,'FLOAT_2D','sprite')
        info.vertex_in(0,'VEC3','pos'); info.vertex_in(1,'VEC2','uv'); info.vertex_in(2,'VEC4','tint')
        info.vertex_out(interface); info.fragment_out(0,'VEC4','fragColor')
        info.vertex_source('void main(){uvInterp=uv;tintInterp=tint;gl_Position=mvp*vec4(pos,1.0);}')
        info.fragment_source('void main(){fragColor=texture(sprite,vec2(uvInterp.x,repeat_v>0.5?fract(uvInterp.y):uvInterp.y))*tintInterp;}')
        _shader = gpu.shader.create_from_info(info)
    return _shader


def draw():
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
            for renderer in sim.definition.rules['renderers']:
                if renderer.get('functionName','').casefold() not in RENDERERS:continue
                geometry,gaps=build_geometry(sim,renderer,sequences,matrix,right,up,eye,scale,texture_height=texture.height)
                for gap in gaps | sim.warnings:
                    warning=f'{sim.definition.name}: {gap}'
                    if warning not in _state['warnings']:
                        _state['warnings'].append(warning)
                        print('Hammerless particle preview:',warning)
                if not len(geometry['pos']):continue
                gpu.state.blend_set('ADDITIVE' if additive else 'ALPHA')
                sh.bind();sh.uniform_float('repeat_v',float(renderer.get('functionName','').casefold()=='render_rope'));sh.uniform_float('mvp',context.region_data.perspective_matrix);sh.uniform_sampler('sprite',texture)
                batch_for_shader(sh,'TRIS',geometry).draw(sh)
    finally:
        gpu.state.blend_set(old_blend);gpu.state.depth_test_set(old_depth);gpu.state.depth_mask_set(old_mask)


class HL_OT_particle_preview(bpy.types.Operator):
    bl_idname='hammerless.particle_preview'
    bl_label='Preview Selected Particle'
    bl_description='Experimental animation; reports unsupported rules. Does not change the map'

    def execute(self,context):
        stop()
        from .ops import game_content, game_root
        from ..core.particlesheet import load_atlas
        obj=context.active_object
        if obj is None or obj.hammerless.classname!='info_particle_system':
            self.report({'ERROR'},'Select a Particle Effect first');return {'CANCELLED'}
        name=next((k.value for k in obj.hammerless.keyvalues if k.key=='effect_name'),'').casefold()
        content=game_content(game_root(context))
        if content is None:
            self.report({'ERROR'},'Set the game folder first');return {'CANCELLED'}
        library,errors=load_library(content)
        if name not in library:
            self.report({'ERROR'},'Particle definition not found');return {'CANCELLED'}
        coverage=audit(library);systems=[];textures={};warnings=list(coverage[name]['recursive_gaps'])
        def assets(definition):
            material=definition.attrs.get('material','')
            if material not in textures:
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
            effect_simulation=EffectSimulation(library,name,asset_loader=assets,
                control_points=sample_control_points(bindings,frame,scale))
            warnings.extend(sorted(effect_simulation.warnings))
            for sim in effect_simulation.systems:
                texture,additive,sequences=textures[sim.definition.attrs.get('material','')]
                if not any(p.get('functionName','').casefold() in RENDERERS for p in sim.definition.rules['renderers']):texture=None
                systems.append((sim,0,texture,additive,sequences))
            shader()
        except Exception as exc:
            self.report({'ERROR'},str(exc));return {'CANCELLED'}
        _state.update(object=obj,effect=name,scene=context.scene,systems=systems,last=time.perf_counter(),time=0,
                      warnings=warnings,errors=errors,effect_simulation=effect_simulation,frame=frame,scale=scale,bindings=bindings)
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


@bpy.app.handlers.persistent
def on_load(*args):
    stop()


def register():
    bpy.utils.register_class(HL_OT_particle_preview)
    bpy.utils.register_class(HL_OT_particle_preview_stop)
    bpy.app.handlers.load_pre.append(on_load)


def unregister():
    global _shader
    stop();_shader=None
    if on_load in bpy.app.handlers.load_pre:bpy.app.handlers.load_pre.remove(on_load)
    bpy.utils.unregister_class(HL_OT_particle_preview_stop)
    bpy.utils.unregister_class(HL_OT_particle_preview)
