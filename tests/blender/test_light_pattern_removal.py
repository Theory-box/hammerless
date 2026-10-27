import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import bpy, hammerless
hammerless.register()
from hammerless.blender.lightpresets import preset_slot
from hammerless.blender.behaviours import new_behaviour_tree
from hammerless.blender.extract import light_style_keyvalues
from hammerless.blender.mapcollection import _on_load

def lamp(name):
    obj = bpy.data.objects.new(name, bpy.data.lights.new(name, 'POINT'))
    bpy.context.scene.collection.objects.link(obj)
    obj.hammerless.light_pattern = '1'
    return obj

a, b = lamp('Remove flicker'), lamp('Keep shared flicker')
assert preset_slot(a).graph == preset_slot(b).graph
bpy.context.view_layer.objects.active = a
a.select_set(True)
a.hammerless.behaviours_index = 0
assert bpy.ops.hammerless.behaviour_remove() == {'FINISHED'}
assert a.hammerless.light_pattern == '0' and preset_slot(a) is None
assert 'style' not in light_style_keyvalues(a, {})
_on_load()
assert preset_slot(a) is None
assert b.hammerless.light_pattern == '1' and preset_slot(b) is not None
# Removing an unrelated behaviour must leave the chosen pattern intact.
bpy.context.view_layer.objects.active = b
b.select_set(True)
b.hammerless.behaviours.add().graph = new_behaviour_tree('Unrelated')
b.hammerless.behaviours_index = len(b.hammerless.behaviours)-1
assert bpy.ops.hammerless.behaviour_remove() == {'FINISHED'}
assert b.hammerless.light_pattern == '1' and preset_slot(b) is not None
# Made-unique copies carry the same preset tag and must reset too.
preset_slot(b).graph = preset_slot(b).graph.copy()
preset_slot(b).graph['hl_unique'] = True
b.hammerless.behaviours_index = 0
assert bpy.ops.hammerless.behaviour_remove() == {'FINISHED'}
assert b.hammerless.light_pattern == '0' and preset_slot(b) is None
print('LIGHT_PATTERN_REMOVAL_OK')
