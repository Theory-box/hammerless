"""Direct ownership, legacy conversion, references, independent copies and save/reload."""
import sys
import tempfile
from pathlib import Path
import bpy
from mathutils import Matrix
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import hammerless
hammerless.register()
from hammerless.blender.presets import make_root, parent_to, convert_single_presets, resolve
from hammerless.blender.mapcollection import organize, organize_preset_objects, _category
from hammerless.blender.extract import extract_scene
from hammerless.core.build import Report
from hammerless.core.entities import PRESET_FIELDS

for o in list(bpy.data.objects):
    bpy.data.objects.remove(o, do_unlink=True)
expected = {'SURVIVAL_ARENA':'Gameplay/Survival', 'SURVIVAL_START':'Gameplay/Survival',
            'ZOMBIE_LADDER':'Gameplay/Navigation', 'ZOMBIE_SPAWN_AREA':'Gameplay/Spawn Areas'}
for key, category in expected.items():
    bpy.ops.hammerless.add_preset(preset=key)
    obj = bpy.context.object
    assert obj.type == 'MESH' and obj.parent is None and obj.hammerless.preset == key
    assert _category(obj) == category
    for field in PRESET_FIELDS.get(key, []):
        assert resolve(obj, field.targets[0])

part = bpy.context.object
part.hammerless.preset = ''
root = make_root(bpy.context, 'ZOMBIE_SPAWN_AREA', (2,3,4), bpy.context.collection)
bpy.context.view_layer.update()
parent_to(part, root)
root.rotation_euler.z = 0.6
root.scale = (2,3,1)
bpy.context.view_layer.update()
original = part.as_pointer()
before = [part.matrix_world @ v.co for v in part.data.vertices]
tree = bpy.data.node_groups.new('Reference', 'HL_LogicTree')
tree.use_fake_user = True
node = tree.nodes.new('HL_NodeObject')
node.target = root
converted, skipped = convert_single_presets(bpy.context)
assert len(converted) == 1 and not skipped, (converted, skipped)
assert part.as_pointer() == original and node.target == part and part.parent is None
bpy.context.view_layer.update()
assert all((a - part.matrix_world @ b.co).length < 1e-5 for a,b in zip(before,part.data.vertices))
assert convert_single_presets(bpy.context) == ([], [])
copy = part.copy()
copy.data = part.data.copy()
bpy.context.scene.collection.objects.link(copy)
copy.hammerless.keyvalues[0].value = 'NO_MOBS'
assert part.hammerless.keyvalues[0].value != copy.hammerless.keyvalues[0].value
organize(bpy.context)
part.hide_set(True)
organize_preset_objects(bpy.context)
assert part.hide_get()
part.hide_set(False)
assert any(c.get('hl_category') == 'Spawn Areas' for c in part.users_collection)
rep = Report()
ir, _ = extract_scene(bpy.context, rep, defer_textures=True)
assert not rep.errors, rep.errors
assert any(e.classname == 'func_button' for e in ir.entities)
name, node_name = part.name, node.name
with tempfile.TemporaryDirectory() as tmp:
    path = str(Path(tmp)/'direct.blend')
    bpy.ops.wm.save_as_mainfile(filepath=path)
    bpy.ops.wm.open_mainfile(filepath=path)
    assert bpy.data.node_groups['Reference'].nodes[node_name].target == bpy.data.objects[name]
print('PASS direct presets, transformed migration, reference remapping, duplication, export and persistence')

# Use the public operator so conversion is one undoable edit.
part = bpy.data.objects[name]
part.hammerless.preset = ''
root = make_root(bpy.context, 'ZOMBIE_SPAWN_AREA', (0,0,0), part.users_collection[0])
bpy.context.view_layer.update()
parent_to(part,root)
root_name = root.name
bpy.context.preferences.edit.use_global_undo = True
bpy.ops.ed.undo_push(message='Before conversion')
bpy.ops.hammerless.convert_preset_objects()
bpy.ops.ed.undo_push(message='After conversion')
assert bpy.data.objects[root_name].type == 'MESH'
bpy.ops.ed.undo()
assert bpy.data.objects[root_name].type == 'EMPTY'
assert len(bpy.data.objects[root_name].children) == 1
bpy.ops.ed.redo()
assert bpy.data.objects[root_name].type == 'MESH'
print('PASS conversion undo and redo')
