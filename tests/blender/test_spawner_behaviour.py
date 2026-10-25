"""New spawners preserve opt-in defaults and duplicate with independent settings."""
import sys
import tempfile
from pathlib import Path
import bpy
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import hammerless
hammerless.register()
from hammerless.blender.extract import extract_scene
from hammerless.blender.logic import compile_logic
from hammerless.core.build import Report

bpy.ops.hammerless.add_entity(classname='info_zombie_spawn')
assert not bpy.context.object.hammerless.behaviours
bpy.ops.hammerless.add_entity(classname='commentary_zombie_spawner')
original = bpy.context.object
slot = original.hammerless.behaviours[0]
tree = slot.graph
keys = {n: tree.nodes[n].setting_id for n in ('enabled', 'type', 'delay')}
assert not slot.values[keys['enabled']].boolean
assert slot.values[keys['type']].choice == 'witch'
assert all(n.bl_idname in {'HL_NodeSelf', 'HL_NodeMapStart', 'HL_NodeExposedSetting',
                         'HL_NodeMath', 'HL_NodeIf', 'HL_NodeFireInput', 'NodeFrame'} for n in tree.nodes)
copy = original.copy()
bpy.context.scene.collection.objects.link(copy)
other = copy.hammerless.behaviours[0]
other.values[keys['enabled']].boolean = True
other.values[keys['type']].choice = 'tank'
other.values[keys['delay']].number = 3.0
assert not slot.values[keys['enabled']].boolean
assert slot.values[keys['type']].choice == 'witch'
assert slot.values[keys['delay']].number == 0.0

def check():
    report = Report()
    ir, _ = extract_scene(bpy.context, report, defer_textures=True)
    compile_logic(bpy.context, ir, report)
    assert not report.errors, report.errors
    script = '\n'.join(ir.extra_scripts.values())
    assert 'SpawnZombie' in script and 'witch' in script and 'tank' in script
    assert 'if (false)' in script and 'if (true)' in script
    spawners = [e for e in ir.entities if e.classname == 'commentary_zombie_spawner']
    assert len(spawners) == 2
    assert {e.keyvalues['targetname'] for e in spawners} == {'zombie_spawner'}
    registrations = [e.keyvalues['vscripts'].split()[0] for e in spawners]
    assert len(set(registrations)) == 2, 'Self must resolve each spawner separately'
    for path in registrations:
        assert f'::HL_ObjectRefs["{path}"] <- self;' in ir.extra_scripts[f'scripts/vscripts/{path}.nut']
        assert f'? ::HL_ObjectRefs["{path}"] : null)' in script
    assert 'Entities.FindByName(null, "zombie_spawner")' not in script
    return script

check()
with tempfile.TemporaryDirectory() as tmp:
    bpy.ops.wm.save_as_mainfile(filepath=str(Path(tmp) / 'spawners.blend'))
    bpy.ops.wm.open_mainfile(filepath=str(Path(tmp) / 'spawners.blend'))
    check()
print('PASS spawner defaults, independent copies, compilation and save/reload')
hammerless.unregister()
