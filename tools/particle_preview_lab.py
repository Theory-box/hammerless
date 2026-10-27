"""Interactive branch preview in a separate factory-startup Blender session.
Run blender --factory-startup --python tools/particle_preview_lab.py -- --game GAME
No installed-addon replacement and no blend file opened or saved.
"""
import argparse,sys,traceback
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import bpy
from mathutils import Vector


def main():
    p=argparse.ArgumentParser();p.add_argument('--game',required=True);p.add_argument('--smoke-test',action='store_true')
    args=p.parse_args(sys.argv[sys.argv.index('--')+1:])
    import hammerless
    from hammerless.core.vpk import GameContent
    from hammerless.core.particlepreview import load_library
    from hammerless.blender import particlepreview as preview
    hammerless.register()
    library,errors=load_library(GameContent(args.game))
    if not library:raise RuntimeError('No particle definitions found')
    for obj in list(bpy.data.objects):bpy.data.objects.remove(obj,do_unlink=True)
    scene=bpy.context.scene;scene.name='Hammerless Particle Preview Lab'
    scene.hammerless.game_root=args.game
    emitter=bpy.data.objects.new('Preview emitter',None);scene.collection.objects.link(emitter)
    emitter.empty_display_type='SPHERE';emitter.empty_display_size=.08
    emitter.hammerless.classname='info_particle_system'
    kv=next((item for item in emitter.hammerless.keyvalues if item.key=='effect_name'),None)
    if kv is None:kv=emitter.hammerless.keyvalues.add();kv.key='effect_name'
    kv.value='groundfog_street'
    emitter.select_set(True);bpy.context.view_layer.objects.active=emitter
    items=[(name,name,'') for name in sorted(library)]
    presets=[('groundfog_street','Ground fog - flat'),('groundfog_street_b','Ground fog - upright'),
             ('gas_smoke','Gas smoke'),('small_sparks','Sparks'),('drainagepipe_short_stream','Water stream')]

    def select(name):
        if name not in library:raise ValueError('Unknown effect '+name)
        kv.value=name
        for obj in scene.objects:obj.select_set(False)
        emitter.select_set(True);bpy.context.view_layer.objects.active=emitter
        bpy.context.view_layer.update()
        result=bpy.ops.hammerless.particle_preview()
        if result!={'FINISHED'}:raise RuntimeError('Preview failed: '+str(result))

    class HL_OT_lab_select(bpy.types.Operator):
        bl_idname='hammerless.lab_select';bl_label='Choose an Effect';bl_property='effect'
        effect:bpy.props.EnumProperty(items=items)
        def execute(self,context):select(self.effect);return {'FINISHED'}
        def invoke(self,context,event):context.window_manager.invoke_search_popup(self);return {'RUNNING_MODAL'}

    class HL_OT_lab_restart(bpy.types.Operator):
        bl_idname='hammerless.lab_restart';bl_label='Restart Preview'
        def execute(self,context):select(kv.value);return {'FINISHED'}

    class HL_PT_preview_lab(bpy.types.Panel):
        bl_idname='HL_PT_preview_lab';bl_label='Particle Preview Lab'
        bl_space_type='VIEW_3D';bl_region_type='UI';bl_category='Preview Lab'
        def draw(self,context):
            layout=self.layout
            layout.label(text='Standalone branch renderer',icon='INFO')
            layout.label(text=kv.value)
            layout.operator('hammerless.lab_select',text='Search All Effects',icon='VIEWZOOM')
            row=layout.row(align=True);row.operator('hammerless.lab_restart',text='Restart',icon='PLAY')
            row.operator('hammerless.particle_preview_stop',text='Stop',icon='PAUSE')
            layout.label(text=f"Elapsed: {preview._state.get('time',0):.2f} seconds")
            layout.separator()
            for name,label in presets:
                if name in library:layout.operator('hammerless.lab_select',text=label).effect=name
            layout.separator();layout.label(text='Orbit and zoom normally in the viewport.')
            layout.label(text='Short bursts: press Restart to replay.')
            warnings=preview._state.get('warnings',[])
            layout.label(text=f'Known gaps: {len(warnings)} - approximate preview')
            for warning in warnings[:5]:layout.label(text=warning[:65],icon='INFO')

    for cls in (HL_OT_lab_select,HL_OT_lab_restart,HL_PT_preview_lab):bpy.utils.register_class(cls)
    for area in bpy.context.screen.areas:
        if area.type=='VIEW_3D':
            space=area.spaces.active;space.show_region_ui=True
            space.region_3d.view_location=(0,0,0);space.region_3d.view_distance=12
            space.region_3d.view_rotation=Vector((0,-1,.45)).to_track_quat('Z','Y')
            space.region_3d.view_perspective='PERSP'
            space.shading.background_type='WORLD';scene.world.color=(.015,.015,.015)
    select('groundfog_street')
    print('Particle Preview Lab ready:',len(library),'effects; installed addon and maps unchanged',flush=True)
    if args.smoke_test:
        state={'index':0,'waits':0}
        def check():
            try:
                if preview._state and preview._state['time']==0 and state['waits']<20:
                    state['waits']+=1;return .2
                state['waits']=0
                assert preview._state and preview._state['time']>0, str((list(preview._state),bpy.context.active_object,kv.value))
                assert len(scene.objects)==1
                if state['index']<len(presets):
                    name=presets[state['index']][0];state['index']+=1;select(name);return .3
                preview.stop();assert not preview._state
                print('PASS standalone lab registration, five effect switches, timer and cleanup',flush=True)
                bpy.ops.wm.quit_blender()
            except Exception:
                traceback.print_exc();import os;os._exit(1)
        bpy.app.timers.register(check,first_interval=.5)


def start():
    try:main()
    except Exception:
        traceback.print_exc()
        if '--smoke-test' in sys.argv:
            import os;os._exit(1)
    return None

bpy.app.timers.register(start,first_interval=1)
