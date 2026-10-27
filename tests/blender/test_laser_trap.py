"""Exercise laser placement, independent settings and generated script on main."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import bpy, hammerless
hammerless.register()
from hammerless.blender.extract import extract_scene
from hammerless.blender.logic import compile_logic
from hammerless.core.build import Report

owners = []
for x in (0, 10):
    bpy.context.scene.cursor.location.x = x
    assert bpy.ops.hammerless.spawn_item(item='custom:laser_trap') == {'FINISHED'}
    owners.append(bpy.context.view_layer.objects.active)
    assert abs(owners[-1].location.x-x) < .001
assert owners[0] != owners[1]
slots = [o.hammerless.behaviours[0] for o in owners]
def setting(slot, key):
    return slot.values[slot.graph.nodes[key].setting_id]
setting(slots[1], 'budget').integer = 2345
assert setting(slots[0], 'budget').integer == 1000
report = Report()
ir, _ = extract_scene(bpy.context, report, defer_textures=True)
compile_logic(bpy.context, ir, report)
assert not report.errors, report.errors
source = '\n'.join(ir.extra_scripts.values())
assert '2345' in source and '1000' in source
assert 'GetBoundingMins' not in source
assert 'm_Collision.m_vecMins' in source
assert 'target.TakeDamage(amount, 8, a)' in source
from hammerless.core.logic import _Compiler
compiler = _Compiler(ir, [])
handles = [compiler.object_handle(compiler.entity_of(o.name), o.name) for o in owners]
assert len(set(handles)) == 2
assert all(h in source for h in handles)
print('PASS laser Custom placement, independent budgets, owner handles and damage export')
