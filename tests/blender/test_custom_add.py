"""Exercise the actual Add operator, assemblies, independent settings and export."""
import sys, tempfile
from pathlib import Path
import bpy
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import hammerless
hammerless.register()
from hammerless.core.spawnlist import filtered, SPAWN_ITEMS, register_examples
from hammerless.blender.logic_examples import all_examples
from hammerless.blender.spawn import fill_list
from hammerless.blender.extract import extract_scene
from hammerless.blender.logic import compile_logic
from hammerless.core.build import Report

ids={'custom:swelling_pod', 'custom:emergency_knife', 'custom:laser_trap'}
assert {i.id for i in filtered('CUSTOM')}==ids
count=len(SPAWN_ITEMS)
register_examples(all_examples())
assert len(SPAWN_ITEMS)==count
assert 'custom:emergency_knife' in {i.id for i in filtered('VOLUMES','emergency')}
assert {i.id for i in filtered('FAVORITES',favorites=ids)}==ids
fill_list(bpy.context.scene)
assert ids <= {i.name for i in bpy.context.scene.hammerless.spawn_items}

for key, field, attr, default, changed in [('swelling_pod','radius','number',150,321),
                                         ('emergency_knife','price','integer',500,777)]:
    owners=[]
    for x in (0,10):
        bpy.context.scene.cursor.location=(x,0,0)
        assert bpy.ops.hammerless.spawn_item(item='custom:'+key)=={'FINISHED'}
        obj=bpy.context.view_layer.objects.active
        owners.append(obj)
        assert abs(obj.matrix_world.translation.x-x)<.001
        assert len(obj.hammerless.behaviours)==1
        obj.hammerless.behaviours[0].preview_enabled=False
        if key=='swelling_pod':
            assert any(o.type=='ARMATURE' for o in bpy.context.selected_objects)
    def value(obj):
        slot=obj.hammerless.behaviours[0]
        return slot.values[slot.graph.nodes[field+'_setting'].setting_id]
    setattr(value(owners[1]),attr,changed)
    assert getattr(value(owners[0]),attr)==default
    # Blender duplicates the whole selected assembly (armature remapping included).
    bpy.ops.object.duplicate()
    duplicate=bpy.context.view_layer.objects.active
    duplicate.location.x+=10
    duplicate.hammerless.behaviours[0].preview_enabled=False
    setattr(value(duplicate),attr,changed+1)
    assert getattr(value(owners[1]),attr)==changed
    if key=='swelling_pod':
        original_arm=next(m.object for m in owners[1].modifiers if m.type=='ARMATURE')
        copied_arm=next(m.object for m in duplicate.modifiers if m.type=='ARMATURE')
        assert copied_arm!=original_arm

def check():
    report=Report()
    ir,_=extract_scene(bpy.context,report,defer_textures=True)
    compile_logic(bpy.context,ir,report)
    assert not report.errors,report.errors
    source='\n'.join(ir.extra_scripts.values())
    assert source.count('HL_Interactions.append(')==3
    assert source.count('::HL_KnifeUsers <- {}')==1
    assert 'pod_charge' in source and '321' in source and '322' in source
    assert '777' in source and '778' in source
    owners=[o for o in bpy.context.scene.objects if o.hammerless.behaviours]
    assert len(owners)==6
    from hammerless.core.logic import _Compiler
    compiler=_Compiler(ir,[])
    handles=[]
    for obj in owners:
        entity=compiler.entity_of(obj.name)
        assert entity is not None,obj.name
        handle=compiler.object_handle(entity,obj.name)
        assert handle in source,(obj.name,handle)
        handles.append(handle)
    assert len(set(handles))==6,'Each placed/duplicated owner must resolve its own entity'
    for label,field,attr,expected in [('Swelling Pod','radius','number',[150,321,322]),
                                      ('Emergency Knife','price','integer',[500,777,778])]:
        values=[]
        for obj in owners:
            if obj.name.startswith(label):
                slot=obj.hammerless.behaviours[0]
                values.append(getattr(slot.values[slot.graph.nodes[field+'_setting'].setting_id],attr))
        assert sorted(values)==expected,(label,values)
check()
with tempfile.TemporaryDirectory() as tmp:
    bpy.ops.wm.save_as_mainfile(filepath=str(Path(tmp)/'custom.blend'))
    bpy.ops.wm.open_mainfile(filepath=str(Path(tmp)/'custom.blend'))
    check()
print('PASS Custom menu, search/favorites, repeat placement, assembly duplication, independent settings, export and reload')
hammerless.unregister()
