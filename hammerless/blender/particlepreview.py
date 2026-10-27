"""Opt-in, transient selected-effect preview. Never creates or saves scene objects."""
import time
import bpy
import gpu
import numpy as np
from mathutils import Vector
from gpu_extras.batch import batch_for_shader
from ..core.particlepreview import load_library, audit, Simulation

_state = {}
_shader = None


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
        previous = _state['time']; _state['time'] += dt
        for sim, delay, texture, additive, sequences in _state['systems']:
            elapsed = max(0,_state['time']-delay)-max(0,previous-delay)
            if elapsed:
                sim.advance(elapsed)
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
        info.sampler(0,'FLOAT_2D','sprite')
        info.vertex_in(0,'VEC3','pos'); info.vertex_in(1,'VEC2','uv'); info.vertex_in(2,'VEC4','tint')
        info.vertex_out(interface); info.fragment_out(0,'VEC4','fragColor')
        info.vertex_source('void main(){uvInterp=uv;tintInterp=tint;gl_Position=mvp*vec4(pos,1.0);}')
        info.fragment_source('void main(){fragColor=texture(sprite,uvInterp)*tintInterp;}')
        _shader = gpu.shader.create_from_info(info)
    return _shader


def draw():
    context = bpy.context
    if not _state or context.region_data is None: return
    obj = _state['object']
    if not obj.visible_get() or context.scene != _state['scene']: return
    scale = max(.001, context.scene.hammerless.units_per_meter)
    matrix = obj.matrix_world
    view = context.region_data.view_matrix.inverted().to_3x3()
    right, up = view @ Vector((1,0,0)), view @ Vector((0,1,0))
    sh = shader()
    old_blend = gpu.state.blend_get(); old_depth = gpu.state.depth_test_get()
    old_mask = gpu.state.depth_mask_get()
    try:
        gpu.state.depth_test_set('LESS_EQUAL'); gpu.state.depth_mask_set(False)
        for sim, delay, texture, additive, sequences in _state['systems']:
            if texture is None or not len(sim.age): continue
            pos=[];uv=[];colors=[]
            centers=[matrix @ Vector(p/scale) for p in sim.position]
            # Sort translucent cards back to front for the current viewport.
            eye=context.region_data.view_matrix.inverted().translation
            order=sorted(range(len(centers)),key=lambda i:(centers[i]-eye).length_squared,reverse=True)
            renderer=next((p for p in sim.definition.rules['renderers'] if p.get('functionName','').casefold()=='render_animated_sprites'),{})
            for i in order:
                sheet=sequences.get(int(sim.sequence[i]))
                if sequences and sheet is None:
                    # A sheet is not a single sprite; drawing the whole atlas
                    # would hide a missing/unsupported sequence selection.
                    warning=f'{sim.definition.name}: missing sheet sequence {int(sim.sequence[i])}'
                    if warning not in _state['warnings']:
                        _state['warnings'].append(warning)
                        print('Hammerless particle preview:',warning)
                    continue
                rect=(0,0,1,1)
                if sheet is not None:
                    frame=sheet.frame(float(sim.age[i]),float(sim.life[i]),
                        rate=float(renderer.get('animation rate',1)),
                        fit=bool(renderer.get('animation_fit_lifetime',False)),
                        fps=bool(renderer.get('use animation rate as FPS',False)))
                    rect=sheet.rectangles[frame,0]
                u0,v0,u1,v1=rect
                angle=float(sim.rotation[i]);co=np.cos(angle);si=np.sin(angle)
                center=centers[i];radius=max(0,float(sim.radius[i]))/scale
                for x,y in [(-1,-1),(1,-1),(1,1),(-1,-1),(1,1),(-1,1)]:
                    pos.append(center+right*(x*co-y*si)*radius+up*(x*si+y*co)*radius)
                    uv.append((u0+(u1-u0)*(x+1)/2,1-(v0+(v1-v0)*(1-y)/2)));colors.append(tuple(sim.color[i]))
            if not pos: continue
            gpu.state.blend_set('ADDITIVE' if additive else 'ALPHA')
            sh.bind();sh.uniform_float('mvp',context.region_data.perspective_matrix);sh.uniform_sampler('sprite',texture)
            batch_for_shader(sh,'TRIS',{'pos':pos,'uv':uv,'tint':colors}).draw(sh)
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
        def add(key,delay,path):
            if key in path or key not in library: return
            if len(systems)>=16:
                warnings.append('Preview limited to 16 child systems');return
            definition=library[key];material=definition.attrs.get('material','')
            if material not in textures:
                image=load_atlas(content,material) if material else None
                if image is None:
                    textures[material]=(None,False,{})
                    if material: warnings.append('Missing sprite: '+material)
                else:
                    rgba,sequences,additive=image;h,w=rgba.shape[:2]
                    pixels=np.ascontiguousarray(rgba[::-1],dtype=np.float32)
                    textures[material]=(gpu.types.GPUTexture((w,h),format='RGBA32F',data=gpu.types.Buffer('FLOAT',pixels.size,pixels.ravel())),additive,sequences)
            texture,additive,sequences=textures[material]
            if not any(p.get('functionName','').casefold() == 'render_animated_sprites'
                       for p in definition.rules['renderers']):
                texture = None  # Do not disguise unsupported ropes/trails as sprites.
            systems.append((Simulation(definition,seed=len(systems),limit=256),delay,texture,additive,sequences))
            for child,wait in definition.children: add(child,delay+max(0,wait),path|{key})
        try:
            add(name,0,set())
            shader()
        except Exception as exc:
            self.report({'ERROR'},str(exc));return {'CANCELLED'}
        _state.update(object=obj,effect=name,scene=context.scene,systems=systems,last=time.perf_counter(),time=0,
                      warnings=warnings,errors=errors)
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
