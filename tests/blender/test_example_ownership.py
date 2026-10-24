"""Catalogue construction and Blender's actual duplicate/remap lifecycle."""
import sys
import tempfile
from pathlib import Path
import bpy
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import hammerless
hammerless.register()
from hammerless.blender import logic_examples as examples, example_ownership, logic, exposed
from hammerless.blender.logicgroups import flatten
from hammerless.core.build import Report

for obj in list(bpy.data.objects): bpy.data.objects.remove(obj, do_unlink=True)
made = []
instance_variables = {}
for index, ex in enumerate(examples.all_examples()):
    if not example_ownership.applicable(ex): continue
    # Asset import has its own tests. Use ordinary entities for character/model
    # placeholders here so this regression requires no extracted game models.
    objects = {}
    for key, spec in ex['objects'].items():
        shape = spec[0]
        if shape.startswith(('PLACED:', 'MANNEQUIN:')) or shape == 'CURSOR_RING':
            shape = 'ENTITY:prop_dynamic'
        obj = examples._objects(bpy.context, index+1, {'objects': {key:(shape, spec[1], *spec[2:])}})[key]
        objects[key] = obj
    problems = []
    tree = example_ownership.build(bpy.context, ex, objects, problems, index+1)
    assert not problems, (ex['title'], problems)
    if ex['title'] == 'Power Goes Out':
        assert tree.nodes['stutter'].inputs['Set Pattern'].value == 'mmamammmmammamamaaamammma'
        assert tree.nodes['stutter_second'].inputs['Set Pattern'].value == 'mmamammmmammamamaaamammma'
    for obj in objects.values():
        for slot in obj.hammerless.behaviours:
            logic._SELF_OWNER[0] = obj; logic._SELF_ISOLATE[0] = True
            logic._EXPOSED_SLOT[0] = slot
            report = Report()
            flat = flatten(bpy.context, slot.graph, report, obj.name + '/')
            assert not report.errors, (ex['title'], report.errors)
            assert not report.warnings, (ex['title'], report.warnings)
            variables = {n.settings['name'] for n in flat.nodes if n.kind in ('GET_VAR','SET_VAR')
                         and n.settings.get('scope') in ('MAP','PLAYER')}
            for name in variables:
                assert name not in instance_variables or instance_variables[name] == obj.name, (name, obj.name)
                instance_variables[name] = obj.name
    made.append((ex, objects, tree))
    print('BUILT', index+1, ex['title'], flush=True)

logic._SELF_OWNER[0] = None; logic._SELF_ISOLATE[0] = False
logic._EXPOSED_SLOT[0] = None
ex, objects, tree = next(item for item in made if item[0]['title']=='Button Opens a Gate')
owner, gate = objects['button'], objects['gate']
bpy.ops.object.select_all(action='DESELECT')
owner.select_set(True); gate.select_set(True)
bpy.context.view_layer.objects.active = owner
bpy.context.preferences.edit.use_global_undo = True
bpy.ops.ed.undo_push(message='Before assembly duplicate')
bpy.ops.object.duplicate()
copy = bpy.context.object
assert copy != owner
slot = copy.hammerless.behaviours[0]
ref = slot.values['example_target_gate']
assert ref.object != gate and ref.object in bpy.context.selected_objects
original = owner.hammerless.behaviours[0].values['example_target_gate']
assert original.object == gate
logic._SELF_OWNER[0] = copy; logic._SELF_ISOLATE[0] = True
logic._EXPOSED_SLOT[0] = slot
flat = flatten(bpy.context, tree, Report(), copy.name + '/')
assert next(n for n in flat.nodes if n.kind=='BUTTON').obj == copy.name
assert next(n for n in flat.nodes if n.kind=='MOVE').obj == ref.object.name
owner_name, gate_name, copy_name, partner_name = owner.name, gate.name, copy.name, ref.object.name
logic._SELF_OWNER[0] = None; logic._SELF_ISOLATE[0] = False
logic._EXPOSED_SLOT[0] = None
bpy.ops.ed.undo_push(message='Assembly duplicate')
bpy.ops.ed.undo()
assert bpy.data.objects.get(copy_name) is None
bpy.ops.ed.redo()
def values():
    return (bpy.data.objects[copy_name].hammerless.behaviours[0].values['example_target_gate'],
            bpy.data.objects[owner_name].hammerless.behaviours[0].values['example_target_gate'])
ref, original = values()
assert ref.object == bpy.data.objects[partner_name] and original.object == bpy.data.objects[gate_name]
bpy.ops.object.select_all(action='DESELECT')
ref.object.select_set(True)
bpy.ops.object.delete()
assert (ref.object is None or ref.object.name not in bpy.context.scene.objects)
assert original.object == bpy.data.objects[gate_name]
bpy.ops.ed.undo_push(message='Deleted partner')
bpy.ops.ed.undo()
ref, original = values()
assert ref.object == bpy.data.objects[partner_name]
with tempfile.TemporaryDirectory() as tmp:
    path = str(Path(tmp)/'ownership.blend')
    bpy.ops.wm.save_as_mainfile(filepath=path)
    bpy.ops.wm.open_mainfile(filepath=path)
    ref, original = values()
    assert ref.object == bpy.data.objects[partner_name] and original.object == bpy.data.objects[gate_name]
print('PASS catalogue construction, independent compilation, duplicate, delete, undo/redo, save/reload', flush=True)
